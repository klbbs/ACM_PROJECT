import time
from test_multiuser import site


def test_presence_is_authenticated_scoped_and_expires(site):
    request, admin, member, app, *_ = site
    assert request(None, 'GET', '/api/presence').status_code == 401
    assert request(member, 'POST', '/api/presence', {}, {'X-CSRF-Token':'wrong'}).status_code == 403
    assert request(admin, 'POST', '/api/presence', {}).status_code == 200
    assert request(member, 'POST', '/api/presence', {}).status_code == 200
    visible = request(member, 'GET', '/api/presence').json()['items']
    assert {r['username'] for r in visible} == {'kendas', 'member'}
    assert next(r for r in visible if r['self'])['username'] == 'member'
    assert all(set(r) == {'username', 'self'} for r in visible)
    with app.state.identities.connect() as db:
        db.execute('UPDATE online_presence SET seen_at=? WHERE user_id=?', (int(time.time())-91, admin['user']['id']))
    assert [r['username'] for r in request(member,'GET','/api/presence').json()['items']] == ['member']
    request(member,'POST','/api/auth/logout',{})
    assert request(admin,'GET','/api/presence').json()['items'] == []
