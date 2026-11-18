"""Versioned program prefilter. Population units never cross source boundaries.

These deliberately broad ceilings reject overwhelmingly easy public samples;
they are application heuristics, not measured CF equivalences. Full-statement
AI assessment remains the delivery gate. Unknown statistics stay unknown.
"""
import math
import time

from acmweb.difficulty_estimates import estimate
from .difficulty import score_candidate, selection_range
from .rating_review import metadata_matches

VERSION = 'success-sample-ceilings-2026-11-18.1'
SOURCES = {
    'codeforces': 'https://codeforces.com/api/contest.standings',
    'luogu': 'https://www.luogu.com.cn/problem/list',
    'nowcoder': 'https://ac.nowcoder.com/acm/contest/problem-list',
}


def sample(problem):
    s = problem.get('submission_statistics')
    if not isinstance(s, dict): return None
    platform = problem.get('platform')
    source = SOURCES.get(platform)
    if platform == 'atcoder':
        cid = problem.get('contest_id')
        source = f'https://atcoder.jp/contests/{cid}/standings/json' if isinstance(cid, str) else None
    if not source or s.get('source') != source: return None
    stamp = s.get('observed_at')
    if type(stamp) is not int or not 946684800 <= stamp <= time.time(): return None
    count = s.get('submitted_count')
    if type(count) is not int or count < 0: return None
    if platform == 'luogu':
        if (s.get('scope'), s.get('rate_unit'), s.get('count_kind')) != ('all_time', 'submission', 'all_submissions'): return None
        numerator, denominator, minimum = s.get('accepted_count'), count, 1000
    else:
        kind = 'scoring_attempts_until_first_solve' if platform == 'codeforces' else 'all_contest_submissions'
        if (s.get('scope'), s.get('rate_unit'), s.get('count_kind')) != ('contest', 'participant', kind): return None
        if s.get('contest_id') != problem.get('contest_id') and platform != 'nowcoder': return None
        numerator, denominator, minimum = s.get('accepted_participants'), s.get('attempted_participants'), 100
        if type(denominator) is int and denominator > count: return None
    if not (type(numerator) is int and type(denominator) is int and 0 <= numerator <= denominator and denominator >= minimum): return None
    rate = s.get('acceptance_rate')
    if type(rate) not in (float, int) or not math.isfinite(rate) or abs(rate - numerator / denominator) > 1e-9: return None
    return {'rate':rate, 'population':denominator, 'scope':s['scope'], 'rate_unit':s['rate_unit'],
            'count_kind':s['count_kind'], 'source':source, 'observed_at':stamp}


def score(problem, low, high, reference=()):
    evidence = score_candidate(problem, low, high) if problem.get('platform') == 'codeforces' else estimate(problem, reference)
    evidence = dict(evidence)
    stats = sample(problem)
    base = {'version':VERSION, 'statistics_status':'usable' if stats else 'unknown_or_insufficient',
            'statistics':stats, 'rating_ceiling':None,
            'notice':'程序初筛启发式，非官方难度；最终须完整读题并通过AI独立评分。未知统计不补零，仅使用注明来源的既有难度。'}
    if stats:
        thresholds = ((.98,1200),(.95,1400),(.90,1600)) if stats['rate_unit'] == 'participant' else ((.95,1600),(.90,1800))
        base['rating_ceiling'] = next((ceiling for rate,ceiling in thresholds if stats['rate'] >= rate), None)
        # A platform lacking native ratings can still expose a usable, explicitly
        # low-confidence sample estimate. No integer-tag guessing is involved.
        if evidence.get('kind') == 'unknown':
            ratio = (stats['rate'] * stats['population'] + 1) / (stats['population'] + 2)
            center = round(max(800, min(3500, 1200 + 400 * math.log2((1-ratio)/ratio))), 2)
            evidence.update(kind='estimate',minimum=center,maximum=center,estimated_rating=center,
                method='source_scoped_success_heuristic_v1',confidence='low',source_urls=[stats['source']],
                notice='同来源样本通过率的初筛启发式，未按共同选手标定；须AI完整读题评分，不是官方等价。')
    eligible = evidence.get('eligible', metadata_matches(evidence,low,high))
    if base['rating_ceiling'] is not None and selection_range(low,high)[0] > base['rating_ceiling']:
        eligible = False
        base['decision'] = 'sample_too_easy'
    else:
        base['decision'] = 'eligible' if eligible else 'metadata_out_of_range'
    evidence.update(eligible=eligible, base_filter=base)
    return evidence
