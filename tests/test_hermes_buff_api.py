"""Synthetic end-to-end checks for explicit Hermes preparation and consumption."""

import asyncio
import threading
import time

from test_hermes_api import PID, add_candidate, client, generic_complete, start, wait


SCORE = {'score_range': [800, 1200]}
NEXT = 'codeforces:5-A'


def segment_with_stock(client, expected=1):
    for _ in range(200):
        response = client.get('/api/hermes/buff')
        assert response.status_code == 200, response.text
        segments = response.json()['segments']
        if segments and segments[0]['available'] == expected:
            return segments[0]
        time.sleep(.01)
    raise AssertionError(response.json())


def configure(client, target=1):
    response = client.post('/api/hermes/buff', json={**SCORE, 'target': target})
    assert response.status_code == 200, response.text
    return response.json()


def test_warming_creates_no_draw_and_committed_delivery_refills(client, monkeypatch):
    c, service, model = client
    add_candidate(service, NEXT)
    model.side_effect = generic_complete
    monkeypatch.setattr(service, 'choose', lambda pool, *_: (min(pool, key=lambda p: p['id']), 'test'))
    assert c.get('/api/hermes/buff').json()['segments'] == []
    assert model.await_count == 0
    assert configure(c)['segments'][0]['target'] == 1
    assert segment_with_stock(c)['stocked'] == 1
    assert model.await_count == 2
    assert c.get('/api/hermes/draws').json()['total'] == 0
    assert service.control.memory.drawn_ids() == set()

    key = 'a'
    job = wait(c, start(c, key))
    assert job['status'] == 'completed', job
    assert job['result']['problem']['id'] == PID
    assert job['result']['selection_strategy'] == 'hermes_prepared_pool'
    assert job['selection_progress']['ai_review_calls'] == 0
    assert job['selection_progress']['report_attempts'] == 0
    assert job['delivery']['state'] == 'pending'
    assert wait(c, start(c, key))['id'] == job['id']
    assert service.control.memory.drawn_ids() == {PID}
    assert segment_with_stock(c)['stocked'] == 1
    assert model.await_count == 4
    assert configure(c, 0)['segments'] == []


def test_new_draw_preempts_inflight_preparation(client):
    c, service, model = client
    entered = threading.Event()
    cancelled = threading.Event()

    async def complete(task, *args):
        if not entered.is_set():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return await generic_complete(task, *args)

    model.side_effect = complete
    configure(c)
    assert entered.wait(2)
    job = wait(c, start(c, 'b'))
    assert cancelled.is_set()
    assert job['status'] == 'completed', job
    assert job['result']['problem']['id'] == PID
    assert job['selection_progress']['ai_review_calls'] == 1
    assert service.control.memory.drawn_ids() == {PID}


def test_per_request_exclusion_keeps_stock_for_other_draws(client, monkeypatch):
    c, service, model = client
    add_candidate(service, NEXT)
    model.side_effect = generic_complete
    monkeypatch.setattr(service, 'choose', lambda pool, *_: (min(pool, key=lambda p: p['id']), 'test'))
    configure(c)
    segment_with_stock(c)

    response = c.post('/api/hermes/draw', json={**SCORE, 'exclude_ids': [PID]},
                      headers={'Idempotency-Key': 'c' * 32})
    assert response.status_code == 200, response.text
    job = wait(c, response.json()['id'])
    assert job['status'] == 'completed', job
    assert job['result']['problem']['id'] == NEXT
    assert job['selection_progress']['ai_review_calls'] == 1
    assert segment_with_stock(c)['stocked'] == 1

    job = wait(c, start(c, 'd'))
    assert job['status'] == 'completed', job
    assert job['result']['problem']['id'] == PID
    assert job['result']['selection_strategy'] == 'hermes_prepared_pool'
