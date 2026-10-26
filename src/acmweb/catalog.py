"""Official catalog discovery and one constrained pool for all AI selection paths."""
import asyncio
from bisect import bisect_left, bisect_right
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
from .difficulty_estimates import ATCODER_MODELS, ATCODER_POINTS

PLATFORMS = ('codeforces', 'atcoder', 'luogu', 'nowcoder')
PUBLISHED_SOURCES = {'codeforces.contest.startTimeSeconds', 'atcoder.archive.time',
                     'atcoder.contest.startTime', 'luogu.problem.createTime', 'nowcoder.problem.publishTime'}
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
            tags_source='codeforces.problemset.problems',
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
    from .expanded_topic_sources import discover_atcoder
    found = _with_atcoder_official_practice(client, discover_atcoder(client, None))
    client.catalog_discovery = {key: value for key, value in found.items() if key != 'items'}
    return _enrich_atcoder_difficulties(client, found['items'])


def _enrich_atcoder_difficulties(client, rows):
    # Fixed public datasets, fetched without any platform cookie. Only bounded
    # numerical metadata is retained; aggregate dataset user names are dropped.
    if not callable(getattr(client, 'json', None)):
        return rows
    models, points = {}, {}
    try:
        data = client.json(ATCODER_MODELS)
        if isinstance(data, dict) and len(data) <= 50000:
            models = {pid: row['difficulty'] for pid, row in data.items()
                      if isinstance(row, dict) and type(row.get('difficulty')) is int
                      and -10000 <= row['difficulty'] <= 10000 and not row.get('is_experimental')}
    except (CrawlError, ValueError, TypeError, KeyError):
        pass
    try:
        data = client.json(ATCODER_POINTS)
        if isinstance(data, list) and len(data) <= 50000:
            points = {row['id']: row['point'] for row in data if isinstance(row, dict)
                      and isinstance(row.get('id'), str) and type(row.get('point')) in (int, float)
                      and 0 < row['point'] <= 10000}
    except (CrawlError, ValueError, TypeError, KeyError):
        pass
    population = sorted(models.values())
    for row in rows:
        pid = row['problem_id']
        if pid in models and population:
            difficulty = models[pid]
            row.update(atcoder_problem_difficulty=difficulty,
                atcoder_difficulty_percentile=(bisect_left(population, difficulty) + bisect_right(population, difficulty)) / (2 * len(population)),
                atcoder_statistics_population=len(population), atcoder_statistics_source=ATCODER_MODELS,
                atcoder_statistics_fetched_at=int(time.time()))
        if pid in points:
            row.update(atcoder_points=points[pid], atcoder_points_source=ATCODER_POINTS)
    return rows


def _atcoder_practice(client):
    html = client.get('https://atcoder.jp/contests/practice2/tasks', params={'lang': 'en'})
    soup = BeautifulSoup(html, 'html.parser')
    stamp = next((publication_time(node.get_text(strip=True))
                  for node in soup.select('.contest-duration .fixtime-full, .contest-duration time')
                  if publication_time(node.get_text(strip=True))), None)
    rows = parse_atcoder_tasks(html, 'practice2', stamp)
    for row in rows:
        row['published_at_source'] = 'atcoder.contest.startTime' if stamp else None
        row['candidate_source'] = 'AtCoder 官方 Library Practice Contest'
    return rows


def parse_luogu_catalog(html):
    data = page_data(html); container = data.get('problems', {})
    entries = container.get('result', []) if isinstance(container, dict) else container
    if not isinstance(entries, list): raise CrawlError('洛谷官方题目列表结构变化')
    result = []
    for p in entries:
        if not re.fullmatch(r'(?:P|B)\d+', str(p.get('pid', ''))): continue
        stamp = publication_time(p.get('createTime'))
        result.append(_problem('luogu', p['pid'], p.get('name') or p.get('title') or p['pid'],
            native_difficulty=p.get('difficulty'), difficulty_source='洛谷官方难度等级（与 CF rating 不可比）',
            published_at=stamp, published_at_source='luogu.problem.createTime' if stamp else None,
            tags=[str(x) for x in p.get('tags', [])], tags_source='luogu.official_problem_list'))
    return result


def _luogu(client):
    ordinary = _luogu_page_batch(client, {}, max_pages=8)
    templates = _luogu_page_batch(client, {'keyword': '模板'}, max_pages=8) if not ordinary.get('error_code') else {
        'items': [], 'warnings': [], 'truncated': True, 'pages_read': 0}
    rows = list({row['id']: row for row in ordinary['items'] + templates['items']}.values())
    warnings = list(dict.fromkeys(ordinary['warnings'] + templates['warnings']))
    client.catalog_discovery = {'coverage': f"洛谷普通列表与模板列表，本次共 {ordinary['pages_read'] + templates['pages_read']} 页；并非全站镜像",
                               'truncated': ordinary['truncated'] or templates['truncated'],
                               'warnings': warnings}
    if warnings: client.catalog_discovery['partial_failure'] = True
    if not rows and warnings: raise CrawlError(warnings[0])
    return rows


def _luogu_page_batch(client, params, max_pages=3, first_page=1):
    """Return bounded public pages plus honest continuation/partial evidence."""
    rows, seen, warnings = [], set(), []
    exhausted, pages_read = False, 0
    for page in range(first_page, first_page + max_pages):
        try:
            html = client.get('https://www.luogu.com.cn/problem/list',
                              params={'type': 'luogu', **params, 'page': page})
            parsed = parse_luogu_catalog(html)
            container = page_data(html).get('problems', {})
        except (CrawlError, AIError, ValueError, TypeError, KeyError) as exc:
            code, message = _sync_error(exc)
            warnings.append(message)
            return {'items': rows, 'truncated': True, 'next_page': page,
                    'warnings': warnings, 'error_code': code, 'pages_read': pages_read}
        pages_read += 1
        fresh = [row for row in parsed if row['id'] not in seen]
        if not fresh:
            exhausted = not parsed
            if parsed: warnings.append('官方分页返回重复题目，本次已停止；不能据此认定专题题库已遍历完整')
            break
        rows.extend(fresh)
        seen.update(row['id'] for row in fresh)
        total = container.get('count') if isinstance(container, dict) else None
        per_page = container.get('perPage', 50) if isinstance(container, dict) else 50
        # Honor the upstream page size rather than assume every page has 50 rows.
        if type(total) is int and type(per_page) is int and per_page > 0:
            exhausted = page * per_page >= total
        else:
            exhausted = len(parsed) < 50
        if exhausted:
            break
    return {'items': rows, 'truncated': not exhausted,
            'next_page': 1 if exhausted else first_page + pages_read,
            'warnings': warnings, 'pages_read': pages_read}


def _luogu_pages(client, params, max_pages=3):
    result = _luogu_page_batch(client, params, max_pages=max_pages)
    if not result['items'] and result.get('error_code'):
        raise CrawlError(result['warnings'][0])
    return result['items']


def _discover_luogu_topic(database, client, topic):
    from .training_topics import LUOGU_TOPIC_TAGS
    tags = sorted(tag for tag in topic.get('luogu_tags', LUOGU_TOPIC_TAGS.get(topic['id'], []))
                  if type(tag) is int and tag > 0)
    if not tags and topic['id'].startswith('luogu-'):
        tags = [int(topic['id'][6:])]
    cursor_key = 'specialist_catalog_cursor:luogu:' + topic['id']
    cursor = database.get(cursor_key, {})
    if not isinstance(cursor, dict): cursor = {}
    offset = int(cursor.get('tag_offset', 0)) % max(1, len(tags))
    ordered = tags[offset:] + tags[:offset]
    chosen = ordered[:6]
    queries = []
    for tag in chosen:
        queries.extend([(str(tag) + ':template', {'tag': str(tag), 'keyword': '模板'}, 4 if len(tags) <= 2 else 1),
                        (str(tag), {'tag': str(tag)}, 8 if len(tags) <= 2 else 2)])
    if not queries:
        queries = [('keyword', {'keyword': topic['title']}, 8)]
    rows, warnings, pages, requested_tags = [], [], 0, set()
    page_cursors = cursor.get('pages', {})
    if not isinstance(page_cursors, dict): page_cursors = {}
    page_cursors = dict(page_cursors)
    truncated = len(chosen) < len(tags)
    error_code = None
    for key, params, budget in queries:
        if params.get('tag'): requested_tags.add(params['tag'])
        first_page = page_cursors.get(key, 1)
        if type(first_page) is not int or not 1 <= first_page <= 10000: first_page = 1
        result = _luogu_page_batch(client, params, max_pages=budget, first_page=first_page)
        rows.extend(result['items']); warnings.extend(result['warnings'])
        pages += result['pages_read']; truncated |= result['truncated']
        page_cursors[key] = result['next_page']
        if result.get('error_code'):
            error_code = result['error_code']
            break  # Access challenges/rate limits must not hammer every tag.
    next_offset = offset if error_code else (offset + len(chosen)) % max(1, len(tags))
    database.set(cursor_key, {'tag_offset': next_offset, 'pages': page_cursors})
    return {'items': list({row['id']: row for row in rows}.values()),
            'coverage': f'洛谷专题有界补抓：本次 {pages} 页，{len(requested_tags)}/{len(tags)} 个相关标签；后续刷新轮换标签并继续分页',
            'truncated': truncated, 'warnings': list(dict.fromkeys(warnings)),
            **({'error_code': error_code} if error_code else {})}


_OFFICIAL_TAG_SOURCES = {'codeforces': 'codeforces.problemset.problems',
                         'luogu': 'luogu.official_problem_list',
                         'nowcoder': 'nowcoder.official_problem_list'}
_OFFICIAL_PUBLICATION_SOURCES = {
    'codeforces': {'codeforces.contest.startTimeSeconds'},
    'atcoder': {'atcoder.archive.time', 'atcoder.contest.startTime'},
    'luogu': {'luogu.problem.createTime'}, 'nowcoder': {'nowcoder.problem.publishTime'},
}


def _official_tags_source(platform, row):
    expected = _OFFICIAL_TAG_SOURCES.get(platform)
    if not expected:
        return None
    if row.get('tags_source') == expected:
        return expected
    # These exact signatures were emitted only by our old native public-list
    # parsers. Do not promote arbitrary old tags, AtCoder index tags, or labels
    # obtained from a third-party source into official metadata.
    legacy = {'codeforces': 'Codeforces 官方 rating',
              'luogu': '洛谷官方难度等级（与 CF rating 不可比）'}
    if (platform in legacy and not row.get('tags_source') and row.get('candidate_source') == '官方题库'
            and row.get('difficulty_source') == legacy.get(platform)):
        return expected
    return None


def _merge_catalog_metadata(platform, previous, row):
    merged = ({**previous, **{key: value for key, value in row.items() if value is not None}}
              if previous else dict(row))
    old_tags, new_tags = previous.get('tags', []), row.get('tags', [])
    old_source, new_source = _official_tags_source(platform, previous), _official_tags_source(platform, row)
    if old_tags and (not new_tags or old_source and not new_source):
        merged['tags'] = old_tags
        if old_source: merged['tags_source'] = old_source
        else: merged.pop('tags_source', None)
    elif old_tags and new_tags and old_source == new_source and old_source:
        # A filtered list may expose only a subset of the real native tags.
        # Merge only evidence from the same platform's official list source.
        tags, seen = [], set()
        for tag in old_tags + new_tags:
            if type(tag) not in (str, int): continue
            key = str(tag).casefold()
            if key not in seen:
                seen.add(key); tags.append(tag)
        merged['tags'] = tags
        merged['tags_source'] = old_source
    elif new_tags:
        if new_source: merged['tags_source'] = new_source
        else: merged.pop('tags_source', None)
    if row.get('difficulty') is None and previous.get('difficulty') is not None:
        merged['difficulty_source'] = previous.get('difficulty_source')
    official_dates = _OFFICIAL_PUBLICATION_SOURCES.get(platform, set())
    old_official = previous.get('published_at_source') in official_dates and publication_time(previous.get('published_at'))
    new_official = row.get('published_at_source') in official_dates and publication_time(row.get('published_at'))
    # A new third-party (or invalid/future) timestamp cannot erase an already
    # verified official date and thereby make a formerly eligible task vanish.
    if (old_official and not new_official) or not row.get('published_at') and previous.get('published_at'):
        for key in ('published_at', 'published_at_source', 'publication_note'):
            if key in previous: merged[key] = previous[key]
    return merged


def _store_catalog_rows(database, platform, rows):
    """Merge discovery batches without erasing previous official coverage."""
    unique = {row['id']: row for row in rows if row.get('platform') == platform}
    with database.connect() as db:
        existing = {row[0]: json.loads(row[1]) for row in db.execute(
            'SELECT id,data FROM problem_catalog WHERE platform=?', (platform,)) if row[0] in unique}
        for pid, row in unique.items():
            unique[pid] = _merge_catalog_metadata(platform, existing.get(pid, {}), row)
        db.executemany('INSERT OR REPLACE INTO problem_catalog VALUES(?,?,?)',
            [(platform, pid, json.dumps(row, ensure_ascii=False)) for pid, row in unique.items()])
    return len(unique)


def _with_atcoder_official_practice(client, found):
    """One official ACL task-list request; supplemental failure keeps the index."""
    found = {**found, 'items': list(found['items']), 'warnings': list(found.get('warnings', []))}
    try:
        practice = _atcoder_practice(client)
        if not practice: raise CrawlError('官方练习赛任务列表返回空内容')
    except (CrawlError, AIError, ValueError, TypeError, KeyError):
        found['warnings'].append('AtCoder 官方 ACL 练习赛补抓失败；已保留公开索引与旧缓存，可稍后重试')
        found['partial_failure'] = True
        return found
    unique = {row['id']: row for row in found['items']}
    for row in practice:
        unique[row['id']] = _merge_catalog_metadata('atcoder', unique.get(row['id'], {}), row)
    found['items'] = list(unique.values())
    found['coverage'] += f'；另补充官方 ACL 练习赛 {len(practice)} 道（官方页面提供时保留真实开始时间）'
    return found


def _official_nowcoder_items(rows):
    # This adapter receives rows only from the fixed official ac.nowcoder.com
    # list parser. AtCoder's unrelated third-party index is never labelled here.
    return [{**row, 'tags_source': 'nowcoder.official_problem_list'} for row in rows]


class _DiscoveryDeadline:
    def __init__(self, client, deadline):
        self.client, self.deadline = client, deadline

    def _call(self, method, *args, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CrawlError('Timeout: 公开专题补抓已达到本轮时间预算')
        if hasattr(self.client, 'timeout'):
            self.client.timeout = min(12, remaining)
        return getattr(self.client, method)(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self._call('get', *args, **kwargs)

    def json(self, *args, **kwargs):
        return self._call('json', *args, **kwargs)

    def close(self):
        return self.client.close()


def refresh_specialist_candidates(database, accounts, topic, on_progress=None):
    """Bounded discovery for this user's verified platforms; no credentials used."""
    verified_set = set(verified_platforms(accounts))
    verified = [platform for platform in PLATFORMS if platform in verified_set]
    result = [{'platform': platform, 'status': 'queued', 'count': 0} for platform in verified]
    result.extend({'platform': platform, 'status': 'skipped', 'count': 0,
                   'coverage': '未请求此平台：仅当前用户已验证登录的平台参与专题选题',
                   'message': '尚无有效登录检测；请在连接与设置检测登录后重试',
                   'truncated': False, 'warnings': []}
                  for platform in PLATFORMS if platform not in verified)
    deadline = time.monotonic() + 150

    def publish():
        if on_progress:
            on_progress({'platforms': [dict(row) for row in result],
                         'completed_platforms': sum(row['status'] not in ('queued', 'running') for row in result),
                         'total_platforms': len(result), 'count': sum(row.get('count', 0) for row in result)})

    publish()
    for index, platform in enumerate(verified):
        cache_key = 'specialist_catalog:' + platform + ':' + topic['id']
        status_key = 'specialist_catalog_status:' + platform + ':' + topic['id']
        previous = database.get(status_key, {})
        # Legacy versions cached empty batches without a status record; do not
        # inherit those poisoned success TTLs after upgrading this code.
        if (int(database.get(cache_key, 0)) > int(time.time()) - 300
                and previous.get('status') == 'updated'):
            result[index] = {**previous, 'platform': platform, 'status': 'cached',
                             'message': '使用最近 5 分钟的专题公开元信息缓存；未再次请求平台'}
            publish()
            continue
        result[index]['status'] = 'running'
        publish()
        # Reserve an equal share of remaining time for each later platform.
        # A slow source must not starve the other verified account catalogs.
        remaining = max(0, deadline - time.monotonic())
        platform_deadline = min(deadline, time.monotonic() + remaining / (len(verified) - index))
        client = _DiscoveryDeadline(Client(timeout=12, retries=0), platform_deadline)
        try:
            if platform == 'codeforces':
                if int(database.get('catalog_updated_at:codeforces', 0)) > int(time.time()) - 300:
                    with database.connect() as db:
                        count = db.execute("SELECT count(*) FROM problem_catalog WHERE platform='codeforces'").fetchone()[0]
                    found = {'items': [] if count else _cf(client), 'existing_count': count,
                             'coverage': 'Codeforces 最近同步的官方全量已结束且具有 rating 的公开题库',
                             'truncated': False, 'warnings': []}
                else:
                    found = {'items': _cf(client), 'coverage': 'Codeforces 官方全量已结束且具有 rating 的公开题库',
                             'truncated': False, 'warnings': []}
            elif platform == 'luogu':
                found = _discover_luogu_topic(database, client, topic)
            elif platform == 'atcoder':
                from .expanded_topic_sources import discover_atcoder
                found = _with_atcoder_official_practice(client, discover_atcoder(client, topic))
                found['items'] = _enrich_atcoder_difficulties(client, found['items'])
            else:
                from .expanded_topic_sources import discover_nowcoder
                found = discover_nowcoder(client, topic)
                found['items'] = _official_nowcoder_items(found['items'])
            count = _store_catalog_rows(database, platform, found['items'])
            usable_count = count or int(found.get('existing_count', 0))
            status = 'partial' if (found.get('error_code') or found.get('partial_failure')) and usable_count else 'updated' if usable_count else 'failed' if found.get('error_code') else 'empty'
            row = {'platform': platform, 'status': status, 'count': count,
                   'coverage': found['coverage'], 'truncated': bool(found.get('truncated')),
                   'warnings': found.get('warnings', []), 'updated_at': int(time.time())}
            if found.get('error_code'): row['error_code'] = found['error_code']
            if not usable_count: row['message'] = '本次公开来源没有取得可用题目；旧缓存保留，可重试；不代表平台没有该专题题目'
            if usable_count and not found.get('error_code') and not found.get('partial_failure'):
                database.set(cache_key, int(time.time()))
                if platform == 'codeforces': database.set('catalog_updated_at:codeforces', int(time.time()))
            database.set(status_key, row)
            result[index] = row
        except (CrawlError, AIError, ValueError, TypeError, KeyError) as exc:
            code, message = _sync_error(exc)
            row = {'platform': platform, 'status': 'failed', 'count': 0,
                   'error_code': code, 'message': message,
                   'coverage': '公开元信息补抓失败，保留既有缓存', 'truncated': True, 'warnings': []}
            database.set(status_key, row)
            result[index] = row
        finally:
            client.close()
        publish()
    return result


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
            tags_source='nowcoder.official_problem_list',
            publication_note='官方题库列表未提供可靠发布日期'))
    return result


def _nowcoder(client):
    from .expanded_topic_sources import discover_nowcoder
    found = discover_nowcoder(client, None)
    client.catalog_discovery = {key: value for key, value in found.items() if key != 'items'}
    return _official_nowcoder_items(found['items'])


DISCOVERY = {'codeforces': _cf, 'atcoder': _atcoder, 'luogu': _luogu, 'nowcoder': _nowcoder}


def _sync_error(exc):
    """Only structured, allowlisted upstream facts reach users; never exception text."""
    message = str(exc)
    http = re.search(r'\bHTTP (\d{3})\b', message)
    if http:
        code = int(http.group(1))
        if code == 429: return 'rate_limited', '官方平台请求过于频繁（HTTP 429），请稍后重试；旧缓存仍可使用'
        if code in (401, 403): return 'access_denied', f'官方平台拒绝访问（HTTP {code}），可能需要浏览器验证或服务器访问受限；旧缓存仍可使用'
        if code >= 500: return 'upstream_unavailable', f'官方平台服务暂时异常（HTTP {code}）；请稍后重试'
        return 'http_error', f'官方平台返回 HTTP {code}；已保留旧缓存'
    if 'Timeout' in message: return 'timeout', '连接官方平台超时，请稍后重试或检查服务器到该平台的网络'
    if 'SSLError' in message: return 'tls_error', '连接官方平台时证书校验失败，请检查服务器时间和证书环境'
    if '网络请求失败' in message: return 'network_error', '无法连接官方平台，请检查服务器网络、DNS 或该平台的访问限制'
    if 'JSON' in message: return 'unexpected_response', '官方平台返回登录页、验证页或非预期内容，未取得可用题库'
    if '空内容' in message: return 'empty_catalog', '官方列表未返回支持的题目，可能存在访问验证或页面结构变化'
    return 'source_changed', '官方题库数据结构不兼容或页面发生变化；已保留旧缓存，请反馈此平台同步问题'


def refresh_catalog(database, platform=None, on_progress=None):
    """Publish each platform's progress and retain its old cache on any source failure."""
    if platform is not None and platform not in PLATFORMS: raise AIError('无效题库平台')
    names = (platform,) if platform else PLATFORMS
    started = int(time.time())
    results = [{'platform': name, 'status': 'queued', 'count': 0} for name in names]

    def snapshot():
        return {'count': sum(row['count'] for row in results),
                'platforms': [dict(row) for row in results],
                'complete': all(row['status'] == 'updated' for row in results),
                'completed_platforms': sum(row['status'] in ('updated', 'partial', 'failed') for row in results),
                'total_platforms': len(names), 'started_at': started, 'updated_at': int(time.time())}

    def publish():
        if on_progress: on_progress(snapshot())

    publish()
    for index, name in enumerate(names):
        results[index]['status'] = 'running'
        publish()
        client = Client(timeout=15, retries=0)
        try:
            rows = DISCOVERY[name](client)
            if not rows: raise CrawlError('官方题库返回空内容或结构变化，保留原缓存')
            _store_catalog_rows(database, name, rows)
            now = int(time.time()); database.set('catalog_updated_at:' + name, now)
            if name == 'codeforces': database.set('catalog_updated_at', now)
            results[index] = {'platform': name,
                'status': 'partial' if getattr(client, 'catalog_discovery', {}).get('partial_failure') else 'updated', 'count': len(rows),
                'dated_count': sum(bool(row.get('published_at')) for row in rows), 'updated_at': now,
                'message': '已补充公开题库元信息并保留既有缓存；是否参与 AI 选题仍需满足登录与筛选条件',
                'coverage': getattr(client, 'catalog_discovery', {}).get('coverage',
                    'Codeforces 已结束且具有 rating 的官方公开题库' if name == 'codeforces' else '洛谷普通列表及模板列表各最多 8 页；并非全站镜像'),
                'truncated': bool(getattr(client, 'catalog_discovery', {}).get('truncated', name != 'codeforces')),
                'warnings': getattr(client, 'catalog_discovery', {}).get('warnings', [])}
        except (CrawlError, AIError, KeyError, ValueError, TypeError) as exc:
            code, message = _sync_error(exc)
            results[index] = {'platform': name, 'status': 'failed', 'count': 0,
                'error_code': code, 'message': message, 'updated_at': int(time.time())}
        finally: client.close()
        publish()
    database.set('catalog_refresh_status', results)
    database.set('catalog_refresh_at', int(time.time()))
    return snapshot()


def _canonical(platform, pid):
    return pid if pid.startswith(platform + ':') else platform + ':' + pid


def _pool(database, platform, include_excluded=False, allow_attempted=False):
    with database.connect() as db:
        rows = db.execute('SELECT data FROM problem_catalog WHERE platform=?', (platform,)).fetchall()
        cached = db.execute('SELECT data FROM wb_problems WHERE id LIKE ?', (platform + ':%',)).fetchall()
        submission_query = "SELECT DISTINCT platform,problem_id FROM submissions" + (" WHERE verdict='AC'" if allow_attempted else '')
        excluded = {_canonical(row[0], row[1]) for row in db.execute(submission_query)}
        excluded.update(_canonical(row[0], row[1]) for row in db.execute("SELECT platform,problem_id FROM practice_feedback WHERE status IN ('done','skip')"))
    with database.connect() as db:
        if not allow_attempted:
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
    return list(merged.values()) if include_excluded else [p for p in merged.values() if p['id'] not in excluded]


def _date_floor(value):
    if not value: return None
    try: return datetime.combine(date.fromisoformat(str(value)), datetime.min.time(), ZoneInfo('Asia/Shanghai')).timestamp()
    except ValueError: raise AIError('题目最早发布日期必须为 YYYY-MM-DD') from None


def candidates(database, minimum=800, maximum=1600, tags=None, limit=60, min_contest=0,
               platform='codeforces', published_after=None, predicate=None, allow_attempted=False,
               prioritize=None):
    """Low-level per-platform query; UI/AI must use recommendation_candidates."""
    if platform not in PLATFORMS: raise AIError('请选择有效的推荐平台')
    floor = _date_floor(published_after); prefs = database.get('training', {})
    avoided = set(prefs.get('avoid_tags', [])); result = []
    for p in _pool(database, platform, allow_attempted=allow_attempted):
        if platform == 'codeforces' and (not isinstance(p.get('difficulty'), int) or not minimum <= p['difficulty'] <= maximum or p.get('contest_id', 0) < min_contest): continue
        if floor is not None and (p.get('published_at_source') not in PUBLISHED_SOURCES or
                not publication_time(p.get('published_at')) or p['published_at'] < floor): continue
        if tags and not set(tags) & set(p.get('tags', [])): continue
        if p.get('tags') and set(p['tags']).issubset(avoided): continue
        if predicate is not None and not predicate(p): continue
        result.append(p)
    secrets.SystemRandom().shuffle(result)
    if prioritize is not None:
        result.sort(key=lambda row: not prioritize(row))
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
                              limit=60, published_after=None, excluded_ids=None, preferred_ids=None,
                              predicate=None, allow_attempted=False, prioritize=None):
    """Mix only recently verified sessions; balanced shuffle prevents CF pool domination."""
    prefs = database.get("training", {})
    if requires_publication_date(prefs): raise AIError(LEGACY_DATE_NOTICE)
    platforms = verified_platforms(accounts)
    if not platforms: raise AIError('没有已验证登录的平台；请到连接与设置保存账号并点击“检测登录状态”（检测有效期 5 分钟）')
    floor = max(filter(None, (published_after, prefs.get('published_after'))), default=None)
    excluded = set(excluded_ids or [])
    pools = [[row for row in candidates(database, minimum, maximum, tags, 100000 if preferred_ids else max(60, limit),
              platform=platform, published_after=floor, predicate=predicate,
              allow_attempted=allow_attempted, prioritize=prioritize) if row['id'] not in excluded] for platform in platforms]
    preferred = set(preferred_ids or [])
    if prioritize is not None:
        preferred.update(row['id'] for pool in pools for row in pool if prioritize(row))
    result = [row for pool in pools for row in pool if row['id'] in preferred][:limit]
    pools = [[row for row in pool if row['id'] not in preferred] for pool in pools]
    secrets.SystemRandom().shuffle(pools)
    while any(pools) and len(result) < limit:
        for pool in pools:
            if pool and len(result) < limit: result.append(pool.pop())
    return result


def recommendation_status(database, accounts, published_after=None, minimum=800, maximum=3500, tags=None):
    prefs = database.get('training', {})
    requires_date = requires_publication_date(prefs)
    verified = set(verified_platforms(accounts)); floor = max(filter(None, (published_after, prefs.get('published_after'))), default=None)
    floor_ts = _date_floor(floor)
    avoided = set(prefs.get('avoid_tags', []))
    rows = []
    for platform in PLATFORMS:
        raw = _pool(database, platform, include_excluded=True)
        pool = _pool(database, platform)
        unknown = sum(not publication_time(p.get('published_at')) or p.get('published_at_source') not in PUBLISHED_SOURCES for p in pool)
        counts = {'attempted_or_reserved': len(raw) - len(pool), 'unknown_date': 0, 'before_date': 0,
                  'difficulty': 0, 'tags': 0, 'avoided_tags': 0}
        eligible = []
        for problem in pool:
            # Keep the same exclusive rejection order as candidates(): CF
            # difficulty first, then date, requested tags, and avoided tags.
            if platform == 'codeforces' and (not isinstance(problem.get('difficulty'), int) or not minimum <= problem['difficulty'] <= maximum):
                counts['difficulty'] += 1; continue
            if floor_ts is not None:
                if not publication_time(problem.get('published_at')) or problem.get('published_at_source') not in PUBLISHED_SOURCES:
                    counts['unknown_date'] += 1; continue
                if problem['published_at'] < floor_ts:
                    counts['before_date'] += 1; continue
            if tags and not set(tags) & set(problem.get('tags', [])):
                counts['tags'] += 1; continue
            if problem.get('tags') and set(problem['tags']).issubset(avoided):
                counts['avoided_tags'] += 1; continue
            eligible.append(problem)
        reasons = []
        if platform not in verified: reasons.append('登录尚未通过检测或检测已过期：到连接与设置点击“检测登录状态”；仅保存 Cookie 不会参与选题')
        if requires_date: reasons.append(LEGACY_DATE_NOTICE)
        if not raw: reasons.append('本地尚无此平台的官方题库缓存：点击“同步平台题库”；同步公开列表不需要登录，也不会检测 Cookie')
        labels = {'attempted_or_reserved': '题已提交、已完成、已跳过或被对战占用', 'unknown_date': '题缺少可靠发布日期，按日期限制排除',
                  'before_date': '题早于最早发布日期', 'difficulty': '题不在 CF 难度区间内', 'tags': '题不匹配所选标签', 'avoided_tags': '题仅含已屏蔽标签'}
        reasons.extend(f'{count} {labels[key]}' for key, count in counts.items() if count)
        active_count = len(eligible) if platform in verified and not requires_date else 0
        rows.append({'platform': platform, 'login_verified': platform in verified,
            'cached_count': len(pool), 'total_cached_count': len(raw), 'unknown_publication_count': unknown,
            'date_eligible_count': 0 if requires_date else len(candidates(database, minimum=800, maximum=3500, limit=100000, platform=platform, published_after=floor)),
            'eligible_count': active_count, 'filter_counts': counts, 'reasons': reasons,
            'updated_at': database.get('catalog_updated_at:' + platform),
            'notice': (LEGACY_DATE_NOTICE if requires_date else '') or ('需要重新检测登录状态，保存 Cookie 不代表登录成功' if platform not in verified else '') or
                      ('部分题目缺少可靠发布日期；设置日期下限后将排除这些题目' if unknown else '')})
    return {'platforms': rows, 'published_after': floor, 'requires_publication_date': requires_date,
            'candidate_count': sum(row['eligible_count'] for row in rows),
            'notice': LEGACY_DATE_NOTICE if requires_date else '', 'refresh': database.get('catalog_refresh_status', []),
            'last_refresh_at': database.get('catalog_refresh_at')}
