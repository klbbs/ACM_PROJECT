"""Independent updater acceptance: real temporary Git and HTTP, stubbed runtimes.

Never calls a user's Docker daemon/systemd, never uses a personal deployment.
"""
import hashlib
import json
import logging
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from acmweb import updater as u


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    seed = tmp_path / 'seed'
    subprocess.run(['git', 'clone', str(remote), str(seed)], check=True, capture_output=True)
    git(seed, 'config', 'user.email', 'fixture@example.invalid')
    git(seed, 'config', 'user.name', 'Fixture')
    git(seed, 'checkout', '-b', 'main')
    (seed / 'app.txt').write_text('old source')
    (seed / 'compose.yaml').write_text(json.dumps({'services': {'web': {
        'image': 'acm-lab:0.19.1', 'build': {'context': '.', 'target': 'runtime-old'}}}}))
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'old')
    git(seed, 'push', '-u', 'origin', 'main')
    project = tmp_path / 'installed'
    subprocess.run(['git', 'clone', '--branch', 'main', str(remote), str(project)],
                   check=True, capture_output=True)
    old = git(project, 'rev-parse', 'HEAD')
    (seed / 'app.txt').write_text('new source')
    (seed / 'compose.yaml').write_text(json.dumps({'services': {'web': {
        'image': 'acm-lab:0.20.0', 'build': {'context': '.', 'target': 'runtime-new'}}}}))
    (seed / 'new.txt').write_text('new file')
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'new')
    git(seed, 'push')
    new = git(seed, 'rev-parse', 'HEAD')
    (project / 'data').mkdir()
    (project / 'data/learning.txt').write_text('private learning')
    (project / 'account.toml').write_text('private synthetic config')
    (project / '.env').write_text('ACM_PUBLIC_ORIGIN=https://lab.example.invalid\n')
    (project / 'personal.txt').write_text('untracked notes')
    (project / '.venv/bin').mkdir(parents=True)
    (project / '.venv/bin/python').write_text('old runtime')
    (project / 'frontend/dist').mkdir(parents=True)
    (project / 'frontend/dist/index.html').write_text('old frontend')
    conn = sqlite3.connect(project / 'data/state.sqlite3')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=0')
    conn.execute('CREATE TABLE records (value TEXT)')
    conn.execute("INSERT INTO records VALUES ('committed WAL')")
    conn.commit()
    install = u.Install(mode='systemd', service='fixture.service', was_active=True)
    monkeypatch.setattr(u, 'detect_install', lambda *a, **kw: install)
    monkeypatch.setattr(u, 'is_root', lambda: True)
    logger = logging.getLogger('update.acceptance')
    runner = u.Runner(logger)
    cfg = u.UpdateConfig(project=project, mode='systemd', skip_frontend_build=True,
                         health_timeout=.08, health_interval=.01)
    fixture = dict(project=project, seed=seed, remote=remote, old=old, new=new,
                   conn=conn, install=install, logger=logger, runner=runner, cfg=cfg)
    yield fixture
    conn.close()


def plan(f, fetch=True):
    return u.build_plan(f['cfg'], f['runner'], f['logger'], fetch=fetch)


def tree_digest(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def health_server():
    class Handler(BaseHTTPRequestHandler):
        healthy = True
        hosts = []
        def do_GET(self):
            Handler.hosts.append(self.headers.get('Host'))
            self.send_response(200 if Handler.healthy else 503)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}' if Handler.healthy else b'{}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/api/health', Handler
    server.shutdown()
    thread.join()
    server.server_close()


def stub_systemd(f, monkeypatch, health_server, *, failed_health=False, build_error=False):
    """Only OS/runtime/build boundaries stubbed; Git/data/switch/HTTP remain real."""
    url, handler = health_server
    f['cfg'].health_url = url
    events = []
    def build(*args):
        events.append('build')
        if build_error:
            raise u.UpdateFailed('synthetic build failure')
        destination = args[2]
        (destination / 'bin').mkdir(parents=True)
        (destination / 'bin/python').write_text('candidate runtime')
        return destination
    monkeypatch.setattr(u, 'build_candidate_venv', build)
    monkeypatch.setattr(u, 'build_frontend', lambda *a: None)
    monkeypatch.setattr(u, 'stop_service', lambda *a: events.append('stop'))
    def start(*args, **kwargs):
        current = git(f['project'], 'rev-parse', 'HEAD')
        events.append('start-new' if current == f['new'] else 'start-old')
        handler.healthy = not (failed_health and current == f['new'])
        if current == f['new'] and failed_health:
            # Simulate an application migration committed by the failed new runtime.
            db = sqlite3.connect(f['project'] / 'data/state.sqlite3')
            db.execute("INSERT INTO records VALUES ('failed migration')")
            db.commit()
            db.close()
            (f['project'] / 'account.toml').write_text('changed by failed runtime')
    monkeypatch.setattr(u, 'start_service', start)
    monkeypatch.setattr(u, 'service_alive', lambda *a: lambda: True)
    monkeypatch.setattr(u, 'systemd_main_pid', lambda *a: 123)
    return events, handler


def apply(f):
    p = plan(f)
    state = f['project'] / '.acm-update'
    u.prepare_state_dirs(state)
    return u.apply_update(f['cfg'], f['runner'], f['logger'], p, state, 'fixture', None)


def test_preflight_real_remote_has_no_fetch_lock_or_file_writes(installation):
    f = installation
    # Quiesce this fixture writer before measuring; WAL behavior is covered separately.
    f['conn'].close()
    before = tree_digest(f['project'])
    p = plan(f, fetch=False)
    assert p.target_sha == f['new'] and not p.target_known
    f['cfg'].check = True
    assert u.run_update(f['cfg']) == 0
    assert git(f['project'], 'rev-parse', 'origin/main') == f['old']
    assert tree_digest(f['project']) == before
    assert not (f['project'] / '.acm-update').exists()


@pytest.mark.parametrize('staged', [False, True])
def test_reject_dirty_tracked_worktree_and_index(installation, staged):
    f = installation
    (f['project'] / 'app.txt').write_text('user edit')
    if staged:
        git(f['project'], 'add', 'app.txt')
    with pytest.raises(u.Refused, match='工作区或索引'):
        plan(f)
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']
    assert (f['project'] / 'app.txt').read_text() == 'user edit'


def test_update_lock_excludes_second_operator(tmp_path):
    path = tmp_path / 'update.lock'
    logger = logging.getLogger('lock.acceptance')
    with u.UpdateLock(path, logger):
        with pytest.raises(u.Refused):
            with u.UpdateLock(path, logger):
                pytest.fail('second operator acquired lock')
    with u.UpdateLock(path, logger):
        pass


@pytest.mark.parametrize('path', ['account.toml', 'data/exfiltrate.txt', 'personal.txt'])
def test_upstream_private_path_or_untracked_collision_refused(installation, path):
    f = installation
    target = f['seed'] / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('must never overwrite user data')
    git(f['seed'], 'add', path)
    git(f['seed'], 'commit', '-m', 'unsafe addition')
    git(f['seed'], 'push')
    with pytest.raises(u.Refused):
        plan(f)
    assert (f['project'] / 'personal.txt').read_text() == 'untracked notes'
    assert (f['project'] / 'account.toml').read_text() == 'private synthetic config'


def test_candidate_build_failure_never_stops_old_service(installation, monkeypatch, health_server):
    f = installation
    events, _ = stub_systemd(f, monkeypatch, health_server, build_error=True)
    with pytest.raises(u.UpdateFailed, match='synthetic build failure'):
        apply(f)
    assert events == ['build']
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']
    assert (f['project'] / '.venv/bin/python').read_text() == 'old runtime'


def test_fast_forward_actual_git_and_actual_http_health(installation, monkeypatch, health_server):
    f = installation
    events, handler = stub_systemd(f, monkeypatch, health_server)
    assert apply(f) == 0
    assert events == ['build', 'stop', 'start-new']
    assert git(f['project'], 'rev-parse', 'HEAD') == f['new']
    assert git(f['project'], 'status', '--porcelain', '--untracked-files=no') == ''
    assert (f['project'] / 'app.txt').read_text() == 'new source'
    assert (f['project'] / '.venv/bin/python').read_text() == 'candidate runtime'
    assert (f['project'] / 'personal.txt').read_text() == 'untracked notes'
    assert handler.hosts == ['lab.example.invalid']
    manifest = json.loads((f['project'] / '.acm-update/backups/fixture/manifest.json').read_text())
    assert manifest['status'] == 'ok'
    assert manifest['old_sha'] == f['old']


def test_failed_health_restores_git_runtime_sqlite_wal_and_credentials(installation, monkeypatch, health_server):
    f = installation
    events, _ = stub_systemd(f, monkeypatch, health_server, failed_health=True)
    with pytest.raises(u.UpdateFailed, match='健康检查'):
        apply(f)
    assert events == ['build', 'stop', 'start-new', 'stop', 'start-old']
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']
    assert git(f['project'], 'status', '--porcelain', '--untracked-files=no') == ''
    assert (f['project'] / 'app.txt').read_text() == 'old source'
    assert not (f['project'] / 'new.txt').exists()
    assert (f['project'] / '.venv/bin/python').read_text() == 'old runtime'
    assert not (f['project'] / '.venv').is_symlink()
    assert (f['project'] / 'account.toml').read_text() == 'private synthetic config'
    with sqlite3.connect(f['project'] / 'data/state.sqlite3') as db:
        assert db.execute('SELECT value FROM records').fetchall() == [('committed WAL',)]
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    with sqlite3.connect(f['project'] / '.acm-update/failed/fixture/data/state.sqlite3') as db:
        assert ('failed migration',) in db.execute('SELECT value FROM records').fetchall()
    manifest = json.loads((f['project'] / '.acm-update/backups/fixture/manifest.json').read_text())
    assert manifest['status'] == 'rolled_back' and not manifest['rollback_problems']
    assert (f['project'] / 'personal.txt').read_text() == 'untracked notes'


@pytest.mark.parametrize('rollback_healthy', [True, False])
def test_docker_stub_restores_previous_image_and_checks_rollback_health(
        installation, monkeypatch, health_server, rollback_healthy):
    """Image command state is a stub: no installed Docker engine is used."""
    f = installation
    url, handler = health_server
    f['cfg'].health_url = url
    f['install'].mode = 'docker'
    f['install'].image = 'fixture:current'
    f['install'].service = 'web'
    tags = {'fixture:current': 'old-image'}
    events = []
    monkeypatch.setattr(u, 'docker_image_exists', lambda image, runner: image in tags)
    def build(*args):
        tags['fixture:candidate'] = 'new-image'
        return {'candidate_tag': 'fixture:candidate', 'rollback_tag': 'fixture:rollback',
                'old_image_id': 'old-image', 'new_image_id': 'new-image'}
    monkeypatch.setattr(u, 'build_candidate_image', build)
    def tag(source, target, runner):
        tags[target] = tags.get(source, source)
    monkeypatch.setattr(u, 'docker_tag', tag)
    monkeypatch.setattr(u, 'stop_service', lambda *a: events.append('stop'))
    monkeypatch.setattr(u, 'container_alive', lambda *a: lambda: True)
    def start(*args, **kwargs):
        expected = kwargs['expected_image']
        assert tags['fixture:current'] == expected
        events.append(expected)
        handler.healthy = expected == 'old-image' and rollback_healthy
    monkeypatch.setattr(u, 'start_service', start)
    with pytest.raises(u.UpdateFailed):
        apply(f)
    assert tags['fixture:current'] == 'old-image'
    assert events == ['stop', 'new-image', 'stop', 'old-image']
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']
    manifest = json.loads((f['project'] / '.acm-update/backups/fixture/manifest.json').read_text())
    assert manifest['status'] == ('rolled_back' if rollback_healthy else 'rollback_incomplete')
    # An actual HTTP probe must have occurred after the old image restart.
    assert len(handler.hosts) >= 3


def test_stop_command_uncertain_failure_attempts_restore_old_service(
        installation, monkeypatch, health_server):
    f = installation
    events, _ = stub_systemd(f, monkeypatch, health_server)
    def stopped_but_status_failed(*args):
        events.append('stop-requested')
        raise u.UpdateFailed('synthetic stop status unavailable after command')
    monkeypatch.setattr(u, 'stop_service', stopped_but_status_failed)
    with pytest.raises(u.UpdateFailed):
        apply(f)
    assert events == ['build', 'stop-requested', 'start-old']
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']


def test_healthy_http_does_not_override_dead_target_service(health_server):
    url, handler = health_server
    # An unrelated/old listener can return ok; target runtime must also be alive.
    handler.healthy = True
    with pytest.raises(u.UpdateFailed):
        u.wait_health(url, 'lab.example.invalid', .05, .01, alive=lambda: False)


def test_systemd_same_pid_rejected_without_real_systemctl(monkeypatch, tmp_path):
    class StubRunner:
        def run(self, argv, **kwargs):
            assert argv == ['systemctl', 'start', 'fixture.service']
    monkeypatch.setattr(u, 'systemd_state', lambda *a: 'active')
    monkeypatch.setattr(u, 'systemd_main_pid', lambda *a: 777)
    with pytest.raises(u.UpdateFailed, match='MainPID'):
        u.start_service(u.UpdateConfig(project=tmp_path),
                        u.Install(mode='systemd', service='fixture.service'),
                        StubRunner(), logging.getLogger('samepid'), previous_pid=777)


def test_non_fast_forward_remote_is_refused(installation):
    f = installation
    git(f['seed'], 'checkout', '--orphan', 'unrelated')
    git(f['seed'], 'rm', '--ignore-unmatch', '-rf', '.')
    (f['seed'] / 'app.txt').write_text('unrelated replacement')
    git(f['seed'], 'add', '.')
    git(f['seed'], 'commit', '-m', 'unrelated root')
    git(f['seed'], 'push', '--force', 'origin', 'HEAD:main')
    with pytest.raises(u.Refused, match='快进'):
        plan(f)
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']
    assert (f['project'] / 'app.txt').read_text() == 'old source'


def test_upstream_symlink_rejected_before_service_stop(installation, monkeypatch, health_server):
    f = installation
    (f['seed'] / 'outside-link').symlink_to('/tmp/not-a-real-secret')
    git(f['seed'], 'add', 'outside-link')
    git(f['seed'], 'commit', '-m', 'symlink candidate')
    git(f['seed'], 'push')
    events, _ = stub_systemd(f, monkeypatch, health_server)
    with pytest.raises(u.UpdateFailed, match='链接'):
        apply(f)
    assert events == []
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']


def test_native_service_using_external_runtime_is_not_silently_accepted(
        installation, monkeypatch):
    f = installation
    cfg = u.UpdateConfig(project=f['project'], mode='systemd', service='fixture.service')
    monkeypatch.setattr(u.shutil, 'which', lambda name: '/usr/bin/systemctl')
    monkeypatch.setattr(u, 'systemd_show', lambda *a: {
        'LoadState': 'loaded', 'ActiveState': 'active',
        'WorkingDirectory': str(f['project']),
        'ExecStart': '{ path=/opt/other-runtime/bin/acm-web ; argv[]=/opt/other-runtime/bin/acm-web ; }',
        'FragmentPath': '/etc/systemd/system/fixture.service',
    })
    # Merely matching cwd cannot establish that swapping .venv upgrades this service.
    with pytest.raises(u.Refused):
        u.detect_systemd(f['project'], cfg, f['runner'], f['logger'])


def test_operator_edit_during_candidate_build_is_not_overwritten(
        installation, monkeypatch, health_server):
    f = installation
    events, _ = stub_systemd(f, monkeypatch, health_server)
    original_build = u.build_candidate_venv
    def build_with_concurrent_edit(*args):
        result = original_build(*args)
        (f['project'] / 'app.txt').write_text('operator edit while building')
        return result
    monkeypatch.setattr(u, 'build_candidate_venv', build_with_concurrent_edit)
    with pytest.raises(u.UpdateError):
        apply(f)
    assert events == ['build']
    assert (f['project'] / 'app.txt').read_text() == 'operator edit while building'
    assert git(f['project'], 'rev-parse', 'HEAD') == f['old']


class ComposeVersionStub(u.Runner):
    """Docker argv interpreter reading actual current/candidate compose files.

    Does not trust updater's expected_image: up chooses the tag from config.
    No Docker engine, systemd or real image build is invoked.
    """
    def __init__(self, logger, handler, fail_new_health):
        super().__init__(logger)
        self.tags = {'acm-lab:0.19.1': 'old-image-id'}
        self.running = 'old-image-id'
        self.starts = []
        self.builds = []
        self.handler = handler
        self.fail_new_health = fail_new_health

    def config(self, argv):
        files = [Path(argv[i + 1]) for i, x in enumerate(argv) if x == '-f']
        merged = {}
        for path in files:
            doc = json.loads(path.read_text())
            for name, config in doc['services'].items():
                service = merged.setdefault(name, {})
                for key, value in config.items():
                    if key == 'build' and isinstance(value, dict):
                        service.setdefault(key, {}).update(value)
                    else:
                        service[key] = value
        return {'services': merged}

    def run(self, argv, cwd=None, check=True, **kwargs):
        if argv[0] != 'docker':
            return super().run(argv, cwd=cwd, check=check, **kwargs)
        out = ''
        code = 0
        if argv[1:3] == ['image', 'inspect']:
            tag = argv[-1]
            code = 0 if tag in self.tags else 1
            out = self.tags.get(tag, '')
        elif argv[1] == 'tag':
            assert argv[2] in self.tags
            self.tags[argv[3]] = self.tags[argv[2]]
        elif argv[1] == 'inspect':
            out = self.running or ''
        elif argv[1] == 'compose':
            if 'config' in argv:
                out = json.dumps(self.config(argv))
            elif 'build' in argv:
                service = self.config(argv)['services'][argv[-1]]
                target = service['build'].get('target')
                self.builds.append((service['image'], target))
                self.tags[service['image']] = ('new-image-id' if target == 'runtime-new'
                                               else 'wrong-build-target-image-id')
            elif 'stop' in argv:
                self.running = None
            elif 'up' in argv:
                assert '--no-build' in argv
                service = self.config(argv)['services'][argv[-1]]
                tag = service['image']
                if tag not in self.tags:
                    raise u.UpdateFailed('stub: compose requested missing image tag ' + tag)
                self.running = self.tags[tag]
                self.starts.append((tag, self.running))
                self.handler.healthy = not (self.fail_new_health and tag == 'acm-lab:0.20.0')
            elif 'ps' in argv:
                out = 'fixture-container' if self.running else ''
            elif 'version' in argv:
                out = 'stub compose v2'
            else:
                raise AssertionError('Unexpected compose argv: ' + repr(argv))
        else:
            raise AssertionError('Unexpected Docker argv: ' + repr(argv))
        if code and check:
            raise u.UpdateFailed('stub Docker failure')
        return subprocess.CompletedProcess(argv, code, out, '')


@pytest.mark.parametrize('fail_new_health', [False, True])
def test_docker_version_tag_and_build_target_follow_target_compose(
        installation, health_server, fail_new_health):
    f = installation
    url, handler = health_server
    f['cfg'].health_url = url
    f['install'].mode = 'docker'
    f['install'].service = 'web'
    f['install'].image = 'acm-lab:0.19.1'
    f['install'].build_target = 'runtime-old'
    f['install'].compose_files = [f['project'] / 'compose.yaml']
    runner = ComposeVersionStub(f['logger'], handler, fail_new_health)
    f['runner'] = runner
    if fail_new_health:
        with pytest.raises(u.UpdateFailed):
            apply(f)
        assert runner.starts == [('acm-lab:0.20.0', 'new-image-id'),
                                 ('acm-lab:0.19.1', 'old-image-id')]
        assert git(f['project'], 'rev-parse', 'HEAD') == f['old']
    else:
        assert apply(f) == 0
        assert runner.starts == [('acm-lab:0.20.0', 'new-image-id')]
        assert git(f['project'], 'rev-parse', 'HEAD') == f['new']
    assert runner.builds and runner.builds[0][1] == 'runtime-new'
    assert runner.tags['acm-lab:0.19.1'] == 'old-image-id'
    assert runner.tags['acm-lab:0.20.0'] == 'new-image-id'
    manifest = json.loads((f['project'] / '.acm-update/backups/fixture/manifest.json').read_text())
    assert manifest['status'] == ('rolled_back' if fail_new_health else 'ok')
