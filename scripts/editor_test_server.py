"""Synthetic, loopback-only fixture server for editor browser tests. No real accounts."""
import json
import os
import tempfile
from pathlib import Path

from acmweb.app import create_app
from acmweb.settings import Settings
import uvicorn


def main():
    if os.getenv('ACM_EDITOR_TEST_SERVER') != '1':
        raise SystemExit('仅供自动化测试；需设置 ACM_EDITOR_TEST_SERVER=1')
    with tempfile.TemporaryDirectory(prefix='acm-editor-e2e-') as directory:
        root = Path(directory)
        static_dir = Path(__file__).resolve().parent.parent/'frontend/dist'
        if not (static_dir/'index.html').is_file():
            raise SystemExit('请先运行 npm --prefix frontend run build')
        settings = Settings(static_dir=static_dir, data_dir=root, config_path=root/'account.toml', enable_codex=False,
                            allow_env_ai_key=False, multi_user=False, production=False,
                            access_token='', public_origin='')
        app = create_app(settings)
        with app.state.database.connect() as db:
            for contest in (4, 5):
                pid = f'codeforces:{contest}-A'
                problem = {'id': pid, 'platform': 'codeforces', 'problem_id': f'{contest}-A',
                           'title': f'编辑器测试示例 {contest}（合成数据）', 'difficulty': 1200,
                           'tags': ['math'], 'contest_id': contest,
                           'url': f'https://codeforces.com/problemset/problem/{contest}/A'}
                statement = {**problem, 'samples': [{'input': '1\n2', 'output': '3'}],
                             'time_limit': '1 second', 'memory_limit': '256 MB',
                             'render_version': 2, 'sections': ['此题仅用于编辑器自动化测试，不是真实题面。'], 'has_images': False}
                db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', pid, json.dumps(problem)))
                db.execute('INSERT INTO wb_problems VALUES(?,?,?)', (pid, json.dumps(statement), 1))
        uvicorn.run(app, host='127.0.0.1', port=18766, access_log=False)


if __name__ == '__main__':
    main()
