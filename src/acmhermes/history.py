"""Selection history coverage, independent of the dashboard's day-only watermark."""
import asyncio
import os
import threading
import time
import uuid
from ojcrawler.adapters import ADAPTERS
from ojcrawler.config import load
from ojcrawler.http import Client
from ojcrawler.storage import Store, run_lock
from acmweb.catalog import PLATFORMS
from acmweb.codex_bridge import AIError

HOSTS={'codeforces':'codeforces.com','atcoder':'atcoder.jp','luogu':'www.luogu.com.cn','nowcoder':'ac.nowcoder.com'}
COVERAGE={'codeforces':'CF 账号可见提交；不保证未归入用户历史的私有 Gym/Mashup',
          'atcoder':'AtCoder Problems 公开索引，可能存在同步延迟',
          'luogu':'洛谷账号可见记录，不包含不可见私有提交',
          'nowcoder':'牛客公开练习记录，不包含未归档比赛记录'}
DDL='''CREATE TABLE IF NOT EXISTS hermes_history_sync (
    platform TEXT NOT NULL, account_key TEXT NOT NULL, covered_until INTEGER NOT NULL,
    PRIMARY KEY(platform,account_key)
);'''


class Stopped(BaseException):
    """Cooperative thread stop; deliberately not swallowed by adapter fallbacks."""


class HistoryClient(Client):
    def __init__(self, stop, **kwargs):
        self.stop=stop
        super().__init__(**kwargs)
        original_get=self.session.get
        def checked_get(*args,**options):
            # Client.get also waits for host pacing. Check again at the actual
            # send boundary so cancellation during that wait cannot send a new
            # request. Redirects within an in-flight request remain best effort.
            if self.stop.is_set():raise Stopped()
            return original_get(*args,**options)
        self.session.get=checked_get

    def get(self,*args,**kwargs):
        if self.stop.is_set():raise Stopped()
        value=super().get(*args,**kwargs)
        if self.stop.is_set():raise Stopped()
        return value


def bindings(accounts):
    """Fresh, exact login evidence only; saving a public handle is insufficient."""
    checker=accounts.credential_checks
    return tuple(sorted((r['platform'],r['handle']) for r in checker.statuses()['items']
                        if r.get('status')=='authenticated' and r.get('login_verified')
                        and r.get('platform') in PLATFORMS and r.get('handle')))


def synchronize(database,accounts,stop):
    """Runs in a worker; partial rows persist, incomplete coverage never advances."""
    expected=bindings(accounts)
    if not expected:
        raise AIError('没有已验证登录的平台，无法同步历史提交；请先连接并检测平台账号')
    revision=accounts.revision()
    config=load(accounts.effective_path)
    selected=[a for a in config.accounts if (a.platform,a.handle) in expected]
    if {(a.platform,a.handle) for a in selected} != set(expected):
        raise AIError('平台账号在同步前已变化，请重新连接后再抽题')
    rows=[]
    try:
        with run_lock(database.path):
            store=Store(database.path)
            try:
                with store.db:store.db.executescript(DDL)
                for account in selected:
                    if stop.is_set():raise Stopped()
                    if accounts.revision()!=revision:
                        raise AIError('历史同步期间平台配置已变化，未继续抽题；请重新发起')
                    key=account.handle.casefold() if account.platform=='codeforces' else account.handle
                    previous=store.db.execute('SELECT covered_until FROM hermes_history_sync WHERE platform=? AND account_key=?',
                                              (account.platform,key)).fetchone()
                    since=max(0,previous[0]-config.overlap_seconds) if previous else 0
                    started=int(time.time())
                    cookie=account.cookie or (os.environ.get(account.cookie_env) if account.cookie_env else None)
                    client=HistoryClient(stop,interval=config.interval,timeout=config.timeout,retries=0,
                                         cookie=cookie,cookie_host=HOSTS[account.platform])
                    try:
                        try:
                            adapter=ADAPTERS[account.platform](client,account.handle,max_pages=config.max_pages,
                                page_size=config.page_size,enrich=False)
                            result=adapter.collect(since)
                        except Exception:
                            # An OJ outage excludes only that platform for this
                            # draw. Cancellation is BaseException and propagates.
                            result=None
                    finally:client.close()
                    if stop.is_set():raise Stopped()
                    if accounts.revision()!=revision:
                        raise AIError('历史同步期间平台配置已变化，未保存旧账号响应或继续抽题')
                    if result is None:
                        rows.append({'platform':account.platform,'handle':account.handle,'complete':False,
                            'count':0,'since':since,'coverage':COVERAGE[account.platform],
                            'reason':'history_unavailable'})
                        continue
                    # Keep partial official rows for exclusion, but no profile
                    # replacement or misleading global checkpoint on failure.
                    store.save('hermes-'+uuid.uuid4().hex,started,result,capture_snapshot=False)
                    row={'platform':account.platform,'handle':account.handle,'complete':result.complete,
                         'count':len(result.submissions),'since':since,'coverage':COVERAGE[account.platform]}
                    rows.append(row)
                    if not result.complete:
                        # Adapter error text is not returned/logged: it may
                        # include private upstream response data.
                        row['reason']='history_incomplete'
                        continue
                    with store.db:
                        store.db.execute('''INSERT INTO hermes_history_sync VALUES(?,?,?)
                            ON CONFLICT(platform,account_key) DO UPDATE SET covered_until=excluded.covered_until''',
                            (account.platform,key,started))
                failed={row['platform'] for row in rows if not row['complete']}
                usable=sorted({row['platform'] for row in rows if row['complete']}-failed)
                if not usable:
                    raise AIError('所有可用平台历史同步未完整，暂时没有可安全排除已做题的平台；请稍后重试')
                return {'complete':not failed,'accounts':rows,'usable_platforms':usable,
                    'unavailable_platforms':sorted(failed),
                    'notice':'仅从本次历史同步完整的平台抽题；故障平台本轮跳过，已读到的记录保留且失败进度不推进。'}
            finally:store.close()
    except RuntimeError:
        raise AIError('另一个提交同步正在运行，请等待结束后重新抽题；未调用 AI') from None


async def sync_history(database,accounts):
    stop=threading.Event()
    try:
        return await asyncio.to_thread(synchronize,database,accounts,stop)
    finally:
        # asyncio cannot kill an in-flight requests call. The thread checks
        # before/after each request, before persisting, and between accounts.
        stop.set()
