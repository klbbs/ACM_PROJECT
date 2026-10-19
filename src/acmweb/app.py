import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import analytics
from .ai import AIProvider, ProviderConfig
from .catalog import candidates
from .codex_bridge import AIError, CodexBridge
from .database import Database
from .jobs import Jobs
from .settings import Settings
from .accounts import Accounts, AccountList


class Login(BaseModel):
    token: str = Field(max_length=1000)


class Analyze(BaseModel):
    model_config=ConfigDict(extra="forbid")
    provider: Literal["api","codex"]
    model: str = Field(min_length=1,max_length=150)
    days: Literal[0,7,30,90] = 30
    platform: str = Field(default="",max_length=30)
    goal: str = Field(default="",max_length=1000)
    minimum: int = Field(default=800,ge=800,le=3500)
    maximum: int = Field(default=1600,ge=800,le=3500)
    tags: list[str] = Field(default_factory=list,max_length=10)
    min_contest: int = Field(default=0,ge=0,le=100000)


class Feedback(BaseModel):
    platform: Literal["codeforces","atcoder","luogu","nowcoder"]
    problem_id: str = Field(min_length=1,max_length=100)
    status: Literal["todo","done","skip"]
    note: str = Field(default="",max_length=1000)


class Training(BaseModel):
    model_config=ConfigDict(extra="forbid")
    goal_rating: int = Field(default=2000,ge=800,le=4000)
    minimum: int = Field(default=1000,ge=800,le=3500)
    maximum: int = Field(default=1600,ge=800,le=3500)
    min_contest: int = Field(default=0,ge=0,le=100000)
    preferred_tags: list[str] = Field(default_factory=list,max_length=20)
    avoid_tags: list[str] = Field(default_factory=list,max_length=20)
    prefer_recent: bool = True


def create_app(settings=None):
    settings=settings or Settings()
    settings.validate()
    if settings.multi_user:
        from .multiuser import create_multi_app
        return create_multi_app(settings, create_app)
    database=Database(settings.database)
    codex=CodexBridge(settings.codex_binary,settings.enable_codex,
                      home=settings.data_dir/'codex' if settings.production or settings.isolated else None,
                      device_only=settings.production or settings.isolated)
    account_config=Accounts(settings)
    ai=AIProvider(database,settings,codex)
    jobs=Jobs(database,settings,ai,account_config)
    @asynccontextmanager
    async def lifespan(app):
        yield
        await jobs.close()
        app.state.translations.close()
        await codex.close()

    app=FastAPI(title="ACM Lab API",version="0.11.0",lifespan=lifespan,docs_url=None if settings.production else "/api/docs",openapi_url="/api/openapi.json")
    app.state.database=database;app.state.jobs=jobs;app.state.ai=ai;app.state.codex=codex
    if not settings.access_token:
        app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost","[::1]","testserver"])
    session_value=hmac.new((settings.access_token or secrets.token_urlsafe(32)).encode(),b"acm-lab-session",hashlib.sha256).hexdigest()

    @app.middleware("http")
    async def protect(request,call_next):
        path=request.url.path
        public=path in {"/api/health","/api/auth/status","/api/auth/login"}
        if path.startswith("/api/") and not public:
            bearer=request.headers.get("authorization","").removeprefix("Bearer ")
            authorized=(not settings.access_token and request.client and request.client.host in {"127.0.0.1","::1","testclient"}) or (settings.access_token and (hmac.compare_digest(bearer,settings.access_token) or hmac.compare_digest(request.cookies.get("acm_session",""),session_value)))
            if not authorized:return JSONResponse({"detail":"请先使用访问令牌解锁工作台"},status_code=401)
        if path.startswith("/api/") and request.method not in {"GET","HEAD","OPTIONS"}:
            origin=request.headers.get("origin")
            if origin and urlsplit(origin).netloc!=request.headers.get("host"):
                return JSONResponse({"detail":"拒绝跨站请求"},status_code=403)
            content_type=request.headers.get("content-type","")
            if request.headers.get("content-length","0") not in {"0",""} and "application/json" not in content_type:
                return JSONResponse({"detail":"仅接受 JSON 请求"},status_code=415)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="same-origin"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        if path.startswith("/api/"):response.headers["Cache-Control"]="no-store"
        return response

    @app.exception_handler(AIError)
    async def ai_error(request,exc):return JSONResponse({"detail":str(exc)},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        # Pydantic's default errors include the input, which could be an API Key.
        return JSONResponse({"detail":"请求参数不符合要求","fields":[{"loc":e["loc"],"type":e["type"]} for e in exc.errors()]},status_code=422)

    @app.get("/api/health")
    def health():return {"status":"ok","version":"0.11.0"}

    @app.get("/api/auth/status")
    def auth_status(request:Request):
        cookie=request.cookies.get("acm_session","")
        bearer=request.headers.get("authorization","").removeprefix("Bearer ")
        return {"required":bool(settings.access_token),"authenticated":not settings.access_token or hmac.compare_digest(cookie,session_value) or hmac.compare_digest(bearer,settings.access_token)}

    @app.post("/api/auth/login")
    def login(payload:Login,response:Response,request:Request):
        if not settings.access_token or not hmac.compare_digest(payload.token,settings.access_token):
            raise HTTPException(401,"访问令牌不正确")
        loopback=request.url.hostname in {"localhost","127.0.0.1","::1"}
        response.set_cookie("acm_session",session_value,httponly=True,secure=settings.production and not loopback,samesite="strict",max_age=86400)
        return {"ok":True}

    @app.post("/api/auth/logout")
    def logout(response:Response):
        response.delete_cookie("acm_session");return {"ok":True}

    @app.get("/api/overview")
    def get_overview(days:int=Query(default=30,ge=0,le=90),platform:str=""):
        result = analytics.overview(database,days,platform)
        result["actual_time_evidence"] = app.state.daily.evidence()
        return result

    @app.get("/api/submissions")
    def get_submissions(platform:str="",verdict:str="",search:str=Query(default="",max_length=100),offset:int=Query(default=0,ge=0),limit:int=Query(default=30,ge=1,le=100),days:int=Query(default=0,ge=0,le=90)):
        return analytics.submissions(database,platform,verdict,search,offset,limit,days)

    @app.get("/api/accounts")
    def accounts():
        return account_config.public()

    @app.put("/api/accounts")
    async def save_accounts(payload:AccountList):
        if any(kind=='crawl' and not task.done() for kind,task in jobs.tasks.values()):
            raise HTTPException(409,"正在采集，请完成后再修改账号")
        return account_config.save(payload)

    @app.get("/api/jobs")
    def job_list():return {"items":jobs.list()}

    @app.post("/api/crawl")
    async def start_crawl():
        if not account_config.public()['configured']:raise HTTPException(400,"请先在连接与设置中添加平台账号")
        return jobs.start("crawl")

    @app.get("/api/ai/presets")
    def ai_presets():return ai.presets()

    @app.post("/api/ai/test")
    async def test_ai():return jobs.start("ai_test")

    @app.get("/api/ai/settings")
    def get_ai():return ai.public_config()

    @app.put("/api/ai/settings")
    def set_ai(config:ProviderConfig):return ai.save(config)

    @app.get("/api/ai/models")
    async def get_models(provider:Literal["api","codex"]="api"):return await ai.models(provider)

    @app.get("/api/ai/account")
    async def get_account():
        try:return {"available":True,**await codex.account()}
        except AIError as exc:return {"available":False,"connected":False,"message":str(exc)}

    @app.post("/api/ai/login")
    async def chatgpt_login(mode:Literal["device","browser"]="device"):return await codex.login(mode)

    @app.post("/api/ai/login/cancel")
    async def cancel_login():return await codex.cancel_login()

    @app.post("/api/ai/logout")
    async def official_logout():return await codex.logout()

    @app.post("/api/analysis")
    async def analyze(payload:Analyze):return jobs.start("analysis",payload.model_dump())

    @app.post("/api/recommend")
    async def recommend(payload:Analyze):
        if payload.minimum>payload.maximum:raise HTTPException(400,"最低难度不能超过最高难度")
        return jobs.start("recommend",payload.model_dump())

    @app.get("/api/reports")
    def reports():return {"items":database.reports()}

    @app.get("/api/reports/{report_id}")
    def report(report_id:str):
        result=database.report(report_id)
        if not result:raise HTTPException(404,"报告不存在")
        return result

    @app.get("/api/catalog")
    def catalog(minimum:int=Query(default=800,ge=800,le=3500),maximum:int=Query(default=1600,ge=800,le=3500),tags:str="",min_contest:int=Query(default=0,ge=0,le=100000),platform:Literal["codeforces","atcoder","luogu","nowcoder"]="codeforces"):
        return {"items":candidates(database,minimum,maximum,[t for t in tags.split(',') if t],60,min_contest,platform=platform),"updated_at":database.get("catalog_updated_at") if platform=="codeforces" else None,"source":"Codeforces 官方题库" if platform=="codeforces" else "已获取的官方题面缓存（非完整题库）"}

    @app.post("/api/catalog/refresh")
    async def refresh():return jobs.start("catalog")

    @app.post("/api/feedback")
    def feedback(payload:Feedback):
        with database.connect() as db:
            db.execute("INSERT INTO practice_feedback VALUES(?,?,?,?,?) ON CONFLICT(platform,problem_id) DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at",
                       (payload.platform,payload.problem_id,payload.status,payload.note,int(time.time())))
        return {"ok":True}

    @app.get("/api/training")
    def get_training():return {**Training().model_dump(),**database.get("training",{})}

    @app.put("/api/training")
    def set_training(payload:Training):
        if payload.minimum>payload.maximum:raise HTTPException(400,"最低难度不能超过最高难度")
        database.set("training",payload.model_dump());return payload.model_dump()

    from .daily import Daily, register as register_daily
    app.state.daily = Daily(database, ai)
    jobs.daily_evidence = app.state.daily.evidence
    register_daily(app, app.state.daily)

    from .workbench import Workbench, register
    app.state.workbench = Workbench(database, ai, account_config)
    register(app, app.state.workbench, jobs, app.state.daily)

    from .selftest import register_selftest
    register_selftest(app)

    # Personal translations are short-lived memory objects. Moderated
    # translations use one central store in multi-user deployments so an
    # approved model result can be reused by every tenant.
    from .translations import SharedTranslations, Translations, register_personal, register_shared
    shared_path = settings.translation_database or (settings.data_dir / "translations.sqlite3")
    shared_translations = SharedTranslations(shared_path)
    app.state.translation_store = shared_translations
    # Isolated child apps are served through the authenticated multi-user
    # parent; only that parent may expose moderation endpoints.
    app.state.multi_child = bool(settings.isolated)
    app.state.translations = Translations(app.state.workbench, ai, shared_translations, settings.translation_owner)
    register_personal(app, app.state.translations, jobs)

    async def run_translation(problem_id, payload):
        return await app.state.translations.generate(problem_id, payload)

    jobs.run_translation = run_translation

    async def local_translation_context(request, admin=False):
        # Single-user mode has one trusted owner; the regular app middleware
        # already authenticated the request before this route is reached.
        return app.state.translations.owner, app.state.translations

    if not settings.isolated:
        register_shared(app, shared_translations, local_translation_context)

    if (settings.static_dir/"assets").exists():
        app.mount("/assets",StaticFiles(directory=settings.static_dir/"assets"),name="assets")

    @app.get("/{path:path}")
    def frontend(path:str):
        if path.startswith("api/"):raise HTTPException(404,"API 不存在")
        if path in {"favicon.svg","third-party-notices.txt"} and (settings.static_dir/path).exists():return FileResponse(settings.static_dir/path)
        index=settings.static_dir/"index.html"
        if index.exists():return FileResponse(index)
        return JSONResponse({"message":"前端尚未构建，请运行 cd frontend && npm ci && npm run build"},status_code=503)
    return app
