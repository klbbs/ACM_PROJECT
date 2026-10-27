"""Private, idempotent platform submissions; a lost response never retries a POST."""
import asyncio
import hashlib
import json
import os
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .codex_bridge import AIError
from .cookie_validation import valid_cookie
from .daily import Complete
from .problem_sources import normalize
from .workbench import ERROR_VERDICTS

DDL = '''
CREATE TABLE IF NOT EXISTS wb_remote_submissions (
 id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, problem_id TEXT NOT NULL,
 handle TEXT NOT NULL, revision INTEGER NOT NULL, language TEXT NOT NULL,
 compiler_id TEXT NOT NULL, compiler_name TEXT NOT NULL DEFAULT '',
 code TEXT NOT NULL, code_hash TEXT NOT NULL, account_revision TEXT NOT NULL,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, sent_at INTEGER,
 status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
 submission_id TEXT, official_url TEXT, attempt_id TEXT, verdict TEXT
);
CREATE INDEX IF NOT EXISTS wb_remote_submissions_problem ON wb_remote_submissions(problem_id,created_at DESC);
'''
ACTIVE = {'queued', 'submitting', 'judging', 'uncertain'}
FINAL = {'AC', 'WA', 'CE', 'RE', 'TLE', 'MLE', 'OLE', 'ILE', 'PE', 'PARTIAL',
         'FAILED', 'CHALLENGED', 'REJECTED', 'SKIPPED'}
PUBLIC_FIELDS = ('id', 'request_key', 'problem_id', 'handle', 'revision', 'language',
                 'compiler_id', 'compiler_name', 'created_at', 'updated_at', 'status',
                 'reason', 'message', 'submission_id', 'official_url', 'attempt_id', 'verdict')


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid')
    problem_id: str = Field(min_length=1, max_length=200)
    handle: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')


class Submit(Options):
    revision: int = Field(ge=1, strict=True)
    compiler_id: str = Field(min_length=1, max_length=50, pattern=r'^[A-Za-z0-9_.+-]+$')
    request_key: str = Field(min_length=32, max_length=64, pattern=r'^[A-Fa-f0-9-]+$')


class Dismiss(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed_not_submitted: Literal[True]


class RemoteSubmissions:
    def __init__(self, db, accounts, workbench, jobs, daily):
        self.db, self.accounts, self.workbench, self.jobs, self.daily = db, accounts, workbench, jobs, daily
        self.closed = False
        self.owner_valid = None
        with db.connect() as conn:
            conn.executescript(DDL)
            conn.execute("UPDATE wb_remote_submissions SET status='failed',reason='interrupted_before_send',message='服务已重启，尚未发出本次提交；可重新提交' WHERE status='queued'")
            conn.execute("UPDATE wb_remote_submissions SET status='uncertain',reason='interrupted',message='服务在提交过程中重启，发送结果尚未确认；不会自动重交，请查看官方记录' WHERE status='submitting'")
            conn.execute("UPDATE wb_remote_submissions SET status='uncertain',reason='interrupted',message='结果查询已中断，可刷新重新核对官方记录；不会重新交题' WHERE status='judging' AND submission_id IS NULL")

    @staticmethod
    def _driver(platform, cookie):
        from .remote_judges import PlatformSession
        return PlatformSession(platform, cookie)

    def account(self, pid, handle):
        platform = pid.split(':', 1)[0]
        account = next((a for a in self.accounts.document().get('accounts', [])
                        if a['platform'] == platform and
                        (a['handle'].casefold() == handle.casefold() if platform == 'codeforces' else a['handle'] == handle)), None)
        if not account:
            raise HTTPException(400, '请先在连接与设置中添加此平台账号')
        cookie = account.get('cookie') or ('' if getattr(self.accounts.settings, 'isolated', False)
                                           else os.environ.get(account.get('cookie_env', ''), ''))
        if not cookie:
            raise HTTPException(400, '尚未保存此账号的登录 Cookie，请先登录平台、保存 Cookie 并检测登录')
        if not valid_cookie(cookie):
            raise HTTPException(400, '已保存的 Cookie 格式无效，请重新获取并保存')
        revision = hashlib.sha256((platform+'\0'+account['handle']+'\0'+cookie).encode()).hexdigest()
        return account['handle'], cookie, revision

    def _row(self, rid):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM wb_remote_submissions WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, '未找到本人的提交任务')
        return dict(row)

    @staticmethod
    def public(row):
        return {k: row[k] for k in PUBLIC_FIELDS}

    def get(self, rid):
        return self.public(self._row(rid))

    def list(self, problem_id=None):
        pid = normalize(problem_id) if problem_id else None
        with self.db.connect() as conn:
            rows = conn.execute('SELECT * FROM wb_remote_submissions '+
                                ('WHERE problem_id=? ' if pid else '')+
                                'ORDER BY created_at DESC,rowid DESC LIMIT 30', (pid,) if pid else ()).fetchall()
            blocking = conn.execute("""SELECT * FROM wb_remote_submissions
                WHERE status IN ('queued','submitting','judging','uncertain')
                AND problem_id<>? AND substr(problem_id,1,instr(problem_id,':')-1)=?
                ORDER BY created_at DESC,rowid DESC LIMIT 30""",
                (pid, pid.split(':', 1)[0])).fetchall() if pid else []
        return {'items': [self.public(row) for row in rows],
                'blocking': [self.public(row) for row in blocking]}

    def update(self, rid, **values):
        allowed = {'status', 'reason', 'message', 'sent_at', 'submission_id', 'official_url',
                   'attempt_id', 'verdict', 'compiler_name'}
        assert values and set(values) <= allowed
        with self.db.connect() as conn:
            conn.execute('UPDATE wb_remote_submissions SET '+','.join(k+'=?' for k in values)+
                         ',updated_at=? WHERE id=?', (*values.values(), int(time.time()), rid))

    def options(self, payload):
        from .remote_judges import SubmissionError
        pid = normalize(payload.problem_id)
        handle, cookie, _ = self.account(pid, payload.handle)
        driver = self._driver(pid.split(':')[0], cookie)
        try:
            items = driver.options(pid, handle)
            return {'platform': pid.split(':')[0], 'available': True, 'items': items}
        except SubmissionError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            driver.close()

    def queue(self, payload, owner=None):
        pid = normalize(payload.problem_id)
        # Replaying the same request remains safe even when the account/draft has since changed.
        with self.db.connect() as conn:
            existing = conn.execute('SELECT * FROM wb_remote_submissions WHERE request_key=?', (payload.request_key,)).fetchone()
        if existing:
            self._same_request(existing, payload, pid)
            return self.public(existing)
        handle, _, account_revision = self.account(pid, payload.handle)
        now, rid = int(time.time()), uuid.uuid4().hex
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT * FROM wb_remote_submissions WHERE request_key=?', (payload.request_key,)).fetchone()
            if existing:
                self._same_request(existing, payload, pid)
                return self.public(existing)
            pending = conn.execute("SELECT problem_id,handle,status FROM wb_remote_submissions WHERE status IN ('queued','submitting','judging','uncertain')").fetchall()
            if any(r['problem_id'].split(':')[0] == pid.split(':')[0] and
                   (r['handle'].casefold() == handle.casefold() if pid.startswith('codeforces:') else r['handle'] == handle)
                   for r in pending):
                raise HTTPException(409, '此平台账号已有提交正在处理或发送结果未确认，请先检查该任务，避免重复交题')
            if conn.execute('SELECT COUNT(*) FROM wb_remote_submissions').fetchone()[0] >= 2000:
                raise HTTPException(409, '提交任务记录已达到上限，请联系管理员归档')
            draft = conn.execute('SELECT * FROM wb_drafts WHERE problem_id=?', (pid,)).fetchone()
            if not draft or draft['revision'] != payload.revision:
                raise HTTPException(409, '代码版本已变化，请等待自动保存完成后重新提交')
            if not draft['code'].strip():
                raise HTTPException(400, '请先输入代码')
            digest = hashlib.sha256(draft['code'].encode()).hexdigest()
            conn.execute('''INSERT INTO wb_remote_submissions
                (id,request_key,problem_id,handle,revision,language,compiler_id,code,code_hash,
                 account_revision,created_at,updated_at,status,message) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid,payload.request_key,pid,handle,payload.revision,draft['language'],payload.compiler_id,
                 draft['code'],digest,account_revision,now,now,'queued','已排队，尚未向平台发送代码'))
        try:
            self.jobs.start('wb_remote_submit', {'intent_id': rid, 'owner': owner})
        except Exception:
            self.update(rid, status='failed', reason='queue_rejected', message='当前任务已满，尚未向平台发送代码；请稍后重新提交')
        return self.get(rid)

    @staticmethod
    def _same_request(row, payload, pid):
        handle_matches = row['handle'].casefold() == payload.handle.casefold() if pid.startswith('codeforces:') else row['handle'] == payload.handle
        if row['problem_id'] != pid or not handle_matches or row['revision'] != payload.revision or row['compiler_id'] != payload.compiler_id:
            raise HTTPException(409, '同一次提交请求不能改为其他账号、题目、代码版本或编译器')

    def _check_owner_configuration(self, row, owner=None):
        if self.closed:
            raise AIError('工作区已关闭，提交停止；请查看任务结果后再操作')
        if owner and (not self.owner_valid or not self.owner_valid(owner)):
            raise AIError('本站登录会话已失效，尚未发送代码')
        _, _, revision = self.account(row['problem_id'], row['handle'])
        if revision != row['account_revision']:
            raise AIError('本次提交期间账号凭据已更改，请先确认官方记录再重新提交')

    async def execute(self, rid, owner=None):
        from .remote_judges import SubmissionError
        row = self._row(rid)
        if row['status'] != 'queued':
            return self.get(rid)
        with self.db.connect() as conn:
            claimed = conn.execute("UPDATE wb_remote_submissions SET status='submitting',sent_at=?,updated_at=?,message=? WHERE id=? AND status='queued'",
                                   (int(time.time()),int(time.time()),'正在校验官方表单并向平台提交，请勿重复点击',rid)).rowcount
        if not claimed:
            return self.get(rid)
        driver = None
        try:
            self._check_owner_configuration(row, owner)
            _, cookie, _ = self.account(row['problem_id'], row['handle'])
            driver = self._driver(row['problem_id'].split(':')[0], cookie)
            driver.before_send = lambda: self._check_owner_configuration(row, owner)
            accepted = await asyncio.to_thread(driver.submit, row['problem_id'], row['handle'], row['code'], row['compiler_id'])
            sid = str(accepted['id'])
            if not sid.isdecimal() or not 0 < int(sid) <= 10**12:
                self.update(rid, status='uncertain', reason='unknown_receipt', message='平台响应未包含可确认的提交号；不会自动重交，请检查官方记录')
                return self.get(rid)
            self.update(rid, status='judging', submission_id=sid, compiler_name=accepted.get('compiler_name', ''),
                        official_url=accepted['url'], message='平台已接收代码，正在读取官方判题结果')
            await self.watch(rid, driver)
        except SubmissionError as exc:
            self.update(rid, status='uncertain' if exc.uncertain else 'blocked', reason=exc.reason,
                        message=str(exc))
        except asyncio.CancelledError:
            current = self._row(rid)
            if current['status'] == 'submitting':
                self.update(rid, status='uncertain', reason='interrupted', message='提交过程已中断，发送结果未确认；不会自动重交，请查看官方记录')
            raise
        except (HTTPException, AIError):
            current = self._row(rid)
            if current['submission_id']:
                self.update(rid, status='judging', reason='judge_unavailable',
                            message='代码已被平台受理，暂未完成结果同步；请刷新判题，不要重新提交')
            else:
                self.update(rid, status='blocked', reason='configuration_changed',
                            message='工作区或账号凭据已变化，尚未发送本次代码；请重新检查账号配置')
        except Exception:
            current = self._row(rid)
            self.update(rid, status='judging' if current['submission_id'] else 'uncertain',
                        reason='submission_unconfirmed', message='本次提交未能确认，系统不会自动重交，请先查看官方记录')
        finally:
            if driver:
                driver.close()
        return self.get(rid)

    async def watch(self, rid, driver, *, rounds=25):
        from .remote_judges import SubmissionError
        for number in range(rounds):
            row = self._row(rid)
            if self.closed or row['status'] not in {'judging', 'uncertain'} or not row['submission_id']:
                return
            try:
                observed = await asyncio.to_thread(driver.judge, row['problem_id'], row['handle'], row['submission_id'])
                if self._row(rid)['status'] == 'completed':
                    return  # A concurrent explicit problem sync already finished it.
                result = self.record(row, observed)
                if result['verdict'] in FINAL:
                    self.update(rid, status='completed', reason='', message='官方判题：'+result['verdict'], **result)
                    await self.after_judge(result['attempt_id'])
                    return
                self.update(rid, status='judging', reason='', message='官方判题中，请稍候', **result)
            except SubmissionError as exc:
                if self._row(rid)['status'] == 'completed':
                    return
                self.update(rid, status='judging', reason=exc.reason,
                    message='代码已提交；查询判题未完成：' + str(exc) + ' 不会重新交题。')
                if exc.reason in {'rate_limited', 'login_required', 'account_mismatch', 'challenge', 'access_restricted'}:
                    return
            except (AIError, HTTPException):
                if self._row(rid)['status'] == 'completed':
                    return
                self.update(rid, status='judging', reason='judge_unavailable',
                            message='代码已提交，但暂未取得可核验的官方结果；可稍后刷新判题，不会重交代码')
            if number + 1 < rounds:
                await asyncio.sleep(4)

    async def reconcile_collected(self, pid, handle, submissions, *, collected_at=None):
        """Reuse freshly fetched official CF evidence for known sent receipts.

        Never guesses a SID, uses a draft, or posts code. Incomplete/mismatched
        records leave the existing intent unchanged.
        """
        if not pid.startswith('codeforces:'):
            return
        from .remote_judges import PlatformSession, SubmissionError
        by_id = {str(item.id): item for item in submissions}
        with self.db.connect() as connection:
            intents = connection.execute("SELECT * FROM wb_remote_submissions WHERE problem_id=? AND lower(handle)=lower(?) "
                "AND status IN ('judging','uncertain') AND submission_id IS NOT NULL", (pid, handle)).fetchall()
        for saved in intents:
            row = dict(saved)
            # A concurrent poll, refresh or dismiss may have moved this intent
            # while the collection ran. Never replay stale evidence over it.
            current = self._row(row['id'])
            if current['status'] not in {'judging', 'uncertain'} or (
                    collected_at is not None and current['updated_at'] > collected_at):
                continue
            item = by_id.get(str(row['submission_id']))
            if item is None or item.problem_id != pid.split(':', 1)[1] or not isinstance(item.raw, dict):
                continue
            try:
                if hashlib.sha256(row['code'].encode()).hexdigest() != row['code_hash']:
                    continue
                observed = PlatformSession.codeforces_record(pid.split(':', 1)[1], handle, str(item.id), item.raw)
                result = self.record(row, observed)
            except (SubmissionError, AIError, HTTPException, ValueError, TypeError):
                continue
            self.update(row['id'], status='completed' if result['verdict'] in FINAL else 'judging',
                reason='', message='已通过同步本题提交取得官方判题：' + result['verdict'], **result)
            await self.after_judge(result['attempt_id'])

    def record(self, intent, observed):
        pid, platform = intent['problem_id'], intent['problem_id'].split(':')[0]
        author = observed.get('account', '')
        matches = author.casefold() == intent['handle'].casefold() if platform == 'codeforces' else author == intent['handle']
        timestamp = observed.get('submitted_at')
        if (str(observed.get('id')) != intent['submission_id'] or not matches
                or normalize(platform+':'+str(observed.get('problem_id', ''))) != pid
                or type(timestamp) is not int or not (intent['sent_at']-60 <= timestamp <= int(time.time())+300)):
            raise AIError('官方记录的账号、题号、提交号或提交时间不匹配，未关联代码')
        # The editor mode is a user's draft preference, not the compiler that
        # the official platform actually used (e.g. cpp17 + CF C++23).
        from .problem_submissions import editor_language
        language = editor_language(observed.get('language'))
        def same_language_family(left, right):
            return left == right or left in {'cpp17', 'cpp20'} and right in {'cpp17', 'cpp20'}
        if (not language or not same_language_family(intent['language'], language)
                or hashlib.sha256(intent['code'].encode()).hexdigest() != intent['code_hash']):
            raise AIError('官方编译语言与本次代码语言不匹配或源码校验失败，未关联代码')
        status = observed.get('verdict')
        status = status if status in FINAL else 'PENDING'
        now = int(time.time())
        judge = {key: observed.get(key) for key in ('language','submitted_at','time_ms','memory_bytes','score','url')}
        judge.update(verdict=status, platform=platform, source='official_remote_submission')
        if platform == 'codeforces':
            judge['creationTimeSeconds'] = timestamp
            judge['passedTestCount'] = (observed.get('raw') or {}).get('passedTestCount')
            judge['timeConsumedMillis'] = observed.get('time_ms')
            judge['memoryConsumedBytes'] = observed.get('memory_bytes')
        from dataclasses import asdict
        from ojcrawler.models import Submission
        raw = {key: value for key, value in observed.items() if key not in {'account', 'code', 'cookie', 'sourceCode'}}
        standard = asdict(Submission(str(intent['submission_id']), pid.split(':',1)[1], timestamp, status,
            language=observed.get('language'), contest_id=observed.get('contest_id'),
            time_ms=observed.get('time_ms'), memory_bytes=observed.get('memory_bytes'),
            score=observed.get('score'), url=observed.get('url'), raw=raw))
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute("SELECT * FROM wb_attempts WHERE substr(problem_id,1,instr(problem_id,':')-1)=? AND "+
                               ('lower(handle)=lower(?)' if platform=='codeforces' else 'handle=?')+
                               ' AND submission_id=?', (platform,intent['handle'],int(intent['submission_id']))).fetchone()
            if old and (old['problem_id'] != pid or old['code_hash'] != intent['code_hash']
                    or hashlib.sha256(old['code'].encode()).hexdigest() != old['code_hash']
                    or not same_language_family(old['language'], language)):
                raise AIError('此官方提交已有另一份代码记录，不能覆盖')
            aid = old['id'] if old else uuid.uuid4().hex
            if not old:
                conn.execute('''INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,handle,submission_id)
                    VALUES(?,?,?,?,?,?,?,?)''', (aid,pid,language,intent['code'],intent['code_hash'],
                    intent['created_at'],intent['handle'],int(intent['submission_id'])))
                conn.execute('INSERT INTO wb_submission_code_claims VALUES(?,?,?)', (aid,intent['code_hash'],now))
            elif old['language'] != language:
                conn.execute('UPDATE wb_attempts SET language=?,review=NULL WHERE id=?', (language,aid))
                conn.execute('DELETE FROM wb_submission_reviews WHERE attempt_id=?', (aid,))
            encoded = json.dumps(judge, ensure_ascii=False)
            conn.execute('''UPDATE wb_attempts SET verdict=?,judge_data=?,last_synced=?,
                review=CASE WHEN verdict=? AND judge_data=? THEN review ELSE NULL END WHERE id=?''',
                (status,encoded,now,status,encoded,aid))
            # The immutable code is the exact payload sent by ACM Lab and the
            # official receipt was checked against this account, problem and SID.
            conn.execute('INSERT OR REPLACE INTO wb_official_sources VALUES(?,?,?)',
                         (aid,intent['code_hash'],now))
            conn.execute('INSERT OR REPLACE INTO wb_submission_source_bindings VALUES(?,?,?,?,?,?)',
                         (aid,pid,intent['handle'],int(intent['submission_id']),language,intent['code_hash']))
            existing = conn.execute("SELECT account,data FROM submissions WHERE platform=? AND "+
                                    ('lower(account)=lower(?)' if platform=='codeforces' else 'account=?')+
                                    ' AND id=?', (platform,intent['handle'],intent['submission_id'])).fetchone()
            if existing:
                standard['problem'] = json.loads(existing['data']).get('problem', {})
            if not standard['problem']:
                catalog = conn.execute('SELECT data FROM problem_catalog WHERE platform=? AND id=?', (platform,pid)).fetchone()
                if catalog:
                    item = json.loads(catalog['data'])
                    standard['problem'] = {key:item.get(key) for key in ('title','difficulty','tags')}
            conn.execute('''INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(platform,account,id) DO UPDATE SET verdict=excluded.verdict,data=excluded.data,updated_at=excluded.updated_at''',
                (platform,existing['account'] if existing else intent['handle'],intent['submission_id'],pid.split(':',1)[1],
                 timestamp,status,json.dumps(standard,ensure_ascii=False),now))
        return {'attempt_id':aid, 'verdict':status}

    async def after_judge(self, aid):
        attempt = self.workbench.attempt(aid)
        if attempt['verdict'] == 'AC' and self.daily:
            timestamp = attempt['judge_data']['submitted_at']
            if self.daily.today(timestamp) == self.daily.today():
                try:
                    await asyncio.to_thread(self.daily.complete, Complete(problem_id=attempt['problem_id']))
                except HTTPException:
                    pass

    def refresh(self, rid):
        row = self._row(rid)
        if row['reason'] == 'rate_limited':
            # Bounded manual retry: at most one refresh per minute, never a resend.
            retry = min(60, 60 - (int(time.time()) - row['updated_at']))
            if retry > 0:
                raise HTTPException(429, '官方平台暂时限制判题查询频率，请等待后重试；代码不会重交',
                                    headers={'Retry-After': str(retry)})
        if not row['submission_id']:
            if row['status'] != 'uncertain':
                return self.public(row)
        if any(kind in {'wb_remote_submit','wb_remote_refresh'} and not task.done() for kind, task in self.jobs.tasks.values()):
            return self.public(row)
        self.update(rid, status='judging' if row['submission_id'] else 'uncertain', reason='', message='正在读取官方记录并核对当次代码，不会重交代码' if not row['submission_id'] else '正在重新读取官方判题结果，不会重交代码')
        try:
            self.jobs.start('wb_remote_refresh', {'intent_id':rid})
        except Exception:
            self.update(rid, reason='queue_rejected', message='判题刷新尚未排入任务，请稍后重试；代码不会重交')
        return self.get(rid)

    async def reread(self, rid):
        from .remote_judges import SubmissionError
        row = self._row(rid)
        driver = None
        try:
            _, cookie, _ = self.account(row['problem_id'], row['handle'])
            driver = self._driver(row['problem_id'].split(':')[0], cookie)
            if not row['submission_id']:
                if hashlib.sha256(row['code'].encode()).hexdigest() != row['code_hash']:
                    raise SubmissionError('record_mismatch')
                credentials = None
                if row['problem_id'].startswith('codeforces:'):
                    account = next((item for item in self.accounts.document().get('accounts', [])
                        if item['platform'] == 'codeforces' and item['handle'].casefold() == row['handle'].casefold()), {})
                    if not account.get('api_key') or not account.get('api_secret'):
                        raise SubmissionError('api_required')
                    credentials = {name: account[name] for name in ('api_key', 'api_secret')}
                observed = await asyncio.to_thread(driver.find_receipt, row['problem_id'], row['handle'], row['sent_at'], row['code_hash'],
                    **({'api_credentials': credentials} if credentials is not None else {}))
                if hashlib.sha256(observed['code'].encode()).hexdigest() != row['code_hash']:
                    raise SubmissionError('record_mismatch')
                recovered_sid = str(observed['id'])
                result = self.record({**row, 'submission_id': recovered_sid}, observed)
                with self.db.connect() as conn:
                    conn.execute('INSERT OR REPLACE INTO wb_official_sources VALUES(?,?,?)',
                                 (result['attempt_id'],row['code_hash'],int(time.time())))
                self.update(rid, status='completed' if result['verdict'] in FINAL else 'judging',
                            submission_id=recovered_sid, official_url=observed['url'],
                            reason='', message='已通过本人官方记录和源码核对找回提交结果', **result)
                await self.after_judge(result['attempt_id'])
            else:
                await self.watch(rid, driver, rounds=1)
        except SubmissionError as error:
            self.update(rid, status='judging' if row['submission_id'] else 'uncertain', reason=error.reason,
                        message='尚未核验本次提交：' + str(error) + ' 原记录已保留，不会重新交题。')
        except HTTPException:
            self.update(rid, status='judging' if row['submission_id'] else 'uncertain', reason='configuration_changed',
                        message='当前平台账号或登录凭据不可用，请在连接与设置检查该账号后重试；不会重新交题')
        except AIError:
            self.update(rid, status='judging' if row['submission_id'] else 'uncertain', reason='record_mismatch',
                        message='官方记录与本次账号、题号、提交时间或源码不匹配，未关联代码；不会重新交题')
        except Exception:
            self.update(rid, status='judging' if row['submission_id'] else 'uncertain', reason='receipt_unavailable',
                        message='官方结果查询未能完成，原提交记录已保留；可稍后刷新，不会重新交题')
        finally:
            if driver:
                driver.close()
        return self.get(rid)

    def dismiss(self, rid):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM wb_remote_submissions WHERE id=?', (rid,)).fetchone()
            if not row:
                raise HTTPException(404, '未找到本人的提交任务')
            if row['status'] != 'uncertain' or row['submission_id']:
                raise HTTPException(409, '仅能解除尚无提交号的未确认发送；已受理的代码不能撤销')
            conn.execute("UPDATE wb_remote_submissions SET status='failed',reason='user_confirmed_not_submitted',message='本人已在官方确认未提交成功，已解除本次提交阻塞',updated_at=? WHERE id=?", (int(time.time()),rid))
        return self.get(rid)


def register(app, service):
    from fastapi import Request
    service.owner_valid = lambda owner: app.state.browser_login.owner_valid(owner)
    @app.get('/api/remote-submissions')
    def listing(problem_id: str | None = None):
        return service.list(problem_id)

    @app.get('/api/remote-submissions/{rid}')
    def get(rid: str):
        return service.get(rid)

    @app.post('/api/remote-submissions/options')
    def options(payload: Options):
        return service.options(payload)

    @app.post('/api/remote-submissions')
    async def submit(payload: Submit, request: Request):
        owner = request.scope.get('state', {}).get('browser_login_owner')
        if getattr(app.state, 'multi_child', False) and not owner:
            raise HTTPException(401, '请重新登录 ACM Lab')
        return service.queue(payload, owner)

    @app.post('/api/remote-submissions/{rid}/refresh')
    async def refresh(rid: str):
        return service.refresh(rid)

    @app.post('/api/remote-submissions/{rid}/dismiss')
    def dismiss(rid: str, payload: Dismiss):
        return service.dismiss(rid)

    original = service.jobs.run_workbench

    async def run(kind, payload):
        if kind == 'wb_remote_submit':
            return await service.execute(payload['intent_id'], payload.get('owner'))
        if kind == 'wb_remote_refresh':
            return await service.reread(payload['intent_id'])
        return await original(kind, payload)

    service.jobs.run_workbench = run
