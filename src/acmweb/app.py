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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
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
from ojcrawler.config import load


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


class Schedule(BaseModel):
    enabled: bool = False
    hour: int = Field(default=9,ge=0,le=23)
    minute: int = Field(default=0,ge=0,le=59)
    analyze: bool = False
    provider: Literal["api","codex"] = "api"
    model: str = Field(default="",max_length=150)


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
    database=Database(settings.database)
    codex=CodexBridge(settings.codex_binary,settings.enable_codex)
    ai=AIProvider(database,settings,codex)
    jobs=Jobs(database,settings,ai)
    scheduler=AsyncIOScheduler(timezone="Asia/Shanghai")

    async def scheduled():
        schedule=database.get("schedule",{})
        try:
            job=jobs.start("crawl")
            task=jobs.tasks[job["id"]][1]
            await task
            with database.connect() as db:
                state=db.execute("SELECT status FROM web_jobs WHERE id=?",(job["id"],)).fetchone()[0]
            if schedule.get("analyze") and state=="completed":
                jobs.start("analysis",{"provider":schedule.get("provider","api"),"model":schedule.get("model",""),"days":30})
        except AIError:
            pass

    def configure_schedule():
        if scheduler.get_job("daily"):
            scheduler.remove_job("daily")
        schedule=database.get("schedule",{})
        if schedule.get("enabled"):
            scheduler.add_job(scheduled,CronTrigger(hour=schedule.get("hour",9),minute=schedule.get("minute",0),timezone="Asia/Shanghai"),id="daily",max_instances=1,coalesce=True,misfire_grace_time=3600)

    @asynccontextmanager
    async def lifespan(app):
        configure_schedule();scheduler.start()
        yield
        scheduler.shutdown(wait=False)
        await jobs.close();await codex.close()

    app=FastAPI(title="ACM Lab API",version="0.2.0",lifespan=lifespan,docs_url=None if settings.production else "/api/docs",openapi_url="/api/openapi.json")
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
        response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        if path.startswith("/api/"):response.headers["Cache-Control"]="no-store"
        return response

    @app.exception_handler(AIError)
    async def ai_error(request,exc):return JSONResponse({"detail":str(exc)},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        # Pydantic's default errors include the input, which could be an API Key.
        return JSONResponse({"detail":"请求参数不符合要求","fields":[{"loc":e["loc"],"type":e["type"]} for e in exc.errors()]},status_code=422)

    @app.get("/api/health")
    def health():return {"status":"ok","version":"0.2.0"}

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
        return analytics.overview(database,days,platform)

    @app.get("/api/submissions")
    def get_submissions(platform:str="",verdict:str="",search:str=Query(default="",max_length=100),offset:int=Query(default=0,ge=0),limit:int=Query(default=30,ge=1,le=100),days:int=Query(default=0,ge=0,le=90)):
        return analytics.submissions(database,platform,verdict,search,offset,limit,days)

    @app.get("/api/accounts")
    def accounts():
        if not settings.config_path.exists():return {"items":[],"configured":False}
        try:
            config=load(settings.config_path)
        except (ValueError,OSError):
            raise HTTPException(400,"账号配置无效，请检查 account.toml 和 Cookie 环境变量")
        return {"configured":True,"items":[{"platform":a.platform,"handle":a.handle,"has_cookie":bool(a.cookie or a.cookie_env),"enrich":a.enrich} for a in config.accounts]}

    @app.get("/api/jobs")
    def job_list():return {"items":jobs.list()}

    @app.post("/api/crawl")
    async def start_crawl():
        if not settings.config_path.exists():raise HTTPException(400,"请先在服务端创建 account.toml")
        return jobs.start("crawl")

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
    def catalog(minimum:int=Query(default=800,ge=800,le=3500),maximum:int=Query(default=1600,ge=800,le=3500),tags:str="",min_contest:int=Query(default=0,ge=0,le=100000)):
        return {"items":candidates(database,minimum,maximum,[t for t in tags.split(',') if t],60,min_contest),"updated_at":database.get("catalog_updated_at"),"source":"Codeforces 官方题库"}

    @app.post("/api/catalog/refresh")
    async def refresh():return jobs.start("catalog")

    @app.post("/api/feedback")
    def feedback(payload:Feedback):
        with database.connect() as db:
            db.execute("INSERT INTO practice_feedback VALUES(?,?,?,?,?) ON CONFLICT(platform,problem_id) DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at",
                       (payload.platform,payload.problem_id,payload.status,payload.note,int(time.time())))
        return {"ok":True}

    @app.get("/api/schedule")
    def get_schedule():
        schedule=database.get("schedule",Schedule().model_dump())
        job=scheduler.get_job("daily")
        return {**schedule,"timezone":"Asia/Shanghai","next_run":str(job.next_run_time) if job and scheduler.running else None}

    @app.get("/api/training")
    def get_training():return database.get("training",Training().model_dump())

    @app.put("/api/training")
    def set_training(payload:Training):
        if payload.minimum>payload.maximum:raise HTTPException(400,"最低难度不能超过最高难度")
        database.set("training",payload.model_dump());return payload.model_dump()

    @app.put("/api/schedule")
    def set_schedule(payload:Schedule):
        if payload.analyze and not payload.model:raise HTTPException(400,"自动分析需要指定模型")
        if payload.provider=="codex" and not settings.enable_codex:raise HTTPException(400,"服务器部署请使用 API Key")
        database.set("schedule",payload.model_dump());configure_schedule();return get_schedule()

    if (settings.static_dir/"assets").exists():
        app.mount("/assets",StaticFiles(directory=settings.static_dir/"assets"),name="assets")

    @app.get("/{path:path}")
    def frontend(path:str):
        if path.startswith("api/"):raise HTTPException(404,"API 不存在")
        if path=="favicon.svg" and (settings.static_dir/path).exists():return FileResponse(settings.static_dir/path)
        index=settings.static_dir/"index.html"
        if index.exists():return FileResponse(index)
        return JSONResponse({"message":"前端尚未构建，请运行 cd frontend && npm ci && npm run build"},status_code=503)
    return app
