"""Upgrade an existing desktop workspace without copying credentials or history."""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

WEB_TABLES = ('web_settings', 'web_jobs', 'ai_reports', 'problem_catalog', 'practice_feedback')


def merge_database(source, target):
    """Back up the destination and import only missing web rows, atomically."""
    from acmweb.database import Database

    target = Path(target)
    if Path(source).resolve() == target.resolve():
        raise ValueError('源数据库与目标数据库不能相同')
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.parent / 'backups' / ('before-web-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3')
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with sqlite3.connect(target) as original, sqlite3.connect(backup) as copy:
            original.backup(copy)
        backup.chmod(0o600)
    Database(target)
    with sqlite3.connect(target) as destination:
        destination.execute('ATTACH DATABASE ? AS incoming', (str(Path(source).resolve()),))
        with destination:
            for table in WEB_TABLES:
                if destination.execute("SELECT 1 FROM incoming.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    destination.execute(f'INSERT OR IGNORE INTO main.{table} SELECT * FROM incoming.{table}')
            destination.execute("UPDATE web_jobs SET status='interrupted', error='升级前未完成的任务' WHERE status IN ('queued','running')")
    return backup


def update_config(path):
    """Change only crawler paths; preserve all account fields and cookies."""
    content = path.read_text(encoding='utf-8')
    section = re.search(r'(?ms)^\[crawler\][^\n]*\n(.*?)(?=^\[|\Z)', content)
    if section is None:
        raise ValueError('账号配置缺少 [crawler]')
    body = section.group(1)
    for key, value in [('database', 'data/account-history/accounts.sqlite3'), ('output_dir', 'data/account-history/runs')]:
        pattern = rf'(?m)^{key}\s*=.*$'
        line = f'{key} = "{value}"'
        body = re.sub(pattern, lambda _: line, body) if re.search(pattern, body) else line + '\n' + body
    updated = content[:section.start(1)] + body + content[section.end(1):]
    temp = path.with_name(path.name + '.upgrade.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(updated)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def upgrade(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('源目录与目标目录必须独立')
    config = target / 'account.toml'
    database = target / 'data/account-history/accounts.sqlite3'
    if not config.is_file() or not database.is_file():
        raise ValueError('目标必须包含 account.toml 和 data/account-history/accounts.sqlite3')
    # Validate before changing any destination files.
    if not re.search(r'(?m)^\[crawler\]', config.read_text(encoding='utf-8')):
        raise ValueError('账号配置缺少 [crawler]')
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=source).decode().split('\0')
    for name in filter(None, paths):
        path = source / name
        if path.is_symlink():
            raise ValueError(f'源码中含符号链接: {name}')
    if not (source / 'frontend/dist/index.html').is_file():
        raise ValueError('请先构建前端')
    backup = merge_database(source / 'data/accounts.sqlite3', database)
    shutil.copy2(config, backup.with_suffix('.toml'))
    backup.with_suffix('.toml').chmod(0o600)
    for name in filter(None, paths):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    for name in ('frontend/dist', 'releases'):
        if (source / name).exists():
            shutil.copytree(source / name, target / name, dirs_exist_ok=True)
    update_config(config)
    print(f'升级完成；数据库和配置备份：{backup.parent}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='保留桌面账号与历史，合并 ACM Lab 网页数据；运行前停止服务')
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    upgrade(Path(__file__).resolve().parent.parent, args.target)
