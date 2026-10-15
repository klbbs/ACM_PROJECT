import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


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
        row = self.db.execute("SELECT watermark FROM accounts WHERE platform=? AND account=?", (platform, account)).fetchone()
        return row[0] if row else 0

    def save(self, run_id, collected_at, result):
        with self.db:
            for row in result.submissions:
                data = asdict(row)
                old = self.db.execute("SELECT data FROM submissions WHERE platform=? AND account=? AND id=?",
                                      (result.platform, result.account, row.id)).fetchone()
                if old:
                    previous_problem = json.loads(old[0]).get("problem", {})
                    # Replayed submissions may omit previously enriched metadata.
                    data["problem"] = {**previous_problem, **{k: v for k, v in data["problem"].items() if v is not None and v != [] and v != {}}}
                self.db.execute("""INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(platform,account,id) DO UPDATE SET
                    problem_id=excluded.problem_id, submitted_at=excluded.submitted_at,
                    verdict=excluded.verdict, data=excluded.data, updated_at=excluded.updated_at""",
                    (result.platform, result.account, row.id, row.problem_id, row.submitted_at, row.verdict, dumps(data), collected_at))
            self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)", (
                run_id, result.platform, result.account, collected_at, dumps(result.profile),
                dumps(result.rating_history), int(result.complete), dumps(result.warnings), result.error))
            # Partial pages can be retained safely, but never advance a checkpoint.
            if result.complete:
                maximum = max((s.submitted_at for s in result.submissions), default=0)
                self.db.execute("""INSERT INTO accounts VALUES(?,?,?) ON CONFLICT(platform,account)
                    DO UPDATE SET watermark=MAX(accounts.watermark,excluded.watermark)""",
                    (result.platform, result.account, maximum))

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
