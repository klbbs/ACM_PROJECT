import json
import time
from datetime import datetime

from test_multiuser import site
from ojcrawler.models import Collection, Submission
from ojcrawler.storage import Store
from acmweb.jobs import Jobs

_REAL_CRAWL = Jobs.crawl


def child(site, who):
    request, _, _, app, *_ = site
    assert request(who, 'GET', '/api/social/me').status_code == 200
    return app.state.workspaces.apps[who['user']['id']][0]


def claim(site, who, handle):
    request = site[0]
    assert request(who, 'PUT', '/api/accounts', {'items': [{'platform': 'codeforces', 'handle': handle, 'cookie': 'session=verified-private-value'}]}).status_code == 200
    runtime = child(site, who)
    checks = runtime.state.credential_checks
    _, fingerprint = checks.credential('codeforces', handle)
    checks.cache[('codeforces', handle)] = (fingerprint, {'platform': 'codeforces', 'handle': handle, 'checked_at': int(time.time()), 'login_verified': True, 'status': 'authenticated'})
    result = request(who, 'POST', '/api/social/accounts', {'platform': 'codeforces', 'handle': handle})
    assert result.status_code == 200, result.text
    return runtime


def friends(site):
    request, admin, member, *_ = site
    uid, mid = admin['user']['id'], member['user']['id']
    assert request(admin, 'POST', '/api/social/friends', {'user_id': mid}).status_code == 200
    assert request(member, 'POST', f'/api/social/friends/{uid}', {'action': 'accept'}).status_code == 200


def official(handle, ts, verdict='OK', cid=1000, index='A', sid=123, members=None):
    return {'id': sid, 'creationTimeSeconds': ts, 'verdict': verdict,
        'problem': {'contestId': cid, 'index': index, 'name': 'Official test problem', 'rating': 1200, 'tags': ['dp']},
        'author': {'participantType': 'PRACTICE', 'members': members if members is not None else [{'handle': handle}]}}


def prepare_duel(site, monkeypatch):
    request, admin, member, *_ = site
    claim(site, admin, 'FirstOwner'); claim(site, member, 'SecondOwner'); friends(site)
    monkeypatch.setattr('acmweb.social.official_history', lambda handle, since=None: [])
    monkeypatch.setattr('acmweb.social.official_problemset', lambda: [{'id': 'codeforces:1000-A', 'title': 'Official test problem', 'difficulty': 1200, 'tags': ['dp'], 'url': 'https://codeforces.com/problemset/problem/1000/A'}])
    invitation = request(admin, 'POST', '/api/social/duels', {'user_id': member['user']['id'], 'difficulty': 1200, 'duration_minutes': 60})
    assert invitation.status_code == 200, invitation.text
    did = invitation.json()['id']
    accepted = request(member, 'POST', f'/api/social/duels/{did}/action', {'action': 'accept'})
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


def test_profiles_privacy_search_friend_authorization(site):
    request, admin, member, *_ = site
    uid, mid = admin['user']['id'], member['user']['id']
    assert request(None, 'GET', '/api/social/users?q=member').status_code == 401
    assert request(admin, 'PUT', '/api/social/me', {'display_name': '<script>name</script>', 'bio': 'private bio'}).status_code == 200
    assert request(member, 'GET', f'/api/social/users/{uid}').status_code == 403
    search = request(member, 'GET', '/api/social/users?q=kendas').json()['items'][0]
    assert search['display_name'] == '<script>name</script>' and 'bio' not in search
    assert request(admin, 'POST', '/api/social/friends', {'user_id': uid}).status_code == 422
    assert request(admin, 'POST', '/api/social/friends', {'user_id': mid}).status_code == 200
    assert request(admin, 'POST', f'/api/social/friends/{mid}', {'action': 'accept'}).status_code == 403
    assert request(admin, 'POST', f'/api/social/friends/{mid}', {'action': 'decline'}).status_code == 403
    assert request(member, 'POST', f'/api/social/friends/{uid}', {'action': 'accept'}).status_code == 200
    assert request(member, 'GET', f'/api/social/users/{uid}').json()['profile']['bio'] == 'private bio'
    assert request(member, 'POST', f'/api/social/friends/{uid}', {'action': 'cancel'}).status_code == 200
    assert request(member, 'GET', f'/api/social/users/{uid}').status_code == 403
    assert request(admin, 'GET', '/api/social/users?q=%25').json()['items'] == []


def test_verified_identity_unique_and_credential_change_invalidates(site):
    request, admin, member, app, *_ = site
    assert request(admin, 'POST', '/api/social/accounts', {'platform': 'codeforces', 'handle': 'Tourist'}).status_code == 409
    runtime = claim(site, admin, 'Owner')
    assert request(admin, 'GET', '/api/social/me').json()['accounts'] == [{'platform': 'codeforces', 'handle': 'Owner'}]
    assert request(member, 'PUT', '/api/accounts', {'items': [{'platform': 'codeforces', 'handle': 'owner', 'cookie': 'session=other'}]}).status_code == 200
    other = child(site, member); _, fp = other.state.credential_checks.credential('codeforces', 'owner')
    other.state.credential_checks.cache[('codeforces', 'owner')] = (fp, {'platform': 'codeforces', 'handle': 'owner', 'checked_at': int(time.time()), 'login_verified': True})
    assert request(member, 'POST', '/api/social/accounts', {'platform': 'codeforces', 'handle': 'owner'}).status_code == 409
    assert request(admin, 'PUT', '/api/accounts', {'items': [{'platform': 'codeforces', 'handle': 'Owner', 'cookie': 'session=changed'}]}).status_code == 200
    assert request(admin, 'GET', '/api/social/me').json()['accounts'] == []
    # No private credential material in central API responses.
    assert 'changed' not in request(member, 'GET', '/api/social/users?q=kendas').text


def test_daily_board_deduplicated_public_metadata_only(site):
    request, admin, member, *_ = site
    runtime = claim(site, admin, 'Owner')
    now = int(time.time())
    with Store(runtime.state.database.path).db as db:
        for sid, pid, handle in [('1', '1000-A', 'Owner'), ('2', '1000-A', 'OWNER'), ('3', '1000-B', 'Other')]:
            data = {'problem': {'title': 'Nice problem', 'difficulty': 1200, 'tags': ['dp']}, 'code': 'PRIVATE SOURCE'}
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('codeforces', handle, sid, pid, now, 'AC', json.dumps(data), now))
    assert request(member, 'GET', '/api/social/leaderboard').json()['items'] == []
    assert request(admin, 'PUT', '/api/social/me', {'leaderboard': True, 'bio': 'PRIVATE_BIO_FOR_FRIENDS'}).status_code == 200
    board = request(member, 'GET', '/api/social/leaderboard').json()
    assert board['items'][0]['count'] == 1
    detail = request(member, 'GET', f"/api/social/users/{admin['user']['id']}").json()
    assert len(detail['items']) == 1
    assert detail['items'][0]['difficulty'] == 1200 and detail['items'][0]['tags'] == ['dp']
    assert detail['items'][0]['url'] == 'https://codeforces.com/problemset/problem/1000/A'
    assert 'PRIVATE SOURCE' not in json.dumps(detail) and 'cookie' not in json.dumps(detail)
    assert 'PRIVATE_BIO_FOR_FRIENDS' not in json.dumps(detail)
    friends(site)
    assert request(member, 'GET', f"/api/social/users/{admin['user']['id']}").json()['profile']['bio'] == 'PRIVATE_BIO_FOR_FRIENDS'
    assert request(member, 'GET', '/api/social/leaderboard?day=2099-01-01').status_code == 422


def test_board_counts_all_today_solutions_not_only_daily_tasks_and_legacy_judges(site):
    from acmweb.social import TZ
    request, admin, member, *_ = site
    runtime = claim(site, admin, 'Owner')
    now = int(time.time())
    day = datetime.fromtimestamp(now, TZ).date().isoformat()
    start = int(datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp())
    with runtime.state.database.connect() as db:
        db.execute("INSERT INTO daily_plans VALUES(?, 'ready',1,?,?,?)", (day, day, day, now))
        db.execute("INSERT INTO daily_plan_tasks(day,ordinal,problem_id,data,created_at) VALUES(?,0,'codeforces:1000-A','{}',?)", (day, now))
        for sid, pid, timestamp, verdict, account in [
            ('1', '1000-A', now, 'AC', 'Owner'),  # daily task
            ('2', '1000-B', now, 'AC', 'Owner'),  # independent practice
            ('3', 'codeforces:1000-B', now, 'AC', 'OWNER'),  # same problem, raw/canonical IDs
            ('4', '1000-C', now, 'AC', 'Owner'),  # another independent problem
            ('5', '1000-D', now, 'WA', 'Owner'),  # official rejudge overrides old workbench AC
            ('6', '1000-E', start - 1, 'AC', 'Owner'),
            ('7', '1000-F', now + 120, 'AC', 'Owner'),
            ('8', '1000-G', now, 'AC', 'Other'),
        ]:
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                ('codeforces', account, sid, pid, timestamp, verdict, '{}', now))
        for aid, pid, sid, verdict, judge in [
            ('legacy', '1000-H', 9, 'AC', {'verdict': 'OK', 'creationTimeSeconds': now}),
            ('duplicate', '1000-B', 2, 'AC', {'verdict': 'OK', 'creationTimeSeconds': now}),
            ('rejudged', '1000-D', 5, 'AC', {'verdict': 'OK', 'creationTimeSeconds': now}),
            ('draft', '1000-I', None, 'AC', {'verdict': 'OK', 'creationTimeSeconds': now}),
            ('bad-evidence', '1000-J', 10, 'AC', {'verdict': 'WA', 'creationTimeSeconds': now}),
        ]:
            db.execute('''INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,
                handle,submission_id,verdict,judge_data,last_synced) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (aid, 'codeforces:' + pid, 'cpp20', 'PRIVATE-SOURCE', 'hash', now,
                 'Owner', sid, verdict, json.dumps(judge), now))
    assert request(admin, 'PUT', '/api/social/me', {'leaderboard': True}).status_code == 200
    board = request(member, 'GET', '/api/social/leaderboard').json()
    assert board['items'][0]['count'] == 4
    assert '自主练习、每日任务和对战' in board['rule']
    detail = request(member, 'GET', f"/api/social/users/{admin['user']['id']}").json()
    assert {row['id'] for row in detail['items']} == {
        'codeforces:1000-A', 'codeforces:1000-B', 'codeforces:1000-C', 'codeforces:1000-H',
    }
    assert 'PRIVATE-SOURCE' not in json.dumps(detail)
    assert request(member, 'GET', f"/api/social/users/{member['user']['id']}").json()['items'] == []


def test_board_counts_all_supported_platforms_without_a_daily_plan(site):
    request, admin, member, *_ = site
    accounts = [{'platform': platform, 'handle': handle, 'cookie': 'session=private-fixture'}
        for platform, handle in [('codeforces', 'Owner'), ('atcoder', 'owner_at'), ('luogu', '1234'), ('nowcoder', '5678')]]
    assert request(admin, 'PUT', '/api/accounts', {'items': accounts}).status_code == 200
    runtime = child(site, admin)
    now = int(time.time())
    for account, pid in zip(accounts, ('1000-A', 'abc300_a', 'P1000', '1000')):
        platform, handle = account['platform'], account['handle']
        checks = runtime.state.credential_checks
        _, fingerprint = checks.credential(platform, handle)
        checks.cache[(platform, handle)] = (fingerprint, {
            'platform': platform, 'handle': handle, 'checked_at': now, 'login_verified': True,
        })
        assert request(admin, 'POST', '/api/social/accounts', {'platform': platform, 'handle': handle}).status_code == 200
        with runtime.state.database.connect() as db:
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                (platform, handle, '1', pid, now, 'AC', '{}', now))
    assert request(admin, 'PUT', '/api/social/me', {'leaderboard': True}).status_code == 200
    assert request(member, 'GET', '/api/social/leaderboard').json()['items'][0]['count'] == 4
    assert request(admin, 'GET', '/api/daily').json()['plan'] is None
    with runtime.state.database.connect() as db:
        for aid, pid, sid, source in [('remote-at', 'abc301_b', 101, 'official_remote_submission'),
                                      ('selftest-at', 'abc301_c', 102, 'selftest')]:
            db.execute('''INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,
                handle,submission_id,verdict,judge_data,last_synced) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (aid, 'atcoder:' + pid, 'cpp20', 'PRIVATE-SOURCE', 'hash', now, 'owner_at', sid,
                 'AC', json.dumps({'platform': 'atcoder', 'source': source, 'verdict': 'AC', 'submitted_at': now}), now))
    assert request(member, 'GET', '/api/social/leaderboard').json()['items'][0]['count'] == 5


def test_duel_official_evidence_winner_daily_exclusion_and_idempotency(site, monkeypatch):
    request, admin, member, app, *_ = site
    duel = prepare_duel(site, monkeypatch); did = duel['id']; start = duel['starts_at']
    for who in (admin, member):
        assert child(site, who).state.database.get('battle_problem_ids') == ['codeforces:1000-A']
    assert request(admin, 'POST', '/api/social/duels', {'user_id': member['user']['id'], 'difficulty': 1200}).status_code == 409
    assert request(admin, 'DELETE', '/api/social/accounts/codeforces/FirstOwner', {}).status_code == 409
    monkeypatch.setattr('acmweb.social.time.time', lambda: start+60)
    histories = {'FirstOwner': [official('FirstOwner', start+10)], 'SecondOwner': [official('SecondOwner', start+20, sid=456)]}
    monkeypatch.setattr('acmweb.social.official_history', lambda handle, since=None: histories[handle])
    checked = request(member, 'POST', f'/api/social/duels/{did}/check', {})
    assert checked.status_code == 200, checked.text
    assert checked.json()['status'] == 'finished' and checked.json()['winner_id'] == admin['user']['id']
    assert request(admin, 'POST', f'/api/social/duels/{did}/check', {}).json()['status'] == 'finished'
    runtime = child(site, admin)
    with runtime.state.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM submissions WHERE verdict='AC'").fetchone()[0] == 1
    assert runtime.state.daily.state()['completed'] == 1
    with app.state.identities.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM social_duel_locks').fetchone()[0] == 0


def test_duel_old_other_team_and_virtual_acs_cannot_win(site, monkeypatch):
    request, admin, member, *_ = site
    duel = prepare_duel(site, monkeypatch); start, did = duel['starts_at'], duel['id']
    monkeypatch.setattr('acmweb.social.time.time', lambda: start+60)
    team = official('FirstOwner', start+5, members=[{'handle': 'FirstOwner'}, {'handle': 'Friend'}])
    virtual = official('FirstOwner', start+6); virtual['author']['participantType'] = 'VIRTUAL'
    histories = {'FirstOwner': [official('FirstOwner', start-1), official('Impostor', start+4), team, virtual], 'SecondOwner': []}
    monkeypatch.setattr('acmweb.social.official_history', lambda handle, since=None: histories[handle])
    checked = request(admin, 'POST', f'/api/social/duels/{did}/check', {'verdict': 'AC', 'finish_time': start+1})
    assert checked.status_code == 200 and checked.json()['status'] == 'active'
    assert checked.json()['winner_id'] is None and checked.json()['sender_ac'] is None
    with child(site, admin).state.database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 0


def test_duel_equal_second_draw_and_pending_judgement_waits(site, monkeypatch):
    request, admin, member, *_ = site
    duel = prepare_duel(site, monkeypatch); start, did = duel['starts_at'], duel['id']
    clock = [start+60]; monkeypatch.setattr('acmweb.social.time.time', lambda: clock[0])
    histories = {'FirstOwner': [official('FirstOwner', start+10)], 'SecondOwner': [official('SecondOwner', start+10), official('SecondOwner', start+5, 'TESTING', sid=999)]}
    monkeypatch.setattr('acmweb.social.official_history', lambda handle, since=None: histories[handle])
    result = request(member, 'POST', f'/api/social/duels/{did}/check', {}).json()
    assert result['status'] == 'active'
    histories['SecondOwner'][1]['verdict'] = 'WRONG_ANSWER'; clock[0] += 61
    result = request(member, 'POST', f'/api/social/duels/{did}/check', {}).json()
    assert result['status'] == 'draw' and result['winner_id'] is None


def test_duel_selection_excludes_old_attempts_and_daily_tasks_fails_closed(site, monkeypatch):
    request, admin, member, app, *_ = site
    first = claim(site, admin, 'FirstOwner'); claim(site, member, 'SecondOwner'); friends(site)
    monkeypatch.setattr('acmweb.social.official_history', lambda handle, since=None: [official(handle, 1, 'WRONG_ANSWER')])
    monkeypatch.setattr('acmweb.social.official_problemset', lambda: [{'id': 'codeforces:1000-A', 'difficulty': 1200}])
    did = request(admin, 'POST', '/api/social/duels', {'user_id': member['user']['id'], 'difficulty': 1200}).json()['id']
    assert request(member, 'POST', f'/api/social/duels/{did}/action', {'action': 'accept'}).status_code == 409
    assert request(admin, 'GET', '/api/social/duels').json()['items'][0]['status'] == 'pending'
    monkeypatch.setattr('acmweb.social.official_history', lambda handle, since=None: [])
    with first.state.database.connect() as db:
        db.execute("INSERT INTO daily_plans VALUES('2026-10-20','ready',1,'2026-10-20','2026-11-20',1)")
        db.execute("INSERT INTO daily_plan_tasks(day,ordinal,problem_id,data,created_at) VALUES('2026-10-20',1,'codeforces:1000-A','{}',1)")
    assert request(member, 'POST', f'/api/social/duels/{did}/action', {'action': 'accept'}).status_code == 409
    assert first.state.database.get('battle_problem_ids', []) == []
    with app.state.identities.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM social_duel_locks').fetchone()[0] == 1


def test_duel_timeout_without_ac_and_csrf_guards(site, monkeypatch):
    request, admin, member, app, *_ = site
    duel = prepare_duel(site, monkeypatch); did = duel['id']
    assert request(admin, 'PUT', '/api/social/me', {'leaderboard': True}, extra={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert request(admin, 'POST', f'/api/social/duels/{did}/check', {}, extra={'Origin': 'https://evil.example'}).status_code == 403
    assert request(admin, 'PUT', '/api/social/me', {'user_id': member['user']['id']}).status_code == 422
    monkeypatch.setattr('acmweb.social.time.time', lambda: duel['ends_at'] + 31)
    checked = request(member, 'POST', f'/api/social/duels/{did}/check', {})
    assert checked.status_code == 200, checked.text
    assert checked.json()['status'] == 'draw' and checked.json()['winner_id'] is None
    with app.state.identities.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM social_duel_locks').fetchone()[0] == 0


def test_duel_check_concurrent_requests_and_unknown_ids_do_not_leak_locks(site, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    request, admin, member, app, *_ = site
    duel = prepare_duel(site, monkeypatch); did = duel['id']
    monkeypatch.setattr('acmweb.social.time.time', lambda: duel['starts_at'] + 60)
    entered, release = Event(), Event()
    def delayed(handle, since=None):
        entered.set()
        assert release.wait(5)
        return []
    monkeypatch.setattr('acmweb.social.official_history', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(request, admin, 'POST', f'/api/social/duels/{did}/check', {})
        try:
            assert entered.wait(5)
            assert request(member, 'POST', f'/api/social/duels/{did}/check', {}).status_code == 409
            assert request(member, 'POST', '/api/social/duels/random-id/check', {}).status_code == 404
            assert set(app.state.social.check_locks) == {did}
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 200
    assert app.state.social.check_locks == {}


def test_official_history_safety_bound_and_challenge_are_not_complete_evidence(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from acmweb.social import official_history, official_problemset
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, chunk_size):
            yield json.dumps({'status': 'OK', 'result': [{'creationTimeSeconds': 1}] * 5000}).encode()
    class Session:
        headers = {}
        def get(self, url, **kwargs):
            assert url.startswith('https://codeforces.com/api/') and kwargs['allow_redirects'] is False and kwargs['stream'] is True
            return Response()
        def close(self): pass
    monkeypatch.setattr('acmweb.social.requests.Session', Session)
    monkeypatch.setattr('acmweb.social.time.sleep', lambda _: None)
    with pytest.raises(HTTPException) as incomplete:
        official_history('Owner')
    assert incomplete.value.status_code == 409
    Response.status_code = 403
    with pytest.raises(HTTPException) as blocked:
        official_history('Owner')
    assert blocked.value.status_code == 502
    with pytest.raises(HTTPException) as catalog:
        official_problemset()
    assert catalog.value.status_code == 502


def test_daily_catalog_metadata_joins_raw_and_canonical_ids_without_full_catalog_scan(site):
    request, admin, _, *_ = site
    runtime = claim(site, admin, 'Owner')
    now = int(time.time())
    with runtime.state.database.connect() as db:
        for pid, title, rating, tags in [('1000-A', 'Catalog A', 1300, ['dp']), ('1001-B', 'Catalog B', 1500, ['graphs'])]:
            db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', 'codeforces:' + pid, json.dumps({'title': title, 'difficulty': rating, 'tags': tags})))
        # Unrelated catalog bodies are not selected or decoded by this path.
        db.execute("INSERT INTO problem_catalog VALUES('codeforces','codeforces:9999-Z','unrelated invalid json')")
        for i, pid in enumerate(('1000-A', 'codeforces:1000-A', 'codeforces:1001-B')):
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)', ('codeforces', 'Owner', str(i), pid, now-10+i, 'AC', '{}', now))
    detail = request(admin, 'GET', f"/api/social/users/{admin['user']['id']}")
    assert detail.status_code == 200, detail.text
    items = detail.json()['items']
    assert [p['id'] for p in items] == ['codeforces:1000-A', 'codeforces:1001-B']
    assert [p['title'] for p in items] == ['Catalog A', 'Catalog B']
    assert [p['difficulty'] for p in items] == [1300, 1500]
    assert [p['tags'] for p in items] == [['dp'], ['graphs']]


def test_duel_ac_preserves_account_snapshot_rating_history_and_watermark(site, monkeypatch):
    from acmweb.analytics import overview
    request, admin, member, *_ = site
    duel = prepare_duel(site, monkeypatch); start, did = duel['starts_at'], duel['id']
    runtime = child(site, admin)
    saved_profile = {'handle': 'FirstOwner', 'rating': 1723, 'maxRating': 1800}
    saved_history = [{'contest_id': '900', 'new_rating': 1723, 'old_rating': 1690}]
    store = Store(runtime.state.database.path)
    try:
        store.save('full-profile-before-duel', start-20, Collection('codeforces', 'FirstOwner',
            profile=saved_profile, rating_history=saved_history,
            submissions=[Submission('old-record', '900-B', start-30, 'AC')],
            complete=True, warnings=['retained account warning']))
        before_watermark = store.watermark('codeforces', 'FirstOwner')
        before_snapshots = store.db.execute('SELECT * FROM snapshots ORDER BY collected_at,run_id').fetchall()
    finally:
        store.close()
    previous_overview = overview(runtime.state.database, now=start)
    monkeypatch.setattr('acmweb.social.time.time', lambda: start+60)
    accepted = official('FirstOwner', start+10)
    accepted.update(programmingLanguage='GNU C++20', timeConsumedMillis=31, memoryConsumedBytes=262144)
    monkeypatch.setattr('acmweb.social.official_history', lambda handle, since=None: [accepted] if handle == 'FirstOwner' else [])
    result = request(member, 'POST', f'/api/social/duels/{did}/check', {})
    assert result.status_code == 200, result.text
    assert result.json()['winner_id'] == admin['user']['id']
    store = Store(runtime.state.database.path)
    try:
        assert store.watermark('codeforces', 'FirstOwner') == before_watermark
        assert store.db.execute('SELECT * FROM snapshots ORDER BY collected_at,run_id').fetchall() == before_snapshots
        record = json.loads(store.db.execute("SELECT data FROM submissions WHERE id='123'").fetchone()[0])
        assert record['language'] == 'GNU C++20' and record['contest_id'] == '1000'
        assert record['time_ms'] == 31 and record['memory_bytes'] == 262144
        assert record['url'] == 'https://codeforces.com/contest/1000/submission/123'
        assert record['problem']['difficulty'] == 1200 and record['problem']['tags'] == ['dp']
    finally:
        store.close()
    current_overview = overview(runtime.state.database, now=start+60)
    assert current_overview['platforms'][0]['rating'] == previous_overview['platforms'][0]['rating'] == 1723
    assert current_overview['platforms'][0]['rating_history'] == saved_history
    assert current_overview['platforms'][0]['last_sync'] == previous_overview['platforms'][0]['last_sync']
    assert current_overview['stats']['accepted'] == previous_overview['stats']['accepted'] + 1


def test_verified_binding_automatically_joins_board_without_second_claim(site):
    request, admin, member, *_ = site
    assert request(admin, 'PUT', '/api/accounts', {'items': [
        {'platform': 'codeforces', 'handle': 'Owner', 'cookie': 'session=verified-private-value'}]}).status_code == 200
    runtime = child(site, admin)
    checks = runtime.state.credential_checks
    _, fingerprint = checks.credential('codeforces', 'Owner')
    checks.cache[('codeforces', 'Owner')] = (fingerprint, {'platform': 'codeforces', 'handle': 'Owner',
        'checked_at': int(time.time()), 'login_verified': True})
    now = int(time.time())
    with runtime.state.database.connect() as db:
        db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
            ('codeforces', 'Owner', '801', '1000-A', now, 'AC', json.dumps({'problem': {'tags': ['dp'], 'difficulty': 1200}, 'score': 100}), now))
    assert request(admin, 'PUT', '/api/social/me', {'leaderboard': True}).status_code == 200
    board = request(member, 'GET', '/api/social/leaderboard').json()
    assert board['items'][0]['count'] == 1
    detail = request(member, 'GET', f"/api/social/users/{admin['user']['id']}").json()['items'][0]
    assert detail['score'] == 100 and detail['tags'] == ['dp']
    # Ownership persists when the short-lived connectivity cache is gone.
    checks.cache.clear()
    assert request(member, 'GET', '/api/social/leaderboard').json()['items'][0]['count'] == 1


def test_automatic_claim_respects_explicit_optout_and_cannot_steal_identity(site):
    request, admin, member, *_ = site
    runtime = claim(site, admin, 'Owner')
    assert request(admin, 'DELETE', '/api/social/accounts/codeforces/OWNER', {}).status_code == 200
    assert request(admin, 'GET', '/api/social/me').json()['accounts'] == []
    assert request(admin, 'POST', '/api/social/accounts', {'platform': 'codeforces', 'handle': 'Owner'}).status_code == 200
    assert request(member, 'PUT', '/api/accounts', {'items': [
        {'platform': 'codeforces', 'handle': 'owner', 'cookie': 'session=other'}]}).status_code == 200
    other = child(site, member)
    checks = other.state.credential_checks
    _, fp = checks.credential('codeforces', 'owner')
    checks.cache[('codeforces', 'owner')] = (fp, {'platform': 'codeforces', 'handle': 'owner',
        'checked_at': int(time.time()), 'login_verified': True})
    assert request(member, 'GET', '/api/social/me').json()['accounts'] == []
    assert request(admin, 'GET', '/api/social/me').json()['accounts'] == [{'platform': 'codeforces', 'handle': 'Owner'}]


def test_unverified_or_changed_binding_never_auto_claims(site):
    request, admin, *_ = site
    runtime = claim(site, admin, 'Owner')
    assert request(admin, 'PUT', '/api/accounts', {'items': [
        {'platform': 'codeforces', 'handle': 'Owner', 'cookie': 'session=changed'}]}).status_code == 200
    # Stale cache proves the former credential only.
    assert request(admin, 'GET', '/api/social/me').json()['accounts'] == []
    checks = runtime.state.credential_checks
    _, fp = checks.credential('codeforces', 'Owner')
    checks.cache[('codeforces', 'Owner')] = (fp, {'platform': 'codeforces', 'handle': 'Owner',
        'checked_at': int(time.time()), 'login_verified': False})
    assert request(admin, 'GET', '/api/social/me').json()['accounts'] == []


def test_catalog_sync_updates_today_board_and_public_problem_details(site, monkeypatch):
    import asyncio
    import acmweb.jobs as jobs_module
    request, admin, member, *_ = site
    runtime = claim(site, admin, 'Owner')
    assert request(admin, 'PUT', '/api/social/me', {'leaderboard': True}).status_code == 200
    monkeypatch.setattr(jobs_module, 'refresh_catalog', lambda database, on_progress=None:
        {'count': 1, 'complete': True, 'platforms': [{'platform': 'codeforces', 'status': 'updated', 'count': 1}]})
    now = int(time.time())
    def collect(config, args):
        assert args.since <= now
        store = Store(config.database)
        try:
            result = Collection('codeforces', 'Owner', complete=True, submissions=[
                Submission('901', '1000-A', now, 'AC', score=100,
                    problem={'title': 'Synced official fixture', 'difficulty': 1200, 'tags': ['dp']})])
            store.save('fixture-catalog-run', now, result)
            store.save_run({'run_id': 'fixture-catalog-run', 'collected_at': now,
                'accounts': [result.to_dict()]})
        finally:
            store.close()
        return 0
    monkeypatch.setattr(jobs_module, 'crawl', collect)
    # The multi-user fixture stubs Jobs.crawl globally; restore the real bridge
    # while replacing only the official network collector with test evidence.
    monkeypatch.setattr(Jobs, 'crawl', _REAL_CRAWL)
    async def run():
        jobs = runtime.state.jobs
        job = jobs.start('catalog')
        await jobs.tasks[job['id']][1]
        return jobs.list()[0]
    row = asyncio.run(run())
    assert row['status'] == 'completed'
    assert row['result']['submission_sync']['status'] == 'completed'
    assert request(member, 'GET', '/api/social/leaderboard').json()['items'][0]['count'] == 1
    item = request(member, 'GET', f"/api/social/users/{admin['user']['id']}").json()['items'][0]
    assert item['url'] == 'https://codeforces.com/problemset/problem/1000/A'
    assert item['score'] == 100 and item['difficulty'] == 1200 and item['tags'] == ['dp']
