"""Synthetic aggregate evidence tests; these are not live OJ acceptance."""
from copy import deepcopy

import pytest

from acmhermes.difficulty import GYM_PRIORITY_BONUS, score_candidate

NOW = 1793606400


def candidate(*, rating=1800, accepted=500, population=1000, gym=False):
    return {'platform': 'codeforces', 'id': 'codeforces:123-A', 'difficulty': rating,
            'is_gym': gym, 'published_at': NOW - 365 * 86400,
            'contest_statistics': {'participants': population,
                'accepted_participants': accepted, 'scope': 'contest',
                'source': 'codeforces.contest.standings', 'observed_at': NOW - 3600}}


def score(problem, low=1800, high=1800):
    return score_candidate(problem, low, high, now=NOW)


def test_high_success_official_1800_is_excluded_instead_of_matching_old_rating():
    result = score(candidate(accepted=850))
    assert result['estimated_rating'] < 1800
    assert result['minimum'] == result['maximum'] == result['estimated_rating']
    assert not result['eligible']
    assert result['empirical_weight'] == .55
    assert result['official_rating'] == 1800
    assert result['empirical_rating'] == 800
    assert result['kind'] == 'estimate'
    assert result['evidence']['pass_rate'] == .85


def test_empirical_weight_slightly_outweighs_official_and_centre_rounds_to_hundred():
    result = score(candidate(accepted=300), 1700, 1900)
    assert result['empirical_rating'] == 1900
    assert result['estimated_rating'] == 1900
    assert result['eligible']
    assert result['confidence'] == 'medium'


def test_gym_requires_evidence_and_gets_bonus_without_changing_rating():
    official = score(candidate(accepted=300), 1900, 1900)
    gym = score(candidate(accepted=300, gym=True), 1900, 1900)
    assert gym['estimated_rating'] == official['estimated_rating']
    assert gym['priority_score'] == pytest.approx(official['priority_score'] + GYM_PRIORITY_BONUS)
    assert gym['gym_bonus'] == GYM_PRIORITY_BONUS
    assert not score(candidate(accepted=850, gym=True))['eligible']
    assert score(candidate(accepted=850, gym=True))['gym_bonus'] == 0


def test_unrated_gym_estimated_from_sufficient_contest_data():
    result = score(candidate(rating=None, accepted=300, gym=True), 1900, 1900)
    assert result['eligible'] and result['estimated_rating'] == 1900
    assert result['confidence'] == 'low'
    assert result['empirical_weight'] == 1
    assert '不是官方定级' in result['notice']


@pytest.mark.parametrize('replacement', [None, {}, {'participants': 1000},
    {'participants': 1000, 'accepted_participants': 500}])
def test_unknown_gym_without_verified_statistics_is_not_invented(replacement):
    problem = candidate(rating=None, gym=True)
    problem['contest_statistics'] = replacement
    result = score(problem)
    assert result['estimated_rating'] is None
    assert result['kind'] == 'unknown' and not result['eligible']


@pytest.mark.parametrize(('field', 'value'), [
    ('participants', 99), ('participants', True), ('participants', 0),
    ('participants', '1000'), ('accepted_participants', 1001),
    ('accepted_participants', -1), ('accepted_participants', True),
    ('accepted_participants', float('nan')),
    ('observed_at', NOW + 1), ('observed_at', None),
    ('scope', 'lifetime'), ('scope', None),
    ('source', 'https://evil.example/standings'),
    ('source', 'https://codeforces.com.evil.example/standings'),
    ('source', 'http://codeforces.com/api/contest.standings'),
    ('source', 'https://user:secret@codeforces.com/api/contest.standings'),
    ('source', 'https://codeforces.com/api/contest.standings?apiKey=private'),
])
def test_bad_insufficient_or_mixed_evidence_does_not_override_official(field, value):
    problem = candidate(accepted=850)
    problem['contest_statistics'][field] = value
    result = score(problem)
    assert result['kind'] == 'official' and result['estimated_rating'] == 1800
    assert result['eligible'] and result['empirical_weight'] == 0
    assert result['empirical_rating'] is None
    assert 'secret' not in str(result)
    assert 'private' not in str(result)


def test_lifetime_solves_never_fabricate_a_contest_success_rate():
    problem = candidate(accepted=850)
    del problem['contest_statistics']
    problem['solved_count'] = 50000
    result = score(problem)
    assert result['estimated_rating'] == 1800
    assert 'pass_rate' not in result['evidence']
    assert result['evidence']['age_days'] == 365


def test_problem_age_does_not_independently_reduce_difficulty():
    older = candidate(accepted=300)
    newer = deepcopy(older)
    newer['published_at'] = NOW - 86400
    old, new = score(older, 1900, 1900), score(newer, 1900, 1900)
    assert old['estimated_rating'] == new['estimated_rating']
    assert old['priority_score'] == new['priority_score']
    assert old['evidence']['age_days'] != new['evidence']['age_days']


@pytest.mark.parametrize('source', [
    'https://board.xcpcio.com/icpc/2025/final',
    'https://rank.iccpc.cn/contest/2025',
    'https://codeforces.com/api/contest.standings',
])
def test_contest_provenance_accepts_user_requested_reference_hosts(source):
    problem = candidate(accepted=300)
    problem['contest_statistics']['source'] = source
    result = score(problem, 1900, 1900)
    assert result['eligible']
    assert source in result['source_urls']


@pytest.mark.parametrize('accepted', [0, 1000])
def test_extreme_success_rates_are_smoothed_and_bounded(accepted):
    result = score(candidate(rating=None, accepted=accepted), 800, 3500)
    assert result['eligible']
    assert 800 <= result['estimated_rating'] <= 3500


def test_submission_rate_is_not_confused_with_participant_success():
    problem = candidate(accepted=300)
    problem['contest_statistics'].update(submissions=10000, accepted_submissions=3000)
    result = score(problem, 1900, 1900)
    assert result['evidence']['pass_rate'] == .3
    assert result['evidence']['submissions'] == 10000
    assert result['estimated_rating'] == 1900


def test_safe_evidence_drops_unexpected_personal_data_and_leaves_input_unchanged():
    problem = candidate(accepted=300)
    problem['contest_statistics']['users'] = ['private user']
    problem['cookie'] = 'sensitive cookie'
    before = deepcopy(problem)
    result = score(problem)
    assert problem == before
    assert 'private user' not in str(result) and 'sensitive cookie' not in str(result)
    assert set(('kind', 'minimum', 'maximum', 'method', 'source_urls', 'notice')) <= result.keys()


@pytest.mark.parametrize(('low', 'high'), [(799, 1800), (2000, 1800), (1800, 3501), (True, 1800)])
def test_invalid_request_ranges_fail_closed(low, high):
    with pytest.raises(ValueError):
        score(candidate(), low, high)


@pytest.mark.parametrize('extra', [
    {'contest_id': 100123}, {'id': 'codeforces:100123-A'},
    {'url': 'https://codeforces.com/gym/100123/problem/A'},
])
def test_gym_metadata_identification_without_explicit_flag(extra):
    problem = candidate(accepted=300)
    problem.pop('is_gym')
    problem.update(extra)
    assert score(problem, 1900, 1900)['gym_bonus'] == GYM_PRIORITY_BONUS


def test_large_contest_id_on_other_platform_is_not_a_gym():
    problem = candidate(accepted=300)
    problem.update(platform='atcoder', contest_id=100123)
    assert score(problem, 1900, 1900)['gym_bonus'] == 0


def test_actual_unix_epoch_is_valid_for_statistics_and_publication():
    result = score(candidate(accepted=300), 1900, 1900)
    assert result['evidence']['observed_at'] == NOW - 3600
    assert result['evidence']['age_days'] == 365
    assert result['empirical_rating'] == 1900
    assert '启发式' in result['notice']


@pytest.mark.parametrize('patch', [
    {'contest_id': 456}, {'contest_id': '123'}, {'contest_id': True},
    {'problem_index': 'B'}, {'problem_index': 1}, {'complete': False},
    {'complete': 'true'}, {'observed_at': NOW - 366 * 86400},
    {'participant_unit': 'team', 'numerator_unit': 'person'},
    {'participant_unit': 'submissions', 'numerator_unit': 'submissions'},
    {'numerator_unit': 'person'}, {'attempted_participants': 200},
    {'attempted_participants': 1001}, {'attempted_participants': True},
])
def test_inconsistent_incomplete_or_unmatched_statistics_do_not_affect_rating(patch):
    problem = candidate(accepted=300)
    problem['contest_statistics'].update(patch)
    result = score(problem)
    assert result['kind'] == 'official'
    assert result['estimated_rating'] == 1800
    assert result['empirical_rating'] is None
    assert result['eligible']


def test_matched_identity_units_and_attempted_counts_are_explained():
    problem = candidate(accepted=300)
    problem['contest_statistics'].update(contest_id=123, problem_index='a',
        participant_unit='teams', numerator_unit='team', attempted_participants=700,
        complete=True)
    result = score(problem, 1900, 1900)
    assert result['eligible'] and result['empirical_rating'] == 1900
    evidence = result['evidence']
    assert evidence['participant_unit'] == 'team'
    assert evidence['attempted_participants'] == 700
    assert evidence['statistics_scope'] == 'contest'
    assert evidence['source'] == result['source_urls'][0]
    assert evidence['age_days'] == 365
    assert evidence['priority_components']['sample_support'] == .1
    assert '不保证实时' in result['notice']
    assert '缓存刷新由采集器处理' in result['notice']


def test_old_complete_contest_statistics_are_not_arbitrarily_expired():
    problem = candidate(accepted=300)
    problem['published_at'] = NOW - 1000 * 86400
    problem['contest_statistics'].update(observed_at=NOW - 999 * 86400, complete=True)
    result = score(problem, 1900, 1900)
    assert result['eligible']
    assert result['evidence']['age_days'] == 1000
    assert '历史完整比赛统计不因年龄自动失效' in result['notice']


def test_more_contest_participants_raise_only_sample_priority_not_rating():
    smaller = score(candidate(accepted=60, population=200), 1900, 1900)
    larger = score(candidate(accepted=300, population=1000), 1900, 1900)
    assert smaller['estimated_rating'] == larger['estimated_rating'] == 1900
    assert larger['priority_score'] > smaller['priority_score']
    assert larger['evidence']['priority_components']['sample_support'] > smaller['evidence']['priority_components']['sample_support']


def test_individuals_unit_from_standings_provider_is_normalized():
    problem = candidate(accepted=300)
    problem['contest_statistics'].update(participant_unit='individuals', numerator_unit='person')
    result = score(problem, 1900, 1900)
    assert result['eligible']
    assert result['evidence']['participant_unit'] == 'person'


@pytest.mark.parametrize(('gym', 'accepted', 'expected'), [(False, 850, 1300), (True, 300, 1900)])
def test_real_provider_parser_output_flows_into_difficulty_without_shape_adapter(gym, accepted, expected):
    # Real producer and consumer, synthetic standings. No model/OJ requests.
    from acmhermes.contest_evidence import _problem_rows
    contest_id = 100123 if gym else 123
    problem = {'contestId': contest_id, 'index': 'A', 'name': 'Synthetic problem'}
    if not gym:
        problem['rating'] = 1800
    rows = []
    for index in range(1000):
        members = [{'handle': f'synthetic_{index}'}]
        if gym:
            members.append({'handle': f'synthetic_teammate_{index}'})
        rows.append({'party': {'members': members, 'participantType': 'CONTESTANT'},
                     'problemResults': [{'type': 'FINAL', 'points': int(index < accepted),
                                         'rejectedAttemptCount': 1}]})
    parsed = _problem_rows({'contest': {'id': contest_id, 'name': 'Synthetic contest',
        'phase': 'FINISHED', 'type': 'ICPC' if gym else 'CF',
        'startTimeSeconds': NOW - 86400}, 'problems': [problem], 'rows': rows},
        contest_id, gym, NOW - 3600)
    result = score(parsed['problems'][0], 800, 3500)
    assert result['estimated_rating'] == expected
    assert result['empirical_rating'] is not None
    assert result['evidence']['participant_unit'] == ('team' if gym else 'person')
    assert result['evidence']['attempted_participants'] == 1000
    assert result['gym_bonus'] == (GYM_PRIORITY_BONUS if gym else 0)
    assert not gym or result['official_rating'] is None
    if not gym:
        assert not score(parsed['problems'][0])['eligible']
