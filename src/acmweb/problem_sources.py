"""Allowlisted official problem sources. Account collection is a separate concern."""
import json
import os
import re
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup
from ojcrawler.http import Client
from ojcrawler.models import CrawlError
from ojcrawler.adapters.luogu import page_data
from .codex_bridge import AIError

HOSTS = {'codeforces': 'codeforces.com', 'atcoder': 'atcoder.jp', 'luogu': 'www.luogu.com.cn', 'nowcoder': 'ac.nowcoder.com'}

def normalize(value):
    value = value.strip()
    if value.startswith('https://'):
        u = urlsplit(value)
        if u.netloc != u.hostname or u.query or u.fragment:
            raise AIError('请使用不带查询参数的官方题目链接')
        host = u.hostname
        patterns = {
            'atcoder.jp': (r'/contests/[a-z0-9_-]+/tasks/([a-z0-9_]+?)/?', 'atcoder:{}'),
            'www.luogu.com.cn': (r'/problem/([A-Za-z0-9_]+?)/?', 'luogu:{}'),
            'ac.nowcoder.com': (r'/acm/problem/(\d+)/?', 'nowcoder:{}'),
        }
        if host == 'ac.nowcoder.com':
            m = re.fullmatch(r'/acm/contest/(\d+)/([A-Za-z]\d?)/?', u.path)
            if m: return f'nowcoder:contest-{int(m[1])}-{m[2].upper()}'
        if host in patterns:
            pattern, fmt = patterns[host]; m = re.fullmatch(pattern, u.path)
            if m: value = fmt.format(m[1])
            else: raise AIError('题目链接格式错误')
        elif host not in {'codeforces.com', 'www.codeforces.com'}:
            raise AIError('仅允许四个平台的官方题目地址')
        else:
            m = re.fullmatch(r'/(?:problemset/problem/(\d+)/([A-Za-z]\d?)|contest/(\d+)/problem/([A-Za-z]\d?))/?', u.path)
            if not m: raise AIError('Codeforces 题目链接格式错误')
            value = f'codeforces:{m[1] or m[3]}-{m[2] or m[4]}'
    for pattern, fmt in [
        (r'(?:codeforces:)?([1-9]\d{0,4})-([A-Za-z]\d?)', lambda m:f'codeforces:{int(m[1])}-{m[2].upper()}'),
        (r'atcoder:([a-z0-9]+(?:_[a-z0-9]+)+)', lambda m:'atcoder:'+m[1]),
        (r'(?:luogu:)?((?:P|B|CF|SP|UVA|AT|T|U)\d+[A-Za-z0-9_]*)', lambda m:'luogu:'+m[1].upper()),
        (r'nowcoder:([1-9]\d{0,11})', lambda m:'nowcoder:'+m[1]),
        (r'nowcoder:contest-([1-9]\d{0,11})-([A-Za-z]\d?)', lambda m:f'nowcoder:contest-{m[1]}-{m[2].upper()}'),
    ]:
        m = re.fullmatch(pattern, value)
        if m: return fmt(m)
    raise AIError('请输入官方题目链接或题号，例如 4-A、luogu:P1001、atcoder:abc001_1、nowcoder:1000')

def source_url(pid):
    pid = normalize(pid); platform, key = pid.split(':',1)
    if platform == 'codeforces':
        a,b=key.split('-'); return f'https://codeforces.com/problemset/problem/{a}/{b}'
    if platform == 'atcoder': return f'https://atcoder.jp/contests/{"abs" if key == "practice_1" else key.rsplit("_",1)[0]}/tasks/{key}'
    if platform == 'luogu': return f'https://www.luogu.com.cn/problem/{key}'
    if key.startswith('contest-'):
        _, contest,index=key.split('-'); return f'https://ac.nowcoder.com/acm/contest/{contest}/{index}'
    return f'https://ac.nowcoder.com/acm/problem/{key}'

def _result(pid,title,body,samples,**extra):
    from .statement_math import preserve_math_markup
    preserve_math_markup(body)
    if not title or not body.get_text(strip=True): raise AIError('题面为空或平台要求登录/验证，请打开原题检查访问权限')
    for tag in list(body.select('script,style,iframe,object,form,button,svg,textarea')): tag.decompose()
    images=[]
    allowed={'div','p','span','strong','b','em','i','u','ol','ul','li','table','thead','tbody','tr','td','th','img','br','sup','sub','pre','code','h1','h2','h3','h4','blockquote','hr','a','section'}
    for tag in list(body.find_all(True)):
        if tag.name not in allowed: tag.unwrap(); continue
        attrs={}
        if tag.name in {'img','a'}:
            key='src' if tag.name=='img' else 'href'; raw=tag.get(key) or tag.get('data-src',''); url=urljoin(source_url(pid),raw); u=urlsplit(url)
            if raw and u.scheme=='https' and u.hostname and not u.username and not u.password:
                attrs[key]=url
                if key=='src':
                    attrs['alt']=tag.get('alt',''); images.append({'url':url,'alt':attrs['alt']})
        tag.attrs=attrs
    result={'id':pid,'title':title,'url':source_url(pid),'body_html':str(body),'sections':[body.get_text('\n',strip=True)],'samples':samples,'images':images,'has_images':bool(images),'render_version':2,'source':'official','time_limit':'','memory_limit':'',**extra}
    result['diagnostics']={'sections':1,'samples':len(samples),'images':len(images),'math':bool(re.search(r'\$|\\\(|\\\[',str(body))),'warnings':[]}
    if len(json.dumps(result))>300000: raise AIError('题面过大，请查看原题')
    return result

def parse_statement(html,pid):
    pid=normalize(pid); platform=pid.split(':')[0]; soup=BeautifulSoup(html,'html.parser')
    if platform=='atcoder':
        root=soup.select_one('#task-statement .lang-en') or soup.select_one('#task-statement .lang-ja') or soup.select_one('#task-statement')
        if root is None: raise AIError('AtCoder 题面缺失，可能要求登录或比赛访问权限')
        inputs=[];outputs=[]
        for heading in list(root.find_all(re.compile('^h[1-6]$'))):
            text=heading.get_text(' ',strip=True)
            kind='input' if re.match(r'(Sample Input|入力例)',text) else ('output' if re.match(r'(Sample Output|出力例)',text) else None)
            if not kind: continue
            pre=heading.find_next('pre')
            next_heading=heading.find_next(re.compile('^h[1-6]$'))
            if pre is None or (next_heading and next_heading in heading.find_all_next() and list(heading.find_all_next()).index(next_heading)<list(heading.find_all_next()).index(pre)):
                raise AIError('AtCoder 样例缺少内容')
            (inputs if kind=='input' else outputs).append(pre.get_text('',strip=False).strip())
        for var in list(root.select('var')):
            var.replace_with('\\('+var.get_text()+'\\)')
        if len(inputs)!=len(outputs): raise AIError('AtCoder 样例解析不完整')
        title=soup.select_one('.h2'); title=title.get_text(' ',strip=True).replace('Editorial','').strip() if title else ''
        return _result(pid,title,root,[{'input':a,'output':b} for a,b in zip(inputs,outputs)])
    if platform=='luogu':
        data=page_data(html); p=data.get('problem',{}); content=p.get('contenu') or p.get('content') or p
        if not isinstance(content,dict): raise AIError('洛谷题面结构变化')
        sections=[]
        for key,label in [('background','背景'),('description','题目描述'),('formatI','输入格式'),('inputFormat','输入格式'),('formatO','输出格式'),('outputFormat','输出格式'),('hint','提示')]:
            if content.get(key): sections.append('## '+label+'\n\n'+content[key])
        if not sections: raise AIError('洛谷未返回题面正文，请检查登录/访问限制')
        # Markdown stays Markdown; the frontend renders its math, code and image links.
        text='\n\n'.join(sections); body=BeautifulSoup('<div>placeholder</div>','html.parser')
        samples=[{'input':str(x[0]),'output':str(x[1])} for x in p.get('samples',content.get('samples',[])) if isinstance(x,list) and len(x)==2]
        result=_result(pid,p.get('name') or p.get('title',''),body,samples); result['body_html']='';result['sections']=[text]
        result['images']=[{'url':urljoin(source_url(pid),m[1]),'alt':m[0]} for m in re.findall(r'!\[([^\]]*)\]\((https://[^\s)]+)\)',text)]; result['has_images']=bool(result['images']);result['diagnostics']['images']=len(result['images']);result['diagnostics']['math']=bool(re.search(r'\$|\\\(|\\\[',text))
        return result
    if platform=='nowcoder':
        root=soup.select_one('.subject-describe') or soup.select_one('.question-main')
        if root is None: raise AIError('牛客未返回题面（可能没有权限、需要登录或页面结构变化），请打开原题检查')
        title=soup.select_one('.subject-title') or soup.select_one('.question-title') or soup.select_one('title')
        samples=[]
        for sample in root.select('.question-oi-bd'):
            mods=sample.select('.question-oi-mod'); inputs=[]; outputs=[]
            for mod in mods:
                heading=mod.find('h2'); pre=mod.select_one('.question-oi-cont pre')
                if not heading or pre is None: continue
                label=heading.get_text(strip=True)
                if label=='输入': inputs.append(pre.get_text().strip())
                elif label=='输出': outputs.append(pre.get_text().strip())
            if len(inputs)!=len(outputs): raise AIError('牛客样例解析不完整')
            samples.extend({'input':a,'output':b} for a,b in zip(inputs,outputs))
        return _result(pid,title.get_text(' ',strip=True) if title else '',root,samples)
    raise AIError('Codeforces 使用原有解析器')

def fetch_statement(pid,accounts):
    pid=normalize(pid); platform=pid.split(':')[0]; host=HOSTS[platform]
    account=next((a for a in accounts if a.get('platform')==platform and (a.get('cookie') or a.get('cookie_env'))),{}); failures=[]
    saved_cookie=account.get('cookie') or os.environ.get(account.get('cookie_env',''), '')
    for cookie in [None]+([saved_cookie] if saved_cookie else []):
        client=Client(timeout=12,retries=0,cookie=cookie,cookie_host=host if cookie else None)
        try:
            return parse_statement(client.get(source_url(pid),params={'lang':'en'} if platform=='atcoder' else None),pid)
        except (AIError,CrawlError) as exc: failures.append(str(exc))
        finally: client.close()
    raise AIError('官方题面获取失败：'+'；'.join(failures))

def capabilities(pid,accounts):
    pid=normalize(pid);platform,key=pid.split(':',1); matched=[a for a in accounts if a.get('platform')==platform]
    url=source_url(pid)
    if platform=='atcoder': url=f'https://atcoder.jp/contests/{"abs" if key == "practice_1" else key.rsplit("_",1)[0]}/submit?taskScreenName={key}'
    elif platform=='codeforces': url=f'https://codeforces.com/problemset/submit?contestId={key.split("-")[0]}&submittedProblemIndex={key.split("-")[1]}'
    elif platform=='luogu':url+='#submit'
    enabled = platform in {'codeforces','atcoder'}
    return {'platform':platform,'statement':True,'auto_submit':enabled,'official_submit_url':url,'account_configured':bool(matched),'cookie_configured':any(a.get('cookie') for a in matched),'login_verified':False,'judge_sync':True,'notice':('可在编辑器下方选择账号及官方编译器后直接提交，判题结果自动同步；平台要求重新登录或浏览器验证时会明确停止，不会自动重复交题。' if enabled else '此平台远程交题尚未完成真实适配，请在官方页面提交；可先同步账号记录，再关联提交号。')}
