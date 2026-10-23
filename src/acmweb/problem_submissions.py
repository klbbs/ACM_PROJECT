"""One-click, code-independent synchronization of the current problem's records.

No code is sent to an OJ here. A record never implicitly claims that the current
editor draft is its source. Public collection and authenticated source reading
are intentionally separate capabilities.
"""
import asyncio
import hashlib
import os
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ojcrawler.http import Client
from ojcrawler.models import Collection, Submission
from ojcrawler.storage import Store, codeforces_judge_verdict
from .codex_bridge import AIError
from .problem_sources import HOSTS, normalize

DDL = '''CREATE TABLE IF NOT EXISTS wb_official_sources (
 attempt_id TEXT PRIMARY KEY, code_hash TEXT NOT NULL, fetched_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS wb_official_sources_cleanup AFTER DELETE ON wb_attempts
BEGIN DELETE FROM wb_official_sources WHERE attempt_id=OLD.id; END;'''


class AccountRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    handle: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')


class SourceRequest(AccountRequest):
    submission_id: str = Field(min_length=1, max_length=12, pattern=r'^[1-9]\d*$')


def editor_language(value):
    import re
    value = value or ''
    if re.search(r'(python|pypy)\s*3', value, re.I):
        return 'python3'
    if re.search(r'c\+\+|g\+\+|clang\+\+', value, re.I):
        return 'cpp20' if re.search(r'\+\+\s*(20|23|26)', value) else 'cpp17'
    if re.search(r'(java|jdk)\s*(17|19|21|23|25)', value, re.I):
        return 'java17'
    return None


class ProblemSubmissions:
    def __init__(self, database, accounts, workbench, remote, jobs):
        self.db, self.accounts, self.workbench, self.remote, self.jobs = database, accounts, workbench, remote, jobs
        with database.connect() as conn:
            conn.executescript(DDL)

    def account(self, pid, handle):
        platform = pid.split(':')[0]
        for account in self.accounts.document().get('accounts', []):
            matches = account['handle'].casefold() == handle.casefold() if platform == 'codeforces' else account['handle'] == handle
            if account['platform'] == platform and matches:
                return account
        raise HTTPException(400, '请先在连接与设置添加此平台账号')

    def listing(self, pid, handle=None):
        pid = normalize(pid)
        if handle:
            handle = self.account(pid, handle)['handle']
        items = self.workbench.history(pid)['items']
        platform = pid.split(':')[0]
        if handle:
            items = [item for item in items if (item.get('account') or '').casefold() == handle.casefold()] if platform == 'codeforces' else [item for item in items if item.get('account') == handle]
        # This list is judge evidence, never a dump of private source code.
        return {'problem_id':pid, 'items':items,
                'handles':[a['handle'] for a in self.accounts.document().get('accounts', []) if a['platform'] == platform],
                'sync_status':self.db.get('problem_sync:'+pid+':'+handle, None) if handle else None}

    def queue(self, pid, payload):
        pid = normalize(pid)
        account = self.account(pid, payload.handle)
        key = 'problem_sync:'+pid+':'+account['handle']
        previous = self.db.get(key, {})
        if int(time.time()) - previous.get('started_at', 0) < 20:
            raise HTTPException(429, '请等待 20 秒后再次同步本题记录', headers={'Retry-After':'20'})
        result = self.jobs.start('wb_problem_submissions', {'problem_id':pid, 'handle':account['handle']})
        self.db.set(key, {'status':'queued', 'started_at':int(time.time()), 'job_id':result['id']})
        return result

    def _collect(self, pid, account):
        platform, key = pid.split(':', 1)
        cookie = account.get('cookie') or ('' if self.accounts.settings.isolated else os.environ.get(account.get('cookie_env', ''), ''))
        client = Client(cookie=cookie or None, cookie_host=HOSTS[platform] if cookie else None,
                        timeout=20, retries=0)
        try:
            if platform == 'codeforces':
                contest, index = key.split('-')
                out = Collection(platform, account['handle'])
                seen = set()
                for start in (1, 201, 401):
                    response = client.json('https://codeforces.com/api/contest.status',
                        params={'contestId':int(contest), 'handle':account['handle'], 'from':start, 'count':200})
                    if not isinstance(response, dict) or response.get('status') != 'OK' or not isinstance(response.get('result'), list):
                        raise AIError('CF 未返回有效的本场提交记录，请稍后重试')
                    rows = response['result']
                    for row in rows:
                        p = row.get('problem', {})
                        if str(p.get('contestId')) != contest or p.get('index') != index:
                            continue
                        members = row.get('author', {}).get('members', [])
                        if len(members) != 1 or not isinstance(members[0].get('handle'), str) or members[0]['handle'].casefold() != account['handle'].casefold():
                            raise AIError('官方记录的账号不匹配，未保存本次结果')
                        sid, timestamp = row.get('id'), row.get('creationTimeSeconds')
                        if type(sid) is not int or not 0 < sid <= 10**12 or type(timestamp) is not int or not 0 < timestamp <= time.time()+300:
                            raise AIError('官方记录的提交号或时间无效，未保存本次结果')
                        if sid in seen:
                            raise AIError('CF 分页记录重复，已停止同步')
                        seen.add(sid)
                        out.submissions.append(Submission(str(sid), key, timestamp,
                            codeforces_judge_verdict(row.get('verdict')), language=row.get('programmingLanguage'),
                            contest_id=contest, time_ms=row.get('timeConsumedMillis'), memory_bytes=row.get('memoryConsumedBytes'),
                            url=f'https://codeforces.com/contest/{contest}/submission/{sid}',
                            problem={'title':p.get('name'), 'difficulty':p.get('rating'), 'tags':p.get('tags', [])}, raw=row))
                    if len(rows) < 200:
                        out.complete = True
                        break
                return out, '本场比赛最近 600 条提交（仅保留当前题目）'
            from ojcrawler.adapters import atcoder, luogu, nowcoder
            adapter = {'atcoder':atcoder.AtCoder, 'luogu':luogu.Luogu, 'nowcoder':nowcoder.Nowcoder}[platform]
            since = int(time.time())-7*86400 if platform == 'atcoder' else 0
            out = adapter(client, account['handle'], max_pages=3, page_size=100, enrich=False).collect(since)
            out.submissions = [s for s in out.submissions if s.problem_id == key]
            coverage = {'atcoder':'AtCoder Problems 最近 7 天、最多 3 页（第三方同步可能延迟）',
                        'luogu':'公开提交列表最近 3 页',
                        'nowcoder':'公开练习提交最近 3 页；比赛未归档记录可能不可见'}[platform]
            return out, coverage
        finally:
            client.close()

    async def sync(self, pid, handle):
        account = self.account(pid, handle)
        key = 'problem_sync:'+pid+':'+account['handle']
        status = {**self.db.get(key, {}), 'status':'running'}
        self.db.set(key, status)
        try:
            out, coverage = await asyncio.to_thread(self._collect, pid, account)
            # Account edits/removal during collection cannot import into a new binding.
            if self.account(pid, handle) != account:
                raise AIError('账号配置已变化，请重新同步；原记录已保留')
            platform = pid.split(':')[0]
            same_account = out.account.casefold() == handle.casefold() if platform == 'codeforces' else out.account == handle
            if out.platform != platform or not same_account:
                raise AIError('采集结果的平台或账号不匹配，未保存本次结果')
            for row in out.submissions:
                if row.problem_id != pid.split(':', 1)[1] or type(row.submitted_at) is not int or not 0 < row.submitted_at <= time.time()+300:
                    raise AIError('官方记录题号或时间无效，未保存本次结果')
            # Never replace full collection watermarks, profiles or snapshots.
            complete = out.complete
            out.complete = False
            store = Store(self.db.path)
            try:
                store.save('problem-'+uuid.uuid4().hex, int(time.time()), out, capture_snapshot=False)
            finally:
                store.db.close()
            result = {'problem_id':pid, 'handle':handle, 'count':len(out.submissions), 'complete':complete,
                      'coverage':coverage, 'warnings':out.warnings,
                      'message':('已同步本题提交记录' if out.submissions else '在本次查询范围内没有找到本题提交；已保留历史记录')}
            if out.error:
                # Adapter diagnostics contain fixed messages/hosts, not arbitrary HTML.
                result.update(message='本次查询未完整完成，已保留可读取记录和历史记录', error=out.error)
            self.db.set(key, {**status, **result, 'status':'completed' if complete else 'partial', 'finished_at':int(time.time())})
            # Judge results from the local send path can now be reviewed without
            # associating the editor's current text with an unrelated submission.
            for item in self.listing(pid, handle)['items']:
                if item.get('attempt_id') and item['submission_id'] in {s.id for s in out.submissions}:
                    try:
                        if platform != 'codeforces':
                            from .workbench import Link
                            self.workbench.sync_collected(self.workbench.attempt(item['attempt_id']),
                                Link(handle=handle, submission_id=int(item['submission_id'])))
                        await self.workbench.automatic_review(item['attempt_id'])
                    except Exception:
                        pass  # Optional AI failure cannot discard synchronized judge results.
            return result
        except asyncio.CancelledError:
            self.db.set(key, {**status, 'status':'interrupted', 'message':'同步中断，原记录保留；代码不会重新提交'})
            raise
        except Exception as exc:
            self.db.set(key, {**status, 'status':'failed', 'message':'本题记录未完成同步，原记录已保留'})
            if isinstance(exc, (AIError, HTTPException)):
                raise
            raise AIError('本题记录未完成同步，请检查平台网络或登录状态；原记录已保留') from None

    def source(self, pid, payload):
        pid = normalize(pid)
        # Prefer the immutable code already sent through this workbench. It is
        # labelled local_sent/local_unverified, never falsely official_verified.
        handle = self.account(pid, payload.handle)['handle']
        for item in self.listing(pid, handle)['items']:
            if item['submission_id'] == payload.submission_id and item.get('attempt_id'):
                attempt = self.workbench.attempt(item['attempt_id'])
                return {'problem_id':pid, 'submission_id':payload.submission_id, 'handle':handle,
                        'code':attempt['code'], 'language':attempt['language'], 'attempt_id':attempt['id'],
                        'code_verified':attempt['code_verified'], 'provenance':'official' if attempt['code_verified'] else 'local_saved'}
        platform = pid.split(':')[0]
        if platform not in {'codeforces', 'atcoder'}:
            raise HTTPException(400, '判题记录可以同步；此平台暂不能自动读取官方源码，不影响查看结果')
        _, cookie, _ = self.remote.account(pid, handle)
        from .remote_judges import SubmissionError
        driver = self.remote._driver(platform, cookie)
        try:
            observed = driver.source(pid, handle, payload.submission_id)
        except SubmissionError as exc:
            raise HTTPException(400, '判题记录保留；官方源码暂不可读：'+str(exc)) from None
        finally:
            driver.close()
        # Same owner/problem/time validation as actual workbench submissions.
        now = int(time.time())
        language = editor_language(observed.get('language'))
        if not language:
            raise HTTPException(400, '官方源码使用当前编辑器尚未支持的语言；判题记录保留')
        code = observed['code']
        if not code.strip() or len(code.encode()) > 65536:
            raise HTTPException(400, '官方源码为空或超过 64 KiB，未导入')
        intent = {'problem_id':pid, 'handle':handle, 'submission_id':payload.submission_id,
                  'sent_at':observed['submitted_at'], 'created_at':now, 'language':language,
                  'code':code, 'code_hash':hashlib.sha256(code.encode()).hexdigest()}
        # record() independently validates author, problem, SID and timestamp.
        result = self.remote.record(intent, observed)
        aid = result['attempt_id']
        with self.db.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO wb_official_sources VALUES(?,?,?)', (aid,intent['code_hash'],now))
        return {'problem_id':pid, 'submission_id':payload.submission_id, 'handle':handle,
                'code':code, 'language':language, 'attempt_id':aid, 'code_verified':True, 'provenance':'official'}


def register(app, service):
    @app.get('/api/problem-submissions/{pid}')
    def listing(pid: str, handle: str | None = None):
        return service.listing(pid, handle)

    @app.post('/api/problem-submissions/{pid}/sync')
    async def sync(pid: str, payload: AccountRequest):
        return service.queue(pid, payload)

    @app.post('/api/problem-submissions/{pid}/source')
    def source(pid: str, payload: SourceRequest):
        return service.source(pid, payload)

    original = service.jobs.run_workbench

    async def run(kind, payload):
        if kind == 'wb_problem_submissions':
            return await service.sync(payload['problem_id'], payload['handle'])
        return await original(kind, payload)

    service.jobs.run_workbench = run
