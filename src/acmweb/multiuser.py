"""Authenticated multi-user boundary around isolated personal workspaces."""
import asyncio
import hmac
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Literal

from fastapi import HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .users import Users, csrf_token, digest, password_hash, instance_lock
from .security_headers import SecureFastAPI

COOKIE = 'acm_user_session'


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(pattern=r'^[a-zA-Z0-9_]{3,32}$')
    password: str = Field(min_length=10,max_length=128,repr=False)
    invitation: str = Field(default='',max_length=200,repr=False)
    setup_token: str = Field(default='',max_length=1000,repr=False)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1,max_length=128,repr=False)
    new_password: str = Field(min_length=10,max_length=128,repr=False)


class InitialAdminCredentials(Credentials):
    password: str = Field(min_length=10,max_length=128,repr=False)
    display_name: str = Field(default='', max_length=40)


class LoginCredentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(pattern=r'^[a-zA-Z0-9_]{3,32}$')
    password: str = Field(min_length=3,max_length=128,repr=False)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['admin','member']
    active: bool


class AdministratorRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=1000)


class AdministratorReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['approved', 'rejected']
    note: str = Field(default='', max_length=1000)


class Entry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1,max_length=150)
    body: str = Field(min_length=1,max_length=20000)
    visibility: Literal['private','shared'] = 'private'
    version: int = Field(default=1,ge=1)


class Workspaces:
    def __init__(self, settings, factory):
        self.settings, self.factory = settings, factory
        self.apps = {}
        self.lock = asyncio.Lock()
        self.process_slots = asyncio.Semaphore(4)

    def budget(self):
        count = sum(not task.done() for app, _ in self.apps.values() for _,task in app.state.jobs.tasks.values())
        count += sum(bool(getattr(app.state.daily, 'active_ai', False)) for app, _ in self.apps.values())
        count += sum(bool(getattr(getattr(app.state, name, None), 'active_ai', False))
                     for app, _ in self.apps.values() for name in ('specialist_training', 'training_assessment'))
        if count >= 4:
            from .codex_bridge import AIError
            raise AIError('服务器任务已满，请稍后重试')

    async def get(self, uid):
        async with self.lock:
            if uid not in self.apps:
                directory = self.settings.data_dir/'users'/uid
                directory.mkdir(parents=True,exist_ok=True,mode=0o700)
                directory.chmod(0o700)
                child = replace(self.settings, multi_user=False, isolated=True,
                                data_dir=directory, config_path=directory/'account.toml',
                                translation_database=self.settings.data_dir/'translations.sqlite3',
                                translation_owner=uid,
                                access_token=secrets.token_urlsafe(40), allow_env_ai_key=False)
                app = self.factory(child)
                app.state.internal_token = child.access_token
                app.state.multi_child = True
                # The owner is derived from this authenticated workspace, not
                # from any upload payload sent by the browser.
                app.state.translations.owner = uid
                app.state.jobs.guard = self.budget
                app.state.daily.guard = self.budget
                app.state.specialist_training.guard = self.budget
                app.state.training_assessment.guard = self.budget
                app.state.codex.process_slots = self.process_slots
                lifecycle = app.router.lifespan_context(app)
                await lifecycle.__aenter__()
                self.apps[uid] = (app,lifecycle)
            return self.apps[uid][0]

    async def remove(self, uid):
        async with self.lock:
            value = self.apps.pop(uid,None)
            if value:
                await value[1].__aexit__(None,None,None)

    async def close(self):
        for uid in list(self.apps):
            await self.remove(uid)


def create_multi_app(settings, factory):
    from .runtime_logs import get_event_log, emit_event, register_admin_logs, user_id_context
    event_log = get_event_log(settings.log_directory or settings.data_dir / 'logs')
    identities = Users(settings.data_dir/'users.sqlite3')
    from .ai_host_allowlist import AIHostAllowlist, register as register_ai_hosts
    ai_host_allowlist = AIHostAllowlist(identities, settings)
    workspaces = Workspaces(settings,factory)
    assets = factory(replace(settings,multi_user=False,access_token=secrets.token_urlsafe(40),
                             data_dir=settings.data_dir/'site-static',config_path=settings.data_dir/'site-static/missing.toml',
                             translation_database=settings.data_dir/'translations.sqlite3', translation_owner='assets',
                             enable_codex=False,server_codex=False))

    async def idle_cleanup():
        while True:
            await asyncio.sleep(60)
            for app,_ in list(workspaces.apps.values()):
                bridge = app.state.codex
                if bridge.proc and not bridge.inference_lock.locked() and not bridge.login_id and time.monotonic()-bridge.last_used > 300:
                    await bridge.close()

    @asynccontextmanager
    async def lifespan(app):
        with instance_lock(settings.data_dir/'.multiuser.lock'):
            identities.normalize_administrator()
            cleanup = None
            try:
                emit_event(event_log, 'service.started', mode='multi')
                # Restore existing user workspaces on server restart.
                with identities.connect() as db:
                    users = db.execute('SELECT id FROM users WHERE active=1').fetchall()
                for user in users:
                    if (settings.data_dir/'users'/user['id']/'accounts.sqlite3').exists():
                        await workspaces.get(user['id'])
                from .startup_diagnostics import report_multiuser
                report_multiuser(settings, identities, workspaces, event_log=event_log)
                cleanup = asyncio.create_task(idle_cleanup())
                yield
            finally:
                if cleanup:
                    cleanup.cancel()
                    await asyncio.gather(cleanup,return_exceptions=True)
                await workspaces.close()
                emit_event(event_log, 'service.stopped', mode='multi')

    app = SecureFastAPI(production=settings.production,title='ACM Lab users',version='0.21.6',lifespan=lifespan,docs_url=None,openapi_url=None)
    app.state.identities, app.state.workspaces = identities,workspaces
    app.state.event_log = event_log

    def owner_valid(uid, owner):
        now = int(time.time())
        with identities.connect() as db:
            return bool(db.execute('SELECT 1 FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.hash=? AND u.id=? AND u.active=1 AND s.expires_at>? AND s.seen_at>?', (owner, uid, now, now-7200)).fetchone())

    def clear_login(uid, owner=None):
        value = workspaces.apps.get(uid)
        if value:
            value[0].state.nowcoder_qr.clear(owner)
            value[0].state.browser_login.clear(owner)

    app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(settings.allowed_hosts))

    def require(request, admin=False):
        user = identities.resolve(request.cookies.get(COOKIE,''))
        if not user:
            raise HTTPException(401,'请先登录')
        if admin and user['role'] != 'admin':
            raise HTTPException(403,'需要管理员权限')
        return user

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return JSONResponse({'detail':'输入格式无效，请检查用户名、密码长度及必填内容'},status_code=422)

    @app.middleware('http')
    async def protect(request, call_next):
        path = request.url.path
        try:
            if path.startswith('/api/'):
                peer = request.client.host if request.client else 'unknown'
                user = identities.resolve(request.cookies.get(COOKIE,''))
                if not user:
                    identities.limit('ip:'+digest(peer),300,60)
                request.state.user = user
                user_id_context.set(user['id'] if user else None)
                public = path in {'/api/health','/api/auth/status','/api/auth/setup','/api/auth/login','/api/auth/register'}
                if not public:
                    require(request)
                    identities.limit('user:'+user['id'],240,60)
                if request.method not in {'GET','HEAD','OPTIONS'}:
                    origin = request.headers.get('origin')
                    expected = settings.public_origin or f'{request.url.scheme}://{request.headers.get("host","")}'
                    if origin and origin != expected:
                        raise HTTPException(403,'拒绝跨站请求')
                    if request.headers.get('sec-fetch-site') == 'cross-site':
                        raise HTTPException(403,'拒绝跨站请求')
                    if request.headers.get('x-acm-request') != '1':
                        raise HTTPException(403,'缺少请求校验头')
                    if not public and not hmac.compare_digest(request.headers.get('x-csrf-token','').encode(),csrf_token(request.cookies.get(COOKIE,'')).encode()):
                        raise HTTPException(403,'会话校验已失效，请刷新页面')
                    if 'application/json' not in request.headers.get('content-type',''):
                        raise HTTPException(415,'仅接受 JSON 请求')
                    async def bounded_body():
                        chunks, size = [],0
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > 524288:
                                raise HTTPException(413,'请求内容过大')
                            chunks.append(chunk)
                        return b''.join(chunks)
                    try:
                        request._body = await asyncio.wait_for(bounded_body(),15)
                    except asyncio.TimeoutError:
                        raise HTTPException(408,'请求上传超时') from None
                    if path in {'/api/auth/setup','/api/auth/login','/api/auth/register'}:
                        operation = path.rsplit('/', 1)[-1]
                        identities.limit('auth-ip:'+operation+':'+digest(peer),120 if operation == 'login' else 30,900)
                    elif user and path == '/api/platform-login/nowcoder/start':
                        identities.limit('platform-login-start:'+user['id'],6,60)
                        identities.limit('platform-login-start-global',30,60)
                    elif user and ((path.startswith('/api/problem-submissions/') and path.endswith('/source'))
                                   or path == '/api/remote-submissions/options'
                                   or (path.startswith('/api/remote-submissions/') and path.endswith('/refresh'))):
                        # Read-only OJ queries do not consume the paid AI/send
                        # allowance. Keep a separate bounded per-user budget.
                        try:
                            identities.limit('platform-read:'+user['id'],20,60)
                        except HTTPException as exc:
                            if exc.status_code != 429:
                                raise
                            raise HTTPException(429, 'ACM Lab 的平台只读查询过于频繁，请按提示等待后重试',
                                                headers=exc.headers) from None
                    elif user and (path in {'/api/hermes/draw','/api/hermes/draw/image'} or (path.startswith('/api/training/plans') and request.method == 'POST') or (path.startswith('/api/platforms/') and path.endswith('/collect')) or path.startswith('/api/problem-submissions/') or path.startswith('/api/workbench/') and not path.endswith(('/draft','/snapshot','/editor-settings','/coach-settings','/layout-settings','/code-templates')) or path.startswith('/api/remote-submissions') and not path.endswith('/dismiss') or path.startswith('/api/social/duels/') and path.endswith(('/action','/check')) or path in {'/api/accounts/check','/api/crawl','/api/analysis','/api/recommend','/api/catalog/refresh','/api/training/catalog/refresh','/api/training/method-check','/api/ai/login','/api/ai/test','/api/daily/roll'}):
                        identities.limit('cost:'+user['id'],6,60)
                response = await call_next(request)
            else:
                response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)
        return response

    @app.get('/api/health')
    def health():
        return {'status':'ok'} if settings.production else {'status':'ok','version':'0.21.6','mode':'multi-user'}

    @app.get('/api/auth/status')
    def status(request:Request):
        user = getattr(request.state,'user',None)
        return {'required':True,'multi_user':True,'authenticated':bool(user),
                'setup_required':identities.count()==0,'user':user,
                'csrf_token':csrf_token(request.cookies[COOKIE]) if user else None}

    def set_session(response, uid):
        response.set_cookie(COOKIE,identities.session(uid),httponly=True,secure=settings.production,
                            samesite='strict',max_age=86400,path='/')

    @app.post('/api/auth/setup')
    def setup(payload:InitialAdminCredentials,response:Response):
        if not settings.access_token or not hmac.compare_digest(digest(payload.setup_token),digest(settings.access_token)):
            raise HTTPException(403,'初始化令牌不正确')
        if identities.count():
            raise HTTPException(409,'管理员已初始化')
        uid = identities.create(payload.username.lower(),payload.password,setup=True,max_users=settings.max_users,
                                display_name=payload.display_name.strip())
        set_session(response,uid)
        return {'ok':True}

    @app.post('/api/auth/register')
    def register(payload:Credentials,response:Response):
        if len(payload.password) < 10:
            raise HTTPException(422,'成员密码至少 10 位')
        uid = identities.create(payload.username.lower(),payload.password,invitation=payload.invitation,max_users=settings.max_users)
        set_session(response,uid)
        return {'ok':True}

    @app.post('/api/auth/login')
    def login(payload:LoginCredentials,response:Response,request:Request):
        peer = request.client.host if request.client else 'unknown'
        identities.limit('login-source:'+digest(peer+'\0'+payload.username.lower()),10,900)
        user = identities.authenticate(payload.username.lower(),payload.password)
        set_session(response,user['id'])
        return {'ok':True}

    @app.post('/api/auth/logout')
    async def logout(request:Request,response:Response):
        user = require(request)
        clear_login(user['id'], digest(request.cookies.get(COOKIE,'')))
        with identities.connect() as db:
            db.execute('DELETE FROM sessions WHERE hash=?',(digest(request.cookies.get(COOKIE,'')),))
        response.delete_cookie(COOKIE,secure=settings.production,httponly=True,samesite='strict')
        return {'ok':True}

    @app.post('/api/auth/password')
    async def change_password(payload:PasswordChange,request:Request,response:Response):
        user = require(request)
        identities.limit('password:'+user['id'],5,900)
        await asyncio.to_thread(identities.authenticate,user['username'],payload.current_password)
        hashed = await asyncio.to_thread(password_hash,payload.new_password)
        with identities.connect() as db:
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            identities.audit('password.changed',user['id'],None,db)
        clear_login(user['id'])
        response.delete_cookie(COOKIE,secure=settings.production,httponly=True,samesite='strict')
        return {'ok':True,'reauthenticate':True}

    @app.post('/api/auth/logout-all')
    async def logout_all(request:Request,response:Response):
        user = require(request)
        with identities.connect() as db:
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            identities.audit('sessions.revoked',user['id'],None,db)
        clear_login(user['id'])
        response.delete_cookie(COOKIE,secure=settings.production,httponly=True,samesite='strict')
        return {'ok':True}

    def current_admin(db, actor, request):
        # Recheck privileges and this exact session while holding the write lock.
        now = int(time.time())
        valid = db.execute("""SELECT 1 FROM users u JOIN sessions s ON s.user_id=u.id
            WHERE u.id=? AND u.role='admin' AND u.active=1 AND s.hash=?
            AND s.expires_at>? AND s.seen_at>?""",
            (actor['id'], digest(request.cookies.get(COOKIE, '')), now, now-7200)).fetchone()
        if not valid:
            raise HTTPException(403, '管理员身份已变更，请重新登录')

    @app.get('/api/auth/administrator-requests')
    def own_administrator_requests(request:Request):
        user = require(request)
        with identities.connect() as db:
            rows = db.execute("""SELECT r.id,r.reason,r.status,r.created_at,r.reviewed_at,
                r.review_note,a.username AS reviewer FROM administrator_requests r
                LEFT JOIN users a ON a.id=r.reviewed_by WHERE r.user_id=?
                ORDER BY r.created_at DESC,r.rowid DESC LIMIT 20""", (user['id'],)).fetchall()
        return {'items': [dict(r) for r in rows]}

    @app.post('/api/auth/administrator-requests')
    def request_administrator(payload:AdministratorRequest, request:Request):
        user = require(request)
        reason = payload.reason.strip()
        if not reason:
            raise HTTPException(422, '请填写申请理由')
        identities.limit('administrator-request:'+user['id'], 3, 86400)
        rid, now = uuid.uuid4().hex, int(time.time())
        with identities.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT role,active FROM users WHERE id=?', (user['id'],)).fetchone()
            if not current or not current['active'] or current['role'] == 'admin':
                raise HTTPException(409, '当前账号无需或无法申请管理员')
            if db.execute("SELECT 1 FROM administrator_requests WHERE user_id=? AND status='pending'", (user['id'],)).fetchone():
                raise HTTPException(409, '已有待审核申请，请等待管理员处理')
            db.execute("INSERT INTO administrator_requests(id,user_id,reason,status,created_at) VALUES(?,?,?,'pending',?)",
                       (rid, user['id'], reason, now))
            identities.audit('administrator.requested', user['id'], rid, db)
        return {'id': rid, 'status': 'pending'}

    @app.get('/api/admin/administrator-requests')
    def administrator_requests(request:Request):
        require(request, True)
        with identities.connect() as db:
            rows = db.execute("""SELECT r.*,u.username,a.username AS reviewer
                FROM administrator_requests r JOIN users u ON u.id=r.user_id
                LEFT JOIN users a ON a.id=r.reviewed_by
                ORDER BY (r.status='pending') DESC,r.created_at DESC,r.rowid DESC LIMIT 100""").fetchall()
        return {'items': [dict(r) for r in rows]}

    @app.post('/api/admin/administrator-requests/{rid}/review')
    def review_administrator(rid:str, payload:AdministratorReview, request:Request):
        actor = require(request, True)
        with identities.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current_admin(db, actor, request)
            row = db.execute('SELECT * FROM administrator_requests WHERE id=?', (rid,)).fetchone()
            if not row:
                raise HTTPException(404, '申请不存在')
            if row['user_id'] == actor['id']:
                raise HTTPException(403, '不能批准或审核自己的申请')
            if row['status'] != 'pending':
                raise HTTPException(409, '申请已经处理，请刷新列表')
            target = db.execute('SELECT role,active FROM users WHERE id=?', (row['user_id'],)).fetchone()
            if payload.decision == 'approved':
                if not target or not target['active'] or target['role'] != 'member':
                    raise HTTPException(409, '仅能批准启用中的普通成员')
                db.execute("UPDATE users SET role='admin' WHERE id=?", (row['user_id'],))
                db.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
            db.execute('UPDATE administrator_requests SET status=?,reviewed_at=?,reviewed_by=?,review_note=? WHERE id=?',
                       (payload.decision, int(time.time()), actor['id'], payload.note.strip(), rid))
            identities.audit('administrator.'+payload.decision, actor['id'], row['user_id'], db)
        if payload.decision == 'approved':
            clear_login(row['user_id'])
        return {'ok': True, 'status': payload.decision, 'reauthenticate': payload.decision == 'approved'}

    @app.get('/api/admin/users')
    def users(request:Request):
        require(request,True)
        with identities.connect() as db:
            rows = db.execute('SELECT id,username,role,active,created_at FROM users ORDER BY created_at,id').fetchall()
        return {'items':[dict(r) for r in rows]}

    @app.post('/api/presence')
    def heartbeat(request:Request):
        user = require(request)
        now = int(time.time())
        with identities.connect() as db:
            db.execute('DELETE FROM online_presence WHERE seen_at<?', (now-90,))
            db.execute('INSERT INTO online_presence VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET seen_at=excluded.seen_at', (user['id'], now))
        return {'ok': True}

    @app.get('/api/presence')
    def online_users(request:Request):
        user = require(request)
        now = int(time.time())
        with identities.connect() as db:
            rows = db.execute('''SELECT u.id,u.username FROM online_presence p JOIN users u ON u.id=p.user_id
                WHERE u.active=1 AND p.seen_at>=? AND EXISTS (SELECT 1 FROM sessions s WHERE s.user_id=u.id
                AND s.expires_at>? AND s.seen_at>?) ORDER BY u.username''', (now-90,now,now-7200)).fetchall()
        return {'items': [{'username': r['username'], 'self': r['id']==user['id']} for r in rows], 'expires_after': 90}

    @app.put('/api/admin/users/{uid}')
    async def update_user(uid:str,payload:UserUpdate,request:Request):
        actor = require(request,True)
        if uid == actor['id']:
            raise HTTPException(400,'不能停用自己或修改自己的角色')
        with identities.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current_admin(db, actor, request)
            target = db.execute('SELECT role,active FROM users WHERE id=?',(uid,)).fetchone()
            if not target:
                raise HTTPException(404,'用户不存在')
            if payload.role == 'admin' and target['role'] != 'admin':
                raise HTTPException(409, '新增管理员需要本人申请并经已有管理员批准')
            if target['role'] == 'admin' and target['active'] and (payload.role != 'admin' or not payload.active):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                    raise HTTPException(409, '至少保留一位启用中的管理员')
            db.execute('UPDATE users SET role=?,active=? WHERE id=?',(payload.role,int(payload.active),uid))
            db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            identities.audit('user.updated',actor['id'],uid,db)
        clear_login(uid)
        if not payload.active:
            await workspaces.remove(uid)
        return {'ok':True}

    @app.post('/api/admin/invitations')
    def invite(request:Request):
        actor = require(request,True)
        identities.limit('invites:'+actor['id'],20,900)
        token, expiry = secrets.token_urlsafe(24),int(time.time())+86400
        with identities.connect() as db:
            db.execute('DELETE FROM invitations WHERE expires_at<?',(int(time.time()),))
            db.execute('INSERT INTO invitations VALUES(?,?,?,NULL)',(digest(token),actor['id'],expiry))
            identities.audit('invitation.created',actor['id'],None,db)
        return {'invitation':token,'expires_at':expiry}

    @app.get('/api/admin/audit')
    def audit(request:Request):
        require(request,True)
        with identities.connect() as db:
            rows = db.execute('SELECT a.*,u.username FROM audit a LEFT JOIN users u ON a.actor=u.id ORDER BY a.id DESC LIMIT 100').fetchall()
        return {'items':[dict(r) for r in rows]}

    def authorize_logs(request):
        actor = require(request, True)
        identities.limit('admin-logs:' + actor['id'], 60, 60)

    register_admin_logs(app, event_log, authorize_logs)

    def authorize_ai_hosts(request):
        return require(request, True)

    register_ai_hosts(app, ai_host_allowlist, identities, workspaces, authorize_ai_hosts, current_admin)

    @app.get('/api/shared')
    def shared(request:Request,search:str=Query(default='',max_length=100),offset:int=Query(default=0,ge=0),limit:int=Query(default=30,ge=1,le=50),trashed:bool=False):
        user = require(request)
        with identities.connect() as db:
            scope = "(e.owner_id=? OR e.visibility='shared')" if not trashed else ("(e.owner_id=? OR e.visibility='shared')" if user['role']=='admin' else 'e.owner_id=?')
            where = f"e.deleted={int(trashed)} AND {scope} AND (e.title LIKE ? OR e.body LIKE ?)"
            args = (user['id'],'%'+search+'%','%'+search+'%')
            count = db.execute('SELECT COUNT(*) FROM shared_entries e WHERE '+where,args).fetchone()[0]
            rows = db.execute('SELECT e.*,u.username AS author FROM shared_entries e JOIN users u ON e.owner_id=u.id WHERE '+where+' ORDER BY e.updated_at DESC,e.id LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return {'total':count,'items':[dict(r) for r in rows]}

    def insert_entry(payload,user):
        eid, now = uuid.uuid4().hex,int(time.time())
        with identities.connect() as db:
            db.execute('INSERT INTO shared_entries VALUES(?,?,?,?,?,1,0,?,?)',(eid,user['id'],payload.title,payload.body,payload.visibility,now,now))
            identities.audit('entry.created',user['id'],eid,db)
        return {'id':eid,'version':1}

    @app.post('/api/shared')
    def create_entry(payload:Entry,request:Request):
        user = require(request)
        identities.limit('entry:'+user['id'],30,900)
        return insert_entry(payload,user)

    @app.put('/api/shared/{eid}')
    def update_entry(eid:str,payload:Entry,request:Request):
        user = require(request)
        with identities.connect() as db:
            # Even administrators cannot read/edit another user's private drafts.
            row = db.execute('SELECT * FROM shared_entries WHERE id=? AND deleted=0 AND (owner_id=? OR visibility=\'shared\')',(eid,user['id'])).fetchone()
            if not row:
                raise HTTPException(404,'资料不存在')
            if row['owner_id'] != user['id']:
                raise HTTPException(403,'只有作者可以编辑此资料')
            result = db.execute('UPDATE shared_entries SET title=?,body=?,visibility=?,version=version+1,updated_at=? WHERE id=? AND version=?',(payload.title,payload.body,payload.visibility,int(time.time()),eid,payload.version))
            if result.rowcount != 1:
                raise HTTPException(409,'资料已被更新，请刷新后重试')
            identities.audit('entry.updated',user['id'],eid,db)
        return {'id':eid,'version':payload.version+1}

    @app.delete('/api/shared/{eid}')
    def delete_entry(eid:str,request:Request):
        user = require(request)
        with identities.connect() as db:
            result = db.execute("UPDATE shared_entries SET deleted=1,version=version+1 WHERE id=? AND deleted=0 AND (owner_id=? OR (visibility='shared' AND ?='admin'))",(eid,user['id'],user['role']))
            if result.rowcount != 1:
                raise HTTPException(404,'资料不存在或没有移除权限')
            identities.audit('entry.removed',user['id'],eid,db)
        return {'ok':True}

    @app.post('/api/shared/report/{report_id}')
    async def share_report(report_id:str,request:Request):
        user = require(request)
        identities.limit('entry:'+user['id'],30,900)
        workspace = await workspaces.get(user['id'])
        report = workspace.state.database.report(report_id)
        if not report:
            raise HTTPException(404,'报告不存在')
        # Share only an explicit summary snapshot; raw submissions/config stay private.
        data = report['data']
        return insert_entry(Entry(title=str(data.get('title','训练报告'))[:150],body=str(data.get('summary',''))[:20000] or '暂无摘要',visibility='shared'),user)

    @app.post('/api/shared/{eid}/restore')
    def restore_entry(eid:str,request:Request):
        user = require(request)
        with identities.connect() as db:
            result = db.execute("UPDATE shared_entries SET deleted=0,version=version+1 WHERE id=? AND deleted=1 AND (owner_id=? OR (visibility='shared' AND ?='admin'))",(eid,user['id'],user['role']))
            if result.rowcount != 1:
                raise HTTPException(404,'资料不存在或没有恢复权限')
            identities.audit('entry.restored',user['id'],eid,db)
        return {'ok':True}

    from .social import register as register_social
    register_social(app, identities, workspaces, require)

    # Translation submissions are stored centrally, but the source statement
    # and generated translation remain in the submitting user's workspace.
    # Every request resolves the current session again so role changes and
    # disabled accounts take effect immediately.
    from .translations import SharedTranslations, register_shared
    translation_store = SharedTranslations(settings.data_dir/'translations.sqlite3')
    app.state.translation_store = translation_store

    async def translation_context(request, admin=False):
        user = require(request, admin)
        child = await workspaces.get(user['id'])
        return user['id'], child.state.translations

    register_shared(app, translation_store, translation_context)

    @app.api_route('/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'])
    async def dispatch(path:str,request:Request):
        if path.startswith('api/'):
            user = require(request)
            if path.startswith(('api/auth/','api/admin/','api/shared/','api/translations','api/social/')):
                raise HTTPException(404,'接口不存在')
            child = await workspaces.get(user['id'])
            headers = [(k,v) for k,v in request.scope['headers'] if k.lower() not in {b'authorization',b'cookie'}]
            headers.append((b'authorization',('Bearer '+child.state.internal_token).encode()))
            child.state.nowcoder_qr.owner_valid = lambda owner: owner_valid(user['id'], owner)
            child.state.browser_login.owner_valid = lambda owner: owner_valid(user['id'], owner)
            scope = {**request.scope,'headers':headers, 'state': {**request.scope.get('state', {}), 'browser_login_owner': digest(request.cookies.get(COOKIE,''))}}
            return ChildResponse(child,scope,request.receive)
        return ChildResponse(assets,request.scope,request.receive)

    return app


class ChildResponse(Response):
    """Forward ASGI directly, preserving streaming and the child's status/headers."""
    def __init__(self, app, scope, receive):
        super().__init__()
        self.child,self.scope,self.receive = app,scope,receive

    async def __call__(self, scope, receive, send):
        await self.child(self.scope,self.receive,send)
