from decimal import Decimal, ROUND_HALF_UP

import pytest

from acmhermes.base_filter import score
from acmhermes.display_difficulty import display_estimate


def cf_problem(**changes):
    stats = dict(source='codeforces.contest.standings', scope='contest', complete=True,
                 observed_at=1700000000, participants=500, accepted_participants=80,
                 contest_id=123, problem_index='A', participant_unit='person')
    row = {'id': 'codeforces:123-A', 'platform': 'codeforces', 'contest_id': 123,
           'difficulty': 1900, 'contest_statistics': {**stats, **changes}}
    row['score_evidence'] = score(row, 1800, 1800)
    return row


def test_published_components_reproduce_final_score_without_counting_prefilter_again():
    problem = cf_problem()
    result = display_estimate(problem, {'rating': 1800.5})
    official, empirical = 1900, problem['score_evidence']['empirical_rating']
    assert [part['rating'] for part in result['components']] == [1800.5, empirical, official]
    assert result['components'][1]['source'] == problem['score_evidence']['evidence']['source']
    expected = (Decimal('1800.5') * 100 + Decimal(str(empirical)) * 55 + official * 45) / 200
    assert result['rating'] == int(expected.to_integral_value(rounding=ROUND_HALF_UP))
    assert result['weight_sum'] == 200
    assert str(empirical) in result['display_text']


@pytest.mark.parametrize('changes', [{'complete': False}, {'participants': 99}, {'contest_id': 124},
                                     {'problem_index': 'B'}, {'participant_unit': 'unspecified'}])
def test_unqualified_statistics_have_no_weight(changes):
    result = display_estimate(cf_problem(**changes), {'rating': 1800})
    assert result['rating'] == 1831 and result['weight_sum'] == 145
    assert [c['key'] for c in result['components']] == ['final_ai_report', 'cf_official']


@pytest.mark.parametrize('platform', ['atcoder', 'luogu', 'nowcoder', 'codeforces'])
def test_low_confidence_statistics_only_use_final_ai_with_half_up(platform):
    row = {'id': platform + ':test', 'platform': platform, 'score_evidence':
           {'confidence': 'low', 'estimated_rating': 1900, 'empirical_rating': 1900}}
    result = display_estimate(row, {'rating': 1800.5})
    assert result['rating'] == 1801 and result['weight_sum'] == 100
