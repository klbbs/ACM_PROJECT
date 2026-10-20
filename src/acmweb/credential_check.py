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
from .cookie_validation import valid_cookie, COOKIE_ERROR
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
    force_refresh: bool = Field(default=False, strict=True)


def challenge_reason(soup):
    """Recognize challenge evidence, rather than calling every 403 a captcha."""
    text=soup.get_text(' ',strip=True).casefold()
    if soup.select_one('.g-recaptcha,.h-captcha,.cf-turnstile,#captcha,#geetest') or any(
        phrase in text for phrase in ('请完成验证码','请输入验证码','请进行人机验证','请完成人机验证','verify you are human')):
        return 'captcha'
    if soup.select_one('#challenge-running,#cf-challenge-running,script[src*="/cdn-cgi/challenge-platform/"][src*="/orchestrate/chl_page/"]') or any(
        phrase in text for phrase in ('just a moment','checking your browser','正在验证您的浏览器')):
        return 'browser_challenge'
    return None



def nowcoder_identity(soup):
    """Static server bootstrap evidence; never execute JavaScript or use profile IDs.

    The official ACM common.js session module reads globalInfo.ownerId. Its
    bootstrap starts with window.isLogin, followed by the current globalInfo.
    Accept only one such block and a flat literal with one positive ownerId.
    """
    blocks=[]
    for script in soup.find_all('script'):
        if script.get('src') or script.get('type','').casefold() not in {'','text/javascript','application/javascript'}:continue
        if script.find_parent(['template','noscript','textarea','title','style','xmp','iframe']):continue
        source=script.get_text()
        flag=re.match(r'\A\s*window\.isLogin\s*=\s*(true|false)\s*;',source)
        if flag:blocks.append((flag.group(1),source[flag.end():]))
    if len(blocks)!=1:return 'unknown',None
    flag,remaining=blocks[0]
    if flag=='false':return 'expired',None
    literal=re.match(r'\s*window\.globalInfo\s*=\s*\{(.*?)\}\s*;',remaining,re.S)
    if not literal:return 'unknown',None
    tail=remaining[literal.end():]
    if re.search(r'window\.isLogin\s*=|window\.globalInfo\s*=|window\.globalInfo\.ownerId\s*=|window\.globalInfo\[\s*[\"\']ownerId[\"\']\s*\]\s*=',tail):return 'unknown',None
    # A small literal-only tokenizer prevents matching ownerId inside a string,
    # nested public profile, JS expression, template, or comment.
    string=r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
    key=rf'(?:[A-Za-z_$][\w$]*|{string})'
    value=rf'(?:{string}|-?\d+(?:\.\d+)?|true|false|null|undefined)'
    pair=re.compile(rf'\s*({key})\s*:\s*({value})\s*(,|$)')
    fields=literal.group(1);position=0;owners=[]
    while fields[position:].strip():
        match=pair.match(fields,position)
        if not match:return 'unknown',None
        if match.group(1).strip("\"'")=='ownerId':owners.append(match.group(2).strip("\"'"))
        position=match.end()
    if len(owners)==1 and re.fullmatch(r'[1-9][0-9]*',owners[0]):return 'authenticated',owners[0]
    return 'unknown',None


def inspect_identity(platform, html):
    """Return only identity evidence or a status, never page/CSRF/cookie contents."""
    soup=BeautifulSoup(html,'html.parser')
    if challenge_reason(soup):
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
    if platform=='nowcoder':
        status,identity=nowcoder_identity(soup)
        if status!='unknown':return status,identity
    if not logout and login:return 'expired',None
    return 'unknown',None


def diagnostics(result):
    """Expose only fixed local text and official entrypoints, never challenge HTML."""
    reason=result['reason']; status=result['status']
    messages={
        'captcha':'平台要求验证码或人机验证，当前无法确认 Cookie 是否有效。',
        'browser_challenge':'平台正在进行浏览器验证，服务器请求尚未通过验证。',
        'access_restricted':'平台限制了服务器的访问（可能与出口 IP 或浏览器环境有关），不能据此认定 Cookie 过期。',
        'rate_limited':'平台限制了请求频率，请等待后再检测。',
        'timeout':'服务器连接平台超时，请稍后重试。',
        'network_error':'服务器无法连接平台，请检查服务器网络后重试。',
        'upstream_unavailable':'平台服务暂时不可用，请稍后重试。',
        'response_too_large':'平台响应超过检测大小上限，未使用不完整页面判断登录。',
    }
    result['message']=messages.get(reason,{
        'invalid':COOKIE_ERROR,
        'missing':'未保存 Cookie；公开信息仍可采集，登录凭据尚未检测。',
        'authenticated':'官方返回的当前登录身份与绑定账号一致；交题仍需在官方页面完成。',
        'account_mismatch':'当前 Cookie 的登录账号与绑定账号不一致，请重新获取正确账号的 Cookie。',
        'expired':'平台返回登录页面或未登录状态，请重新登录并更新 Cookie。',
        'blocked':'平台返回验证或访问限制，无法判断 Cookie 是否有效。',
        'unknown':'未获得可靠的当前登录身份，不能认定登录成功；本次牛客页面未提供可确认的当前会话身份。' if result['platform']=='nowcoder' else '未获得可靠的当前登录身份，可能是页面结构变化，不能认定登录成功。',
    }[status])
    result['browser_action_url']=URLS[result['platform']]
    result['browser_action_required']=reason in {'captcha','browser_challenge','access_restricted'} or status in {'expired','account_mismatch'}
    if result['browser_action_required']:
        result['guidance']=[
            '打开官方页面，在你自己的浏览器中登录并手动完成出现的验证码或浏览器验证。',
            '使用 Cookie 助手重新获取该平台 Cookie，回到本站粘贴并保存，再点击重新检测。',
            '浏览器验证不会直接传给服务器，新的 Cookie 也不保证服务器出口 IP 获准访问；仍受限时可在官方网页交题。',
        ]
    elif reason=='rate_limited':
        result['guidance']=[f"请至少等待 {result['retry_after_seconds']} 秒，再点击重新检测；连续重试可能延长平台限制。"]
    elif reason in {'timeout','network_error','upstream_unavailable'}:
        result['guidance']=['本次失败发生在服务器到平台的连接；浏览器已登录不代表服务器可访问。稍后重试或联系本站管理员检查出口网络。']
    elif status=='unknown':
        result['guidance']=['可以在官方网页继续做题与交题；公开资料可采集不等于本站已验证登录，重新获取 Cookie 也不会强制把结果改为登录成功。']
    else:result['guidance']=[]
    return result


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

    def check(self,platform,handle,force_refresh=False):
        cookie,digest=self.credential(platform,handle); key=(platform,handle);now=time.time()
        with self.lock:
            cached=self.cache.get(key)
            if not force_refresh and cached and cached[0]==digest and now-cached[1]['checked_at']<60:return dict(cached[1],cached=True)
            retry_at=self.last.get(key,0)+10
            if cached and cached[1].get('reason')=='rate_limited':
                retry_at=max(retry_at,cached[1]['checked_at']+cached[1]['retry_after_seconds'])
            if now<retry_at:
                remaining=max(1,int(retry_at-now+0.999))
                raise HTTPException(429,f'检测过于频繁，请等待 {remaining} 秒后重试',headers={'Retry-After':str(remaining)})
            self.last[key]=now
        result={'platform':platform,'handle':handle,'checked_at':int(now),'status':'missing','login_verified':False,'public_reachable':False,'auto_submit_supported':False,'submission_method':'official_page','reason':'missing_cookie','retry_after_seconds':10,'cached':False}
        identity=None
        if cookie and not valid_cookie(cookie):
            result['status']='invalid';result['reason']='invalid_cookie'
        elif cookie:
            if not SLOTS.acquire(blocking=False):raise HTTPException(429,'平台检测繁忙，请稍后再试')
            try:
                with requests.Session() as session:
                    with session.get(URLS[platform],headers={'Cookie':cookie,'User-Agent':requests.utils.default_user_agent(),'Accept-Language':'en,zh-CN;q=0.8'},timeout=12,allow_redirects=False,stream=True) as response:
                        status=response.status_code
                        headers=requests.structures.CaseInsensitiveDict(response.headers)
                        body=bytearray()
                        truncated=False
                        if status==200 or status in (403,418,429,503):
                            limit=1048576 if status==200 else 65536
                            for chunk in response.iter_content(chunk_size=16384):
                                if len(body)+len(chunk)>limit:
                                    truncated=True
                                    break
                                body.extend(chunk)
                        encoding=response.encoding or 'utf-8'
                        if encoding.casefold() in {'iso-8859-1','latin-1'}:encoding='utf-8'
                        html=body.decode(encoding,errors='replace')
                result['upstream_status']=status
                challenge=None if truncated else challenge_reason(BeautifulSoup(html,'html.parser'))
                if status==429:
                    result.update(status='blocked',reason='rate_limited')
                    delay=headers.get('Retry-After','')
                    result['retry_after_seconds']=max(10,min(3600,int(delay))) if delay.isdigit() and len(delay)<8 else 60
                elif challenge:
                    result.update(status='blocked',reason=challenge)
                elif status in (403,418):result.update(status='blocked',reason='access_restricted')
                elif status==401:result.update(status='expired',reason='not_logged_in')
                elif status in (502,503,504):result.update(status='unknown',reason='upstream_unavailable')
                elif 300<=status<400:
                    location=headers.get('Location','')
                    # No redirects are followed, including same-origin ones.
                    target=urlsplit(location)
                    same_origin=not target.netloc or (target.scheme=='https' and target.netloc==urlsplit(URLS[platform]).netloc)
                    expired=same_origin and re.search(r'/(login|enter)(?:[/?#]|$)',target.path)
                    result.update(status='expired' if expired else 'unknown',reason='not_logged_in' if expired else 'unexpected_redirect')
                elif status==200:
                    result['public_reachable']=True
                    result['status'],identity=('unknown',None) if truncated else inspect_identity(platform,html)
                    result['reason']='response_too_large' if truncated else ('not_logged_in' if result['status']=='expired' else 'page_changed')
                    if identity:
                        matches=identity.casefold()==handle.casefold() if platform=='codeforces' else identity==handle
                        result['status']='authenticated' if matches else 'account_mismatch'
                        result['reason']='identity_verified' if matches else 'identity_mismatch'
                        result['login_verified']=matches
                        result['matched_account']=identity
                else:result.update(status='unknown',reason='unexpected_response')
            except requests.Timeout:result.update(status='unknown',reason='timeout')
            except requests.RequestException:result.update(status='unknown',reason='network_error')
            except (UnicodeError, ValueError, LookupError):result.update(status='unknown',reason='page_changed')
            finally:SLOTS.release()
        diagnostics(result)
        with self.lock:self.cache[key]=(digest,result)
        return dict(result)


def register(app,accounts):
    checker=CredentialChecks(accounts);app.state.credential_checks=checker;accounts.credential_checks=checker
    @app.get('/api/accounts/checks')
    def checks():return checker.statuses()
    @app.post('/api/accounts/check')
    def check(data:CheckInput):return checker.check(data.platform,data.handle,data.force_refresh)
