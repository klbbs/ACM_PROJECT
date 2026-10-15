"""Prepare private local config; never overwrite an existing credential file."""
import os
import secrets
from pathlib import Path

root=Path(__file__).resolve().parent.parent
(root/'data').mkdir(exist_ok=True)
legacy=(root/'data/account-history/accounts.sqlite3').is_file()
for filename,content in [
    ('account.toml',(root/'accounts.example.toml').read_text()),
    ('.env',f'ACM_ACCESS_TOKEN={secrets.token_urlsafe(36)}\nACM_ENV=production\nACM_ENABLE_CODEX=false\nACM_DATA_DIR={"data/account-history" if legacy else "data"}\nACM_DATA_SUBDIR={"account-history" if legacy else ""}\nACM_UID={os.getuid() if hasattr(os,"getuid") else 1000}\nACM_GID={os.getgid() if hasattr(os,"getgid") else 1000}\n')]:
    path=root/filename
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        print(f'{filename} 已存在，保持不变。')
        continue
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(content)
    print(f'已创建 {filename}，权限 600。')
print('请在本机编辑账号配置；访问令牌在 .env 中，不会输出到日志。')
