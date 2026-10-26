"""Isolated regressions for private topic-method feasibility evidence.

Every AI reply, official statement and platform login here is synthetic. These
tests never contact a judge or a model, never run or submit code, and do not
prove real platform retrieval or a real model conclusion.
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from acmweb.ai import AIProvider, AIResult
from acmweb.codex_bridge import AIError
from acmweb.database import Database
from acmweb.settings import Settings
from acmweb.topic_feasibility import SUMMARY_PREFIX, TopicFeasibility
from acmweb.training import Training
from acmweb.training_topics import TOPICS, matches_topic

# A real official topic whose precise tag is 区间 DP (144) and whose parent is
# 动态规划 DP (3). Only the parent's broad Codeforces tag `dp` is inferable.
TOPIC = 'luogu-144'
PARENT = 'luogu-3'
PID = 'codeforces:100-A'
YES, NO, UNSURE = 'yes', 'no', 'uncertain'


def statement(pid=PID, sections=None, **overrides):
    return {'id': pid, 'source': 'official', 'render_version': 2, 'title': 'Synthetic merge exercise',
            'sections': ['Merge the given ranges; n <= 2000.'] if sections is None else sections,
            'body_html': '<p>Merge the given ranges.</p>',
            'samples': [{'input': '1\n1 2', 'output': '3'}], 'images': [], **overrides}


def problem(pid=PID, tags=('dp',), difficulty=1500, published='2025-02-01T00:00:00+00:00',
            platform='codeforces', title='Synthetic merge exercise'):
    stamp = int(datetime.fromisoformat(published).timestamp())
    return {'id': pid, 'platform': platform, 'problem_id': pid.split(':', 1)[1], 'title': title,
            'url': 'https://example.invalid/' + pid,
            'difficulty': difficulty if platform == 'codeforces' else None,
            'native_difficulty': 2 if platform == 'luogu' else None, 'tags': list(tags),
            'contest_id': 1000, 'published_at': stamp,
            'published_at_source': ('codeforces.contest.startTimeSeconds' if platform == 'codeforces'
                                    else 'luogu.problem.createTime')}


def seed(db, *problems):
    with db.connect() as conn:
        conn.executemany('INSERT OR REPLACE INTO problem_catalog VALUES(?,?,?)',
                         [(p['platform'], p['id'], json.dumps(p)) for p in problems])


def cache_statement(db, pid=PID, **overrides):
    material = statement(pid, **overrides)
    with db.connect() as conn:
        conn.execute('INSERT INTO wb_problems VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                     (pid, json.dumps(material), int(time.time())))
    return material


def mark_ac(db, pid=PID):
    platform, key = pid.split(':', 1)
    with db.connect() as conn:
        conn.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)',
                     (platform, 'synthetic-handle', 'submission-1', key, int(time.time()), 'AC', '{}',
                      int(time.time())))


def accounts(*platforms):
    platforms = platforms or ('codeforces',)
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': [
        {'platform': name, 'status': 'authenticated', 'login_verified': True} for name in platforms]}))


def no_accounts():
    return SimpleNamespace(credential_checks=SimpleNamespace(statuses=lambda: {'items': []}))


def reply(pid, topic, decision='yes', **overrides):
    return {'problem_id': pid, 'topic_id': topic, 'decision': decision,
            'evidence': ['题面要求合并区间，n <= 2000。', '合并需要区间状态转移。'],
            'complexity': '时间 O(n^2)、额外空间 O(n^2)，满足 n <= 2000。',
            'limitations': ['只阅读题面文字与样例，未运行或提交代码。'], **overrides}


def answers(decisions=YES, provenance=None):
    """Synthetic model transport: one reply per problem, never a real request."""
    async def complete(task, provider, model, payload, schema):
        assert task == 'topic_method_feasibility'
        assert provider in {'api', 'codex'} and model
        decision = decisions.get(payload['problem_id'], YES) if isinstance(decisions, dict) else decisions
        return AIResult(reply(payload['problem_id'], payload['topic_id'], decision),
                        {} if provenance is None else provenance)
    return complete


def envelope(tmp_path, name='user.sqlite3', checker=None, ai=None):
    db = Database(tmp_path / name)
    fetched = []

    def fetch(pid):
        fetched.append(pid)
        cache_statement(db, pid)
        return {'problem_id': pid}

    transport = ai or SimpleNamespace(complete=AsyncMock(side_effect=answers()))
    service = TopicFeasibility(db, checker or accounts(), SimpleNamespace(statement=fetch), transport)
    return SimpleNamespace(db=db, service=service, ai=transport, fetched=fetched)


def run(service, *args, **kwargs):
    return asyncio.run(service.check(TOPIC, *args, **kwargs))


def rows(db):
    with db.connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM topic_method_evidence')]


def count(db, table):
    with db.connect() as conn:
        return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def pids_sent(ai):
    return {call.args[3]['problem_id'] for call in ai.complete.call_args_list}


def test_topic_fixture_really_needs_the_parent_tag():
    """Guards the meaning of this file: `dp` proves the parent, never 区间 DP."""
    assert TOPICS[TOPIC]['parent_id'] == PARENT
    assert TOPICS[PARENT]['cf_tags'] == {'dp'} and not TOPICS[TOPIC]['cf_tags']
    assert matches_topic(problem(), TOPICS[PARENT])
    assert not matches_topic(problem(), TOPICS[TOPIC])


def test_parent_tag_only_candidate_is_read_then_becomes_private_pool_evidence(empty):
    seed(empty.db, problem())
    result = run(empty.service, 'api', 'synthetic-model')
    assert result['items'][0]['decision'] == 'yes' and result['accepted_count'] == 1
    assert result['checked_count'] == 1 and result['not_checked_count'] == 0
    assert result['incomplete'] is False
    # The statement read is the candidate's own official statement.
    assert empty.fetched == [PID]
    task, provider, model, payload, schema = empty.ai.complete.call_args.args
    assert task == 'topic_method_feasibility' and provider == 'api' and model == 'synthetic-model'
    assert payload['problem_id'] == PID and payload['topic_id'] == TOPIC
    assert payload['statement']['id'] == PID
    assert payload['statement']['sections'] == statement()['sections']
    assert payload['topic'] == {key: TOPICS[TOPIC][key] for key in ('id', 'title', 'description')}
    assert len(payload['statement_hash']) == 64
    saved = rows(empty.db)
    assert len(saved) == 1
    assert saved[0]['problem_id'] == PID and saved[0]['decision'] == 'yes'
    assert saved[0]['statement_hash'] == payload['statement_hash']
    assert saved[0]['provider'] == 'api' and saved[0]['model'] == 'synthetic-model'
    approved = empty.service.approved_map(TOPIC)
    assert set(approved) == {PID}
    evidence = approved[PID]
    assert evidence['matched'] and evidence['source'] == 'ai_method_feasibility'
    assert evidence['decision'] == 'yes' and evidence['topic_id'] == TOPIC
    assert '非官方标签' in evidence['notice'] and len(evidence['terms']) == 2
    # The evidence alone may place the task in the real training pool.
    training = Training(empty.db, empty.ai, accounts())
    training.topic_feasibility = empty.service
    pool = training._pool(TOPICS[TOPIC], cf_minimum=1200, cf_maximum=1800)
    assert [p['id'] for p in pool] == [PID]
    assert pool[0]['topic_evidence']['source'] == 'ai_method_feasibility'
    assert not matches_topic(pool[0], TOPICS[TOPIC])  # still no official precise tag
    # No official tag, plan, selection report or completion record is invented.
    with empty.db.connect() as conn:
        stored = json.loads(conn.execute('SELECT data FROM problem_catalog WHERE id=?', (PID,)).fetchone()[0])
    assert stored['tags'] == ['dp'] and count(empty.db, 'problem_catalog') == 1
    assert count(empty.db, 'ai_selection_reports') == 0 and count(empty.db, 'ai_reports') == 0
    summary = empty.service.summary(TOPIC)
    assert summary['checked_count'] == 1 and summary['accepted_count'] == 1
    assert summary['rejected_count'] == 0 and summary['uncertain_count'] == 0
    assert summary['attempted_count'] == 1 and summary['incomplete'] is False
    assert '未完成的读取不作为 AI 不确定结论' in summary['notice']


def test_no_and_uncertain_conclusions_never_enter_the_pool(empty):
    second = 'codeforces:100-B'
    seed(empty.db, problem(), problem(second))
    empty.ai.complete.side_effect = answers({PID: NO, second: UNSURE})
    result = run(empty.service, 'api', 'm')
    assert result['checked_count'] == 2 and result['accepted_count'] == 0
    assert result['rejected_count'] == 1 and result['uncertain_count'] == 1
    assert {row['decision'] for row in rows(empty.db)} == {'no', 'uncertain'}
    assert empty.service.approved_map(TOPIC) == {}
    training = Training(empty.db, empty.ai, accounts())
    training.topic_feasibility = empty.service
    assert training._pool(TOPICS[TOPIC], cf_minimum=1200, cf_maximum=1800) == []
    assert empty.service.summary(TOPIC)['accepted_count'] == 0


def test_negative_cache_avoids_a_second_paid_call_for_the_same_model_only(empty):
    seed(empty.db, problem())
    empty.ai.complete.side_effect = answers(NO)
    first = run(empty.service, 'api', 'model-a')
    assert first['rejected_count'] == 1 and empty.ai.complete.await_count == 1
    again = run(empty.service, 'api', 'model-a')
    assert again['cached_count'] == 1 and again['checked_count'] == 0
    assert again['attempted_count'] == 0
    assert empty.ai.complete.await_count == 1  # Negative evidence is reused, not re-billed.
    other = run(empty.service, 'api', 'model-b')
    assert empty.ai.complete.await_count == 2  # A different model is a different judgement.
    assert other['cached_count'] == 0 and other['rejected_count'] == 1
    assert {row['model'] for row in rows(empty.db)} == {'model-a', 'model-b'}
    back = run(empty.service, 'api', 'model-a')
    assert back['cached_count'] == 1 and back['attempted_count'] == 0
    assert empty.ai.complete.await_count == 2  # Switching back also reuses its cache.


def test_accepted_evidence_is_not_rebilled_for_a_different_model(empty):
    seed(empty.db, problem())
    run(empty.service, 'api', 'model-a')
    assert set(empty.service.approved_map(TOPIC)) == {PID}
    again = run(empty.service, 'api', 'model-b')
    assert again['cached_count'] == 1 and again['attempted_count'] == 0
    assert empty.ai.complete.await_count == 1
    # The stored model stays the one that actually judged the statement.
    assert empty.service.approved_map(TOPIC)[PID]['model'] == 'model-a'


def test_changed_official_statement_forces_a_new_paid_conclusion(empty):
    seed(empty.db, problem())
    run(empty.service, 'api', 'm')
    first = rows(empty.db)[0]['statement_hash']
    assert set(empty.service.approved_map(TOPIC)) == {PID}
    cache_statement(empty.db, PID, sections=['Rewritten official statement: n <= 10.'])
    # Evidence is bound to the text it was read from, not only to the problem id.
    assert empty.service.approved_map(TOPIC) == {}
    training = Training(empty.db, empty.ai, accounts())
    training.topic_feasibility = empty.service
    assert training._pool(TOPICS[TOPIC], cf_minimum=1200, cf_maximum=1800) == []
    rerun = run(empty.service, 'api', 'm')
    assert rerun['attempted_count'] == 1 and rerun['accepted_count'] == 1
    assert empty.ai.complete.await_count == 2
    assert rows(empty.db)[0]['statement_hash'] not in ('', first)


@pytest.mark.parametrize('cached,patches', [
    ({'source': 'user_import'}, {}),
    ({'sections': [], 'body_html': '<div></div>'}, {}),
    ({'render_version': 1}, {}),
    ({'samples': [{'input': '8'}]}, {}),
    ({'id': 'codeforces:100-Z'}, {}),
    ({'sections': ['X' * 400]}, {'acmweb.selection_reports.MAX_STATEMENT_BYTES': 100}),
])
def test_unofficial_missing_oversize_or_wrong_statement_never_reaches_the_model(empty, monkeypatch, cached, patches):
    seed(empty.db, problem())
    cache_statement(empty.db, PID, **cached)
    # Refreshing the official page is allowed to fail; it must not send anything.
    empty.service.wb.statement = lambda pid: {'problem_id': pid}
    for target, value in patches.items():
        monkeypatch.setattr(target, value)
    result = run(empty.service, 'api', 'm')
    assert empty.ai.complete.await_count == 0
    assert result['not_checked_count'] == 1 and result['uncertain_count'] == 0
    assert result['accepted_count'] == 0 and rows(empty.db) == []
    assert empty.service.active_ai is False


@pytest.mark.parametrize('change', ['statement', 'ac', 'battle'])
def test_changed_statement_or_eligibility_mid_flight_is_not_saved(empty, change):
    seed(empty.db, problem())
    cache_statement(empty.db)

    async def drifted(task, provider, model, payload, schema):
        if change == 'statement':
            cache_statement(empty.db, PID, sections=['Constraints changed during the check: n <= 100000.'])
        elif change == 'ac':
            mark_ac(empty.db, PID)
        else:
            empty.db.set('battle_problem_ids', [PID])
        return AIResult(reply(PID, TOPIC), {})

    empty.ai.complete.side_effect = drifted
    result = run(empty.service, 'api', 'm')
    assert result['checked_count'] == 0 and result['accepted_count'] == 0
    assert result['uncertain_count'] == 0  # Not "AI uncertain": no conclusion was accepted.
    assert result['not_checked_count'] == 1 and result['incomplete'] is True
    assert result['items'][0]['decision'] == 'not_checked'
    assert '未收录为方法可行' in result['items'][0]['message']
    assert rows(empty.db) == [] and empty.service.approved_map(TOPIC) == {}
    assert empty.ai.complete.await_count == 1


def test_switched_default_ai_configuration_mid_flight_is_not_saved(tmp_path):
    ai = AIProvider(Database(tmp_path / 'user.sqlite3'), Settings(data_dir=tmp_path), None)
    empty = envelope(tmp_path, ai=ai)
    empty.db.set('ai', {'model': 'saved-model', 'channel': 'api'})
    seed(empty.db, problem())

    async def reconfigured(task, provider, model, payload, schema):
        empty.db.set('ai', {'model': 'switched-model', 'channel': 'api'})
        return AIResult(reply(payload['problem_id'], payload['topic_id']), {})

    ai.complete = reconfigured
    result = run(empty.service, 'api', 'saved-model')
    assert result['accepted_count'] == 0 and result['not_checked_count'] == 1
    assert rows(empty.db) == [] and empty.service.approved_map(TOPIC) == {}
    # Control: the identical flow is accepted while the saved configuration holds.
    empty.db.set('ai', {'model': 'saved-model', 'channel': 'api'})
    ai.complete = answers()
    accepted = run(empty.service, 'api', 'saved-model')
    assert accepted['accepted_count'] == 1 and len(rows(empty.db)) == 1


@pytest.mark.parametrize('invalid', [
    {'data': {'problem_id': 'codeforces:999-Z'}},
    {'data': {'topic_id': 'luogu-42'}},
    {'data': {'decision': 'maybe'}},
    {'data': {'evidence': []}},
    {'data': {'complexity': '未知，无法确认上界。'}},
    {'data': {'evidence': ['可以直接合并区间。', 'int main(){ return 0; }']}},
    {'provenance': {'model': 'another-model'}},
    {'provenance': {'channel': 'codex'}},
])
def test_wrong_id_unsupported_schema_program_or_model_provenance_is_not_saved(empty, invalid):
    seed(empty.db, problem())
    fields = dict(invalid.get('data', {}))

    async def wrong(task, provider, model, payload, schema):
        return AIResult(reply(payload['problem_id'], TOPIC, **fields), invalid.get('provenance', {}))

    empty.ai.complete.side_effect = wrong
    result = run(empty.service, 'api', 'synthetic-model')
    assert rows(empty.db) == [] and result['accepted_count'] == 0
    assert result['uncertain_count'] == 0 and result['not_checked_count'] == 1
    assert empty.ai.complete.await_count == 1  # No silent retry of a paid request.


def test_hard_date_rating_ac_avoid_battle_account_and_exclusion_filters(empty):
    # Both tags are avoided for the AVOID problem only, so `dp` still reaches
    # the parent-topic predicate for every other candidate.
    old = 'codeforces:100-OLD'
    avoided = 'codeforces:100-AVOID'
    attempted = 'codeforces:100-AC'
    battle = 'codeforces:100-BATTLE'
    too_hard = 'codeforces:100-HARD'
    excluded = 'codeforces:100-EXCLUDED'
    atcoder = 'atcoder:abc100_a'
    seed(empty.db, problem(tags=('dp', 'implementation')),
         problem(old, tags=('dp', 'implementation'), published='2020-01-01T00:00:00+00:00'),
         problem(avoided, tags=('dp', 'games')),
         problem(attempted, tags=('dp', 'implementation')),
         problem(battle, tags=('dp', 'implementation')),
         problem(too_hard, tags=('dp', 'implementation'), difficulty=3000),
         problem(excluded, tags=('dp', 'implementation')),
         problem(atcoder, tags=('dp', 'implementation'), platform='atcoder'))
    mark_ac(empty.db, attempted)
    empty.db.set('battle_problem_ids', [battle])
    empty.db.set('training', {'minimum': 800, 'maximum': 3500, 'published_after': '2024-01-01',
                             'avoid_tags': ['dp', 'games']})
    result = run(empty.service, 'api', 'm', 1200, 1800, excluded_ids=(excluded,))
    assert pids_sent(empty.ai) == {PID}
    assert empty.fetched == [PID]
    assert result['attempted_count'] == 1 and result['accepted_count'] == 1
    assert {row['problem_id'] for row in rows(empty.db)} == {PID}


def test_batch_reads_at_most_six_single_statements(empty):
    extra = ['codeforces:200-%s' % chr(ord('A') + index) for index in range(7)]
    seed(empty.db, problem(), *[problem(pid, difficulty=1400 + index) for index, pid in enumerate(extra)])
    result = run(empty.service, 'api', 'm')
    assert len(result['items']) == 6 and result['attempted_count'] == 6
    assert empty.ai.complete.await_count == 6
    assert len(pids_sent(empty.ai)) == 6
    # Each paid request carries only its own problem's official statement.
    assert all(call.args[3]['statement']['id'] == call.args[3]['problem_id']
               for call in empty.ai.complete.call_args_list)
    assert result['not_checked_count'] == 0 and result['timed_out'] is False


def test_exhausted_batch_budget_is_not_checked_and_never_ai_uncertain(empty, monkeypatch):
    seed(empty.db, problem(), problem('codeforces:100-B'), problem('codeforces:100-C'))
    # A batch budget of 10s cannot cover a single 30s minimum reading window.
    monkeypatch.setattr('acmweb.topic_feasibility.MAX_CHECK_SECONDS', 10)
    monkeypatch.setattr('acmweb.topic_feasibility.MIN_PROBLEM_SECONDS', 30)
    result = run(empty.service, 'api', 'm')
    assert result['timed_out'] is True and result['incomplete'] is True
    assert result['not_checked_count'] == 3 and result['uncertain_count'] == 0
    assert result['checked_count'] == 0 and empty.ai.complete.await_count == 0
    assert rows(empty.db) == []
    assert '不会自动重试收费请求' in empty.service.summary(TOPIC)['notice']


def test_slow_single_problem_times_out_as_not_checked_without_retry(empty, monkeypatch):
    seed(empty.db, problem())
    monkeypatch.setattr('acmweb.topic_feasibility.MAX_PROBLEM_SECONDS', 0.02)
    monkeypatch.setattr('acmweb.topic_feasibility.MIN_PROBLEM_SECONDS', 0)

    async def slow(task, provider, model, payload, schema):
        await asyncio.sleep(0.5)
        return AIResult(reply(payload['problem_id'], TOPIC), {})

    empty.ai.complete.side_effect = slow
    result = run(empty.service, 'api', 'm')
    assert result['not_checked_count'] == 1 and result['uncertain_count'] == 0
    assert result['items'][0]['decision'] == 'not_checked'
    assert rows(empty.db) == [] and empty.ai.complete.await_count == 1
    assert empty.service.active_ai is False


def test_unknown_topic_and_no_verified_platform_are_refused_before_ai(empty):
    seed(empty.db, problem())
    with pytest.raises(HTTPException) as unknown:
        asyncio.run(empty.service.check('missing-topic', 'api', 'm'))
    assert unknown.value.status_code == 422 and empty.ai.complete.await_count == 0
    unverified = envelope(empty.db.path.parent, 'unverified.sqlite3', checker=no_accounts())
    seed(unverified.db, problem())
    with pytest.raises(AIError, match='已验证登录'):
        run(unverified.service, 'api', 'm')
    assert unverified.ai.complete.await_count == 0 and rows(unverified.db) == []
    assert unverified.fetched == [] and unverified.service.active_ai is False


@pytest.mark.parametrize('provider,model,bounds,minimum,maximum,error', [
    ('openai', 'm', None, None, None, AIError),
    ('api', '', None, None, None, AIError),
    ('api', '   ', None, None, None, AIError),
    ('api', 'x' * 151, None, None, None, AIError),
    ('api', None, None, None, None, AIError),
    ('api', 'm', {'codeforces': {'minimum': '1200'}}, None, None, HTTPException),
    ('api', 'm', {'codeforces': {'min': 1200}}, None, None, HTTPException),
    ('api', 'm', {'codeforces': 1200}, None, None, HTTPException),
    ('api', 'm', ['codeforces'], None, None, HTTPException),
    ('api', 'm', None, 2000, 1200, HTTPException),
])
def test_invalid_model_ids_or_difficulty_bounds_never_reach_the_model(
        empty, provider, model, bounds, minimum, maximum, error):
    seed(empty.db, problem())
    with pytest.raises(error):
        run(empty.service, provider, model, minimum, maximum, difficulty_bounds=bounds)
    assert empty.ai.complete.await_count == 0 and empty.fetched == []
    assert rows(empty.db) == [] and empty.service.active_ai is False


def test_duplicate_click_is_locked_and_the_flag_is_released(empty):
    seed(empty.db, problem())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def pending(task, provider, model, payload, schema):
            entered.set()
            await release.wait()
            return AIResult(reply(payload['problem_id'], TOPIC), {})

        empty.ai.complete.side_effect = pending
        first = asyncio.create_task(empty.service.check(TOPIC, 'api', 'm'))
        await entered.wait()
        assert empty.service.active_ai is True
        with pytest.raises(AIError, match='正在进行'):
            await empty.service.check(TOPIC, 'api', 'm')
        assert empty.ai.complete.await_count == 1
        release.set()
        return await first

    result = asyncio.run(scenario())
    assert result['accepted_count'] == 1 and empty.service.active_ai is False


def test_failed_call_is_not_checked_and_never_retried(tmp_path):
    empty = envelope(tmp_path, 'failure.sqlite3')
    seed(empty.db, problem())
    empty.ai.complete.side_effect = AIError('模型暂时不可用')
    result = run(empty.service, 'api', 'm')
    assert result['not_checked_count'] == 1 and result['uncertain_count'] == 0
    assert result['accepted_count'] == 0 and empty.ai.complete.await_count == 1
    assert rows(empty.db) == [] and empty.service.active_ai is False


def test_forged_rows_and_catalog_approval_flags_cannot_grant_pool_access(empty):
    seed(empty.db, problem())
    run(empty.service, 'api', 'm')
    real = rows(empty.db)[0]
    spoof = problem('codeforces:100-SPOOF', tags=('implementation',))
    spoof.update({'approved': True, 'matched': True, 'decision': 'yes', 'source': 'ai_method_feasibility',
                  'topic_id': TOPIC})
    seed(empty.db, spoof)
    forged = []
    for pid, columns in [
        ('codeforces:100-F1', {'decision': 'yes', 'data': json.dumps({**json.loads(real['data']), 'decision': 'no'})}),
        ('codeforces:100-F2', {'provider': 'unknown-channel'}),
        ('codeforces:100-F3', {'created_at': int(time.time()) + 600}),
        ('codeforces:100-F4', {'data': json.dumps({**json.loads(real['data']), 'problem_id': 'codeforces:100-F9'})}),
        ('codeforces:100-F5', {'statement_hash': hashlib.sha256(b'other statement').hexdigest()}),
        ('codeforces:100-F6', {'method_hash': 'stale'}),
        ('codeforces:100-F7', {'prompt_hash': 'stale'}),
    ]:
        forged.append({**real, 'problem_id': pid, **columns})
    with empty.db.connect() as conn:
        conn.executemany('INSERT OR REPLACE INTO topic_method_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
                         [(row['problem_id'], TOPIC, row['provider'], row['model'], row['statement_hash'],
                           row['method_hash'], row['prompt_hash'], row['decision'], row['data'], row['created_at'])
                          for row in forged])
    assert set(empty.service.approved_map(TOPIC)) == {PID}
    training = Training(empty.db, empty.ai, accounts())
    training.topic_feasibility = empty.service
    assert [p['id'] for p in training._pool(TOPICS[TOPIC], cf_minimum=1200, cf_maximum=1800)] == [PID]


def test_method_and_prompt_hash_changes_invalidate_stored_evidence(empty, monkeypatch):
    seed(empty.db, problem())
    run(empty.service, 'api', 'm')
    assert set(empty.service.approved_map(TOPIC)) == {PID}
    assert empty.service.summary(TOPIC)['accepted_count'] == 1
    monkeypatch.setitem(TOPICS, TOPIC, {**TOPICS[TOPIC], 'description': 'changed method material'})
    assert empty.service.approved_map(TOPIC) == {} and empty.service.summary(TOPIC) == {}
    monkeypatch.undo()
    assert set(empty.service.approved_map(TOPIC)) == {PID}
    monkeypatch.setattr('acmweb.topic_feasibility.PROMPT_VERSION', 'synthetic-next-version')
    assert empty.service.approved_map(TOPIC) == {} and empty.service.summary(TOPIC) == {}


def test_coverage_summary_get_paths_and_user_databases_are_isolated(empty, tmp_path):
    seed(empty.db, problem())
    run(empty.service, 'api', 'm')
    assert empty.ai.complete.await_count == 1
    training = Training(empty.db, empty.ai, accounts())
    training.topic_feasibility = empty.service
    coverage = training.candidate_coverage(TOPIC, cf_minimum=1200, cf_maximum=1800)
    assert coverage['method_check_summary']['accepted_count'] == 1
    codeforces = next(row for row in coverage['platforms'] if row['platform'] == 'codeforces')
    assert codeforces['ai_eligible_count'] == 1
    assert empty.ai.complete.await_count == 1  # Coverage and summary never call the model.
    other = envelope(tmp_path, 'other-user.sqlite3')
    assert other.service.approved_map(TOPIC) == {} and other.service.summary(TOPIC) == {}
    assert other.db.get(SUMMARY_PREFIX + TOPIC) is None
    other_training = Training(other.db, other.ai, accounts())
    other_training.topic_feasibility = other.service
    assert other_training._pool(TOPICS[TOPIC], cf_minimum=1200, cf_maximum=1800) == []
    assert other.ai.complete.await_count == 0


@pytest.fixture
def empty(tmp_path):
    return envelope(tmp_path)
