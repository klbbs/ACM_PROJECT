"""Behavioural tests for the one-command updater.

What is real here: temporary Git remotes/clones, file switching, SQLite
snapshots (including WAL), backups, rollback restoration, the update lock and
the HTTP health probes (a real local HTTP server).

What is simulated: ``systemctl``/``docker``/``npm``/the Python used to build the
candidate virtualenv are small stub executables on PATH that record their argv
and keep a tiny state directory.  These tests therefore prove the updater's
control flow, argv construction and backup/rollback behaviour -- they are NOT a
real Docker or systemd acceptance run (this machine has no Docker).
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from acmweb import updater  # noqa: E402


# ---------------------------------------------------------------------------
# stub executables


STUB_SYSTEMCTL = '''#!/usr/bin/env python3
import os, pathlib, subprocess, sys

state = pathlib.Path(os.environ['STUB_SYSTEMD_STATE'])
state.mkdir(parents=True, exist_ok=True)
with (pathlib.Path(os.environ['STUB_LOG'])).open('a') as handle:
    handle.write('systemctl ' + ' '.join(sys.argv[1:]) + '\\n')
unit = os.environ.get('STUB_UNIT', 'acm-lab.service')
project = os.environ['STUB_PROJECT']
args = sys.argv[1:]
if not args:
    sys.exit(2)


def read(name, default):
    path = state / name
    return path.read_text().strip() if path.exists() else default


def write(name, value):
    (state / name).write_text(str(value))


def show(name):
    print('LoadState=loaded' if name == unit else 'LoadState=not-found')
    print('ActiveState=' + (read('active', 'active') if name == unit else 'inactive'))
    print('FragmentPath=' + ('/etc/systemd/system/' + unit if name == unit else ''))
    print('WorkingDirectory=' + (project if name == unit else ''))
    print('ExecStart={ path=' + project + '/.venv/bin/acm-web ; argv[]=' + project + '/.venv/bin/acm-web ; ignore_errors=no }' if name == unit else 'ExecStart=')
    print('MainPID=' + (read('pid', '111') if name == unit else '0'))


cmd = args[0]
if cmd == 'show':
    show(args[1] if len(args) > 1 else unit)
    sys.exit(0)
if cmd == 'is-active':
    name = args[1] if len(args) > 1 else unit
    value = read('active', 'active') if name == unit else 'inactive'
    print(value)
    sys.exit(0 if value == 'active' else 3)
if cmd == 'stop':
    write('active', 'inactive')
    sys.exit(0)
if cmd == 'start':
    write('active', 'active')
    write('pid', int(read('pid', '111')) + 1)
    hook = os.environ.get('STUB_START_HOOK')
    marker = state / 'hook-ran'
    if hook and not marker.exists():
        marker.write_text('1')
        subprocess.run([sys.executable, hook], check=False)
    sys.exit(0)
if cmd == 'list-units':
    if os.environ.get('STUB_LIST_UNIT', '1') == '1':
        print(unit + ' loaded active running ACM Lab')
    sys.exit(0)
if cmd == 'reset-failed':
    sys.exit(0)
sys.exit(2)
'''


STUB_DOCKER = '''#!/usr/bin/env python3
import json, os, pathlib, re, subprocess, sys

state = pathlib.Path(os.environ['STUB_DOCKER_STATE'])
state.mkdir(parents=True, exist_ok=True)
with (pathlib.Path(os.environ['STUB_LOG'])).open('a') as handle:
    handle.write('docker ' + ' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
images_file = state / 'images.json'
images = json.loads(images_file.read_text()) if images_file.exists() else {}
container_file = state / 'container.json'
container = json.loads(container_file.read_text()) if container_file.exists() else None
config_path = pathlib.Path(os.environ.get('STUB_COMPOSE_JSON', '/nonexistent'))
service_name = os.environ.get('STUB_COMPOSE_SERVICE', 'web')


def save_images():
    images_file.write_text(json.dumps(images))


def save_container():
    container_file.write_text(json.dumps(container))


def split_files():
    files, index, project_dir = [], 1, None
    while index < len(args):
        token = args[index]
        if token == '-f':
            files.append(pathlib.Path(args[index + 1]))
            index += 2
        elif token == '--project-directory':
            project_dir = pathlib.Path(args[index + 1])
            index += 2
        elif token in {'--env-file', '--profile'}:
            index += 2
        elif token.startswith('-'):
            index += 1
        else:
            break
    return files, args[index:], project_dir


def compose_image(files):
    """Minimal YAML reader: the fixture compose files use a plain image line."""
    image = None
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('image:'):
                image = stripped.split(':', 1)[1].strip().strip().strip('"').strip("'")
    return image


def compose_volumes(files, project_dir):
    """Resolve relative bind mounts the way Compose does (project directory)."""
    base = project_dir or (files[0].parent if files else pathlib.Path('.'))
    volumes = []
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        in_volumes = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped == 'volumes:':
                in_volumes = True
                continue
            if in_volumes:
                if stripped.startswith('- '):
                    item = stripped[2:].strip()
                    item = item.strip('"').strip("'")
                    source = item.split(':')[0]
                    if source and not source.startswith('/'):
                        item = str((base / source).resolve()) + item[len(source):]
                    volumes.append(item)
                elif not stripped.startswith('-'):
                    in_volumes = False
    return sorted(set(volumes))


def compose_config(files, project_dir=None):
    image = compose_image(files) or 'acm-lab:0.19.1'
    return {'services': {service_name: {
        'image': image,
        'build': {'context': '.', 'target': 'runtime'},
        'volumes': compose_volumes(files, project_dir),
    }}}


if args and args[0] == 'compose':
    files, rest, project_dir = split_files()
    if not rest:
        sys.exit(2)
    sub = rest[0]
    if sub == 'version':
        print('Docker Compose version v2.29.0')
        sys.exit(0)
    if sub == 'config':
        print(json.dumps(compose_config(files, project_dir)))
        sys.exit(0)
    if sub == 'ps':
        if container and ('-a' in rest or container['running']):
            print(container['id'])
        sys.exit(0)
    if sub == 'build':
        override = files[-1]
        tag = json.loads(override.read_text())['services'][service_name]['image']
        images[tag] = 'sha256:candidate-image'
        save_images()
        sys.exit(0)
    if sub == 'stop':
        if container:
            container['running'] = False
            save_container()
        sys.exit(0)
    if sub == 'up':
        tag = compose_image(files) or compose_config(files, project_dir)['services'][service_name]['image']
        container = {'id': 'cid-1', 'image': images.get(tag), 'running': True}
        save_container()
        hook = os.environ.get('STUB_DOCKER_UP_HOOK')
        marker = state / 'up-hook-ran'
        if hook and not marker.exists():
            marker.write_text('1')
            subprocess.run([sys.executable, hook], check=False)
        sys.exit(0)
    sys.exit(2)

if args and args[0] == 'image':
    if len(args) < 3 or args[1] != 'inspect':
        sys.exit(2)
    fmt = args[args.index('--format') + 1] if '--format' in args else None
    tag = args[-1]
    if tag not in images:
        sys.exit(1)
    print(images[tag] if fmt else '[]')
    sys.exit(0)

if args and args[0] == 'inspect':
    print((container or {}).get('image') or '')
    sys.exit(0)

if args and args[0] == 'tag':
    source, destination = args[1], args[2]
    if source not in images:
        sys.exit(1)
    images[destination] = images[source]
    save_images()
    sys.exit(0)

sys.exit(2)
'''


STUB_PYTHON = '''#!/usr/bin/env python3
import os, pathlib, sys

with (pathlib.Path(os.environ['STUB_LOG'])).open('a') as handle:
    handle.write('python ' + ' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[:2] == ['-m', 'venv']:
    dest = pathlib.Path(args[2])
    (dest / 'bin').mkdir(parents=True, exist_ok=True)
    body = ('#!/usr/bin/env python3\\n'
            'import os, pathlib, sys\\n'
            'with open(os.environ["STUB_LOG"], "a") as f:\\n'
            '    f.write("venv-python " + " ".join(sys.argv[1:]) + "\\\\n")\\n'
            'sys.exit(0)\\n')
    for name in ('python', 'acm-web'):
        path = dest / 'bin' / name
        path.write_text(body)
        path.chmod(0o755)
    (dest / 'pyvenv.cfg').write_text('home = /usr/bin\\n')
    (dest / 'MARKER').write_text('candidate-venv')
    sys.exit(0)
sys.exit(0)
'''


STUB_NPM = '''#!/usr/bin/env python3
import os, pathlib, sys

with (pathlib.Path(os.environ['STUB_LOG'])).open('a') as handle:
    handle.write('npm ' + ' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
cwd = pathlib.Path.cwd()
if args[:1] == ['ci']:
    (cwd / 'node_modules').mkdir(parents=True, exist_ok=True)
    (cwd / 'node_modules' / 'installed').write_text('ci')
    sys.exit(0)
if args[:2] == ['run', 'build']:
    dist = cwd / 'dist'
    dist.mkdir(exist_ok=True)
    (dist / 'index.html').write_text('<html>candidate build</html>')
    sys.exit(0)
sys.exit(2)
'''


# ---------------------------------------------------------------------------
# real HTTP health server


class HealthServer:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        self.host_headers = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                outer.host_headers.append(self.headers.get('Host'))
                code, body = outer.responder(self)
                payload = body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}/api/health'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ok_health(request):
    return 200, '{"status":"ok"}'


# ---------------------------------------------------------------------------
# deployment fixture


def git(cwd, *args, check=True):
    proc = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f'git {args} failed: {proc.stderr}')
    return proc.stdout.strip()


def write(path, text, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    if mode is not None:
        os.chmod(path, mode)


@pytest.fixture
def site(tmp_path, monkeypatch):
    root = tmp_path
    remote = root / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-b', 'main', str(remote)],
                   check=True, capture_output=True)
    seed = root / 'seed'
    seed.mkdir()
    subprocess.run(['git', 'init', '-b', 'main'], cwd=seed, check=True, capture_output=True)
    git(seed, 'config', 'user.name', 'Updater Test')
    git(seed, 'config', 'user.email', 'updater@example.invalid')
    write(seed / '.gitignore',
          '.acm-update/\n.venv/\ndata/\n.env\naccount.toml\nfrontend/dist/\n'
          'notes.txt\n*.sqlite3*\n')
    write(seed / 'README.md', '# ACM Lab\n')
    write(seed / 'src/acmweb/__init__.py', 'VERSION = 1\n')
    write(seed / 'requirements.lock', '# lock\n')
    write(seed / 'compose.yaml',
          'services:\n  web:\n    image: acm-lab:0.19.1\n'
          '    build:\n      context: .\n      target: runtime\n'
          '    volumes:\n      - ./data:/app/data\n'
          '      - ./account.toml:/app/account.toml:ro\n')
    write(seed / 'frontend/package.json', '{"name":"acm-lab-web"}\n')
    write(seed / 'frontend/package-lock.json', '{"lockfileVersion":3}\n')
    git(seed, 'add', '-A')
    git(seed, 'commit', '-m', 'seed')
    git(seed, 'remote', 'add', 'origin', str(remote))
    git(seed, 'push', 'origin', 'main')

    project = root / 'project'
    subprocess.run(['git', 'clone', str(remote), str(project)], check=True, capture_output=True)
    git(project, 'config', 'user.name', 'Updater Test')
    git(project, 'config', 'user.email', 'updater@example.invalid')

    stubs = root / 'stubs'
    stubs.mkdir()
    for name, body in (('systemctl', STUB_SYSTEMCTL), ('docker', STUB_DOCKER),
                       ('fake-python', STUB_PYTHON), ('npm', STUB_NPM)):
        write(stubs / name, body, 0o755)
    log = root / 'calls.log'
    log.write_text('')

    import shutil
    monkeypatch.setenv('PATH', f'{stubs}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setenv('STUB_LOG', str(log))
    monkeypatch.setenv('STUB_SYSTEMD_STATE', str(root / 'systemd-state'))
    monkeypatch.setenv('STUB_DOCKER_STATE', str(root / 'docker-state'))
    monkeypatch.setenv('STUB_PROJECT', str(project))
    monkeypatch.setenv('STUB_UNIT', 'acm-lab.service')
    monkeypatch.setenv('STUB_LIST_UNIT', '1')
    compose_json = root / 'compose.json'
    write(compose_json, json.dumps({'services': {'web': {
        'image': 'acm-lab:0.19.1',
        'build': {'context': '.', 'target': 'runtime'},
    }}}))
    monkeypatch.setenv("STUB_COMPOSE_JSON", str(compose_json))

    # Local private state that must survive updates untouched.
    write(project / '.env', 'ACM_ACCESS_TOKEN=private-token-value\n')
    write(project / 'account.toml', '[crawler]\n')
    data = project / 'data'
    data.mkdir()
    db = sqlite3.connect(data / 'accounts.sqlite3')
    db.execute('CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT)')
    db.execute("INSERT INTO accounts (name) VALUES ('alice')")
    db.execute("INSERT INTO accounts (name) VALUES ('bob')")
    db.commit()
    db.close()
    write(data / 'private-config-backups/keep.toml', 'keep me\n')
    write(project / 'notes.txt', 'personal notes\n')
    write(project / 'frontend/dist/index.html', '<html>old build</html>')
    write(project / 'frontend/node_modules/.keep', '')
    old_venv = project / '.venv'
    write(old_venv / 'bin/python', '#!/usr/bin/env python3\nprint("old")\n', 0o755)
    write(old_venv / 'OLD_MARKER', 'old-venv\n')

    # Docker deployment state: an old image and a running container.
    docker_state = root / 'docker-state'
    docker_state.mkdir()
    write(docker_state / 'images.json', json.dumps({'acm-lab:0.19.1': 'sha256:old-image'}))
    write(docker_state / 'container.json', json.dumps(
        {'id': 'cid-1', 'image': 'sha256:old-image', 'running': True}))
    # Systemd deployment state: active with a stable MainPID.
    write(root / 'systemd-state/active', 'active')
    write(root / 'systemd-state/pid', '111')

    health = HealthServer(ok_health)

    env = SimpleNamespace(
        root=root, project=project, remote=remote, seed=seed, stubs=stubs,
        log=log, health=health, data=data, compose_json=compose_json,
        docker_state=docker_state, systemd_state=root / 'systemd-state',
    )
    env.release = lambda changes=None, delete=(), force=(): push_release(env, changes or {}, delete, force)
    env.run = lambda *args, check=True: run_update(env, *args, check=check)
    env.calls = lambda pattern='': [line for line in log.read_text().splitlines() if pattern in line]
    # Tests must not depend on the real machine's root privileges: the
    # service-control stubs are the only "privileged" part here.
    monkeypatch.setattr(updater, 'is_root', lambda: True)
    try:
        yield env
    finally:
        health.close()


def push_release(env, changes, delete=(), force=()):
    work = env.root / 'work'
    if not work.exists():
        subprocess.run(['git', 'clone', str(env.remote), str(work)], check=True, capture_output=True)
        git(work, 'config', 'user.name', 'Updater Test')
        git(work, 'config', 'user.email', 'updater@example.invalid')
    else:
        git(work, 'fetch', 'origin')
        git(work, 'reset', '--hard', 'origin/main')
    for name, content in changes.items():
        write(work / name, content)
        git(work, 'add', '-f', name)
    for name in delete:
        path = work / name
        if path.exists():
            path.unlink()
            git(work, 'add', '-A', name)
    for name in force:
        git(work, 'add', '-f', name)
    if not git(work, 'status', '--porcelain'):
        return git(work, 'rev-parse', 'HEAD')
    git(work, 'commit', '-m', 'release')
    git(work, 'push', 'origin', 'main')
    return git(work, 'rev-parse', 'HEAD')


def run_update(env, *args, check=True):
    argv = ['--project', str(env.project), *args]
    code = updater.main(argv)
    if check and code != 0:
        raise AssertionError(f'update exited {code}')
    return code


def head(env):
    return git(env.project, 'rev-parse', 'HEAD')


def db_rows(path, table='accounts'):
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute(f'SELECT name FROM {table} ORDER BY name').fetchall()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# private path policy


@pytest.mark.parametrize('path', [
    '.env',
    '.env.production',
    'account.toml',
    'accounts.web.toml',
    'data/accounts.sqlite3',
    '.venv/pyvenv.cfg',
    '.ssh/id_rsa',
    '.aws/credentials',
    'releases/acm.zip',
    'frontend/dist/index.html',
    'backend/app.db',
    'notes.log',
    'src/acmweb/__pycache__/x.pyc',
])
def test_private_paths_are_rejected(path):
    assert updater.is_private_path(path)


@pytest.mark.parametrize('path', [
    '.env.example',
    'accounts.example.toml',
    'src/acmweb/settings.py',
    'docs/data-model.md',
    'frontend/src/data.ts',
    'deploy/acm-lab.service.example',
])
def test_example_and_source_paths_are_allowed(path):
    assert not updater.is_private_path(path)


# ---------------------------------------------------------------------------
# preflight


def test_check_has_no_side_effects(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    before_head = head(site)
    before_refs = git(site.project, 'for-each-ref')
    before_files = sorted(p.name for p in site.project.iterdir())

    code = site.run('--check', '--mode', 'systemd', '--health-url', site.health.url)

    assert code == updater.EXIT_OK
    assert head(site) == before_head
    assert git(site.project, 'for-each-ref') == before_refs
    assert not (site.project / '.acm-update').exists()
    assert not (site.project / '.git' / 'FETCH_HEAD').exists()
    assert sorted(p.name for p in site.project.iterdir()) == before_files
    assert not site.calls('systemctl stop')
    assert not site.calls('systemctl start')


def test_check_reports_remote_sha_without_fetching(site, capsys):
    target = site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    assert site.run('--check', '--mode', 'systemd', '--health-url', site.health.url) == 0
    output = capsys.readouterr().out
    assert '预检结果' in output
    assert target[:12] in output
    assert '未 fetch' in output


def test_dirty_tracked_tree_is_refused_before_any_change(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    write(site.project / 'README.md', '# locally edited\n')
    before = head(site)
    code = site.run('--mode', 'systemd', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED
    assert head(site) == before
    assert not (site.project / '.acm-update').exists()
    assert not site.calls('systemctl stop')


def test_diverged_upstream_is_refused(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    write(site.project / 'local.txt', 'local commit\n')
    git(site.project, 'add', 'local.txt')
    git(site.project, 'commit', '-m', 'local work')
    before = head(site)
    code = site.run('--mode', 'systemd', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED
    assert head(site) == before
    assert not site.calls('systemctl stop')


@pytest.mark.parametrize('path,content', [
    ('.env', 'ACM_ACCESS_TOKEN=leaked\n'),
    ('data/server.sqlite3', 'not really sqlite\n'),
    ('.venv/pyvenv.cfg', 'home = /usr\n'),
    ('account.toml', '[crawler]\n'),
    ('.ssh/id_rsa', 'private key\n'),
])
def test_upstream_tracking_private_paths_is_refused(site, path, content):
    site.release({path: content})
    before = head(site)
    code = site.run('--mode', 'systemd', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED
    assert head(site) == before
    assert not site.calls('systemctl stop')
    assert not (site.project / '.acm-update' / 'backups').exists()


def test_untracked_file_collision_is_refused(site):
    site.release({'notes.txt': 'from upstream\n'})
    write(site.project / 'notes.txt', 'personal notes\n')
    before = head(site)
    code = site.run('--mode', 'systemd', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED
    assert head(site) == before
    assert (site.project / 'notes.txt').read_text() == 'personal notes\n'
    assert not site.calls('systemctl stop')


def test_up_to_date_is_a_noop(site):
    before = head(site)
    code = site.run('--mode', 'systemd', '--health-url', site.health.url)
    assert code == 0
    assert head(site) == before
    assert not site.calls('systemctl stop')


# ---------------------------------------------------------------------------
# backups


def test_sqlite_backup_includes_committed_wal(tmp_path):
    source = tmp_path / 'data'
    source.mkdir()
    database = source / 'accounts.sqlite3'
    writer = sqlite3.connect(str(database))
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)')
    writer.executemany('INSERT INTO notes (body) VALUES (?)',
                       [('x' * 400,) for _ in range(2000)])
    writer.commit()
    wal = Path(str(database) + '-wal')
    assert wal.exists() and wal.stat().st_size > 0, 'test setup must leave a non-empty WAL'
    backup_dir = tmp_path / 'backup'
    logger = updater.logging.getLogger('test-backup')
    info = updater.backup_data(tmp_path, source, backup_dir, logger)
    try:
        assert info['databases'] == ['accounts.sqlite3']
        copy = backup_dir / 'data' / 'accounts.sqlite3'
        assert copy.is_file()
        assert not Path(str(copy) + '-wal').exists()
        connection = sqlite3.connect(str(copy))
        try:
            count = connection.execute('SELECT COUNT(*) FROM notes').fetchone()[0]
            assert count == 2000
        finally:
            connection.close()
        assert oct(backup_dir.stat().st_mode & 0o777) == '0o700'
        assert info['file_count'] >= 1
    finally:
        writer.close()


def test_backup_verification_catches_missing_files(tmp_path):
    source = tmp_path / 'data'
    source.mkdir()
    write(source / 'a.txt', 'a')
    write(source / 'b.txt', 'bb')
    destination = tmp_path / 'dest'
    updater.copy_tree(source, destination)
    (destination / 'b.txt').unlink()
    with pytest.raises(updater.UpdateFailed):
        updater.verify_backup(source, destination, [])


def test_backup_is_complete_and_private(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    backup_dir = site.root / 'manual-backup'
    logger = updater.logging.getLogger('test-backup-2')
    info = updater.backup_data(site.project, site.data, backup_dir, logger)
    assert (backup_dir / 'data' / 'accounts.sqlite3').is_file()
    assert (backup_dir / 'data' / 'private-config-backups' / 'keep.toml').is_file()
    names = {entry['path'] for entry in info['credentials']}
    assert '.env' in names and 'account.toml' in names
    env_backup = backup_dir / 'credentials' / '.env'
    assert env_backup.read_text() == 'ACM_ACCESS_TOKEN=private-token-value\n'
    assert oct(env_backup.stat().st_mode & 0o777) == '0o600'
    assert info['data_dir'] == str(site.data)


def test_check_does_not_refresh_git_index(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    tracked = site.project / 'README.md'
    os.utime(tracked, (1_600_000_000, 1_600_000_000))
    index = site.project / '.git' / 'index'
    import hashlib
    before = (index.stat().st_mtime_ns, hashlib.sha256(index.read_bytes()).hexdigest())
    assert site.run('--check', '--mode', 'systemd', '--health-url', site.health.url) == 0
    after = (index.stat().st_mtime_ns, hashlib.sha256(index.read_bytes()).hexdigest())
    assert after == before, 'git status refreshed the index during a read-only preflight'


def test_health_ok_does_not_hide_dead_target():
    server = HealthServer(ok_health)
    try:
        with pytest.raises(updater.UpdateFailed):
            updater.wait_health(server.url, None, 0.05, 0.01, alive=lambda: False)
    finally:
        server.close()


def test_runner_redacts_credentials_from_argv_and_stderr():
    import io
    import logging
    stream = io.StringIO()
    logger = logging.getLogger('test-redaction')
    logger.handlers = [logging.StreamHandler(stream)]
    runner = updater.Runner(logger)
    script = ('import sys; sys.stderr.write("https://bob:s3cret@example.invalid/x?token=abc123\\n");'
              ' sys.exit(2)')
    with pytest.raises(updater.UpdateFailed) as info:
        runner.run([sys.executable, '-c', script])
    message = str(info.value)
    assert 's3cret' not in message and 'abc123' not in message
    assert '***@' in message
    logged = stream.getvalue()
    assert 's3cret' not in logged and 'abc123' not in logged


# ---------------------------------------------------------------------------
# systemd update


def test_systemd_update_switches_code_and_keeps_private_files(site, capsys):
    old = head(site)
    target = site.release({
        'src/acmweb/__init__.py': 'VERSION = 2\n',
        'src/acmweb/new_module.py': 'NEW = True\n',
    }, delete=('README.md',))

    code = site.run('--mode', 'systemd', '--skip-frontend-build',
                    '--python', str(site.stubs / 'fake-python'),
                    '--health-url', site.health.url)
    assert code == updater.EXIT_OK

    assert head(site) == target
    assert (site.project / 'src/acmweb/__init__.py').read_text() == 'VERSION = 2\n'
    assert (site.project / 'src/acmweb/new_module.py').is_file()
    assert not (site.project / 'README.md').exists()
    # Untracked private material survives and the tree stays clean.
    assert (site.project / 'notes.txt').read_text() == 'personal notes\n'
    assert (site.project / '.env').read_text() == 'ACM_ACCESS_TOKEN=private-token-value\n'
    assert (site.data / 'private-config-backups' / 'keep.toml').is_file()
    assert db_rows(site.data / 'accounts.sqlite3') == [('alice',), ('bob',)]
    assert git(site.project, 'status', '--porcelain', '--untracked-files=no') == ''

    # The runtime was swapped to the freshly built candidate venv.
    venv = site.project / '.venv'
    assert venv.is_symlink()
    assert '.acm-update' in os.readlink(venv)
    assert (venv / 'bin' / 'acm-web').exists()
    assert os.stat(venv).st_mode & 0o001

    # Backup records the original SHA and is private.
    backups = list((site.project / '.acm-update' / 'backups').iterdir())
    assert len(backups) == 1
    backup_dir = backups[0]
    assert oct(backup_dir.stat().st_mode & 0o777) == '0o700'
    manifest = json.loads((backup_dir / 'manifest.json').read_text())
    assert manifest['status'] == 'ok'
    assert manifest['old_sha'] == old
    assert manifest['new_sha'] == target
    assert (backup_dir / 'worktree.tar').is_file()
    assert (backup_dir / 'data' / 'accounts.sqlite3').is_file()
    assert (backup_dir / 'credentials' / '.env').is_file()
    # Old venv is preserved for manual recovery.
    assert (backup_dir / 'venv-previous' / 'OLD_MARKER').read_text() == 'old-venv\n'

    assert site.calls('systemctl stop')
    assert site.calls('systemctl start')
    assert site.health.requests, 'health endpoint must be probed'
    output = capsys.readouterr().out
    assert '更新完成' in output


def test_systemd_venv_installs_candidate_not_old_venv(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    assert site.run('--mode', 'systemd', '--skip-frontend-build',
                    '--python', str(site.stubs / 'fake-python'),
                    '--health-url', site.health.url) == 0
    calls = site.log.read_text()
    venv_calls = [line for line in calls.splitlines() if line.startswith('python -m venv')]
    assert len(venv_calls) == 1
    candidate_venv = venv_calls[0].split()[-1]
    assert '/.acm-update/venvs/' in candidate_venv
    install_calls = [line for line in calls.splitlines()
                     if line.startswith('venv-python -m pip install')]
    assert install_calls, 'candidate venv must run pip install itself'
    install = install_calls[0]
    assert '/.acm-update/candidates/' in install
    # The old venv is not used as the pip target.
    assert '.venv/bin/python -m pip' not in calls


def test_dirty_index_is_detected_but_untracked_allowed(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    write(site.project / 'extra.txt', 'untracked\n')
    assert site.run('--check', '--mode', 'systemd', '--health-url', site.health.url) == 0


def test_health_failure_rolls_back_everything(site):
    old = head(site)
    target = site.release({'src/acmweb/__init__.py': 'VERSION = 2\n',
                           'src/acmweb/new_module.py': 'NEW = True\n'})
    # Simulate a migration performed when the new service first starts.
    migrate = site.root / 'migrate.py'
    write(migrate, (
        'import sqlite3, sys\n'
        f'db = sqlite3.connect({str(site.data / "accounts.sqlite3")!r})\n'
        "db.execute(\"INSERT INTO accounts (name) VALUES ('migrated')\")\n"
        'db.commit(); db.close()\n'))
    os.environ['STUB_START_HOOK'] = str(migrate)

    def broken_when_new(request):
        version = (site.project / 'src/acmweb/__init__.py').read_text()
        if 'VERSION = 2' in version:
            return 503, '{"detail":"migration broken"}'
        return 200, '{"status":"ok"}'

    site.health.responder = broken_when_new
    try:
        code = site.run('--mode', 'systemd', '--skip-frontend-build',
                        '--python', str(site.stubs / 'fake-python'),
                        '--health-url', site.health.url, '--health-timeout', '5',
                        check=False)
    finally:
        os.environ.pop('STUB_START_HOOK', None)

    assert code == updater.EXIT_FAILED
    assert head(site) == old
    assert (site.project / 'src/acmweb/__init__.py').read_text() == 'VERSION = 1\n'
    assert not (site.project / 'src/acmweb/new_module.py').exists()
    assert (site.project / 'README.md').is_file()
    assert db_rows(site.data / 'accounts.sqlite3') == [('alice',), ('bob',)]
    venv = site.project / '.venv'
    assert not venv.is_symlink()
    assert (venv / 'OLD_MARKER').read_text() == 'old-venv\n'
    # Failed data is preserved for inspection, not deleted.
    failed = list((site.project / '.acm-update' / 'failed').iterdir())
    assert failed
    failed_rows = db_rows(failed[0] / 'data' / 'accounts.sqlite3')
    assert ('migrated',) in failed_rows
    backups = list((site.project / '.acm-update' / 'backups').iterdir())
    manifest = json.loads((backups[0] / 'manifest.json').read_text())
    assert manifest['status'] == 'rolled_back'
    assert manifest['old_sha'] == old and manifest['new_sha'] == target
    assert (site.project / 'src/acmweb/__init__.py').read_text() == 'VERSION = 1\n'


def test_stop_failure_still_restarts_old_service(site, monkeypatch):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    old = head(site)

    def failing_stop(*args, **kwargs):
        raise updater.UpdateFailed('synthetic stop status unavailable after command')

    monkeypatch.setattr(updater, 'stop_service', failing_stop)
    code = site.run('--mode', 'systemd', '--skip-frontend-build',
                    '--python', str(site.stubs / 'fake-python'),
                    '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_FAILED
    assert head(site) == old
    assert site.calls('systemctl start'), 'old service must be restarted when stop state is unknown'
    assert (site.project / 'src/acmweb/__init__.py').read_text() == 'VERSION = 1\n'


def test_docker_rollback_with_unhealthy_old_container_reports_incomplete(site):
    old = head(site)
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    migrate = site.root / 'docker-migrate-2.py'
    write(migrate, (
        'import sqlite3\n'
        f'db = sqlite3.connect({str(site.data / "accounts.sqlite3")!r})\n'
        "db.execute(\"INSERT INTO accounts (name) VALUES ('migrated')\")\n"
        'db.commit(); db.close()\n'))
    os.environ['STUB_DOCKER_UP_HOOK'] = str(migrate)
    site.health.responder = lambda request: (503, '{"detail":"always down"}')
    try:
        code = site.run('--mode', 'docker', '--health-url', site.health.url,
                        '--health-timeout', '2', '--health-interval', '0.05', check=False)
    finally:
        os.environ.pop('STUB_DOCKER_UP_HOOK', None)

    assert code == updater.EXIT_FAILED
    assert head(site) == old
    images = json.loads((site.docker_state / 'images.json').read_text())
    assert images['acm-lab:0.19.1'] == 'sha256:old-image'
    backup = next((site.project / '.acm-update' / 'backups').iterdir())
    manifest = json.loads((backup / 'manifest.json').read_text())
    assert manifest['status'] == 'rollback_incomplete'
    assert manifest['rollback_problems']
    # The stop-time backup is still restored even though rollback health failed.
    assert db_rows(site.data / 'accounts.sqlite3') == [('alice',), ('bob',)]


def test_lock_blocks_a_second_update(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    lock_path = site.project / '.acm-update' / 'update.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [sys.executable, '-c', (
            'import fcntl, sys, time\n'
            f'handle = open({str(lock_path)!r}, "a+")\n'
            'fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
            'print("READY", flush=True)\n'
            'time.sleep(30)\n')],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'READY'
        before = head(site)
        code = site.run('--mode', 'systemd', '--health-url', site.health.url, check=False)
        assert code == updater.EXIT_REFUSED
        assert head(site) == before
        assert not site.calls('systemctl stop')
    finally:
        holder.kill()
        holder.wait(timeout=10)


# ---------------------------------------------------------------------------
# frontend build


def test_frontend_build_runs_in_candidate_with_reused_node_modules(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n',
                  'frontend/src/app.tsx': 'export const app = 1\n'})
    assert site.run('--mode', 'systemd',
                    '--python', str(site.stubs / 'fake-python'),
                    '--health-url', site.health.url) == 0
    npm_calls = site.calls('npm ')
    assert any(line.startswith('npm run build') for line in npm_calls)
    assert not any(line.startswith('npm ci') for line in npm_calls)
    dist = site.project / 'frontend' / 'dist' / 'index.html'
    assert 'candidate build' in dist.read_text()


def test_frontend_build_runs_npm_ci_when_lockfile_changes(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n',
                  'frontend/package-lock.json': '{"lockfileVersion":3,"changed":true}\n'})
    assert site.run('--mode', 'systemd',
                    '--python', str(site.stubs / 'fake-python'),
                    '--health-url', site.health.url) == 0
    assert any(line.startswith('npm ci') for line in site.calls('npm '))


# ---------------------------------------------------------------------------
# docker update


def test_docker_update_supports_image_tag_change(site):
    compose_volumes = ('    volumes:\n      - ./data:/app/data\n'
                       '      - ./account.toml:/app/account.toml:ro\n')
    old = head(site)
    target = site.release({
        'src/acmweb/__init__.py': 'VERSION = 2\n',
        'compose.yaml': ('services:\n  web:\n    image: acm-lab:0.20.0\n'
                         '    build:\n      context: .\n      target: runtime\n'
                         + compose_volumes),
    })
    code = site.run('--mode', 'docker', '--health-url', site.health.url)
    assert code == updater.EXIT_OK

    images = json.loads((site.docker_state / 'images.json').read_text())
    assert images['acm-lab:0.20.0'] == 'sha256:candidate-image'
    assert images['acm-lab:0.19.1'] == 'sha256:old-image'
    rollback = [tag for tag in images if 'update-rollback-' in tag]
    assert rollback and images[rollback[0]] == 'sha256:old-image'
    container = json.loads((site.docker_state / 'container.json').read_text())
    assert container['image'] == 'sha256:candidate-image'
    assert head(site) == target
    assert 'acm-lab:0.20.0' in (site.project / 'compose.yaml').read_text()
    manifest = json.loads(next((site.project / '.acm-update' / 'backups').iterdir())
                          .joinpath('manifest.json').read_text())
    assert manifest['images']['target_image'] == 'acm-lab:0.20.0'
    assert manifest['images']['rollback_tag'] == rollback[0]
    # The build used the candidate compose files and candidate build context.
    build_lines = [line for line in site.calls('docker ') if ' build ' in line]
    assert any('/.acm-update/candidates/' in line for line in build_lines)
    assert all(str(site.project / 'compose.yaml') not in line for line in build_lines)
    assert old in manifest['old_sha']


def test_docker_rollback_restores_previous_compose_and_image_tag(site):
    old = head(site)
    site.release({
        'src/acmweb/__init__.py': 'VERSION = 2\n',
        'compose.yaml': ('services:\n  web:\n    image: acm-lab:0.20.0\n'
                         '    build:\n      context: .\n      target: runtime\n'
                         '    volumes:\n      - ./data:/app/data\n'
                         '      - ./account.toml:/app/account.toml:ro\n'),
    })
    site.health.responder = lambda request: (503, '{"detail":"always down"}')
    code = site.run('--mode', 'docker', '--health-url', site.health.url,
                    '--health-timeout', '2', '--health-interval', '0.05', check=False)
    assert code == updater.EXIT_FAILED
    assert head(site) == old
    assert 'acm-lab:0.19.1' in (site.project / 'compose.yaml').read_text()
    images = json.loads((site.docker_state / 'images.json').read_text())
    assert images['acm-lab:0.19.1'] == 'sha256:old-image'
    container = json.loads((site.docker_state / 'container.json').read_text())
    assert container['image'] == 'sha256:old-image' and container['running'] is True


def test_docker_volume_change_is_refused_before_stop(site):
    site.release({
        'src/acmweb/__init__.py': 'VERSION = 2\n',
        'compose.yaml': ('services:\n  web:\n    image: acm-lab:0.20.0\n'
                         '    build:\n      context: .\n      target: runtime\n'
                         '    volumes:\n      - ./other-data:/app/data\n'),
    })
    old = head(site)
    code = site.run('--mode', 'docker', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED
    assert head(site) == old
    assert not any(' stop ' in line for line in site.calls('docker '))
    assert not any(' build ' in line for line in site.calls('docker '))


def test_docker_update_pins_candidate_image_and_keeps_old_image(site):
    old = head(site)
    target = site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    code = site.run('--mode', 'docker', '--health-url', site.health.url)
    assert code == updater.EXIT_OK

    images = json.loads((site.docker_state / 'images.json').read_text())
    rollback_tags = [tag for tag in images if 'update-rollback-' in tag]
    assert rollback_tags, images
    assert images[rollback_tags[0]] == 'sha256:old-image'
    assert images['acm-lab:0.19.1'] == 'sha256:candidate-image'
    assert any('compose build' in line or ' build web' in line for line in site.calls('docker '))
    assert any('up -d --no-build web' in line for line in site.calls('docker '))
    # The new image was never built directly over the deployed tag.
    assert not any(line.endswith('build -t acm-lab:0.19.1') for line in site.calls('docker '))
    container = json.loads((site.docker_state / 'container.json').read_text())
    assert container['running'] is True
    assert head(site) == target
    assert (site.project / 'src/acmweb/__init__.py').read_text() == 'VERSION = 2\n'
    assert db_rows(site.data / 'accounts.sqlite3') == [('alice',), ('bob',)]
    manifest = json.loads(next((site.project / '.acm-update' / 'backups').iterdir())
                          .joinpath('manifest.json').read_text())
    assert manifest['mode'] == 'docker'
    assert manifest['old_sha'] == old and manifest['new_sha'] == target
    assert manifest['images']['rollback_tag'] == rollback_tags[0]


def test_docker_failure_restores_image_code_and_data(site):
    old = head(site)
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    migrate = site.root / 'docker-migrate.py'
    write(migrate, (
        'import sqlite3\n'
        f'db = sqlite3.connect({str(site.data / "accounts.sqlite3")!r})\n'
        "db.execute(\"INSERT INTO accounts (name) VALUES ('migrated')\")\n"
        'db.commit(); db.close()\n'))
    os.environ['STUB_DOCKER_UP_HOOK'] = str(migrate)

    def broken_when_new(request):
        if 'VERSION = 2' in (site.project / 'src/acmweb/__init__.py').read_text():
            return 503, '{"detail":"not ready"}'
        return 200, '{"status":"ok"}'

    site.health.responder = broken_when_new
    try:
        code = site.run('--mode', 'docker', '--health-url', site.health.url,
                        '--health-timeout', '5', check=False)
    finally:
        os.environ.pop('STUB_DOCKER_UP_HOOK', None)

    assert code == updater.EXIT_FAILED
    assert head(site) == old
    assert (site.project / 'src/acmweb/__init__.py').read_text() == 'VERSION = 1\n'
    images = json.loads((site.docker_state / 'images.json').read_text())
    assert images['acm-lab:0.19.1'] == 'sha256:old-image'
    container = json.loads((site.docker_state / 'container.json').read_text())
    assert container['image'] == 'sha256:old-image'
    assert db_rows(site.data / 'accounts.sqlite3') == [('alice',), ('bob',)]
    failed = list((site.project / '.acm-update' / 'failed').iterdir())
    assert ('migrated',) in db_rows(failed[0] / 'data' / 'accounts.sqlite3')


# ---------------------------------------------------------------------------
# mode detection


def test_ambiguous_deployment_requires_explicit_mode(site):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    code = site.run('--check', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED
    assert not site.calls('systemctl stop')


def test_unknown_deployment_requires_explicit_mode(site, monkeypatch):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    monkeypatch.setenv('STUB_LIST_UNIT', '0')
    (site.docker_state / 'container.json').unlink()
    code = site.run('--check', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED


def test_explicit_docker_mode_refuses_without_existing_deployment(site):
    (site.docker_state / 'container.json').unlink()
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    code = site.run('--mode', 'docker', '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED


def test_wrapper_check_does_not_write_bytecode(site, tmp_path):
    import shutil as shutil_module
    release = tmp_path / 'release'
    (release / 'scripts').mkdir(parents=True)
    (release / 'src' / 'acmweb').mkdir(parents=True)
    shutil_module.copy2(ROOT / 'update.sh', release / 'update.sh')
    shutil_module.copy2(ROOT / 'scripts' / 'update.py', release / 'scripts' / 'update.py')
    shutil_module.copy2(ROOT / 'src' / 'acmweb' / '__init__.py',
                        release / 'src' / 'acmweb' / '__init__.py')
    shutil_module.copy2(ROOT / 'src' / 'acmweb' / 'updater.py',
                        release / 'src' / 'acmweb' / 'updater.py')
    os.chmod(release / 'update.sh', 0o755)
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    proc = subprocess.run(
        [str(release / 'update.sh'), '--check', '--mode', 'docker',
         '--health-url', site.health.url, '--project', str(site.project)],
        cwd=release, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert not list(release.rglob('__pycache__')), 'preflight wrote bytecode caches'


def test_systemd_unit_must_execute_project_venv(monkeypatch, tmp_path):
    project = tmp_path / 'project'
    (project / '.venv' / 'bin').mkdir(parents=True)
    cfg = updater.UpdateConfig(project=project, mode='systemd', service='acm-lab')
    monkeypatch.setattr(updater, 'systemd_show', lambda *args: {
        'LoadState': 'loaded', 'ActiveState': 'active',
        'WorkingDirectory': str(project),
        'ExecStart': '{ path=/opt/other-runtime/bin/acm-web ; argv[]=/opt/other-runtime/bin/acm-web ; }',
        'FragmentPath': '/etc/systemd/system/acm-lab.service', 'MainPID': '10',
    })
    with pytest.raises(updater.Refused):
        updater.detect_systemd(project, cfg, updater.Runner(updater.logging.getLogger('t')),
                               updater.logging.getLogger('t'))
    supported = {'WorkingDirectory': str(project), 'ActiveState': 'active',
                 'LoadState': 'loaded', 'FragmentPath': '/etc/systemd/system/acm-lab.service',
                 'MainPID': '10'}
    supported['ExecStart'] = ('{ path=' + str(project / '.venv' / 'bin' / 'acm-web')
                              + ' ; argv[]=' + str(project / '.venv' / 'bin' / 'acm-web') + ' ; }')
    assert updater.unit_runtime_check(supported, project) == (True, '')
    python_unit = dict(supported)
    python_unit['ExecStart'] = (
        '{ path=' + str(project / '.venv' / 'bin' / 'python')
        + ' ; argv[]=' + str(project / '.venv' / 'bin' / 'python') + ' -m acmweb.main ; }')
    assert updater.unit_runtime_check(python_unit, project) == (True, '')
    # Same cwd but another runtime cannot be upgraded by swapping .venv.
    assert updater.unit_runtime_check({
        'WorkingDirectory': str(project),
        'ExecStart': '{ path=/opt/external/bin/acm-web ; argv[]=/opt/external/bin/acm-web ; }',
    }, project)[0] is False


def test_edit_during_candidate_build_is_refused_without_stop(site, monkeypatch):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    old = head(site)
    original = updater.build_candidate_venv

    def build_then_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        write(site.project / 'README.md', '# edited while building\n')
        return result

    monkeypatch.setattr(updater, 'build_candidate_venv', build_then_edit)
    code = site.run('--mode', 'systemd', '--skip-frontend-build',
                    '--python', str(site.stubs / 'fake-python'),
                    '--health-url', site.health.url, check=False)
    assert code == updater.EXIT_REFUSED
    assert head(site) == old
    assert not site.calls('systemctl stop'), 'service must not be stopped after a concurrent edit'
    assert (site.project / 'README.md').read_text() == '# edited while building\n'


def test_verbose_traceback_is_redacted(site, monkeypatch, capsys):
    def explode(cfg):
        raise RuntimeError('boom https://ops:s3cret@example.invalid/x?token=abc123')

    monkeypatch.setattr(updater, 'run_update', explode)
    code = updater.main(['--project', str(site.project), '--verbose'])
    assert code == updater.EXIT_FAILED
    stderr = capsys.readouterr().err
    assert 's3cret' not in stderr and 'abc123' not in stderr
    assert '***@' in stderr


def test_docker_backup_failure_restarts_old_container_with_health(site, monkeypatch):
    site.release({'src/acmweb/__init__.py': 'VERSION = 2\n'})
    old = head(site)
    calls = []
    real_start = updater.start_service

    def failing_backup(*args, **kwargs):
        raise updater.UpdateFailed('synthetic backup failure')

    def recording_start(*args, **kwargs):
        calls.append(kwargs.get('expected_image'))
        return real_start(*args, **kwargs)

    monkeypatch.setattr(updater, 'backup_data', failing_backup)
    monkeypatch.setattr(updater, 'start_service', recording_start)
    code = site.run('--mode', 'docker', '--health-url', site.health.url,
                    '--health-timeout', '3', '--health-interval', '0.05', check=False)
    assert code == updater.EXIT_FAILED
    assert calls == ['sha256:old-image'], 'old container must be restarted with the previous image'
    assert head(site) == old
    assert site.health.requests, 'recovery must verify health, not just issue a start command'
    container = json.loads((site.docker_state / 'container.json').read_text())
    assert container['image'] == 'sha256:old-image' and container['running'] is True


# ---------------------------------------------------------------------------
# health checks and input validation


def test_health_probe_uses_configured_host_header():
    server = HealthServer(ok_health)
    try:
        ok, detail = updater.probe_health(server.url, host_header='acm.example.com')
        assert ok, detail
        assert server.host_headers[-1] == 'acm.example.com'
        assert server.requests[-1] == '/api/health'
    finally:
        server.close()


def test_health_probe_rejects_error_and_redirect():
    def responder(request):
        if request.path == '/api/health':
            return 503, '{"detail":"nope"}'
        return 200, '{"status":"ok"}'

    server = HealthServer(responder)
    try:
        ok, detail = updater.probe_health(server.url)
        assert not ok and '503' in detail
    finally:
        server.close()


def test_public_origin_host_and_validation():
    cfg = updater.UpdateConfig(project=Path('/tmp/x'), public_origin='https://acm.example.com')
    assert updater.resolve_public_origin_host(cfg, Path('/tmp/x')) == 'acm.example.com'
    bad = updater.UpdateConfig(project=Path('/tmp/x'), public_origin='http://acm.example.com')
    with pytest.raises(updater.UsageError):
        updater.resolve_public_origin_host(bad, Path('/tmp/x'))
    with pytest.raises(updater.UsageError):
        updater.parse_health_url('http://user:pass@127.0.0.1:8000/api/health')


def test_input_validation_never_reaches_a_shell(site, tmp_path):
    marker = tmp_path / 'pwned'
    for args in (
        ['--mode', 'systemd', '--ref', '--upload-pack=touch ' + str(marker)],
        ['--mode', 'systemd', '--service', 'acm-lab;touch ' + str(marker)],
        ['--mode', 'systemd', '--health-url', 'http://user:pass@127.0.0.1:1/x'],
    ):
        code = site.run(*args, check=False)
        assert code == updater.EXIT_USAGE
    assert not marker.exists()
    assert not site.calls('systemctl stop')


def test_runner_uses_argv_without_shell(tmp_path):
    logger = updater.logging.getLogger('test-runner')
    runner = updater.Runner(logger)
    target = tmp_path / 'a b; touch pwned'
    target.write_text('x')
    output = runner.output([sys.executable, '-c', 'import sys; print(sys.argv[1])', str(target)])
    assert output == str(target)
    assert not (tmp_path / 'pwned').exists()
