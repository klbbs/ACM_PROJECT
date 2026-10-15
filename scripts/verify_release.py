"""Check source manifest and absence of runtime/private files in a zip release."""
import hashlib
import sys
import zipfile
from pathlib import PurePosixPath


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        root=names[0].split('/')[0]+'/'
        forbidden={'account.toml','accounts.toml','accounts.web.toml','.env','ai-secrets.json','auth.json','cookies.sqlite','users.sqlite3','.multiuser.lock'}
        for name in names:
            parts=PurePosixPath(name).parts[1:]
            if any(p in forbidden or p in {'data','.git','.venv','node_modules','daily','records','for_code_skill'} for p in parts):
                raise RuntimeError('发布包含私人或运行时文件: '+name)
        for line in archive.read(root+'MANIFEST.sha256').decode().splitlines():
            expected,name=line.split('  ',1)
            if hashlib.sha256(archive.read(root+name)).hexdigest()!=expected:
                raise RuntimeError('文件校验失败: '+name)
        print(f'发布包校验通过：{len(names)} 个文件，包含前端构建，未包含私人配置或运行时数据。')


if __name__=='__main__':verify(sys.argv[1])
