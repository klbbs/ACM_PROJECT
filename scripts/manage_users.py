"""Offline user recovery and explicit legacy import. Run with the service stopped."""
import argparse
import getpass
import os
import shutil
import sqlite3
from pathlib import Path

from acmweb.settings import default_data_dir
from acmweb.users import Users, instance_lock, password_hash
from acmweb.database import Database


def import_legacy(root, user, config):
    if user['role'] != 'admin':
        raise ValueError('旧版个人数据只能导入管理员自己的空间')
    source = root/'accounts.sqlite3'
    if not source.exists():
        raise ValueError('旧数据库不存在')
    target = root/'users'/user['id']
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    target.chmod(0o700)
    database = target/'accounts.sqlite3'
    # Never replace an established user's content or credentials.
    if database.exists():
        with sqlite3.connect(database) as db:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                if db.execute('SELECT COUNT(*) FROM "'+table.replace('"','""')+'"').fetchone()[0]:
                    raise ValueError('目标空间已有数据，拒绝覆盖；请先备份并选择空空间')
    if any((target/name).exists() for name in ('account.toml','accounts.web.toml','ai-secrets.json','codex')):
        raise ValueError('目标空间已有配置或授权，拒绝覆盖')
    backup = target/'legacy-import.sqlite3.tmp'
    if backup.exists():
        raise ValueError('存在未处理的导入临时文件，请检查后再试')
    try:
        with sqlite3.connect(source) as src, sqlite3.connect(backup) as dest:
            src.backup(dest)
        backup.chmod(0o600)
        migrated = Database(backup)
        schedule = migrated.get('schedule',{})
        if schedule:
            schedule['enabled'] = False
            migrated.set('schedule',schedule)
        for name in ('accounts.web.toml','ai-secrets.json'):
            if (root/name).exists():
                shutil.copy2(root/name,target/name)
                (target/name).chmod(0o600)
        if config.exists():
            shutil.copy2(config,target/'account.toml')
            (target/'account.toml').chmod(0o600)
        os.replace(backup,database)
    finally:
        backup.unlink(missing_ok=True)
    # Official login intentionally stays with its original location. Sign in per user.


def main():
    parser = argparse.ArgumentParser(description='离线用户运维：须先停止多人服务；不会输出密码或 Cookie')
    parser.add_argument('--data-dir',type=Path,default=default_data_dir())
    parser.add_argument('--config',type=Path,default=Path('account.toml'))
    parser.add_argument('action',choices=['list','reset-password','import-legacy'])
    parser.add_argument('username',nargs='?')
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if not (root/'users.sqlite3').exists():
        parser.error('请先启动多人服务并在网页初始化管理员')
    with instance_lock(root/'.multiuser.lock'):
        users = Users(root/'users.sqlite3')
        users.normalize_administrator()
        with users.connect() as db:
            if args.action == 'list':
                for row in db.execute('SELECT username,role,active FROM users ORDER BY username'):
                    print(row['username'],row['role'],'active' if row['active'] else 'disabled')
                return
            user = db.execute('SELECT * FROM users WHERE username=?',(str(args.username).lower(),)).fetchone()
            if not user:
                parser.error('用户不存在')
        if args.action == 'reset-password':
            password = getpass.getpass('新密码（至少 10 位）：')
            if not 10 <= len(password) <= 128 or password != getpass.getpass('确认新密码：'):
                parser.error('密码长度不符合要求或两次不一致')
            hashed = password_hash(password)
            with users.connect() as db:
                db.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,user['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
                users.audit('password.offline_reset',None,user['id'],db)
            print('密码已更新，全部会话已撤销。')
        else:
            import_legacy(root,user,args.config.resolve())
            users.audit('legacy.imported',user['id'],user['id'])
            print('已导入管理员的独立空间。原始数据保留；定时任务已关闭，官方账号需重新授权。')


if __name__ == '__main__':
    main()
