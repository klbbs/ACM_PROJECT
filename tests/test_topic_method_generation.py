"""Explicit specialist generation uses method evidence, then final reading.

These local AI replies and official statements are synthetic developer fixtures.
"""
import asyncio
import pytest

from acmweb.ai import AIResult
from acmweb.codex_bridge import AIError
from acmweb.training import Generate, Training
from acmweb.selection_reports import SelectionReports
from acmweb.topic_feasibility import DDL, TopicFeasibility
from acmweb.training_topics import TOPICS
from test_selection_reports import report
from test_topic_feasibility import envelope, seed, problem, reply, TOPIC, PID, rows


def prepare(tmp_path):
    fixture = envelope(tmp_path)
    seed(fixture.db, problem())
    async def complete(task, provider, model, payload, schema):
        if task == 'topic_method_feasibility':
            return AIResult(reply(payload['problem_id'], TOPIC), {'model': model})
        if task == 'specialist_selection':
            assert payload['candidates'][0]['topic_evidence']['source'] == 'ai_method_feasibility'
            return AIResult({'selected_problems': [{'id': PID, 'reason': '方法适用性已核查', 'learning_level': 1}]}, {})
        assert task == 'selection_read_report'
        return AIResult(report(PID), {'model': model})
    fixture.ai.complete.side_effect = complete
    training = Training(fixture.db, fixture.ai, fixture.service.accounts)
    training.topic_feasibility = fixture.service
    training.selection_reports = SelectionReports(fixture.db, fixture.ai, fixture.service.wb)
    return fixture, training


def test_generation_reads_unknown_method_then_requires_final_read_report(tmp_path):
    fixture, training = prepare(tmp_path)
    request = Generate(topic_id=TOPIC, mode='consolidate', count=1, provider='api', model='synthetic-model')
    plan = asyncio.run(training.generate(request))
    assert [c.args[0] for c in fixture.ai.complete.call_args_list] == [
        'topic_method_feasibility', 'specialist_selection', 'selection_read_report']
    assert plan['items'][0]['problem_id'] == PID and plan['items'][0]['ai_report_id']
    assert not plan['items'][0]['completed']
    assert len(rows(fixture.db)) == 1 and not training.active_ai
    # Subsequent explicit generation reuses valid method evidence; final fit
    # reading remains a separate requirement (a cached report may be reused).
    fixture.ai.complete.reset_mock()
    asyncio.run(training.generate(request))
    assert 'topic_method_feasibility' not in [c.args[0] for c in fixture.ai.complete.call_args_list]


def test_missing_required_template_does_not_spend_ai_to_invent_one(tmp_path):
    fixture, training = prepare(tmp_path)
    with pytest.raises(AIError, match='模板题'):
        asyncio.run(training.generate(Generate(topic_id=TOPIC, mode='learn', count=1, provider='api', model='synthetic-model')))
    fixture.ai.complete.assert_not_awaited()
    assert rows(fixture.db) == []


def test_failed_supplement_cannot_replace_saved_plan_or_fabricate_candidates(tmp_path):
    fixture, training = prepare(tmp_path)
    request = Generate(topic_id=TOPIC, mode='consolidate', count=1, provider='api', model='synthetic-model')
    saved = asyncio.run(training.generate(request))
    # New method policy invalidates evidence, and a failed new read must leave
    # the existing plan unchanged instead of using ordinary coarse tags.
    with fixture.db.connect() as connection:
        connection.execute("UPDATE topic_method_evidence SET prompt_hash='stale'")
    fixture.ai.complete.side_effect = AIError('synthetic provider unavailable')
    with pytest.raises(AIError, match='不足'):
        asyncio.run(training.generate(request))
    assert training.get_plan(saved['id']) == saved and not training.active_ai


def test_coarse_topic_relevance_is_filtered_before_shortlist_truncation(tmp_path):
    fixture = envelope(tmp_path)
    related = [f'codeforces:100-{index}' for index in 'ABCDEF']
    unrelated = [f'codeforces:200-A{index}' for index in range(200)]
    seed(fixture.db, *[problem(pid) for pid in related],
         *[problem(pid, tags=('math',)) for pid in unrelated])
    result = asyncio.run(fixture.service.check(TOPIC, 'api', 'synthetic-model'))
    assert result['checked_count'] == 6
    sent = {call.args[3]['problem_id'] for call in fixture.ai.complete.call_args_list}
    assert sent == set(related)
    # The broader tag is only a discovery hint: it never becomes an official
    # fine tag, and approval still depended on each individual statement.
    assert all(call.args[3]['statement']['id'] == call.args[3]['problem_id']
               for call in fixture.ai.complete.call_args_list)


def test_early_development_cache_migrates_without_dropping_evidence(tmp_path):
    fixture = envelope(tmp_path)
    seed(fixture.db, problem())
    asyncio.run(fixture.service.check(TOPIC, 'api', 'synthetic-model'))
    # Reconstruct the first development schema before the per-model key was
    # introduced. Existing valid evidence must survive initialization.
    with fixture.db.connect() as connection:
        connection.execute('ALTER TABLE topic_method_evidence RENAME TO temp_method_rows')
        connection.execute(DDL.replace('problem_id, topic_id, provider, model)', 'problem_id, topic_id)'))
        connection.execute('INSERT INTO topic_method_evidence SELECT * FROM temp_method_rows')
        connection.execute('DROP TABLE temp_method_rows')
    restored = TopicFeasibility(fixture.db, fixture.service.accounts, fixture.service.wb, fixture.ai)
    assert set(restored.approved_map(TOPIC)) == {PID}
    with fixture.db.connect() as connection:
        assert sum(bool(row['pk']) for row in connection.execute('PRAGMA table_info(topic_method_evidence)')) == 4
