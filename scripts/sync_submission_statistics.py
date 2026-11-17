#!/usr/bin/env python3
"""Explicit bounded metadata backfill; no AI calls, judging or account login.

Use an existing user's database and account file. Never constructs Database,
whose startup migration would interrupt live application jobs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from acmweb.submission_statistics import refresh_statistics


class ExistingCatalog:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file(): raise ValueError('existing database required')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30); db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM web_settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, json.dumps(value, ensure_ascii=False)))


def coverage(database, platform):
    with database.connect() as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT data FROM problem_catalog WHERE platform=?', (platform,))]
    return sum(isinstance(r.get('submission_statistics'), dict)
               and type(r['submission_statistics'].get('submitted_count')) is int
               and r['submission_statistics'].get('acceptance_rate') is not None for r in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--accounts', type=Path, required=True)
    parser.add_argument('--platform', choices=['codeforces','atcoder','luogu','nowcoder'], action='append')
    parser.add_argument('--target', type=int, default=1000)
    parser.add_argument('--request-budget', type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.target <= 20000 or not 1 <= args.request_budget <= 2000: parser.error('bounded positive budget required')
    database = ExistingCatalog(args.database)
    accounts = tomllib.loads(args.accounts.read_text()).get('accounts', [])
    def run(platform):
        requests, stalls = 0, 0
        # Normalize already-cached CF/Luogu statistics without fetching a site.
        if platform in {'codeforces','luogu'}: refresh_statistics(database, platform, accounts, request_budget=0)
        while coverage(database, platform) < args.target and requests < args.request_budget:
            before = coverage(database, platform)
            result = refresh_statistics(database, platform, accounts, request_budget=min(20, args.request_budget-requests))
            requests += result['requests']
            current = coverage(database, platform)
            print(json.dumps({**result,'with_count_and_rate':current}, ensure_ascii=False), flush=True)
            stalls = stalls + 1 if current <= before else 0
            if result['status'] == 'unavailable' or not result['requests'] or stalls >= 3: break
        return {'platform':platform,'with_count_and_rate':coverage(database,platform),'requests':requests}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run,args.platform or ['codeforces','luogu','atcoder','nowcoder']))
    print(json.dumps({'summary':results},ensure_ascii=False),flush=True)
    return 0 if all(r['with_count_and_rate'] >= args.target for r in results) else 2


if __name__ == '__main__': raise SystemExit(main())
