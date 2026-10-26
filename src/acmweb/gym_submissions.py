"""Supplement CF user.status with known Gym/Mashup contests, read-only.

Private contests need a readable official page; a user supplied URL is only a
query scope, never evidence of AC. No code or browser HTML is accepted from users.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ojcrawler.http import Client
from ojcrawler.models import Collection, CrawlError, Submission
from ojcrawler.storage import Store, codeforces_judge_verdict
from .codex_bridge import AIError


def start_of_day(value=''):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    try:
        if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        chosen = datetime.strptime(value, '%Y-%m-%d').date() if value else today
        if chosen > today or (today - chosen).days > 366:
            raise ValueError
    except ValueError:
        raise ValueError('同步日期须在最近一年内，格式为 YYYY-MM-DD') from None
    return int(datetime.combine(chosen, datetime.min.time(), ZoneInfo('Asia/Shanghai')).timestamp())


def contest_from_url(value):
    parsed = urlsplit(value.strip())
    match = re.fullmatch(r'/gym/([1-9]\d{5,8})(?:/(?:submission/[1-9]\d{0,11}|problem/[A-Za-z]\d?|my|status))?/?', parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc not in {'codeforces.com', 'www.codeforces.com'}
            or parsed.query or parsed.fragment or not match):
        raise ValueError('请填写 Codeforces 官方 Gym 比赛、题目或提交链接（不含查询参数）')
    return int(match[1])


def _timestamp(node):
    # Only explicit official UTC offsets or Unix attributes establish a day.
    # A bare wall-clock string is not safe: account/browser locale may differ.
    for field in ('data-timestamp', 'data-time-seconds'):
        value = node.get(field)
        if value and re.fullmatch(r'\d{10}', value):
            return int(value)
    if node.get('datetime'):
        try:
            parsed = datetime.fromisoformat(node['datetime'].replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return int(parsed.timestamp())
        except ValueError:
            pass
    text = node.get_text(' ', strip=True)
    # The official time span can carry the complete zone-qualified value in title.
    if not re.search(r'(?:UTC|GMT)[+-]', text) and node.get('title'):
        text = node['title'].strip()
    match = re.fullmatch(r'(.*?)\s*(?:UTC|GMT)([+-])(\d{1,2})(?::(\d{2}))?', text)
    if not match:
        raise CrawlError('Gym 官方页面未提供可确认时区的提交时间，未猜测日期或计入榜单')
    hours, minutes = int(match[3]), int(match[4] or 0)
    if hours > 14 or minutes > 59 or hours == 14 and minutes:
        raise CrawlError('Gym 官方提交时区无效，未计入榜单')
    zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if match[2] == '+' else -1)))
    months = {name: index for index, name in enumerate(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1)}
    wall = match[1].strip()
    for name, month in months.items():
        if wall.startswith(name + '/'):
            wall = f'{month:02d}' + wall[3:]
            break
    for fmt in ('%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return int(datetime.strptime(wall, fmt).replace(tzinfo=zone).timestamp())
        except ValueError:
            continue
    raise CrawlError('Gym 官方提交时间格式无法确认，未计入榜单')


def parse_page(html, contest, handle):
    if not isinstance(html, str) or len(html.encode('utf-8')) > 2 * 1024 * 1024:
        raise CrawlError('Gym 页面超出安全读取范围')
    soup = BeautifulSoup(html, 'html.parser')
    if soup.select_one('#challenge-form, #challenge-running, .cf-turnstile') or any(s in html.lower() for s in ('cf-chl-', 'just a moment...', 'checking your browser')):
        raise CrawlError('Gym 官方页面要求浏览器安全验证，尚未同步；请在官方浏览器处理后更新凭据重试')
    if soup.select_one('input[type=password]'):
        raise CrawlError('Gym 官方页面要求登录，请检查此账号的 Cookie 和比赛访问权限')
    table = soup.select_one('table.status-frame-datatable')
    if table is None:
        raise CrawlError('Gym 官方提交列表不可读或没有比赛权限；未将其当作零条提交')
    result = []
    for row in table.select('tr[data-submission-id]'):
        users = [a['href'].removeprefix('/profile/').rstrip('/') for a in row.select('a[href]') if re.fullmatch(r'/profile/[A-Za-z0-9_.-]+/?', a['href'])]
        if len(users) != 1 or users[0].casefold() != handle.casefold():
            continue
        problem = next((a for a in row.select('a[href]') if re.fullmatch(rf'/gym/{contest}/problem/[A-Za-z]\d?', a['href'])), None)
        sid = row.get('data-submission-id', '')
        verdict = row.select_one('[submissionverdict]')
        timestamp = row.select_one('.format-time, time, [data-timestamp]')
        if not re.fullmatch(r'[1-9]\d{0,11}', sid) or problem is None or verdict is None or timestamp is None:
            raise CrawlError('Gym 官方提交行结构变化，未确认完整结果')
        ts = _timestamp(timestamp)
        if not 0 < ts <= time.time() + 300:
            raise CrawlError('Gym 官方提交时间无效，未计入榜单')
        cells = row.find_all('td', recursive=False)
        language = cells[4].get_text(' ', strip=True)[:150] if len(cells) >= 6 else None
        result.append(Submission(sid, f'{contest}-' + problem['href'].rsplit('/', 1)[1].upper(), ts,
            codeforces_judge_verdict(verdict.get('submissionverdict')), language=language,
            contest_id=str(contest), url=f'https://codeforces.com/gym/{contest}/submission/{sid}',
            problem={'title': problem.get_text(' ', strip=True)[:200]},
            raw={'source': 'official_gym_page', 'creationTimeSeconds': ts,
                 'verdict': verdict.get('submissionverdict')}))
    pages = [int(a.get_text(strip=True)) for a in soup.select('.pagination a') if a.get_text(strip=True).isdigit()]
    return result, max(pages, default=1)


def collect_contest(client, contest, handle, since, *, deadline=None):
    # API works for public Gyms; private Mashups often return "not found".
    # Fall back to authenticated HTML, but never derive AC from the URL itself.
    rows, seen = [], set()
    deadline = min(deadline or float('inf'), time.monotonic() + 60)
    def budget():
        if time.monotonic() >= deadline:
            raise CrawlError('Gym 查询时间超出安全读取范围')
    try:
        for page in range(5):
            budget()
            payload = client.json('https://codeforces.com/api/contest.status',
                params={'contestId': contest, 'handle': handle, 'from': page * 200 + 1, 'count': 200})
            if not isinstance(payload, dict) or payload.get('status') != 'OK' or not isinstance(payload.get('result'), list):
                raise CrawlError('Gym API 不可用')
            batch = payload['result']
            for row in batch:
                p = row.get('problem') or {}
                members = (row.get('author') or {}).get('members') or []
                if len(members) != 1 or str(members[0].get('handle', '')).casefold() != handle.casefold():
                    continue
                sid, ts, index = row.get('id'), row.get('creationTimeSeconds'), p.get('index')
                if (type(sid) is not int or not 0 < sid < 10**12 or type(ts) is not int
                        or not 0 < ts <= time.time() + 300 or p.get('contestId') != contest
                        or not isinstance(index, str) or not re.fullmatch(r'[A-Za-z]\d?', index)):
                    raise CrawlError('Gym API 提交证据不匹配')
                if sid in seen:
                    raise CrawlError('Gym API 分页重复，未确认完整结果')
                seen.add(sid)
                if ts >= since:
                    rows.append(Submission(str(sid), f'{contest}-{index.upper()}', ts,
                        codeforces_judge_verdict(row.get('verdict')), contest_id=str(contest),
                        language=row.get('programmingLanguage'), url=f'https://codeforces.com/gym/{contest}/submission/{sid}',
                        problem={'title': p.get('name'), 'difficulty': p.get('rating'), 'tags': p.get('tags', [])}, raw=row))
            if len(batch) < 200 or batch and all(type(r.get('creationTimeSeconds')) is int and r['creationTimeSeconds'] < since for r in batch):
                return rows
        raise CrawlError('Gym 提交超过 1000 条安全查询范围，未确认完整结果')
    except (CrawlError, AttributeError, KeyError, TypeError, ValueError):
        # Do not send code or request source; only read this account's status.
        rows, seen = [], set()
        for page in range(1, 6):
            budget()
            html = client.get(f'https://codeforces.com/gym/{contest}/status/page/{page}', params={'handle': handle})
            batch, last = parse_page(html, contest, handle)
            for row in batch:
                if row.id in seen:
                    raise CrawlError('Gym 页面分页未前进，未确认完整结果')
                seen.add(row.id)
                if row.submitted_at >= since:
                    rows.append(row)
            if page >= last:
                return rows
        raise CrawlError('Gym 页面超过 5 页安全查询范围，未确认完整结果')


def _failure(error):
    # Only static messages can reach task results/UI, never upstream contents.
    text = str(error)
    if '时区' in text or '时间格式' in text or '时间无效' in text:
        return 'time_unverified', 'Gym 官方提交时间或时区无法核验，未猜测日期；历史记录保留。'
    if '安全验证' in text or 'HTTP 403' in text:
        return 'access_restricted', 'Gym 官方页面要求浏览器验证或拒绝服务器访问；请在官方浏览器确认比赛权限并更新凭据，仍受限时暂不能完成同步。'
    if '登录' in text or '比赛权限' in text:
        return 'login_or_permission', 'Gym 官方页面要求登录或比赛权限；请确认已绑定账号能打开此比赛，更新 Cookie 后重试。'
    if '安全读取范围' in text or '安全查询范围' in text:
        return 'query_limit', 'Gym 查询达到时间、页数或大小上限，本次未确认完整数量；请稍后重试，历史记录保留。'
    return 'unreadable', 'Gym 官方记录暂不可读或格式无法核验；历史保留，未猜测 AC 或时间。'


def sync_gym(database, accounts, settings, *, url='', day=''):
    current = [a for a in accounts.document().get('accounts', []) if a['platform'] == 'codeforces']
    if url and not current:
        raise AIError('请先添加 Codeforces 账号，再补充 Gym 比赛范围')
    requested = contest_from_url(url) if url else None
    now = int(time.time())
    since = start_of_day(day)
    scopes = database.get('cf_gym_scopes', {})
    deadline = time.monotonic() + 180
    results = []
    for account in current:
        handle = account['handle']
        key = handle.casefold()
        contests = set(scopes.get(key, []))
        # Existing official history also discovers Gyms without a manual scope.
        with database.connect() as db:
            known = db.execute("SELECT DISTINCT problem_id FROM submissions WHERE platform='codeforces' AND account=? COLLATE NOCASE", (handle,)).fetchall()
        for row in known:
            match = re.fullmatch(r'(?:codeforces:)?([1-9]\d{5,8})-[A-Za-z]\d?', row['problem_id'])
            if match:
                contests.add(int(match[1]))
        if requested:
            contests.add(requested)
        if len(contests) > 20:
            raise AIError('每个 CF 账号最多同步 20 场 Gym，请先清理历史补充范围')
        # Persist discovery before networking, without overwriting another
        # concurrently queued catalog/crawl task's newly added contest scope.
        with database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute("SELECT value FROM web_settings WHERE key='cf_gym_scopes'").fetchone()
            latest = json.loads(saved['value']) if saved else {}
            contests.update(latest.get(key, []))
            if len(contests) > 20:
                raise AIError('每个 CF 账号最多同步 20 场 Gym，请先清理历史补充范围')
            latest[key] = sorted(contests)
            db.execute("INSERT INTO web_settings VALUES('cf_gym_scopes',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(latest),))
        cookie = account.get('cookie') or ('' if settings.isolated else os.environ.get(account.get('cookie_env', ''), ''))
        client = Client(cookie=cookie or None, cookie_host='codeforces.com' if cookie else None, timeout=20, retries=0)
        try:
            for contest in sorted(contests):
                try:
                    rows = collect_contest(client, contest, handle, since, deadline=deadline)
                    # Binding changes must not mix a replacement account into results.
                    if account not in accounts.document().get('accounts', []):
                        raise CrawlError('账号配置已更改，请重新同步')
                    store = Store(database.path)
                    try:
                        store.save(f'gym-{now}-{contest}', now, Collection('codeforces', handle,
                            submissions=rows, complete=False), capture_snapshot=False)
                    finally:
                        store.close()
                    results.append({'platform': 'codeforces', 'account': handle, 'contest_id': contest,
                        'complete': True, 'count': len(rows), 'warnings': [], 'error': None})
                except (CrawlError, AttributeError, KeyError, TypeError, ValueError) as exc:
                    reason, message = _failure(exc)
                    results.append({'platform': 'codeforces', 'account': handle, 'contest_id': contest,
                        'complete': False, 'count': 0, 'warnings': [],
                        'error_code': reason, 'error': message})
        finally:
            client.close()
    return results
