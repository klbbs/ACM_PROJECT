"""Date-bound duel behavior with isolated official API fixtures."""
import json
from datetime import datetime

import pytest

from acmweb.social import TZ, official_problemset
from test_multiuser import site
from test_social import claim, friends


def setup_duel(site, monkeypatch, floor='2024-01-01', pool=None):
    request, sender, target, *_ = site
    claim(site, sender, 'DateOwnerA')
    claim(site, target, 'DateOwnerB')
    friends(site)
    monkeypatch.setattr('acmweb.social.official_history', lambda *args, **kwargs: [])
    monkeypatch.setattr('acmweb.social.official_problemset', lambda: pool or [])
    response = request(sender, 'POST', '/api/social/duels', {
        'user_id': target['user']['id'], 'difficulty': 1200,
        'duration_minutes': 60, 'published_after': floor})
    assert response.status_code == 200, response.text
    return response.json()['id']


def test_both_sides_see_fixed_date_and_accept_cannot_lower_it(site, monkeypatch):
    request, sender, target, *_ = site
    did = setup_duel(site, monkeypatch)
    for actor in (sender, target):
        invitation = request(actor, 'GET', '/api/social/duels').json()['items'][0]
        assert invitation['published_after'] == '2024-01-01'
    result = request(target, 'POST', f'/api/social/duels/{did}/action',
        {'action': 'accept', 'published_after': '2000-01-01'})
    assert result.status_code == 422
    assert request(sender, 'GET', '/api/social/duels').json()['items'][0]['published_after'] == '2024-01-01'


def test_duel_filters_reliable_contest_time_at_beijing_day_boundary_not_problem_id(site, monkeypatch):
    floor = int(datetime(2024, 1, 1, tzinfo=TZ).timestamp())
    def item(pid, timestamp, source='codeforces.contest.startTimeSeconds', rating=1200):
        return {'id': pid, 'difficulty': rating, 'title': 'Synthetic task', 'published_at': timestamp,
            'published_at_source': source, 'tags': [], 'url': 'https://codeforces.com/problemset/problem/4/A'}
    pool = [item('codeforces:99999-A', floor - 1),
            item('codeforces:1-A', None), item('codeforces:2-A', floor, 'guessed_from_problem_id'),
            item('codeforces:3-A', floor, rating=1300), item('codeforces:4-A', floor)]
    request, _, target, *_ = site
    did = setup_duel(site, monkeypatch, pool=pool)
    accepted = request(target, 'POST', f'/api/social/duels/{did}/action', {'action': 'accept'})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['problem']['id'] == 'codeforces:4-A'
    assert accepted.json()['problem']['published_at'] == floor


def test_missing_reliable_dates_and_legacy_invites_fail_with_actionable_reason(site, monkeypatch):
    request, sender, target, app, *_ = site
    did = setup_duel(site, monkeypatch, pool=[{'id': 'codeforces:99999-A', 'difficulty': 1200}])
    denied = request(target, 'POST', f'/api/social/duels/{did}/action', {'action': 'accept'})
    assert denied.status_code == 409
    assert '2024-01-01' in denied.text and '缺少可靠比赛日期' in denied.text
    with app.state.identities.connect() as db:
        db.execute('UPDATE social_duels SET published_after=NULL WHERE id=?', (did,))
    legacy = request(target, 'POST', f'/api/social/duels/{did}/action', {'action': 'accept'})
    assert legacy.status_code == 409 and '重新邀请' in legacy.text


def test_official_problemset_joins_actual_finished_contest_start_time(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield json.dumps(self.payload).encode()
    class Session:
        headers = {}
        def get(self, url, **kwargs):
            assert url.startswith('https://codeforces.com/api/')
            assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
            if 'problemset.problems' in url:
                return Response({'status': 'OK', 'result': {'problems': [
                    {'contestId': 3, 'index': 'A', 'name': 'Task', 'rating': 1200},
                    {'contestId': 99999, 'index': 'B', 'name': 'Undated', 'rating': 1200}]}})
            return Response({'status': 'OK', 'result': [{'id': 3, 'phase': 'FINISHED', 'startTimeSeconds': 1704067200}]})
        def close(self): pass
    monkeypatch.setattr('acmweb.social.requests.Session', Session)
    monkeypatch.setattr('acmweb.social.pace_official_api', lambda: None)
    rows = official_problemset()
    assert rows[0]['published_at'] == 1704067200
    assert rows[0]['published_at_source'] == 'codeforces.contest.startTimeSeconds'
    assert rows[1]['published_at'] is None

