"""Provenance-labelled submission counts and success rates across platforms.

Counts and rates may have different units. A contest participant success rate
must never be described as an all-time accepted-submission rate. No handles,
ranklist rows, credentials, source code or raw upstream bodies are persisted.
"""
import json
import math
import re
import time
from collections import defaultdict

from bs4 import BeautifulSoup
from ojcrawler.http import Client
from ojcrawler.models import CrawlError

TTL = 6 * 3600
AT_SOURCE = 'https://atcoder.jp/contests/{contest}/standings/json'
NC_SOURCE = 'https://ac.nowcoder.com/acm/contest/problem-list'


def count(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def statistics(submitted, *, source, scope, count_kind, accepted=None,
               attempted_people=None, accepted_people=None, observed_at=None):
    submitted, accepted = count(submitted), count(accepted)
    attempted_people, accepted_people = count(attempted_people), count(accepted_people)
    if submitted is None:
        return None
    stamp = int(time.time()) if observed_at is None else count(observed_at)
    if stamp is None or stamp > time.time() + 300: return None
    out = {'submitted_count': submitted, 'source': source, 'scope': scope,
           'count_unit': 'submission', 'count_kind': count_kind,
           'observed_at': stamp,
           'acceptance_rate': None, 'rate_unit': None}
    if accepted is not None and accepted <= submitted:
        out['accepted_count'] = accepted
        if submitted:
            out.update(acceptance_rate=accepted / submitted, rate_unit='submission')
    if attempted_people is not None and accepted_people is not None and accepted_people <= attempted_people:
        out.update(attempted_participants=attempted_people, accepted_participants=accepted_people)
        if attempted_people and out['rate_unit'] is None:
            out.update(acceptance_rate=accepted_people / attempted_people, rate_unit='participant')
    return out


def from_catalog(row):
    brief = row.get('brief_statistics') if isinstance(row.get('brief_statistics'), dict) else {}
    contest = row.get('contest_statistics') if isinstance(row.get('contest_statistics'), dict) else {}
    if (row.get('platform') == 'luogu' and brief.get('scope') == 'all_time_submissions'
            and brief.get('source') == 'https://www.luogu.com.cn/problem/list'):
        return statistics(brief.get('submitted_count'), accepted=brief.get('accepted_count'),
            source=brief['source'], scope='all_time', count_kind='all_submissions', observed_at=brief.get('observed_at'))
    if (row.get('platform') == 'codeforces' and contest.get('complete')
            and contest.get('source') == 'codeforces.contest.standings'):
        # CF standings expose scoring attempts through first solve, not every
        # later resubmission or CE. Keep that limitation in the data contract.
        out = statistics(contest.get('submissions'), source='https://codeforces.com/api/contest.standings',
            scope='contest', count_kind='scoring_attempts_until_first_solve',
            attempted_people=contest.get('attempted_participants'),
            accepted_people=contest.get('accepted_participants'), observed_at=contest.get('observed_at'))
        if out:
            out['contest_id'] = contest.get('contest_id')
            out['participant_unit'] = contest.get('participant_unit', 'person')
        return out
    return None


def parse_atcoder(data, contest, known):
    if (not isinstance(data, dict) or type(data.get('Fixed')) is not bool
            or not isinstance(data.get('TaskInfo'), list) or not isinstance(data.get('StandingsData'), list)
            or len(data['StandingsData']) > 200000 or data.get('hasMore') or data.get('truncated')):
        raise CrawlError('AtCoder statistics are incomplete')
    tasks = [t.get('TaskScreenName') for t in data['TaskInfo'] if isinstance(t, dict)]
    if not tasks or any(not isinstance(t, str) for t in tasks) or len(tasks) != len(set(tasks)):
        raise CrawlError('AtCoder task identity is invalid')
    totals = {task: [0, 0, 0] for task in tasks}
    seen = set()
    for row in data['StandingsData']:
        if not isinstance(row, dict) or not isinstance(row.get('UserScreenName'), str):
            raise CrawlError('AtCoder ranklist shape changed')
        identity = row['UserScreenName'].casefold()
        if identity in seen: raise CrawlError('AtCoder ranklist contains duplicate participants')
        seen.add(identity)
        values = row.get('TaskResults', {})
        if not isinstance(values, dict): raise CrawlError('AtCoder task result shape changed')
        for task in tasks:
            value = values.get(task)
            if value is None: continue
            if (not isinstance(value, dict) or value.get('Pending') or value.get('Frozen')
                    or count(value.get('Count')) is None or count(value.get('Score')) is None
                    or type(value.get('Status')) is not int):
                raise CrawlError('AtCoder judging is not final')
            n = value['Count']
            totals[task][0] += n
            totals[task][1] += int(n > 0)
            points = known.get(task, {}).get('atcoder_points')
            # Official status 1 is a successful result; partial score alone is
            # not a full solve when the published maximum is available.
            full = value.get('Status') == 1 and value['Score'] > 0
            if type(points) in (float, int) and math.isfinite(points) and points > 0:
                full = full and value['Score'] >= points * 100
            totals[task][2] += int(n > 0 and full)
    result = {}
    for task, (submitted, attempted, solved) in totals.items():
        row = known.get(task)
        if not row or row.get('contest_id') != contest: continue
        stat = statistics(submitted, attempted_people=attempted, accepted_people=solved,
            source=AT_SOURCE.format(contest=contest), scope='contest', count_kind='all_contest_submissions')
        stat['contest_id'] = contest
        stat['standings_fixed'] = data['Fixed']
        result[row['id']] = stat
    return result


def parse_nowcoder(data, contest, known):
    if not isinstance(data, dict) or type(data.get('code')) is not int or data.get('code') != 0 or not isinstance(data.get('data'), dict):
        raise CrawlError('Nowcoder statistics unavailable')
    payload = data['data']; basic = payload.get('basicInfo', {})
    entries = payload.get('data')
    if (not isinstance(basic, dict) or basic.get('contestId') != contest
            or not isinstance(entries, list) or len(entries) > 1000
            or type(basic.get('problemCount')) is not int or basic['problemCount'] != len(entries)
            or basic.get('pageCount', 1) != 1):
        raise CrawlError('Nowcoder problem list is incomplete or mismatched')
    result = {}
    seen = set()
    for value in entries:
        if not isinstance(value, dict) or type(value.get('problemId')) is not int:
            raise CrawlError('Nowcoder problem identity changed')
        pid = 'nowcoder:' + str(value['problemId'])
        if pid in seen: raise CrawlError('Nowcoder problem list contains duplicates')
        seen.add(pid)
        if pid not in known: continue
        stat = statistics(value.get('submitCount'), attempted_people=value.get('submitPersonCount'),
            accepted_people=value.get('acceptedCount'), source=NC_SOURCE,
            scope='contest', count_kind='all_contest_submissions')
        if stat is None:
            continue
        if stat.get('acceptance_rate') is None:
            stat['limitation'] = '没有有效的尝试人数分母，人数通过率保持未知'
        stat['contest_id'] = contest
        result[pid] = stat
    return result


def related_nowcoder_contests(html):
    soup = BeautifulSoup(html, 'html.parser')
    heading = soup.find(string=lambda x: isinstance(x, str) and x.strip() == '相关比赛')
    if not heading: return []
    listing = heading.parent.find_next('ul')
    if listing is None: return []
    result = []
    for link in listing.find_all('a', href=True):
        match = re.fullmatch(r'/acm/contest/([1-9]\d*)/?', link['href'])
        if match and int(match[1]) not in result: result.append(int(match[1]))
    return result[:5]


def save_statistics(database, platform, updates):
    saved = 0
    with database.connect() as conn:
        for pid, stat in updates.items():
            row = conn.execute('SELECT data FROM problem_catalog WHERE platform=? AND id=?', (platform, pid)).fetchone()
            if not row: continue
            data = json.loads(row['data'])
            old = data.get('submission_statistics', {})
            # A replay with few/no attempts cannot replace a more informative
            # original contest snapshot for the same official problem ID.
            if (platform == 'nowcoder' and old.get('contest_id') != stat.get('contest_id')
                    and (old.get('attempted_participants') or 0) > (stat.get('attempted_participants') or 0)):
                continue
            data['submission_statistics'] = stat
            conn.execute('UPDATE problem_catalog SET data=? WHERE platform=? AND id=?',
                         (json.dumps(data, ensure_ascii=False), platform, pid)); saved += 1
    return saved


def refresh_statistics(database, platform, accounts=(), *, request_budget=8, on_progress=None, client_factory=Client):
    """Bounded incremental enrichment; failures never erase previous evidence."""
    with database.connect() as conn:
        rows = [json.loads(r['data']) for r in conn.execute('SELECT data FROM problem_catalog WHERE platform=?', (platform,))]
    existing = {r['id']: r for r in rows}; now = int(time.time())
    updates = {r['id']: s for r in rows if (s := from_catalog(r))}
    saved = save_statistics(database, platform, updates)
    if platform in {'codeforces', 'luogu'}:
        return {'platform':platform,'updated':saved,'requests':0,'status':'updated' if saved else 'unknown'}
    account = next((a for a in accounts if a.get('platform') == platform), {})
    host = 'atcoder.jp' if platform == 'atcoder' else 'ac.nowcoder.com'
    cookie = account.get('cookie')
    if not cookie:
        return {'platform':platform,'updated':saved,'requests':0,'status':'login_required',
                'message':'此平台统计需要有效登录会话；请先完成平台登录并检测'}
    client = client_factory(timeout=15, retries=0, cookie=cookie, cookie_host=host if cookie else None)
    requests = 0; error = None; skipped_contests = 0
    def fresh(row):
        stat = row.get('submission_statistics', {})
        return type(stat.get('observed_at')) is int and 0 <= int(time.time()) - stat['observed_at'] < TTL and count(stat.get('submitted_count')) is not None
    checked_key = 'catalog_statistics_checked:' + platform
    checked = database.get(checked_key, {})
    if not isinstance(checked, dict): checked = {}
    pending = [r for r in rows if not fresh(r)
               and not (type(checked.get(r['id'])) is int and 0 <= now - checked[r['id']] < 600)]
    try:
        if platform == 'atcoder':
            grouped = defaultdict(list)
            for row in pending:
                contest = row.get('contest_id')
                if isinstance(contest, str) and re.fullmatch(r'(?:abc|arc|agc)\d+', contest): grouped[contest].append(row)
            known = {r['problem_id']:r for r in rows}
            contests = sorted(grouped, key=lambda cid:(-sum(bool(r.get('tags')) for r in grouped[cid]), cid))
            for cid in contests[:request_budget]:
                requests += 1
                data = client.json(AT_SOURCE.format(contest=cid))
                try:
                    batch = parse_atcoder(data, cid, known)
                except CrawlError:
                    skipped_contests += 1
                    for row in grouped[cid]: checked[row['id']] = now
                    database.set(checked_key, checked)
                    continue
                saved += save_statistics(database, platform, batch)
                if on_progress: on_progress({'platform':platform,'updated':saved,'requests':requests})
        elif platform == 'nowcoder':
            seen = set()
            for row in pending:
                if requests + 2 > request_budget: break
                if fresh(existing[row['id']]): continue
                key = row['problem_id']
                if not re.fullmatch(r'[1-9]\d*', key): continue
                requests += 1
                contests = related_nowcoder_contests(client.get('https://ac.nowcoder.com/acm/problem/' + key + '/submit-list'))
                checked[row['id']] = now
                database.set(checked_key, checked)
                for cid in contests:
                    if cid in seen: continue
                    if requests >= request_budget: break
                    seen.add(cid); requests += 1
                    batch = parse_nowcoder(client.json(NC_SOURCE, params={'token':'','id':cid}), cid, existing)
                    if not batch: continue
                    saved += save_statistics(database, platform, batch)
                    for pid, stat in batch.items(): existing[pid]['submission_statistics'] = stat
                    break
                if on_progress: on_progress({'platform':platform,'updated':saved,'requests':requests})
        else: raise ValueError('unsupported statistics platform')
    except (CrawlError, ValueError, TypeError, KeyError):
        error = '统计来源不可读、登录失效或数据不完整；本次已停止，既有统计保留'
    finally: client.close()
    status = ('unavailable' if error and not saved else 'partial' if error or skipped_contests
              else 'updated' if saved else 'cached' if not requests else 'partial')
    message = error or ('部分比赛统计不完整，已跳过并保留既有数据' if skipped_contests
                        else '本轮尚无新增统计；没有可用来源的题目保持未知' if requests and not saved else None)
    return {'platform':platform,'updated':saved,'requests':requests,'status':status,
            'skipped_contests':skipped_contests,'message':message}
