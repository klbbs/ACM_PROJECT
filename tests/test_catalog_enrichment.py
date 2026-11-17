import json
from copy import deepcopy

from acmweb.catalog import _merge_catalog_metadata
from acmweb.catalog_enrichment import AT_TAG_SOURCE, atcoder_tag_batch, cf_statistics_batch
from ojcrawler.models import CrawlError


class Pages:
    def __init__(self, data): self.data, self.calls = data, []
    def get(self, url, params):
        self.calls.append((url, params))
        item = self.data[params['page']]
        if isinstance(item, Exception): raise item
        return json.dumps({'problems': item})


def problem(key='abc001_1'):
    return {'id': 'atcoder:' + key, 'problem_id': key, 'platform': 'atcoder', 'tags': []}


def test_atcoder_exact_mirror_identity_and_published_taxonomy_only():
    row = problem()
    client = Pages({1: {'count': 50, 'perPage': 50, 'result': [
        {'pid': 'AT_abc001_1', 'tags': [3, True, 999999]},
        {'pid': 'AT123', 'tags': [5]},
        {'pid': 'ATabc001_2', 'tags': [6]}]}})
    result = atcoder_tag_batch(client, [row])
    assert result['tagged_count'] == 1 and result['next_page'] == 1
    assert row['tags'] == ['动态规划 DP'] and row['luogu_tag_ids'] == [3]
    assert row['tags_source'] == AT_TAG_SOURCE
    assert row['tags_mirror_id'] == 'AT_abc001_1'
    assert client.calls[0][1] == {'type': 'AT', 'page': 1}


def test_atcoder_failure_keeps_prior_tags_and_failed_page_cursor():
    row = {**problem(), 'tags': ['已有标签'], 'tags_source': AT_TAG_SOURCE}
    client = Pages({9: CrawlError('sensitive upstream text')})
    before = deepcopy(row)
    result = atcoder_tag_batch(client, [row], first_page=9)
    assert row == before and result['next_page'] == 9 and result['warning']
    assert 'sensitive' not in result['warning']
    merged = _merge_catalog_metadata('atcoder', row, problem())
    assert merged['tags'] == ['已有标签'] and merged['tags_source'] == AT_TAG_SOURCE


def test_atcoder_repeated_pages_stop_without_advancing_past_failure():
    entry = {'count': 500, 'perPage': 1, 'result': [{'pid': 'ATabc001_1', 'tags': [3]}]}
    client = Pages({1: entry, 2: entry})
    result = atcoder_tag_batch(client, [problem()])
    assert result['pages'] == 1 and result['next_page'] == 2 and result['warning']


def test_cf_batch_retains_stats_and_advances_bounded_contest_cursor():
    from acmhermes import contest_evidence
    calls = []
    class Provider:
        def __init__(self, **kwargs): pass
        def standings(self, contest):
            calls.append(contest)
            return {'status': 'available', 'statistics': {'A': {
                'attempted_participants': 4, 'accepted_participants': 2,
                'acceptance_rate': .5, 'acceptance_rate_scope': 'contest_attempting_participants'}}}
    import unittest.mock
    with unittest.mock.patch.object(contest_evidence, 'ContestEvidenceProvider', Provider):
        rows = [{'id':f'codeforces:{cid}-A', 'contest_id':cid, 'problem_id':f'{cid}-A', 'difficulty':1800}
                for cid in (10, 11, 12)]
        result = cf_statistics_batch(None, rows, first_contest=11, max_contests=1)
    assert calls == [11] and result['next_contest'] == 12
    assert rows[1]['contest_statistics']['acceptance_rate'] == .5
    assert 'contest_statistics' not in rows[0] and rows[1]['difficulty'] == 1800


def test_mirrored_atcoder_topic_requires_exact_id_and_attributed_tags():
    from acmweb.training_topics import topic_evidence, TOPICS
    row = {**problem(), 'tags_source': AT_TAG_SOURCE, 'tags_mirror_id': 'ATabc001_1',
           'luogu_tag_ids': [3]}
    assert topic_evidence(row, TOPICS['luogu-3'])['source'] == 'luogu_mirrored_atcoder_tag'
    row['tags_mirror_id'] = 'ATabc001_2'
    assert not topic_evidence(row, TOPICS['luogu-3'])['matched']


def test_cf_access_failure_stops_sends_and_keeps_failed_contest_cursor(monkeypatch):
    from acmhermes import contest_evidence
    calls = []
    class Provider:
        def __init__(self, **kwargs): pass
        def standings(self, cid):
            calls.append(cid)
            return {'status': 'unavailable', 'reason': 'rate_limited'}
    monkeypatch.setattr(contest_evidence, 'ContestEvidenceProvider', Provider)
    rows = [{'contest_id': n, 'problem_id': f'{n}-A'} for n in (10, 11)]
    result = cf_statistics_batch(None, rows, first_contest=10)
    assert calls == [10] and result['next_contest'] == 10 and result['warning']


def test_cf_unsupported_contest_does_not_pin_continuation(monkeypatch):
    from acmhermes import contest_evidence
    class Provider:
        def __init__(self, **kwargs): pass
        def standings(self, cid): return {'status':'unavailable','reason':'invalid_standings'}
    monkeypatch.setattr(contest_evidence, 'ContestEvidenceProvider', Provider)
    result = cf_statistics_batch(None, [{'contest_id':10}], first_contest=10)
    assert result['next_contest'] == 1 and result['contests_checked'] == 1


def test_cf_mirror_requires_exact_identity_and_keeps_markdown(monkeypatch):
    from acmweb import problem_sources
    import pytest
    class Client:
        def __init__(self, **kw): assert not kw.get('cookie')
        def get(self, url): return json.dumps({'problem': {
            'pid':'CF4A','name':'Watermelon','content':{'description':'A $w$ task.'},'samples':[['8','YES']]}})
        def close(self): pass
    monkeypatch.setattr(problem_sources,'Client',Client)
    result=problem_sources.fetch_codeforces_public_mirror('codeforces:4-A')
    assert result['id']=='codeforces:4-A' and result['source']=='luogu_mirror'
    assert result['sections'] and result['samples'][0]['output']=='YES'
    assert result['mirror_url'].endswith('/CF4A') and result['diagnostics']['warnings']
    with pytest.raises(problem_sources.AIError):
        problem_sources.fetch_codeforces_public_mirror('codeforces:5-A')
    with pytest.raises(problem_sources.AIError):
        problem_sources.fetch_codeforces_public_mirror('codeforces:100001-A')


def test_workbench_official_failure_uses_attributed_mirror(monkeypatch, tmp_path):
    from acmweb.database import Database
    from acmweb.workbench import Workbench
    from acmweb import workbench, problem_sources
    class Accounts:
        def document(self):return {'accounts': []}
    class Blocked:
        def __init__(self, **kwargs): pass
        def get(self, *args, **kwargs):raise CrawlError('blocked official source')
        def close(self):pass
    monkeypatch.setattr(workbench, 'Client', Blocked)
    monkeypatch.setattr(problem_sources, 'fetch_codeforces_public_mirror', lambda pid: {
        'id':pid,'source':'luogu_mirror','sections':['body'],'samples':[]})
    db=Database(tmp_path/'catalog.sqlite3')
    service=Workbench(db,None,Accounts())
    assert service.statement('4-A') == {'problem_id':'codeforces:4-A'}
    with db.connect() as conn:
        stored=json.loads(conn.execute('select data from wb_problems').fetchone()[0])
    assert stored['id']=='codeforces:4-A' and stored['source']=='luogu_mirror'
