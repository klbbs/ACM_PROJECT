import argparse
import json
import logging
import os
import sys
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .adapters import ADAPTERS
from .config import load
from .http import Client
from .storage import Store, run_lock, write_json

log = logging.getLogger("ojcrawler")


def timestamp(value):
    try:
        ts = int(value)
    except ValueError:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = int(dt.timestamp())
        except ValueError as exc:
            raise argparse.ArgumentTypeError("时间应为 Unix 秒或 ISO 日期时间") from exc
    if ts < 0:
        raise argparse.ArgumentTypeError("时间不能早于 Unix epoch")
    return ts


def crawl(config, args):
    with run_lock(config.database):
        store = Store(config.database)
        started = int(time.time())
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        run = {"schema_version": 1, "run_id": run_id, "collected_at": started, "accounts": []}
        try:
            selected = [a for a in config.accounts if not args.platform or a.platform in args.platform]
            if not selected:
                raise ValueError("没有匹配的账号")
            for account in selected:
                watermark = store.watermark(account.platform, account.handle)
                since = args.since if args.since is not None else (0 if args.full else max(0, watermark - config.overlap_seconds))
                client = Client(interval=config.interval, timeout=config.timeout, retries=config.retries,
                                cookie=account.cookie or (os.environ.get(account.cookie_env) if account.cookie_env else None),
                                cookie_host={"codeforces": "codeforces.com", "luogu": "www.luogu.com.cn",
                                             "nowcoder": "ac.nowcoder.com", "atcoder": "atcoder.jp"}[account.platform])
                try:
                    adapter = ADAPTERS[account.platform](client, account.handle, max_pages=args.max_pages or config.max_pages,
                                                        page_size=config.page_size, enrich=account.enrich)
                    log.info("采集 %s/%s since=%d", account.platform, account.handle, since)
                    result = adapter.collect(since)
                finally:
                    client.close()
                store.save(run_id, started, result)
                item = result.to_dict()
                item["since"] = since
                item["coverage"] = "public_practice_submissions" if account.platform == "nowcoder" else "public_account_submissions"
                item["checkpoint"] = store.watermark(account.platform, account.handle)
                run["accounts"].append(item)
                log.info("%s/%s: %s, %d 条提交，%d 条提示", account.platform, account.handle,
                         "complete" if result.complete else "failed/partial", len(result.submissions), len(result.warnings))
                if result.error:
                    log.error("%s/%s: %s", account.platform, account.handle, result.error)
                for warning in result.warnings:
                    log.warning("%s/%s: %s", account.platform, account.handle, warning)
            run["finished_at"] = int(time.time())
            run["complete"] = all(a["complete"] for a in run["accounts"])
            store.save_run(run)
            path = config.output_dir / f"{run_id}.json"
            write_json(path, run)
            print(json.dumps({"run_id": run_id, "complete": run["complete"], "output": str(path)}, ensure_ascii=False))
            return 0 if run["complete"] else 1
        finally:
            store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="算法平台账号数据采集工具")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("crawl", help="执行一次增量采集")
    fetch.add_argument("--config", default="accounts.toml")
    fetch.add_argument("--platform", action="append", choices=sorted(ADAPTERS))
    group = fetch.add_mutually_exclusive_group()
    group.add_argument("--full", action="store_true", help="重抓所有历史，包括旧记录重新判题")
    group.add_argument("--since", type=timestamp, help="显式时间下限（不会删除历史）")
    fetch.add_argument("--max-pages", type=int)
    check = sub.add_parser("check", help="离线校验配置")
    check.add_argument("--config", default="accounts.toml")
    export = sub.add_parser("export", help="导出累计提交记录")
    export.add_argument("--database", default="data/accounts.sqlite3")
    export.add_argument("--output", required=True)
    export.add_argument("--format", choices=["json", "jsonl"], default="jsonl")
    export.add_argument("--platform", choices=sorted(ADAPTERS))
    export.add_argument("--account")
    export.add_argument("--since", type=timestamp, default=0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # urllib3 debugging can reveal request details; never enable it via --verbose.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        if args.command == "export":
            if not Path(args.database).is_file():
                raise ValueError("数据库不存在，请先采集")
            if Path(args.output).resolve() == Path(args.database).resolve():
                raise ValueError("导出文件不能覆盖数据库")
            store = Store(args.database)
            try:
                rows = store.export(args.platform, args.account, args.since)
            finally:
                store.close()
            if args.format == "json":
                write_json(args.output, {"schema_version": 1, "submissions": rows})
            else:
                path = Path(args.output)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
                tmp.replace(path)
            print(f"已导出 {len(rows)} 条提交至 {args.output}")
            return 0
        config = load(args.config)
        if args.command == "check":
            print(json.dumps({"accounts": [{"platform": a.platform, "handle": a.handle, "enrich": a.enrich} for a in config.accounts],
                              "database": str(config.database), "output_dir": str(config.output_dir)}, ensure_ascii=False, indent=2))
            return 0
        if args.max_pages is not None and args.max_pages < 1:
            raise ValueError("max-pages 必须为正整数")
        return crawl(config, args)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
