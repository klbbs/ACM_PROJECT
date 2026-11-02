"""Synthetic evidence contracts, not live OJ or onsite-board acceptance."""
import asyncio
from copy import deepcopy
import json
import threading
import time

import pytest

from acmhermes.contest_evidence import ContestEvidenceProvider, EvidenceUnavailable, _problem_rows
from acmweb.database import Database


def standings(contest_id=20):
    return {'status': 'OK', 'result': {'contest': {'id': contest_id, 'name': 'Contest', 'phase': 'FINISHED',
            'type': 'CF', 'startTimeSeconds': 1600000000},
        'problems': [{'contestId': contest_id, 'index': 'A', 'name': 'Problem', 'rating': 1800, 'tags': ['math']}],
        'rows': [row('one', 100, 1), row('two', 0, 3), row('three', 0, 0), row('practice', 100, 0, 'PRACTICE')]}}


def row(handle, points, failed, kind='CONTESTANT'):
    return {'party': {'members': [{'handle': handle}], 'participantType': kind},
            'problemResults': [{'points': points, 'rejectedAttemptCount': failed, 'type': 'FINAL'}]}


@pytest.fixture
def database(tmp_path):
    return Database(tmp_path / 'db.sqlite3')


def test_regular_api_exact_parameter_aggregates_without_private_rows(database):
    calls = []
    def request(url, params):
        calls.append((url, params))
        return standings()
    p = ContestEvidenceProvider(database, api_key='secretkey', api_secret='secret', request=request)
    result = p.standings(20)
    assert calls == [('https://codeforces.com/api/contest.standings', {'contestId': '20'})]
    stats = result['statistics']['A']
    assert stats['participants'] == 3 and stats['accepted_participants'] == 1
    assert stats['attempted_participants'] == 2 and stats['submissions'] == 5
    assert stats['scope'] == 'contest' and stats['contest_id'] == 20 and stats['problem_index'] == 'A'
    assert stats['participant_unit'] == 'person'
    assert result['problems'][0]['official_rating'] == 1800
    cache = json.dumps(database.get('hermes_contest_evidence:v1:contest:20'))
    assert all(x not in cache for x in ('secretkey', 'practice', '"handle"', '"members"'))
    assert p.standings(20) == result and len(calls) == 1


def test_duplicate_party_and_unofficial_not_double_counted():
    payload = standings()['result']
    payload['rows'].extend([deepcopy(payload['rows'][0]), row('virtual', 100, 0, 'VIRTUAL')])
    assert _problem_rows(payload, 20, False, int(time.time()))['statistics']['A']['participants'] == 3


def test_gym_signed_only_when_api_available():
    calls = []
    def request(url, params):
        calls.append((url, dict(params)))
        data = standings(100001)
        data['result']['contest']['type'] = 'ICPC'
        data['result']['rows'][0]['party']['members'].append({'handle': 'teammate'})
        return data
    no_key = ContestEvidenceProvider(request=request).standings(100001, gym=True)
    assert no_key['reason'] == 'gym_api_required' and calls == []
    result = ContestEvidenceProvider(api_key='KEY', api_secret='SECRET', request=request).standings(100001, gym=True)
    params = calls[0][1]
    assert params['apiKey'] == 'KEY' and len(params['apiSig']) == 134
    assert 'SECRET' not in json.dumps(params)
    assert result['statistics']['A']['participant_unit'] == 'team'
    assert result['problems'][0]['is_gym'] is True


@pytest.mark.parametrize('change', [
    lambda p: p['contest'].update(phase='CODING'),
    lambda p: p['contest'].update(type='IOI'),
    lambda p: p['contest'].update(id=21),
    lambda p: p.update(truncated=True),
    lambda p: p['rows'][0]['problemResults'][0].update(points=float('nan')),
    lambda p: p['rows'][0]['problemResults'][0].update(points=float('inf')),
    lambda p: p['rows'][0]['problemResults'][0].update(type='PRELIMINARY'),
    lambda p: p['rows'][0]['problemResults'][0].update(rejectedAttemptCount=-1),
    lambda p: p['problems'][0].update(index='TOOLONG'),
    lambda p: p['rows'][0].update(problemResults=[]),
])
def test_invalid_or_not_final_evidence_rejected(change):
    payload = standings()['result']
    change(payload)
    with pytest.raises(EvidenceUnavailable):
        _problem_rows(payload, 20, False, int(time.time()))


def test_no_official_rows_not_a_zero_solve_rate():
    p = standings()['result']
    for r in p['rows']:
        r['party']['participantType'] = 'PRACTICE'
    result = _problem_rows(p, 20, True, int(time.time()))
    assert result['status'] == 'metadata_only' and result['statistics'] == {}
    assert 'contest_statistics' not in result['problems'][0]


def test_error_is_fixed_and_negative_cached(database):
    calls = []
    def request(url, params):
        calls.append(1)
        raise RuntimeError('SECRET_COOKIE model response')
    provider = ContestEvidenceProvider(database, request=request)
    result = provider.standings(20)
    assert result == {'status': 'unavailable', 'reason': 'network_or_format'}
    assert provider.standings(20) == result and len(calls) == 1


def test_cancel_during_request_does_not_publish(database):
    stop = threading.Event()
    def request(url, params):
        stop.set()
        return standings()
    with pytest.raises(asyncio.CancelledError):
        ContestEvidenceProvider(database, stop=stop, request=request).standings(20)
    assert database.get('hermes_contest_evidence:v1:contest:20') is None


def test_gym_random_sample_bounded_and_listing_cache(database, monkeypatch):
    calls = []
    def sample(self, items, count):
        return items[-count:]
    monkeypatch.setattr('random.SystemRandom.sample', sample)
    monkeypatch.setattr(ContestEvidenceProvider, 'onsite_for_contest', lambda *a, **k:
        {'status': 'unavailable', 'reason': 'onsite_mapping_unverified'})
    def request(url, params):
        calls.append((url, params))
        if url.endswith('contest.list'):
            return {'status': 'OK', 'result': [{'id': 100001 + i, 'phase': 'FINISHED',
                    'name': 'do not persist arbitrary listing fields', 'startTimeSeconds': 1600000000}
                   for i in range(5)]}
        return standings(int(params['contestId']))
    provider = ContestEvidenceProvider(database, api_key='K', api_secret='S', request=request)
    result = provider.gym_catalog(limit=100)
    assert result['contests_checked'] == 2 and len(result['rows']) == 2
    assert {r['contest_id'] for r in result['rows']} == {100004, 100005}
    assert len(calls) == 3
    assert provider.gym_catalog()['status'] == 'available' and len(calls) == 3


def xcpc_data():
    return {'contest_name': 'Exact Contest', 'start_time': 1600000000, 'end_time': 1600018000,
            'problems': [{'id': '0', 'label': 'A'}, {'id': '1', 'label': 'B'}]}, [
            {'team_id': 'one', 'name': 'PRIVATE TEAM', 'members': ['PRIVATE MEMBER'], 'group': ['official']},
            {'team_id': 'two', 'group': ['official']}, {'team_id': 'guest', 'group': ['unofficial']}], [
            {'id': '1', 'team_id': 'one', 'problem_id': '0', 'status': 'CORRECT'},
            {'id': '2', 'team_id': 'two', 'problem_id': '0', 'status': 'REJECTED'},
            {'id': '3', 'team_id': 'guest', 'problem_id': '0', 'status': 'CORRECT'}]


def test_xcpc_public_static_snapshot_only_official_teams_and_safe_cache(database):
    config, teams, runs = xcpc_data()
    calls = []
    def request(url, params):
        calls.append(url)
        return {'config.json': config, 'team.json': teams, 'run.json': runs}[url.rsplit('/', 1)[-1]]
    p = ContestEvidenceProvider(database, request=request)
    result = p.onsite_snapshot('xcpcio', '/icpc/test/2020')
    assert result['status'] == 'available' and len(calls) == 3
    a = result['statistics']['A']
    assert a['participants'] == 2 and a['accepted_participants'] == 1 and a['submissions'] == 2
    assert 'PRIVATE' not in json.dumps(database.get('hermes_contest_evidence:v1:onsite:xcpcio:/icpc/test/2020'))
    assert p.onsite_snapshot('xcpcio', '/icpc/test/2020') == result and len(calls) == 3


def test_onsite_exact_event_mapping_and_full_label_set_required(database):
    config, teams, runs = xcpc_data()
    def request(url, params):
        if url.endswith('contest_list.json'):
            return {'icpc': {'config': config, 'board_link': '/icpc/test/2020'}}
        return {'config.json': config, 'team.json': teams, 'run.json': runs}[url.rsplit('/', 1)[-1]]
    p = ContestEvidenceProvider(database, request=request)
    assert p.onsite_for_contest('Different Contest', 1600000000, ['A', 'B'])['reason'] == 'onsite_mapping_unverified'
    assert p.onsite_for_contest('Exact Contest', 1700000000, ['A', 'B'])['reason'] == 'onsite_mapping_unverified'
    assert p.onsite_for_contest('Exact Contest', 1600000000, ['A'])['reason'] == 'onsite_mapping_unverified'
    assert p.onsite_for_contest('Exact Contest', 1600000000, ['A', 'B'])['status'] == 'available'


@pytest.mark.parametrize('ref', ['https://127.0.0.1/', '/icpc/../../secret', '//other.test/x', '/icpc/foo?url=x'])
def test_onsite_invalid_reference_never_requests(ref):
    calls = []
    p = ContestEvidenceProvider(request=lambda *args: calls.append(args))
    assert p.onsite_snapshot('xcpcio', ref)['reason'] == 'invalid_source'
    assert calls == []


def test_xcpc_pending_is_not_final_evidence():
    from acmhermes.contest_evidence import _xcpc_statistics
    config, teams, runs = xcpc_data()
    runs.append({'status': 'PENDING'})
    with pytest.raises(EvidenceUnavailable, match='onsite_not_final'):
        _xcpc_statistics(config, teams, runs, '/icpc/test')


def test_iccpc_uses_timezone_and_distinct_official_teams():
    from acmhermes.contest_evidence import _iccpc_statistics
    payload = {'contest': {'title': 'CCPC Test', 'start_time': '2020-01-01 09:00:00',
                          'end_time': '2020-01-01 14:00:00'},
        'team': [{'team_id': 'A', 'tkind': 0}, {'team_id': 'B', 'tkind': 1}],
        'problem': [{'problem_id': 1, 'num': 0}],
        'solution': [{'solution_id': 10, 'team_id': 'A', 'problem_id': 1, 'result': 4},
                     {'solution_id': 10, 'team_id': 'A', 'problem_id': 1, 'result': 4},
                     {'solution_id': 11, 'team_id': 'B', 'problem_id': 1, 'result': 4}],
        'time_context': {'wall_clock_timezone': 'Asia/Shanghai', 'encoding': 'naive_app_wall'}}
    result = _iccpc_statistics(payload, 'id')
    assert result['statistics']['A']['participants'] == 1
    assert result['statistics']['A']['submissions'] == 1
    assert result['contest']['published_at'] == 1577840400
    payload['time_context'] = {}
    with pytest.raises(EvidenceUnavailable, match='unknown_onsite_timezone'):
        _iccpc_statistics(payload, 'id')


def test_gym_metadata_only_recovers_by_exact_onsite_mapping(database, monkeypatch):
    p = ContestEvidenceProvider(database, api_key='K', api_secret='S')
    monkeypatch.setattr(p, '_get', lambda url, params=None: {'status': 'OK', 'result': [
        {'id': 100001, 'phase': 'FINISHED', 'startTimeSeconds': 1600000000}]})
    payload = standings(100001)['result']
    for r in payload['rows']:
        r['party']['participantType'] = 'PRACTICE'
    metadata = _problem_rows(payload, 100001, True, int(time.time()))
    monkeypatch.setattr(p, 'standings', lambda *a, **k: metadata)
    calls = []
    def onsite(*args, **kwargs):
        calls.append(args)
        return {'status': 'available', 'statistics': {'A': {'participants': 100,
                'accepted_participants': 20, 'source': 'board.xcpcio.com', 'scope': 'contest'}}}
    monkeypatch.setattr(p, 'onsite_for_contest', onsite)
    result = p.gym_catalog()
    assert result['status'] == 'available' and result['onsite_events_checked'] == 1 and len(calls) == 1
    assert result['rows'][0]['contest_statistics']['contest_id'] == 100001
    assert result['rows'][0]['contest_statistics']['source'] == 'board.xcpcio.com'
