from .base import Adapter
from ..models import CrawlError, Submission, verdict, rating_event


class Codeforces(Adapter):
    platform = "codeforces"

    def api(self, method, **params):
        data = self.client.json(f"https://codeforces.com/api/{method}", params)
        if data.get("status") != "OK":
            raise CrawlError(f"CF {method}: API 返回 FAILED，请检查 handle 或限流")
        return data["result"]

    def fetch(self, out, since):
        out.profile = self.api("user.info", handles=self.account)[0]
        def ratings():
            return [rating_event(contest_id=r["contestId"], contest_name=r.get("contestName"),
                                 rated_at=r["ratingUpdateTimeSeconds"], old_rating=r["oldRating"],
                                 new_rating=r["newRating"], rank=r.get("rank"), raw=r)
                    for r in self.api("user.rating", handle=self.account)]
        out.rating_history = self.optional(out, "rating_history", ratings) or []
        previous = None
        for page in range(self.max_pages):
            rows = self.api("user.status", handle=self.account, **{"from": page * self.page_size + 1, "count": self.page_size})
            ids = tuple(str(r["id"]) for r in rows)
            if rows and ids == previous:
                raise CrawlError("CF 分页未前进，检查点未推进")
            previous = ids
            for row in rows:
                if row["creationTimeSeconds"] < since:
                    continue
                p = row["problem"]
                pid = f"{p.get('contestId', p.get('problemsetName', 'unknown'))}-{p['index']}"
                out.submissions.append(Submission(
                    str(row["id"]), pid, row["creationTimeSeconds"], verdict(row.get("verdict")),
                    language=row.get("programmingLanguage"), contest_id=str(row["contestId"]) if "contestId" in row else None,
                    time_ms=row.get("timeConsumedMillis"), memory_bytes=row.get("memoryConsumedBytes"),
                    url=f"https://codeforces.com/{'gym' if row['contestId'] >= 100000 else 'contest'}/{row['contestId']}/submission/{row['id']}" if "contestId" in row else None,
                    problem={"title": p.get("name"), "difficulty": p.get("rating"), "tags": p.get("tags", []), "raw": p}, raw=row))
            if not rows or len(rows) < self.page_size or min(r["creationTimeSeconds"] for r in rows) < since:
                out.complete = True
                return
        self.limit()
