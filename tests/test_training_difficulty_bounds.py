"""Local difficulty/identity regressions; no real model or OJ acceptance."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from acmweb import catalog
from acmweb.codex_bridge import AIError
from acmweb.difficulty_estimates import ATCODER_MODELS, ATCODER_POINTS, LUOGU_LEVELS, estimate, overlaps
from acmweb.training import Generate
from test_training import training


def test_luogu_heuristic_is_wide_ordered_explicitly_unofficial_and_traceable():
    beginner = estimate({'platform': 'luogu', 'native_difficulty': 1})
    advanced = estimate({'platform': 'luogu', 'native_difficulty': 7})
    assert beginner['kind'] == advanced['kind'] == 'estimate'
    assert beginner['confidence'] == 'low'
    assert beginner['maximum'] < advanced['minimum']
    assert '不是官方等价' in advanced['notice'] and '启发式' in advanced['notice']
    assert advanced['source_urls'] == [LUOGU_LEVELS]
    assert overlaps(beginner, 1100, 1300) and not overlaps(beginner, 1500, 1800)
    assert estimate({'platform': 'luogu', 'native_difficulty': 0})['kind'] == 'unknown'
    top = estimate({'platform': 'luogu', 'native_difficulty': 8})
    assert top['native_label'] == 'NOI/NOI+/CTS' and top['maximum'] == 3500
    assert estimate({'platform': 'luogu', 'native_difficulty': 9})['kind'] == 'unknown'


def test_atcoder_uses_public_statistics_quantiles_not_task_letter_or_ai_guess():
    problem = {'platform': 'atcoder', 'problem_id': 'abc400_f', 'atcoder_problem_difficulty': 2263,
        'atcoder_difficulty_percentile': .5, 'atcoder_statistics_source': ATCODER_MODELS,
        'atcoder_statistics_population': 1000}
    reference = [800] * 100 + [1600] * 100 + [2400] * 100
    result = estimate(problem, reference)
    assert (result['minimum'], result['maximum']) == (1200, 2000)
    assert result['kind'] == 'estimate' and result['reference_count'] == 300
    assert ATCODER_MODELS in result['source_urls'] and '不是共同选手标定' in result['notice']
    assert estimate({**problem, 'atcoder_statistics_source': 'unverified-ai'}, reference)['kind'] == 'unknown'
    assert estimate(problem, [800])['kind'] == 'unknown'
    assert estimate({'platform': 'atcoder', 'problem_id': 'abc999_a'})['kind'] == 'unknown'
    assert estimate({'platform': 'nowcoder', 'native_difficulty': 1600})['kind'] == 'unknown'


def test_abc_points_fallback_keeps_source_and_large_uncertainty_band():
    result = estimate({'platform': 'atcoder', 'problem_id': 'abc400_f',
        'atcoder_points': 550, 'atcoder_points_source': ATCODER_POINTS})
    assert result['method'] == 'abc_public_points_heuristic_v1'
    assert result['maximum'] - result['minimum'] >= 800
    assert '分值不等于 rating' in result['notice']
    assert estimate({'platform': 'atcoder', 'problem_id': 'arc100_a',
        'atcoder_points': 550, 'atcoder_points_source': ATCODER_POINTS})['kind'] == 'unknown'


def test_statistics_enrichment_drops_private_aggregate_fields_and_experimental_models():
    rows = [catalog._problem('atcoder', 'abc400_a', 'Task'), catalog._problem('atcoder', 'abc400_b', 'Task')]
    class Public:
        def json(self, url):
            if url == ATCODER_MODELS:
                return {'abc400_a': {'difficulty': 1000, 'is_experimental': False},
                        'abc400_b': {'difficulty': 1500, 'is_experimental': True},
                        'another': {'difficulty': 0}}
            return [{'id': 'abc400_a', 'point': 300, 'first_user_id': 'PRIVATE-AGGREGATE-HANDLE'}]
    enriched = catalog._enrich_atcoder_difficulties(Public(), rows)
    assert enriched[0]['atcoder_problem_difficulty'] == 1000
    assert enriched[0]['atcoder_statistics_population'] == 2
    assert 'atcoder_problem_difficulty' not in enriched[1]
    assert 'PRIVATE-AGGREGATE-HANDLE' not in json.dumps(enriched)


def test_explicit_specialist_range_is_local_and_shared_with_reading_report(training):
    training.db.set('training', {'minimum': 2500, 'maximum': 3000})
    before = training.db.get('training')
    plan = asyncio.run(training.generate(Generate(topic_id='dsu', count=2, provider='api', model='mock',
        cf_minimum=800, cf_maximum=1200)))
    assert training.db.get('training') == before
    assert plan['rating_request'] == {'minimum': 800, 'maximum': 1200}
    assert all(overlaps(item['cf_rating_estimate'], 800, 1200) for item in plan['items'])
    initial = training.ai.complete.call_args.args[3]
    assert initial['rating_request']['scope'] == 'this_selection'
    report_items = training.selection_reports.calls[0][0]
    assert all(item['rating_request']['minimum'] == 800 for item in report_items)
    assert all(item['cf_rating_estimate']['kind'] == 'estimate' for item in report_items)


def test_legacy_shared_selector_retains_daily_native_rules(training):
    training.db.set('training', {'minimum': 2500, 'maximum': 3000})
    # No explicit range is the existing daily-specialist contract: native
    # levels remain eligible, not silently subjected to a new conversion.
    items = asyncio.run(training.select_items('dsu', 'learn', 4, 'api', 'mock'))
    assert len(items) == 4
    assert training.ai.complete.call_args.args[3]['rating_request']['scope'] == 'legacy_global_cf_only'


@pytest.mark.parametrize('values', [{'cf_minimum': 1500, 'cf_maximum': 900}, {'cf_minimum': True},
                                    {'cf_minimum': 799}, {'cf_maximum': 3501}])
def test_invalid_range_rejected_before_ai(values):
    with pytest.raises(ValidationError):
        Generate(topic_id='dsu', provider='api', model='mock', **values)


def test_real_cf_rating_evidence_only_uses_currently_bound_own_account_snapshots(training):
    training.accounts.document = lambda: {'accounts': [{'platform': 'codeforces', 'handle': 'current'}]}
    now = int(time.time())
    with training.db.connect() as db:
        db.executemany('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)', [
            ('r1', 'codeforces', 'current', now, json.dumps({'rating': 1450, 'secret': 'PRIVATE'}), '[]', 1, '[]', None),
            ('r2', 'codeforces', 'removed', now, json.dumps({'rating': 2900}), '[]', 1, '[]', None),
            ('r3', 'codeforces', 'current', now + 1000, json.dumps({'rating': 3000}), '[]', 1, '[]', None)])
    evidence = training._learner_evidence()
    assert [entry['rating'] for entry in evidence['cf_ratings']] == [1450]
    assert 'PRIVATE' not in json.dumps(evidence) and 'current' not in json.dumps(evidence)


def test_no_trustworthy_mapping_is_reported_without_calling_ai(training):
    training.accounts.credential_checks.statuses = lambda: {'items': [
        {'platform': 'nowcoder', 'status': 'authenticated', 'login_verified': True}]}
    row = catalog._problem('nowcoder', '1000', '并查集', tags=['并查集'], native_difficulty=1600)
    with training.db.connect() as db:
        db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('nowcoder', row['id'], json.dumps(row)))
    with pytest.raises(AIError, match='缺少可核查的难度映射'):
        asyncio.run(training.select_items('dsu', 'consolidate', 1, 'api', 'mock', cf_minimum=800, cf_maximum=1600))
    training.ai.complete.assert_not_called()


def test_difficulty_is_rechecked_after_report_changes_native_level(training):
    original = training.selection_reports.prepare
    async def changed(items, provider, model):
        result = await original(items, provider, model)
        with training.db.connect() as db:
            row = json.loads(db.execute("SELECT data FROM problem_catalog WHERE id='luogu:P3367'").fetchone()[0])
            row['native_difficulty'] = 7
            db.execute("UPDATE problem_catalog SET data=? WHERE id='luogu:P3367'", (json.dumps(row),))
        return result
    training.selection_reports.prepare = changed
    with pytest.raises(AIError, match='阅读报告期间已变化'):
        asyncio.run(training.generate(Generate(topic_id='dsu', count=2, provider='api', model='mock', cf_minimum=800, cf_maximum=1200)))
    assert training.plans() == {'plans': []}

