import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .base import Adapter
from ..models import CrawlError, Submission, verdict, rating_event


def number(text):
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def parse_submissions(text):
    soup = BeautifulSoup(text, "html.parser")
    if not soup.select_one(".profile-menu") or not soup.select_one(".my-state-main"):
        raise CrawlError("牛客账号不存在、返回登录/错误页或主页结构变化")
    table = next((t for t in soup.select("table") if "提交时间" in t.get_text()), None)
    if table is None:
        raise CrawlError("牛客练习提交表格缺失，不能确认空记录")
    rows = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        link = cells[0].find("a", href=True)
        if not link:
            if len(cells) == 1 and any(s in cells[0].get_text() for s in ("暂无", "没有")):
                continue
            raise CrawlError("牛客提交行缺少 submissionId")
        if len(cells) != 9:
            raise CrawlError("牛客提交表格列数变化")
        sid = parse_qs(urlsplit(link["href"]).query).get("submissionId", [None])[0]
        problem = cells[1].find("a", href=True)
        if not sid or not problem:
            raise CrawlError("牛客提交 ID 或题目链接缺失")
        pid = problem["href"].rstrip("/").split("/")[-1]
        values = [c.get_text(" ", strip=True) for c in cells]
        ts = int(datetime.strptime(values[8], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        rows.append(Submission(sid, pid, ts, verdict(values[2]), language=values[7], score=number(values[3]),
            time_ms=number(values[4]), memory_bytes=int(number(values[5]) * 1024) if number(values[5]) is not None else None,
            url=f"https://ac.nowcoder.com/acm/contest/view-submission?submissionId={sid}",
            problem={"title": values[1], "difficulty": None, "tags": []}, raw={"cells": values, "problem_url": problem["href"]}))
    next_link = soup.select_one(".js-next-pager:not(.disabled) a[href]")
    next_page = None
    if next_link:
        next_page = parse_qs(urlsplit(next_link["href"]).query).get("page", [None])[0]
        if next_page is None:
            raise CrawlError("牛客下一页链接无法解析")
    return rows, int(next_page) if next_page else None


class Nowcoder(Adapter):
    platform = "nowcoder"

    def fetch(self, out, since):
        base = f"https://ac.nowcoder.com/acm/contest/profile/{self.account}"
        soup = BeautifulSoup(self.client.get(base), "html.parser")
        if not soup.select_one(".profile-menu"):
            raise CrawlError("牛客账号不存在、页面不可访问或结构变化")
        rating_node = soup.select_one(".profile-status-box .state-num")
        name = soup.select_one(".coder-name")
        out.profile = {"uid": self.account, "name": name.get_text(' ', strip=True) if name else None,
                       "rating": number(rating_node.get_text()) if rating_node else None, "url": base}
        def ratings():
            data = self.client.json("https://ac.nowcoder.com/acm/contest/rating-history", {"uid": self.account})
            if data.get("code") != 0 or not isinstance(data.get("data"), list):
                raise CrawlError("牛客评级接口返回错误或结构变化")
            return [rating_event(contest_id=r["contestId"], contest_name=r.get("contestName"),
                rated_at=int(r["time"]) // 1000, new_rating=r["rating"],
                old_rating=r["rating"] - r["changeValue"] if r.get("changeValue") is not None else None,
                rank=r.get("rank"), raw=r) for r in data["data"]]
        out.rating_history = self.optional(out, "rating_history", ratings) or []
        out.warnings.append("牛客提交采集范围为公开练习页；比赛内未归档到练习页的提交不包含在内")
        page, previous = 1, None
        for _ in range(self.max_pages):
            text = self.client.get(f"{base}/practice-coding", {"page": page, "pageSize": self.page_size, "orderType": "DESC"})
            rows, next_page = parse_submissions(text)
            ids = tuple(r.id for r in rows)
            if rows and ids == previous:
                raise CrawlError("牛客分页重复，检查点未推进")
            previous = ids
            out.submissions.extend(r for r in rows if r.submitted_at >= since)
            if next_page is None or (rows and min(r.submitted_at for r in rows) < since):
                out.complete = True
                return
            if next_page <= page:
                raise CrawlError("牛客下一页页码未前进")
            page = next_page
        self.limit()
