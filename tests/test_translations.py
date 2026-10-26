import asyncio
import copy
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from acmweb.ai import AIResult, ProviderConfig
from acmweb.app import create_app
from acmweb.settings import Settings
from acmweb.translations import Document, fingerprint, Translations, Translate, fragment_blocks, model_identity, TOKEN
from acmweb.statement_markdown import RENDER_VERSION, html_markdown
from acmweb.codex_bridge import AIError
from fastapi import HTTPException
from acmweb.users import csrf_token
from acmweb.workbench import parse_statement


HTML = '''<div class="problem-statement"><div class="header"><div class="title">A. Translate me</div></div>
<div><p>Find the value of <span>$$x + 1$$</span>.</p><p>Use https://example.test/a.</p></div>
<div class="sample-tests"><div class="sample-test"><div class="input"><pre>1</pre></div><div class="output"><pre>2</pre></div></div></div></div>'''

# Synthetic section-title containers model the sanitizer's div-to-h2 upgrade.
CF_SECTION_DIV_HTML = '''<p>Compute the value of $$x + 1$$.</p>
<div class="section-title">Input</div><p>One integer x is given.</p>
<div class="section-title">Output</div><p>Print one integer.</p>
<div class="section-title">Note</div><p>See the diagram.</p>
<img alt="Synthetic diagram" src="https://example.test/synthetic-diagram.png"/>'''


def _cf_section_statement(statement, tag='div', render_version=3):
    body = CF_SECTION_DIV_HTML
    if tag == 'h2':
        body = body.replace('<div class="section-title">', '<h2 class="section-title">').replace('</div>', '</h2>')
    markdown = html_markdown(body)
    return {**copy.deepcopy(statement), 'body_html': body, 'render_version': render_version,
            'markdown': markdown, 'sections': [markdown],
            'images': ['https://example.test/synthetic-diagram.png']}


@pytest.fixture
def workspace(tmp_path):
    config = tmp_path / 'account.toml'
    config.write_text('[[accounts]]\nplatform="codeforces"\nhandle="demo"\n')
    app = create_app(Settings(data_dir=tmp_path, config_path=config, enable_codex=False))
    statement = parse_statement(HTML, 'codeforces:4-A')
    statement['source'] = 'official'
    with app.state.workbench.db.connect() as db:
        db.execute('INSERT INTO wb_problems VALUES(?,?,?)', ('codeforces:4-A', json.dumps(statement), int(time.time())))
    with TestClient(app) as client:
        yield app, client, statement


def test_translation_job_upload_review_and_free_reuse(workspace, monkeypatch):
    app, client, statement = workspace
    app.state.ai.save(ProviderConfig(base_url='https://api.example.test/v1', vendor='custom', protocol='chat'))
    async def complete(task, provider, model, payload, schema):
        assert task == 'translation'
        assert provider == 'api' and model == 'mock-model'
        return AIResult(
            data={'blocks': [{'id': block['id'], 'text': '译文 ' + block['text']} for block in payload['blocks']]},
            provenance={'channel': 'api', 'provider': 'api', 'endpoint': 'https://api.example.test/v1',
                        'vendor': 'custom', 'protocol': 'chat', 'model': model,
                        'task': task, 'prompt_version': 'test'},
        )

    monkeypatch.setattr(app.state.ai, 'complete', complete)
    queued = client.post('/api/workbench/problem/4-A/translate', json={
        'engine': 'ai', 'provider': 'api', 'model': 'mock-model', 'consent': True,
    })
    assert queued.status_code == 200, queued.text
    job_id = queued.json()['id']
    job = None
    for _ in range(50):
        job = next(item for item in client.get('/api/jobs').json()['items'] if item['id'] == job_id)
        if job['status'] not in {'queued', 'running'}:
            break
        time.sleep(0.01)
    assert job['status'] == 'completed', job
    assert set(job['result']) == {'translation_id', 'reused'}
    tid = job['result']['translation_id']
    listing = client.get('/api/workbench/problem/4-A/translations')
    assert listing.status_code == 200
    item = next(item for item in listing.json()['items'] if item['id'] == tid)
    assert item['scope'] == 'private' and item['shareable'] is True
    # The server accepts only a reference to its own generated result.
    upload = client.post(f'/api/translations/{tid}/upload', json={})
    assert upload.status_code == 200, upload.text
    shared_id = upload.json()['id']
    assert client.post(f'/api/translations/{tid}/upload', json={}).status_code == 409
    assert client.get('/api/translations/review').json()['items'][0]['id'] == shared_id
    assert client.post(f'/api/translations/{shared_id}/review', json={'decision': 'approved', 'note': 'ok'}).status_code == 200
    shared = client.get('/api/workbench/problem/4-A/translations').json()['items']
    approved = next(item for item in shared if item['id'] == shared_id)
    assert approved['scope'] == 'shared' and approved['status'] == 'approved'

    # Any later translation request reuses the approved central result without
    # invoking the AI transport again.
    async def should_not_run(*args, **kwargs):
        raise AssertionError('approved shared translation should be free')

    monkeypatch.setattr(app.state.ai, 'complete', should_not_run)
    second = app.state.translations.generate('4-A', type('Payload', (), {
        'engine': 'ai', 'provider': 'api', 'model': 'mock-model', 'consent': True,
    })())
    result = asyncio.run(second)
    assert result == {'translation_id': shared_id, 'reused': True}

    # A second model must remain available for a distinct contribution, rather
    # than silently returning an unrelated model's cached translation.
    monkeypatch.setattr(app.state.ai, 'complete', complete)
    async def other_model(task, provider, model, payload, schema):
        result = await complete(task, provider, 'mock-model', payload, schema)
        result.provenance['model'] = model
        return result
    monkeypatch.setattr(app.state.ai, 'complete', other_model)
    second = asyncio.run(app.state.translations.generate('4-A', Translate(
        model='another-model', consent=True)))
    assert second['reused'] is False
    uploaded = client.post(f"/api/translations/{second['translation_id']}/upload", json={})
    assert uploaded.status_code == 200


def test_private_translation_ttl_limits_restart_and_legacy_cleanup(workspace):
    app, _, statement = workspace
    service = app.state.translations
    service.private_limit = 2
    for i in range(3):
        service._remember({'id': str(i), 'problem_id': 'codeforces:4-A',
                           'fingerprint': fingerprint(statement), 'statement': {'title': 'PRIVATE-' + str(i)}})
    assert set(service.ephemeral) == {'1', '2'}
    item = service.get('1')
    item['statement']['title'] = 'tampered'
    assert service.get('1')['statement']['title'] == 'PRIVATE-1'
    service.ephemeral['1'] = (time.monotonic() - 1, *service.ephemeral['1'][1:])
    with pytest.raises(HTTPException): service.get('1')
    assert '1' not in service.ephemeral
    with service.db.connect() as db:
        db.execute('CREATE TABLE private_translations(data TEXT)')
        db.execute('INSERT INTO private_translations VALUES(?)', ('LEGACY-PRIVATE-TEXT',))
        db.execute('INSERT INTO web_jobs VALUES(?,?,?,?,?,?,?)', ('legacy-translation', 'translation', 'completed', 1, 1,
                   json.dumps({'statement': 'LEGACY-PRIVATE-TEXT'}), None))
    restarted = Translations(service.wb, service.ai, service.shared)
    assert restarted.ephemeral == {}
    with service.db.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_translations'").fetchone()
        assert db.execute("SELECT result FROM web_jobs WHERE id='legacy-translation'").fetchone()[0] is None


def test_private_translation_expires_without_next_request(workspace):
    service = workspace[0].state.translations
    service.private_ttl = 0.01
    async def expire():
        service._remember({'id': 'short-lived', 'statement': {'title': 'temporary'}})
        await asyncio.sleep(0.03)
        assert 'short-lived' not in service.ephemeral
        assert 'short-lived' not in service.expiry_handles
    asyncio.run(expire())


def test_private_cache_eviction_and_shutdown_cancel_timers(workspace, monkeypatch):
    service = workspace[0].state.translations
    service.private_limit = 1
    handles = []
    class Handle:
        cancelled = False
        def cancel(self): self.cancelled = True
    class Loop:
        def call_later(self, delay, callback, tid):
            handle = Handle()
            handles.append(handle)
            return handle
    monkeypatch.setattr('acmweb.translations.asyncio.get_running_loop', lambda: Loop())
    for tid in ('first', 'second'):
        service._remember({'id': tid, 'statement': {'title': tid}})
    assert handles[0].cancelled and not handles[1].cancelled
    assert set(service.expiry_handles) == {'second'}
    service.close()
    assert handles[1].cancelled
    assert service.ephemeral == {} and service.expiry_handles == {}


def test_user_import_translation_cannot_enter_shared_storage(workspace, monkeypatch):
    app, client, statement = workspace
    imported = {**statement, 'source': 'user_import'}
    app.state.workbench.store_statement('codeforces:4-A', imported)
    async def complete(task, provider, model, payload, schema):
        return AIResult(data={'blocks': payload['blocks']}, provenance={'channel': 'api', 'model': model})
    monkeypatch.setattr(app.state.ai, 'complete', complete)
    result = asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture-model', consent=True)))
    item = app.state.translations.get(result['translation_id'])
    assert item['scope'] == 'private' and not item['shareable']
    uploaded = client.post(f"/api/translations/{result['translation_id']}/upload", json={})
    assert uploaded.status_code == 400
    with app.state.translation_store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM translations').fetchone()[0] == 0


def test_translation_fingerprint_excludes_fetch_metadata(workspace):
    _, _, statement = workspace
    before = fingerprint(statement)
    changed = {**statement, 'fetched_at': int(time.time()) + 100, 'diagnostics': {'warnings': ['temporary']}}
    assert fingerprint(changed) == before


def _legacy_translation_fingerprint(statement):
    """Historical hash fixture, independent of the implementation under test."""
    keys = ('id', 'url', 'title', 'body_html', 'sections', 'samples', 'images', 'time_limit', 'memory_limit')
    if statement.get('id', '').startswith('codeforces:') and statement.get('render_version', 0) >= 3:
        keys += ('render_version', 'markdown')
    content = {key: statement.get(key) for key in keys}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _seed_legacy_shared_translation(workspace, status='approved'):
    """Synthetic stored contribution: this does not contact any real platform."""
    app, _, statement = workspace
    service = app.state.translations
    source = {**copy.deepcopy(statement), 'render_version': 2,
              'sections': ['Old HTML-derived text'], 'markdown': 'Old renderer output'}
    doc = Document(source)
    translated = doc.apply({'blocks': [
        {'id': block['id'], 'text': '共享译文 ' + block['text']} for block in doc.blocks
    ]})
    config = service.ai.config()
    provenance = {'channel': 'api', 'endpoint': config['base_url'], 'vendor': config['vendor'],
                  'protocol': config['protocol'], 'model': 'legacy-fixture-model'}
    item = {'id': 'legacy-private-fixture', 'problem_id': source['id'],
            'fingerprint': fingerprint(statement), 'engine': 'ai', 'model': provenance['model'],
            'model_key': model_identity(provenance), 'provenance': provenance,
            'statement': translated, 'scope': 'private', 'shareable': True,
            'status': 'private', 'created_at': 1}
    service._remember(item)
    sid = service.shared.upload(service, 'other-user-fixture', item['id'])['id']
    service._expire(item['id'])
    item['fingerprint'] = _legacy_translation_fingerprint(source)
    with service.shared.connect() as db:
        db.execute('UPDATE translations SET fingerprint=?,source_statement=?,data=?,status=? WHERE id=?',
                   (item['fingerprint'], json.dumps(source), json.dumps(item), status, sid))
    return sid, item, source


def test_translation_fingerprint_ignores_only_derived_html_rendering(workspace):
    statement = workspace[2]
    rendered = {**statement, 'render_version': RENDER_VERSION + 1,
                'sections': ['New renderer output'], 'markdown': '# New rendering'}
    assert fingerprint(rendered) == fingerprint(statement)
    without_html = {**statement, 'body_html': '', 'sections': ['Original text'], 'markdown': 'Original text'}
    assert fingerprint({**without_html, 'sections': ['Changed prose']}) != fingerprint(without_html)
    assert fingerprint({**without_html, 'markdown': 'Changed prose'}) != fingerprint(without_html)


def test_cf_section_heading_tag_upgrade_keeps_original_content_fingerprint(workspace):
    legacy = _cf_section_statement(workspace[2], tag='div', render_version=3)
    current = _cf_section_statement(workspace[2], tag='h2', render_version=RENDER_VERSION)
    assert legacy['body_html'] != current['body_html']
    assert legacy['markdown'] != current['markdown']
    assert fingerprint(legacy) == fingerprint(current)
    # Also cover unchanged h2 source whose historical hash included only the
    # previous render version, as well as the genuinely older div sanitizer.
    assert fingerprint({**current, 'render_version': 3}) == fingerprint(current)
    assert _legacy_translation_fingerprint({**current, 'render_version': 3}) != _legacy_translation_fingerprint(current)


@pytest.mark.parametrize('change', ['paragraph', 'formula', 'sample', 'image_html', 'image_list', 'heading_text'])
def test_cf_heading_compatibility_does_not_hide_real_content_changes(workspace, change):
    legacy = _cf_section_statement(workspace[2], tag='div', render_version=3)
    current = _cf_section_statement(workspace[2], tag='h2', render_version=RENDER_VERSION)
    if change == 'paragraph':
        current['body_html'] = current['body_html'].replace('One integer x is given.', 'Two integers are given.')
    elif change == 'formula':
        current['body_html'] = current['body_html'].replace('$$x + 1$$', '$$x + 2$$')
    elif change == 'sample':
        current['samples'] = [{'input': '7', 'output': '8'}]
    elif change == 'image_html':
        current['body_html'] = current['body_html'].replace('synthetic-diagram.png', 'changed-diagram.png')
    elif change == 'image_list':
        current['images'] = ['https://example.test/changed-diagram.png']
    else:
        current['body_html'] = current['body_html'].replace('>Input</h2>', '>Different input</h2>')
    assert fingerprint(legacy) != fingerprint(current)


@pytest.mark.parametrize('field,value', [
    ('body_html', '<p>Different official problem statement</p>'),
    ('samples', [{'input': '8', 'output': '9'}]),
    ('time_limit', '3 seconds'),
    ('memory_limit', '512 megabytes'),
])
def test_legacy_shared_translation_is_not_reused_for_changed_official_content(workspace, field, value):
    app, client, statement = workspace
    sid, _, _ = _seed_legacy_shared_translation(workspace)
    changed = {**statement, field: value}
    app.state.workbench.store_statement('codeforces:4-A', changed)
    listing = client.get('/api/workbench/problem/4-A/translations')
    assert listing.status_code == 200, listing.text
    assert sid not in {item['id'] for item in listing.json()['items']}
    assert app.state.translation_store.approved(statement['id'], fingerprint(changed), changed) == []


@pytest.mark.parametrize('status', ['pending', 'rejected', 'revoked'])
def test_legacy_unapproved_shared_translation_is_not_public(workspace, status):
    app, client, statement = workspace
    sid, _, _ = _seed_legacy_shared_translation(workspace, status)
    listing = client.get('/api/workbench/problem/4-A/translations')
    assert listing.status_code == 200, listing.text
    assert sid not in {item['id'] for item in listing.json()['items']}
    assert app.state.translation_store.approved(statement['id'], fingerprint(statement), statement) == []


@pytest.mark.parametrize('status', ['pending', 'approved', 'revoked'])
def test_same_model_cannot_upload_duplicate_of_legacy_shared_version(workspace, status):
    app, client, statement = workspace
    _, legacy, _ = _seed_legacy_shared_translation(workspace, status)
    current = {**legacy, 'id': 'current-private-fixture', 'fingerprint': fingerprint(statement)}
    app.state.translations._remember(current)
    uploaded = client.post(f"/api/translations/{current['id']}/upload", json={})
    assert uploaded.status_code == 409, uploaded.text
    with app.state.translation_store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM translations').fetchone()[0] == 1


def test_ai_translation_batches_long_statements_and_saves_only_complete_result(workspace, monkeypatch):
    app, _, statement = workspace
    sections = [f'Paragraph {i}. ' + ('A natural-language sentence. ' * 90) for i in range(7)]
    statement = {**statement, 'body_html': '', 'sections': sections}
    app.state.workbench.store_statement('codeforces:4-A', statement)
    calls = []

    async def complete(task, provider, model, payload, schema):
        blocks = payload['blocks']
        assert task == 'translation'
        assert sum(len(block['text']) for block in blocks) <= 4000
        assert len(blocks) <= 48
        calls.append(blocks)
        return AIResult(
            data={'blocks': [{'id': block['id'], 'text': '译文 ' + block['text']} for block in blocks]},
            provenance={'channel': 'api', 'endpoint': 'https://api.example.test/v1',
                        'vendor': 'custom', 'protocol': 'chat', 'model': model},
        )

    monkeypatch.setattr(app.state.ai, 'complete', complete)
    result = asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture-model', consent=True)))
    assert len(calls) > 1
    translated = app.state.translations.get(result['translation_id'])
    assert translated['statement']['sections'] == ['译文 ' + section for section in sections]

    app.state.translations._expire(result['translation_id'])
    calls.clear()

    async def fail_second_batch(task, provider, model, payload, schema):
        calls.append(payload['blocks'])
        if len(calls) == 2:
            raise AIError('AI 请求失败，HTTP 429：额度不足或触发限流')
        return AIResult(
            data={'blocks': [{'id': block['id'], 'text': '译文 ' + block['text']} for block in payload['blocks']]},
            provenance={'channel': 'api', 'endpoint': 'https://api.example.test/v1',
                        'vendor': 'custom', 'protocol': 'chat', 'model': model},
        )

    monkeypatch.setattr(app.state.ai, 'complete', fail_second_batch)
    with pytest.raises(AIError, match='第 2/.*批失败.*HTTP 429'):
        asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture-model', consent=True)))
    assert app.state.translations.ephemeral == {}


def test_ai_translation_splits_oversized_single_text_block_and_reassembles(workspace, monkeypatch):
    app, _, statement = workspace
    original = 'x' * 3980 + '$x + 1$' + 'y' * 4001
    statement = {**statement, 'body_html': '', 'sections': [original]}
    app.state.workbench.store_statement('codeforces:4-A', statement)
    calls = []

    async def complete(task, provider, model, payload, schema):
        blocks = payload['blocks']
        calls.extend(blocks)
        assert all(len(block['text']) <= 4000 for block in blocks)
        return AIResult(
            data={'blocks': blocks},
            provenance={'channel': 'api', 'endpoint': 'https://api.example.test/v1',
                        'vendor': 'custom', 'protocol': 'chat', 'model': model},
        )

    monkeypatch.setattr(app.state.ai, 'complete', complete)
    result = asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture-model', consent=True)))
    assert len(calls) > 1
    sent = ''.join(block['text'] for block in calls)
    assert 'ACMPROTECT_' not in sent and '$x + 1$' not in sent
    translated = app.state.translations.get(result['translation_id'])['statement']['sections'][0]
    assert translated == original


def test_ai_translation_respects_small_output_budget(workspace, monkeypatch):
    app, _, statement = workspace
    app.state.ai.save(ProviderConfig(max_output_tokens=256))
    original = 'Read the problem carefully. ' * 30
    app.state.workbench.store_statement('codeforces:4-A', {**statement, 'body_html': '', 'sections': [original]})
    calls = []

    async def complete(task, provider, model, payload, schema):
        calls.append(payload['blocks'])
        assert len(payload['blocks']) <= 2
        assert sum(len(block['text']) for block in payload['blocks']) <= 128
        return AIResult(data={'blocks': payload['blocks']}, provenance={'channel': 'api', 'model': model})

    monkeypatch.setattr(app.state.ai, 'complete', complete)
    result = asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture-model', consent=True)))
    assert len(calls) > 1
    assert app.state.translations.get(result['translation_id'])['statement']['sections'] == [original]


def test_markdown_code_blocks_remain_original_and_tokens_are_not_split():
    statement = {'title': 'Statement', 'sections': [
        'Read this example:\n```cpp\n// Never translate this comment\nint main() {}\n```\nThen solve $x + 1$.']}
    doc = Document(statement)
    assert not any('Never translate' in block['text'] for block in doc.blocks)
    translated = doc.apply({'blocks': [{'id': block['id'], 'text': '译文 ' + block['text']} for block in doc.blocks]})
    assert '```cpp\n// Never translate this comment\nint main() {}\n```' in translated['sections'][0]
    token = 'ACMPROTECT_0123456789abcdef_0_END'
    fragments = fragment_blocks([{'id': '0', 'text': 'x' * 3980 + token + 'y' * 100}])
    assert token in fragments[1]['text']
    assert ''.join(fragment['text'] for fragment in fragments) == 'x' * 3980 + token + 'y' * 100


def test_prose_relation_symbols_and_bare_tex_commands_are_protected_from_translation():
    source = 'Read a ≠ b and c =\u0338 d; use `x != y`; preserve \\neq and \\not=.'
    doc = Document({'title': 'Relations', 'sections': [source]})
    masked = next(block['text'] for block in doc.blocks if 'Read' in block['text'])
    assert not any(value in masked for value in ('≠', '\u0338', '!=', r'\neq', r'\not='))
    translated = doc.apply({'blocks': [
        {**block, 'text': block['text'].replace('Read', '读取')} for block in doc.blocks]})
    assert translated['sections'] == [source.replace('Read', '读取')]


def test_bad_prose_batch_stops_without_paid_retry_and_does_not_persist(workspace, monkeypatch):
    app, _, statement = workspace
    app.state.workbench.store_statement('codeforces:4-A', {**statement, 'body_html': '',
        'sections': [('Explain $x$ carefully. ' * 60)] * 3})
    calls = []

    async def complete(task, provider, model, payload, schema):
        calls.append(payload)
        return AIResult(data={'blocks': [{'id': block['id'], 'text':
            'ACMPROTECT_broken'}
            for block in payload['blocks']]},
                        provenance={'channel': 'api', 'model': model})

    monkeypatch.setattr(app.state.ai, 'complete', complete)
    with pytest.raises(AIError, match='第 1/.*占位符'):
        asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture-model', consent=True)))
    assert len(calls) == 1
    assert app.state.translations.ephemeral == {}


@pytest.mark.parametrize('damage', ['delete', 'escape', 'reorder', 'duplicate'])
def test_gym_translation_never_sends_markers_to_models_that_would_damage_them(workspace, monkeypatch, damage):
    app = workspace[0]
    statement = parse_statement('''<div class="problem-statement"><div class="header"><div class="title">A. Synthetic Gym</div></div>
        <p>Compute $$$a \\neq b$$$ and $$$c \\leq d$$$ carefully.</p>
        <p>Use <code>x != y</code> and <a href="https://example.test/gym">the diagram</a>.</p>
        <pre>int x = 1; // Never translate</pre>
        <div class="sample-tests"><div class="sample-test"><div class="input"><pre>1  2\n3</pre></div><div class="output"><pre>4</pre></div></div></div></div>''', 'codeforces:100001-A')
    statement['source'] = 'official'
    app.state.workbench.store_statement(statement['id'], statement)
    calls = []
    async def complete(task, provider, model, payload, schema):
        calls.append(copy.deepcopy(payload))
        output = []
        for block in payload['blocks']:
            text = block['text']
            markers = TOKEN.findall(text)
            if markers:
                if damage == 'delete': text = TOKEN.sub('', text)
                elif damage == 'escape': text = text.replace('_', r'\_')
                elif damage == 'duplicate': text += markers[0]
                elif damage == 'reorder':
                    text = text.replace(markers[0], 'SWAP').replace(markers[-1], markers[0]).replace('SWAP', markers[-1])
            text = text.replace('Compute', '计算').replace('carefully.', '小心处理。')
            output.append({'id': block['id'], 'text': text})
        return AIResult(data={'blocks': output}, provenance={'channel': 'api', 'model': model})
    monkeypatch.setattr(app.state.ai, 'complete', complete)
    result = asyncio.run(app.state.translations.generate(statement['id'], Translate(model='fixture', consent=True)))
    translated = app.state.translations.get(result['translation_id'])['statement']
    assert len(calls) == 1  # Initial translation only, no automatic paid retry.
    assert all('ACMPROTECT_' not in block['text'] and '$$$' not in block['text']
               for call in calls for block in call['blocks'])
    assert '$$$a \\neq b$$$' in translated['body_html']
    assert '$$$c \\leq d$$$' in translated['body_html']
    assert translated['body_html'].index('a \\neq b') < translated['body_html'].index('c \\leq d')
    assert '计算' in translated['body_html']
    assert '<code>x != y</code>' in translated['body_html']
    assert 'int x = 1; // Never translate' in translated['body_html']
    assert translated['samples'] == statement['samples']
    assert translated['images'] == statement['images']


@pytest.mark.parametrize('failure', ['new_formula', 'new_code', 'new_link', 'new_delimiter', 'config_change', 'network', 'wrong_id', 'empty'])
def test_server_assembled_protected_translation_fails_closed(workspace, monkeypatch, failure):
    app = workspace[0]
    app.state.workbench.store_statement('codeforces:4-A', {**workspace[2], 'body_html': '',
        'sections': ['Compute $x + 1$ carefully.']})
    if failure == 'config_change':
        monkeypatch.setattr('acmweb.translations.AI_BATCH_BLOCK_LIMIT', 1)
    calls = []
    async def complete(task, provider, model, payload, schema):
        calls.append(payload)
        if failure == 'network':
            raise AIError('AI 网络请求失败')
        values = [{'id': block['id'], 'text': block['text']} for block in payload['blocks']]
        if failure == 'new_formula': values[0]['text'] += ' $x + 2$'
        elif failure == 'new_code': values[0]['text'] += ' `x = 2`'
        elif failure == 'new_link': values[0]['text'] += ' https://example.test/invented'
        elif failure == 'new_delimiter': values[0]['text'] += ' $'
        elif failure == 'wrong_id': values[0]['id'] = 'unknown'
        elif failure == 'empty': values[0]['text'] = ''
        return AIResult(data={'blocks': values}, provenance={'channel': 'api',
            'model': 'changed' if len(calls) == 2 and failure == 'config_change' else model})
    monkeypatch.setattr(app.state.ai, 'complete', complete)
    with pytest.raises(AIError):
        asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture', consent=True)))
    assert len(calls) == (2 if failure == 'config_change' else 1)
    assert app.state.translations.ephemeral == {}


def test_protected_prose_batches_keep_existing_preflight_call_limit(workspace, monkeypatch):
    app = workspace[0]
    monkeypatch.setattr('acmweb.translations.AI_BATCH_BLOCK_LIMIT', 1)
    app.state.workbench.store_statement('codeforces:4-A', {**workspace[2], 'title': '', 'body_html': '',
        'sections': ['Compute $x$ carefully. ' * 70]})
    async def should_not_call(*args, **kwargs):
        raise AssertionError('Batch limit must be checked before any paid request')
    monkeypatch.setattr(app.state.ai, 'complete', should_not_call)
    with pytest.raises(AIError, match='超过 64 批翻译'):
        asyncio.run(app.state.translations.generate('4-A', Translate(model='fixture', consent=True)))
    assert app.state.translations.ephemeral == {}


def test_numeric_and_symbol_only_formula_nodes_are_not_sent_for_translation():
    doc = Document({'title': 'Numbers', 'body_html': '<p>  123 ; $$$a + b$$$ (42). </p>'})
    assert doc.blocks == [{'id': '0', 'text': 'Numbers'}]
    result = doc.apply({'blocks': [{'id': '0', 'text': '数字'}]})
    assert result['body_html'] == '<p>  123 ; $$$a + b$$$ (42). </p>'


def test_protected_prose_reassembly_preserves_spaces_and_newlines(workspace, monkeypatch):
    service = workspace[0].state.translations
    original = '  Read\n  $x$ \t and\n $y$.  '
    doc = Document({'title': 'Spaced', 'sections': [original]})
    calls = []
    async def complete(task, provider, model, payload, schema):
        calls.append(payload)
        return AIResult(data={'blocks': [{**block, 'text': block['text'].replace('Read', '读取').replace('and', '以及')}
            for block in payload['blocks']]}, provenance={'channel': 'api', 'model': model})
    monkeypatch.setattr(service.ai, 'complete', complete)
    data, _ = asyncio.run(service.translate_ai(Translate(model='fixture', consent=True), doc.blocks))
    assert len(calls) == 1
    assert doc.apply(data)['sections'] == [original.replace('Read', '读取').replace('and', '以及')]


def test_legacy_rendered_math_and_non_pre_samples_never_become_translation_blocks():
    html = ('<p>Read <span class="katex"><span class="katex-html">x = y</span></span>.</p>'
            '<div class="sample-tests"><div class="sample-test">'
            '<div class="input">SAMPLE_INPUT Alpha</div><div class="output">SAMPLE_OUTPUT Beta</div>'
            '</div></div><p class="input">Ordinary input description.</p>'
            '<p class="output">Ordinary output description.</p>')
    doc = Document({'title': 'Legacy', 'body_html': html})
    sent = '\n'.join(block['text'] for block in doc.blocks)
    assert 'x = y' not in sent
    assert 'SAMPLE_INPUT' not in sent and 'SAMPLE_OUTPUT' not in sent
    assert 'Ordinary input description.' in sent and 'Ordinary output description.' in sent
    result = doc.apply({'blocks': [{**block, 'text': block['text'].replace('Read', '读取').replace('Ordinary', '普通')}
        for block in doc.blocks]})
    assert '<span class="katex-html">x = y</span>' in result['body_html']
    assert '<div class="input">SAMPLE_INPUT Alpha</div>' in result['body_html']
    assert '<div class="output">SAMPLE_OUTPUT Beta</div>' in result['body_html']
    assert '普通 input description.' in result['body_html']


def test_dense_gym_formula_prose_uses_bounded_initial_batches(workspace, monkeypatch):
    service = workspace[0].state.translations
    original = 'Compute $x_i$ carefully.\n' * 75
    doc = Document({'title': '', 'sections': [original]})
    calls = []
    async def complete(task, provider, model, payload, schema):
        calls.append(payload)
        assert len(payload['blocks']) <= 48
        assert all('ACMPROTECT_' not in block['text'] and '$' not in block['text'] for block in payload['blocks'])
        return AIResult(data={'blocks': [{**block, 'text': block['text'].replace('Compute', '计算')}
            for block in payload['blocks']]}, provenance={'channel': 'api', 'model': model})
    monkeypatch.setattr(service.ai, 'complete', complete)
    data, _ = asyncio.run(service.translate_ai(Translate(model='fixture', consent=True), doc.blocks))
    assert len(calls) == 2  # 76 prose strings grouped by the existing 48-item cap.
    assert doc.apply(data)['sections'] == [original.replace('Compute', '计算')]


def test_official_translation_requests_are_batched(monkeypatch, workspace):
    app, _, _ = workspace
    monkeypatch.setenv('ACM_GOOGLE_TRANSLATE_KEY', 'server-only-key')
    calls = []

    class Response:
        status_code = 200

        def __init__(self, count):
            self.count = count

        def json(self):
            return {'data': {'translations': [{'translatedText': f'译文-{i}'} for i in range(self.count)]}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            calls.append((url, headers, json))
            return Response(len(json['q']))

    monkeypatch.setattr('acmweb.translations.httpx.AsyncClient', lambda **kwargs: Client())
    blocks = [{'id': str(i), 'text': f'paragraph {i}'} for i in range(130)]
    result = asyncio.run(app.state.translations.ordinary('google', blocks))
    assert [len(call[2]['q']) for call in calls] == [128, 2]
    assert result['blocks'][0] == {'id': '0', 'text': '译文-0'}
    assert result['blocks'][-1] == {'id': '129', 'text': '译文-1'}
    assert all(call[1]['X-Goog-Api-Key'] == 'server-only-key' for call in calls)


def test_official_translation_splits_a_single_large_markdown_block(monkeypatch, workspace):
    app, _, _ = workspace
    monkeypatch.setenv('ACM_GOOGLE_TRANSLATE_KEY', 'server-only-key')
    calls = []

    class Response:
        status_code = 200

        def __init__(self, count): self.count = count
        def json(self):
            return {'data': {'translations': [{'translatedText': f'text-{i}'} for i in range(self.count)]}}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, *, headers, json):
            calls.append(json['q'])
            return Response(len(json['q']))

    monkeypatch.setattr('acmweb.translations.httpx.AsyncClient', lambda **kwargs: Client())
    blocks = [{'id': '0', 'text': 'x' * 8500}]
    result = asyncio.run(app.state.translations.ordinary('google', blocks))
    assert len(calls) == 1
    assert [len(fragment) for fragment in calls[0]] == [4000, 4000, 500]
    assert result['blocks'] == [{'id': '0', 'text': 'text-0text-1text-2'}]


def test_multiuser_translation_store_is_central_and_statement_stays_private(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, multi_user=True, enable_codex=False,
                        access_token='initialization-secret-long-enough', allowed_hosts=('testserver',),
                        ai_allowed_hosts=('api.example.test',))
    app = create_app(settings)
    html_statement = _cf_section_statement(parse_statement(HTML, 'codeforces:4-A'))
    html_statement['source'] = 'official'
    with TestClient(app, base_url='https://testserver') as client:
        def request(who, method, path, body=None):
            headers = {'X-ACM-Request': '1', 'Origin': 'https://testserver'}
            if who:
                headers.update({'Cookie': 'acm_user_session=' + who['cookie'], 'X-CSRF-Token': who['csrf_token']})
            return client.request(method, path, headers=headers, **({'json': body} if body is not None else {}))

        setup = client.post('/api/auth/setup', headers={'X-ACM-Request': '1'}, json={
            'username': 'kendas', 'password': 'Correct-password-123', 'setup_token': settings.access_token,
        })
        assert setup.status_code == 200, setup.text
        owner_cookie = setup.cookies['acm_user_session']
        owner = {'user': app.state.identities.resolve(owner_cookie), 'csrf_token': csrf_token(owner_cookie), 'cookie': owner_cookie}
        invite = request(owner, 'POST', '/api/admin/invitations', {}).json()['invitation']
        registered = client.post('/api/auth/register', headers={'X-ACM-Request': '1'}, json={
            'username': 'member', 'password': 'Correct-password-123', 'invitation': invite,
        })
        assert registered.status_code == 200
        member_cookie = registered.cookies['acm_user_session']
        member = {'user': app.state.identities.resolve(member_cookie), 'csrf_token': csrf_token(member_cookie), 'cookie': member_cookie}

        assert request(owner, 'GET', '/api/workbench/problem/4-A').status_code == 200
        owner_child = app.state.workspaces.apps[owner['user']['id']][0]
        owner_child.state.ai.save(ProviderConfig(base_url='https://api.example.test/v1',
                                                vendor='custom', protocol='chat'))
        owner_child.state.workbench.store_statement('codeforces:4-A', html_statement)

        async def complete(task, provider, model, payload, schema):
            return AIResult(
                data={'blocks': [{'id': block['id'], 'text': '译文 ' + block['text']} for block in payload['blocks']]},
                provenance={'channel': 'api', 'provider': 'api', 'endpoint': 'https://api.example.test/v1',
                            'vendor': 'custom', 'protocol': 'chat', 'model': model,
                            'task': task, 'prompt_version': 'test'},
            )

        monkeypatch.setattr(owner_child.state.ai, 'complete', complete)
        queued = request(owner, 'POST', '/api/workbench/problem/4-A/translate', {
            'engine': 'ai', 'provider': 'api', 'model': 'mock-model', 'consent': True,
        })
        assert queued.status_code == 200, queued.text
        job_id = queued.json()['id']
        for _ in range(50):
            job = next(item for item in request(owner, 'GET', '/api/jobs').json()['items'] if item['id'] == job_id)
            if job['status'] not in {'queued', 'running'}:
                break
            time.sleep(0.01)
        assert job['status'] == 'completed', job
        tid = job['result']['translation_id']
        assert tid in owner_child.state.translations.ephemeral
        with owner_child.state.database.connect() as db:
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_translations'").fetchone()
        uploaded = request(owner, 'POST', f'/api/translations/{tid}/upload', {})
        assert uploaded.status_code == 200, uploaded.text
        shared_id = uploaded.json()['id']
        assert request(member, 'GET', '/api/workbench/problem/4-A').status_code == 200
        member_child = app.state.workspaces.apps[member['user']['id']][0]
        member_child.state.workbench.store_statement('codeforces:4-A', html_statement)
        assert request(member, 'GET', '/api/workbench/problem/4-A/translations').json()['items'] == []
        assert request(member, 'POST', f'/api/translations/{tid}/upload', {}).status_code == 404
        assert request(member, 'POST', f'/api/translations/{shared_id}/review', {'decision': 'approved'}).status_code == 403
        assert request(owner, 'POST', f'/api/translations/{shared_id}/review', {'decision': 'approved'}).status_code == 200

        # Synthetic historical contributions cover both sanitizer generations:
        # old div headings and h2 headings whose render_version was still 3.
        legacy_source = _cf_section_statement(html_statement, tag='div', render_version=3)
        legacy_translation = owner_child.state.translations.get(tid)
        legacy_translation['fingerprint'] = _legacy_translation_fingerprint(legacy_source)
        legacy_translation['statement']['render_version'] = 3
        with app.state.translation_store.connect() as db:
            db.execute('UPDATE translations SET fingerprint=?,source_statement=?,data=?,submitted_at=100 WHERE id=?',
                       (legacy_translation['fingerprint'], json.dumps(legacy_source),
                        json.dumps(legacy_translation), shared_id))

        assert request(member, 'GET', '/api/workbench/problem/4-A').status_code == 200
        member_child = app.state.workspaces.apps[member['user']['id']][0]
        current_source = _cf_section_statement(html_statement, tag='h2', render_version=RENDER_VERSION)
        member_child.state.workbench.store_statement('codeforces:4-A', current_source)
        old_div_visible = request(member, 'GET', '/api/workbench/problem/4-A/translations')
        assert old_div_visible.status_code == 200, old_div_visible.text
        assert [item['id'] for item in old_div_visible.json()['items']] == [shared_id]

        newer_source = _cf_section_statement(html_statement, tag='h2', render_version=3)
        newer_translation = copy.deepcopy(legacy_translation)
        newer_translation.update(id='newer-private-synthetic', fingerprint=_legacy_translation_fingerprint(newer_source))
        translated_body = newer_translation['statement']['body_html']
        translated_body = translated_body.replace('<div class="section-title">', '<h2 class="section-title">').replace('</div>', '</h2>')
        newer_translation['statement'].update(body_html=translated_body, render_version=3,
                                               markdown=html_markdown(translated_body),
                                               sections=[html_markdown(translated_body)])
        newer_shared_id = 'newer-approved-synthetic'
        assert newer_translation['fingerprint'] != legacy_translation['fingerprint']
        assert fingerprint(newer_source) == fingerprint(legacy_source) == fingerprint(current_source)
        with app.state.translation_store.connect() as db:
            db.execute('''INSERT INTO translations
                SELECT ?,owner,?,problem_id,?,target,model_key,status,?,?,200,reviewed_at,reviewer,review_note
                FROM translations WHERE id=?''',
                (newer_shared_id, newer_translation['id'], newer_translation['fingerprint'],
                 json.dumps(newer_translation), json.dumps(newer_source), shared_id))

        visible = request(member, 'GET', '/api/workbench/problem/4-A/translations')
        assert visible.status_code == 200, visible.text
        assert visible.json()['can_review'] is False
        assert [item['id'] for item in visible.json()['items']] == [newer_shared_id]
        shared = visible.json()['items'][0]
        assert shared['model_key'] == legacy_translation['model_key']
        assert shared['scope'] == 'shared'
        assert shared['statement']['render_version'] == RENDER_VERSION
        assert shared['statement']['markdown'] == html_markdown(shared['statement']['body_html'])
        assert shared['statement']['sections'] == [shared['statement']['markdown']]
        assert '译文 ' in shared['statement']['markdown']
        assert member_child.state.translations.shared.path == tmp_path / 'translations.sqlite3'

        member_child.state.ai.save(ProviderConfig(base_url='https://api.example.test/v1',
                                                 vendor='custom', protocol='chat'))

        async def should_not_translate(*args, **kwargs):
            raise AssertionError('another user must reuse the approved historical translation for free')

        monkeypatch.setattr(member_child.state.ai, 'complete', should_not_translate)
        reused = asyncio.run(member_child.state.translations.generate('4-A', Translate(
            model='mock-model', consent=True)))
        assert reused == {'translation_id': newer_shared_id, 'reused': True}
        assert member_child.state.translations.ephemeral == {}
        # Reading a legacy shared translation upgrades its presentation without
        # mutating the archived contribution or persisting a private copy.
        with app.state.translation_store.connect() as db:
            stored = db.execute('SELECT data FROM translations WHERE id=?', (shared_id,)).fetchone()
            newer_stored = db.execute('SELECT data FROM translations WHERE id=?', (newer_shared_id,)).fetchone()
            assert db.execute("SELECT COUNT(*) FROM translations WHERE status='approved'").fetchone()[0] == 2
        assert json.loads(stored['data'])['statement']['render_version'] == 3
        assert json.loads(newer_stored['data'])['statement']['render_version'] == 3
