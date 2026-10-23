"""Safe one-command updater for existing ACM Lab server deployments.

The updater only upgrades an installation that already exists.  It supports
two deployment styles:

* ``docker``   -- Docker Compose (``compose.yaml`` with an explicit ``image:``)
* ``systemd``  -- a native virtualenv started by a systemd unit in the project

Everything runs through ``subprocess`` with an argv list (never a shell), the
standard library only, and no web-facing endpoint: the CLI is executed on the
server by an administrator.

Guarantees implemented here:

* read-only preflight (``--check``) that neither fetches nor locks nor writes;
* a single-instance update lock;
* target commit pinned after ``git fetch`` and fast-forward only;
* refusal when the working tree/index is dirty or when upstream would track a
  private path (data, credentials, virtualenv, ...);
* a candidate source tree prepared from ``git archive`` and built *before* the
  old service is stopped; Docker images keep the previous image for rollback
  and the new image is *not* built over the old tag;
* the systemd candidate virtualenv installs the real candidate sources, never
  ``pip install`` into the old venv;
* a full ``data`` backup (SQLite snapshots including WAL) plus ``.env`` and
  account configuration into a 0700 directory with the original commit SHA;
* automatic rollback of code, runtime, image and the stopped-service backup on
  any failure, preserving the failed data for inspection;
* health checks against real HTTP (default ``http://127.0.0.1:8000/api/health``)
  with the configured public-origin ``Host`` header.

Deliberately unsupported: creating a brand new deployment, running from the
web UI, ``git reset --hard`` / ``git clean``, deleting untracked user files,
and non-fast-forward (merge/rebase) updates.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

DEFAULT_REF = 'origin/main'
DEFAULT_HEALTH_URL = 'http://127.0.0.1:8000/api/health'
STATE_DIRNAME = '.acm-update'
LOCK_FILENAME = 'update.lock'

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_USAGE = 3

#: Root-anchored directories that must never become git-tracked files.
PRIVATE_ROOT_DIRS = (
    'data',
    'daily',
    'records',
    'reports',
    'work',
    'for_code_skill',
    'releases',
    'dist',
    'build',
    '.venv',
    '.acm-update',
    '.aws',
    '.ssh',
    '.codex',
    '.agents',
    '.pytest_cache',
    'htmlcov',
    'node_modules',
    'frontend/dist',
    'frontend/node_modules',
    'frontend/test-results',
    'frontend/playwright-report',
)
PRIVATE_BASENAMES = frozenset({
    '.env',
    'account.toml',
    'accounts.toml',
    'accounts.web.toml',
    'ai-secrets.json',
    '.DS_Store',
})
#: Files that look private but are legitimate tracked examples.
ALLOWED_TRACKED_NAMES = frozenset({'.env.example', 'accounts.example.toml'})
PRIVATE_SUFFIXES = ('.sqlite3', '.sqlite', '.db', '.pem', '.key', '.log', '.tsbuildinfo')
DB_SIDECARS = ('-wal', '-shm', '-journal')
DB_NAME_RE = re.compile(r'\.(sqlite3?|db)$', re.IGNORECASE)
DB_SIDECAR_RE = re.compile(r'\.(sqlite3?|db)(-wal|-shm|-journal)$', re.IGNORECASE)
REF_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/+@^~:-]*$')
UNIT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9@._:-]{0,200}$')
COMPOSE_SERVICE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$')


class UpdateError(RuntimeError):
    """An update problem with a process exit code."""

    def __init__(self, message, code=EXIT_REFUSED):
        super().__init__(message)
        self.code = code


class Refused(UpdateError):
    def __init__(self, message):
        super().__init__(message, EXIT_REFUSED)


class UpdateFailed(UpdateError):
    def __init__(self, message):
        super().__init__(message, EXIT_FAILED)


class UsageError(UpdateError):
    def __init__(self, message):
        super().__init__(message, EXIT_USAGE)


def utc_stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def utc_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def tail_text(text, limit=2000):
    text = (text or '').strip()
    return text[-limit:] if len(text) > limit else text


_SECRET_URL_RE = re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://)([^/\s@]+)@')
_SECRET_PAIR_RE = re.compile(
    r'(?i)\b(token|access_token|password|passwd|secret|api[_-]?key|session|cookie)=([^&\s;]+)')


def redact(text):
    """Mask credentials before anything reaches a log, manifest or terminal."""
    if not text:
        return text
    text = _SECRET_URL_RE.sub(lambda match: match.group(1) + '***@', str(text))
    text = _SECRET_PAIR_RE.sub(lambda match: match.group(1) + '=***', text)
    return text


def ensure_dir(path, mode=0o755):
    """Create a directory we own; existing directories keep their mode."""
    path = Path(path)
    if path.is_dir():
        return path
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    return path


def ensure_parent(path):
    """Create missing parent directories without touching existing modes."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path, payload, mode=0o600):
    path = Path(path)
    ensure_dir(path.parent, 0o700)
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write('\n')
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def chmod_tree(root, dir_mode=None, file_mode=None):
    root = Path(root)
    for base, dirs, files in os.walk(root):
        for name in dirs:
            if dir_mode is not None:
                try:
                    os.chmod(os.path.join(base, name), dir_mode)
                except OSError:
                    pass
        for name in files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            if file_mode is not None:
                try:
                    mode = os.stat(path).st_mode
                    extra = 0o111 if mode & 0o111 else 0
                    os.chmod(path, (file_mode & 0o666) | extra)
                except OSError:
                    pass


def make_world_readable(root):
    """Let the unprivileged service user traverse and read a candidate tree."""
    root = Path(root)
    for base, dirs, files in os.walk(root):
        for name in dirs:
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            try:
                os.chmod(path, os.stat(path).st_mode | 0o555)
            except OSError:
                pass
        for name in files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            try:
                os.chmod(path, os.stat(path).st_mode | 0o444)
            except OSError:
                pass
    try:
        os.chmod(root, os.stat(root).st_mode | 0o555)
    except OSError:
        pass


def verify_world_readable(root, check_ancestors_up_to=None):
    """Check that a non-root service user can read/execute the candidate path."""
    root = Path(root)
    missing = []
    chain = [root]
    stop = Path(check_ancestors_up_to) if check_ancestors_up_to else None
    for parent in root.parents:
        chain.append(parent)
        if stop is not None and parent == stop:
            break
    for path in chain:
        if not path.exists():
            continue
        if not os.stat(path).st_mode & 0o001:
            missing.append(f'{path}（缺少其他用户执行位）')
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(base) / name
            if path.is_symlink():
                continue
            mode = os.stat(path).st_mode
            required = 0o004 if path.is_file() else 0o001
            if not mode & required:
                missing.append(str(path))
        if len(missing) > 5:
            break
    if missing:
        raise UpdateFailed('候选目录对服务用户不可读：' + ', '.join(missing[:5]))


def chown_if_root(path, uid, gid):
    if os.geteuid() != 0:
        return
    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except OSError:
        pass


def chown_tree(root, uid, gid):
    if os.geteuid() != 0:
        return
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(base, name)
            try:
                os.chown(path, uid, gid, follow_symlinks=False)
            except OSError:
                pass
    try:
        os.chown(root, uid, gid)
    except OSError:
        pass


def validate_ref(ref):
    if not ref:
        raise UsageError('--ref 不能为空')
    if not REF_RE.match(ref):
        raise UsageError(f'--ref 含不受支持的字符：{ref!r}')
    if '..' in ref or ref.endswith('/') or ref.endswith('.lock'):
        raise UsageError(f'--ref 不是合法的 Git 引用：{ref!r}')


def validate_unit(unit):
    if not UNIT_RE.match(unit or ''):
        raise UsageError(f'服务名不合法：{unit!r}')
    return unit if unit.endswith('.service') else unit + '.service'


def validate_compose_service(name):
    if not COMPOSE_SERVICE_RE.match(name or ''):
        raise UsageError(f'Compose 服务名不合法：{name!r}')
    return name


def parse_health_url(url):
    parts = urlsplit(url or '')
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        raise UsageError(f'健康检查 URL 必须是 http(s) 地址：{url!r}')
    if parts.username or parts.password:
        raise UsageError('健康检查 URL 不能包含账号或密码')
    return parts


def read_dotenv(path, keys):
    """Read a few simple ``KEY=VALUE`` lines without executing anything."""
    path = Path(path)
    values = {}
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if key not in keys:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]
        # Never expand shell syntax; skip values that need it.
        if '${' in value or '$(' in value or '`' in value:
            continue
        values[key] = value
    return values


def is_private_path(path):
    """True when a git-tracked path must not exist (data, secrets, venv...)."""
    pure = PurePosixPath(path)
    name = pure.name
    if name in ALLOWED_TRACKED_NAMES:
        return False
    if name == '.env' or name.startswith('.env.'):
        return True
    if name in PRIVATE_BASENAMES:
        return True
    if name.endswith(PRIVATE_SUFFIXES):
        return True
    if DB_SIDECAR_RE.search(name):
        return True
    if any(part in {'__pycache__', '.git'} for part in pure.parts):
        return True
    for prefix in PRIVATE_ROOT_DIRS:
        if path == prefix or path.startswith(prefix + '/'):
            return True
    return False


def private_tracked_paths(paths):
    return [path for path in paths if is_private_path(path)]


# ---------------------------------------------------------------------------
# command execution / logging


class Runner:
    """Runs argv lists with no shell and records a redacted command log."""

    def __init__(self, logger, env=None, timeout=1800):
        self.logger = logger
        self.env = env
        self.timeout = timeout

    def run(self, argv, cwd=None, check=True, timeout=None, env=None):
        argv = [str(item) for item in argv]
        self.logger.info('执行: %s', redact(shlex.join(argv)))
        merged = None
        if self.env or env:
            merged = dict(os.environ)
            if self.env:
                merged.update(self.env)
            if env:
                merged.update(env)
        try:
            proc = subprocess.run(
                argv,
                cwd=None if cwd is None else str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=merged,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise UpdateFailed(f'找不到命令：{argv[0]}') from exc
        except subprocess.TimeoutExpired as exc:
            raise UpdateFailed(f'命令超时：{argv[0]}') from exc
        if check and proc.returncode != 0:
            detail = redact(tail_text(proc.stderr) or tail_text(proc.stdout))
            message = f'命令失败（返回码 {proc.returncode}）：{redact(shlex.join(argv))}'
            if detail:
                message += '\n' + detail
            raise UpdateFailed(message)
        return proc

    def output(self, argv, **kwargs):
        return self.run(argv, **kwargs).stdout.strip()


class GitRepo:
    def __init__(self, root, runner):
        self.root = Path(root)
        self.runner = runner

    @staticmethod
    def _env():
        # GIT_OPTIONAL_LOCKS=0 keeps read-only commands from refreshing the
        # index (and its mtime) during --check.
        return {'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}

    def _run(self, *args, check=True, timeout=600):
        return self.runner.run(
            ['git', '-c', f'safe.directory={self.root}', '-C', str(self.root), *args],
            check=check, timeout=timeout, env=self._env())

    def git(self, *args, check=True, timeout=600):
        return self._run(*args, check=check, timeout=timeout).stdout.strip()

    def head(self):
        return self.git('rev-parse', '--verify', 'HEAD^{commit}')

    def subject(self, sha):
        return self.git('show', '-s', '--format=%h %s', sha)

    def branch(self):
        proc = self._run('symbolic-ref', '-q', '--short', 'HEAD', check=False)
        return proc.stdout.strip()

    def tracked_paths(self, rev):
        output = self.git('ls-tree', '-r', '-z', '--name-only', rev)
        return [name for name in output.split('\0') if name]

    def dirty_tracked(self):
        return self.git('status', '--porcelain=v1', '--untracked-files=no')

    def fetch(self, remote):
        self.git('fetch', '--no-tags', remote, timeout=900)

    def ls_remote(self, remote, refname):
        proc = self._run('ls-remote', '--exit-code', remote, refname, check=False, timeout=300)
        if proc.returncode != 0:
            if proc.returncode == 2:
                raise Refused(f'上游不存在引用 {refname}（远程 {remote}）')
            raise Refused(f'无法读取远程 {remote}：'
                          f'{redact(tail_text(proc.stderr)) or proc.returncode}')
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            raise Refused(f'上游不存在引用 {refname}（远程 {remote}）')
        return lines[0].split()[0]

    def commit_count(self, old, new):
        output = self.git('rev-list', '--count', f'{old}..{new}')
        return int(output or 0)

    def log_oneline(self, old, new, limit=10):
        output = self.git('log', '--oneline', '--no-decorate', '-n', str(limit), f'{old}..{new}')
        return [line for line in output.splitlines() if line.strip()]

    def is_ancestor(self, old, new):
        proc = self._run('merge-base', '--is-ancestor', old, new, check=False, timeout=120)
        if proc.returncode == 128:
            raise Refused('无法比较提交历史（可能是浅克隆）；请先完整 fetch 后再更新')
        return proc.returncode == 0

    def archive(self, rev, destination):
        destination = Path(destination)
        ensure_dir(destination.parent, 0o700)
        self.git('archive', '--format=tar', f'--output={destination}', rev)
        os.chmod(destination, 0o600)
        return destination

    def object_exists(self, rev):
        proc = self._run('cat-file', '-e', f'{rev}^{{commit}}', check=False)
        return proc.returncode == 0

    def read_tree(self, rev):
        self.git('read-tree', rev)

    def update_head(self, rev, message):
        self.git('update-ref', '-m', message, 'HEAD', rev)

    def remote_names(self):
        return self.git('remote').split()


def split_remote_ref(repo, ref):
    """Map ``origin/main`` to (``origin``, ``refs/heads/main``) when possible."""
    for remote in sorted(repo.remote_names(), key=len, reverse=True):
        prefix = remote + '/'
        if ref.startswith(prefix):
            return remote, 'refs/heads/' + ref[len(prefix):]
    return None, None


class UpdateLock:
    """Non-blocking single-instance lock held for the whole update."""

    def __init__(self, path, logger):
        self.path = Path(path)
        self.logger = logger
        self.handle = None

    def __enter__(self):
        ensure_dir(self.path.parent, 0o755)
        self.handle = open(self.path, 'a+', encoding='utf-8')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise Refused(f'已有另一个更新进程持有锁（{self.path}），本次不执行')
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f'pid={os.getpid()} started={utc_iso()}\n')
        self.handle.flush()
        self.logger.info('已获取更新锁 %s', self.path)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


# ---------------------------------------------------------------------------
# file copying / archives


def copy_tree(source, destination):
    shutil.copytree(source, destination, symlinks=True)


def sanitize_mode(member):
    mode = member.mode & 0o777
    return 0o755 if mode & 0o111 else 0o644


def extract_tar(archive, destination, logger=None):
    """Extract a git archive, refusing links, devices and unsafe paths."""
    destination = Path(destination)
    ensure_dir(destination, 0o755)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            name = member.name
            pure = PurePosixPath(name)
            if name.startswith('/') or '..' in pure.parts or pure.is_absolute():
                raise UpdateFailed(f'归档包含不安全路径：{name}')
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise UpdateFailed(f'仓库包含符号链接或特殊文件，更新器拒绝处理：{name}')
            if not (member.isfile() or member.isdir()):
                raise UpdateFailed(f'归档包含不支持的文件类型：{name}')
        for member in tar:
            if member.isdir():
                target = destination / member.name
                ensure_dir(target, 0o755)
                continue
            target = destination / member.name
            ensure_parent(target)
            payload = tar.extractfile(member)
            if payload is None:
                raise UpdateFailed(f'无法读取归档成员：{member.name}')
            temp = target.with_name(target.name + '.acm-new')
            with payload, open(temp, 'wb') as handle:
                shutil.copyfileobj(payload, handle)
            os.chmod(temp, sanitize_mode(member))
            os.replace(temp, target)
    if logger:
        logger.debug('已解压归档 %s 到 %s', archive, destination)


def prepare_candidate(repo, sha, destination, logger):
    destination = Path(destination)
    if destination.exists():
        raise UpdateFailed(f'候选目录已存在：{destination}')
    ensure_dir(destination.parent, 0o755)
    destination.mkdir(mode=0o755)
    archive = destination.parent / (destination.name + '.tar')
    try:
        repo.archive(sha, archive)
        extract_tar(archive, destination, logger)
    finally:
        if archive.exists():
            archive.unlink()
    return destination


# ---------------------------------------------------------------------------
# backups


def is_db_file(name):
    return bool(DB_NAME_RE.search(name))


def snapshot_sqlite(source, destination):
    """Consistent SQLite copy including committed WAL content."""
    source_conn = sqlite3.connect(str(source), timeout=30)
    try:
        target_conn = sqlite3.connect(str(destination), timeout=30)
        try:
            source_conn.backup(target_conn)
            target_conn.commit()
        finally:
            target_conn.close()
    finally:
        source_conn.close()
    check = sqlite3.connect(str(destination), timeout=30)
    try:
        result = check.execute('PRAGMA integrity_check').fetchone()
    finally:
        check.close()
    if not result or result[0] != 'ok':
        raise UpdateFailed(f'数据备份完整性校验失败：{destination}（{result[0] if result else "无结果"}）')


def snapshot_databases(source, destination, logger):
    source, destination = Path(source), Path(destination)
    databases = []
    for base, _dirs, files in os.walk(source):
        for name in files:
            if not is_db_file(name):
                continue
            src = Path(base) / name
            rel = src.relative_to(source)
            dst = destination / rel
            if not dst.is_file():
                raise UpdateFailed(f'备份缺少数据库文件：{rel}')
            snapshot_sqlite(src, dst)
            for suffix in DB_SIDECARS:
                sidecar = Path(str(dst) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            databases.append(str(rel))
            logger.info('已快照数据库 %s（含 WAL）', rel)
    return databases


def verify_backup(source, destination, databases):
    """Ensure the backup copy really contains every non-sidecar source file."""
    source, destination = Path(source), Path(destination)
    database_paths = set(databases)
    for base, _dirs, files in os.walk(source, followlinks=False):
        for name in files:
            src = Path(base) / name
            rel = src.relative_to(source)
            rel_text = str(rel)
            if is_db_file(name):
                if rel_text not in database_paths:
                    raise UpdateFailed(f'备份漏掉数据库文件：{rel}')
                continue
            if DB_SIDECAR_RE.search(name):
                # Sidecars of snapshotted databases are intentionally replaced by
                # the consistent .backup() copy of the main database file.
                stripped = re.sub(r'(-wal|-shm|-journal)$', '', rel_text)
                if stripped in database_paths:
                    continue
            target = destination / rel
            if src.is_symlink():
                if not target.is_symlink() or os.readlink(target) != os.readlink(src):
                    raise UpdateFailed(f'备份缺少符号链接：{rel}')
                continue
            if not target.is_file():
                raise UpdateFailed(f'备份缺少文件：{rel}')
            if target.stat().st_size != src.stat().st_size:
                raise UpdateFailed(f'备份文件大小不一致：{rel}')
    return True


def credential_paths(project):
    project = Path(project)
    candidates = []
    for name in ('.env', 'account.toml', 'accounts.toml', 'accounts.web.toml'):
        path = project / name
        if path.is_file():
            candidates.append(path)
    for path in sorted(project.glob('.env.*')):
        if path.name != '.env.example' and path.is_file():
            candidates.append(path)
    return candidates


def backup_data(project, data_dir, backup_dir, logger):
    data_dir = Path(data_dir)
    backup_dir = Path(backup_dir)
    if not data_dir.is_dir():
        raise UpdateFailed(f'数据目录不存在，拒绝无备份更新：{data_dir}')
    ensure_dir(backup_dir, 0o700)
    os.chmod(backup_dir, 0o700)
    if backup_dir.exists():
        # The updater writes its manifest and code archive here before stopping
        # the service; anything else means we would overwrite a previous backup.
        unexpected = [path.name for path in backup_dir.iterdir()
                      if path.name not in {'worktree.tar', 'manifest.json'}]
        if unexpected:
            raise UpdateFailed(f'备份目录已存在其他内容，拒绝覆盖：{backup_dir}')
    destination = backup_dir / 'data'
    copy_tree(data_dir, destination)
    databases = snapshot_databases(data_dir, destination, logger)
    verify_backup(data_dir, destination, databases)
    stat_info = data_dir.stat()
    credentials = []
    for path in credential_paths(project):
        rel = path.relative_to(project)
        flat = str(rel).replace('/', '__')
        target = backup_dir / 'credentials' / flat
        ensure_dir(target.parent, 0o700)
        shutil.copy2(path, target)
        os.chmod(target, 0o600)
        info = target.stat()
        original = path.stat()
        credentials.append({
            'path': str(rel),
            'backup': flat,
            'uid': original.st_uid,
            'gid': original.st_gid,
            'size': info.st_size,
        })
    file_count = 0
    for _base, _dirs, files in os.walk(destination):
        file_count += len(files)
    logger.info('已备份数据目录 %s（%s 个文件，%s 个数据库）与 %s 个配置文件',
                data_dir, file_count, len(databases), len(credentials))
    return {
        'data_dir': str(data_dir),
        'data_uid': stat_info.st_uid,
        'data_gid': stat_info.st_gid,
        'data_mode': stat.S_IMODE(stat_info.st_mode),
        'databases': databases,
        'file_count': file_count,
        'credentials': credentials,
    }


def restore_credentials(project, backup_dir, credentials):
    project = Path(project)
    for entry in credentials:
        source = Path(backup_dir) / 'credentials' / entry['backup']
        if not source.is_file():
            raise UpdateFailed(f'缺少配置备份：{entry["path"]}')
        target = project / entry['path']
        ensure_parent(target)
        shutil.copy2(source, target)
        os.chmod(target, 0o600)
        chown_if_root(target, entry['uid'], entry['gid'])


def restore_data(data_dir, backup_dir, failed_dir, uid, gid):
    data_dir = Path(data_dir)
    backup = Path(backup_dir) / 'data'
    if not backup.is_dir():
        raise UpdateFailed(f'缺少数据备份，无法恢复：{backup}')
    failed_dir = Path(failed_dir)
    ensure_dir(failed_dir, 0o700)
    failed_data = failed_dir / 'data'
    if data_dir.exists():
        if failed_data.exists():
            shutil.rmtree(failed_data)
        shutil.move(str(data_dir), str(failed_data))
    copy_tree(backup, data_dir)
    chown_tree(data_dir, uid, gid)


# ---------------------------------------------------------------------------
# health checks


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_health(url, host_header=None, timeout=5):
    headers = {}
    if host_header:
        headers['Host'] = host_header
    request = urllib.request.Request(url, headers=headers, method='GET')
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            body = response.read(65536)
    except urllib.error.HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, 'reason', exc)
        return False, f'连接失败：{reason}'
    if status != 200:
        return False, f'HTTP {status}'
    try:
        payload = json.loads(body.decode('utf-8', 'replace'))
    except ValueError:
        return False, '响应不是 JSON'
    if not isinstance(payload, dict) or payload.get('status') != 'ok':
        return False, '响应状态不是 ok'
    return True, 'HTTP 200 status=ok'


def wait_health(url, host_header, timeout, interval=2.0, alive=None, logger=None):
    deadline = time.monotonic() + timeout
    last = '尚未检查'
    while True:
        ok, detail = probe_health(url, host_header)
        if ok:
            # A stale listener (old container/process) can still answer /api/health.
            if alive is not None and not alive():
                raise UpdateFailed(
                    f'目标服务未运行（健康响应可能来自旧服务）：{detail}')
            if logger:
                logger.info('健康检查通过：%s', redact(url))
            return True, detail
        last = detail
        if alive is not None and not alive():
            raise UpdateFailed(f'目标服务未保持运行（{detail}）')
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    raise UpdateFailed(f'健康检查超时（{timeout:g}s）：{redact(url)}（{last}）')


# ---------------------------------------------------------------------------
# installation detection


@dataclass
class Install:
    mode: str
    service: str = ''
    compose_files: list = field(default_factory=list)
    compose_service: str = 'web'
    image: str = ''
    build_target: str | None = None
    volumes: list = field(default_factory=list)
    was_active: bool = False
    details: dict = field(default_factory=dict)


@dataclass
class UpdateConfig:
    project: Path
    mode: str = 'auto'
    ref: str = DEFAULT_REF
    check: bool = False
    service: str | None = None
    compose_service: str = 'web'
    compose_files: list = field(default_factory=list)
    health_url: str = DEFAULT_HEALTH_URL
    public_origin: str | None = None
    health_timeout: float = 120.0
    health_interval: float = 2.0
    data_dir: Path | None = None
    python: str | None = None
    skip_frontend_build: bool = False
    verbose: bool = False


@dataclass
class Plan:
    repo: GitRepo
    install: Install
    old_sha: str
    target_sha: str
    target_known: bool
    up_to_date: bool
    commit_count: int = 0
    commits: list = field(default_factory=list)
    old_paths: list = field(default_factory=list)
    new_paths: list = field(default_factory=list)
    removed_paths: list = field(default_factory=list)
    added_paths: list = field(default_factory=list)
    data_dir: Path | None = None
    public_origin_host: str | None = None


def resolve_compose_files(project, configured):
    project = Path(project)
    files = []
    if configured:
        for item in configured:
            path = Path(item)
            files.append(path if path.is_absolute() else project / path)
    else:
        for name in ('compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml'):
            path = project / name
            if path.is_file():
                files.append(path)
                break
    for path in files:
        if not path.is_file():
            raise Refused(f'compose 文件不存在：{path}')
    return files


def project_env_file(project):
    path = Path(project) / '.env'
    return path if path.is_file() else None


def compose_argv(files, env_file=None, project_directory=None):
    argv = ['docker', 'compose']
    for path in files:
        argv.extend(['-f', str(path)])
    if project_directory:
        argv.extend(['--project-directory', str(project_directory)])
    if env_file:
        argv.extend(['--env-file', str(env_file)])
    return argv


def compose_config_json(files, project, runner, env_file=None, project_directory=None):
    proc = runner.run(compose_argv(files, env_file, project_directory) + ['config', '--format', 'json'],
                      cwd=project, timeout=180)
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise UpdateFailed('无法解析 docker compose config 输出') from exc


def compose_ids(files, project, service, runner, all_containers=False, env_file=None):
    argv = compose_argv(files, env_file) + ['ps', '-q']
    if all_containers:
        argv.append('-a')
    argv.append(service)
    proc = runner.run(argv, cwd=project, check=False, timeout=120)
    if proc.returncode != 0:
        raise Refused('无法查询 Compose 服务状态：' + (tail_text(proc.stderr) or 'Docker 守护进程不可用'))
    return proc.stdout.strip()


def normalize_volumes(volumes):
    items = []
    for item in volumes or []:
        if isinstance(item, dict):
            items.append(json.dumps(item, sort_keys=True, ensure_ascii=False))
        else:
            items.append(str(item))
    return sorted(items)


def compose_container_image_id(container_id, runner):
    return runner.output(['docker', 'inspect', '--format', '{{.Image}}', container_id], timeout=120)


def docker_image_id(tag, runner):
    return runner.output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], timeout=120)


def docker_image_exists(tag, runner):
    return runner.run(['docker', 'image', 'inspect', tag], check=False, timeout=120).returncode == 0


def docker_tag(source, destination, runner):
    runner.run(['docker', 'tag', source, destination], timeout=120)


def image_repository(image):
    head, sep, last = image.rpartition('/')
    if ':' in last:
        last = last.split(':', 1)[0]
    return (head + '/' if sep else '') + last


def systemd_show(unit, runner):
    proc = runner.run(
        ['systemctl', 'show', unit, '--property=LoadState,ActiveState,WorkingDirectory,ExecStart,FragmentPath,MainPID', '--no-pager'],
        check=False,
        timeout=60,
    )
    info = {}
    for line in (proc.stdout or '').splitlines():
        key, _, value = line.partition('=')
        info[key.strip()] = value.strip()
    return info


def systemd_state(unit, runner):
    proc = runner.run(['systemctl', 'is-active', unit], check=False, timeout=60)
    return (proc.stdout or '').strip() or 'unknown'


def systemd_main_pid(unit, runner):
    info = systemd_show(unit, runner)
    try:
        return int(info.get('MainPID') or 0)
    except ValueError:
        return 0


def unit_runtime_check(info, project):
    """Only accept units that clearly execute this project's ``.venv``.

    Swapping ``.venv`` can only upgrade a service whose ExecStart points into
    that virtualenv.  The unit file itself is never modified.
    """
    project = Path(project)
    working = info.get('WorkingDirectory') or ''
    if working != str(project):
        return False, f'WorkingDirectory 不是项目目录（{working or "未设置"}）'
    exec_start = info.get('ExecStart') or ''
    paths = re.findall(r'path=([^ ;]+)', exec_start)
    if not paths:
        return False, '无法从 ExecStart 解析出可执行文件'
    executable = paths[0]
    venv_bin = str(project / '.venv' / 'bin')
    if not (executable == venv_bin or executable.startswith(venv_bin + '/')):
        return False, f'ExecStart 未使用 {venv_bin} 下的运行环境（实际 {executable}）'
    name = PurePosixPath(executable).name
    argv_text = ' '.join(re.findall(r'argv\[\]=([^;]+)', exec_start))
    if name in {'acm-web', 'acm-web.exe'}:
        return True, ''
    if name.startswith('python') and re.search(r'(?<![\w.-])acmweb(?:\.main)?\b', argv_text):
        return True, ''
    return False, (f'{executable} 不是受支持的服务入口'
                   '（仅支持 .venv/bin/acm-web 或 .venv/bin/python -m acmweb.main）')


def detect_systemd(project, cfg, runner, logger, strict=True):
    if not shutil.which('systemctl'):
        if strict:
            raise Refused('未找到 systemctl，无法以 systemd 模式更新')
        return None
    if cfg.service:
        unit = validate_unit(cfg.service)
        info = systemd_show(unit, runner)
        if info.get('LoadState') not in {'loaded'} and not info.get('FragmentPath'):
            raise Refused(f'systemd 单元不存在：{unit}')
        supported, reason = unit_runtime_check(info, project)
        if not supported:
            raise Refused(f'systemd 单元 {unit} 的运行命令不受支持：{reason}；'
                          f'更新器不会修改单元文件，请先把 ExecStart 指向 '
                          f'{Path(project) / ".venv" / "bin" / "acm-web"} 后再更新')
        return Install(mode='systemd', service=unit, was_active=info.get('ActiveState') == 'active',
                       details=info)
    proc = runner.run(
        ['systemctl', 'list-units', '--type=service', '--state=running', '--no-legend', '--plain', '--no-pager'],
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        if strict:
            raise Refused('无法列出 systemd 服务：' + (tail_text(proc.stderr) or str(proc.returncode)))
        logger.debug('systemctl list-units 失败：%s', tail_text(proc.stderr))
        return None
    matches = []
    unsupported = []
    for line in (proc.stdout or '').splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0]
        if not unit.endswith('.service'):
            continue
        info = systemd_show(unit, runner)
        if info.get('ActiveState') != 'active':
            continue
        supported, reason = unit_runtime_check(info, project)
        if supported:
            matches.append(Install(mode='systemd', service=unit, was_active=True, details=info))
        elif (info.get('WorkingDirectory') or '') == str(Path(project)):
            unsupported.append(f'{unit}（{reason}）')
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise Refused('多个 systemd 服务引用此目录：'
                      + ', '.join(item.service for item in matches)
                      + '；请用 --service 指定要更新的服务')
    if unsupported:
        raise Refused('检测到工作目录为本项目、但运行命令不受支持的 systemd 服务：'
                      + '；'.join(unsupported)
                      + f'。请把 ExecStart 指向 {Path(project) / ".venv" / "bin" / "acm-web"}'
                      '（更新器不会修改单元文件）')
    if strict:
        raise Refused('未找到引用此目录且正在运行的 systemd 服务；更新器不会新建部署')
    return None


def detect_docker(project, cfg, runner, logger, strict=True):
    if not shutil.which('docker'):
        if strict:
            raise Refused('未找到 docker，无法以 Docker Compose 模式更新')
        return None
    files = resolve_compose_files(project, cfg.compose_files)
    if not files:
        if strict:
            raise Refused('未找到 compose 文件；请用 --compose-file 指定')
        return None
    version = runner.run(['docker', 'compose', 'version'], check=False, timeout=60)
    if version.returncode != 0:
        raise Refused('需要 Docker Compose v2（docker compose）；请先安装或启用')
    try:
        config = compose_config_json(files, project, runner, env_file=project_env_file(project))
    except UpdateFailed as exc:
        if strict:
            raise Refused(f'无法解析 compose 配置：{exc}') from exc
        logger.debug('compose 配置不可用：%s', exc)
        return None
    services = config.get('services') or {}
    service = cfg.compose_service
    if service not in services:
        raise Refused(f'compose 中没有服务 {service!r}；请用 --compose-service 指定')
    try:
        existing = compose_ids(files, project, service, runner, all_containers=True,
                               env_file=project_env_file(project))
    except Refused:
        if strict:
            raise
        return None
    if not existing:
        if strict:
            raise Refused('未检测到现有 Compose 部署（没有对应容器）；更新器不会新建部署')
        return None
    image = services[service].get('image')
    if not image:
        raise Refused(f'compose 服务 {service!r} 未显式设置 image:，无法保留旧镜像用于回滚')
    build = services[service].get('build')
    target = build.get('target') if isinstance(build, dict) else None
    running = compose_ids(files, project, service, runner, env_file=project_env_file(project))
    return Install(mode='docker', service=service, compose_files=files, compose_service=service,
                   image=image, build_target=target,
                   volumes=normalize_volumes(services[service].get('volumes')),
                   was_active=bool(running))


def detect_install(project, cfg, runner, logger):
    if cfg.mode == 'systemd':
        install = detect_systemd(project, cfg, runner, logger, strict=True)
        if install is None:
            raise Refused('未找到引用此目录的 systemd 服务；更新器不会新建部署')
        return install
    if cfg.mode == 'docker':
        install = detect_docker(project, cfg, runner, logger, strict=True)
        if install is None:
            raise Refused('未检测到现有 Compose 部署；更新器不会新建部署')
        return install
    systemd_install = detect_systemd(project, cfg, runner, logger, strict=False)
    try:
        docker_install = detect_docker(project, cfg, runner, logger, strict=False)
    except Refused as exc:
        logger.debug('自动识别时跳过 Docker 部署：%s', exc)
        docker_install = None
    found = [item for item in (systemd_install, docker_install) if item is not None]
    if len(found) == 1:
        return found[0]
    if len(found) == 2:
        raise Refused(
            '同时检测到 systemd 服务 {s} 与 Docker Compose 服务 {c}，无法安全判断；'
            '请用 --mode systemd 或 --mode docker 明确指定'.format(
                s=systemd_install.service, c=docker_install.service))
    raise Refused('无法自动识别部署方式；请用 --mode systemd 或 --mode docker 明确指定')


# ---------------------------------------------------------------------------
# planning / preflight


def detect_data_dir(cfg, install, project):
    project = Path(project)
    if cfg.data_dir:
        path = Path(cfg.data_dir).expanduser()
        return path if path.is_absolute() else project / path
    if install.mode == 'docker':
        return project / 'data'
    env = read_dotenv(project / '.env', {'ACM_DATA_DIR'})
    if env.get('ACM_DATA_DIR'):
        path = Path(env['ACM_DATA_DIR']).expanduser()
        return path if path.is_absolute() else project / path
    legacy = project / 'data' / 'account-history'
    if (legacy / 'accounts.sqlite3').is_file():
        return legacy
    return project / 'data'


def resolve_public_origin_host(cfg, project):
    origin = cfg.public_origin
    if not origin:
        origin = read_dotenv(project / '.env', {'ACM_PUBLIC_ORIGIN'}).get('ACM_PUBLIC_ORIGIN', '')
    if not origin:
        return None
    parts = urlsplit(origin)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise UsageError('ACM_PUBLIC_ORIGIN 必须是 HTTPS 站点来源，不能包含路径、账号或查询串')
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        raise UsageError('ACM_PUBLIC_ORIGIN 不能包含路径、查询串或片段')
    return parts.netloc


def check_repo(project, repo, logger):
    toplevel = repo.git('rev-parse', '--show-toplevel')
    if Path(toplevel).resolve() != project:
        raise Refused(f'项目目录不是 Git 仓库根目录：{project}')
    if not repo.branch():
        raise Refused('当前处于游离 HEAD，无法安全快进；请先切换到跟踪上游的分支')
    dirty = repo.dirty_tracked()
    if dirty:
        lines = dirty.splitlines()
        preview = '\n'.join('  ' + line for line in lines[:10])
        raise Refused('工作区或索引中还有未提交的已跟踪改动，更新器不会覆盖用户变更：\n' + preview)
    head_names = repo.tracked_paths('HEAD')
    bad = private_tracked_paths(head_names)
    if bad:
        raise Refused('当前仓库已跟踪私有文件，更新前请先移除：' + ', '.join(bad[:5]))


def resolve_target(cfg, repo, project, fetch):
    remote, refname = split_remote_ref(repo, cfg.ref)
    if fetch:
        if remote:
            repo.fetch(remote)
            logger = repo.runner.logger
            logger.info('已 fetch %s', remote)
        return repo.git('rev-parse', '--verify', cfg.ref + '^{commit}'), remote
    if remote:
        return repo.ls_remote(remote, refname), remote
    try:
        return repo.git('rev-parse', '--verify', cfg.ref + '^{commit}'), None
    except UpdateFailed as exc:
        raise Refused(f'无法解析 --ref {cfg.ref}：{exc}') from exc


def build_plan(cfg, runner, logger, fetch):
    project = Path(cfg.project)
    repo = GitRepo(project, runner)
    check_repo(project, repo, logger)
    install = detect_install(project, cfg, runner, logger)
    if install.mode == 'systemd' and not is_root():
        raise Refused('systemd 更新需要 root 权限（停止/启动服务、备份数据与切换 venv）；请用 sudo 运行')
    old_sha = repo.head()
    target_sha, remote = resolve_target(cfg, repo, project, fetch)
    target_known = repo.object_exists(target_sha)
    data_dir = detect_data_dir(cfg, install, project)
    public_host = resolve_public_origin_host(cfg, project)
    plan = Plan(repo=repo, install=install, old_sha=old_sha, target_sha=target_sha,
                target_known=target_known, up_to_date=target_sha == old_sha,
                data_dir=data_dir, public_origin_host=public_host)
    if plan.up_to_date:
        return plan
    if target_known:
        plan.old_paths = repo.tracked_paths(old_sha)
        plan.new_paths = repo.tracked_paths(target_sha)
        if not repo.is_ancestor(old_sha, target_sha):
            raise Refused('上游目标提交不是当前提交的后代（本地与上游已分叉）；更新器只允许快进，'
                          '请先人工合并或重置工作分支')
        bad = private_tracked_paths(plan.new_paths)
        if bad:
            raise Refused('目标版本把私有路径变成 Git 跟踪文件，更新器拒绝继续：' + ', '.join(bad[:10]))
        old_set, new_set = set(plan.old_paths), set(plan.new_paths)
        plan.added_paths = [p for p in plan.new_paths if p not in old_set]
        plan.removed_paths = [p for p in plan.old_paths if p not in new_set]
        collisions = [p for p in plan.added_paths if (project / p).exists() or (project / p).is_symlink()]
        if collisions:
            raise Refused('目标版本新增的文件会覆盖本地未跟踪文件，更新器拒绝继续：'
                          + ', '.join(collisions[:10]))
        plan.commit_count = repo.commit_count(old_sha, target_sha)
        plan.commits = repo.log_oneline(old_sha, target_sha)
    if install.mode == 'docker':
        if not (project / 'data').is_dir():
            raise Refused(f'Compose 部署的数据目录不存在：{project / "data"}')
        if not docker_image_exists(install.image, runner):
            raise Refused(f'当前镜像 {install.image} 不存在，缺少回滚镜像；请先恢复部署镜像再更新')
    if install.mode == 'systemd':
        venv_python = project / '.venv' / 'bin' / 'python'
        if not venv_python.exists():
            raise Refused(f'未找到现有虚拟环境 {venv_python}；请确认这是原生 systemd 部署')
        if not cfg.skip_frontend_build and not shutil.which('npm'):
            raise Refused('原生部署前端构建需要 Node.js/npm；请先安装 Node.js 22+ 后重试，或确认现有 dist 后使用 --skip-frontend-build')
        if cfg.skip_frontend_build and not (project / 'frontend/dist/index.html').is_file():
            raise Refused('使用 --skip-frontend-build 时现有 frontend/dist/index.html 必须存在')
        if not cfg.python and not shutil.which('python3'):
            raise Refused('创建候选虚拟环境需要 python3')
        if cfg.python and not Path(cfg.python).exists() and not shutil.which(cfg.python):
            raise Refused(f'--python 指定的解释器不存在：{cfg.python}')
    if not data_dir.is_dir():
        raise Refused(f'数据目录不存在：{data_dir}（可用 --data-dir 指定）')
    parse_health_url(cfg.health_url)
    return plan


def recheck_before_stop(cfg, plan, logger):
    """Re-validate the working tree after a potentially long build step.

    Users can edit files while images/frontends/venvs are being prepared; the
    update must not stop the service or overwrite those edits.
    """
    repo = plan.repo
    project = Path(cfg.project)
    logger.info('停止服务前复核：HEAD 未变化、工作区干净、无新增冲突')
    if not repo.object_exists(plan.target_sha):
        raise Refused('目标提交在构建期间已不可用；未停止服务，请重新运行更新')
    head = repo.head()
    if head != plan.old_sha:
        raise Refused(f'构建期间分支已变化（当前 {head[:12]}，预期 {plan.old_sha[:12]}）；'
                      '未停止服务，也未覆盖任何文件，请重新运行更新')
    dirty = repo.dirty_tracked()
    if dirty:
        raise Refused('构建期间出现未提交的已跟踪改动，未停止服务，也未覆盖用户变更：\n'
                      + '\n'.join('  ' + line for line in dirty.splitlines()[:10]))
    bad = private_tracked_paths(repo.tracked_paths(head))
    if bad:
        raise Refused('构建期间仓库开始跟踪私有文件，未停止服务：' + ', '.join(bad[:5]))
    collisions = [path for path in plan.added_paths
                  if (project / path).exists() or (project / path).is_symlink()]
    if collisions:
        raise Refused('构建期间新增了与目标版本冲突的未跟踪文件，未停止服务：'
                      + ', '.join(collisions[:10]))
    if repo.tracked_paths(plan.target_sha) != plan.new_paths:
        raise Refused('目标提交内容在构建期间发生变化；未停止服务，请重新运行更新')


def print_plan(plan, cfg, check):
    install = plan.install
    print('预检结果' + ('（--check：未修改任何文件）' if check else ''))
    print(f'  项目目录：{cfg.project}')
    if install.mode == 'docker':
        files = ', '.join(str(path) for path in install.compose_files)
        print(f'  部署方式：Docker Compose（文件 {files}，服务 {install.service}，镜像 {install.image}）')
    else:
        print(f'  部署方式：systemd（单元 {install.service}）')
    print(f'  当前提交：{plan.repo.subject(plan.old_sha)}')
    if plan.up_to_date:
        print('  目标版本：与当前提交相同，已是最新版本')
        return
    if plan.target_known:
        print(f'  目标提交：{plan.repo.subject(plan.target_sha)}（{cfg.ref}）')
        print(f'  需要更新：{plan.commit_count} 个提交')
        for line in plan.commits[:10]:
            print(f'    - {line}')
        if plan.commit_count > len(plan.commits[:10]):
            print('    - ...')
        print(f'  文件变动：新增 {len(plan.added_paths)}，删除 {len(plan.removed_paths)}')
    else:
        print(f'  目标提交：{plan.target_sha[:12]}（{cfg.ref}，本地尚无该对象）')
        print('  本地尚无目标提交对象，无法在执行前核对文件清单；'
              '实际更新会在 fetch 后、停止服务前完成核对')
    print(f'  数据目录：{plan.data_dir}')
    host = f'（Host: {plan.public_origin_host}）' if plan.public_origin_host else ''
    print(f'  健康检查：{redact(cfg.health_url)}{host}')
    print(f'  回滚：停止服务后先把 data、.env、账号配置备份到 {STATE_DIRNAME}/backups/<时间>，'
          '新版本健康检查失败时自动恢复')


# ---------------------------------------------------------------------------
# building the candidate


def build_frontend(cfg, candidate, project, runner, logger):
    frontend = Path(candidate) / 'frontend'
    if not (frontend / 'package.json').is_file():
        logger.info('候选版本不含前端工程，跳过前端构建')
        return None
    if cfg.skip_frontend_build:
        logger.warning('按 --skip-frontend-build 沿用现有前端构建产物，未重新构建')
        return None
    npm = shutil.which('npm')
    if not npm:
        raise Refused('前端构建需要 Node.js/npm；请先安装 Node.js 22+ 后重试，或确认现有 dist 后使用 --skip-frontend-build')
    if not (frontend / 'package-lock.json').is_file():
        raise Refused('目标版本缺少 frontend/package-lock.json，无法可靠安装前端依赖')
    # Build from the candidate lockfile every time. This repairs missing or
    # stale local node_modules and keeps downloaded packages isolated from the
    # currently running version until the candidate is ready.
    logger.info('按目标版本 package-lock.json 在候选目录安装前端依赖（npm ci）')
    runner.run([npm, 'ci', '--no-audit', '--no-fund'], cwd=frontend, timeout=3600)
    runner.run([npm, 'run', 'build'], cwd=frontend, timeout=3600)
    dist = frontend / 'dist'
    if not (dist / 'index.html').is_file():
        raise UpdateFailed('前端构建未生成 dist/index.html')
    logger.info('候选前端构建完成：%s', dist)
    return dist


def build_candidate_venv(cfg, candidate, destination, project, runner, logger):
    destination = Path(destination)
    if destination.exists():
        raise UpdateFailed(f'候选虚拟环境已存在：{destination}')
    ensure_dir(destination.parent, 0o755)
    python = cfg.python or shutil.which('python3') or sys.executable
    old_venv = Path(project) / '.venv'
    owner = Path(project).stat()
    logger.info('创建候选虚拟环境 %s（来源：%s）', destination, python)
    runner.run([python, '-m', 'venv', str(destination)], cwd=project, timeout=600)
    venv_python = destination / 'bin' / 'python'
    if not venv_python.exists():
        raise UpdateFailed('创建候选虚拟环境失败（缺少 bin/python）')
    constraints = Path(candidate) / 'requirements.lock'
    argv = [str(venv_python), '-m', 'pip', 'install', '--disable-pip-version-check',
            '--no-input', '--no-cache-dir']
    if constraints.is_file():
        argv.extend(['-c', str(constraints)])
    # Install the real candidate tree; never run pip inside the old venv.
    argv.append(str(candidate))
    logger.info('在候选虚拟环境中安装候选代码与锁定依赖')
    runner.run(argv, cwd=candidate, timeout=3600,
               env={'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONNOUSERSITE': '1'})
    browser_lock = Path(candidate) / 'requirements-browser.lock'
    if browser_lock.is_file() and list(old_venv.glob('lib/python*/site-packages/playwright')):
        logger.info('现有环境包含服务端登录浏览器扩展，安装对应的浏览器依赖')
        runner.run([str(venv_python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--no-input', '-r', str(browser_lock), '-c', str(browser_lock)],
                   cwd=candidate, timeout=3600)
    check = runner.run([str(venv_python), '-c', 'import acmweb'], cwd=candidate,
                       check=False, timeout=180)
    if check.returncode != 0:
        raise UpdateFailed('候选虚拟环境无法导入 acmweb：' + (tail_text(check.stderr) or '未知错误'))
    make_world_readable(destination)
    chown_tree(destination, owner.st_uid, owner.st_gid)
    # Only check the directories the updater controls; the project itself is
    # the operator's responsibility.
    verify_world_readable(destination, check_ancestors_up_to=destination.parent.parent)
    logger.info('候选虚拟环境就绪：%s', destination)
    return destination


def candidate_compose_files(cfg, install, candidate):
    """Map this deployment's compose files onto the candidate source tree."""
    project = Path(cfg.project)
    candidate = Path(candidate)
    mapped = []
    for path in install.compose_files:
        path = Path(path)
        try:
            relative = path.relative_to(project)
        except ValueError:
            mapped.append(path)
            continue
        replacement = candidate / relative
        mapped.append(replacement if replacement.is_file() else path)
    return mapped


def resolve_candidate_service(cfg, install, candidate, runner, logger):
    """Resolve the target version's compose service using its own files."""
    files = candidate_compose_files(cfg, install, candidate)
    # Relative bind mounts (./data, ./account.toml) must resolve against the
    # real project root, not the candidate directory.
    config = compose_config_json(files, Path(candidate), runner,
                                 env_file=project_env_file(cfg.project),
                                 project_directory=cfg.project)
    services = config.get('services') or {}
    if install.service not in services:
        raise Refused(f'目标版本的 compose 文件不再包含服务 {install.service!r}；'
                      '更新器不会擅自新建或改名服务')
    service = services[install.service]
    image = service.get('image')
    if not image:
        raise Refused(f'目标版本的 compose 服务 {install.service!r} 未显式设置 image:，'
                      '无法保留旧镜像用于回滚')
    build = service.get('build')
    target = build.get('target') if isinstance(build, dict) else None
    return {
        'files': files,
        'image': image,
        'build_target': target,
        'volumes': normalize_volumes(service.get('volumes')),
    }


def build_candidate_image(cfg, runner, install, candidate, state, stamp, logger):
    if not docker_image_exists(install.image, runner):
        raise Refused(f'当前镜像 {install.image} 不存在，缺少回滚镜像；请先恢复部署镜像再更新')
    target = resolve_candidate_service(cfg, install, candidate, runner, logger)
    if normalize_volumes(install.volumes) != target['volumes']:
        raise Refused('目标版本修改了 Compose 服务的数据挂载（volumes），'
                      '更新器不支持自动迁移数据；请人工处理后重新更新')
    target_image = target['image']
    candidate_tag = f'{image_repository(target_image)}:update-candidate-{stamp}'
    rollback_tag = f'{image_repository(install.image)}:update-rollback-{stamp}'
    build = {'context': str(candidate)}
    if target['build_target']:
        build['target'] = target['build_target']
    override = {'services': {install.service: {'image': candidate_tag, 'build': build}}}
    override_path = Path(state) / 'build' / f'compose.build-{stamp}.yaml'
    ensure_dir(override_path.parent, 0o700)
    write_json_atomic(override_path, override, 0o600)
    files = list(target['files']) + [override_path]
    logger.info('在旧服务运行期间按目标 compose 构建新镜像 %s（旧镜像 %s 保持不变）',
                candidate_tag, install.image)
    runner.run(compose_argv(files, project_env_file(cfg.project), project_directory=cfg.project)
               + ['build', install.service], cwd=candidate, timeout=7200)
    if not docker_image_exists(candidate_tag, runner):
        raise UpdateFailed(f'镜像构建后未找到 {candidate_tag}')
    image_id = docker_image_id(candidate_tag, runner)
    logger.info('新镜像 ID：%s（目标 image: %s）', image_id, target_image)
    return {
        'candidate_tag': candidate_tag,
        'rollback_tag': rollback_tag,
        'target_image': target_image,
        'new_image_id': image_id,
        'override_file': str(override_path),
        'old_image_id': docker_image_id(install.image, runner),
    }


# ---------------------------------------------------------------------------
# switching and rollback


@dataclass
class SwitchContext:
    cfg: UpdateConfig
    plan: Plan
    state: Path
    backup_dir: Path
    failed_dir: Path
    candidate_dir: Path
    stamps: dict
    candidate_venv: Path = None
    candidate_dist: Path = None
    images: dict = field(default_factory=dict)
    code_switched: bool = False
    dist_switched: bool = False
    venv_switched: bool = False
    refs_switched: bool = False
    image_switched: bool = False
    service_started: bool = False
    old_venv_link: str = None
    old_pid: int = 0
    manifest: dict = field(default_factory=dict)


def switch_code(ctx, logger):
    project = Path(ctx.cfg.project)
    old_set, new_set = set(ctx.plan.old_paths), set(ctx.plan.new_paths)
    owner = project.stat()
    ctx.code_switched = True
    removed = [name for name in ctx.plan.old_paths if name not in new_set]
    for name in sorted(removed, key=lambda item: item.count('/'), reverse=True):
        path = project / name
        if path.is_symlink() or path.is_file():
            path.unlink()
    for name in ctx.plan.new_paths:
        source = Path(ctx.candidate_dir) / name
        if not source.is_file():
            raise UpdateFailed(f'候选源码缺少跟踪文件：{name}')
        target = project / name
        if target.is_dir():
            raise UpdateFailed(f'目标路径是目录，无法写入文件：{name}')
        ensure_parent(target)
        temp = target.with_name(target.name + '.acm-update-new')
        shutil.copy2(source, temp)
        os.chmod(temp, sanitize_mode_for_path(source))
        os.replace(temp, target)
        chown_if_root(target, owner.st_uid, owner.st_gid)
    for name in removed:
        parent = (project / name).parent
        while parent != project and parent.is_dir():
            try:
                next(parent.iterdir())
            except StopIteration:
                parent.rmdir()
                parent = parent.parent
            else:
                break
    logger.info('已切换代码：%s 个跟踪文件（删除 %s 个）', len(ctx.plan.new_paths), len(removed))


def sanitize_mode_for_path(path):
    mode = os.stat(path).st_mode
    return 0o755 if mode & 0o111 else 0o644


def switch_dist(ctx, logger):
    if ctx.candidate_dist is None:
        return
    project = Path(ctx.cfg.project)
    dist = project / 'frontend' / 'dist'
    staged = project / 'frontend' / 'dist.acm-update-new'
    if staged.exists():
        shutil.rmtree(staged)
    ctx.dist_switched = True
    copy_tree(ctx.candidate_dist, staged)
    chmod_tree(staged, 0o755, 0o644)
    old = Path(ctx.backup_dir) / 'frontend-dist'
    if old.exists():
        shutil.rmtree(old)
    if dist.exists():
        os.replace(dist, old)
    os.replace(staged, dist)
    owner = project.stat()
    chown_tree(dist, owner.st_uid, owner.st_gid)
    chmod_tree(dist, 0o755, 0o644)
    logger.info('已切换前端构建产物')


def switch_venv(ctx, logger):
    if ctx.candidate_venv is None:
        return
    project = Path(ctx.cfg.project)
    venv = project / '.venv'
    previous = Path(ctx.backup_dir) / 'venv-previous'
    ctx.venv_switched = True
    if venv.is_symlink():
        ctx.old_venv_link = os.readlink(venv)
        venv.unlink()
    elif venv.exists():
        if previous.exists():
            raise UpdateFailed(f'旧虚拟环境备份位置已存在：{previous}')
        os.replace(venv, previous)
    os.symlink(ctx.candidate_venv, venv, target_is_directory=True)
    logger.info('已把 .venv 指向候选虚拟环境 %s', ctx.candidate_venv)


def switch_refs(ctx, logger):
    repo = ctx.plan.repo
    ctx.refs_switched = True
    repo.read_tree(ctx.plan.target_sha)
    repo.update_head(ctx.plan.target_sha,
                     f'acm-update: {ctx.plan.old_sha[:12]} -> {ctx.plan.target_sha[:12]}')
    restore_repo_ownership(repo, ctx)
    logger.info('已把 Git 分支快进到 %s', ctx.plan.target_sha[:12])


def restore_repo_ownership(repo, ctx, owner=None):
    """Keep .git writable for the operator when the update runs as root."""
    if os.geteuid() != 0:
        return
    project = Path(repo.root)
    owner = owner or project.stat()
    git_dir = project / '.git'
    if git_dir.exists():
        chown_tree(git_dir, owner.st_uid, owner.st_gid)


def restore_code(ctx, logger):
    project = Path(ctx.cfg.project)
    repo = ctx.plan.repo
    new_set, old_set = set(ctx.plan.new_paths), set(ctx.plan.old_paths)
    if ctx.code_switched:
        for name in sorted(new_set - old_set, key=lambda item: item.count('/'), reverse=True):
            path = project / name
            if path.is_symlink() or path.is_file():
                path.unlink()
        archive = Path(ctx.backup_dir) / 'worktree.tar'
        if not archive.is_file():
            raise UpdateFailed(f'缺少旧代码归档，无法恢复：{archive}')
        extract_tar(archive, project, logger)
        owner = project.stat()
        for name in old_set:
            path = project / name
            chown_if_root(path, owner.st_uid, owner.st_gid)
            try:
                os.chmod(path, sanitize_mode_for_path(path))
            except OSError:
                pass
        logger.info('已用更新前归档恢复跟踪文件')
    if ctx.refs_switched or repo.head() == ctx.plan.target_sha:
        repo.read_tree(ctx.plan.old_sha)
        repo.update_head(ctx.plan.old_sha,
                         f'acm-update-rollback: {ctx.plan.target_sha[:12]} -> {ctx.plan.old_sha[:12]}')
        restore_repo_ownership(repo, ctx)
        logger.info('已把 Git 分支恢复到 %s', ctx.plan.old_sha[:12])
    elif repo.head() != ctx.plan.old_sha:
        raise UpdateFailed('Git HEAD 处于意外状态，无法确认恢复结果')


def restore_dist(ctx, logger):
    if not ctx.dist_switched:
        return
    project = Path(ctx.cfg.project)
    dist = project / 'frontend' / 'dist'
    old = Path(ctx.backup_dir) / 'frontend-dist'
    failed = Path(ctx.failed_dir) / 'frontend-dist'
    if dist.exists():
        ensure_dir(Path(ctx.failed_dir), 0o700)
        if failed.exists():
            shutil.rmtree(failed)
        shutil.move(str(dist), str(failed))
    if old.exists():
        os.replace(old, dist)
        owner = project.stat()
        chown_tree(dist, owner.st_uid, owner.st_gid)
        chmod_tree(dist, 0o755, 0o644)
        logger.info('已恢复原前端构建产物')


def restore_venv(ctx, logger):
    if not ctx.venv_switched:
        return
    project = Path(ctx.cfg.project)
    venv = project / '.venv'
    previous = Path(ctx.backup_dir) / 'venv-previous'
    if venv.is_symlink():
        venv.unlink()
    elif venv.exists():
        ensure_dir(Path(ctx.failed_dir), 0o700)
        failed = Path(ctx.failed_dir) / 'venv'
        if failed.exists():
            shutil.rmtree(failed)
        shutil.move(str(venv), str(failed))
    if previous.exists():
        os.replace(previous, venv)
        logger.info('已恢复原虚拟环境')
    elif ctx.old_venv_link:
        os.symlink(ctx.old_venv_link, venv, target_is_directory=True)
        logger.info('已恢复原虚拟环境符号链接')


def restore_data_and_credentials(ctx, logger):
    manifest = ctx.manifest
    data_dir = Path(manifest['data_dir'])
    restore_data(data_dir, ctx.backup_dir, ctx.failed_dir, manifest['data_uid'], manifest['data_gid'])
    logger.info('已把数据目录恢复为停服时的备份：%s', data_dir)
    restore_credentials(ctx.cfg.project, ctx.backup_dir, manifest['credentials'])
    logger.info('已恢复 .env 与账号配置')


# ---------------------------------------------------------------------------
# service control


def stop_service(cfg, install, runner, logger):
    if install.mode == 'systemd':
        logger.info('停止 systemd 服务 %s', install.service)
        runner.run(['systemctl', 'stop', install.service], timeout=300)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = systemd_state(install.service, runner)
            if state in {'inactive', 'failed'}:
                return
            if state == 'unknown':
                raise UpdateFailed(f'无法确认 systemd 服务状态，拒绝继续：{install.service}')
            time.sleep(1)
        raise UpdateFailed(f'systemd 服务未在 60 秒内停止：{install.service}')
    logger.info('停止 Compose 服务 %s', install.service)
    runner.run(compose_argv(install.compose_files, project_env_file(cfg.project))
               + ['stop', install.service], cwd=cfg.project, timeout=300)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not compose_ids(install.compose_files, cfg.project, install.service, runner,
                           env_file=project_env_file(cfg.project)):
            return
        time.sleep(1)
    raise UpdateFailed(f'Compose 服务未在 60 秒内停止：{install.service}')


def start_service(cfg, install, runner, logger, expected_image=None, previous_pid=0):
    if install.mode == 'systemd':
        logger.info('启动 systemd 服务 %s（更新前 MainPID=%s）', install.service, previous_pid or '未知')
        runner.run(['systemctl', 'start', install.service], timeout=300)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = systemd_state(install.service, runner)
            if state == 'active':
                break
            if state in {'failed', 'inactive'}:
                raise UpdateFailed(f'systemd 服务启动失败（状态 {state}）：{install.service}')
            time.sleep(1)
        else:
            raise UpdateFailed(f'systemd 服务未在 60 秒内进入 active：{install.service}')
        new_pid = systemd_main_pid(install.service, runner)
        if previous_pid and new_pid and new_pid == previous_pid:
            raise UpdateFailed('systemd 服务未重启（MainPID 未变化），无法确认新版本已生效')
        logger.info('systemd 服务已启动（MainPID=%s）', new_pid or '未知')
        return
    logger.info('启动 Compose 服务 %s（不重新构建）', install.service)
    runner.run(compose_argv(install.compose_files, project_env_file(cfg.project))
               + ['up', '-d', '--no-build', install.service],
               cwd=cfg.project, timeout=600)
    if expected_image:
        wait_container_image(cfg, install, runner, expected_image, logger)


def wait_container_image(cfg, install, runner, expected_image, logger, timeout=90):
    deadline = time.monotonic() + timeout
    last = ''
    while time.monotonic() < deadline:
        container_id = compose_ids(install.compose_files, cfg.project, install.service, runner,
                                   env_file=project_env_file(cfg.project))
        if container_id:
            current = compose_container_image_id(container_id.splitlines()[0], runner)
            if current == expected_image:
                logger.info('容器已使用目标镜像 %s', expected_image)
                return
            last = current
        time.sleep(2)
    raise UpdateFailed(f'容器未使用目标镜像（期望 {expected_image}，实际 {last or "无容器"}）')


def container_alive(cfg, install, runner, expected_image):
    def alive():
        container_id = compose_ids(install.compose_files, cfg.project, install.service, runner,
                                   env_file=project_env_file(cfg.project))
        if not container_id:
            return False
        try:
            return compose_container_image_id(container_id.splitlines()[0], runner) == expected_image
        except UpdateError:
            return False
    return alive


def service_alive(install, runner):
    return lambda: systemd_state(install.service, runner) == 'active'


# ---------------------------------------------------------------------------
# update orchestration


def configure_logging(cfg):
    logger = logging.getLogger('acm.update')
    logger.setLevel(logging.DEBUG if cfg.verbose else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(stream)
    return logger


def attach_file_log(logger, cfg, stamp):
    """Create the per-run log only once the update is actually starting."""
    logs_dir = ensure_dir(Path(cfg.project) / STATE_DIRNAME / 'logs', 0o700)
    log_path = logs_dir / f'update-{stamp}.log'
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    handler = logging.StreamHandler(os.fdopen(fd, 'a', encoding='utf-8'))
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    return log_path


def is_root():
    return os.geteuid() == 0


def prepare_state_dirs(state):
    ensure_dir(state, 0o755)
    ensure_dir(state / 'backups', 0o700)
    ensure_dir(state / 'logs', 0o700)
    ensure_dir(state / 'candidates', 0o755)
    ensure_dir(state / 'venvs', 0o755)
    ensure_dir(state / 'build', 0o700)
    ensure_dir(state / 'failed', 0o700)


def write_manifest(backup_dir, manifest):
    write_json_atomic(Path(backup_dir) / 'manifest.json', manifest, 0o600)


def rollback(ctx, install, runner, logger, error):
    """Undo a partially applied update, preserving the failed data."""
    project = Path(ctx.cfg.project)
    problems = []
    logger.error('更新失败，开始回滚：%s', redact(str(error)))
    try:
        stop_service(ctx.cfg, install, runner, logger)
    except BaseException as exc:  # noqa: BLE001 - rollback must keep going
        problems.append(f'停止新服务失败：{exc}')

    if ctx.service_started:
        try:
            restore_data_and_credentials(ctx, logger)
        except BaseException as exc:  # noqa: BLE001
            problems.append(f'恢复数据失败：{exc}')

    try:
        restore_code(ctx, logger)
    except BaseException as exc:  # noqa: BLE001
        problems.append(f'恢复代码失败：{exc}')

    try:
        restore_dist(ctx, logger)
    except BaseException as exc:  # noqa: BLE001
        problems.append(f'恢复前端产物失败：{exc}')

    try:
        restore_venv(ctx, logger)
    except BaseException as exc:  # noqa: BLE001
        problems.append(f'恢复虚拟环境失败：{exc}')

    if install.mode == 'docker' and ctx.image_switched:
        try:
            rollback_tag = ctx.images.get('rollback_tag')
            if rollback_tag:
                docker_tag(rollback_tag, install.image, runner)
                logger.info('已把镜像标签 %s 恢复为更新前镜像', install.image)
            old_image_id = ctx.images.get('old_image_id')
            start_service(ctx.cfg, install, runner, logger, expected_image=old_image_id)
            alive = container_alive(ctx.cfg, install, runner, old_image_id) if old_image_id else None
            wait_health(ctx.cfg.health_url, ctx.plan.public_origin_host,
                        min(ctx.cfg.health_timeout, 120), ctx.cfg.health_interval,
                        alive=alive, logger=logger)
            logger.info('旧镜像容器已恢复并通过健康检查')
        except BaseException as exc:  # noqa: BLE001
            problems.append(f'恢复旧镜像、容器或健康检查失败：{exc}')
    else:
        try:
            start_service(ctx.cfg, install, runner, logger)
            wait_health(ctx.cfg.health_url, ctx.plan.public_origin_host,
                        min(ctx.cfg.health_timeout, 120), ctx.cfg.health_interval,
                        alive=service_alive(install, runner), logger=logger)
            logger.info('旧版本已恢复并通过健康检查')
        except BaseException as exc:  # noqa: BLE001
            problems.append(f'旧版本恢复后未能通过健康检查：{exc}')

    status = 'rolled_back' if not problems else 'rollback_incomplete'
    ctx.manifest['status'] = status
    ctx.manifest['error'] = redact(str(error))[:4000]
    ctx.manifest['rollback_problems'] = [redact(str(item)) for item in problems]
    ctx.manifest['finished_at'] = utc_iso()
    try:
        write_manifest(ctx.backup_dir, ctx.manifest)
    except BaseException as exc:  # noqa: BLE001
        logger.error('写入回滚清单失败：%s', exc)

    if problems:
        logger.error('回滚未完全成功，需要人工介入：%s', '；'.join(problems))
        logger.error('故障数据与日志保留在 %s', ctx.failed_dir)
    else:
        logger.info('回滚完成：代码、运行环境与数据均恢复为更新前状态')
    return problems


def apply_update(cfg, runner, logger, plan, state, stamp, log_path):
    project = Path(cfg.project)
    install = plan.install
    candidate_dir = Path(state) / 'candidates' / f'{stamp}-{plan.target_sha[:12]}'
    prepare_candidate(plan.repo, plan.target_sha, candidate_dir, logger)
    candidate_venv = None
    candidate_dist = None
    images = {}
    if install.mode == 'systemd':
        frontend_dist = build_frontend(cfg, candidate_dir, project, runner, logger)
        candidate_venv = build_candidate_venv(
            cfg, candidate_dir, Path(state) / 'venvs' / f'{stamp}-{plan.target_sha[:12]}',
            project, runner, logger)
        if frontend_dist is not None:
            candidate_dist = frontend_dist
    else:
        images = build_candidate_image(cfg, runner, install, candidate_dir, state, stamp, logger)

    recheck_before_stop(cfg, plan, logger)

    backup_dir = Path(state) / 'backups' / stamp
    failed_dir = Path(state) / 'failed' / stamp
    ensure_dir(backup_dir, 0o700)
    archive = plan.repo.archive(plan.old_sha, backup_dir / 'worktree.tar')
    manifest = {
        'tool': 'acm-update',
        'format': 1,
        'stamp': stamp,
        'mode': install.mode,
        'status': 'prepared',
        'started_at': utc_iso(),
        'finished_at': None,
        'project': str(project),
        'service': install.service,
        'health_url': redact(cfg.health_url),
        'public_origin_host': plan.public_origin_host,
        'old_sha': plan.old_sha,
        'new_sha': plan.target_sha,
        'old_subject': plan.repo.subject(plan.old_sha),
        'new_subject': plan.repo.subject(plan.target_sha),
        'candidate_dir': str(candidate_dir),
        'code_archive': str(archive),
        'candidate_venv': str(candidate_venv) if candidate_venv else None,
        'candidate_dist': str(candidate_dist) if candidate_dist else None,
        'images': images,
        'log_file': str(log_path) if log_path else None,
    }
    write_manifest(backup_dir, manifest)

    ctx = SwitchContext(cfg=cfg, plan=plan, state=Path(state), backup_dir=backup_dir,
                        failed_dir=failed_dir, candidate_dir=candidate_dir, stamps={'stamp': stamp},
                        candidate_venv=candidate_venv, candidate_dist=candidate_dist,
                        images=images, manifest=manifest)

    stopped = False
    stop_attempted = False
    if install.mode == 'systemd':
        try:
            ctx.old_pid = systemd_main_pid(install.service, runner)
        except UpdateError:
            ctx.old_pid = 0
    try:
        stop_attempted = True
        stop_service(cfg, install, runner, logger)
        stopped = True
        info = backup_data(project, plan.data_dir, backup_dir, logger)
        manifest.update(info)
        manifest['status'] = 'backed_up'
        write_manifest(backup_dir, manifest)
    except BaseException as exc:
        if stop_attempted:
            # The stop command may have taken effect even when the status query
            # failed, so always try to bring the previous service back.
            logger.error('停止服务或备份失败，正在尝试恢复旧服务：%s', exc)
            try:
                old_image_id = images.get('old_image_id')
                start_service(cfg, install, runner, logger, previous_pid=0,
                              expected_image=old_image_id)
                if install.mode == 'systemd':
                    alive = service_alive(install, runner)
                else:
                    alive = container_alive(cfg, install, runner, old_image_id) if old_image_id else None
                wait_health(cfg.health_url, plan.public_origin_host,
                            min(cfg.health_timeout, 120), cfg.health_interval,
                            alive=alive, logger=logger)
            except BaseException as start_exc:  # noqa: BLE001
                logger.error('旧服务未能重新启动或未通过健康检查：%s', start_exc)
        raise UpdateFailed(f'停止服务或备份失败，未做任何代码切换：{exc}') from exc

    try:
        if install.mode == 'systemd':
            switch_code(ctx, logger)
            switch_dist(ctx, logger)
            switch_venv(ctx, logger)
            switch_refs(ctx, logger)
            ctx.service_started = True
            start_service(cfg, install, runner, logger, previous_pid=ctx.old_pid)
            wait_health(cfg.health_url, plan.public_origin_host, cfg.health_timeout,
                        cfg.health_interval, alive=service_alive(install, runner), logger=logger)
        else:
            switch_code(ctx, logger)
            switch_refs(ctx, logger)
            target_image = images.get('target_image') or install.image
            docker_tag(install.image, images['rollback_tag'], runner)
            docker_tag(images['candidate_tag'], target_image, runner)
            logger.info('已把旧镜像保存为 %s，新镜像写入 compose 目标 tag %s',
                        images['rollback_tag'], target_image)
            ctx.image_switched = True
            ctx.service_started = True
            start_service(cfg, install, runner, logger, expected_image=images['new_image_id'])
            wait_health(cfg.health_url, plan.public_origin_host, cfg.health_timeout,
                        cfg.health_interval,
                        alive=container_alive(cfg, install, runner, images['new_image_id']),
                        logger=logger)
    except BaseException as exc:
        problems = rollback(ctx, install, runner, logger, exc)
        suffix = ''
        if problems:
            suffix = ('\n回滚未完全成功，需要人工介入：' + '；'.join(problems)
                      + f'\n故障现场：{failed_dir}')
        raise UpdateFailed(redact(
            f'新版本未通过启动/健康检查，已尝试回滚：{exc}\n'
            f'停服前备份：{backup_dir}{suffix}')) from exc

    manifest['status'] = 'ok'
    manifest['finished_at'] = utc_iso()
    backfilled = {
        'venv_previous': str(backup_dir / 'venv-previous') if ctx.venv_switched else None,
        'old_venv_link': ctx.old_venv_link,
        'image_rollback_tag': images.get('rollback_tag'),
    }
    manifest.update(backfilled)
    write_manifest(backup_dir, manifest)
    report_success(cfg, plan, install, backup_dir, images, logger)
    return EXIT_OK


def report_success(cfg, plan, install, backup_dir, images, logger):
    print(f'更新完成：{plan.old_sha[:12]} -> {plan.target_sha[:12]}')
    print(f'  新版本：{plan.repo.subject(plan.target_sha)}')
    print(f'  部署方式：{install.mode}（服务 {install.service}）')
    print(f'  健康检查：{redact(cfg.health_url)} 通过')
    print(f'  停服前备份：{backup_dir}（data、.env、账号配置；manifest.json 记录原 SHA）')
    if images.get('rollback_tag'):
        target_image = images.get('target_image') or install.image
        compose = ' '.join(['docker compose'] + [f'-f {path}' for path in install.compose_files])
        print(f'  镜像 tag：{install.image} -> {target_image}')
        print(f'  旧镜像保留为：{images["rollback_tag"]}'
              f'（如需手动回滚：恢复旧 compose 文件、docker tag {images["rollback_tag"]} '
              f'{install.image}，再 {compose} up -d --no-build {install.service}）')
    logger.info('更新成功：%s -> %s', plan.old_sha[:12], plan.target_sha[:12])


# ---------------------------------------------------------------------------
# CLI


def run_update(cfg):
    project = Path(cfg.project).resolve()
    cfg.project = project
    stamp = utc_stamp()
    logger = configure_logging(cfg)
    log_path = None
    try:
        if not project.is_dir():
            raise UsageError(f'项目目录不存在：{project}')
        validate_ref(cfg.ref)
        validate_compose_service(cfg.compose_service)
        parse_health_url(cfg.health_url)
        runner = Runner(logger)
        preliminary = build_plan(cfg, runner, logger, fetch=False)
        if cfg.check:
            print_plan(preliminary, cfg, check=True)
            if preliminary.up_to_date:
                print('已是最新版本，无需更新。')
            else:
                print('预检通过：未 fetch、未加锁、未备份、未停止服务、未修改任何文件。')
            return EXIT_OK
        state = ensure_dir(project / STATE_DIRNAME, 0o755)
        log_path = attach_file_log(logger, cfg, stamp)
        with UpdateLock(state / LOCK_FILENAME, logger):
            plan = build_plan(cfg, runner, logger, fetch=True)
            if plan.up_to_date:
                print_plan(plan, cfg, check=False)
                print('已是最新版本，无需更新。')
                return EXIT_OK
            print_plan(plan, cfg, check=False)
            if plan.install.mode == 'systemd' and plan.install.was_active is False:
                logger.warning('systemd 服务 %s 更新前未运行；更新完成后会按一键更新流程启动并检查',
                               plan.install.service)
            return apply_update(cfg, runner, logger, plan, state, stamp, log_path)
    finally:
        if log_path:
            logger.info('日志：%s', log_path)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def build_parser():
    parser = argparse.ArgumentParser(
        prog='update.py',
        description='一键更新已有 ACM Lab 服务器部署（Docker Compose 或 systemd），失败自动回滚')
    parser.add_argument('--project', default='.', help='项目目录（默认当前目录）')
    parser.add_argument('--mode', choices=['auto', 'docker', 'systemd'], default='auto',
                        help='部署方式；auto 无法唯一识别时会要求显式指定')
    parser.add_argument('--ref', default=DEFAULT_REF, help=f'上游引用，默认 {DEFAULT_REF}')
    parser.add_argument('--check', '--dry-run', dest='check', action='store_true',
                        help='只做无副作用预检：不 fetch、不加锁、不备份、不停止服务')
    parser.add_argument('--service', help='systemd 单元名（默认自动识别）')
    parser.add_argument('--compose-service', default='web', help='Compose 服务名，默认 web')
    parser.add_argument('--compose-file', action='append', default=[], dest='compose_files',
                        help='compose 文件，可重复；与部署时使用的文件保持一致')
    parser.add_argument('--health-url', default=DEFAULT_HEALTH_URL,
                        help=f'健康检查地址，默认 {DEFAULT_HEALTH_URL}')
    parser.add_argument('--public-origin', help='公开 HTTPS 站点来源，用于健康检查的 Host 头')
    parser.add_argument('--health-timeout', type=float, default=120.0, help='健康检查超时秒数，默认 120')
    parser.add_argument('--health-interval', type=float, default=2.0, help=argparse.SUPPRESS)
    parser.add_argument('--data-dir', help='数据目录（默认 systemd 读取 .env 的 ACM_DATA_DIR，否则 data）')
    parser.add_argument('--python', help='创建候选虚拟环境用的 Python 3.10+（默认 python3）')
    parser.add_argument('--skip-frontend-build', action='store_true',
                        help='不重新构建前端，沿用现有 frontend/dist（仅在已确认构建产物时使用）')
    parser.add_argument('--verbose', action='store_true')
    return parser


def config_from_args(args):
    project = Path(args.project).expanduser().resolve()
    return UpdateConfig(
        project=project,
        mode=args.mode,
        ref=args.ref,
        check=args.check,
        service=args.service,
        compose_service=args.compose_service,
        compose_files=list(args.compose_files),
        health_url=args.health_url,
        public_origin=args.public_origin,
        health_timeout=args.health_timeout,
        health_interval=args.health_interval,
        data_dir=Path(args.data_dir).expanduser() if args.data_dir else None,
        python=args.python,
        skip_frontend_build=args.skip_frontend_build,
        verbose=args.verbose,
    )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = config_from_args(args)
    try:
        return run_update(cfg)
    except UpdateError as exc:
        print(f'更新中止：{redact(str(exc))}', file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print('更新已中断（如已停机，请检查 .acm-update 下的备份与日志）', file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f'更新失败：{redact(str(exc))}', file=sys.stderr)
        if cfg.verbose:
            import traceback
            # Redact the formatted traceback too: raw frames may embed URLs or
            # error text containing credentials.
            print(redact(traceback.format_exc()), file=sys.stderr)
        return EXIT_FAILED


if __name__ == '__main__':
    sys.exit(main())
