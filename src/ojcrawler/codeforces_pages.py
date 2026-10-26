"""Bounded official CF page fallback; never infer an account, verdict or timezone."""
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from .models import CrawlError


def timestamp(node):
    for field in ('data-timestamp', 'data-time-seconds'):
        value = node.get(field, '')
        if re.fullmatch(r'\d{10}', value):
            return int(value)
    if node.get('datetime'):
        try:
            parsed = datetime.fromisoformat(node['datetime'].replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return int(parsed.timestamp())
        except ValueError:
            pass
    text = node.get_text(' ', strip=True)
    if not re.search(r'(?:UTC|GMT)[+-]', text) and node.get('title'):
        text = node['title'].strip()
    match = re.fullmatch(r'(.*?)\s*(?:UTC|GMT)([+-])(\d{1,2})(?::(\d{2}))?', text)
    if not match:
        raise CrawlError('CF 官方列表没有可核验的提交时区，未猜测日期；已有记录保留')
    hours, minutes = int(match[3]), int(match[4] or 0)
    if hours > 14 or minutes > 59 or hours == 14 and minutes:
        raise CrawlError('CF 官方列表提交时区无效')
    zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if match[2] == '+' else -1)))
    wall = match[1].strip()
    months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
    for index, name in enumerate(months, 1):
        if wall.startswith(name + '/'):
            wall = f'{index:02d}' + wall[3:]
            break
    for fmt in ('%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return int(datetime.strptime(wall, fmt).replace(tzinfo=zone).timestamp())
        except ValueError:
            continue
    raise CrawlError('CF 官方列表时间无法核验')


def parse_submissions(html, handle):
    if not isinstance(html, str) or len(html.encode()) > 2 * 1024 * 1024:
        raise CrawlError('CF 官方列表超出安全读取范围')
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('table.status-frame-datatable')
    if table is None or soup.select_one('input[type="password"]'):
        raise CrawlError('CF 官方提交列表缺失或要求登录，未将其当作零条记录')
    result, seen = [], set()
    for row in table.select('tr[data-submission-id]'):
        sid = row.get('data-submission-id', '')
        members = [a['href'][9:].rstrip('/') for a in row.select('a[href]')
                   if re.fullmatch(r'/profile/[A-Za-z0-9_.-]+/?', a['href'])]
        if len(members) != 1 or members[0].casefold() != handle.casefold():
            raise CrawlError('CF 官方列表账号不匹配或为团队提交，未导入')
        problem = next((a for a in row.select('a[href]') if re.fullmatch(
            r'/(?:contest|gym)/[1-9]\d{0,8}/problem/[A-Za-z]\d?', a['href'])), None)
        source = next((a for a in row.select('a[href]') if re.fullmatch(
            r'/(?:contest|gym)/[1-9]\d{0,8}/submission/' + re.escape(sid), a['href'])), None)
        state, date = row.select_one('[submissionverdict]'), row.select_one('.format-time,time,[data-timestamp]')
        cells = row.find_all('td', recursive=False)
        if not re.fullmatch(r'[1-9]\d{0,11}', sid) or sid in seen or problem is None or source is None or state is None or date is None or len(cells) < 6:
            raise CrawlError('CF 官方列表行证据缺失或重复，未确认完整结果')
        contest, index = problem['href'].split('/')[2], problem['href'].rsplit('/', 1)[1].upper()
        if source['href'].split('/')[2] != contest:
            raise CrawlError('CF 官方列表提交与题目比赛不一致')
        ts = timestamp(date)
        if not 0 < ts <= time.time() + 300:
            raise CrawlError('CF 官方列表提交时间无效')
        seen.add(sid)
        result.append({'id':int(sid), 'contestId':int(contest), 'creationTimeSeconds':ts,
            'author':{'members':[{'handle':members[0]}]},
            'problem':{'contestId':int(contest), 'index':index, 'name':problem.get_text(' ',strip=True), 'tags':[]},
            'programmingLanguage':cells[4].get_text(' ',strip=True)[:150],
            'verdict':state.get('submissionverdict') or 'TESTING', 'source':'official_user_submissions_page'})
    if not result and not re.search(r'\bNo submissions(?: found)?\b', table.get_text(' ', strip=True), re.I):
        raise CrawlError('CF 官方列表没有可核验的记录或明确空结果提示，未当作零条成功')
    pages = [int(a.get_text(strip=True)) for a in soup.select('.pagination a') if a.get_text(strip=True).isdigit()]
    return result, max(pages, default=1)


def read_submissions(client, handle, *, max_pages=3, contest=None, submission_id=None, since=0):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', handle):
        raise CrawlError('CF 账号格式无效')
    result, seen = [], set()
    for page in range(1, min(5, max_pages) + 1):
        batch, last = parse_submissions(client.get('https://codeforces.com/submissions/'
            + quote(handle, safe='') + (f'/page/{page}' if page > 1 else '')), handle)
        for row in batch:
            if row['id'] in seen:
                raise CrawlError('CF 官方列表分页未前进，未确认完整结果')
            seen.add(row['id'])
            if (contest is None or row['contestId'] == int(contest)) and row['creationTimeSeconds'] >= since:
                result.append(row)
        if submission_id is not None and any(str(r['id']) == str(submission_id) for r in result):
            return result, True
        if page >= last or batch and min(r['creationTimeSeconds'] for r in batch) < since:
            return result, True
    return result, False
