"""Read-only, fixed-origin credential checks; never retain upstream page contents."""
import hashlib
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

URLS = {'codeforces':'https://codeforces.com/settings/general',
        'atcoder':'https://atcoder.jp/settings',
        'luogu':'https://www.luogu.com.cn/',
        'nowcoder':'https://ac.nowcoder.com/'}
SLOTS = threading.BoundedSemaphore(2)

class CheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal['codeforces','atcoder','luogu','nowcoder']
    handle: str = Field(min_length=1,max_length=150)


def inspect_identity(platform, html):
    """Return only identity evidence or a status, never page/CSRF/cookie contents."""
    soup=BeautifulSoup(html,'html.parser')
    text=soup.get_text(' ',strip=True)
    if soup.select_one('#challenge-running,#cf-challenge-running,.g-recaptcha') or any(x in text.lower() for x in ('just a moment','checking your browser','verify you are human')):
        return 'blocked',None
    login=soup.select_one('form[action*="/login"],form[action*="/enter"],input[name="password"],input[type="password"]')
    # Settings pages can contain password-change fields. An explicit logout and
    # settings form are essential before trusting any public profile hyperlink.
    logout=any(re.search(r'/(logout|signout)(?:[/?#]|$)',a.get('href','')) for a in soup.select('a[href]')) or bool(soup.select_one('form[action*="logout"]'))
    if platform in {'codeforces','atcoder'}:
        pattern=r'^/profile/([^/?#]+)$' if platform=='codeforces' else r'^/users/([^/?#]+)$'
        identities=[]
        nav=soup.select_one('.lang-chooser') if platform=='codeforces' else soup.select_one('#navbar-collapse')
        for a in (nav or soup).select('a[href]'):
            m=re.match(pattern,a['href'])
            if m:identities.append(m[1])
        forms=soup.select('form')
        settings_form=any('settings' in f.get('action','') or f.select_one('input[name="email"],input[name="handle"],input[name="userScreenName"]') for f in forms)
        if nav is not None and logout and settings_form and len(set(identities))==1:return 'authenticated',identities[0]
        if not logout and login:return 'expired',None
        return 'unknown',None
    if platform=='luogu':
        script=soup.find('script',id='lentille-context')
        if script:
            try: data=json.loads(script.get_text())
            except (ValueError,TypeError):return 'unknown',None
            if isinstance(data,dict):
                # data.user on a public profile is the viewed user, not the session.
                containers=[data,data.get('data',{})]
                for container in containers:
                    if not isinstance(container,dict) or 'currentUser' not in container:continue
                    user=container['currentUser']
                    if user is None:return 'expired',None
                    if isinstance(user,dict) and str(user.get('uid','')).isdigit():return 'authenticated',str(user['uid'])
        return 'unknown',None
    # No stable, verified authenticated-identity API is assumed for Nowcoder.
    if not logout and login:return 'expired',None
    return 'unknown',None


class CredentialChecks:
    def __init__(self,accounts):
        self.accounts=accounts;self.cache={};self.lock=threading.Lock();self.last={}

    def credential(self,platform,handle):
        account=next((a for a in self.accounts.document().get('accounts',[]) if a.get('platform')==platform and a.get('handle')==handle),None)
        if not account:raise HTTPException(404,'请先保存此平台账号')
        isolated = getattr(getattr(self.accounts, 'settings', None), 'isolated', False)
        cookie=account.get('cookie') or ('' if isolated else os.environ.get(account.get('cookie_env',''),''))
        digest=hashlib.sha256((platform+'\0'+handle+'\0'+cookie).encode()).hexdigest()
        return cookie,digest

    def statuses(self):
        items=[]
        for a in self.accounts.document().get('accounts',[]):
            _,digest=self.credential(a['platform'],a['handle'])
            with self.lock:cached=self.cache.get((a['platform'],a['handle']))
            if cached and cached[0]==digest and time.time()-cached[1]['checked_at']<300:items.append(dict(cached[1],cached=True))
        return {'items':items}

    def check(self,platform,handle):
        cookie,digest=self.credential(platform,handle); key=(platform,handle);now=time.time()
        with self.lock:
            cached=self.cache.get(key)
            if cached and cached[0]==digest and now-cached[1]['checked_at']<60:return dict(cached[1],cached=True)
            if now-self.last.get(key,0)<10:raise HTTPException(429,'检测过于频繁，请稍后重试')
            self.last[key]=now
        result={'platform':platform,'handle':handle,'checked_at':int(now),'status':'missing','login_verified':False,'public_reachable':False,'auto_submit_supported':False,'submission_method':'official_page'}
        identity=None
        if cookie:
            if not SLOTS.acquire(blocking=False):raise HTTPException(429,'平台检测繁忙，请稍后再试')
            try:
                with requests.Session() as session:
                    with session.get(URLS[platform],headers={'Cookie':cookie,'User-Agent':requests.utils.default_user_agent(),'Accept-Language':'en,zh-CN;q=0.8'},timeout=12,allow_redirects=False,stream=True) as response:
                        status=response.status_code
                        headers=dict(response.headers)
                        body=bytearray()
                        truncated=False
                        if status==200:
                            for chunk in response.iter_content(chunk_size=16384):
                                if len(body)+len(chunk)>1048576:
                                    truncated=True
                                    break
                                body.extend(chunk)
                        html=body.decode(response.encoding or 'utf-8',errors='replace')
                if status in (403,418,429,503):result['status']='blocked'
                elif status in (401,):result['status']='expired'
                elif 300<=status<400:
                    location=headers.get('Location','')
                    # No redirects are followed, including same-origin ones.
                    target=urlsplit(location)
                    same_origin=not target.netloc or (target.scheme=='https' and target.netloc==urlsplit(URLS[platform]).netloc)
                    result['status']='expired' if same_origin and re.search(r'/(login|enter)(?:[/?#]|$)',target.path) else 'unknown'
                elif status==200:
                    result['public_reachable']=True
                    result['status'],identity=('unknown',None) if truncated else inspect_identity(platform,html)
                    if identity:
                        matches=identity.casefold()==handle.casefold() if platform=='codeforces' else identity==handle
                        result['status']='authenticated' if matches else 'account_mismatch'
                        result['login_verified']=matches
                        result['matched_account']=identity
                else:result['status']='unknown'
            except (requests.RequestException, UnicodeError, ValueError):result['status']='unknown'
            finally:SLOTS.release()
        result['message']={
            'missing':'未保存 Cookie；公开信息仍可采集，登录凭据尚未检测。',
            'authenticated':'官方返回的当前登录身份与绑定账号一致；交题仍需在官方页面完成。',
            'account_mismatch':'当前 Cookie 的登录账号与绑定账号不一致，请重新导出正确账号的 Cookie。',
            'expired':'平台返回登录页面或未登录状态，请重新登录并更新 Cookie。',
            'blocked':'平台返回验证或访问限制，无法判断 Cookie 是否有效；请在浏览器验证后重试。',
            'unknown':'未获得可靠的当前登录身份（可能网络失败或页面结构变化），不能认定登录成功。',
        }[result['status']]
        with self.lock:self.cache[key]=(digest,result)
        return dict(result)


def register(app,accounts):
    checker=CredentialChecks(accounts);app.state.credential_checks=checker;accounts.credential_checks=checker
    @app.get('/api/accounts/checks')
    def checks():return checker.statuses()
    @app.post('/api/accounts/check')
    def check(data:CheckInput):return checker.check(data.platform,data.handle)
