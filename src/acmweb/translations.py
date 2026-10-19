"""Private, structure-preserving translations and moderated shared statements."""
import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup, NavigableString, Comment
from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .codex_bridge import AIError
from .workbench import normalize, source_url


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Block(Strict):
    id: str = Field(max_length=30)
    text: str = Field(max_length=100000)


class Translated(Strict):
    blocks: list[Block] = Field(max_length=1000)


class Translate(Strict):
    engine: Literal['ai', 'google', 'bing'] = 'ai'
    provider: Literal['api', 'codex'] = 'api'
    model: str = Field(default='', max_length=150)
    consent: Literal[True]


class Moderation(Strict):
    decision: Literal['approved', 'rejected']
    note: str = Field(default='', max_length=1000)


class Revocation(Strict):
    note: str = Field(min_length=1, max_length=1000)


def fingerprint(statement):
    # Only immutable statement content contributes; fetch metadata never does.
    keys = ('id', 'url', 'title', 'body_html', 'sections', 'samples', 'images', 'time_limit', 'memory_limit')
    content = {key: statement.get(key) for key in keys}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


PROTECTED = re.compile(r'\$\$\$.*?\$\$\$|\$\$.*?\$\$|\$(?:\\.|[^$\n])+\$|\\\(.*?\\\)|\\\[.*?\\\]|https?://[^\s<>]+|`[^`]*`', re.S)
TOKEN = re.compile(r'ACMPROTECT_[a-f0-9]{16}_\d+_END')


class Document:
    """Translate text nodes only; never entrust HTML, images, examples or code to a model."""
    def __init__(self, statement):
        self.original = statement
        self.soup = BeautifulSoup(statement.get('body_html', ''), 'html.parser')
        self.blocks, self.locations, self.protected = [], [], {}
        self.prefix = uuid.uuid4().hex[:16]
        self.add(statement.get('title', ''), ('title', None))
        if statement.get('body_html'):
            for node in list(self.soup.find_all(string=True)):
                if isinstance(node, Comment) or any(p.name in {'pre', 'code', 'script', 'style', 'math', 'svg'} for p in node.parents):
                    continue
                self.add(str(node), ('node', node))
        else:
            for i, text in enumerate(statement.get('sections', [])):
                self.add(text, ('section', i))
        if len(self.blocks) > 1000 or sum(len(b['text']) for b in self.blocks) > 90000:
            raise AIError('题面过大，请使用官方网页翻译')

    def add(self, text, location):
        if not text.strip():
            return
        def hide(match):
            token = f'ACMPROTECT_{self.prefix}_{len(self.protected)}_END'
            self.protected[token] = match.group()
            return token
        masked = PROTECTED.sub(hide, text)
        # Entirely mathematical text needs no translation.
        if not TOKEN.sub('', masked).strip():
            return
        self.blocks.append({'id': str(len(self.blocks)), 'text': masked})
        self.locations.append(location)

    def apply(self, data):
        try:
            blocks = Translated.model_validate(data).blocks
        except Exception:
            raise AIError('翻译结果格式无效，未保存') from None
        if len(blocks) != len(self.blocks):
            raise AIError('翻译缺少段落，未保存')
        result = copy.deepcopy(self.original)
        for before, block, (kind, target) in zip(self.blocks, blocks, self.locations):
            if before['id'] != block.id or not block.text.strip() or TOKEN.findall(before['text']) != TOKEN.findall(block.text):
                raise AIError('翻译改变了段落或公式占位符，未保存')
            text = block.text
            if 'ACMPROTECT_' in TOKEN.sub('', text):
                raise AIError('翻译包含损坏的占位符，未保存')
            if len(text) > max(1000, len(before['text']) * 8):
                raise AIError('翻译结果长度异常，未保存')
            for token in TOKEN.findall(text):
                text = text.replace(token, self.protected[token])
            if kind == 'title': result['title'] = text
            elif kind == 'section': result['sections'][target] = text
            else: target.replace_with(NavigableString(text))
        if result.get('body_html'):
            result['body_html'] = str(self.soup)
            result['sections'] = [self.soup.get_text('\n', strip=True)]
        result['translation_language'] = 'zh-CN'
        return result


class SharedTranslations:
    def __init__(self, path):
        self.path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS translations (id TEXT PRIMARY KEY,owner TEXT NOT NULL,private_id TEXT NOT NULL,problem_id TEXT NOT NULL,fingerprint TEXT NOT NULL,target TEXT NOT NULL,model_key TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL,source_statement TEXT NOT NULL,submitted_at INTEGER NOT NULL,reviewed_at INTEGER,reviewer TEXT,review_note TEXT NOT NULL DEFAULT '');
            DROP INDEX IF EXISTS translation_active;
            CREATE UNIQUE INDEX translation_active ON translations(problem_id,fingerprint,target,model_key) WHERE status IN ('pending','approved','revoked');
            CREATE TABLE IF NOT EXISTS translation_audit (id INTEGER PRIMARY KEY,at INTEGER NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL);
            ''')
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()

    @staticmethod
    def public(row, source=False):
        item = json.loads(row['data'])
        item.update(id=row['id'], scope='shared', status=row['status'], submitted_at=row['submitted_at'], review_note=row['review_note'])
        # Public consumers receive the actual model identity, never the submitting account.
        if source: item['source_statement'] = json.loads(row['source_statement'])
        return item

    def approved(self, pid, fp):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM translations WHERE problem_id=? AND fingerprint=? AND status='approved' ORDER BY submitted_at DESC", (pid, fp)).fetchall()
        return [self.public(r) for r in rows]

    def own_status(self, owner, private_id):
        with self.connect() as db:
            row = db.execute('SELECT status,review_note FROM translations WHERE owner=? AND private_id=? ORDER BY submitted_at DESC,rowid DESC LIMIT 1', (owner, private_id)).fetchone()
        return dict(row) if row else {'status': 'private', 'review_note': ''}

    def upload(self, service, owner, tid):
        item = service.get(tid)
        if item['engine'] != 'ai' or not item['shareable']:
            raise HTTPException(400, '只有服务器获取的官方题面 AI 译文可以共享')
        statement = service.statement(item['problem_id'])
        if fingerprint(statement) != item['fingerprint'] or statement.get('source') != 'official':
            raise HTTPException(409, '原题版本已更新或非官方来源，请重新翻译')
        sid, now = uuid.uuid4().hex, int(time.time())
        with self.connect() as db:
            try:
                db.execute("INSERT INTO translations VALUES(?,?,?,?,?, 'zh-CN',?, 'pending',?,?,?,NULL,NULL,'')", (sid, owner, tid, item['problem_id'], item['fingerprint'], item['model_key'], json.dumps(item, ensure_ascii=False), json.dumps(statement, ensure_ascii=False), now))
            except sqlite3.IntegrityError:
                raise HTTPException(409, '该题面版本的同一模型已有待审核或已通过译文') from None
            db.execute('INSERT INTO translation_audit(at,actor,action,target) VALUES(?,?,?,?)', (now, owner, 'uploaded', sid))
        return {'id': sid, 'status': 'pending'}

    def queue(self, status):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM translations WHERE status=? ORDER BY submitted_at DESC LIMIT 100', (status,)).fetchall()
        return {'items': [self.public(r, True) for r in rows]}

    def review(self, sid, actor, payload):
        with self.connect() as db:
            row = db.execute("UPDATE translations SET status=?,reviewed_at=?,reviewer=?,review_note=? WHERE id=? AND status='pending'", (payload.decision, int(time.time()), actor, payload.note, sid))
            if row.rowcount != 1: raise HTTPException(409, '译文不存在或已经审核')
            db.execute('INSERT INTO translation_audit(at,actor,action,target) VALUES(?,?,?,?)', (int(time.time()), actor, payload.decision, sid))
        return {'id': sid, 'status': payload.decision}

    def revoke(self, sid, actor, payload):
        now = int(time.time())
        with self.connect() as db:
            row = db.execute("UPDATE translations SET status='revoked',reviewed_at=?,reviewer=?,review_note=? WHERE id=? AND status='approved'", (now, actor, payload.note, sid))
            if row.rowcount != 1: raise HTTPException(409, '译文不存在或当前未共享')
            db.execute('INSERT INTO translation_audit(at,actor,action,target) VALUES(?,?,?,?)', (now, actor, 'revoked', sid))
        return {'id': sid, 'status': 'revoked'}

    def audit(self):
        with self.connect() as db:
            rows = db.execute('SELECT at,actor,action,target FROM translation_audit ORDER BY id DESC LIMIT 100').fetchall()
        return {'items': [dict(row) for row in rows]}


class Translations:
    def __init__(self, wb, ai, shared, owner='local'):
        self.wb, self.db, self.ai, self.shared = wb, wb.db, ai, shared
        # The owner is assigned from the authenticated tenant by the parent
        # application. It is never accepted from a browser request.
        self.owner = owner
        self.ephemeral = {}
        self.ephemeral_lock = threading.Lock()
        with self.db.connect() as db:
            db.execute('DROP TABLE IF EXISTS private_translations')

    def statement(self, pid):
        statement = self.wb.state(pid)['statement']
        if not statement: raise HTTPException(404, '请先获取题面')
        return statement

    @staticmethod
    def shareable(statement):
        return statement.get('source') == 'official' and statement.get('url') == source_url(normalize(statement['id']))

    def get(self, tid):
        with self.ephemeral_lock:
            row = self.ephemeral.get(tid)
        if not row or row['created_at'] < int(time.time()) - 86400:
            raise HTTPException(404, '个人译文已过期，请重新生成')
        return row

    @staticmethod
    def services():
        return {'google': {'configured': bool(os.getenv('ACM_GOOGLE_TRANSLATE_KEY')), 'label': 'Google Cloud Translation'}, 'bing': {'configured': bool(os.getenv('ACM_BING_TRANSLATE_KEY')), 'label': 'Microsoft Translator'}}

    def listing(self, pid):
        pid = normalize(pid)
        statement = self.statement(pid)
        fp = fingerprint(statement)
        now = int(time.time())
        with self.ephemeral_lock:
            self.ephemeral = {key: value for key, value in self.ephemeral.items() if value['created_at'] >= now - 86400}
            private = [item for item in self.ephemeral.values() if item['problem_id'] == pid and item['fingerprint'] == fp]
        private = [{**item, **self.shared.own_status(self.owner, item['id'])} for item in private]
        shared = self.shared.approved(pid, fp) if self.shareable(statement) else []
        url = quote(source_url(pid), safe='')
        return {'fingerprint': fp, 'shareable': self.shareable(statement), 'items': private + shared, 'services': self.services(), 'links': {'google': f'https://translate.google.com/?sl=auto&tl=zh-CN&op=websites&text={url}', 'bing': f'https://www.bing.com/translator?from=auto&to=zh-Hans&text={url}'}, 'note': '共享译文经过人工审核；AI 译文可能有误，请结合原题。普通翻译使用管理员配置的官方付费 API，未配置时可打开官方翻译网页。'}

    async def generate(self, pid, payload):
        pid = normalize(pid)
        statement = self.statement(pid)
        fp = fingerprint(statement)
        if self.shareable(statement):
            cached = self.shared.approved(pid, fp)
            if cached:
                return {'translation_id': cached[0]['id'], 'reused': True}
        doc = Document(statement)
        if not doc.blocks: raise AIError('题面没有可翻译的文字')
        if payload.engine == 'ai':
            if not payload.model: raise AIError('请选择 AI 模型')
            result = await self.ai.complete('translation', payload.provider, payload.model, {'blocks': doc.blocks, 'target_language': 'zh-CN'}, Translated)
            data, provenance = result.data, result.provenance
        else:
            data = await self.ordinary(payload.engine, doc.blocks)
            provenance = {'channel': 'translation_api', 'vendor': payload.engine, 'model': 'official-service'}
        translated = doc.apply(data)
        # The key is derived solely from actual server-side identity, never supplied vendor text.
        model_key = hashlib.sha256(json.dumps({k: provenance.get(k) for k in ('channel', 'endpoint', 'vendor', 'protocol', 'model')}, sort_keys=True).encode()).hexdigest()
        tid, now = uuid.uuid4().hex, int(time.time())
        item = {'id': tid, 'problem_id': pid, 'fingerprint': fp, 'engine': payload.engine, 'model': provenance.get('model'), 'provenance': provenance, 'model_key': model_key, 'statement': translated, 'created_at': now, 'scope': 'private', 'shareable': payload.engine == 'ai' and self.shareable(statement), 'status': 'private'}
        with self.ephemeral_lock:
            self.ephemeral[tid] = item
        return {'translation_id': tid, 'reused': False}

    async def ordinary(self, engine, blocks):
        key = os.getenv('ACM_GOOGLE_TRANSLATE_KEY' if engine == 'google' else 'ACM_BING_TRANSLATE_KEY', '')
        if not key: raise AIError('管理员尚未配置该翻译服务，请使用官方翻译网页入口')
        try:
            # Official services cap both the number of strings and the request
            # size. Keep each batch comfortably below those documented limits
            # and combine only after every batch has validated its response.
            item_limit, char_limit = (128, 30000) if engine == 'google' else (100, 50000)
            batches, current, size = [], [], 0
            for block in blocks:
                block_size = len(block['text'].encode('utf-8'))
                if current and (len(current) >= item_limit or size + block_size > char_limit):
                    batches.append(current)
                    current, size = [], 0
                current.append(block)
                size += block_size
            if current:
                batches.append(current)
            translated = []
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                for batch in batches:
                    if engine == 'google':
                        response = await client.post('https://translation.googleapis.com/language/translate/v2', headers={'X-Goog-Api-Key': key}, json={'q': [b['text'] for b in batch], 'target': 'zh-CN', 'format': 'text'})
                    else:
                        headers = {'Ocp-Apim-Subscription-Key': key, 'Content-Type': 'application/json'}
                        region = os.getenv('ACM_BING_TRANSLATE_REGION', '')
                        if region: headers['Ocp-Apim-Subscription-Region'] = region
                        response = await client.post('https://api.cognitive.microsofttranslator.com/translate', params={'api-version': '3.0', 'to': 'zh-Hans', 'textType': 'plain'}, headers=headers, json=[{'Text': b['text']} for b in batch])
                    if response.status_code != 200: raise AIError(f'官方翻译服务返回 HTTP {response.status_code}，请管理员检查密钥与配额')
                    raw = response.json()
                    if engine == 'google':
                        import html
                        texts = [html.unescape(row['translatedText']) for row in raw['data']['translations']]
                    else: texts = [row['translations'][0]['text'] for row in raw]
                    if len(texts) != len(batch): raise ValueError()
                    translated.extend(texts)
                return {'blocks': [{'id': b['id'], 'text': text} for b, text in zip(blocks, translated)]}
        except AIError: raise
        except Exception: raise AIError('官方翻译请求失败或格式无效，未保存，请检查服务端网络') from None


def register_personal(app, service, jobs):
    @app.get('/api/workbench/problem/{pid}/translations')
    def listing(pid: str, request: Request):
        return {**service.listing(pid), 'can_review': getattr(request.state, 'translation_admin', not getattr(app.state, 'multi_child', False))}

    @app.post('/api/workbench/problem/{pid}/translate')
    async def translate(pid: str, payload: Translate):
        service.statement(normalize(pid))
        return jobs.start('translation', {'problem_id': normalize(pid), **payload.model_dump()})


def register_shared(app, store, context):
    @app.post('/api/translations/{tid}/upload')
    async def upload(tid: str, request: Request):
        actor, service = await context(request, False)
        return store.upload(service, actor, tid)

    @app.get('/api/translations/review')
    async def queue(request: Request, status: Literal['pending', 'approved', 'rejected', 'revoked'] = 'pending'):
        await context(request, True)
        return store.queue(status)

    @app.post('/api/translations/{sid}/review')
    async def review(sid: str, payload: Moderation, request: Request):
        actor, _ = await context(request, True)
        return store.review(sid, actor, payload)

    @app.post('/api/translations/{sid}/revoke')
    async def revoke(sid: str, payload: Revocation, request: Request):
        actor, _ = await context(request, True)
        return store.revoke(sid, actor, payload)

    @app.get('/api/translations/audit')
    async def audit(request: Request):
        await context(request, True)
        return store.audit()
