import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
PLATFORMS = {"codeforces":"Codeforces", "luogu":"洛谷", "nowcoder":"牛客", "atcoder":"AtCoder"}


def submissions(database, platform="", verdict="", search="", offset=0, limit=30, days=0):
    conditions, values = ["1=1"], []
    for key, value in (("platform",platform),("verdict",verdict)):
        if value:
            conditions.append(key+"=?"); values.append(value)
    if search:
        conditions.append("(problem_id LIKE ? OR json_extract(data,'$.problem.title') LIKE ?)")
        values.extend(["%"+search+"%"]*2)
    if days:
        conditions.append("submitted_at>=?"); values.append(int(time.time())-days*86400)
    where = " AND ".join(conditions)
    with database.connect() as db:
        total = db.execute("SELECT COUNT(*) FROM submissions WHERE "+where, values).fetchone()[0]
        rows = db.execute("SELECT platform,account,data FROM submissions WHERE "+where+" ORDER BY submitted_at DESC,id DESC LIMIT ? OFFSET ?", [*values,limit,offset]).fetchall()
    return {"total":total,"items":[{"platform":r[0],"account":r[1],**json.loads(r[2])} for r in rows]}


def overview(database, days=30, platform="", now=None):
    now = now or int(time.time())
    with database.connect() as db:
        rows = db.execute("SELECT platform,account,data FROM submissions").fetchall()
        snapshots = db.execute("SELECT s.* FROM snapshots s JOIN (SELECT platform,account,MAX(collected_at) latest FROM snapshots GROUP BY platform,account) t ON s.platform=t.platform AND s.account=t.account AND s.collected_at=t.latest ORDER BY s.platform").fetchall()
    full_history = [{"platform":r[0],"account":r[1],**json.loads(r[2])} for r in rows]
    all_rows = [r for r in full_history if not platform or r["platform"]==platform]
    rows = [r for r in all_rows if not days or r["submitted_at"]>=now-days*86400]
    solved = {(r["platform"],r["problem_id"]) for r in rows if r["verdict"]=="AC"}
    ac = sum(r["verdict"]=="AC" for r in rows)
    trend = defaultdict(lambda:{"submissions":0,"accepted":0})
    topics = defaultdict(lambda:{"attempts":0,"accepted":0,"problems":set()})
    active = set()
    verdicts = Counter()
    cf_ratings = []
    for r in rows:
        date = datetime.fromtimestamp(r["submitted_at"],TZ).date().isoformat()
        active.add(date);trend[date]["submissions"]+=1;trend[date]["accepted"]+=r["verdict"]=="AC"
        verdicts[r["verdict"]]+=1
        p = r.get("problem") or {}
        if r["platform"]=="codeforces" and r["verdict"]=="AC" and isinstance(p.get("difficulty"), (int,float)):
            cf_ratings.append(p["difficulty"])
        for tag in p.get("tags",[]) or []:
            label = str(tag)
            entry=topics[label]; entry["attempts"]+=1;entry["accepted"]+=r["verdict"]=="AC";entry["problems"].add((r["platform"],r["problem_id"]))
    date = datetime.fromtimestamp(now,TZ).date()
    streak = 0
    if date.isoformat() not in active:
        date-=timedelta(days=1)
    while date.isoformat() in active:
        streak+=1;date-=timedelta(days=1)
    chart_days = days or 90
    dates = [(datetime.fromtimestamp(now,TZ).date()-timedelta(days=i)).isoformat() for i in reversed(range(min(chart_days,90)))]
    topic_rows = [{"tag":tag,"attempts":v["attempts"],"accepted":v["accepted"],"problems":len(v["problems"]),"acceptance":round(v["accepted"]/v["attempts"]*100,1)} for tag,v in topics.items()]
    topic_rows.sort(key=lambda t:(t["acceptance"],-t["attempts"]))
    platforms = []
    seen=set()
    for s in snapshots:
        key=(s['platform'],s['account'])
        if key in seen:continue
        seen.add(key)
        profile=json.loads(s["profile"]);ratings=json.loads(s["rating_history"])
        history=[r for r in full_history if r["platform"]==s["platform"] and r["account"]==s["account"]]
        platforms.append({"platform":s["platform"],"account":s["account"],"name":PLATFORMS.get(s["platform"],s["platform"]),
            "submissions":len(history),"solved":len({r["problem_id"] for r in history if r["verdict"]=="AC"}),
            "rating":profile.get("rating") or ((profile.get("fields") or {}).get("Rating")),
            "last_sync":s["collected_at"],"complete":bool(s["complete"]),"error":s["error"],"warnings":json.loads(s["warnings"]),"rating_history":ratings})
    return {"days":days,"timezone":"Asia/Shanghai","generated_at":now,
        "stats":{"submissions":len(rows),"solved":len(solved),"accepted":ac,"acceptance":round(ac/len(rows)*100,1) if rows else 0,"streak":streak,"active_days":len(active),"all_submissions":len(all_rows)},
        "trend":[{"date":d,**trend[d]} for d in dates],"topics":topic_rows[:30],"verdicts":dict(verdicts),"platforms":platforms,
        "suggested_cf_difficulty":int(sorted(cf_ratings)[len(cf_ratings)//2]) if cf_ratings else 1000,
        "coverage":["牛客仅包含公开练习页", "题目标签按来源记录，提交通过率不等于能力评分", "AtCoder Problems 可能存在同步延迟"],
        "recent":[{k:r.get(k) for k in ("platform","problem_id","submitted_at","verdict","language","problem")} for r in sorted(rows,key=lambda r:r["submitted_at"],reverse=True)[:30]]}
