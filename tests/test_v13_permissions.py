import time
from test_multiuser import site


def test_disabling_automatic_code_sharing_is_not_blocked_by_ai_budget(site):
    request, admin, _, app, *_ = site
    now=int(time.time())
    with app.state.identities.connect() as db:
        db.execute('INSERT INTO rate_limits VALUES(?,?,?,?)',('cost:'+admin['user']['id'],now//60,6,now+60))
    # Consent withdrawal is a cheap preference, not an AI invocation.
    response=request(admin,'PUT','/api/workbench/coach-settings',{'enabled':False})
    assert response.status_code==200,response.text
    assert response.json()['enabled'] is False
    assert request(admin,'PUT','/api/workbench/coach-settings',{'enabled':False},extra={'X-CSRF-Token':''}).status_code==403
