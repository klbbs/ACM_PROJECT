import json
import re
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup

from .base import Adapter
from ..models import CrawlError, Submission, rating_event
from ..access import PlatformAccessError


def page_data(text):
    """Handle JSON, current Luogu lentille data, and legacy encoded state."""
    try:
        data = json.loads(text)
    except ValueError:
        soup = BeautifulSoup(text, "html.parser")
        script = soup.find("script", id="lentille-context")
        if script:
            try:
                data = json.loads(script.get_text())
            except ValueError as exc:
                raise CrawlError("洛谷 lentille-context JSON 无法解析") from exc
        else:
            match = re.search(r'decodeURIComponent\(\s*[\'\"]([^\'\"]+)[\'\"]\s*\)', text)
            if not match:
                raise CrawlError("洛谷返回验证页或页面结构变化；请配置 LUOGU_COOKIE 后重试")
            try:
                data = json.loads(unquote(match.group(1)))
            except ValueError as exc:
                raise CrawlError("洛谷嵌入数据无法解析") from exc
    if not isinstance(data, dict):
        raise CrawlError("洛谷页面数据不是对象")
    if "currentData" in data:
        return data["currentData"]
    if "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data


STATUS = {12: "AC", 11: "WA", 7: "TLE", 5: "MLE", 6: "OLE", 10: "RE", 2: "CE"}


class Luogu(Adapter):
    platform = "luogu"

    def data(self, path, **params):
        url = f"https://www.luogu.com.cn{path}"
        headers = {"Referer": "https://www.luogu.com.cn/"}
        try:
            return page_data(self.client.get(url, params, headers))
        except PlatformAccessError as exc:
            if exc.reason == 'rate_limited':
                raise
            # Ordinary content negotiation; still requires official access and
            # the same strict page-data schema. Never execute a challenge.
            return page_data(self.client.get(url, params, {**headers, 'Accept':'application/json'}))

    def fetch(self, out, since):
        profile = self.data(f"/user/{quote(self.account, safe='')}")
        if not isinstance(profile.get("user"), dict):
            raise CrawlError("洛谷用户数据缺少 user，账号不存在或需要登录")
        user = profile["user"]
        if str(user.get("uid")) != str(int(self.account)):
            raise CrawlError("洛谷返回账号与请求 UID 不一致")
        # Only account data; never retain the page's current logged-in user/context.
        out.profile = {"uid": user.get("uid"), "name": user.get("name"),
                       "rating": user.get("eloValue", user.get("rating")), "ranking": user.get("ranking"),
                       "practice_rating": profile.get("gu"),
                       "passed_count": user.get("passedProblemCount"), "submitted_count": user.get("submittedProblemCount"),
                       "passed_problems": profile.get("passedProblems"), "submitted_problems": profile.get("submittedProblems")}
        if isinstance(profile.get("elo"), list):
            out.rating_history = [rating_event(contest_id=r.get("contest", {}).get("id"),
                contest_name=r.get("contest", {}).get("name"), rated_at=r.get("time"), new_rating=r.get("rating"), raw=r)
                for r in profile["elo"]]
        else:
            out.warnings.append("洛谷用户页未提供 elo 评级历史")
        metadata = {}
        previous = None
        for page in range(1, self.max_pages + 1):
            data = self.data("/record/list", user=self.account, page=page)
            container = data.get("records")
            if not isinstance(container, dict) or not isinstance(container.get("result"), list):
                raise CrawlError("洛谷提交列表结构不兼容或记录无访问权限")
            rows = container["result"]
            ids = tuple(str(r["id"]) for r in rows)
            if rows and ids == previous:
                raise CrawlError("洛谷分页未前进，检查点未推进")
            previous = ids
            for row in rows:
                timestamp = int(row["submitTime"])
                if timestamp < since:
                    continue
                p = row["problem"]
                pid = p["pid"]
                if self.enrich and pid not in metadata:
                    details = self.optional(out, f"problem:{pid}", lambda: self.data(f"/problem/{quote(pid, safe='')}"))
                    metadata[pid] = (details or {}).get("problem", {})
                full = {**p, **metadata.get(pid, {})}
                status = row.get("status")
                out.submissions.append(Submission(str(row["id"]), pid, timestamp, STATUS.get(status, f"LUOGU_{status}"),
                    language=str(row["language"]) if row.get("language") is not None else None,
                    score=row.get("score"), time_ms=row.get("time"),
                    memory_bytes=int(row["memory"]) * 1024 if row.get("memory") is not None else None,
                    url=f"https://www.luogu.com.cn/record/{row['id']}",
                    problem={"title": full.get("title"), "difficulty": full.get("difficulty"), "tags": full.get("tags", []), "raw": full}, raw=row))
            per_page = container.get("perPage")
            count = container.get("count")
            if (not rows or min(int(r["submitTime"]) for r in rows) < since
                or (per_page is not None and len(rows) < int(per_page))
                or (count is not None and per_page is not None and page * int(per_page) >= int(count))):
                out.complete = True
                return
        self.limit()
