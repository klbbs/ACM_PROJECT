#!/usr/bin/env python3
"""Create and verify private backups of the active Hermes BOT account."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile

QQ = Path(".local/share/qq-hermes")
ACM = Path(".local/share/hermes-acm")
NONEBOT = Path(".local/share/nonebot2/nonebot_plugin_hermes")
CONTENT = Path(".local/share/hermes-content")
NAPCAT = QQ / "napcat/opt/QQ/resources/app/app_launcher/napcat/config"
FILES = {
    "config": (
        ".hermes/config.yaml", ".hermes/.env", ".hermes/auth.json", ".hermes/SOUL.md",
        ".hermes/channel_aliases.json", ".hermes/cron/jobs.json", "桌面/AGENTS.md",
        QQ / ".env", QQ / ".outbound-token", QQ / "delivery-policy.json",
        ACM / "server.json", ACM / "connection.json", ACM / "session.json",
        CONTENT / "stickers.json", NAPCAT / "webui.json", NAPCAT / "napcat.json",
    ),
    "memory": (
        ".hermes/state.db", ".hermes/response_store.db", ".hermes/runs_idempotency.db",
        ".hermes/shared-state.db", ".hermes/kanban.db", ".hermes/projects.db",
        ".hermes/channel_directory.json", ".hermes/feishu_seen_message_ids.json",
        ".hermes/cron/executions.db", ".hermes/cron/notepad.db",
        QQ / "control.sqlite3", QQ / "guild1.db", QQ / ".delivery-guard.sqlite3",
        QQ / "outbound.sqlite3", NONEBOT / "messages.db", NONEBOT / "session_keys.db",
    ),
}
TREES = {
    "config": (".hermes/plugins", ".hermes/skills", ".hermes/weixin", ".hermes/pairing",
               CONTENT / "accounts"),
    "memory": (".hermes/memories", ".hermes/knowledge", ".hermes/sessions",
               ".hermes/character-library", ".hermes/plugin-data", ACM / "practice",
               ".cache/nonebot2/nonebot_plugin_hermes/images",
               CONTENT / "characters", CONTENT / "group_files"),
}
MANIFEST = "backup-manifest.json"


def selected_paths(home: Path, kind: str):
    groups = ("config", "memory") if kind == "all" else (kind,)
    for group in groups:
        for name in FILES[group]:
            yield home / name
        for name in TREES[group]:
            yield from (home / name).rglob("*")
    if "config" in groups:
        cfg = home / NAPCAT
        webui = cfg / "webui.json"
        if webui.is_file() and webui.resolve() == webui:
            account = str(json.loads(webui.read_text()).get("autoLoginAccount", ""))
            if account.isascii() and account.isdigit():
                for prefix in ("onebot11_", "napcat_", "napcat_protocol_"):
                    yield cfg / (prefix + account + ".json")


def snapshot(path: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in {".sqlite", ".sqlite3", ".db"}:
        with (closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as source,
              closing(sqlite3.connect(dest)) as copy):
            source.backup(copy)
            assert copy.execute("PRAGMA quick_check").fetchone() == ("ok",)
            copy.execute("PRAGMA journal_mode=DELETE")
    else:
        shutil.copyfile(path, dest)


def pack(home: Path, kind: str) -> Path:
    home = home.resolve(strict=True)
    directory = home / ".local/state/hermesbot/backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.resolve() != directory or directory.stat().st_uid != os.geteuid():
        raise ValueError("Backup directory must belong to the BOT account")
    directory.chmod(0o700)
    created = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=directory) as stage_name:
        stage = Path(stage_name)
        files = {}
        for path in sorted(set(selected_paths(home, kind))):
            relative = path.relative_to(home)
            if any(part in {".git", "__pycache__", "backups", ".venv", "node_modules"} for part in relative.parts):
                continue
            if path.name.endswith(("-wal", "-shm", ".lock", ".pyc", ".tmp")) or ".bak" in path.name:
                continue
            if not path.is_file() or path.resolve() != path:
                continue
            dest = stage / relative
            snapshot(path, dest)
            with dest.open("rb") as stream:
                files[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        if not files:
            raise ValueError("No matching BOT data found")
        manifest = {"schema": 1, "kind": kind, "created_at": created.isoformat(), "sha256": files}
        manifest_path = stage / MANIFEST
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive = stage / "backup.tar.gz"
        with tarfile.open(archive, "w:gz", compresslevel=3) as tar:
            tar.add(manifest_path, arcname=MANIFEST)
            for name in files:
                tar.add(stage / name, arcname=name)
        archive.chmod(0o600)
        verify(archive)
        backup = directory / f"hermesbot-{kind}-{created:%Y%m%dT%H%M%S%fZ}.tar.gz"
        os.replace(archive, backup)
        return backup


def verify(archive: Path) -> tuple[str, int]:
    with tarfile.open(archive, "r:gz") as tar:
        names = set()
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or ".." in path.parts
                    or path.as_posix() != member.name or member.name in names):
                raise ValueError("Archive contains duplicate or unsafe entries")
            names.add(member.name)
        if MANIFEST not in names:
            raise ValueError("Backup manifest missing")
        with tar.extractfile(MANIFEST) as stream:
            manifest = json.load(stream)
        kind = manifest.get("kind")
        files = manifest.get("sha256")
        if (manifest.get("schema") != 1 or kind not in {"all", "config", "memory"}
                or not isinstance(files, dict) or not files or names - {MANIFEST} != set(files)):
            raise ValueError("Invalid backup manifest")
        for member in tar.getmembers():
            if member.name == MANIFEST:
                continue
            with tar.extractfile(member) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != files[member.name]:
                    raise ValueError("Backup checksum mismatch")
        return kind, len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("pack")
    create.add_argument("kind", choices=["config", "memory", "all"])
    check = sub.add_parser("verify")
    check.add_argument("archive", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "pack":
        print(pack(Path.home(), args.kind))
    else:
        kind, count = verify(args.archive)
        print(f"Backup verified ({kind}, {count} files)")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, sqlite3.Error, ValueError, tarfile.TarError) as exc:
        sys.exit(f"Backup operation failed ({type(exc).__name__})")
