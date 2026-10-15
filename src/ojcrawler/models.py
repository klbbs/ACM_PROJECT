from dataclasses import asdict, dataclass, field
from typing import Any


class CrawlError(Exception):
    """Expected transport, authorization, or schema error."""


@dataclass
class Submission:
    id: str
    problem_id: str
    submitted_at: int
    verdict: str
    language: str | None = None
    contest_id: str | None = None
    score: float | None = None
    time_ms: float | None = None
    memory_bytes: int | None = None
    url: str | None = None
    problem: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Collection:
    platform: str
    account: str
    profile: dict[str, Any] = field(default_factory=dict)
    submissions: list[Submission] = field(default_factory=list)
    rating_history: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self):
        return asdict(self)


VERDICTS = {
    "OK": "AC", "Accepted": "AC", "答案正确": "AC", "AC": "AC",
    "WRONG_ANSWER": "WA", "Wrong Answer": "WA", "答案错误": "WA",
    "TIME_LIMIT_EXCEEDED": "TLE", "Time Limit Exceeded": "TLE", "运行超时": "TLE",
    "MEMORY_LIMIT_EXCEEDED": "MLE", "Memory Limit Exceeded": "MLE", "内存超限": "MLE",
    "RUNTIME_ERROR": "RE", "Runtime Error": "RE", "运行错误": "RE",
    "COMPILATION_ERROR": "CE", "Compilation Error": "CE", "编译错误": "CE",
    "PARTIAL": "PARTIAL", "TESTING": "PENDING", "Judging": "PENDING",
}


def verdict(value):
    return VERDICTS.get(str(value), str(value) if value is not None else "PENDING")


def rating_event(*, contest_id=None, contest_name=None, rated_at=None, old_rating=None,
                 new_rating=None, rank=None, performance=None, raw=None):
    return {"contest_id": str(contest_id) if contest_id is not None else None,
            "contest_name": contest_name, "rated_at": rated_at, "old_rating": old_rating,
            "new_rating": new_rating, "rank": rank, "performance": performance, "raw": raw or {}}
