"""Official judge submission transports. Never retry a source-code POST.

A successful HTTP response alone is not proof of submission. Acceptance needs
an explicit official ID or one unambiguous new matching record, then an
independent check of the record's owner and problem.
"""
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from ojcrawler.http import browser_headers
from ojcrawler.models import verdict
from ojcrawler.storage import codeforces_judge_verdict
from .codex_bridge import AIError
from .cookie_validation import prepare_cookie
from .problem_sources import atcoder_contest, normalize


_MESSAGES = {
    'unsupported': '此平台暂未提供经过验证的远程交题通道，请使用官方页面提交。',
    'invalid_input': '题号、账号、提交号或代码格式无效。',
    'login_required': '平台登录已失效，请重新获取完整 Cookie 并检测登录。',
    'account_mismatch': '当前 Cookie 的平台身份与所选提交账号不一致。',
    'challenge': '官方平台要求浏览器安全验证，此读取入口暂不可用；请在官方浏览器处理验证。',
    'access_restricted': '官方平台拒绝此入口的服务器访问，不能据此认定登录失效；已有记录保留。',
    'api_rejected': 'Codeforces 官方 API 未接受读取请求，请检查 Key / Secret 是否属于当前账号及服务器时间；判题记录已保留。',
    'language_unsupported': '官方当前不支持所选编译器，请刷新编译器列表重新选择。',
    'form_changed': '官方提交表单结构已变化，已停止提交，请使用官方页面。',
    'network': '连接官方平台失败，请稍后重试。',
    'rate_limited': '官方平台限制请求频率，请稍后重试。',
    'rejected': '官方未接受本次提交，请检查比赛权限、参赛状态及提交间隔。',
    'uncertain': '无法确认官方是否已受理本次代码，请先查看官方提交记录，勿立即重复提交。',
    'record_mismatch': '官方判题记录的账号或题号与本次提交不一致。',
    'record_unavailable': '官方尚未返回可核验的提交记录，请稍后刷新判题。',
    'source_unavailable': '已取得该次官方判题记录，但源码入口或内容尚不可核验；可能是官方权限、动态页面或访问限制。不会用当前草稿替代。',
}


class SubmissionError(Exception):
    def __init__(self, reason, message=None, uncertain=False):
        # Callers cannot accidentally expose an official HTML/error/secret.
        self.reason = reason if reason in _MESSAGES else 'uncertain'
        self.uncertain = bool(uncertain)
        super().__init__(_MESSAGES[self.reason])


class PlatformSession:
    HOSTS = {'codeforces': 'codeforces.com', 'atcoder': 'atcoder.jp',
             'luogu': 'www.luogu.com.cn', 'nowcoder': 'ac.nowcoder.com'}

    def __init__(self, platform, cookie):
        if platform not in self.HOSTS:
            raise SubmissionError('unsupported')
        self.before_send = None
        self._read_gates = {}
        self.platform = platform
        self.host = self.HOSTS[platform]
        self.base = 'https://' + self.host
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(browser_headers())
        # An explicitly absent Cookie is permitted only for CF's signed,
        # read-only API. Existing remote-submit callers still require Cookie.
        self._api_only = cookie is None and platform == 'codeforces'
        if self._api_only:
            return
        try:
            prepared = prepare_cookie(cookie)
        except ValueError:
            self.session.close()
            raise SubmissionError('login_required') from None
        for part in prepared.split(';'):
            name, _, value = part.strip().partition('=')
            self.session.cookies.set(name, value, domain=self.host, path='/', secure=True)

    def close(self):
        self.session.close()

    def _url(self, value):
        url = urljoin(self.base, value)
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != self.host or parsed.username
                or parsed.password or parsed.port or parsed.fragment or '\\' in url
                or any(ord(c) < 32 for c in url)):
            raise SubmissionError('form_changed')
        return url

    def _request(self, method, url, *, data=None, headers=None):
        url = self._url(url)
        if self._api_only and (method != 'GET' or urlsplit(url).path not in {
                '/api/contest.status', '/api/user.status'}):
            raise SubmissionError('login_required')
        sending_code = method == 'POST' and urlsplit(url).path != '/data/submitSource'
        if method == 'GET':
            # Share the collector's host pacing, including receipt discovery
            # and automatic polling. Never retry a code POST here.
            from ojcrawler.http import _HOST_LOCKS, _LAST_REQUEST
            with _HOST_LOCKS[self.host]:
                time.sleep(max(0, 2.1 - (time.monotonic() - _LAST_REQUEST[self.host])))
                _LAST_REQUEST[self.host] = time.monotonic()
        try:
            response = self.session.request(method, url, data=data, timeout=(10, 25),
                                            allow_redirects=False, stream=True,
                                            headers=({**({'Referer': self.base + '/', 'Origin': self.base} if method == 'POST' else {}), **(headers or {})} or None))
            chunks = []
            size = 0
            try:
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise SubmissionError('uncertain' if sending_code else 'form_changed', uncertain=sending_code)
                    chunks.append(chunk)
                text = b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace')
                return response.status_code, dict(response.headers), text
            finally:
                response.close()
        except requests.RequestException:
            raise SubmissionError('uncertain' if sending_code else 'network', uncertain=sending_code) from None

    @staticmethod
    def _check_page(status, text, *, submitted=False):
        from ojcrawler.access import protection_reason
        soup = BeautifulSoup(text, 'html.parser')
        reason = protection_reason(text, status)
        if reason:
            raise SubmissionError(reason, uncertain=submitted)
        if status >= 400:
            raise SubmissionError('uncertain' if submitted else 'network', uncertain=submitted)
        return soup

    def _get(self, url):
        for _ in range(4):
            status, headers, text = self._request('GET', url)
            if status in (301, 302, 303, 307, 308):
                location = headers.get('Location') or headers.get('location')
                if not location:
                    raise SubmissionError('form_changed')
                url = self._url(location)
                if re.match(r'^/(?:login|enter)(?:[/?]|$)', urlsplit(url).path):
                    raise SubmissionError('login_required')
                continue
            return self._check_page(status, text)
        raise SubmissionError('form_changed')

    def _problem(self, pid, handle):
        try:
            normalized = normalize(pid)
        except Exception:
            raise SubmissionError('invalid_input') from None
        if normalized.split(':', 1)[0] != self.platform or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', handle or ''):
            raise SubmissionError('invalid_input')
        if self.platform not in ('codeforces', 'atcoder'):
            raise SubmissionError('unsupported')
        if self.platform == 'codeforces' and int(normalized.split(':', 1)[1].split('-')[0]) >= 100000:
            raise SubmissionError('unsupported')
        return normalized.split(':', 1)[1]

    @staticmethod
    def _contest(key):
        """Single source of truth for the official AtCoder contest path.

        Statement URLs, submit URLs, the submissions list and the record check
        must all name the same allowlisted contest; a task id alone cannot
        always carry it (e.g. ``math_and_algorithm_a`` lives in
        ``/contests/math-and-algorithm/``).
        """
        try:
            return atcoder_contest(key)
        except AIError:
            raise SubmissionError('invalid_input') from None

    def _identity(self, soup, handle):
        selector, pattern = ('.lang-chooser', r'^/profile/([^/?#]+)$') if self.platform == 'codeforces' else ('#navbar-collapse', r'^/users/([^/?#]+)$')
        nav = soup.select_one(selector)
        identities = {m[1] for a in (nav.select('a[href]') if nav else []) if (m := re.fullmatch(pattern, a['href']))}
        logout = bool(soup.select_one('form[action*="/logout"]')) or any(re.search(r'/(?:logout|signout)(?:[/?]|$)', a['href']) for a in soup.select('a[href]'))
        if len(identities) != 1 or not logout:
            raise SubmissionError('login_required')
        actual = next(iter(identities))
        matches = actual.casefold() == handle.casefold() if self.platform == 'codeforces' else actual == handle
        if not matches:
            raise SubmissionError('account_mismatch')

    def _form(self, key, handle):
        if self.platform == 'codeforces':
            contest, index = key.split('-')
            url = self.base + '/problemset/submit?contestId=' + contest + '&submittedProblemIndex=' + index
            source, lang = 'source', 'programTypeId'
        else:
            url = self.base + f'/contests/{self._contest(key)}/submit?taskScreenName={key}'
            source, lang = 'sourceCode', 'data.LanguageId'
        soup = self._get(url)
        self._identity(soup, handle)
        node = soup.select_one(f'textarea[name="{source}"]')
        form = node.find_parent('form') if node else None
        if form is None or str(form.get('method', 'get')).lower() != 'post':
            raise SubmissionError('form_changed')
        action = self._url(urljoin(url, form.get('action') or url))
        expected_path = '/problemset/submit' if self.platform == 'codeforces' else f'/contests/{self._contest(key)}/submit'
        if urlsplit(action).path != expected_path:
            raise SubmissionError('form_changed')
        hidden = {n['name']: n.get('value', '') for n in form.select('input[type="hidden"][name]')}
        if not hidden.get('csrf_token') or len(hidden['csrf_token']) > 4096:
            raise SubmissionError('form_changed')
        if self.platform == 'atcoder':
            task = form.select_one('select[name="data.TaskScreenName"]')
            if not task or not any(o.get('value') == key for o in task.select('option')):
                raise SubmissionError('form_changed')
            container = form.find(id='select-lang-' + key)
            select = container.find('select') if container else form.select_one('select[name="data.LanguageId"]')
        else:
            select = form.select_one('select[name="programTypeId"]')
        choices = []
        for option in (select.select('option') if select else []):
            value = option.get('value', '')
            name = option.get_text(' ', strip=True)
            if re.fullmatch(r'[1-9]\d{0,8}', value) and name and not option.has_attr('disabled'):
                choices.append({'id': value, 'name': name[:240]})
        if not choices:
            raise SubmissionError('form_changed')
        return action, hidden, source, lang, choices

    def options(self, pid, handle):
        return self._form(self._problem(pid, handle), handle)[4]

    def _records(self, key, handle, submission_id=None):
        if self.platform != 'codeforces':
            return self._api_records(key,handle,submission_id)
        gate_key=('cf_api',key.split('-')[0],handle.casefold())
        if gate_key not in self._read_gates:
            try:
                return self._api_records(key, handle, submission_id)
            except SubmissionError as error:
                if error.reason not in {'challenge','access_restricted','network'}:
                    raise
                self._read_gates[gate_key]=error.reason
        from types import SimpleNamespace
        from ojcrawler.codeforces_pages import read_submissions
        from ojcrawler.models import CrawlError
        def read(url):
            status, _, text = self._request('GET', url)
            self._check_page(status, text)
            return text
        try:
            rows, _ = read_submissions(SimpleNamespace(get=read), handle,
                max_pages=3 if submission_id is not None else 1,
                contest=key.split('-')[0], submission_id=submission_id)
            return rows
        except CrawlError:
            # No usable alternate evidence: stop the judge watcher on the
            # original gate instead of repeatedly probing the blocked API.
            raise SubmissionError(self._read_gates[gate_key]) from None

    def _api_records(self, key, handle, submission_id=None):
        if self.platform == 'codeforces':
            from json import loads
            contest = key.split('-')[0]
            rows, seen = [], set()
            # Same bounded contest scope as one-click problem synchronization;
            # old submissions must not depend on the latest 50 global records.
            for start in ((1, 201, 401) if submission_id is not None else (1,)):
                status, _, text = self._request('GET', self.base + '/api/contest.status?contestId=' + contest
                    + '&handle=' + quote(handle, safe='') + f'&from={start}&count=200')
                self._check_page(status, text)
                try:
                    result = loads(text)
                    if (isinstance(result, dict) and result.get('status') == 'FAILED'
                            and re.search(r'(?:call|request) limit exceeded', str(result.get('comment', '')), re.I)):
                        raise SubmissionError('rate_limited')
                    if result['status'] != 'OK' or not isinstance(result['result'], list): raise ValueError
                    page = result['result']
                    if any(not isinstance(row, dict) or not re.fullmatch(r'[1-9]\d{0,11}', str(row.get('id', ''))) for row in page): raise ValueError
                    ids = [str(row['id']) for row in page]
                    if len(set(ids)) != len(ids) or seen.intersection(ids): raise ValueError
                    seen.update(ids)
                    rows.extend(page)
                except (ValueError, KeyError, TypeError):
                    raise SubmissionError('record_unavailable') from None
                if submission_id is None or str(submission_id) in seen or len(page) < 200:
                    break
            return rows
        soup = self._get(f'/contests/{self._contest(key)}/submissions/me')
        self._identity(soup, handle)
        records = []
        for row in soup.select('table tbody tr'):
            a = next((a for a in row.select('a[href]') if re.fullmatch(r'/contests/[a-z0-9_-]+/submissions/\d+', a['href'])), None)
            if a:
                records.append({'id': a['href'].rsplit('/', 1)[1]})
        # A valid submissions table or the official no-submissions message is
        # required. An unrelated 200 page is not an empty record baseline.
        if not soup.select_one('table') and not any(x in soup.get_text(' ', strip=True) for x in ('No submissions', 'No Submission', '提出はありません', 'There are no submissions')):
            raise SubmissionError('record_unavailable')
        return records

    def submit(self, pid, handle, code, language_id):
        key = self._problem(pid, handle)
        try:
            valid_code = isinstance(code, str) and bool(code.strip()) and len(code.encode('utf-8')) <= 65536
        except UnicodeEncodeError:
            valid_code = False
        if not valid_code:
            raise SubmissionError('invalid_input')
        action, fields, source, lang, options = self._form(key, handle)
        if str(language_id) not in {o['id'] for o in options}:
            raise SubmissionError('language_unsupported')
        compiler_name = next(o['name'] for o in options if o['id'] == str(language_id))
        before = {str(r['id']) for r in self._records(key, handle)}
        started = int(time.time())
        fields.update({source: code, lang: str(language_id)})
        if self.platform == 'codeforces':
            contest, index = key.split('-')
            fields.update({'action': 'submitSolutionFormSubmitted', 'contestId': contest,
                           'submittedProblemIndex': index, 'tabSize': '4'})
        else:
            fields['data.TaskScreenName'] = key
        # Called at the final send boundary so the owner can cancel stale intent.
        if self.before_send is not None:
            self.before_send()
        # Exactly one POST. Even an ambiguous response must never be retried.
        status, headers, text = self._request('POST', action, data=fields)
        if status in (307, 308):
            raise SubmissionError('uncertain', uncertain=True)
        self._check_page(status, text, submitted=True)
        location = headers.get('Location') or headers.get('location')
        explicit = None
        if location:
            try:
                target = self._url(location)
            except SubmissionError:
                raise SubmissionError('uncertain', uncertain=True) from None
            path = urlsplit(target).path
            pattern = r'^/contest/\d+/submission/(\d+)$' if self.platform == 'codeforces' else r'^/contests/[a-z0-9_-]+/submissions/(\d+)$'
            match = re.fullmatch(pattern, path)
            expected_path = (f'/contest/{key.split("-")[0]}/submission/' if self.platform == 'codeforces' else f'/contests/{self._contest(key)}/submissions/')
            explicit = match[1] if match and path.startswith(expected_path) else None
            if re.match(r'^/(?:enter|login)(?:/|$)', path):
                raise SubmissionError('login_required')
        elif BeautifulSoup(text, 'html.parser').select_one('.error, .error-message, .alert-danger'):
            raise SubmissionError('rejected')
        if explicit:
            if explicit in before:
                raise SubmissionError('uncertain', uncertain=True)
            return {'id': explicit, 'url': target, 'compiler_name': compiler_name}
        try:
            candidates = [explicit] if explicit else [str(r['id']) for r in self._records(key, handle) if str(r['id']) not in before]
            matching = []
            for sid in candidates[:10]:
                try:
                    record = self.judge(pid, handle, sid)
                    if record['submitted_at'] >= started - 5:
                        matching.append(record)
                except SubmissionError as error:
                    if error.reason != 'record_mismatch': raise
            if len(matching) != 1 or len(candidates) > 10:
                raise SubmissionError('uncertain', uncertain=True)
            record = matching[0]
            return {'id': record['id'], 'url': record['url'], 'compiler_name': compiler_name}
        except SubmissionError as error:
            raise SubmissionError(error.reason if error.reason == 'challenge' else 'uncertain', uncertain=True) from None

    @staticmethod
    def codeforces_record(key, handle, sid, row):
        if str(row.get('id')) != str(sid):
            raise SubmissionError('record_mismatch')
        problem, owner = row.get('problem', {}), row.get('author', {})
        author = owner.get('members') if isinstance(owner, dict) else None
        contest, index = key.split('-')
        if not isinstance(problem, dict) or not isinstance(author, list) or any(not isinstance(member, dict) for member in author):
            raise SubmissionError('record_unavailable')
        if str(problem.get('contestId')) != contest or problem.get('index') != index or len(author) != 1 or not isinstance(author[0].get('handle'), str) or author[0]['handle'].casefold() != handle.casefold():
            raise SubmissionError('record_mismatch')
        allowed = ('verdict', 'passedTestCount', 'timeConsumedMillis', 'memoryConsumedBytes', 'creationTimeSeconds', 'programmingLanguage')
        raw = {k: row.get(k) for k in allowed}
        ts = row.get('creationTimeSeconds')
        if type(ts) is not int or ts <= 0 or ts > time.time() + 300:
            raise SubmissionError('record_unavailable')
        return {'id': sid, 'account': author[0]['handle'], 'problem_id': key, 'submitted_at': ts,
                'verdict': codeforces_judge_verdict(row.get('verdict')),
                'language': row.get('programmingLanguage'),
                'url': 'https://codeforces.com' + f'/contest/{contest}/submission/{sid}', 'raw': raw,
                'time_ms': row.get('timeConsumedMillis'), 'memory_bytes': row.get('memoryConsumedBytes'), 'contest_id': contest}

    def judge(self, pid, handle, submission_id):
        key = self._problem(pid, handle)
        sid = str(submission_id)
        if not re.fullmatch(r'[1-9]\d{0,11}', sid):
            raise SubmissionError('invalid_input')
        if self.platform == 'codeforces':
            row = next((r for r in self._records(key, handle, sid) if str(r.get('id')) == sid), None)
            if row is None: raise SubmissionError('record_unavailable')
            return self.codeforces_record(key, handle, sid, row)
        contest = self._contest(key)
        url = self.base + f'/contests/{contest}/submissions/{sid}'
        gate_key=('atcoder_detail',contest,sid,handle)
        if gate_key in self._read_gates:
            return self._atcoder_list_record(key,handle,sid,self._read_gates[gate_key])
        try:
            soup = self._get(url)
        except SubmissionError as error:
            if error.reason not in {'challenge','access_restricted'}:
                raise
            self._read_gates[gate_key]=error.reason
            return self._atcoder_list_record(key, handle, sid, error.reason)
        fields = {}
        for row in soup.select('table tr'):
            th, td = row.find('th'), row.find('td')
            if th and td: fields[th.get_text(' ', strip=True)] = td
        def field(*labels):
            return next((fields[k] for k in labels if k in fields), None)
        task, user = field('Task', '問題'), field('User', 'ユーザ')
        ta, ua = task.select_one('a[href]') if task else None, user.select_one('a[href]') if user else None
        if not ta or not ua: raise SubmissionError('record_unavailable')
        if ta['href'] != f'/contests/{contest}/tasks/{key}' or ua['href'] != f'/users/{handle}':
            raise SubmissionError('record_mismatch')
        status, language, timestamp = field('Status', '結果'), field('Language', '言語'), field('Submission Time', '提出日時')
        if status is None or language is None or timestamp is None: raise SubmissionError('record_unavailable')
        time_node = timestamp.select_one('time') or timestamp
        try:
            parsed_time = datetime.fromisoformat(time_node.get('datetime') or time_node.get_text(' ', strip=True))
            if parsed_time.tzinfo is None:
                raise ValueError
            ts = int(parsed_time.timestamp())
        except (ValueError, TypeError):
            raise SubmissionError('record_unavailable') from None
        if ts <= 0 or ts > time.time() + 300: raise SubmissionError('record_unavailable')
        state = status.get_text(' ', strip=True)
        state = state if state in {'AC', 'WA', 'TLE', 'MLE', 'RE', 'CE', 'OLE', 'IE', 'WJ', 'WR', 'Judging'} else 'PENDING'
        state = 'PENDING' if state in {'WJ', 'WR', 'Judging'} else verdict(state)
        raw = {'verdict': state, 'programmingLanguage': language.get_text(' ', strip=True), 'creationTimeSeconds': ts}
        return {'id': sid, 'account': ua['href'].rsplit('/', 1)[1], 'problem_id': key, 'submitted_at': ts, 'verdict': state,
                'language': raw['programmingLanguage'], 'url': url, 'raw': raw, 'contest_id': contest}

    def _atcoder_list_record(self, key, handle, sid, blocked_reason):
        contest = self._contest(key)
        soup = self._get(self.base + f'/contests/{contest}/submissions?f.User=' + quote(handle, safe=''))
        labels = {'time':{'Submission Time','提出日時'},'task':{'Task','問題'},'user':{'User','ユーザ'},
                  'language':{'Language','言語'},'status':{'Status','結果'}}
        for table in soup.select('table'):
            headings=[h.get_text(' ',strip=True) for h in table.select('thead th')]
            columns={key:next((i for i,title in enumerate(headings) if title in names),None) for key,names in labels.items()}
            if any(value is None for value in columns.values()):
                continue
            matches=[row for row in table.select('tbody tr') if row.select_one(f'a[href="/contests/{contest}/submissions/{sid}"]')]
            if len(matches)!=1:
                continue
            cells=matches[0].find_all('td',recursive=False)
            if len(cells)<=max(columns.values()):
                raise SubmissionError('record_unavailable')
            user=cells[columns['user']].select_one('a[href]')
            task=cells[columns['task']].select_one('a[href]')
            if user is None or user['href'] != '/users/'+handle or task is None or task['href'] != f'/contests/{contest}/tasks/{key}':
                raise SubmissionError('record_mismatch')
            node=cells[columns['time']].select_one('time') or cells[columns['time']]
            try:
                date=datetime.fromisoformat(node.get('datetime') or node.get_text(' ',strip=True))
                if date.tzinfo is None or not 0<int(date.timestamp())<=time.time()+300:raise ValueError
            except (ValueError,TypeError):
                raise SubmissionError('record_unavailable') from None
            state=cells[columns['status']].get_text(' ',strip=True)
            state=state if state in {'AC','WA','TLE','MLE','RE','CE','OLE','IE'} else 'PENDING'
            language=cells[columns['language']].get_text(' ',strip=True)
            stamp=int(date.timestamp())
            return {'id':sid,'account':handle,'problem_id':key,'submitted_at':stamp,'verdict':state,
                'language':language,'contest_id':contest,'url':self.base+f'/contests/{contest}/submissions/{sid}',
                'raw':{'verdict':state,'programmingLanguage':language,'creationTimeSeconds':stamp},
                'source_block_reason':blocked_reason}
        raise SubmissionError('record_unavailable')

    def source(self, pid, handle, submission_id, api_credentials=None):
        """Fetch only an independently verified owner's official source page."""
        record = self.judge(pid, handle, submission_id)
        if api_credentials is not None:
            if self.platform != 'codeforces' or not isinstance(api_credentials, dict):
                raise SubmissionError('invalid_input')
            from .codeforces_api_source import fetch_source
            return fetch_source(api_key=api_credentials.get('api_key'),
                api_secret=api_credentials.get('api_secret'), expected=record,
                handle=handle, request=self._request)
        if record.get('source_block_reason'):
            # The exact source/detail URL was already challenged during judge.
            # Reusing a readable list is judge evidence, never source evidence.
            raise SubmissionError(record['source_block_reason'])
        status, headers, text = self._request('GET', record['url'])
        # Do not silently read source from a redirected, different submission.
        if status in (301, 302, 303, 307, 308):
            location = headers.get('Location') or headers.get('location') or ''
            if re.match(r'^/(?:enter|login)(?:[/?]|$)', urlsplit(location).path):
                raise SubmissionError('login_required')
            raise SubmissionError('record_unavailable')
        try:
            soup = self._check_page(status, text)
        except SubmissionError as error:
            if self.platform == 'codeforces' and error.reason in {'challenge','access_restricted'}:
                return self._cf_source_popup(record, handle)
            raise
        self._identity(soup, handle)
        node = soup.select_one('#program-source-text' if self.platform == 'codeforces' else '#submission-code')
        if node is None:
            raise SubmissionError('source_unavailable')
        code = node.get_text('', strip=False)
        if not code.strip() or len(code.encode('utf-8')) > 65536:
            raise SubmissionError('source_unavailable')
        return {**record, 'code':code}

    def _cf_source_popup(self, record, handle):
        """Use the official user's source popup, with its normal CSRF protection.

        This POST only reads source. It never includes code or sends a solution.
        The API record and the exact list row must both identify this submission.
        """
        from json import loads
        from secrets import token_hex
        from ojcrawler.codeforces_pages import source_reference
        url = self.base + '/submissions/' + quote(handle, safe='')
        soup = self._get(url)
        self._identity(soup, handle)
        sid = str(record['id'])
        rows = soup.select(f'tr[data-submission-id="{sid}"]')
        if len(rows) != 1:
            raise SubmissionError('record_unavailable')
        row = rows[0]
        owners = [a['href'].split('/')[-1] for a in row.select('a[href]') if re.fullmatch(r'/profile/[A-Za-z0-9_.-]+',a['href'])]
        contest, index = record['problem_id'].split('-')
        problems = [a['href'] for a in row.select('a[href]') if re.fullmatch(
            r'/(?:contest|gym)/[1-9]\d{0,8}/problem/[A-Za-z]\d?', a['href'])]
        if len(owners) != 1 or len(problems) != 1:
            raise SubmissionError('source_unavailable')
        if (owners[0].casefold() != handle.casefold() or
                problems[0] not in {f'/contest/{contest}/problem/{index}', f'/gym/{contest}/problem/{index}'}):
            raise SubmissionError('record_mismatch')
        if not source_reference(row, soup, contest, sid):
            raise SubmissionError('source_unavailable')
        meta = soup.select_one('meta[name="X-Csrf-Token"]')
        csrf = meta.get('content','') if meta else ''
        if (not re.fullmatch(r'[A-Za-z0-9]{32}', csrf) or not any('/data/submitSource' in s.get_text() for s in soup.select('script:not([src])'))):
            raise SubmissionError('source_unavailable')
        # The official AJAX beforeSend also adds a fresh rv to read requests.
        status, _, text = self._request('POST', self.base + '/data/submitSource?rv=' + token_hex(5)[:9],
            data={'submissionId':sid,'csrf_token':csrf},
            headers={'Referer':url,'X-Csrf-Token':csrf,'X-Requested-With':'XMLHttpRequest',
                     'Accept':'application/json, text/javascript, */*; q=0.01',
                     'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'})
        if status in (301,302,303,307,308):
            raise SubmissionError('record_unavailable')
        self._check_page(status, text)
        try:
            result = loads(text)
            code = result.get('source') if isinstance(result,dict) else None
            if isinstance(result,dict) and 'submissionId' in result and str(result['submissionId']) != sid:
                raise SubmissionError('record_mismatch')
            if not isinstance(code,str) or not code.strip() or len(code.encode()) > 65536:
                raise ValueError
        except (ValueError,TypeError):
            raise SubmissionError('source_unavailable') from None
        return {**record,'code':code}

    def find_receipt(self, pid, handle, sent_at, code_hash):
        """Read-only recovery after an ambiguous POST; never guesses the newest ID."""
        import hashlib
        key = self._problem(pid, handle)
        records = self._records(key, handle)
        if self.platform == 'codeforces':
            contest, index = key.split('-')
            records = [r for r in records if str(r.get('problem', {}).get('contestId')) == contest
                       and r.get('problem', {}).get('index') == index
                       and type(r.get('creationTimeSeconds')) is int
                       and sent_at-60 <= r['creationTimeSeconds'] <= sent_at+300]
        if len(records) > 10:
            raise SubmissionError('record_unavailable')
        matching = []
        for row in records:
            try:
                record = self.judge(pid, handle, row['id'])
                if not sent_at-60 <= record['submitted_at'] <= sent_at+300:
                    continue
                source = self.source(pid, handle, row['id'])
            except SubmissionError as exc:
                if exc.reason == 'record_mismatch':
                    continue
                raise
            if hashlib.sha256(source['code'].encode()).hexdigest() == code_hash:
                matching.append(source)
        if len(matching) != 1:
            raise SubmissionError('record_unavailable')
        return matching[0]
