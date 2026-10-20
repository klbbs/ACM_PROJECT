"""Official catalog discovery and one constrained pool for all AI selection paths."""
import asyncio
import json
import re
import secrets
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from bs4 import BeautifulSoup
from ojcrawler.http import Client
from ojcrawler.models import CrawlError
from ojcrawler.adapters.luogu import page_data
from .codex_bridge import AIError
from .problem_sources import normalize, source_url

PLATFORMS = ('codeforces', 'atcoder', 'luogu', 'nowcoder')
PUBLISHED_SOURCES = {'codeforces.contest.startTimeSeconds', 'atcoder.archive.time',
                     'luogu.problem.createTime', 'nowcoder.problem.publishTime'}
LEGACY_DATE_NOTICE = '旧版训练设置仍使用 CF 最小比赛编号，不能可靠换算为发布日期。请在训练偏好中保存真实的最早发布日期，或明确清除旧限制后再生成题目。'


def requires_publication_date(prefs):
    return isinstance(prefs.get('min_contest'), int) and prefs['min_contest'] > 0 and not prefs.get('published_after')


def publication_time(value):
    """Accept an explicit upstream timestamp, never a contest ID or a guessed year."""
    try:
        if isinstance(value, bool): return None
        if isinstance(value, (int, float)):
            if value > 10**12: value /= 1000
            return int(value) if 946684800 <= value <= time.time() else None
        if isinstance(value, str):
            value = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', value.replace('Z', '+00:00'))
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None: return None
            return publication_time(parsed.timestamp())
    except (ValueError, OverflowError, OSError): pass
    return None


def _problem(platform, key, title, **extra):
    pid = normalize(platform + ':' + str(key))
    return {'id': pid, 'platform': platform, 'problem_id': pid.split(':', 1)[1],
            'title': title, 'url': source_url(pid), 'difficulty': None,
            'difficulty_source': '未标定；不使用 CF 难度比较', 'tags': [],
            'candidate_source': '官方题库', 'published_at': None, **extra}


def _cf(client):
    data = client.json('https://codeforces.com/api/problemset.problems')
    contests = client.json('https://codeforces.com/api/contest.list', params={'gym': 'false'})
    if data.get('status') != 'OK' or contests.get('status') != 'OK':
        raise CrawlError('CF 官方题库或比赛时间获取失败')
    finished = {p['id']: p for p in contests['result'] if p.get('phase') == 'FINISHED'}
    rows = []
    for p in data['result']['problems']:
        if not isinstance(p.get('rating'), int) or p.get('contestId') not in finished: continue
        # The workbench supports standard contest problem indices, not occasional API-only names.
        if not re.fullmatch(r'[1-9]\d{0,4}-[A-Za-z]\d?', f"{p['contestId']}-{p.get('index', '')}"): continue
        contest = finished[p['contestId']]
        rows.append(_problem('codeforces', f"{p['contestId']}-{p['index']}", p['name'],
            difficulty=p['rating'], difficulty_source='Codeforces 官方 rating', tags=p.get('tags', []),
            contest_id=p['contestId'], published_at=publication_time(contest.get('startTimeSeconds')),
            published_at_source='codeforces.contest.startTimeSeconds',
            publication_note='官方比赛开始时间；不使用比赛编号推测日期'))
    return rows


def parse_atcoder_archive(html):
    result = []
    for row in BeautifulSoup(html, 'html.parser').select('tbody tr'):
        stamp = row.select_one('time'); link = row.select_one('a[href^="/contests/"]')
        if not stamp or not link: continue
        m = re.fullmatch(r'/contests/((?:abc|arc|agc)[a-z0-9_-]+)', link['href'])
        ts = publication_time(stamp.get_text(strip=True))
        if m and ts: result.append((m[1], ts))
    return result


def parse_atcoder_tasks(html, contest, published_at):
    result = []
    for row in BeautifulSoup(html, 'html.parser').select('tbody tr'):
        links = row.select(f'a[href^="/contests/{contest}/tasks/"]')
        if not links: continue
        link = links[-1]; m = re.fullmatch(rf'/contests/{re.escape(contest)}/tasks/([a-z0-9_]+)', link['href'])
        if not m: continue
        result.append(_problem('atcoder', m[1], link.get_text(' ', strip=True),
            difficulty_label=links[0].get_text(' ', strip=True), published_at=published_at,
            published_at_source='atcoder.archive.time', publication_note='官方比赛开始时间'))
    return result


def _atcoder(client):
    archive = parse_atcoder_archive(client.get('https://atcoder.jp/contests/archive', params={'lang': 'en'}))
    rows = []
    for contest, ts in archive[:6]:
        rows.extend(parse_atcoder_tasks(client.get(f'https://atcoder.jp/contests/{contest}/tasks', params={'lang': 'en'}), contest, ts))
    return rows


def parse_luogu_catalog(html):
    data = page_data(html); container = data.get('problems', {})
    entries = container.get('result', []) if isinstance(container, dict) else container
    if not isinstance(entries, list): raise CrawlError('洛谷官方题目列表结构变化')
    result = []
    for p in entries:
        if not re.fullmatch(r'(?:P|B)\d+', str(p.get('pid', ''))): continue
        stamp = publication_time(p.get('createTime'))
        result.append(_problem('luogu', p['pid'], p.get('title', p['pid']),
            native_difficulty=p.get('difficulty'), difficulty_source='洛谷官方难度等级（与 CF rating 不可比）',
            published_at=stamp, published_at_source='luogu.problem.createTime' if stamp else None,
            tags=[str(x) for x in p.get('tags', [])]))
    return result


def _luogu(client):
    return parse_luogu_catalog(client.get('https://www.luogu.com.cn/problem/list', params={'page': 1, 'type': 'luogu'}))


def parse_nowcoder_catalog(html):
    result = []
    for row in BeautifulSoup(html, 'html.parser').select('tr[data-problemid]'):
        key = row.get('data-problemid', '')
        link = row.select_one('a.title[href]')
        if not key.isdigit() or not link or link.get('href') != '/acm/problem/' + key: continue
        cells = row.find_all('td', recursive=False)
        native = cells[2].get_text(strip=True) if len(cells) > 2 else ''
        # This list has no reliable publication date. IDs/order never substitute for one.
        result.append(_problem('nowcoder', key, link.get_text(' ', strip=True),
            native_difficulty=int(native) if native.isdigit() else None,
            difficulty_source='牛客官方难度（与 CF rating 不可比）',
            tags=[a.get_text(strip=True) for a in row.select('a[href*="tagId="]')],
            publication_note='官方题库列表未提供可靠发布日期'))
    return result


def _nowcoder(client):
    return parse_nowcoder_catalog(client.get('https://ac.nowcoder.com/acm/problem/list'))


DISCOVERY = {'codeforces': _cf, 'atcoder': _atcoder, 'luogu': _luogu, 'nowcoder': _nowcoder}


def refresh_catalog(database, platform=None):
    """Partial failures retain each platform's previous catalog, never fake success."""
    if platform is not None and platform not in PLATFORMS: raise AIError('无效题库平台')
    results = []
    for name in (platform,) if platform else PLATFORMS:
        client = Client(timeout=15, retries=0)
        try:
            rows = DISCOVERY[name](client)
            if not rows: raise CrawlError('官方题库返回空内容或结构变化，保留原缓存')
            with database.connect() as db:
                db.execute('DELETE FROM problem_catalog WHERE platform=?', (name,))
                db.executemany('INSERT INTO problem_catalog VALUES(?,?,?)',
                    [(name, row['id'], json.dumps(row, ensure_ascii=False)) for row in rows])
            now = int(time.time()); database.set('catalog_updated_at:' + name, now)
            if name == 'codeforces': database.set('catalog_updated_at', now)
            results.append({'platform': name, 'status': 'updated', 'count': len(rows),
                'dated_count': sum(bool(row.get('published_at')) for row in rows)})
        except (CrawlError, AIError, KeyError, ValueError, TypeError):
            results.append({'platform': name, 'status': 'failed', 'count': 0,
                'message': '官方题库获取失败、访问受限或结构变化；已保留原缓存，请稍后重试'})
        finally: client.close()
    database.set('catalog_refresh_status', results)
    return {'count': sum(row['count'] for row in results), 'platforms': results,
            'complete': all(row['status'] == 'updated' for row in results)}


def _canonical(platform, pid):
    return pid if pid.startswith(platform + ':') else platform + ':' + pid


def _pool(database, platform):
    with database.connect() as db:
        rows = db.execute('SELECT data FROM problem_catalog WHERE platform=?', (platform,)).fetchall()
        cached = db.execute('SELECT data FROM wb_problems WHERE id LIKE ?', (platform + ':%',)).fetchall()
        excluded = {_canonical(row[0], row[1]) for row in db.execute('SELECT DISTINCT platform,problem_id FROM submissions')}
        excluded.update(_canonical(row[0], row[1]) for row in db.execute("SELECT platform,problem_id FROM practice_feedback WHERE status IN ('done','skip')"))
    with database.connect() as db:
        excluded.update(row[0] for row in db.execute('SELECT DISTINCT problem_id FROM wb_attempts'))
    excluded.update(database.get('battle_problem_ids', []))
    merged = {}
    for row in rows:
        problem = json.loads(row[0]); merged[problem['id']] = problem
    for row in cached:
        statement = json.loads(row[0]); pid = statement['id']
        if pid in merged or statement.get('source') != 'official': continue
        merged[pid] = _problem(platform, pid.split(':', 1)[1], statement['title'],
            published_at=statement.get('published_at'), published_at_source=statement.get('published_at_source'),
            candidate_source='用户已获取的官方题面缓存')
    return [p for p in merged.values() if p['id'] not in excluded]


def _date_floor(value):
    if not value: return None
    try: return datetime.combine(date.fromisoformat(str(value)), datetime.min.time(), ZoneInfo('Asia/Shanghai')).timestamp()
    except ValueError: raise AIError('题目最早发布日期必须为 YYYY-MM-DD') from None


def candidates(database, minimum=800, maximum=1600, tags=None, limit=60, min_contest=0,
               platform='codeforces', published_after=None):
    """Low-level per-platform query; UI/AI must use recommendation_candidates."""
    if platform not in PLATFORMS: raise AIError('请选择有效的推荐平台')
    floor = _date_floor(published_after); prefs = database.get('training', {})
    avoided = set(prefs.get('avoid_tags', [])); result = []
    for p in _pool(database, platform):
        if platform == 'codeforces' and (not isinstance(p.get('difficulty'), int) or not minimum <= p['difficulty'] <= maximum or p.get('contest_id', 0) < min_contest): continue
        if floor is not None and (p.get('published_at_source') not in PUBLISHED_SOURCES or
                not publication_time(p.get('published_at')) or p['published_at'] < floor): continue
        if tags and not set(tags) & set(p.get('tags', [])): continue
        if p.get('tags') and set(p['tags']).issubset(avoided): continue
        result.append(p)
    secrets.SystemRandom().shuffle(result)
    return result[:limit]


def verified_platforms(accounts):
    checker = getattr(accounts, 'credential_checks', None)
    if checker is None: return []
    return sorted({row['platform'] for row in checker.statuses()['items']
                   if row.get('login_verified') and row.get('status') == 'authenticated' and row['platform'] in PLATFORMS})


async def refresh_recommendation_sessions(accounts):
    """Explicit selection jobs may renew expired evidence; GET routes never do I/O."""
    checker = getattr(accounts, 'credential_checks', None)
    if checker is None or not callable(getattr(checker, 'check', None)) or not callable(getattr(accounts, 'document', None)):
        return
    fresh = {(row['platform'], row['handle']) for row in checker.statuses()['items']}
    for account in accounts.document().get('accounts', []):
        key = (account.get('platform'), account.get('handle'))
        if key[0] not in PLATFORMS or key in fresh: continue
        cookie, _ = checker.credential(*key)
        if not cookie: continue
        try:
            # A serial background check respects both the checker rate limit and global slots.
            await asyncio.to_thread(checker.check, *key)
        except HTTPException as exc:
            if exc.status_code != 429: raise
        except AIError:
            # Configuration errors must not turn mere saved credentials into verified ones.
            continue


def recommendation_candidates(database, accounts, minimum=800, maximum=1600, tags=None,
                              limit=60, published_after=None, excluded_ids=None, preferred_ids=None):
    """Mix only recently verified sessions; balanced shuffle prevents CF pool domination."""
    prefs = database.get("training", {})
    if requires_publication_date(prefs): raise AIError(LEGACY_DATE_NOTICE)
    platforms = verified_platforms(accounts)
    if not platforms: raise AIError('没有已验证登录的平台；请到连接与设置保存账号并点击“检测登录状态”（检测有效期 5 分钟）')
    floor = max(filter(None, (published_after, prefs.get('published_after'))), default=None)
    excluded = set(excluded_ids or [])
    pools = [[row for row in candidates(database, minimum, maximum, tags, 100000 if preferred_ids else max(60, limit),
              platform=platform, published_after=floor) if row['id'] not in excluded] for platform in platforms]
    preferred = set(preferred_ids or [])
    result = [row for pool in pools for row in pool if row['id'] in preferred][:limit]
    pools = [[row for row in pool if row['id'] not in preferred] for pool in pools]
    secrets.SystemRandom().shuffle(pools)
    while any(pools) and len(result) < limit:
        for pool in pools:
            if pool and len(result) < limit: result.append(pool.pop())
    return result


def recommendation_status(database, accounts, published_after=None):
    prefs = database.get('training', {})
    requires_date = requires_publication_date(prefs)
    verified = set(verified_platforms(accounts)); floor = max(filter(None, (published_after, prefs.get('published_after'))), default=None)
    _date_floor(floor)
    rows = []
    for platform in PLATFORMS:
        pool = _pool(database, platform)
        unknown = sum(not publication_time(p.get('published_at')) or p.get('published_at_source') not in PUBLISHED_SOURCES for p in pool)
        rows.append({'platform': platform, 'login_verified': platform in verified,
            'cached_count': len(pool), 'unknown_publication_count': unknown,
            'date_eligible_count': 0 if requires_date else len(candidates(database, minimum=800, maximum=3500, limit=100000, platform=platform, published_after=floor)),
            'updated_at': database.get('catalog_updated_at:' + platform),
            'notice': (LEGACY_DATE_NOTICE if requires_date else '') or ('需要重新检测登录状态，保存 Cookie 不代表登录成功' if platform not in verified else '') or
                      ('部分题目缺少可靠发布日期；设置日期下限后将排除这些题目' if unknown else '')})
    return {'platforms': rows, 'published_after': floor, 'requires_publication_date': requires_date,
            'notice': LEGACY_DATE_NOTICE if requires_date else '', 'refresh': database.get('catalog_refresh_status', [])}
