"""Bounded public metadata supplements with explicit source and population.

No platform credentials, statements, user histories or AI calls are used.
AtCoder's tags come from exact task IDs in Luogu's mirrored AT collection;
they must never be presented as tags published by AtCoder itself.
"""
import json
import re
import time
from pathlib import Path

from ojcrawler.adapters.luogu import page_data
from ojcrawler.models import CrawlError

AT_TAG_SOURCE = 'luogu.atcoder_problem_list'
AT_TAG_URL = 'https://www.luogu.com.cn/problem/list'


def atcoder_tag_batch(client, rows, *, first_page=1, max_pages=8):
    known = {row['problem_id']: row for row in rows}
    taxonomy = json.loads(Path(__file__).with_name('training_topic_catalog.json').read_text())
    names = {tag['id']: tag['name'] for tag in taxonomy['tags']}
    changed, seen = set(), set()
    pages, next_page, warning = 0, first_page, None
    for page in range(first_page, first_page + max_pages):
        try:
            data = page_data(client.get(AT_TAG_URL, params={'type': 'AT', 'page': page}))
            container = data.get('problems', {})
            entries = container.get('result')
            if not isinstance(entries, list):
                raise CrawlError('incompatible AT tag collection')
            ids = tuple(p.get('pid') for p in entries if isinstance(p, dict))
            if ids and ids in seen:
                raise CrawlError('repeated AT tag page')
            seen.add(ids)
            pages += 1
            next_page = page + 1
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                pid = entry.get('pid', '')
                # Exact ID mapping only. Legacy numeric mirror IDs and similar
                # task titles are not sufficient evidence of task identity.
                if not isinstance(pid, str) or not re.fullmatch(r'AT_?[a-z0-9]+(?:_[a-z0-9]+)+', pid):
                    continue
                target = known.get(pid[3:] if pid.startswith('AT_') else pid[2:])
                tags = entry.get('tags', [])
                if target is None or not isinstance(tags, list):
                    continue
                native = [tag for tag in tags if type(tag) is int and tag in names]
                if not native:
                    continue
                target.update(tags=[names[tag] for tag in dict.fromkeys(native)],
                    luogu_tag_ids=list(dict.fromkeys(native)), tags_source=AT_TAG_SOURCE,
                    tags_source_url=AT_TAG_URL + '?type=AT', tags_mirror_id=pid,
                    tags_observed_at=int(time.time()))
                changed.add(target['id'])
            total, per_page = container.get('count'), container.get('perPage', 50)
            if not entries or (type(total) is int and type(per_page) is int and per_page > 0 and page * per_page >= total):
                next_page = 1
                break
        except (CrawlError, ValueError, TypeError, KeyError):
            next_page = page
            warning = 'AtCoder 的洛谷镜像标签补抓未完成；保留题目索引与旧标签，后续从本页继续'
            break
    return {'pages': pages, 'tagged_count': len(changed), 'next_page': next_page,
            'source': AT_TAG_SOURCE, 'warning': warning}


def cf_statistics_batch(client, rows, *, first_contest=1, max_contests=3):
    from acmhermes.contest_evidence import ContestEvidenceProvider
    provider = ContestEvidenceProvider(request=lambda url, params: client.json(url, params=params))
    grouped = {}
    for row in rows:
        if type(row.get('contest_id')) is int:
            grouped.setdefault(row['contest_id'], []).append(row)
    contests = sorted(grouped)
    selected = [cid for cid in contests if cid >= first_contest][:max_contests]
    updated, checked, warning = 0, 0, None
    next_contest = first_contest
    for cid in selected:
        result = provider.standings(cid)
        checked += 1
        next_contest = cid + 1
        if result.get('status') != 'available':
            warning = 'CF 比赛统计补抓未完成；官方 rating 和标签已保留，统计未知时不推算通过率'
            # A contest with unsupported/unfinished judging must not pin the
            # continuation forever. Access and transport failures do stop sends.
            if result.get('reason') in {'invalid_standings', 'judging_incomplete', 'no_official_participants'}:
                continue
            next_contest = cid
            break
        statistics = result['statistics']
        for row in grouped[cid]:
            stat = statistics.get(row['problem_id'].split('-', 1)[1])
            if stat:
                row['contest_statistics'] = stat
                updated += 1
    if contests and next_contest > contests[-1]:
        next_contest = 1
    return {'contests_checked': checked, 'statistics_count': updated,
            'next_contest': next_contest, 'warning': warning}
