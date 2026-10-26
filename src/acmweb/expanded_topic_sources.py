"""Bounded public discovery of AtCoder and Nowcoder candidates.

This module only reads public, allowlisted pages. It never logs in, never sends
a platform cookie to a third-party host, never fetches statements, and never
invents tags or publication dates. Discovery is intentionally bounded: callers
receive an honest ``coverage`` string, a ``truncated`` flag and fixed warnings.

AtCoder does not publish per-task tags or difficulty and has no topic search.
The full public task index is therefore read from the fixed AtCoder Problems
(Kenkoooo) resources; official AtCoder contest-archive pages are used, within a
page budget, to verify recent contest start times. Kenkoooo start times are
labelled as third-party evidence and never presented as an official archive
check. Nowcoder exposes no per-problem publication date; its list page does
expose official knowledge-point tags and an official ``keyword`` search.
"""
import json
import re
import time
from bisect import bisect_left, bisect_right

from bs4 import BeautifulSoup
from ojcrawler.models import CrawlError

from .codex_bridge import AIError
from .problem_sources import normalize, source_url

ATCODER_ORIGIN = 'https://atcoder.jp'
ATCODER_ARCHIVE = ATCODER_ORIGIN + '/contests/archive'
KENKOO_PROBLEMS = 'https://kenkoooo.com/atcoder/resources/problems.json'
KENKOO_CONTESTS = 'https://kenkoooo.com/atcoder/resources/contests.json'
KENKOO_MODELS = 'https://kenkoooo.com/atcoder/resources/problem-models.json'
NOWCODER_LIST = 'https://ac.nowcoder.com/acm/problem/list'

MAX_JSON_BYTES = 8_000_000
MAX_HTML_BYTES = 2_000_000
MAX_ATCODER_ROWS = 12_000
MAX_ATCODER_INDEX_ROWS = 20_000
MAX_ARCHIVE_PAGES = 3          # 50 official contests per page
MAX_NOWCODER_PAGES = 8         # 50 official problems per page
MAX_TOPIC_KEYWORD_PAGES = 1
MAX_TOPIC_TAG_PAGES = 3
MAX_TOPIC_QUERIES = 2
MAX_TOPIC_TAGS = 2
NOWCODER_PAGE_SIZE = 50

_ATCODER_TASK = re.compile(r'[a-z0-9]+(?:_[a-z0-9]+)+')
_CONTEST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,60}')
_NOWCODER_ID = re.compile(r'[1-9][0-9]{0,11}')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]+')
_WHITESPACE = re.compile(r'\s+')
_TOTAL = re.compile(r'共\s*([0-9][0-9,]*)\s*条')

# Fixed, user-safe messages. Upstream text, exception strings, HTML and user
# supplied topic labels must never be copied into warnings.
W_ATCODER_INDEX = 'AtCoder 公开题目索引获取失败；本次未生成 AtCoder 候选，旧题库保持不变'
W_ATCODER_INDEX_SHAPE = 'AtCoder 公开题目索引结构不兼容或超过安全上限，已停止该来源'
W_ATCODER_EMPTY = 'AtCoder 公开索引未返回可用题目；未猜测题号、标签或日期'
W_ATCODER_SKIPPED = 'AtCoder 索引中有 {count} 道题号不合规范或无题名，已跳过且未猜测元数据'
W_ATCODER_LIMIT = 'AtCoder 公开索引超过单次上限，已按 {limit} 道截断；未声称扫描全量'
W_ATCODER_DATES = 'AtCoder 有 {count} 道题无法从官方归档或公开比赛索引核实发布日期，发布日期保持未知'
W_ATCODER_THIRD_PARTY_DATES = 'AtCoder 有 {count} 道题日期来自 Kenkoooo 第三方比赛索引而非官方归档，已标注来源'
W_ATCODER_ARCHIVE = 'AtCoder 官方比赛归档部分请求失败；仅保留已核实的官方日期，其余日期按可用来源标注'
W_ATCODER_CONTESTS = 'AtCoder 公开比赛索引获取失败；仅保留官方归档在本次页数内核实的日期'
W_ATCODER_MODELS = 'AtCoder 公开难度估计获取失败；难度保持未标定，不使用题号或标签推断'
W_NOWCODER_PAGE = '牛客第 {page} 页请求失败，已保留此前页面的候选结果'
W_NOWCODER_FIRST = '牛客官方题库列表获取失败；本次未生成牛客候选，旧题库保持不变'
W_NOWCODER_SHAPE = '牛客题库页面结构不兼容或超过安全上限，已停止该来源并保留已有结果'
W_NOWCODER_SKIPPED = '牛客页面有 {count} 行不符合官方题目链接格式，已跳过且未猜测元数据'
W_NOWCODER_NO_DATE = '牛客官方题库列表未提供逐题发布日期；发布日期保持未知，未按题号推断'
W_NOWCODER_TRUNCATED = '牛客官方题库共 {total} 道，本次按上限只读取了 {count} 道，未声称全量扫描'
W_NOWCODER_TOPIC_EMPTY = '牛客官方关键词检索没有可核实的题名或标签匹配；未按请求推断标签'
W_NOWCODER_TOPIC_FALLBACK = '主题缺少可核实的检索词，已退回牛客通用题库分页；未猜测标签'
W_NOWCODER_TOPIC_TRUNCATED = '牛客官方标签筛选共 {total} 道，本次按上限读取 {count} 道，未声称全量扫描'
W_TOPIC_INVALID = '主题参数无效，已退回通用公开题库发现；未按无效主题过滤'


class _SourceError(Exception):
    """Internal signal for a suspicious or incompatible upstream response."""


def _clean(value, limit=200):
    if not isinstance(value, str):
        return ''
    value = _CONTROL.sub(' ', value)
    value = _WHITESPACE.sub(' ', value).strip()
    return value[:limit]


def _timestamp(value):
    """Accept an explicit upstream timestamp only; never a year or contest id."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            if value > 10 ** 12:
                value /= 1000
            value = int(value)
            return value if 946684800 <= value <= time.time() + 366 * 86400 else None
        if isinstance(value, str):
            from datetime import datetime
            value = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', value.strip().replace('Z', '+00:00'))
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return None
            return _timestamp(parsed.timestamp())
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _get_json(client, url):
    if not callable(getattr(client, 'get', None)):
        raise _SourceError('client cannot fetch public pages')
    try:
        text = client.get(url)
    except (CrawlError, AIError):
        raise
    if not isinstance(text, str) or len(text) > MAX_JSON_BYTES:
        raise _SourceError('unsafe public JSON response')
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise _SourceError('incompatible public JSON response') from exc


def _get_html(client, url, params=None):
    if not callable(getattr(client, 'get', None)):
        raise _SourceError('client cannot fetch public pages')
    try:
        text = client.get(url, params=params) if params is not None else client.get(url)
    except (CrawlError, AIError):
        raise
    if not isinstance(text, str) or len(text) > MAX_HTML_BYTES:
        raise _SourceError('unsafe public HTML response')
    return text


def _result(items, coverage, truncated=False, warnings=()):
    unique = {}
    for item in items:
        if isinstance(item, dict) and item.get('id'):
            unique.setdefault(item['id'], item)
    return {'items': list(unique.values()), 'coverage': coverage,
            'truncated': bool(truncated),
            'warnings': list(dict.fromkeys(message for message in warnings if message))}


def _topic_terms(topic):
    """Return a few bounded search terms, or None when the topic is unusable."""
    if topic is None:
        return []
    if not isinstance(topic, dict):
        return None
    candidates = [topic.get('title')]
    for key in ('text_keywords', 'keywords'):
        value = topic.get(key)
        if isinstance(value, (list, tuple)):
            candidates.extend(value)
    terms, seen = [], set()
    for value in candidates:
        term = _clean(value, 24)
        if not term or (term.isascii() and len(term) < 2):
            continue
        folded = term.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        terms.append(term)
        if len(terms) >= MAX_TOPIC_QUERIES:
            break
    return terms


def _mentions(text, term):
    text, term = text.casefold(), term.casefold()
    if term.isascii():
        return re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text) is not None
    return term in text


def _has_evidence(item, terms):
    text = ' '.join([str(item.get('title', '')), *item.get('tags', [])])
    return any(_mentions(text, term) for term in terms)


def _atcoder_item(problem_id, contest_id, title):
    pid = normalize('atcoder:' + problem_id)
    return {
        'id': pid, 'platform': 'atcoder', 'problem_id': problem_id,
        'title': title, 'url': f'{ATCODER_ORIGIN}/contests/{contest_id}/tasks/{problem_id}',
        'difficulty': None, 'native_difficulty': None,
        'difficulty_source': 'AtCoder 官方未标定难度（不使用题号字母推断）',
        'tags': [], 'candidate_source': 'AtCoder Problems（Kenkoooo）公开题目索引（第三方）',
        'published_at': None, 'published_at_source': None,
        'contest_id': contest_id,
    }


def _parse_atcoder_problems(data):
    if not isinstance(data, list) or len(data) > MAX_ATCODER_INDEX_ROWS:
        raise _SourceError('incompatible AtCoder problem index')
    rows, skipped = [], 0
    for entry in data[:MAX_ATCODER_ROWS]:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        problem_id, contest_id = entry.get('id'), entry.get('contest_id')
        if (not isinstance(problem_id, str) or not _ATCODER_TASK.fullmatch(problem_id)
                or not isinstance(contest_id, str) or not _CONTEST.fullmatch(contest_id)):
            skipped += 1
            continue
        title = _clean(entry.get('name') or entry.get('title'), 300)
        if not title:
            skipped += 1
            continue
        rows.append(_atcoder_item(problem_id, contest_id, title))
    return rows, skipped


def _parse_atcoder_contests(data):
    if not isinstance(data, list) or len(data) > 20_000:
        raise _SourceError('incompatible AtCoder contest index')
    dates = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        contest_id, stamp = entry.get('id'), entry.get('start_epoch_second')
        if not isinstance(contest_id, str) or not _CONTEST.fullmatch(contest_id):
            continue
        stamp = _timestamp(stamp)
        if stamp:
            dates[contest_id] = stamp
    return dates


def _parse_archive(html):
    soup = BeautifulSoup(html, 'html.parser')
    dates = {}
    for row in soup.select('tbody tr'):
        link, stamp = row.select_one('a[href^="/contests/"]'), row.select_one('time')
        if not link or not stamp:
            continue
        match = re.fullmatch(r'/contests/([A-Za-z0-9][A-Za-z0-9_-]{0,60})/?', link.get('href', ''))
        timestamp = _timestamp(stamp.get_text(' ', strip=True))
        if match and timestamp:
            dates[match[1]] = timestamp
    return dates


def _parse_models(data):
    if not isinstance(data, dict) or len(data) > 60_000:
        raise _SourceError('incompatible AtCoder difficulty index')
    models = {}
    for problem_id, row in data.items():
        if (isinstance(problem_id, str) and isinstance(row, dict) and not row.get('is_experimental')
                and type(row.get('difficulty')) is int and -10000 <= row['difficulty'] <= 10000):
            models[problem_id] = row['difficulty']
    return models


def _apply_models(rows, models):
    if not models:
        return 0
    population = sorted(models.values())
    for row in rows:
        difficulty = models.get(row['problem_id'])
        if difficulty is None:
            continue
        row['atcoder_problem_difficulty'] = difficulty
        row['atcoder_statistics_source'] = KENKOO_MODELS
        row['atcoder_statistics_population'] = len(population)
        row['atcoder_difficulty_percentile'] = (
            bisect_left(population, difficulty) + bisect_right(population, difficulty)
        ) / (2 * len(population))
        row['difficulty_source'] = ('AtCoder 官方未标定难度（不使用题号字母推断）；'
                                    'Kenkoooo 公开估计仅作旁证，与 CF rating 不可比')
    return len(models)


def discover_atcoder(client, topic=None):
    """Return bounded AtCoder candidates with honest third-party provenance.

    ``topic`` is accepted for a uniform cross-platform contract. AtCoder offers
    no official tag/topic search, so no tag is created from the request; the
    full public task index is returned and the caller's existing title/template
    evidence rules decide relevance.
    """
    warnings = []
    terms = _topic_terms(topic)
    if terms is None:
        warnings.append(W_TOPIC_INVALID)
    try:
        index = _get_json(client, KENKOO_PROBLEMS)
        rows, skipped = _parse_atcoder_problems(index)
    except (CrawlError, AIError, _SourceError, ValueError, TypeError, KeyError, AttributeError):
        return _result([], 'AtCoder：公开题目索引不可用；未生成候选', False, [W_ATCODER_INDEX])
    if not rows:
        return _result([], 'AtCoder：公开题目索引没有可用题目；未生成候选', False,
                       warnings + [W_ATCODER_EMPTY])
    if skipped:
        warnings.append(W_ATCODER_SKIPPED.format(count=skipped))

    dates, sources = {}, {}
    try:
        for contest_id, stamp in _parse_atcoder_contests(_get_json(client, KENKOO_CONTESTS)).items():
            dates[contest_id] = stamp
            sources[contest_id] = 'kenkoooo.contests.start_epoch_second'
    except (CrawlError, AIError, _SourceError, ValueError, TypeError, KeyError, AttributeError):
        warnings.append(W_ATCODER_CONTESTS)

    official = {}
    for page in range(1, MAX_ARCHIVE_PAGES + 1):
        try:
            parsed = _parse_archive(_get_html(client, ATCODER_ARCHIVE,
                                              params={'lang': 'en', 'page': page}))
        except (CrawlError, AIError, _SourceError, ValueError, TypeError, KeyError, AttributeError):
            warnings.append(W_ATCODER_ARCHIVE)
            break
        if not parsed:
            break
        official.update(parsed)
    for contest_id, stamp in official.items():
        dates[contest_id] = stamp
        sources[contest_id] = 'atcoder.archive.time'

    for row in rows:
        contest_id = row['contest_id']
        row['published_at'] = dates.get(contest_id)
        row['published_at_source'] = sources.get(contest_id) if row['published_at'] else None

    try:
        models = _parse_models(_get_json(client, KENKOO_MODELS))
    except (CrawlError, AIError, _SourceError, ValueError, TypeError, KeyError, AttributeError):
        warnings.append(W_ATCODER_MODELS)
        models = {}
    _apply_models(rows, models)

    official_matched = sum(1 for row in rows
                           if row['published_at_source'] == 'atcoder.archive.time')
    third_party = sum(1 for row in rows
                      if row['published_at_source'] == 'kenkoooo.contests.start_epoch_second')
    unknown = sum(1 for row in rows if not row['published_at'])
    if third_party:
        warnings.append(W_ATCODER_THIRD_PARTY_DATES.format(count=third_party))
    if unknown:
        warnings.append(W_ATCODER_DATES.format(count=unknown))

    truncated = len(index) > MAX_ATCODER_ROWS
    if truncated:
        rows = rows[:MAX_ATCODER_ROWS]
        warnings.append(W_ATCODER_LIMIT.format(limit=MAX_ATCODER_ROWS))
    coverage = (f'AtCoder：公开题目索引 {len(rows)} 道（Kenkoooo 第三方索引）；'
                f'官方归档核实日期 {official_matched} 道；第三方比赛日期 {third_party} 道；'
                f'发布日期未知 {unknown} 道；官方未提供标签/主题检索，tags 保持为空')
    return _result(rows, coverage, truncated, warnings)


def _nowcoder_item(key, title, native, tags):
    pid = normalize('nowcoder:' + key)
    return {
        'id': pid, 'platform': 'nowcoder', 'problem_id': key,
        'title': title, 'url': source_url(pid),
        'difficulty': None, 'native_difficulty': native,
        'difficulty_source': ('牛客官方难度（与 CF rating 不可比）' if native is not None
                              else '牛客官方题库未提供难度'),
        'tags': tags, 'candidate_source': '牛客官方题库列表（含官方知识点标签）',
        'published_at': None, 'published_at_source': None,
    }


def _parse_nowcoder_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    rows, skipped, tag_map = [], 0, {}
    for row in soup.select('tr[data-problemid]'):
        key = row.get('data-problemid') or row.get('data-problemId') or ''
        link = row.select_one('a.title[href]')
        if (not _NOWCODER_ID.fullmatch(str(key)) or not link
                or link.get('href') != '/acm/problem/' + str(key)):
            skipped += 1
            continue
        title = _clean(link.get_text(' ', strip=True), 300)
        if not title:
            skipped += 1
            continue
        cells = row.find_all('td', recursive=False)
        native = _clean(cells[2].get_text(strip=True), 12) if len(cells) > 2 else ''
        native = int(native) if native.isdigit() and 0 <= int(native) <= 10_000 else None
        tags, seen = [], set()
        for tag in row.select('a.tag-label[data-id], a[href*="tagId="]'):
            name = _clean(tag.get_text(' ', strip=True), 40)
            # Keep only published knowledge-point names. A bare number is an
            # identifier, not verified tag evidence, so it is not surfaced.
            if not name or name.isdigit() or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            tags.append(name)
            raw_id = str(tag.get('data-id') or '')
            if not raw_id.isdigit():
                found = re.search(r'tagId=(\d+)', str(tag.get('href') or ''))
                raw_id = found[1] if found else ''
            if raw_id.isdigit():
                known = tag_map.setdefault(name.casefold(), [])
                if raw_id not in known:
                    known.append(raw_id)
        rows.append(_nowcoder_item(str(key), title, native, tags))
    match = _TOTAL.search(html)
    total = int(match[1].replace(',', '')) if match else None
    return rows, total, skipped, tag_map


def _nowcoder_pages(client, params, max_pages, warnings):
    rows, total, pages, skipped, capped, tag_map = [], None, 0, 0, False, {}
    for page in range(1, max_pages + 1):
        try:
            html = _get_html(client, NOWCODER_LIST, params={**params, 'page': page})
        except (CrawlError, AIError, _SourceError, ValueError, TypeError, KeyError, AttributeError):
            warnings.append(W_NOWCODER_FIRST if page == 1 else W_NOWCODER_PAGE.format(page=page))
            break
        try:
            parsed, page_total, page_skipped, page_tags = _parse_nowcoder_page(html)
        except (ValueError, TypeError, KeyError, AttributeError):
            warnings.append(W_NOWCODER_SHAPE)
            break
        pages += 1
        skipped += page_skipped
        for name, ids in page_tags.items():
            known = tag_map.setdefault(name, [])
            known.extend(tag_id for tag_id in ids if tag_id not in known)
        if total is None and page_total is not None:
            total = page_total
        known = {row['id'] for row in rows}
        fresh = [row for row in parsed if row['id'] not in known]
        rows.extend(fresh)
        if not fresh or len(parsed) < NOWCODER_PAGE_SIZE:
            break
        if total is not None and len(rows) >= total:
            break
        capped = page == max_pages
    if skipped:
        warnings.append(W_NOWCODER_SKIPPED.format(count=skipped))
    return rows, total, pages, capped, tag_map


def discover_nowcoder(client, topic=None):
    """Return bounded Nowcoder candidates with official tag evidence.

    With a topic, the officially supported ``keyword`` search first discovers
    matching knowledge-point IDs from the official response; those IDs then
    drive the official ``tagId`` list filter. No tag ID is guessed or taken
    from another platform. Only rows whose title or published tags contain the
    searched term are kept. The official list exposes no publication date.
    """
    warnings = []
    terms = _topic_terms(topic)
    if terms is None:
        warnings.append(W_TOPIC_INVALID)
        terms = []
    if topic is not None and not terms:
        warnings.append(W_NOWCODER_TOPIC_FALLBACK)
    if terms:
        rows, pages, capped, tag_map = [], 0, False, {}
        for term in terms:
            found, _, used, query_capped, found_tags = _nowcoder_pages(
                client, {'keyword': term}, MAX_TOPIC_KEYWORD_PAGES, warnings)
            pages += used
            capped = capped or query_capped
            rows.extend(found)
            for name, ids in found_tags.items():
                known = tag_map.setdefault(name, [])
                known.extend(tag_id for tag_id in ids if tag_id not in known)
        # Official tag IDs are used only when the exact topic term is a tag
        # name published by Nowcoder in the responses above.
        filter_ids = []
        for term in terms:
            for tag_id in tag_map.get(term.casefold(), []):
                if tag_id not in filter_ids:
                    filter_ids.append(tag_id)
        filter_ids = filter_ids[:MAX_TOPIC_TAGS]
        tag_total, tag_pages = None, 0
        for tag_id in filter_ids:
            found, page_total, used, query_capped, _ = _nowcoder_pages(
                client, {'tagId': tag_id}, MAX_TOPIC_TAG_PAGES, warnings)
            tag_pages += used
            capped = capped or query_capped
            if tag_total is None and page_total is not None:
                tag_total = page_total
            rows.extend(found)
        verified = list({row['id']: row for row in rows
                         if _has_evidence(row, terms)}.values())
        if not rows:
            warnings.append(W_NOWCODER_FIRST)
        elif not verified:
            warnings.append(W_NOWCODER_TOPIC_EMPTY)
        if verified:
            warnings.append(W_NOWCODER_NO_DATE)
        truncated = bool(capped and (tag_total is None or len(verified) < tag_total))
        if truncated and len(filter_ids) == 1 and tag_total is not None:
            warnings.append(W_NOWCODER_TOPIC_TRUNCATED.format(total=tag_total,
                                                               count=len(verified)))
        coverage = (f'牛客：官方关键词检索 {len(terms)} 组，'
                    f'官方标签筛选 {len(filter_ids)} 个（ID 来自官方响应），'
                    f'读取 {pages + tag_pages} 页；保留 {len(verified)} 道题名或官方标签可核实匹配；'
                    '官方列表无逐题发布日期')
        return _result(verified, coverage, truncated, warnings)

    rows, total, pages, capped, _ = _nowcoder_pages(client, {}, MAX_NOWCODER_PAGES, warnings)
    truncated = bool(capped and (total is None or len(rows) < total))
    if truncated and total is not None:
        warnings.append(W_NOWCODER_TRUNCATED.format(total=total, count=len(rows)))
    if rows:
        warnings.append(W_NOWCODER_NO_DATE)
    coverage = (f'牛客：官方题库列表前 {pages} 页（每页 {NOWCODER_PAGE_SIZE} 道上限），'
                f'共 {len(rows)} 道；官方列表无逐题发布日期')
    if not rows:
        coverage = '牛客：官方题库列表未返回可用候选；未猜测题号或标签'
    return _result(rows, coverage, truncated, warnings)
