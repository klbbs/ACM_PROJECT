from test_web import client


def test_publication_floor_is_a_real_calendar_date_and_persists(client):
    for value in ['2026-02-30', '2026-13-01', 'yesterday', '2026-1-1']:
        response = client.put('/api/training',json={'published_after':value})
        assert response.status_code == 422
    response = client.put('/api/training',json={'published_after':'2025-01-01'})
    assert response.status_code == 200
    assert client.get('/api/training').json()['published_after'] == '2025-01-01'
    assert client.get('/api/catalog?published_after=2026-02-30').status_code == 422


def test_catalog_explains_unverified_platforms_without_network(client,monkeypatch):
    monkeypatch.setattr('requests.Session.request',lambda *a,**k: (_ for _ in ()).throw(AssertionError('GET must not make upstream requests')))
    result = client.get('/api/catalog').json()
    assert result['items'] == []
    assert len(result['eligibility']['platforms']) == 4
    assert all(not row['login_verified'] for row in result['eligibility']['platforms'])


def test_recommendation_cannot_force_a_single_platform(client,monkeypatch):
    captured={}
    def start(kind,payload):
        captured.update(kind=kind,payload=payload)
        return {'id':'test-job'}
    monkeypatch.setattr(client.app.state.jobs,'start',start)
    response=client.post('/api/recommend',json={'provider':'api','model':'synthetic','platform':'atcoder','published_after':'2025-01-01'})
    assert response.status_code == 200
    assert captured['kind'] == 'recommend' and captured['payload']['platform'] == ''
    assert captured['payload']['published_after'] == '2025-01-01'


def test_legacy_contest_filter_returns_explanation_instead_of_silent_widening(client):
    assert client.put('/api/training',json={'min_contest':2200}).status_code==200
    response=client.get('/api/catalog?published_after=2020-01-01')
    assert response.status_code==200
    assert response.json()['items']==[]
    assert response.json()['eligibility']['requires_publication_date'] is True
    assert '日期' in response.json()['eligibility']['notice']
