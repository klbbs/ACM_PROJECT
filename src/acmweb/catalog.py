import hashlib
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ojcrawler.http import Client
from ojcrawler.models import CrawlError
from .codex_bridge import AIError


def refresh_catalog(database):
    client=Client(timeout=30,retries=2)
    try:
        data=client.json("https://codeforces.com/api/problemset.problems")
    finally:
        client.close()
    if data.get("status")!="OK":
        raise CrawlError("CF 题库获取失败")
    rows=[]
    for p in data["result"]["problems"]:
        if not isinstance(p.get("rating"),int) or "contestId" not in p:
            continue
        pid=f"{p['contestId']}-{p['index']}"
        problem={"id":"codeforces:"+pid,"platform":"codeforces","problem_id":pid,
            "title":p["name"],"difficulty":p["rating"],"tags":p.get("tags",[]),
            "contest_id":p["contestId"],"url":f"https://codeforces.com/problemset/problem/{p['contestId']}/{p['index']}"}
        rows.append(("codeforces",problem["id"],json.dumps(problem,ensure_ascii=False)))
    if not rows:
        raise CrawlError("CF 题库返回空内容，保留原缓存")
    with database.connect() as db:
        db.execute("DELETE FROM problem_catalog WHERE platform='codeforces'")
        db.executemany("INSERT INTO problem_catalog VALUES(?,?,?)",rows)
    database.set("catalog_updated_at",int(time.time()))
    return {"count":len(rows),"platform":"codeforces"}


def candidates(database, minimum=800, maximum=1600, tags=None, limit=60, min_contest=0):
    with database.connect() as db:
        rows=db.execute("SELECT data FROM problem_catalog WHERE platform='codeforces'").fetchall()
        attempted={"codeforces:"+r[0] for r in db.execute("SELECT DISTINCT problem_id FROM submissions WHERE platform='codeforces'")}
        excluded={r[0]+":"+r[1] for r in db.execute("SELECT platform,problem_id FROM practice_feedback WHERE status IN ('done','skip')")}
    tags=tags or []
    result=[]
    today=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    for row in rows:
        problem=json.loads(row[0])
        if problem["id"] in attempted or problem["id"] in excluded:
            continue
        if not minimum<=problem["difficulty"]<=maximum or problem["contest_id"]<min_contest:
            continue
        if tags and not set(tags)&set(problem["tags"]):
            continue
        result.append(problem)
    target=(minimum+maximum)/2
    prefs=database.get("training",{})
    preferred=set(prefs.get("preferred_tags",[]))
    avoid=set(prefs.get("avoid_tags",[]))
    result=[p for p in result if not (p["tags"] and set(p["tags"]).issubset(avoid))]
    result.sort(key=lambda p:(abs(p["difficulty"]-target),-bool(set(p["tags"])&preferred),
                            -p["contest_id"] if prefs.get("prefer_recent",False) else 0,
                            hashlib.sha256((today+p["id"]).encode()).hexdigest()))
    return result[:limit]
