from .base import Adapter
from ..models import CrawlError, Submission, verdict, rating_event
from ..access import PlatformAccessError
from ..codeforces_pages import read_submissions
from bs4 import BeautifulSoup
from urllib.parse import quote
import re


class Codeforces(Adapter):
    platform = "codeforces"

    def api(self, method, **params):
        data = self.client.json(f"https://codeforces.com/api/{method}", params)
        if data.get("status") != "OK":
            if re.search(r'(?:call|request) limit exceeded', str(data.get('comment', '')), re.I):
                raise PlatformAccessError('codeforces.com', 'rate_limited', 429)
            raise CrawlError(f"CF {method}: API 返回 FAILED，请检查 handle 或限流")
        return data["result"]

    def fetch(self, out, since):
        try:
            out.profile = self.api("user.info", handles=self.account)[0]
        except PlatformAccessError as exc:
            if exc.reason == 'rate_limited':
                raise
            soup = BeautifulSoup(self.client.get('https://codeforces.com/profile/' + quote(self.account, safe='')), 'html.parser')
            name = soup.select_one('.info .main-info h1 a[href]')
            if name is None or name.get('href','').rstrip('/').casefold() != ('/profile/'+self.account).casefold():
                raise CrawlError('CF 公开主页身份无法核验，未导入其他账号资料')
            out.profile = {'handle':name.get_text(strip=True), 'source':'official_profile_page'}
            for item in soup.select('.info li'):
                text = item.get_text(' ',strip=True)
                if text.startswith('Contest rating:') and (match := re.search(r'Contest rating:\s*(\d+)', text)):
                    out.profile['rating'] = int(match[1])
            out.warnings.append('CF 用户 API 受限，已改用官方公开主页；未猜测主页没有提供的字段')
        def ratings():
            return [rating_event(contest_id=r["contestId"], contest_name=r.get("contestName"),
                                 rated_at=r["ratingUpdateTimeSeconds"], old_rating=r["oldRating"],
                                 new_rating=r["newRating"], rank=r.get("rank"), raw=r)
                    for r in self.api("user.rating", handle=self.account)]
        out.rating_history = self.optional(out, "rating_history", ratings) or []
        previous = None
        for page in range(self.max_pages):
            try:
                rows = self.api("user.status", handle=self.account, **{"from": page * self.page_size + 1, "count": self.page_size})
            except PlatformAccessError as exc:
                if exc.reason == 'rate_limited':
                    raise
                rows, complete = read_submissions(self.client, self.account, max_pages=min(self.max_pages,5), since=since)
                out.submissions.extend(self.submission(row) for row in rows)
                out.warnings.append('CF API 受限，已读取官方用户提交列表最多 5 页；只采信账号与时间可核验的记录')
                out.complete = complete
                if not complete:
                    raise CrawlError('CF 官方列表回退超过页数上限，本次同步不完整；检查点未推进')
                return
            ids = tuple(str(r["id"]) for r in rows)
            if rows and ids == previous:
                raise CrawlError("CF 分页未前进，检查点未推进")
            previous = ids
            for row in rows:
                if row["creationTimeSeconds"] < since:
                    continue
                out.submissions.append(self.submission(row))
            if not rows or len(rows) < self.page_size or min(r["creationTimeSeconds"] for r in rows) < since:
                out.complete = True
                return
        self.limit()

    @staticmethod
    def submission(row):
        p = row['problem']
        pid = f"{p.get('contestId', p.get('problemsetName', 'unknown'))}-{p['index']}"
        return Submission(
                    str(row["id"]), pid, row["creationTimeSeconds"], verdict(row.get("verdict")),
                    language=row.get("programmingLanguage"), contest_id=str(row["contestId"]) if "contestId" in row else None,
                    time_ms=row.get("timeConsumedMillis"), memory_bytes=row.get("memoryConsumedBytes"),
                    url=f"https://codeforces.com/{'gym' if row['contestId'] >= 100000 else 'contest'}/{row['contestId']}/submission/{row['id']}" if "contestId" in row else None,
                    problem={"title": p.get("name"), "difficulty": p.get("rating"), "tags": p.get("tags", []), "raw": p}, raw=row)
