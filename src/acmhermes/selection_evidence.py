"""Bounded on-demand enrichment; private credentials never become metadata."""
import asyncio
import secrets
import threading

from acmweb.catalog import _store_catalog_rows, verified_platforms
from .contest_evidence import ContestEvidenceProvider
from acmweb.codex_bridge import AIError


async def refresh_evidence(database, accounts, candidates, *, score_range=None):
    if 'codeforces' not in verified_platforms(accounts):
        return {'status': 'not_required', 'contests_checked': 0, 'gym': 'not_required'}
    stop = threading.Event()
    revision = accounts.revision() if callable(getattr(accounts, 'revision', None)) else None
    # Use only an exact verified binding's existing key, never another account.
    statuses = accounts.credential_checks.statuses()['items']
    handles = {str(row.get('handle', '')).casefold() for row in statuses
               if row.get('platform') == 'codeforces' and row.get('login_verified')
               and row.get('status') == 'authenticated'}
    document = accounts.document() if callable(getattr(accounts, 'document', None)) else {}
    credentials = next((row for row in document.get('accounts', [])
                        if row.get('platform') == 'codeforces'
                        and str(row.get('handle', '')).casefold() in handles
                        and row.get('api_key') and row.get('api_secret')), {})
    ids = sorted({row['contest_id'] for row in candidates
                  if row.get('platform') == 'codeforces'
                  and type(row.get('contest_id')) is int and 0 < row['contest_id'] < 100000})
    primary = {row['contest_id'] for row in candidates if row.get('contest_id') in ids
               and score_range is not None and type(row.get('difficulty')) is int
               and score_range[0] <= row['difficulty'] <= score_range[1]}
    rng = secrets.SystemRandom()
    selected = rng.sample(sorted(primary), min(2, len(primary)))
    outside = sorted(set(ids) - primary)
    if outside:
        selected.extend(rng.sample(outside, 1))
    remaining = [cid for cid in ids if cid not in selected]
    selected.extend(rng.sample(remaining, min(3 - len(selected), len(remaining))))

    def collect():
        provider = ContestEvidenceProvider(database, api_key=credentials.get('api_key'),
            api_secret=credentials.get('api_secret'), stop=stop)
        rows, available = [], []
        for cid in selected:
            result = provider.standings(cid)
            if result['status'] == 'available':
                rows.extend(result['problems'])
                available.append(cid)
        gym = provider.gym_catalog(limit=2)
        rows.extend(gym.get('rows', []))
        return rows, {'status': 'available' if available else 'unavailable',
            'contests_checked': len(selected), 'contests_available': len(available),
            'available_contest_ids': available,
            'checked_contest_ids': selected,
            'gym': gym['status'], 'gym_reason': gym.get('reason'),
            'gym_contests_checked': gym.get('contests_checked', 0),
            'gym_problems_discovered': len(gym.get('rows', [])),
            'onsite_status': gym.get('onsite_status', 'not_checked'),
            'onsite_events_checked': gym.get('onsite_events_checked', 0),
            'coverage': '每次至多核对三场普通赛和两场 Gym 的完整官方榜；不是全库统计。',
            'notice': '普通赛统计不可用时明确保留官方 rating；未评级 Gym 没有足量可核验统计时不入选。'}
    try:
        rows, summary = await asyncio.to_thread(collect)
        # Only the awaiting coroutine publishes rows. A cancelled worker cannot
        # append late candidates or continue sending requests.
        if stop.is_set():
            raise asyncio.CancelledError
        if revision is not None and accounts.revision() != revision:
            raise AIError('平台账号在比赛统计核对期间已变化，未发布旧账号候选；请重新抽题')
        _store_catalog_rows(database, 'codeforces', rows)
        return summary
    finally:
        stop.set()
