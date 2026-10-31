"""On-demand public Codeforces catalog for Hermes selection.

Validate official discovery before publishing metadata and its success marker.
An in-flight HTTP request may finish after cancellation; a cooperative client
prevents subsequent requests, and only this coroutine can publish the result.
"""
import asyncio
import json
import threading
import time

from acmweb.catalog import _cf, verified_platforms, _merge_catalog_metadata
from acmweb.codex_bridge import AIError
from ojcrawler.http import Client

TTL_SECONDS = 6 * 60 * 60
SUCCESS_KEY = 'hermes_catalog_success_at:codeforces'
COUNT_KEY = 'hermes_catalog_count:codeforces'
SOURCE = 'codeforces.problemset.problems+contest.list'
COVERAGE = 'Codeforces 已结束且具有 rating 的官方公开题库；不包含全部 Gym 或未评级题'
FAILURE = 'Codeforces 官方题库同步未完成，已保留旧缓存；请稍后重试，本次不会使用不完整或过期题库抽题'


def _cached(database):
    with database.connect() as db:
        rows = [json.loads(row['data']) for row in db.execute(
            "SELECT data FROM problem_catalog WHERE platform='codeforces'")]
    return rows


def _valid_rows(rows):
    return bool(rows) and all(
        isinstance(row, dict) and row.get('platform') == 'codeforces'
        and isinstance(row.get('id'), str) and row['id'].startswith('codeforces:')
        and type(row.get('difficulty')) is int
        and type(row.get('published_at')) is int and 946684800 <= row['published_at'] <= time.time()
        and row.get('published_at_source') == 'codeforces.contest.startTimeSeconds'
        for row in rows)


class _OfficialClient(Client):
    def __init__(self, stopped):
        super().__init__(timeout=15, retries=0)
        self.stopped = stopped
        original_get=self.session.get
        def checked_get(*args,**kwargs):
            # Check after the shared host-pacing wait, at the actual send.
            if self.stopped.is_set():
                raise asyncio.CancelledError
            return original_get(*args,**kwargs)
        self.session.get=checked_get

    def json(self, url, params=None, headers=None):
        if self.stopped.is_set():
            raise asyncio.CancelledError
        data = super().json(url, params=params, headers=headers)
        if self.stopped.is_set():
            raise asyncio.CancelledError
        if not isinstance(data, dict) or data.get('status') != 'OK' or data.get('truncated'):
            raise AIError(FAILURE)
        result = data.get('result')
        if url.endswith('/problemset.problems'):
            if (not isinstance(result, dict) or not isinstance(result.get('problems'), list)
                    or not result['problems'] or result.get('truncated') or result.get('hasMore')):
                raise AIError(FAILURE)
        elif url.endswith('/contest.list'):
            if not isinstance(result, list) or not result:
                raise AIError(FAILURE)
        else:
            raise AIError(FAILURE)
        return data


def _refresh(stopped):
    # The existing public parser uses the complete unpaginated standard
    # problemset and contest APIs. No account cookie or API key is supplied.
    client = _OfficialClient(stopped)
    try:
        rows = _cf(client)
        if stopped.is_set():
            raise asyncio.CancelledError
        if not _valid_rows(rows) or len({row['id'] for row in rows}) != len(rows):
            raise AIError(FAILURE)
        return rows
    finally:
        client.close()


def _publish(database, rows, now):
    # Use one transaction for the new public metadata and freshness marker.
    # Preserve existing supplemental catalog fields using the shared merge rule.
    with database.connect() as db:
        previous = {row['id']: json.loads(row['data']) for row in db.execute(
            "SELECT id,data FROM problem_catalog WHERE platform='codeforces'")}
        merged = [_merge_catalog_metadata('codeforces', previous.get(row['id'], {}), row) for row in rows]
        db.executemany('INSERT OR REPLACE INTO problem_catalog VALUES(?,?,?)', [
            ('codeforces', row['id'], json.dumps(row, ensure_ascii=False)) for row in merged])
        db.execute('INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (COUNT_KEY, json.dumps(len(rows))))
        for key in (SUCCESS_KEY, 'catalog_updated_at:codeforces', 'catalog_updated_at'):
            db.execute('INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, json.dumps(now)))


async def ensure_catalog(database, accounts):
    """Ensure a recently complete rated CF catalog only for verified CF accounts.

    Metadata is deliberately public and bounded. A successful explicit Hermes
    refresh is needed once for legacy caches, whose completeness is unknown.
    """
    if 'codeforces' not in verified_platforms(accounts):
        return {'platform': 'codeforces', 'status': 'not_required', 'count': 0,
                'source': SOURCE, 'coverage': COVERAGE, 'freshness': 'not_checked'}
    now = int(time.time())
    stopped = threading.Event()
    try:
        refreshed_at = database.get(SUCCESS_KEY)
        rows = [row for row in _cached(database) if _valid_rows([row])]
        expected_count = database.get(COUNT_KEY)
        if (type(expected_count) is int and 0 < expected_count <= len(rows) and type(refreshed_at) is int and 0 <= now - refreshed_at < TTL_SECONDS
                and _valid_rows(rows)):
            return {'platform': 'codeforces', 'status': 'cached', 'count': len(rows),
                    'source': SOURCE, 'coverage': COVERAGE, 'freshness': 'fresh',
                    'refreshed_at': refreshed_at, 'max_age_seconds': TTL_SECONDS}
        rows = await asyncio.to_thread(_refresh, stopped)
        # There are no await points between the returned worker result and this
        # local transaction; cancellation while waiting cannot publish late data.
        now = int(time.time())
        _publish(database, rows, now)
        return {'platform': 'codeforces', 'status': 'updated', 'count': len(rows),
                'source': SOURCE, 'coverage': COVERAGE, 'freshness': 'fresh',
                'refreshed_at': now, 'max_age_seconds': TTL_SECONDS}
    except asyncio.CancelledError:
        stopped.set()
        raise
    except Exception:
        # Neither transport exception text nor response bodies reach the API.
        raise AIError(FAILURE) from None
