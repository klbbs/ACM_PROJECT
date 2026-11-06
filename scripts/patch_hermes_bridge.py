#!/usr/bin/env python3
"""Opt-in, bounded bridge adapter migration. Default is a read-only plan.

No login, subprocess launch, message sending or AI request is performed.
Unknown local layouts are rejected. Originals are kept in a private backup.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

MARKER = '# ACM roll expression adapter v1'
CORE_OLD = '''            parts=arg.split(None,1)
            if not parts or len(parts[0])>4 or not parts[0].isdecimal() or not 800<=int(parts[0])<=3500 or (len(parts)>1 and len(parts[1])>80):
                return Reply('用法：/roll 1200 [题型]，分数范围 800～3500。例如 /roll 1200 贪心。')
            # Validate authentication and module readiness before reserving or paying.
            await self.api.ready()
            row,new=self.store.start(shared,int(parts[0]),parts[1] if len(parts)>1 else None,owner=user)'''
CORE_NEW = '''            # ACM roll expression adapter v1
            try:
                score,topic=parse_roll(arg)
            except ValueError as error:
                return Reply(str(error))
            # Validate authentication and module readiness before reserving or paying.
            await self.api.ready()
            if score is None:
                previous=row.get('score') if row else None
                try:
                    score=select_score(previous)
                except ValueError:
                    preferences=await self.api.training_preferences()
                    try:
                        score=select_score(previous,preferences)
                    except ValueError as error:
                        return Reply(str(error))
            row,new=self.store.start(shared,score,topic,owner=user)'''
BACKEND_INSERT = '''    # ACM roll expression adapter v1
    async def training_preferences(self):
        async with self.client() as c:
            await self.auth(c)
            return await self.request(c,'GET','/api/training')
'''
TOPIC_OLD = '''        if not topic:return None
        aliases='''
TOPIC_NEW = '''        if not topic:return None
        # Expressions are interpreted by the server, never aliased as one topic.
        if any(symbol in topic for symbol in '&|,，'):return topic
        aliases='''


def _insert_import(source, name):
    tree = ast.parse(source)
    position = tree.body[0].end_lineno if (tree.body and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)) else 0
    lines = source.splitlines(keepends=True)
    lines.insert(position, name + '\n')
    return ''.join(lines)


def plan(directory, helper):
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('bridge directory must be a real directory')
    names = ('practice_core.py', 'practice_backend.py')
    originals = {}
    for name in (*names, 'acm_roll_commands.py'):
        target = directory / name
        if target.is_symlink():
            raise ValueError('refusing symlink: ' + name)
        if target.exists():
            if not target.is_file():
                raise ValueError('not a regular file: ' + name)
            originals[name] = target.read_bytes()
    if any(name not in originals for name in names):
        raise ValueError('practice_core.py and practice_backend.py are required')
    core, backend = (originals[name].decode('utf-8') for name in names)
    if MARKER in core or MARKER in backend:
        if not (CORE_NEW in core and BACKEND_INSERT in backend and TOPIC_NEW in backend):
            raise ValueError('partial or unsupported previous adapter; manual review required')
    else:
        if core.count(CORE_OLD) != 1 or backend.count(TOPIC_OLD) != 1:
            raise ValueError('unknown bridge layout; no files changed')
        # Restrict the source edit to the /roll branch, after existing recovery.
        tree = ast.parse(core)
        branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "cmd == '/roll'"]
        if len(branches) != 1 or CORE_OLD not in ast.get_source_segment(core, branches[0]):
            raise ValueError('expected /roll branch not found; no files changed')
        core = _insert_import(core.replace(CORE_OLD, CORE_NEW),
                              'from acm_roll_commands import parse_roll, select_score')
        anchor = '    @staticmethod\n    def topic_name(topic,caps):'
        if backend.count(anchor) != 1 or 'def training_preferences(' in backend:
            raise ValueError('unknown backend configuration API; no files changed')
        backend = backend.replace(anchor, BACKEND_INSERT + anchor).replace(TOPIC_OLD, TOPIC_NEW)
        core = core.replace('/roll 分数 [题型]（图片）', '/roll [分数] [题型表达式]（图片，&交集、|并集）')
    proposed = dict(zip(names, (core.encode(), backend.encode())))
    proposed['acm_roll_commands.py'] = helper
    for name, content in proposed.items():
        compile(content, name, 'exec')
    return originals, {name: content for name, content in proposed.items() if originals.get(name) != content}


def _atomic(path, content, mode):
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(directory, originals, changes):
    """Write only the planned files, rollback originals on a write error."""
    directory = Path(directory)
    if not changes:
        return None
    for name in changes:
        target = directory / name
        if target.is_symlink() or (target.read_bytes() if target.exists() else None) != originals.get(name):
            raise ValueError('bridge changed after planning; no files changed')
    backup = Path(tempfile.mkdtemp(prefix='acm-roll-backup-', dir=directory))
    backup.chmod(0o700)
    modes = {}
    for name in changes:
        target = directory / name
        modes[name] = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
        if name in originals:
            _atomic(backup / name, originals[name], 0o600)
    manifest = {name: hashlib.sha256(originals[name]).hexdigest() if name in originals else None
                for name in changes}
    _atomic(backup / 'manifest.json', json.dumps(manifest, indent=2).encode(), 0o600)
    written = []
    try:
        for name, content in changes.items():
            _atomic(directory / name, content, modes[name])
            written.append(name)
    except BaseException:
        for name in reversed(written):
            if name in originals:
                _atomic(directory / name, originals[name], modes[name])
            else:
                (directory / name).unlink(missing_ok=True)
        raise
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='apply after creating private backups; default only plans')
    args = parser.parse_args(argv)
    helper = (Path(__file__).resolve().parent.parent / 'src/acmhermes/commands.py').read_bytes()
    originals, changes = plan(args.directory, helper)
    print(json.dumps({'files': sorted(changes), 'applied': bool(args.apply and changes)}, ensure_ascii=False))
    if args.apply:
        backup = apply(args.directory, originals, changes)
        if backup:
            print('Backup: ' + str(backup))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
