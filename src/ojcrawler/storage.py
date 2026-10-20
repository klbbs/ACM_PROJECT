import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import verdict


def codeforces_judge_verdict(value, *, normalized=False):
    """Only known final CF states are evidence of a completed judgement."""
    known = {'OK', 'WRONG_ANSWER', 'TIME_LIMIT_EXCEEDED', 'MEMORY_LIMIT_EXCEEDED',
             'RUNTIME_ERROR', 'COMPILATION_ERROR', 'IDLENESS_LIMIT_EXCEEDED',
             'PRESENTATION_ERROR', 'FAILED', 'PARTIAL', 'CHALLENGED', 'SKIPPED',
             'REJECTED'}
    if normalized:
        known.update({'AC', 'WA', 'TLE', 'MLE', 'RE', 'CE'})
    return verdict(value) if value in known else 'PENDING'


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def sync_workbench_submission(db, platform, account, submission, collected_at):
    """Refresh linked judge evidence without changing the user's code snapshot.

    The crawler also runs standalone, where workbench tables do not exist.
    This update belongs to the caller's submission transaction.
    """
    if platform != "codeforces" or not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wb_attempts'"
    ).fetchone():
        return
    raw = submission.get("raw") or {}
    fallback = {"verdict": submission["verdict"],
                "creationTimeSeconds": submission["submitted_at"],
                "programmingLanguage": submission.get("language"),
                "timeConsumedMillis": submission.get("time_ms"),
                "memoryConsumedBytes": submission.get("memory_bytes")}
    judge = {key: raw.get(key, fallback.get(key)) for key in (
        "verdict", "passedTestCount", "timeConsumedMillis", "memoryConsumedBytes",
        "creationTimeSeconds", "programmingLanguage")}
    encoded = json.dumps(judge)
    status = codeforces_judge_verdict(judge['verdict'], normalized='verdict' not in raw)
    db.execute("""UPDATE wb_attempts SET verdict=?,judge_data=?,last_synced=?,
        review=CASE WHEN verdict=? AND judge_data=? THEN review ELSE NULL END
        WHERE handle=? COLLATE NOCASE AND submission_id=? AND problem_id=?
        AND last_synced<=?""", (status, encoded, collected_at,
        status, encoded, account, submission["id"],
        "codeforces:" + submission["problem_id"], collected_at))


@contextmanager
def run_lock(database):
    # Linux/macOS advisory lock released by OS even after a crashed cron job.
    import fcntl
    path = Path(str(database) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("另一个采集任务正在使用该数据库") from exc
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS submissions (
                platform TEXT NOT NULL, account TEXT NOT NULL, id TEXT NOT NULL,
                problem_id TEXT NOT NULL, submitted_at INTEGER NOT NULL, verdict TEXT NOT NULL,
                data TEXT NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY (platform, account, id)
            );
            CREATE INDEX IF NOT EXISTS submissions_time ON submissions(platform, account, submitted_at);
            CREATE TABLE IF NOT EXISTS accounts (
                platform TEXT NOT NULL, account TEXT NOT NULL, watermark INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(platform, account)
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                run_id TEXT NOT NULL, platform TEXT NOT NULL, account TEXT NOT NULL,
                collected_at INTEGER NOT NULL, profile TEXT NOT NULL, rating_history TEXT NOT NULL,
                complete INTEGER NOT NULL, warnings TEXT NOT NULL, error TEXT,
                PRIMARY KEY(run_id, platform, account)
            );
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, collected_at INTEGER NOT NULL, data TEXT NOT NULL);
        """)

    def watermark(self, platform, account):
        collation = " COLLATE NOCASE" if platform == "codeforces" else ""
        row = self.db.execute("SELECT MAX(watermark) FROM accounts WHERE platform=? AND account=?" + collation, (platform, account)).fetchone()
        return row[0] or 0

    def account_key(self, platform, account):
        if platform == "codeforces":
            # Preserve existing primary keys; never rename, merge or delete history.
            for table in ('accounts', 'submissions'):
                old = self.db.execute(f"SELECT account FROM {table} WHERE platform=? AND account=? COLLATE NOCASE ORDER BY account=? DESC,account LIMIT 1",
                                      (platform, account, account)).fetchone()
                if old:
                    return old[0]
        return account

    def save(self, run_id, collected_at, result, *, capture_snapshot=True):
        """Merge submissions and, by default, capture the collected account state.

        Submission-only official checks disable snapshot capture and use an
        incomplete Collection so they cannot replace profiles or advance a
        full-history collection checkpoint.
        """
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            account = self.account_key(result.platform, result.account)
            for row in result.submissions:
                data = asdict(row)
                collation = " COLLATE NOCASE" if result.platform == "codeforces" else ""
                old = self.db.execute("SELECT data,account FROM submissions WHERE platform=? AND account=?" + collation + " AND id=? ORDER BY account=? DESC,account LIMIT 1",
                                      (result.platform, account, row.id, account)).fetchone()
                if old:
                    previous_problem = json.loads(old[0]).get("problem", {})
                    # Replayed submissions may omit previously enriched metadata.
                    data["problem"] = {**previous_problem, **{k: v for k, v in data["problem"].items() if v is not None and v != [] and v != {}}}
                self.db.execute("""INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(platform,account,id) DO UPDATE SET
                    problem_id=excluded.problem_id, submitted_at=excluded.submitted_at,
                    verdict=excluded.verdict, data=excluded.data, updated_at=excluded.updated_at""",
                    (result.platform, old[1] if old else account, row.id, row.problem_id, row.submitted_at, row.verdict, dumps(data), collected_at))
                sync_workbench_submission(self.db, result.platform, result.account, data, collected_at)
            if capture_snapshot:
                self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)", (
                    run_id, result.platform, result.account, collected_at, dumps(result.profile),
                    dumps(result.rating_history), int(result.complete), dumps(result.warnings), result.error))
            # Partial pages can be retained safely, but never advance a checkpoint.
            if result.complete:
                maximum = max((s.submitted_at for s in result.submissions), default=0)
                self.db.execute("""INSERT INTO accounts VALUES(?,?,?) ON CONFLICT(platform,account)
                    DO UPDATE SET watermark=MAX(accounts.watermark,excluded.watermark)""",
                    (result.platform, account, maximum))

    def save_run(self, run):
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,?,?)", (run["run_id"], run["collected_at"], dumps(run)))

    def export(self, platform=None, account=None, since=0):
        clauses, params = ["submitted_at >= ?"], [since]
        for key, value in (("platform", platform), ("account", account)):
            if value:
                clauses.append(f"{key} = ?")
                params.append(value)
        rows = self.db.execute("SELECT platform,account,data FROM submissions WHERE " + " AND ".join(clauses)
                               + " ORDER BY submitted_at,platform,account,id", params)
        return [{"platform": p, "account": a, **json.loads(data)} for p, a, data in rows]

    def close(self):
        self.db.close()
