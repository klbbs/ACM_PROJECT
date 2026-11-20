import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from acmhermes import prepared_pool
from acmhermes.memory import DrawMemory
from acmhermes.models import Draw
from acmhermes.prepared_pool import PreparedPool, PreparedRefill
from acmhermes.review_memory import ReviewMemory
from acmweb.codex_bridge import AIError
from acmweb.database import Database


def open_pool(path):
    db = Database(path)
    return PreparedPool(db, ReviewMemory(db), DrawMemory(db))


@pytest.fixture
def pool(tmp_path):
    return open_pool(tmp_path / 'private.sqlite3')


def context(request):
    return f'{request.score_range}:{request.topic}'


def problem(number):
    return f'codeforces:{number}-A'


def prepared(pid):
    return {'choice': {'problem_id': pid}, 'report': {'title': 'Private report'}}


def stock(pool, request, pid, owner='background'):
    assert pool.begin_refill(request, context(request), owner)
    try:
        assert pool.reviews.claim(pid, owner, 1200)
        return pool.stock(request, context(request), prepared(pid), 'statement-hash', owner)
    finally:
        pool.end_refill(request, owner)


def test_persistent_inventory_uses_atomic_claim_and_draw_completion(pool):
    request = Draw(score_range=(1700, 1800), topic='dp')
    pool.configure(request, 2)
    assert stock(pool, request, problem(1))
    assert stock(pool, request, problem(2))

    pool = open_pool(pool.db.path)
    key = context(request)
    pid = problem(1)
    assert pool.ids() == {pid, problem(2)}
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(pool.reserve, key, owner, problem_id=pid) for owner in ('first', 'second')]
        reservations = [future.result() for future in futures]
    assert sum(entry is not None for entry in reservations) == 1
    winner = 'first' if reservations[0] is not None else 'second'
    entry = reservations[0] or reservations[1]
    assert entry.result == prepared(pid)
    assert entry.statement_hash == 'statement-hash'
    assert pool.ids(winner) == {problem(2)}
    assert pool.status(context) == [{'score_range': [1700, 1800], 'topic': 'dp', 'target': 2,
                                     'stocked': 2, 'available': 1, 'failures': 0, 'paused': False,
                                     'retry_at': None, 'failure_reason': None}]

    pool.reviews.release(winner)
    assert pool.reserve(key, 'replacement', problem_id=pid) == entry
    result = {'problem': {'id': pid, 'platform': 'codeforces'}}
    with pytest.raises(RuntimeError), pool.db.connect() as conn:
        pool.memory.record(conn, 'replacement', result)
        raise RuntimeError('completion failed')
    assert pool.reserve(key, 'replacement', problem_id=pid) == entry
    with pool.db.connect() as conn:
        pool.memory.record(conn, 'replacement', result)
    assert pool.reserve(key, 'another', problem_id=pid) is None
    assert pool.ids() == {problem(2)}
    assert pool.memory.drawn_ids() == {pid}


def test_refill_respects_scope_target_exclusions_and_configuration(pool):
    first = Draw(score_range=(800, 1000))
    second = Draw(score_range=(1300, 1500), topic='dp')
    pool.configure(first, 1)
    pool.configure(second, 2)
    assert pool.begin_refill(first, context(first), 'holder')
    assert not pool.begin_refill(second, context(second), 'blocked')
    pid = problem(3)
    assert not pool.stock(first, context(first), prepared(pid), 'hash', 'holder')
    assert pool.reviews.claim(pid, 'holder', 1200)
    assert not pool.stock(second, context(second), prepared(pid), 'hash', 'holder')
    assert pool.stock(first, context(first), prepared(pid), 'hash', 'holder')
    pool.end_refill(first, 'holder')
    assert not pool.begin_refill(first, context(first), 'full')
    assert not stock(pool, second, pid)
    assert pool.reserve(context(first), 'consumer', excluded={pid}) is None
    assert pool.entries(context(second)) == []

    with pool.db.connect() as conn:
        conn.execute('INSERT OR IGNORE INTO ai_reports VALUES(?,?,?,?,?,?)',
                     ('r', 1, 'recommend', 'mock', 'mock', json.dumps({
                         'selected_problems': [{'id': pid, 'platform': 'codeforces'}]
                     })))
    assert pool.reserve(context(first), 'consumer') is None
    assert pool.begin_refill(first, 'changed-account-or-model', 'new-context')
    assert pool.ids() == set()
    pool.configure(first, 0)
    assert pool.reviews.claim(problem(4), 'new-context', 1200)
    assert not pool.stock(first, 'changed-account-or-model', prepared(problem(4)), 'hash', 'new-context')
    pool.end_refill(first, 'new-context')


def test_worker_prewarms_and_cancels_active_preparation(pool):
    async def scenario():
        request = Draw(score_range=(1900, 2100))
        pool.configure(request, 2)
        finished_first = asyncio.Event()
        entered_second = asyncio.Event()
        calls = []

        async def prepare(actual, owner):
            calls.append(owner)
            assert actual == request
            pid = problem(10 + len(calls))
            assert pool.reviews.claim(pid, owner, 1200)
            if len(calls) == 1:
                finished_first.set()
                return prepared(pid), 'hash'
            entered_second.set()
            await asyncio.Event().wait()

        worker = PreparedRefill(pool, context_for=context, prepare=prepare)
        worker.start()
        try:
            await asyncio.wait_for(finished_first.wait(), 2)
            await asyncio.wait_for(entered_second.wait(), 2)
            assert worker.running
            assert len(pool.entries(context(request))) == 1
        finally:
            await worker.close()
        assert not worker.active
        assert not worker.running
        assert not pool.reviews.claimed()
        assert pool.begin_refill(request, context(request), 'restart')
        pool.end_refill(request, 'restart')

    asyncio.run(scenario())


def test_failures_back_off_then_pause_until_reconfigured(pool, monkeypatch):
    request = Draw(score_range=(1000, 1200))
    another = Draw(score_range=(1400, 1600))
    now = 10_000
    monkeypatch.setattr(prepared_pool.time, 'time', lambda: now)
    pool.configure(request, 1)
    pool.configure(another, 1)

    for number, cooldown in enumerate((*prepared_pool.RETRY_DELAYS, None), start=1):
        assert pool.begin_refill(request, context(request), 'holder')
        pool.end_refill(request, 'holder', failure_reason='hermes_candidates_exhausted')
        state = next(item for item in pool.status(context) if item['score_range'] == [1000, 1200])
        assert state['failures'] == number
        assert state['failure_reason'] == 'hermes_candidates_exhausted'
        assert state['retry_at'] == (now + cooldown if cooldown else None)
        assert state['paused'] is (cooldown is None)
        assert not pool.begin_refill(request, context(request), 'too-early')
        if cooldown:
            now += cooldown

    assert stock(pool, another, problem(44))
    now += 3600
    pool = open_pool(pool.db.path)
    assert not pool.begin_refill(request, context(request), 'still-paused')
    pool.configure(request, 1)
    assert pool.begin_refill(request, context(request), 'resumed')
    pool.end_refill(request, 'resumed')
    state = next(item for item in pool.status(context) if item['score_range'] == [1000, 1200])
    assert state['failures'] == 0
    assert state['failure_reason'] is None


def test_disabling_active_segment_keeps_scheduler_available(pool):
    async def scenario():
        first = Draw(score_range=(800, 900))
        second = Draw(score_range=(1700, 1800))
        entered = asyncio.Event()
        prepared_second = asyncio.Event()
        pool.configure(first, 1)

        async def prepare(request, owner):
            if request == first:
                assert pool.reviews.claim(problem(60), owner, 1200)
                entered.set()
                await asyncio.Event().wait()
            assert request == second
            assert pool.reviews.claim(problem(61), owner, 1200)
            prepared_second.set()
            return prepared(problem(61)), 'hash'

        worker = PreparedRefill(pool, context_for=context, prepare=prepare)
        worker.start()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            pool.configure(second, 1)
            await worker.cancel_segment(second)
            assert worker.running
            pool.configure(first, 0)
            await asyncio.wait_for(worker.cancel_segment(first), 2)
            await asyncio.wait_for(prepared_second.wait(), 2)
            assert worker.active
            assert problem(60) not in pool.reviews.claimed()
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_statement_change_releases_stock_capacity(pool):
    request = Draw(score_range=(1800, 1900))
    pid = problem(67)
    cache = {pid: 'statement-hash'}

    def statement_fingerprint(problem_id, connection):
        if problem_id not in cache:
            raise AIError('statement unavailable')
        return cache[problem_id]

    pool = PreparedPool(pool.db, pool.reviews, pool.memory, statement_fingerprint=statement_fingerprint)
    pool.configure(request, 1)
    assert stock(pool, request, pid)
    cache[pid] = 'another-hash'
    assert pool.status(context)[0]['stocked'] == 0
    assert pool.entries(context(request)) == []
    assert pool.reserve(context(request), 'foreground', problem_id=pid) is None
    assert pool.begin_refill(request, context(request), 'background')
    try:
        assert pool.ids() == set()
        assert pool.reviews.claim(pid, 'background', 1200)
        assert not pool.stock(request, context(request), prepared(pid), 'statement-hash', 'background')
        assert pool.stock(request, context(request), prepared(pid), 'another-hash', 'background')
    finally:
        pool.end_refill(request, 'background')
    del cache[pid]
    assert pool.status(context)[0]['stocked'] == 0


def test_crashed_refill_lease_expires_with_candidate_claim(pool, monkeypatch):
    request = Draw(score_range=(2200, 2350))
    pid = problem(71)
    now = 30_000
    monkeypatch.setattr(prepared_pool.time, 'time', lambda: now)
    pool.configure(request, 1)
    assert pool.begin_refill(request, context(request), 'lost')
    assert pool.reviews.claim(pid, 'lost', 1200)
    now += prepared_pool.REFILL_HEARTBEAT_SECONDS
    assert pool.renew_refill('lost')
    now += prepared_pool.REFILL_LEASE_SECONDS - 1
    assert not pool.begin_refill(request, context(request), 'replacement')
    now += 1
    assert pool.begin_refill(request, context(request), 'replacement')
    assert not pool.renew_refill('lost')
    assert pool.reviews.claim(pid, 'replacement', 1200)
    assert not pool.stock(request, context(request), prepared(pid), 'hash', 'lost')
    pool.end_refill(request, 'replacement')
