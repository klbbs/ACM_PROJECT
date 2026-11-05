"""Explainable, bounded selection heuristics; never an official CF rating.

Contest-window *people/team* success is comparable within a contest. Lifetime
solved counts are exposure, not a success-rate denominator. External evidence
must first be matched to the exact contest/problem by its collector.
"""
from __future__ import annotations

import math
import re
import time
from urllib.parse import urlsplit

OFFICIAL_WEIGHT = 0.45
EMPIRICAL_WEIGHT = 0.55
GYM_PRIORITY_BONUS = 0.12
MIN_POPULATION = 100
CF_RATING_SOURCE = 'https://codeforces.com/apiHelp/objects#Problem'
TRUSTED_HOSTS = {'codeforces.com', 'board.xcpcio.com', 'rank.iccpc.cn'}
STATISTIC_SOURCES = {'codeforces.contest.standings': 'https://codeforces.com/apiHelp/methods#contest.standings'}


def _integer(value, minimum=0, maximum=10**12):
    return type(value) is int and minimum <= value <= maximum


def _source(value):
    if not isinstance(value, str):
        return None
    if value in STATISTIC_SOURCES:
        return STATISTIC_SOURCES[value]
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == 'https' and parsed.hostname in TRUSTED_HOSTS
                and not parsed.username and not parsed.password and not parsed.query
                and not parsed.fragment and parsed.port in (None, 443)):
            return value
    except ValueError:
        pass
    return None


def selection_range(minimum, maximum):
    """A single target means its neighbourhood; explicit intervals stay exact."""
    return (max(800, minimum - 100), min(3500, maximum + 100)) if minimum == maximum else (minimum, maximum)


def _rating(value):
    return max(800.0, min(3500.0, value))


def _gym(problem):
    if problem.get('is_gym') is True or '/gym/' in str(problem.get('url', '')):
        return True
    if problem.get('platform') != 'codeforces':
        return False
    contest = problem.get('contest_id')
    if _integer(contest, 100000):
        return True
    match = re.fullmatch(r'(?:codeforces:)?(\d+)-[A-Za-z]\d?',
                         str(problem.get('id', problem.get('problem_id', ''))))
    return bool(match and int(match[1]) >= 100000)



def _identity_matches(problem, stats):
    match = re.fullmatch(r'(?:codeforces:)?(\d+)-([A-Za-z]\d?)',
                         str(problem.get('id', problem.get('problem_id', ''))))
    expected_contest = problem.get('contest_id')
    expected_index = problem.get('problem_index')
    if match:
        expected_contest = expected_contest if expected_contest is not None else int(match[1])
        expected_index = expected_index if expected_index is not None else match[2]
    if 'contest_id' in stats:
        if not (_integer(stats['contest_id'], 1) and stats['contest_id'] == expected_contest):
            return False
    if 'problem_index' in stats:
        value = stats['problem_index']
        if not (isinstance(value, str) and isinstance(expected_index, str)
                and value.casefold() == expected_index.casefold()):
            return False
    return True


def _count_unit(value):
    if value is None:
        return 'unspecified'
    return {'person': 'person', 'persons': 'person', 'user': 'person',
            'users': 'person', 'individual': 'person', 'individuals': 'person', 'participants': 'person',
            'team': 'team', 'teams': 'team'}.get(value) if isinstance(value, str) else None

def score_candidate(problem, requested_min, requested_max, *, now=None):
    """Return JSON-safe score evidence, qualification and selection priority.

    ``contest_statistics`` must carry participants, accepted_participants,
    source, observed_at and scope='contest'. Optional submissions and accepted
    submissions are retained as aggregate evidence only. The population can be
    teams at an onsite contest; a single record must use the same counting unit.
    No network, database, model invocation, mutable cache or hidden randomness.
    """
    if not (_integer(requested_min, 800, 3500) and _integer(requested_max, 800, 3500)
            and requested_min <= requested_max):
        raise ValueError('score range must be ordered within 800..3500')
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError('now must be finite')
    official = problem.get('difficulty')
    official = official if _integer(official, 800, 3500) else None
    result = {'kind': 'unknown', 'minimum': None, 'maximum': None,
              'method': 'contest_success_blend_heuristic_v2', 'source_urls': [],
              'confidence': 'unknown', 'notice': '', 'eligible': False,
              'estimated_rating': None, 'official_rating': official,
              'empirical_rating': None, 'empirical_weight': 0.0,
              'priority_score': 0.0, 'gym_bonus': 0.0, 'reasons': [], 'evidence': {}}
    lower, upper = selection_range(requested_min, requested_max)
    result.update(requested_score_range=[requested_min, requested_max], effective_score_range=[lower, upper])
    reasons = result['reasons']
    published = problem.get('published_at')
    if _integer(published, 946684800) and published <= now:
        result['evidence']['age_days'] = int((now - published) / 86400)
    else:
        reasons.append('缺少有效出题时间；不根据题号或旧题累计通过人数推断难度。')
    stats = problem.get('contest_statistics')
    stats = stats if isinstance(stats, dict) else {}
    source = _source(stats.get('source'))
    observed = stats.get('observed_at')
    population, accepted = stats.get('participants'), stats.get('accepted_participants')
    valid_counts = (_integer(population, 1) and _integer(accepted)
                    and accepted <= population)
    valid_time = (_integer(observed, 946684800) and observed <= now
                  and (not _integer(published, 946684800) or observed >= published))
    valid_identity = _identity_matches(problem, stats)
    complete = stats.get('complete', True) is True
    participant_unit = _count_unit(stats.get('participant_unit'))
    numerator_unit = _count_unit(stats.get('numerator_unit'))
    valid_unit = (participant_unit is not None and numerator_unit is not None
                  and (numerator_unit == 'unspecified' or numerator_unit == participant_unit))
    attempted = stats.get('attempted_participants')
    valid_attempted = ('attempted_participants' not in stats or
                       (_integer(attempted) and valid_counts and accepted <= attempted <= population))
    valid_scope = stats.get('scope') == 'contest'
    enough = valid_counts and population >= MIN_POPULATION
    # A finite sample with zero solves is still evidence; beta smoothing avoids
    # infinite estimates. Low sample counts must never invent a Gym rating.
    empirical = None
    if (source and valid_time and valid_scope and enough and valid_identity
            and complete and valid_unit and valid_attempted):
        ratio = (accepted + 1) / (population + 2)
        empirical = _rating(1500 + 300 * math.log2((1 - ratio) / ratio))
        result['evidence'].update(participants=population, accepted_participants=accepted,
                                  pass_rate=round(accepted / population, 6),
                                  statistics_scope='contest', observed_at=observed,
                                  source=source, participant_unit=participant_unit)
        if attempted is not None:
            result['evidence']['attempted_participants'] = attempted
        for name in ('submissions', 'accepted_submissions'):
            value = stats.get(name)
            if _integer(value):
                result['evidence'][name] = value
        result['source_urls'].append(source)
        result['empirical_rating'] = round(empirical, 2)
        result['empirical_weight'] = EMPIRICAL_WEIGHT if official else 1.0
        reasons.append('使用同一比赛窗口的参赛人数与通过人数；实测统计权重 55%，官方 rating 权重 45%。'
                       if official else '无官方 rating，按比赛窗口通过比例进行初步估计；不是官方定级。')
        reasons.append('统计为采集时保存的比赛窗口快照，不保证实时；缓存刷新由采集器处理，历史完整比赛统计不因年龄自动失效。')
        reasons.append('通过比例到分数的映射是启发式，不同比赛的选手强度和团队赛环境存在差异，此估计未经共同选手标定。')
    else:
        if stats:
            if not valid_identity:
                reasons.append('统计的比赛或题号与候选题不一致，不采用此统计。')
            if not complete:
                reasons.append('统计不完整，不采用此统计。')
            if not valid_unit or not valid_attempted:
                reasons.append('统计人数计数单位或提交人数不一致，不采用此统计。')
            if not valid_time:
                reasons.append('统计观测时间无效、早于出题时间或位于未来，不采用此统计。')
            reasons.append('统计来源、时间、比赛窗口或样本量不满足要求；不将累计 AC 人数当作通过率。')
        else:
            reasons.append('缺少可核查的比赛通过率；仅保留已知官方 rating，不编造统计。')
    center = (OFFICIAL_WEIGHT * official + EMPIRICAL_WEIGHT * empirical
              if official is not None and empirical is not None
              else empirical if empirical is not None else official)
    if official is not None:
        result['source_urls'].append(CF_RATING_SOURCE)
    if center is not None:
        result.update(kind='estimate' if empirical is not None else 'official',
                      minimum=round(center, 2), maximum=round(center, 2), estimated_rating=round(center, 2),
                      confidence='medium' if empirical is not None and official is not None else
                      'low' if empirical is not None else 'official',
                      eligible=lower <= center <= upper)
        if not result['eligible']:
            reasons.append('综合难度不在请求区间，不能用原始官方 rating 重新放入候选。')
        if result['eligible']:
            # Priority is separate from rating. Gym preference never makes an
            # easy Gym task fit a harder requested score range.
            distance = abs(center - (requested_min + requested_max) / 2)
            fit = max(0.0, 1 - distance / max(100, upper - lower))
            reliability = min(1.0, population / 1000) if empirical is not None else 0
            bonus = GYM_PRIORITY_BONUS if _gym(problem) else 0.0
            result['gym_bonus'] = bonus
            result['priority_score'] = round(fit + reliability * 0.10 + bonus, 6)
            result['evidence']['priority_components'] = {
                'range_fit': round(fit, 6), 'sample_support': round(reliability * .10, 6),
                'gym_bonus': bonus}
            reasons.append('优先分由区间贴合度、参赛样本支持度和 Gym 加分组成；出题日期用于核对统计窗口与时间偏好，不把旧题自动视作更容易。')
    else:
        reasons.append('无官方 rating 且统计不足，无法确定难度，不参与本次抽题。')
    result['notice'] = ' '.join(reasons)
    return result
