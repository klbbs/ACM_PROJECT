"""Read-only, per-user coverage and exclusive rejection counts; no OJ or AI I/O."""
from acmweb.catalog import PLATFORMS, PUBLISHED_SOURCES, _pool, _canonical, _date_floor, publication_time, verified_platforms
from acmweb.difficulty_estimates import cf_reference, estimate, overlaps
from acmweb.training_topics import TOPICS, matches_topic
from .difficulty import score_candidate, selection_range


def diagnose(service, request, allowed_platforms=None):
    verified=set(verified_platforms(service.accounts))
    available=verified if allowed_platforms is None else verified & set(allowed_platforms)
    prefs=service.db.get('training',{})
    floor=_date_floor(prefs.get('published_after'))
    avoided=set(prefs.get('avoid_tags',[]))
    topic=TOPICS.get(request.topic or '')
    methods=service.app.state.topic_feasibility.approved_map(topic['id']) if topic else {}
    excluded=service.control.memory.drawn_ids() | set(request.exclude_ids)
    low,high=request.score_range
    lower,upper=selection_range(low,high)
    reference=cf_reference(service.db)
    refresh={row['platform']:row for row in service.db.get('catalog_refresh_status',[]) if isinstance(row,dict) and row.get('platform') in PLATFORMS}
    result=[]
    for platform in PLATFORMS:
        raw=_pool(service.db,platform,include_excluded=True)
        unattempted={row['id'] for row in _pool(service.db,platform)}
        counts={key:0 for key in ('platform_unavailable','attempted_or_reserved','difficulty','unknown_date','before_date','avoided_tags','topic','client_or_drawn')}
        eligible=0
        for row in raw:
            reason=None
            if platform not in available:reason='platform_unavailable'
            elif row['id'] not in unattempted:reason='attempted_or_reserved'
            else:
                score=score_candidate(row,low,high) if platform=='codeforces' else estimate(row,reference)
                if not score.get('eligible',overlaps(score,lower,upper)):reason='difficulty'
                elif floor is not None and (row.get('published_at_source') not in PUBLISHED_SOURCES or not publication_time(row.get('published_at'))):reason='unknown_date'
                elif floor is not None and row['published_at']<floor:reason='before_date'
                elif row.get('tags') and set(row['tags']).issubset(avoided):reason='avoided_tags'
                elif request.topic and not ((matches_topic(row,topic) or row['id'] in methods) if topic else request.topic.casefold() in {str(t).casefold() for t in row.get('tags',[])}):reason='topic'
                elif _canonical(platform,row['id']) in excluded:reason='client_or_drawn'
            if reason:counts[reason]+=1
            else:eligible+=1
        coverage=refresh.get(platform,{})
        result.append({'platform':platform,'login_verified':platform in verified,'available_this_draw':platform in available,
            'cached_count':len(raw),'eligible_count':eligible,'filter_counts':counts,
            'with_topic_metadata':sum(bool(row.get('tags')) for row in raw),
            'with_known_date':sum(row.get('published_at_source') in PUBLISHED_SOURCES and bool(publication_time(row.get('published_at'))) for row in raw),
            'truncated':coverage.get('truncated'),
            **{key:coverage[key] for key in ('next_page','total_count','coverage') if key in coverage}})
    return {'requested_score_range':[low,high],'effective_score_range':[lower,upper],
            'published_after':prefs.get('published_after'),'candidate_count':sum(row['eligible_count'] for row in result),
            'platforms':result,'notice':'题库缓存总量与符合当前条件的候选量不同；缺少专题、难度或日期证据不会据题号猜测。'}
