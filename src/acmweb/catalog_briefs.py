"""Small, provenance-labelled public aggregates, never per-user submissions.

Counts of distinct successful users and counts of submissions have different
populations. A missing denominator stays unknown; cumulative solved counts must
never become a contest acceptance rate or an invented submission count.
"""
import time

CF_STATISTICS = 'https://codeforces.com/api/problemset.problems'
LUOGU_STATISTICS = 'https://www.luogu.com.cn/problem/list'
NOWCODER_STATISTICS = 'https://ac.nowcoder.com/acm/problem/list'
SOURCES = {CF_STATISTICS, LUOGU_STATISTICS, NOWCODER_STATISTICS}


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def brief_statistics(*, source, scope, submitted_count=None, accepted_count=None,
                     solved_count=None, observed_at=None):
    """Retain only evidenced fields; rates are fractions, not percentages.

    ``all_time_users`` supports solved_count only. ``all_time_submissions``
    supports accepted/submitted counts and a rate only when both are valid.
    Never combine snapshots to synthesize a denominator for a new numerator.
    """
    if source not in SOURCES or scope not in {'all_time_users', 'all_time_submissions'}:
        return None
    counts = {}
    if scope == 'all_time_users':
        solved = _count(solved_count)
        if solved is not None:
            counts['solved_count'] = solved
    else:
        submitted, accepted = _count(submitted_count), _count(accepted_count)
        if submitted is not None:
            counts['submitted_count'] = submitted
        if accepted is not None and (submitted is None or accepted <= submitted):
            counts['accepted_count'] = accepted
        if submitted and accepted is not None and accepted <= submitted:
            counts['acceptance_rate'] = accepted / submitted
    if not counts:
        return None
    stamp = _count(observed_at) if observed_at is not None else int(time.time())
    if stamp is None or stamp > time.time() + 300:
        return None
    return {**counts, 'scope': scope, 'source': source, 'observed_at': stamp}
