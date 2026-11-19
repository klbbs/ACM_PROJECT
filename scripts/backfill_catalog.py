#!/usr/bin/env python3
"""Resume a bounded public-catalog backfill against an existing workspace."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import time

from sync_submission_statistics import ExistingCatalog
from acmhermes.catalog import _publish, _valid_rows
from acmweb.catalog import (_cf, _enrich_atcoder_difficulties, _luogu_page_batch,
                           _official_nowcoder_items, _store_catalog_rows, _sync_error,
                           _with_atcoder_official_practice)
from acmweb.catalog_enrichment import atcoder_tag_batch
from acmweb.expanded_topic_sources import discover_atcoder, discover_nowcoder
from acmweb.submission_statistics import from_catalog
from ojcrawler.http import Client
from ojcrawler.models import CrawlError


@dataclass
class Batch:
    rows: list
    next_page: int
    pages_read: int
    total: int | None
    warnings: list
    interrupted: bool = False


def read_batch(database, client, platform, page, pages):
    if platform == 'luogu':
        found = _luogu_page_batch(client, {}, max_pages=pages, first_page=page)
        return Batch(found['items'], found['next_page'], found['pages_read'],
                     found.get('total_count'), found['warnings'], bool(found['warnings']))
    if platform == 'nowcoder':
        found = discover_nowcoder(client, None, first_page=page, max_pages=pages)
        return Batch(_official_nowcoder_items(found['items']), found['next_page'],
                     found['pages_read'], found['total_count'], found['warnings'],
                     found['next_page'] == page or found['pages_read'] < pages and found['truncated'])
    with database.connect() as db:
        rows = [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM problem_catalog WHERE platform='atcoder'")]
    previous = {row['id']: row.get('tags_observed_at') for row in rows}
    found = atcoder_tag_batch(client, rows, first_page=page, max_pages=pages)
    fields = ('id', 'platform', 'tags', 'luogu_tag_ids', 'tags_source', 'tags_source_url',
              'tags_mirror_id', 'tags_observed_at')
    updated = [{key: row[key] for key in fields} for row in rows
               if row.get('tags_observed_at') != previous[row['id']]]
    return Batch(updated, found['next_page'],
                 found['pages'], None, [found['warning']] if found['warning'] else [],
                 bool(found['warning']))


def save_batch(database, platform, batch, page):
    for row in batch.rows:
        if platform == 'luogu' and (statistics := from_catalog(row)):
            row['submission_statistics'] = statistics
    _store_catalog_rows(database, platform, batch.rows)
    with database.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cursor_key = 'catalog_cursor:' + platform
        current = db.execute('SELECT value FROM web_settings WHERE key=?', (cursor_key,)).fetchone()
        cursor = json.loads(current[0]) if current else {}
        if cursor.get('ordinary', 1) == page:
            cursor['ordinary'] = batch.next_page
            db.execute('INSERT OR REPLACE INTO web_settings VALUES (?,?)', (cursor_key, json.dumps(cursor)))
        cached = db.execute('SELECT count(*) FROM problem_catalog WHERE platform=?', (platform,)).fetchone()[0]
    value = {'platform':platform, 'first_page':page, 'next_page':batch.next_page,
             'count':len(batch.rows), 'cached_count':cached, 'pages_read':batch.pages_read,
             'total_count':batch.total, 'interrupted':batch.interrupted, 'warnings':batch.warnings}
    database.set('catalog_backfill_status:' + platform, {**value, 'updated_at':int(time.time())})
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--platform', required=True, choices=('codeforces','atcoder','luogu','nowcoder'))
    parser.add_argument('--page-budget', type=int, default=40)
    parser.add_argument('--first-page', type=int, help='Use an explicit start page without resetting the saved cursor')
    parser.add_argument('--refresh-atcoder-index', action='store_true', help='Refresh task metadata before adding mirror tags')
    args = parser.parse_args()
    if not 1 <= args.page_budget <= 400:
        parser.error('page budget must be between 1 and 400')
    if args.first_page is not None and not 1 <= args.first_page <= 10000:
        parser.error('first page must be between 1 and 10000')
    if args.refresh_atcoder_index and args.platform != 'atcoder':
        parser.error('--refresh-atcoder-index requires --platform atcoder')
    database = ExistingCatalog(args.database)
    client = Client(timeout=15, retries=0)
    # This operation reads public data directly. Never use process-wide proxies.
    client.session.trust_env = False
    try:
        if args.platform == 'codeforces':
            rows = _cf(client)
            if not _valid_rows(rows) or len(rows) != len({row['id'] for row in rows}):
                raise CrawlError('invalid Codeforces catalog')
            _publish(database, rows, int(time.time()))
            print(json.dumps({'platform':'codeforces','catalog_rows':len(rows)}), flush=True)
            return 0
        if args.refresh_atcoder_index:
            found = _with_atcoder_official_practice(client, discover_atcoder(client, None))
            rows = _enrich_atcoder_difficulties(client, found['items'])
            _store_catalog_rows(database, 'atcoder', rows)
            print(json.dumps({'platform':'atcoder','index_rows':len(rows),
                              'warnings':found['warnings']}, ensure_ascii=False), flush=True)
        remaining = args.page_budget
        page = args.first_page or database.get('catalog_cursor:' + args.platform, {}).get('ordinary', 1)
        while remaining > 0:
            batch = read_batch(database, client, args.platform, page, min(8, remaining))
            summary = save_batch(database, args.platform, batch, page)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            if batch.interrupted or not batch.pages_read or batch.next_page <= page:
                return 2 if batch.interrupted else 0
            remaining -= batch.pages_read
            page = batch.next_page
        return 0
    except (CrawlError, ValueError, TypeError, KeyError, AttributeError) as exc:
        code, message = _sync_error(exc)
        print(json.dumps({'platform':args.platform,'error_code':code,'message':message}, ensure_ascii=False), flush=True)
        return 2
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
