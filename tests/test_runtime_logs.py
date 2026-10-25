import asyncio
import json
import stat

from fastapi import FastAPI
from fastapi.testclient import TestClient

from acmweb.runtime_logs import RuntimeLogMiddleware, RuntimeLogs, register_admin_logs


def test_runtime_store_allowlists_metadata_and_sets_private_permissions(tmp_path):
    store = RuntimeLogs(tmp_path / 'logs')
    store.emit('request.failed', level='ERROR', method='POST', route='/api/accounts',
               status=500, duration_ms=12, password='secret', cookie='private', body='source')

    path = tmp_path / 'logs' / 'runtime.jsonl'
    record = json.loads(path.read_text())
    assert record['event'] == 'request.failed'
    assert record['details'] == {'method': 'POST', 'route': '/api/accounts',
                                 'status': 500, 'duration_ms': 12}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert all(word not in path.read_text() for word in ('secret', 'private', 'source'))


def test_runtime_store_rotates_bounded_files_and_pages_newest_first(tmp_path):
    store = RuntimeLogs(tmp_path / 'logs', max_bytes=1024, backup_count=2)
    for index in range(40):
        store.emit('request.completed', method='GET', route='/api/overview',
                   status=200, duration_ms=index)

    page = store.listing(limit=5)
    assert [row['details']['duration_ms'] for row in page['items']] == list(range(39, 34, -1))
    assert page['has_more'] and page['next_cursor'] == page['items'][-1]['id']
    assert page['storage']['file_count'] <= 3
    assert sum(file.stat().st_size for file in (tmp_path / 'logs').iterdir()) <= 3 * 1024


def test_daily_count_metadata_is_bounded_and_does_not_accept_provider_text(tmp_path):
    store = RuntimeLogs(tmp_path / 'logs')
    store.emit('daily.failed', level='ERROR', reason='daily_invalid_count',
               requested_count=4, actual_count=5, raw_error='PRIVATE-SOURCE')
    assert store.listing()['items'][0]['details'] == {
        'reason': 'daily_invalid_count', 'requested_count': 4, 'actual_count': 5,
    }
    store.emit('daily.failed', reason='PRIVATE-KEY', requested_count=True, actual_count=1001)
    assert store.listing()['items'][0]['details'] == {}
    assert 'PRIVATE' not in (tmp_path / 'logs' / 'runtime.jsonl').read_text()


def test_training_diagnostics_keep_only_bounded_counts_and_known_mode(tmp_path):
    store = RuntimeLogs(tmp_path / 'logs')
    store.emit('training.selection_failed', level='WARNING',
               reason='training_template_unavailable', train_mode='learn',
               requested_count=4, template_count=0, candidate_count=3200,
               eligible_count=17, cookie='PRIVATE-COOKIE', source='PRIVATE-SOURCE',
               error='PRIVATE-ERROR')
    assert store.listing()['items'][0]['details'] == {
        'reason': 'training_template_unavailable', 'train_mode': 'learn',
        'requested_count': 4, 'template_count': 0, 'candidate_count': 3200,
        'eligible_count': 17,
    }
    store.emit('training.selected', train_mode='PRIVATE-MODE', template_count=True,
               candidate_count=-1, eligible_count=100001)
    assert store.listing()['items'][0]['details'] == {}
    assert 'PRIVATE' not in (tmp_path / 'logs' / 'runtime.jsonl').read_text()


def test_admin_log_route_calls_authorizer_and_does_not_reflect_untrusted_file_data(tmp_path):
    store = RuntimeLogs(tmp_path / 'logs')
    store.emit('request.completed', method='GET', route='/api/overview', status=200)
    app = FastAPI()
    checks = []
    register_admin_logs(app, store, lambda request: checks.append(request.url.path))

    with TestClient(app) as client:
        result = client.get('/api/admin/logs')

    assert result.status_code == 200
    assert checks == ['/api/admin/logs']
    assert result.json()['items'][0]['event'] == 'request.completed'


def test_middleware_logs_failure_without_query_or_exception_text(tmp_path):
    store = RuntimeLogs(tmp_path / 'logs')

    async def app(scope, receive, send):
        raise RuntimeError('private-token-and-code')

    middleware = RuntimeLogMiddleware(app, store)
    scope = {'type': 'http', 'method': 'POST', 'path': '/api/submit',
             'query_string': b'token=private-query', 'headers': [], 'state': {}}

    async def run():
        try:
            await middleware(scope, lambda: None, lambda message: None)
        except RuntimeError:
            pass

    asyncio.run(run())
    text = (tmp_path / 'logs' / 'runtime.jsonl').read_text()
    record = json.loads(text)
    assert record['event'] == 'request.failed'
    assert record['details']['route'] == '/unmatched'
    assert all(secret not in text for secret in ('private-token-and-code', 'private-query'))


def test_invalid_event_and_symlink_target_are_not_written(tmp_path):
    store = RuntimeLogs(tmp_path / 'logs')
    store.emit('untrusted.event', secret='private')
    assert not (tmp_path / 'logs').exists()

    outside = tmp_path / 'outside'
    outside.write_text('')
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs' / 'runtime.jsonl').symlink_to(outside)
    store.emit('service.started', mode='single')
    assert outside.read_text() == ''
    assert store.last_error == 'storage_unavailable'


def test_nested_dispatch_records_child_route_once_with_only_known_reason(tmp_path):
    from acmweb.runtime_logs import record_failure
    from types import SimpleNamespace
    store = RuntimeLogs(tmp_path / 'logs')

    async def child(scope, receive, send):
        scope['route'] = SimpleNamespace(path='/api/ai/settings')
        record_failure(scope, '该 AI 提供方域名尚未加入服务器白名单，请联系管理员在管理后台添加')
        record_failure(scope, 'PRIVATE-cookie=secret')
        await send({'type': 'http.response.start', 'status': 400, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    async def dispatch(scope, receive, send):
        scope['route'] = SimpleNamespace(path='/{path:path}')
        await RuntimeLogMiddleware(child, store)({**scope}, receive, send)

    async def run():
        async def send(message): pass
        await RuntimeLogMiddleware(dispatch, store)(
            {'type': 'http', 'path': '/api/ai/settings', 'method': 'PUT', 'state': {}}, None, send)
    asyncio.run(run())
    rows = store.listing()['items']
    assert len(rows) == 1
    assert rows[0]['details']['route'] == '/api/ai/settings'
    assert rows[0]['details']['reason'] == 'ai_host_not_allowed'
    assert 'PRIVATE' not in json.dumps(rows)
