"""Authenticated multi-user boundary around isolated personal workspaces."""
import asyncio
import hmac
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .users import Users, csrf_token, digest, password_hash, instance_lock

COOKIE = 'acm_user_session'


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(pattern=r'^[a-zA-Z0-9_]{3,32}$')
    password: str = Field(min_length=15,max_length=128,repr=False)
    invitation: str = Field(default='',max_length=200,repr=False)
    setup_token: str = Field(default='',max_length=1000,repr=False)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1,max_length=128,repr=False)
    new_password: str = Field(min_length=15,max_length=128,repr=False)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['admin','member']
    active: bool


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
                                access_token=secrets.token_urlsafe(40), allow_env_ai_key=False)
                app = self.factory(child)
                app.state.internal_token = child.access_token
                app.state.jobs.guard = self.budget
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
    identities = Users(settings.data_dir/'users.sqlite3')
    workspaces = Workspaces(settings,factory)
    assets = factory(replace(settings,multi_user=False,access_token=secrets.token_urlsafe(40),
                             data_dir=settings.data_dir/'site-static',config_path=settings.data_dir/'site-static/missing.toml',
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
            cleanup = None
            try:
                # Restore schedules only for active users with an existing workspace.
                with identities.connect() as db:
                    users = db.execute('SELECT id FROM users WHERE active=1').fetchall()
                for user in users:
                    if (settings.data_dir/'users'/user['id']/'accounts.sqlite3').exists():
                        await workspaces.get(user['id'])
                cleanup = asyncio.create_task(idle_cleanup())
                yield
            finally:
                if cleanup:
                    cleanup.cancel()
                    await asyncio.gather(cleanup,return_exceptions=True)
                await workspaces.close()

    app = FastAPI(title='ACM Lab users',version='0.4.0',lifespan=lifespan,docs_url=None,openapi_url=None)
    app.state.identities, app.state.workspaces = identities,workspaces
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
                identities.limit('ip:'+digest(peer),300,60)
                user = identities.resolve(request.cookies.get(COOKIE,''))
                request.state.user = user
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
                        identities.limit('auth-ip:'+digest(peer),30,900)
                        identities.limit('auth-global',60,60)
                    elif user and path in {'/api/crawl','/api/analysis','/api/recommend','/api/catalog/refresh','/api/ai/login'}:
                        identities.limit('cost:'+user['id'],6,60)
                response = await call_next(request)
            else:
                response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)
        response.headers.update({
            'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
            'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy':"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        })
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if settings.production:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.get('/api/health')
    def health():
        return {'status':'ok','version':'0.4.0','mode':'multi-user'}

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
    def setup(payload:Credentials,response:Response):
        if identities.count():
            raise HTTPException(409,'管理员已初始化')
        if not settings.access_token or not hmac.compare_digest(digest(payload.setup_token),digest(settings.access_token)):
            raise HTTPException(403,'初始化令牌不正确')
        uid = identities.create(payload.username.lower(),payload.password,setup=True,max_users=settings.max_users)
        set_session(response,uid)
        return {'ok':True}

    @app.post('/api/auth/register')
    def register(payload:Credentials,response:Response):
        uid = identities.create(payload.username.lower(),payload.password,invitation=payload.invitation,max_users=settings.max_users)
        set_session(response,uid)
        return {'ok':True}

    @app.post('/api/auth/login')
    def login(payload:Credentials,response:Response):
        identities.limit('login-name:'+digest(payload.username.lower()),10,900)
        user = identities.authenticate(payload.username.lower(),payload.password)
        set_session(response,user['id'])
        return {'ok':True}

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response):
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
        response.delete_cookie(COOKIE,secure=settings.production,httponly=True,samesite='strict')
        return {'ok':True,'reauthenticate':True}

    @app.post('/api/auth/logout-all')
    def logout_all(request:Request,response:Response):
        user = require(request)
        with identities.connect() as db:
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            identities.audit('sessions.revoked',user['id'],None,db)
        response.delete_cookie(COOKIE,secure=settings.production,httponly=True,samesite='strict')
        return {'ok':True}

    @app.get('/api/admin/users')
    def users(request:Request):
        require(request,True)
        with identities.connect() as db:
            rows = db.execute('SELECT id,username,role,active,created_at FROM users ORDER BY created_at,id').fetchall()
        return {'items':[dict(r) for r in rows]}

    @app.put('/api/admin/users/{uid}')
    async def update_user(uid:str,payload:UserUpdate,request:Request):
        actor = require(request,True)
        if uid == actor['id']:
            raise HTTPException(400,'不能停用自己或修改自己的角色')
        with identities.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT role,active FROM users WHERE id=?',(actor['id'],)).fetchone()
            if not current or not current['active'] or current['role']!='admin':
                raise HTTPException(403,'需要管理员权限')
            if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
                raise HTTPException(404,'用户不存在')
            db.execute('UPDATE users SET role=?,active=? WHERE id=?',(payload.role,int(payload.active),uid))
            db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            identities.audit('user.updated',actor['id'],uid,db)
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

    @app.api_route('/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'])
    async def dispatch(path:str,request:Request):
        if path.startswith('api/'):
            user = require(request)
            if path.startswith(('api/auth/','api/admin/','api/shared/')):
                raise HTTPException(404,'接口不存在')
            child = await workspaces.get(user['id'])
            headers = [(k,v) for k,v in request.scope['headers'] if k.lower() not in {b'authorization',b'cookie'}]
            headers.append((b'authorization',('Bearer '+child.state.internal_token).encode()))
            scope = {**request.scope,'headers':headers}
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
