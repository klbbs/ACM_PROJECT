import json

from test_multiuser import site


def test_recommendation_filter_precedes_pagination_and_preserves_user_isolation(site):
    request, admin, member, app, *_ = site
    assert request(admin, 'GET', '/api/reports').status_code == 200
    db = app.state.workspaces.apps[admin['user']['id']][0].state.database
    with db.connect() as conn:
        conn.execute('INSERT INTO ai_reports VALUES(?,?,?,?,?,?)', (
            'older-recommendation', 10, 'recommend', 'api', 'text-model',
            json.dumps({'selected_problems': [{'id': 'codeforces:4-A'}]}),
        ))
        conn.executemany('INSERT INTO ai_reports VALUES(?,?,?,?,?,?)', [
            (f'new-analysis-{i}', 200+i, 'analysis', 'api', 'text-model', '{}') for i in range(55)
        ])
    assert all(row['kind'] == 'analysis' for row in request(admin, 'GET', '/api/reports').json()['items'])
    selected = request(admin, 'GET', '/api/reports?kind=recommend')
    assert selected.status_code == 200
    assert [row['id'] for row in selected.json()['items']] == ['older-recommendation']
    assert request(admin, 'GET', '/api/reports/older-recommendation').json()['data']['selected_problems'] == [
        {'id': 'codeforces:4-A'},
    ]
    assert request(member, 'GET', '/api/reports?kind=recommend').json()['items'] == []
    assert request(member, 'GET', '/api/reports/older-recommendation').status_code == 404
    assert request(None, 'GET', '/api/reports?kind=recommend').status_code == 401
    assert request(admin, 'GET', '/api/reports?kind=unsupported').status_code == 422
