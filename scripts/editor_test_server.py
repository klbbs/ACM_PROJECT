"""Synthetic, loopback-only fixture server for editor browser tests. No real accounts."""
import json
import os
import tempfile
import time
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
        # Synthetic principal evidence exists only in this guarded temporary fixture.
        app.state.credential_checks.statuses = lambda: {'items':[{'platform':'codeforces','handle':'fixture','status':'authenticated','login_verified':True,'checked_at':int(time.time())}]}
        def fixture_login_check(platform, handle, force_refresh=False):
            cookie, _ = app.state.credential_checks.credential(platform, handle)
            return {'platform':platform,'handle':handle,'status':'authenticated' if cookie else 'missing',
                    'login_verified':bool(cookie),'checked_at':int(time.time()),'auto_submit_supported':False,
                    'message':'隔离合成身份验证' if cookie else '未保存 Cookie；公开信息仍可采集，登录凭据尚未检测。'}
        app.state.credential_checks.check = fixture_login_check

        # Exercise the actual queue, persistence and UI with a synthetic OJ transport.
        # This replacement exists only in this explicitly guarded fixture process.
        remote_receipts = {}
        remote_sources = {}
        submission_reviews = []
        class FixtureRemoteJudge:
            before_send = None

            def options(self, pid, handle):
                return [{'id':'89','name':'Synthetic GNU C++20'},
                        {'id':'31','name':'Synthetic Python 3'}]

            def submit(self, pid, handle, code, compiler):
                if self.before_send:
                    self.before_send()
                sid = str(930001 + len(remote_receipts))
                remote_sources[sid] = code
                remote_receipts[sid] = {'id':sid, 'problem_id':pid.split(':',1)[1],
                    'account':handle, 'submitted_at':int(time.time()),
                    'verdict':'AC' if 'fixture_correct' in code else 'WA',
                    'language':'Synthetic Python 3',
                    'url':f'https://codeforces.com/contest/4/submission/{sid}'}
                return {'id':sid, 'url':remote_receipts[sid]['url'], 'compiler_name':'Synthetic Python 3'}

            def judge(self, pid, handle, sid):
                return remote_receipts[sid]

            def source(self, pid, handle, sid):
                from acmweb.remote_judges import SubmissionError
                if sid in remote_receipts:
                    record = remote_receipts[sid]
                elif sid in {'920001','920002','920003'} and pid == 'codeforces:4-A':
                    record = {'id':sid,'problem_id':'4-A','account':handle,
                        'submitted_at':int(time.time())-(3600 if sid == '920003' else 0),
                        'verdict':'AC' if sid == '920002' else 'WA', 'language':'Python 3',
                        'url':f'https://codeforces.com/contest/4/submission/{sid}'}
                else:
                    raise SubmissionError('record_unavailable')
                return {**record,'code':remote_sources.get(sid,
                    'print("fixture_correct")' if record['verdict'] == 'AC' else 'print("fixture_wrong")')}

            def close(self):
                pass

        app.state.remote_submissions._driver = lambda *_: FixtureRemoteJudge()

        @app.post('/_fixture/remote-count')
        def remote_count():
            return {'sent':len(remote_receipts), 'submission_reviews':submission_reviews}

        # Test-process-only substitutes: never post code or call paid AI APIs.
        from acmweb import workbench
        async def fixture_ai(model, prompt, schema):
            if schema.get('title') == 'DailySelection':
                payload = json.loads(prompt.rsplit('\n', 1)[-1])
                return json.dumps({'selected_problems': [{'id': item['id'], 'reason': '根据合成候选的难度与每日训练时间选择。'}
                    for item in payload['candidates'][:payload['requested_count']]]}, ensure_ascii=False)
            if schema.get('title') == 'Hint':
                return json.dumps({'focus':'边界检查','hint':'先检查输入较小的一端。','question':'边界输入是否满足所有要求？'},ensure_ascii=False)
            if schema.get('title') == 'Review':
                return json.dumps({'category':'boundary','line':1,'evidence':'仅针对本地快照检查边界；未运行代码。','question':'最小输入符合要求吗？','confidence':'low'},ensure_ascii=False)
            if schema.get('title') == 'SubmissionReview':
                payload = json.loads(prompt.rsplit('\n', 1)[-1])
                submission = payload['submission']
                sid = submission['submission_id']
                expected_source = remote_sources.get(sid, 'print("fixture_wrong")' if sid in {'920001', '920003'} else None)
                assert expected_source is not None and submission['source_code'] == expected_source
                assert submission['verdict'] == 'WA' and submission['account'].casefold() == 'fixture_user'
                assert payload['problem']['id'] == 'codeforces:4-A'
                submission_reviews.append({key:submission[key] for key in
                    ('submission_id', 'source_code', 'language', 'verdict')})
                return json.dumps({'categories':['boundary_conditions','unable_to_determine'],
                    'phenomenon':'官方合成判题为 WA，具体失败输入未知。',
                    'evidence':['已核验本次提交源码、题面及 WA 判题；缺少失败测试点，无法确定根因。'],
                    'check_directions':['先检查输入边界是否满足题目条件。'], 'confidence':'low'},ensure_ascii=False)
            raise RuntimeError('Unexpected test AI schema')
        app.state.ai._api_generate = fixture_ai
        original_json = workbench.Client.json
        def fixture_judge(self, url, **kwargs):
            if url != 'https://codeforces.com/api/contest.status':
                return original_json(self,url,**kwargs)
            handle = kwargs.get('params',{}).get('handle','')
            rows = [{'id':sid,'creationTimeSeconds':int(time.time())-(3600 if sid == 920003 else 0),
                     'problem':{'contestId':4,'index':'A','name':'Synthetic acceptance problem','rating':1200,'tags':['math']},
                     'author':{'members':[{'handle':handle}]},'verdict':status,
                     'programmingLanguage':'Python 3','passedTestCount':1,'timeConsumedMillis':46,
                     'memoryConsumedBytes':786432} for sid,status in [(920001,'WRONG_ANSWER'),(920002,'OK'),(920003,'WRONG_ANSWER')]]
            return {'status':'OK','result':rows}
        workbench.Client.json = fixture_judge
        app.state.database.set('training',{'minimum':800,'maximum':1800})
        seeded_statements = []
        with app.state.database.connect() as db:
            for contest in range(4, 12):
                pid = f'codeforces:{contest}-A'
                problem = {'id': pid, 'platform': 'codeforces', 'problem_id': f'{contest}-A',
                           'title': f'编辑器测试示例 {contest}（合成数据）', 'difficulty': 1200,
                           'tags': ['math'], 'contest_id': contest,
                           'url': f'https://codeforces.com/problemset/problem/{contest}/A'}
                statement = {**problem, 'samples': [{'input': '1\n2', 'output': '3'}],
                             'time_limit': '1 second', 'memory_limit': '256 MB',
                             'render_version': 2, 'sections': ['此题仅用于编辑器自动化测试，不是真实题面。'], 'has_images': False}
                db.execute('INSERT INTO problem_catalog VALUES(?,?,?)', ('codeforces', pid, json.dumps(problem)))
                seeded_statements.append((pid, json.dumps(statement), 1))
                db.execute('INSERT INTO wb_problems VALUES(?,?,?)', seeded_statements[-1])
        @app.post('/_fixture/reset-workbench')
        def reset_workbench():
            # This route exists only in this guarded, temporary loopback fixture.
            # Restore original statement samples as well as private practice state.
            from fastapi import HTTPException
            if any(not task.done() for _, task in app.state.jobs.tasks.values()):
                raise HTTPException(409, 'Previous fixture job is still running')
            with app.state.database.connect() as db:
                for table in ('wb_remote_submissions', 'wb_submission_code_claims',
                              'wb_auto_reviews', 'wb_hints', 'wb_attempts', 'wb_drafts', 'wb_problems',
                              'submissions', 'accounts', 'snapshots', 'runs', 'practice_feedback'):
                    db.execute('DELETE FROM ' + table)
                db.execute("DELETE FROM web_settings WHERE key LIKE 'problem_sync:%'")
                db.executemany('INSERT INTO wb_problems VALUES(?,?,?)', seeded_statements)
            remote_receipts.clear()
            remote_sources.clear()
            submission_reviews.clear()
            return {'ok': True}

        uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('ACM_EDITOR_TEST_PORT', '18766')), access_log=False)


if __name__ == '__main__':
    main()
