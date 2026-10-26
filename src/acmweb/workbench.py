"""Private practice workspace. Never executes code or submits it to a judge."""
import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from urllib.parse import urlsplit, urljoin

from bs4 import BeautifulSoup
from requests.utils import default_user_agent
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Literal
from fastapi import HTTPException, Query
from ojcrawler.http import Client
from ojcrawler.models import CrawlError
from ojcrawler.storage import codeforces_judge_verdict
from .catalog import recommendation_candidates, recommendation_status
from .codex_bridge import AIError
from .daily import Complete
from .statement_markdown import RENDER_VERSION, html_markdown, upgrade_statement

DDL = '''
CREATE TABLE IF NOT EXISTS wb_problems (id TEXT PRIMARY KEY,data TEXT NOT NULL,fetched_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wb_drafts (problem_id TEXT PRIMARY KEY,language TEXT NOT NULL,code TEXT NOT NULL,revision INTEGER NOT NULL,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wb_hints (problem_id TEXT NOT NULL,seq INTEGER NOT NULL,data TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(problem_id,seq));
CREATE TABLE IF NOT EXISTS wb_attempts (id TEXT PRIMARY KEY,problem_id TEXT NOT NULL,language TEXT NOT NULL,code TEXT NOT NULL,code_hash TEXT NOT NULL,created_at INTEGER NOT NULL,handle TEXT,submission_id INTEGER,verdict TEXT NOT NULL DEFAULT 'UNLINKED',judge_data TEXT,review TEXT,last_synced INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS wb_submission_reviews (attempt_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, data TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wb_submission_code_claims (attempt_id TEXT PRIMARY KEY,code_hash TEXT NOT NULL,confirmed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wb_official_sources (attempt_id TEXT PRIMARY KEY,code_hash TEXT NOT NULL,fetched_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wb_submission_source_bindings (attempt_id TEXT PRIMARY KEY,problem_id TEXT NOT NULL,handle TEXT NOT NULL,submission_id INTEGER NOT NULL,language TEXT NOT NULL,code_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS wb_submission_code_claims_cleanup AFTER DELETE ON wb_attempts
BEGIN DELETE FROM wb_submission_code_claims WHERE attempt_id=OLD.id; END;
CREATE TRIGGER IF NOT EXISTS wb_submission_reviews_cleanup AFTER DELETE ON wb_attempts
BEGIN DELETE FROM wb_submission_reviews WHERE attempt_id=OLD.id; END;
CREATE TRIGGER IF NOT EXISTS wb_submission_source_bindings_cleanup AFTER DELETE ON wb_attempts
BEGIN DELETE FROM wb_submission_source_bindings WHERE attempt_id=OLD.id; END;
CREATE TABLE IF NOT EXISTS wb_auto_reviews (fingerprint TEXT PRIMARY KEY,created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS wb_remote_platform_case ON wb_attempts(substr(problem_id,1,instr(problem_id,':')-1),CASE WHEN problem_id LIKE 'codeforces:%' THEN lower(handle) ELSE handle END,submission_id) WHERE submission_id IS NOT NULL;
DROP INDEX IF EXISTS wb_remote;
DROP INDEX IF EXISTS wb_remote_platform;
'''


from .problem_sources import normalize, source_url, fetch_statement, capabilities


def parse_statement(html, pid):
    if not normalize(pid).startswith('codeforces:'):
        from .problem_sources import parse_statement as parse_other
        return parse_other(html, pid)
    soup = BeautifulSoup(html, 'html.parser')
    root = soup.select_one('.problem-statement')
    if root is None:
        raise AIError('未获取到题面，平台可能要求浏览器验证；请打开原题查看后重试')
    from .statement_math import preserve_math_markup
    preserve_math_markup(root)
    for node in root.select('script,style,iframe,form,button,object,svg'):
        node.decompose()
    body = BeautifulSoup(''.join(str(c) for c in root.find_all(recursive=False) if not set(c.get('class', [])) & {'header', 'sample-tests'}), 'html.parser')
    for heading in body.select('.section-title'):
        heading.name = 'h2'
    for line in body.select('pre .test-example-line'):
        line.append('\n')
    images = []
    allowed = {'div','p','span','strong','b','em','i','u','ol','ul','li','table','thead','tbody','tr','td','th','img','br','sup','sub','pre','code','h1','h2','h3','h4','blockquote','hr','a'}
    for tag in list(body.find_all(True)):
        if tag.name not in allowed:
            tag.unwrap()
            continue
        attributes = {}
        if tag.name in {'img','a'}:
            key = 'src' if tag.name == 'img' else 'href'
            url = urljoin(source_url(pid), tag.get(key) or tag.get('data-src') or '')
            parsed = urlsplit(url)
            if (tag.get(key) or tag.get('data-src')) and parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
                attributes[key] = url
                if tag.name == 'img':
                    attributes['alt'] = tag.get('alt', '')
                    images.append({'url': url, 'alt': attributes['alt']})
        tag.attrs = attributes
    body_html = str(body)
    for br in root.select('br'):
        br.replace_with('\n')
    for line in root.select('.test-example-line'):
        line.append('\n')
    def content(node):
        return node.get_text('', strip=False).strip() if node else ''
    samples = []
    for sample in root.select('.sample-test'):
        inputs, outputs = sample.select('.input pre'), sample.select('.output pre')
        if len(inputs) != len(outputs):
            raise AIError('样例解析不完整，请使用原题')
        samples.extend({'input': content(a), 'output': content(b)} for a, b in zip(inputs, outputs))
    title = content(root.select_one('.header .title'))
    if not title:
        raise AIError('题面缺少标题，保留已有缓存')
    result = {'id': pid, 'title': title, 'url': source_url(pid), 'samples': samples,
              'time_limit': content(root.select_one('.time-limit')), 'memory_limit': content(root.select_one('.memory-limit')),
              'sections': []}
    result['markdown'] = html_markdown(body_html)
    result['sections'] = [result['markdown']]
    if sum(map(len, result['sections'])) > 70000 or len(json.dumps(result)) > 150000:
        raise AIError('题面过大，请使用原题页面')
    result['has_images'] = bool(root.select('img'))
    result['body_html'] = body_html
    result['images'] = images
    result['render_version'] = RENDER_VERSION
    result['diagnostics'] = {'sections': len(result['sections']), 'samples': len(samples), 'images': len(images), 'math': bool(re.search(r'\$|\\\(|\\\[', body_html)), 'warnings': (['部分图片未提供可用 HTTPS 地址，请查看原题'] if len(images) < len(root.select('img')) else [])}
    return result


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Draft(Strict):
    language: Literal['cpp17', 'cpp20', 'python3', 'java17'] = 'cpp17'
    code: str = Field(max_length=65536)
    revision: int = Field(ge=0)


class EditorSettings(Strict):
    keymap: Literal['standard', 'vim'] = 'standard'
    font_size: int = Field(default=14, ge=12, le=24, strict=True)
    line_wrapping: bool = Field(default=False, strict=True)


class LayoutSettings(Strict):
    mode: Literal['auto', 'split', 'stacked'] = 'auto'
    statement_percent: int = Field(default=50, ge=30, le=70, strict=True)


class StatementImport(Strict):
    html: str = Field(min_length=1, max_length=200000)


class Open(Strict):
    problem: str = Field(max_length=300)


class Coach(Strict):
    provider: Literal['api', 'codex']
    model: str = Field(min_length=1, max_length=150)


class CoachSettings(Strict):
    enabled: bool = Field(strict=True)
    provider: Literal['api', 'codex'] = 'api'
    model: str = Field(default='', max_length=150)
    consent: bool = Field(default=False, strict=True)


ERROR_VERDICTS = frozenset({'WA', 'RE', 'CE', 'TLE', 'MLE', 'OLE', 'PE', 'FAIL', 'PARTIAL',
    'IDLENESS_LIMIT_EXCEEDED', 'PRESENTATION_ERROR', 'FAILED'})


def official_judge(judge):
    return {key: value for key, value in (judge or {}).items() if key != 'collected_at'}


class Link(Strict):
    handle: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    submission_id: int = Field(gt=0, le=10**12)
    review: Coach | None = None


class SubmissionLink(Link):
    revision: int = Field(ge=1, strict=True)
    code_matches_submission: Literal[True]


class Hint(Strict):
    focus: str = Field(max_length=50)
    hint: str = Field(min_length=1, max_length=220)
    question: str = Field(min_length=1, max_length=100)


class Review(Strict):
    category: Literal['range', 'boundary', 'logic', 'complexity', 'implementation', 'unknown']
    line: int = Field(ge=0)
    evidence: str = Field(min_length=1, max_length=240)
    question: str = Field(min_length=1, max_length=120)
    confidence: Literal['low', 'medium', 'high']


REVIEW_CATEGORIES = Literal[
    'algorithm_strategy', 'implementation_logic', 'boundary_conditions',
    'complexity_resources', 'input_output_format', 'language_compilation',
    'judging_information_insufficient', 'unable_to_determine',
]


class SubmissionReview(Strict):
    categories: list[REVIEW_CATEGORIES] = Field(min_length=1, max_length=8)
    phenomenon: str = Field(min_length=1, max_length=500)
    evidence: list[Annotated[str, Field(max_length=500)]] = Field(min_length=1, max_length=8)
    check_directions: list[Annotated[str, Field(max_length=300)]] = Field(min_length=1, max_length=8)
    minimal_change_idea: str | None = Field(default=None, max_length=500)
    confidence: Literal['low', 'medium', 'high']


def training_evidence(db):
    with db.connect() as conn:
        attempts = conn.execute('SELECT problem_id,verdict,review FROM wb_attempts').fetchall()
        hints = conn.execute('SELECT COUNT(*) FROM wb_hints').fetchone()[0]
        topic_rows = conn.execute('SELECT a.problem_id,a.verdict,c.data FROM wb_attempts a LEFT JOIN problem_catalog c ON c.id=a.problem_id').fetchall()
    counts = {}
    for row in attempts:
        counts[row['verdict']] = counts.get(row['verdict'], 0) + 1
    topics = {}
    for row in topic_rows:
        if not row['data'] or row['verdict'] in {'UNLINKED', 'PENDING'}:
            continue
        for tag in json.loads(row['data']).get('tags', []):
            item = topics.setdefault(tag, {'accepted_snapshots': 0, 'non_accepted_snapshots': 0})
            item['accepted_snapshots' if row['verdict'] == 'AC' else 'non_accepted_snapshots'] += 1
    return {'topics': topics, 'local_snapshots': len(attempts), 'distinct_problems': len({r['problem_id'] for r in attempts}),
            'verified_verdicts': {k: v for k, v in counts.items() if k != 'UNLINKED'},
            'hints_requested': hints,
            'ai_hypotheses': [json.loads(r['review'])['category'] for r in attempts if r['review']],
            'note': '判题核验账号和题号；未核验远程源码对应关系。AI 错因是推测，提示次数不代表能力。'}


class Workbench:
    def __init__(self, db, ai, accounts):
        self.db, self.ai, self.accounts = db, ai, accounts
        self.lock = asyncio.Lock()
        self.submission_review_pending = set()
        with db.connect() as conn:
            conn.executescript(DDL)
            conn.execute('''INSERT OR IGNORE INTO wb_submission_source_bindings
                SELECT a.id,a.problem_id,a.handle,a.submission_id,a.language,a.code_hash
                FROM wb_attempts a JOIN wb_official_sources s ON s.attempt_id=a.id AND s.code_hash=a.code_hash
                WHERE a.handle IS NOT NULL AND a.submission_id IS NOT NULL AND a.language IS NOT NULL''')

    def coach_settings(self):
        value = self.db.get('automatic_coach', {})
        return {key: value.get(key, default) for key, default in
                (('enabled', False), ('provider', 'api'), ('model', ''))}

    def set_coach_settings(self, data):
        if data.enabled and (not data.consent or not data.model.strip()):
            raise HTTPException(400, '开启自动提示前，请选择模型并同意发送题面、当次代码和判题信息')
        self.db.set('automatic_coach', {'enabled': data.enabled, 'provider': data.provider,
            'model': data.model.strip(), 'generation': uuid.uuid4().hex})
        return self.coach_settings()

    def automatic_generation(self, token):
        value = self.db.get('automatic_coach', {})
        return bool(value.get('enabled') and value.get('generation') == token)

    async def automatic_review(self, aid):
        settings = self.db.get('automatic_coach', {})
        if not settings.get('enabled'):
            return {'automatic_review': 'disabled'}
        attempt = self.attempt(aid)
        if attempt['verdict'] not in ERROR_VERDICTS or not attempt.get('judge_data'):
            return {'automatic_review': 'not_applicable'}
        return await self.coach(attempt['problem_id'],
            Coach(provider=settings['provider'], model=settings['model']), aid,
            automatic_token=settings['generation'])

    def choices(self):
        prefs = self.db.get('training', {})
        with self.db.connect() as conn:
            report = conn.execute("SELECT data FROM ai_reports WHERE kind='recommend' ORDER BY created_at DESC LIMIT 1").fetchone()
            recent = [dict(r) for r in conn.execute('SELECT problem_id,updated_at FROM wb_drafts ORDER BY updated_at DESC LIMIT 30')]
            all_catalog = {r['id']: json.loads(r['data']) for r in conn.execute('SELECT id,data FROM problem_catalog')}
            rows = conn.execute("SELECT problem_id,verdict FROM wb_attempts WHERE verdict NOT IN ('UNLINKED','PENDING','AC')").fetchall()
        selected = {p['id']: p for p in json.loads(report['data']).get('selected_problems', [])} if report else {}
        notice = ''
        try:
            pool = recommendation_candidates(self.db, self.accounts, prefs.get('minimum', 1000),
                prefs.get('maximum', 1600), limit=60, preferred_ids=list(selected))
        except AIError as exc:
            pool, notice = [], str(exc)
        weak_tags = set()
        for row in rows:
            weak_tags.update(all_catalog.get(row['problem_id'], {}).get('tags', []))
        target = (prefs.get('minimum', 1000) + prefs.get('maximum', 1600)) / 2
        for p in pool:
            ai_choice = selected.get(p['id'])
            ai_reason = ai_choice.get('reason', '') if ai_choice else ''
            p.pop('ai_report_id', None)
            if ai_choice and isinstance(ai_choice.get('ai_report_id'), str):
                p['ai_report_id'] = ai_choice['ai_report_id']
            weak = sorted(set(p.get('tags', [])) & weak_tags)
            base = round(70 - abs(p['difficulty'] - target) / 20) if p['platform'] == 'codeforces' and isinstance(p.get('difficulty'), int) else 70
            p['match_score'] = min(100, max(10, base) + (15 if weak else 0) + (15 if ai_reason else 0))
            p['reason'] = ai_reason or ('近期未通过题目的关联知识点：' + '、'.join(weak) if weak else ('符合当前训练难度范围' if p['platform'] == 'codeforces' else '来自已验证登录的平台；保留该平台原始难度'))
            p['recommendation_source'] = '已保存的 AI 推荐' if ai_choice else '训练规则匹配'
        pool.sort(key=lambda p: -p['match_score'])
        return {'items': pool[:30], 'recent': recent, 'profile': training_evidence(self.db),
                'handles_by_platform': {p:[a['handle'] for a in self.accounts.document().get('accounts', []) if a.get('platform')==p] for p in ('codeforces','atcoder','luogu','nowcoder')},
                'handles': [a['handle'] for a in self.accounts.document().get('accounts', []) if a.get('platform') == 'codeforces'],
                'eligibility': recommendation_status(self.db, self.accounts), 'notice': notice,
                'score_note': '在已验证登录的平台中混合随机选题，并遵守最早发布日期。匹配指数综合同平台难度、错误关联标签和 AI 推荐；跨平台难度不作等价换算，也不是通过概率。'}

    def state(self, pid):
        pid = normalize(pid)
        with self.db.connect() as conn:
            statement = conn.execute('SELECT data,fetched_at FROM wb_problems WHERE id=?', (pid,)).fetchone()
            draft = conn.execute('SELECT * FROM wb_drafts WHERE problem_id=?', (pid,)).fetchone()
            hints = conn.execute('SELECT data FROM wb_hints WHERE problem_id=? ORDER BY seq', (pid,)).fetchall()
            attempts = conn.execute('SELECT * FROM wb_attempts WHERE problem_id=? ORDER BY created_at DESC,id DESC LIMIT 30', (pid,)).fetchall()
        cached_statement = json.loads(statement['data']) if statement else None
        upgraded_statement = upgrade_statement(cached_statement)
        if upgraded_statement is not cached_statement:
            with self.db.connect() as conn:
                conn.execute('UPDATE wb_problems SET data=? WHERE id=? AND data=?', (json.dumps(upgraded_statement, ensure_ascii=False), pid, statement['data']))
        accounts = self.accounts.document().get('accounts', [])
        attempt_views = []
        for row in attempts:
            attempt = self.decode(row)
            attempt['analysis_available'], attempt['analysis_disabled_reason'] = self.submission_review_availability(attempt)
            attempt['submission_review'] = self.saved_submission_review(attempt)
            attempt_views.append(attempt)
        caps = capabilities(pid, accounts)
        # Linking is independent of recommendation eligibility and login-check expiry.
        caps['handles'] = [a['handle'] for a in accounts if a.get('platform') == caps['platform']]
        checker = getattr(self.accounts, 'credential_checks', None)
        checks = [r for r in checker.statuses()['items'] if r['platform'] == caps['platform']] if checker else []
        caps['credential_checks'] = checks
        caps['credential_check'] = max(checks, key=lambda r: r['checked_at']) if checks else None
        caps['login_verified'] = bool(len([a for a in self.accounts.document().get('accounts', []) if a.get('platform') == caps['platform']]) == 1 and caps['credential_check'] and caps['credential_check']['login_verified'])
        return {'id': pid, 'capabilities': caps, 'url': source_url(pid), 'statement': upgraded_statement,
                'fetched_at': statement['fetched_at'] if statement else None,
                'fetch_status': self.db.get('statement_status:'+pid, {}),
                'draft': dict(draft) if draft else {'language': 'cpp17', 'code': '', 'revision': 0},
                'hints': [json.loads(r['data']) for r in hints], 'attempts': attempt_views}

    def draft(self, pid, data):
        pid = normalize(pid)
        try:
            size = len(data.code.encode('utf-8'))
        except UnicodeEncodeError:
            raise AIError('代码包含无效 Unicode 字符') from None
        if size > 65536:
            raise AIError('代码最大 64 KiB')
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT revision FROM wb_drafts WHERE problem_id=?', (pid,)).fetchone()
            if (old['revision'] if old else 0) != data.revision:
                raise HTTPException(409, '草稿已在另一页面更新，请保留本页代码并复制备份，再重新载入')
            if not old and conn.execute('SELECT COUNT(*) FROM wb_drafts').fetchone()[0] >= 200:
                raise AIError('草稿数量已达到 200 上限')
            conn.execute('INSERT INTO wb_drafts VALUES(?,?,?,?,?) ON CONFLICT(problem_id) DO UPDATE SET language=excluded.language,code=excluded.code,revision=excluded.revision,updated_at=excluded.updated_at',
                         (pid, data.language, data.code, data.revision + 1, int(time.time())))
        return {'revision': data.revision + 1}

    def snapshot(self, pid, revision):
        pid = normalize(pid)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            draft = conn.execute('SELECT * FROM wb_drafts WHERE problem_id=?', (pid,)).fetchone()
            if not draft or draft['revision'] != revision:
                raise HTTPException(409, '请先保存最新草稿')
            if not draft['code'].strip():
                raise AIError('代码不能为空')
            if conn.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] >= 2000:
                raise AIError('提交代码记录已达到 2000 上限，请联系管理员归档')
            aid = uuid.uuid4().hex
            conn.execute('INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at) VALUES(?,?,?,?,?,?)',
                         (aid, pid, draft['language'], draft['code'], hashlib.sha256(draft['code'].encode()).hexdigest(), int(time.time())))
        return {'id': aid}

    def prepare_submission(self, pid, data):
        """Record only the saved version explicitly claimed by the user for this submission."""
        pid = normalize(pid)
        platform = pid.split(':', 1)[0]
        handles = [a['handle'] for a in self.accounts.document().get('accounts', []) if a.get('platform') == platform]
        handle = next((h for h in handles if (h.lower() == data.handle.lower() if platform == 'codeforces' else h == data.handle)), None)
        if not handle:
            raise HTTPException(400, '请先绑定此平台账号，账号名需与配置一致')
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            draft = conn.execute('SELECT * FROM wb_drafts WHERE problem_id=?', (pid,)).fetchone()
            if not draft or draft['revision'] != data.revision:
                raise HTTPException(409, '代码版本已变化，请先等待自动保存完成后重试')
            if not draft['code'].strip():
                raise HTTPException(400, '请输入本次提交的代码')
            digest = hashlib.sha256(draft['code'].encode()).hexdigest()
            collation = ' COLLATE NOCASE' if platform == 'codeforces' else ''
            old = conn.execute("SELECT * FROM wb_attempts WHERE substr(problem_id,1,instr(problem_id,':')-1)=? AND handle=?" + collation + " AND submission_id=?",
                               (platform, handle, data.submission_id)).fetchone()
            if old:
                if old['problem_id'] != pid or old['code_hash'] != digest or old['language'] != draft['language']:
                    raise HTTPException(409, '此平台提交已有代码记录，不能用当前代码覆盖；请查看原记录')
                conn.execute('INSERT OR IGNORE INTO wb_submission_code_claims VALUES(?,?,?)',
                             (old['id'], digest, int(time.time())))
                return old['id'], False, handle
            if conn.execute('SELECT COUNT(*) FROM wb_attempts').fetchone()[0] >= 2000:
                raise HTTPException(409, '提交代码记录达到 2000 上限，请联系管理员归档')
            aid = uuid.uuid4().hex
            now = int(time.time())
            conn.execute('INSERT INTO wb_attempts(id,problem_id,language,code,code_hash,created_at,handle,submission_id) VALUES(?,?,?,?,?,?,?,?)',
                         (aid, pid, draft['language'], draft['code'], digest, now, handle, data.submission_id))
            # This is a user's source-code declaration, not a remote source verification.
            # A later paste/save cannot determine when the official submission occurred.
            conn.execute('INSERT INTO wb_submission_code_claims VALUES(?,?,?)', (aid, digest, now))
        return aid, True, handle

    def validate_submission_time(self, attempt, submitted_at):
        if type(submitted_at) is not int or submitted_at <= 0 or submitted_at > int(time.time()) + 300:
            raise AIError('提交时间异常，请确认官方判题记录后重试')
        if submitted_at >= attempt['created_at'] - 300:
            return
        with self.db.connect() as conn:
            claim = conn.execute('SELECT code_hash FROM wb_submission_code_claims WHERE attempt_id=?',
                                 (attempt['id'],)).fetchone()
        if not claim or claim['code_hash'] != attempt['code_hash']:
            raise AIError('提交时间早于旧代码记录，请在“关联本次提交结果”中确认代码后重新关联')

    def history(self, pid):
        pid = normalize(pid)
        platform, raw = pid.split(':', 1)
        with self.db.connect() as conn:
            official = conn.execute('SELECT * FROM submissions WHERE platform=? AND problem_id IN (?,?) ORDER BY submitted_at DESC LIMIT 200', (platform, raw, pid)).fetchall()
            attempts = conn.execute('SELECT * FROM wb_attempts WHERE problem_id=? ORDER BY created_at DESC LIMIT 200', (pid,)).fetchall()
        merged = {}
        for row in official:
            data = json.loads(row['data'])
            key = (row['account'].casefold() if platform == 'codeforces' else row['account'], str(row['id']))
            merged[key] = {'id': str(row['id']), 'submission_id': str(row['id']), 'platform': platform,
                'account': row['account'], 'verdict': row['verdict'], 'submitted_at': row['submitted_at'],
                'language': data.get('language'), 'url': data.get('url') or source_url(pid),
                'attempt_id': None, 'has_local_code': False, 'code_verified': False, 'review': None}
        for row in attempts:
            if row['submission_id'] is None:
                key = ('local', row['id'])
            else:
                key = ((row['handle'] or '').casefold() if platform == 'codeforces' else (row['handle'] or ''), str(row['submission_id']))
            item = merged.get(key) or {'id': str(row['submission_id'] or row['id']),
                'submission_id': str(row['submission_id']) if row['submission_id'] else None,
                'platform': platform, 'account': row['handle'], 'verdict': row['verdict'],
                'submitted_at': row['created_at'], 'url': source_url(pid)}
            verified = self.decode(row)['code_verified']
            item.update(attempt_id=row['id'], has_local_code=True, code_verified=verified,
                        code_provenance='official' if verified else 'local_unverified', language=row['language'],
                        review=json.loads(row['review']) if row['review'] and item['verdict'] == row['verdict'] else None)
            decoded_attempt = self.decode(row)
            if item['verdict'] != decoded_attempt['verdict']:
                decoded_attempt['verdict'] = item['verdict']
            item['analysis_available'], item['analysis_disabled_reason'] = self.submission_review_availability(
                decoded_attempt, item['verdict'])
            item['submission_review'] = self.saved_submission_review(decoded_attempt)
            merged[key] = item
        for item in merged.values():
            item.setdefault('analysis_available', False)
            item.setdefault('analysis_disabled_reason', '尚未读取并验证本次提交源码')
            item.setdefault('submission_review', None)
        return {'problem_id': pid, 'items': sorted(merged.values(), key=lambda row: row['submitted_at'], reverse=True)[:200]}

    def _review_fingerprint(self, attempt, statement):
        judge = official_judge(attempt.get('judge_data'))
        statement_hash = hashlib.sha256(json.dumps(statement, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return hashlib.sha256(json.dumps([
            attempt['problem_id'], attempt['handle'], attempt['submission_id'],
            attempt['code_hash'], attempt['language'], attempt['verdict'], judge, statement_hash,
        ], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def submission_review_availability(self, attempt, verdict=None):
        verdict = verdict or attempt['verdict']
        if verdict == 'AC':
            return False, '已通过的提交无需错因分析'
        if verdict in {'', 'UNLINKED', 'PENDING', 'TESTING', 'JUDGING', 'QUEUED', 'PRETESTS_PASSED'}:
            return False, '判题尚未结束或没有可分析的判题结果'
        if verdict not in ERROR_VERDICTS:
            return False, '此判题结果暂不支持错因分析'
        if not attempt.get('submission_id') or not attempt.get('handle') or not attempt.get('judge_data'):
            return False, '缺少已关联的账号、提交号或官方判题信息'
        if not attempt.get('language'):
            return False, '缺少本次提交使用的语言'
        if hashlib.sha256(attempt['code'].encode()).hexdigest() != attempt['code_hash']:
            return False, '提交源码校验失败，不能用于 AI 分析'
        platform = attempt['problem_id'].split(':', 1)[0]
        account_matches = any(
            account.get('platform') == platform and
            (account.get('handle', '').casefold() == attempt['handle'].casefold()
             if platform == 'codeforces' else account.get('handle') == attempt['handle'])
            for account in self.accounts.document().get('accounts', [])
        )
        if not account_matches:
            return False, '当前账号设置中找不到此提交对应的平台账号'
        with self.db.connect() as conn:
            source = conn.execute('SELECT * FROM wb_submission_source_bindings WHERE attempt_id=?', (attempt['id'],)).fetchone()
            statement = conn.execute('SELECT 1 FROM wb_problems WHERE id=?', (attempt['problem_id'],)).fetchone()
        if not source or any(source[key] != attempt[value] for key, value in (
            ('problem_id', 'problem_id'), ('handle', 'handle'), ('submission_id', 'submission_id'),
            ('language', 'language'), ('code_hash', 'code_hash'))):
            return False, '尚未读取并验证本次提交源码；请先获取本次代码'
        if not statement:
            return False, '缺少该题题面，请先获取题面'
        return True, ''

    def saved_submission_review(self, attempt):
        with self.db.connect() as conn:
            row = conn.execute('SELECT fingerprint,data FROM wb_submission_reviews WHERE attempt_id=?', (attempt['id'],)).fetchone()
            statement = conn.execute('SELECT data FROM wb_problems WHERE id=?', (attempt['problem_id'],)).fetchone()
        if not row or not statement:
            return None
        if row['fingerprint'] != self._review_fingerprint(attempt, json.loads(statement['data'])):
            return None
        return json.loads(row['data'])

    def submission_review_snapshot(self, aid):
        attempt = self.attempt(aid)
        available, reason = self.submission_review_availability(attempt)
        if not available:
            raise AIError(reason)
        with self.db.connect() as conn:
            statement_row = conn.execute('SELECT data FROM wb_problems WHERE id=?', (attempt['problem_id'],)).fetchone()
        statement = json.loads(statement_row['data'])
        return attempt, statement, self._review_fingerprint(attempt, statement)

    async def analyze_submission(self, aid, config):
        attempt, statement, fingerprint = self.submission_review_snapshot(aid)
        if aid in self.submission_review_pending:
            raise AIError('此提交的 AI 分析正在进行中')
        self.submission_review_pending.add(aid)
        try:
            data = {
                'problem': statement,
                'submission': {
                    'submission_id': str(attempt['submission_id']),
                    'account': attempt['handle'],
                    'source_code': attempt['code'],
                    'source_code_sha256': attempt['code_hash'],
                    'source_provenance': '已核验的本次提交源码',
                    'language': attempt['language'],
                    'verdict': attempt['verdict'],
                    'judge_result': attempt['judge_data'],
                },
            }
            completed = await self.ai.complete('submission_review', config.provider, config.model, data, SubmissionReview)
            serialized = json.dumps(completed.data, ensure_ascii=False)
            if re.search(r'```|#include|\bint\s+main\s*\(|\bdef\s+\w+\s*\(|\bfor\s*\(|\bwhile\s*\(|public\s+static', serialized):
                raise AIError('AI 输出疑似包含完整代码，已拦截；可以重试分析')
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT * FROM wb_attempts WHERE id=?', (aid,)).fetchone()
                current_statement = conn.execute('SELECT data FROM wb_problems WHERE id=?', (attempt['problem_id'],)).fetchone()
                if not current or not current_statement:
                    raise AIError('提交或题面已变化，请重新分析')
                current_attempt = self.decode(current)
                current_data = json.loads(current_statement['data'])
                current_available, current_reason = self.submission_review_availability(current_attempt)
                if not current_available or self._review_fingerprint(current_attempt, current_data) != fingerprint:
                    raise AIError(current_reason or '提交源码、语言、判题结果或题面已变化，请重新分析')
                result = {'result': completed.data, 'provenance': completed.provenance,
                          'fingerprint': fingerprint, 'analyzed_at': int(time.time())}
                conn.execute('INSERT INTO wb_submission_reviews VALUES(?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET fingerprint=excluded.fingerprint,data=excluded.data,created_at=excluded.created_at',
                             (aid, fingerprint, json.dumps(result, ensure_ascii=False), result['analyzed_at']))
            return {'attempt_id': aid, 'submission_id': str(attempt['submission_id']), 'analysis': result}
        finally:
            self.submission_review_pending.discard(aid)

    def decode(self, row):
        result = dict(row)
        for key in ('judge_data', 'review'):
            result[key] = json.loads(result[key]) if result[key] else None
        with self.db.connect() as conn:
            source = conn.execute('SELECT * FROM wb_submission_source_bindings WHERE attempt_id=?', (row['id'],)).fetchone()
        result['code_verified'] = bool(source and all(source[key] == result[value] for key, value in (
            ('problem_id', 'problem_id'), ('handle', 'handle'), ('submission_id', 'submission_id'),
            ('language', 'language'), ('code_hash', 'code_hash'))))
        return result

    def attempt(self, aid):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM wb_attempts WHERE id=?', (aid,)).fetchone()
        if not row:
            raise HTTPException(404, '提交代码记录不存在')
        return self.decode(row)

    def statement(self, pid):
        pid = normalize(pid)
        if not pid.startswith('codeforces:'):
            try:
                return self.store_statement(pid, fetch_statement(pid, self.accounts.document().get('accounts', [])))
            except (AIError, CrawlError) as exc:
                self.db.set('statement_status:'+pid, {'status':'failed','error':str(exc),'at':int(time.time())})
                raise AIError(str(exc)) from None
        account = next((a for a in self.accounts.document().get('accounts', []) if a.get('platform') == 'codeforces' and a.get('cookie')), {})
        contest, index = pid.split(':')[1].split('-')
        failures = []
        # Public statements should not be broken by a stale saved login cookie.
        cookies = [None] + ([account['cookie']] if account.get('cookie') else [])
        for cookie in cookies:
            client = Client(timeout=12, retries=0, cookie=cookie, cookie_host='codeforces.com' if cookie else None)
            try:
                for url in (source_url(pid), f'https://codeforces.com/contest/{contest}/problem/{index}'):
                    try:
                        result = parse_statement(client.get(url, params={'locale': 'en'}, headers={'User-Agent': default_user_agent()}), pid)
                        result['source'] = 'official'
                        return self.store_statement(pid, result)
                    except (CrawlError, AIError) as exc:
                        from ojcrawler.access import PlatformAccessError
                        if isinstance(exc, PlatformAccessError) and exc.reason == 'rate_limited':
                            error = '官方题面读取受到频率限制，已停止切换入口；请等待后重试，已有题面保留'
                            self.db.set('statement_status:'+pid, {'status':'failed','error':error,'at':int(time.time())})
                            raise AIError(error) from None
                        failures.append(str(exc))
            finally:
                client.close()
        error = '官方两种题面地址均未获取成功：' + '；'.join(failures) + '。可在浏览器完成平台验证后重试，或导入原题的 problem-statement HTML。服务器 IP 被限制时 Cookie 不一定有效。'
        self.db.set('statement_status:'+pid, {'status': 'failed', 'error': error, 'at': int(time.time())})
        raise AIError(error)

    def store_statement(self, pid, result):
        now = int(time.time())
        with self.db.connect() as conn:
            conn.execute('INSERT INTO wb_problems VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,fetched_at=excluded.fetched_at', (pid, json.dumps(result, ensure_ascii=False), now))
        self.db.set('statement_status:'+pid, {'status': 'completed', 'at': now})
        return {'problem_id': pid}

    def sync(self, aid, data):
        attempt = self.attempt(aid)
        if not attempt['problem_id'].startswith('codeforces:'):
            return self.sync_collected(attempt, data)
        handles = [a['handle'] for a in self.accounts.document().get('accounts', []) if a.get('platform') == 'codeforces']
        handle = next((h for h in handles if h.lower() == data.handle.lower()), None)
        if not handle:
            raise AIError('请先在账号设置中添加该 Codeforces 账号')
        if attempt['submission_id'] and (attempt['submission_id'] != data.submission_id or attempt['handle'].lower() != handle.lower()):
            raise AIError('代码记录已关联提交号，不可更换')
        if int(time.time()) - attempt['last_synced'] < 20:
            raise AIError('请等待 20 秒后再次同步')
        contest, index = attempt['problem_id'].split(':')[1].split('-')
        client = Client(timeout=25, retries=1)
        found = None
        try:
            for start in (1, 201, 401):
                response = client.json('https://codeforces.com/api/contest.status', params={'contestId': int(contest), 'handle': handle, 'from': start, 'count': 200})
                if response.get('status') != 'OK':
                    raise AIError('Codeforces 未返回有效判题记录')
                rows = response['result']
                found = next((r for r in rows if r.get('id') == data.submission_id), None)
                if found or len(rows) < 200 or rows[-1]['id'] < data.submission_id:
                    break
        except CrawlError as exc:
            from ojcrawler.access import PlatformAccessError
            if not isinstance(exc, PlatformAccessError) or exc.reason == 'rate_limited':
                raise AIError('判题同步未完成：' + str(exc)) from None
            from ojcrawler.codeforces_pages import read_submissions
            try:
                rows, _ = read_submissions(client, handle, max_pages=3, contest=contest, submission_id=data.submission_id)
                found = next((r for r in rows if r['id'] == data.submission_id), None)
            except CrawlError as fallback_error:
                raise AIError('官方 API 及提交列表均未完成核验：' + str(fallback_error)) from None
        finally:
            client.close()
        if not found:
            raise AIError('未在该账号最近 600 条本场比赛记录中找到提交号')
        problem = found.get('problem', {})
        members = found.get('author', {}).get('members', [])
        if str(problem.get('contestId')) != contest or problem.get('index') != index or len(members) != 1 or members[0].get('handle', '').lower() != handle.lower():
            raise AIError('提交账号或题号不匹配（暂不支持团队提交）')
        self.validate_submission_time(attempt, found.get('creationTimeSeconds'))
        status = codeforces_judge_verdict(found.get('verdict'))
        now = int(time.time())
        judge = {k: found.get(k) for k in ('verdict', 'passedTestCount', 'timeConsumedMillis', 'memoryConsumedBytes', 'creationTimeSeconds', 'programmingLanguage')}
        pid = f'{contest}-{index}'
        url = f'https://codeforces.com/contest/{contest}/submission/{data.submission_id}'
        standard = {'id': str(data.submission_id), 'problem_id': pid, 'submitted_at': found['creationTimeSeconds'], 'verdict': status,
                    'language': found.get('programmingLanguage'), 'contest_id': contest, 'time_ms': found.get('timeConsumedMillis'),
                    'memory_bytes': found.get('memoryConsumedBytes'), 'score': None, 'url': url,
                    'problem': {'title': problem.get('name'), 'tags': problem.get('tags', []), 'difficulty': problem.get('rating')}, 'raw': found}
        try:
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT handle,submission_id FROM wb_attempts WHERE id=?', (aid,)).fetchone()
                if current['submission_id'] and (current['submission_id'] != data.submission_id or current['handle'].lower() != handle.lower()):
                    raise AIError('另一请求已关联提交，请刷新页面')
                if conn.execute('SELECT 1 FROM wb_attempts WHERE substr(problem_id,1,instr(problem_id,":")-1)=? AND handle=? COLLATE NOCASE AND submission_id=? AND id!=?', ('codeforces', handle, data.submission_id, aid)).fetchone():
                    raise AIError('此平台提交已关联另一份代码记录')
                conn.execute('UPDATE wb_attempts SET handle=?,submission_id=?,verdict=?,judge_data=?,last_synced=?,review=CASE WHEN verdict=? AND judge_data=? THEN review ELSE NULL END WHERE id=?',
                             (handle, data.submission_id, status, json.dumps(judge), now, status, json.dumps(judge), aid))
                existing = conn.execute("SELECT account FROM submissions WHERE platform='codeforces' AND account=? COLLATE NOCASE AND id=?", (handle, str(data.submission_id))).fetchone()
                conn.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(platform,account,id) DO UPDATE SET verdict=excluded.verdict,data=excluded.data,updated_at=excluded.updated_at',
                             ('codeforces', existing['account'] if existing else handle, str(data.submission_id), pid, found['creationTimeSeconds'], status, json.dumps(standard), now))
        except sqlite3.IntegrityError:
            raise AIError('此平台提交已关联另一份代码记录') from None
        return {'attempt_id': aid, 'verdict': status, 'code_verified': False}

    def sync_collected(self, attempt, data):
        platform, key = attempt['problem_id'].split(':', 1)
        handles=[a['handle'] for a in self.accounts.document().get('accounts', []) if a.get('platform')==platform]
        if data.handle not in handles:
            raise AIError('请先绑定此平台账号，账号名需与配置一致')
        if attempt['submission_id'] and (attempt['submission_id']!=data.submission_id or attempt['handle']!=data.handle):
            raise AIError('代码记录已关联提交号，不可更换')
        now=int(time.time())
        try:
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row=conn.execute('SELECT * FROM submissions WHERE platform=? AND account=? AND id=?',(platform,data.handle,str(data.submission_id))).fetchone()
                if not row: raise AIError('本用户尚未采集到此账号的提交号，请先同步该平台账号信息后重试')
                if normalize(platform+':'+row['problem_id']) != attempt['problem_id']:
                    raise AIError('提交题号不匹配，请选择本题的提交记录')
                self.validate_submission_time(attempt, row['submitted_at'])
                raw=json.loads(row['data'])
                judge={k:raw.get(k) for k in ('language','submitted_at','time_ms','memory_bytes','score','url')}
                judge.update(verdict=row['verdict'],source='collected_official_record',platform=platform,collected_at=row['updated_at'])
                status=row['verdict'] or 'PENDING'
                serialized=json.dumps(judge)
                current=conn.execute('SELECT * FROM wb_attempts WHERE id=?',(attempt['id'],)).fetchone()
                if current['submission_id'] and (current['submission_id']!=data.submission_id or current['handle']!=data.handle):
                    raise AIError('另一请求已关联提交，请刷新页面')
                unchanged = current['verdict'] == status and official_judge(json.loads(current['judge_data'] or '{}')) == official_judge(judge)
                conn.execute('UPDATE wb_attempts SET handle=?,submission_id=?,verdict=?,judge_data=?,last_synced=?,review=CASE WHEN ? THEN review ELSE NULL END WHERE id=?',(data.handle,data.submission_id,status,serialized,now,unchanged,attempt['id']))
        except sqlite3.IntegrityError:
            raise AIError('此平台提交已关联另一份代码记录') from None
        return {'attempt_id':attempt['id'],'verdict':status,'code_verified':False,'source':'collected_official_record','collected_at':row['updated_at']}

    async def coach(self, pid, config, aid=None, *, automatic_token=None):
        pid = normalize(pid)
        async with self.lock:
            if automatic_token and not self.automatic_generation(automatic_token):
                return {'automatic_review': 'cancelled'}
            state = self.state(pid)
            if not state['statement']:
                raise AIError('请先成功获取题面')
            attempt = self.attempt(aid) if aid else None
            if attempt and (attempt['problem_id'] != pid or attempt['verdict'] not in ERROR_VERDICTS or not attempt.get('judge_data')):
                raise AIError('仅对本题已核验的未通过提交进行错因复盘')
            if not attempt and len(state['hints']) >= 8:
                raise AIError('本题已提供 8 次提示，请先独立尝试或复习已有提示')
            schema = Review if attempt else Hint
            data = {'problem': state['statement'], 'previous_hints': state['hints']}
            if attempt:
                data['attempt'] = {k: attempt[k] for k in ('code', 'language', 'verdict', 'judge_data')}
                data['code_correspondence_verified'] = False
            if automatic_token:
                # Collection timestamps change on every sync and are not a new official result.
                judge = official_judge(attempt['judge_data'])
                fingerprint = hashlib.sha256(json.dumps([pid, attempt['handle'], attempt['submission_id'],
                    attempt['code_hash'], attempt['verdict'], judge], sort_keys=True).encode()).hexdigest()
                with self.db.connect() as conn:
                    inserted = conn.execute('INSERT OR IGNORE INTO wb_auto_reviews VALUES(?,?)',
                        (fingerprint, int(time.time()))).rowcount
                if not inserted:
                    return {'automatic_review': 'already_requested'}
            request = asyncio.create_task(self.ai.complete('review' if attempt else 'hint', config.provider, config.model, data, schema))
            try:
                if automatic_token:
                    while not request.done():
                        await asyncio.wait({request}, timeout=.05)
                        if not self.automatic_generation(automatic_token):
                            return {'automatic_review': 'cancelled'}
                completed = await request
            finally:
                if not request.done():
                    request.cancel()
                    try:
                        await request
                    except asyncio.CancelledError:
                        pass
            if automatic_token and not self.automatic_generation(automatic_token):
                return {'automatic_review': 'cancelled'}
            result = completed.data
            serialized = json.dumps(result, ensure_ascii=False)
            if re.search(r'```|#include|\bint\s+main\s*\(|\bdef\s+\w+\s*\(|\bfor\s*\(|\bwhile\s*\(|public\s+static', serialized):
                raise AIError('AI 输出疑似包含代码，已拦截；请重试获取小提示')
            if attempt and result['line'] > len(attempt['code'].splitlines()):
                raise AIError('AI 引用代码行号越界，已丢弃')
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if automatic_token:
                    # Serialize the final consent check with disabling the preference.
                    row = conn.execute("SELECT value FROM web_settings WHERE key='automatic_coach'").fetchone()
                    settings = json.loads(row[0]) if row else {}
                    if not settings.get('enabled') or settings.get('generation') != automatic_token:
                        return {'automatic_review': 'cancelled'}
                if attempt:
                    current = conn.execute('SELECT * FROM wb_attempts WHERE id=?', (aid,)).fetchone()
                    if not current or current['verdict'] != attempt['verdict'] or current['code_hash'] != attempt['code_hash'] or official_judge(json.loads(current['judge_data'] or '{}')) != official_judge(attempt['judge_data']):
                        raise AIError('判题已改变，请重新复盘')
                    conn.execute('UPDATE wb_attempts SET review=? WHERE id=?', (json.dumps(result, ensure_ascii=False), aid))
                else:
                    conn.execute('INSERT INTO wb_hints VALUES(?,?,?,?)', (pid, len(state['hints']) + 1, json.dumps(result, ensure_ascii=False), int(time.time())))
            return {'problem_id': pid, 'attempt_id': aid, **({'automatic_review': 'completed'} if automatic_token else {})}


def register(app, service, jobs, daily=None):
    @app.get('/api/platforms')
    def platforms():
        from .problem_sources import HOSTS
        accounts=service.accounts.public()['items']
        return {'items':[{'platform':p,'account_collection':True,'statement':True,'ai_hint':True,'auto_submit':p in {'codeforces','atcoder'},'judge_sync':True,'accounts':[a for a in accounts if a['platform']==p]} for p in HOSTS]}

    @app.post('/api/platforms/{platform}/collect')
    async def collect_platform(platform: str):
        from .problem_sources import HOSTS
        if platform not in HOSTS: raise HTTPException(404, '平台不存在')
        if not any(a.get('platform')==platform for a in service.accounts.document().get('accounts', [])):
            raise HTTPException(400, '请先绑定此平台账号，再采集账号信息')
        return jobs.start('crawl', {'platform':platform})

    @app.get('/api/workbench/editor-settings')
    def editor_settings():
        return EditorSettings.model_validate(service.db.get('editor_settings', {})).model_dump()

    @app.put('/api/workbench/editor-settings')
    def save_editor_settings(data: EditorSettings):
        service.db.set('editor_settings', data.model_dump())
        return data.model_dump()

    @app.get('/api/workbench/layout-settings')
    def layout_settings():
        return LayoutSettings.model_validate(service.db.get('layout_settings', {})).model_dump()

    @app.put('/api/workbench/layout-settings')
    def save_layout_settings(data: LayoutSettings):
        service.db.set('layout_settings', data.model_dump())
        return data.model_dump()

    @app.get('/api/workbench/coach-settings')
    def coach_settings():
        return service.coach_settings()

    @app.put('/api/workbench/coach-settings')
    def save_coach_settings(data: CoachSettings):
        return service.set_coach_settings(data)

    @app.get('/api/workbench')
    def choices(): return service.choices()

    @app.get('/api/workbench/resolve')
    def resolve(problem: str = Query(max_length=300)): return service.state(problem)

    @app.get('/api/workbench/problem/{pid}')
    def state(pid: str): return service.state(pid)

    @app.put('/api/workbench/problem/{pid}/draft')
    def draft(pid: str, data: Draft): return service.draft(pid, data)

    @app.post('/api/workbench/problem/{pid}/import')
    def import_statement(pid: str, data: StatementImport):
        pid = normalize(pid)
        result = parse_statement(data.html, pid)
        result['source'] = 'user_import'
        return service.store_statement(pid, result)

    @app.post('/api/workbench/open')
    async def open_problem(data: Open):
        return jobs.start('wb_statement', {'pid': normalize(data.problem)})

    @app.get('/api/workbench/problem/{pid}/history')
    def history(pid: str):
        return service.history(pid)

    @app.get('/api/workbench/attempt/{aid}')
    def attempt(aid: str):
        return service.attempt(aid)

    @app.post('/api/workbench/problem/{pid}/submission')
    async def submission(pid: str, data: SubmissionLink):
        aid, created, handle = service.prepare_submission(pid, data)
        try:
            job = jobs.start('wb_sync', {'aid': aid, 'handle': handle, 'new_attempt': created,
                'submission_id': data.submission_id,
                'review': data.review.model_dump() if data.review else None})
        except Exception:
            # Queue rejection must not leave a newly claimed association behind.
            if created:
                with service.db.connect() as conn:
                    conn.execute("DELETE FROM wb_attempts WHERE id=? AND last_synced=0", (aid,))
            raise
        return {**job, 'attempt_id': aid}

    @app.post('/api/workbench/problem/{pid}/hint')
    async def hint(pid: str, data: Coach):
        return jobs.start('wb_hint', {'pid': normalize(pid), **data.model_dump()})

    @app.post('/api/workbench/attempt/{aid}/sync')
    async def sync(aid: str, data: Link):
        service.attempt(aid)
        return jobs.start('wb_sync', {'aid': aid, **data.model_dump()})

    @app.post('/api/workbench/attempt/{aid}/review')
    async def review(aid: str, data: Coach):
        attempt = service.attempt(aid)
        return jobs.start('wb_review', {'aid': aid, 'pid': attempt['problem_id'], **data.model_dump()})

    @app.post('/api/workbench/attempt/{aid}/submission-review')
    async def submission_review(aid: str, data: Coach):
        service.submission_review_snapshot(aid)
        if data.provider == 'api' and not service.ai.key():
            raise HTTPException(400, '尚未配置 AI API Key，请先在连接与设置完成 AI 配置')
        if data.provider == 'codex' and (not service.ai.codex or not getattr(service.ai.codex, 'enabled', True)):
            raise HTTPException(400, '官方 GPT 通道尚未登录或不可用，请配置 AI API')
        return jobs.start('wb_submission_review', {'aid': aid, **data.model_dump()})

    async def run(kind, payload):
        if kind == 'wb_statement': return await asyncio.to_thread(service.statement, payload['pid'])
        if kind == 'wb_sync':
            try:
                result = await asyncio.to_thread(service.sync, payload['aid'], Link.model_validate({k: payload[k] for k in ('handle', 'submission_id')}))
            except Exception:
                if payload.get('new_attempt'):
                    # An incorrect ID must not permanently claim this code or block retry.
                    with service.db.connect() as conn:
                        conn.execute("DELETE FROM wb_attempts WHERE id=? AND last_synced=0 AND verdict='UNLINKED'", (payload['aid'],))
                raise
            if result['verdict'] == 'AC' and daily:
                attempt = service.attempt(payload['aid'])
                judge = attempt.get('judge_data') or {}
                submitted_at = judge.get('creationTimeSeconds', judge.get('submitted_at'))
                if type(submitted_at) is int and daily.today(submitted_at) == daily.today():
                    try:
                        await asyncio.to_thread(daily.complete, Complete(problem_id=attempt['problem_id']))
                    except Exception:
                        result['checkin_error'] = '判题已保存，但签到未更新；请刷新每日任务或同步平台记录'
            return result
        if kind == 'wb_submission_review':
            return await service.analyze_submission(payload['aid'], Coach.model_validate({k: payload[k] for k in ('provider', 'model')}))
        if kind in {'wb_hint', 'wb_review'}:
            return await service.coach(payload['pid'], Coach.model_validate({k: payload[k] for k in ('provider', 'model')}), payload.get('aid'))
        raise AIError('未知工作台任务')
    jobs.run_workbench = run
