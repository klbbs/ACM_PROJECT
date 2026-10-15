from urllib.parse import quote
from datetime import datetime
from bs4 import BeautifulSoup

from .base import Adapter
from ..models import CrawlError, Submission, verdict, rating_event


class AtCoder(Adapter):
    platform = "atcoder"

    def fetch(self, out, since):
        user = quote(self.account, safe="")
        def ratings():
            history = self.client.json(f"https://atcoder.jp/users/{user}/history/json")
            if not isinstance(history, list):
                raise CrawlError("AtCoder 评级历史响应不兼容")
            return [rating_event(contest_id=(r.get("ContestScreenName") or "").removesuffix(".contest.atcoder.jp"), contest_name=r.get("ContestName"),
                rated_at=int(datetime.fromisoformat(r["EndTime"].replace("Z", "+00:00")).timestamp()) if r.get("EndTime") else None,
                old_rating=r.get("OldRating"), new_rating=r.get("NewRating"), rank=r.get("Place"),
                performance=r.get("Performance"), raw=r) for r in history if r.get("IsRated", True)]
        out.rating_history = self.optional(out, "rating_history", ratings) or []
        def profile():
            soup = BeautifulSoup(self.client.get(f"https://atcoder.jp/users/{user}"), "html.parser")
            tables = soup.select("#main-container table.dl-table")
            if not tables:
                raise CrawlError("AtCoder 用户不存在或主页结构已变化")
            fields = {}
            for row in [row for table in tables for row in table.select("tr")]:
                th, td = row.find("th"), row.find("td")
                if th and td:
                    fields[th.get_text(' ', strip=True)] = td.get_text(' ', strip=True)
            return {"handle": self.account, "url": f"https://atcoder.jp/users/{user}", "fields": fields}
        out.profile = profile()
        problems, models = {}, {}
        if self.enrich:
            rows = self.optional(out, "problem_metadata", lambda: self.client.json("https://kenkoooo.com/atcoder/resources/problems.json"))
            problems = {p["id"]: p for p in rows} if rows is not None else {}
            models = self.optional(out, "problem_difficulty", lambda: self.client.json("https://kenkoooo.com/atcoder/resources/problem-models.json")) or {}
        cursor = since
        seen = set()
        for _ in range(self.max_pages):
            rows = self.client.json("https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions",
                                    {"user": self.account, "from_second": cursor})
            if not isinstance(rows, list):
                raise CrawlError("AtCoder Problems 提交接口结构不兼容")
            fresh = [r for r in rows if str(r["id"]) not in seen]
            for row in fresh:
                seen.add(str(row["id"]))
                pid = row["problem_id"]
                p = problems.get(pid, {})
                model = models.get(pid, {})
                out.submissions.append(Submission(str(row["id"]), pid, row["epoch_second"], verdict(row["result"]),
                    language=row.get("language"), contest_id=row["contest_id"], score=row.get("point"),
                    time_ms=row.get("execution_time"),
                    url=f"https://atcoder.jp/contests/{row['contest_id']}/submissions/{row['id']}",
                    problem={"title": p.get("title"), "difficulty": model.get("difficulty"), "difficulty_source": "atcoder-problems-estimate", "tags": [], "raw": p, "model": model}, raw=row))
            if len(rows) < 500:
                out.complete = True
                return
            end = max(r["epoch_second"] for r in rows)
            if end <= cursor:
                raise CrawlError("AtCoder 同一秒返回 500 条记录，无法安全推进时间游标；检查点未推进")
            # Inclusive boundary: request the final second again so ties are not lost.
            cursor = end
        self.limit()
