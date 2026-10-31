import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import date as calendar_date
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Annotated
from urllib.parse import urlsplit

from fastapi import HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, AfterValidator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import analytics
from .ai import AIProvider, ProviderConfig
from .catalog import recommendation_candidates, recommendation_status
from .codex_bridge import AIError, CodexBridge
from .database import Database
from .jobs import Jobs
from .settings import Settings
from .security_headers import SecureFastAPI
from .accounts import Accounts, AccountList


class Login(BaseModel):
    token: str = Field(max_length=1000)


class CrawlRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    gym_url: str = Field(default='', max_length=500)
    day: str = Field(default='', max_length=10)


class SpecialistCatalogRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    topic_id: str = Field(min_length=1, max_length=80)


class TopicMethodRequest(SpecialistCatalogRequest):
    cf_minimum: int | None = Field(default=None, ge=800, le=3500)
    cf_maximum: int | None = Field(default=None, ge=800, le=3500)


def publication_date(value):
    if value is not None:
        calendar_date.fromisoformat(value)
    return value


PublicationDate = Annotated[str | None, Field(pattern=r'^\d{4}-\d{2}-\d{2}$'), AfterValidator(publication_date)]


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
    published_after: PublicationDate = None


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
    published_after: PublicationDate = None
    preferred_tags: list[str] = Field(default_factory=list,max_length=20)
    avoid_tags: list[str] = Field(default_factory=list,max_length=20)
    prefer_recent: bool = True


def create_app(settings=None):
    settings=settings or Settings()
    settings.validate()
    from .runtime_logs import get_event_log, emit_event, register_admin_logs
    if settings.log_directory is None:
        settings.log_directory = settings.data_dir / 'logs'
    event_log = get_event_log(settings.log_directory)
    if settings.multi_user:
        from .multiuser import create_multi_app
        return create_multi_app(settings, create_app)
    database=Database(settings.database)
    codex=CodexBridge(settings.codex_binary,settings.enable_codex,
                      home=settings.data_dir/'codex' if settings.production or settings.isolated else None,
                      device_only=settings.production or settings.isolated)
    account_config=Accounts(settings)
    from .credential_check import register as register_credential_checks
    ai=AIProvider(database,settings,codex)
    jobs=Jobs(database,settings,ai,account_config)
    jobs.event_log = event_log
    @asynccontextmanager
    async def lifespan(app):
        if not settings.isolated:
            from .startup_diagnostics import report_single
            emit_event(event_log, 'service.started', mode='single')
            report_single(app)
        try:
            yield
        finally:
            app.state.remote_submissions.closed = True
            app.state.nowcoder_qr.close()
            await app.state.browser_login.aclose()
            await app.state.credential_checks.close()
            await jobs.close()
            if hasattr(app.state, 'hermes'):
                app.state.hermes.close()
            app.state.translations.close()
            await codex.close()
            if not settings.isolated:
                emit_event(event_log, 'service.stopped', mode='single')

    private_schema = settings.production or settings.isolated
    app=SecureFastAPI(production=settings.production,title="ACM Lab API",version="0.21.8",lifespan=lifespan,docs_url=None if private_schema else "/api/docs",openapi_url=None if private_schema else "/api/openapi.json")
    app.state.settings=settings
    app.state.account_config=account_config
    app.state.event_log=event_log
    if not settings.isolated and settings.translation_owner != 'assets':
        register_admin_logs(app, event_log)
    register_credential_checks(app,account_config)
    from .nowcoder_qr import register as register_nowcoder_qr
    register_nowcoder_qr(app, account_config, jobs)
    from .extension import register as register_extension
    from .announcements import register as register_announcements
    register_extension(app)
    register_announcements(app, database)
    app.state.database=database;app.state.jobs=jobs;app.state.ai=ai;app.state.codex=codex
    ai.event_log = event_log
    from .browser_login_routes import register as register_browser_login
    register_browser_login(app, account_config)

    app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(settings.allowed_hosts))
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
        return response

    @app.exception_handler(AIError)
    async def ai_error(request,exc):
        from .runtime_logs import record_failure
        record_failure(request.scope, exc)
        return JSONResponse({"detail":str(exc)},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        # Pydantic's default errors include the input, which could be an API Key.
        return JSONResponse({"detail":"请求参数不符合要求","fields":[{"loc":e["loc"],"type":e["type"]} for e in exc.errors()]},status_code=422)

    @app.get("/api/health")
    def health():return {"status":"ok"} if settings.production else {"status":"ok","version":"0.21.8"}

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
        app.state.nowcoder_qr.clear()
        app.state.browser_login.clear()
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
        result = account_config.save(payload)
        if payload.auto_check:
            result['connection_checks'] = app.state.credential_checks.schedule_connections()
        return result

    @app.get("/api/jobs")
    def job_list():return {"items":jobs.list()}

    @app.post("/api/crawl")
    async def start_crawl(payload: CrawlRequest | None = None):
        if not account_config.public()['configured']:raise HTTPException(400,"请先在连接与设置中添加平台账号")
        if payload and payload.day:
            from .gym_submissions import start_of_day
            try:
                start_of_day(payload.day)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        if payload and payload.gym_url:
            from .gym_submissions import contest_from_url
            try:
                contest_from_url(payload.gym_url)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            if not any(a['platform'] == 'codeforces' for a in account_config.public()['items']):
                raise HTTPException(400, '请先添加 Codeforces 账号，再补充 Gym 比赛范围')
        return jobs.start("crawl", payload.model_dump(exclude_defaults=True) if payload else None)

    @app.get("/api/ai/presets")
    def ai_presets():return ai.presets()

    @app.post("/api/ai/test")
    async def test_ai():
        config = ai.config()
        if not config['model'].strip():
            raise AIError('请先保存默认模型 ID')
        if not ai.key(config):
            raise AIError('尚未为当前 API 地址配置 Key，请保存该提供方的密钥')
        return jobs.start("ai_test")

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
        return jobs.start("recommend",{**payload.model_dump(), 'platform':''})

    @app.get("/api/reports")
    def reports(kind: Literal['analysis','recommend'] | None = None):
        return {"items":database.reports(kind)}

    @app.get("/api/reports/{report_id}")
    def report(report_id:str):
        result=database.report(report_id)
        if not result:raise HTTPException(404,"报告不存在")
        return result

    @app.get("/api/catalog")
    def catalog(minimum:int=Query(default=800,ge=800,le=3500),maximum:int=Query(default=1600,ge=800,le=3500),tags:str="",published_after:PublicationDate=None):
        if minimum>maximum:raise HTTPException(400,"最低难度不能超过最高难度")
        status = recommendation_status(database,account_config,published_after=published_after,minimum=minimum,maximum=maximum,tags=[t for t in tags.split(',') if t])
        with database.connect() as db:
            recent = db.execute("SELECT data FROM ai_reports WHERE kind='recommend' ORDER BY created_at DESC LIMIT 1").fetchone()
        preferred = [p['id'] for p in json.loads(recent['data']).get('selected_problems', [])] if recent else []
        pool = recommendation_candidates(database,account_config,minimum,maximum,[t for t in tags.split(',') if t],60,published_after=published_after,preferred_ids=preferred) if not status['requires_publication_date'] and any(p['login_verified'] for p in status['platforms']) else []
        return {"items":pool,"updated_at":database.get("catalog_updated_at"),"source":"所有已验证登录平台的官方候选题", "eligibility":status}

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
    app.state.daily = Daily(database, ai, account_config)
    app.state.daily.event_log = event_log
    jobs.daily_evidence = app.state.daily.evidence
    register_daily(app, app.state.daily)

    from .workbench import Workbench, register
    app.state.workbench = Workbench(database, ai, account_config)
    register(app, app.state.workbench, jobs, app.state.daily)
    from .selection_reports import SelectionReports, register as register_selection_reports
    app.state.selection_reports = SelectionReports(database, ai, app.state.workbench, event_log=event_log)
    jobs.selection_reports = app.state.selection_reports
    app.state.daily.selection_reports = app.state.selection_reports
    register_selection_reports(app, app.state.selection_reports)
    from .code_templates import register as register_code_templates
    register_code_templates(app, database)

    from .remote_submissions import RemoteSubmissions, register as register_remote_submissions
    app.state.remote_submissions = RemoteSubmissions(database, account_config, app.state.workbench, jobs, app.state.daily)
    register_remote_submissions(app, app.state.remote_submissions)
    from .problem_submissions import ProblemSubmissions, register as register_problem_submissions
    app.state.problem_submissions = ProblemSubmissions(database, account_config, app.state.workbench, app.state.remote_submissions, jobs)
    register_problem_submissions(app, app.state.problem_submissions)

    from .training import Training as SpecialistTraining, register as register_specialist_training
    from .training_assessment import TrainingAssessment, register as register_training_assessment
    app.state.specialist_training = SpecialistTraining(database, ai, account_config, event_log=event_log)
    app.state.specialist_training.selection_reports = app.state.selection_reports
    from .topic_feasibility import TopicFeasibility
    app.state.topic_feasibility = TopicFeasibility(database, account_config, app.state.workbench, ai)
    app.state.specialist_training.topic_feasibility = app.state.topic_feasibility
    app.state.daily.specialist_training = app.state.specialist_training
    register_specialist_training(app, app.state.specialist_training)

    from .training import Generate as SpecialistGenerate
    class BackgroundSpecialistGenerate(SpecialistGenerate):
        request_key: str = Field(min_length=32, max_length=32, pattern=r'^[a-f0-9]{32}$')

    @app.post('/api/training/plans/generate')
    async def queue_specialist_generation(payload: BackgroundSpecialistGenerate):
        from .training_topics import TOPICS
        if payload.topic_id not in TOPICS:
            raise HTTPException(422, '请选择支持的训练专题')
        selection = ai.selection()
        if not selection['ready']:
            raise HTTPException(400, '请先在连接与设置中保存可用的默认 AI 模型')
        if (payload.provider, payload.model) != (selection['channel'], selection['model']):
            raise HTTPException(409, '默认 AI 配置已变化，请重新读取配置后生成')
        return jobs.start('specialist_generate', payload.model_dump(exclude={'request_key'}), request_key=payload.request_key)

    @app.get('/api/training/generation')
    def specialist_generation_status(request_key: str | None = Query(default=None, min_length=32, max_length=32, pattern=r'^[a-f0-9]{32}$')):
        with database.connect() as connection:
            if request_key is not None:
                row = connection.execute("SELECT j.* FROM web_jobs j JOIN web_job_requests r ON r.job_id=j.id "
                    "WHERE r.request_key=? AND r.kind='specialist_generate' AND j.kind='specialist_generate'", (request_key,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM web_jobs WHERE kind='specialist_generate' "
                    "ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END,created_at DESC,rowid DESC LIMIT 1").fetchone()
        if not row:
            return {'job':None}
        return {'job':{**dict(row), 'result':json.loads(row['result']) if row['result'] else None}}

    async def run_specialist_generate(job_id, payload):
        selection = ai.selection()
        if not selection['ready'] or (selection['channel'], selection['model']) != (payload['provider'], payload['model']):
            raise AIError('默认 AI 配置已变化或不可用，本次尚未生成，请重新发起')
        def progress(value):
            # Callback is request-scoped and closes before Jobs writes its final
            # state. Never persist raw model, task statement or account values.
            stage = value.get('stage')
            if stage not in {'validating','catalog','method_check','selecting','reading','saving'}:
                return
            jobs.update(job_id, 'running', {'stage':stage,
                'requested_count':payload['count'], 'actual_count':max(0, min(payload['count'], int(value.get('actual_count',0))))})
        try:
            return await app.state.specialist_training.generate(SpecialistGenerate.model_validate(payload),
                budget_reserved=True, on_progress=progress)
        except HTTPException as exc:
            raise AIError(f'专项生成请求被拒绝（HTTP {exc.status_code}），请检查配置或已有训练任务；原题单保留') from None

    jobs.run_specialist_generate = run_specialist_generate

    @app.get('/api/training/candidates')
    def training_candidates(topic_id: str = Query(min_length=1, max_length=80),
                            cf_minimum: int | None = Query(default=None, ge=800, le=3500),
                            cf_maximum: int | None = Query(default=None, ge=800, le=3500)):
        # Cache-only: inspecting coverage never logs into a platform, fetches a
        # statement, changes a plan, or calls a model.
        return app.state.specialist_training.candidate_coverage(topic_id,
            cf_minimum=cf_minimum, cf_maximum=cf_maximum)

    @app.post('/api/training/catalog/refresh')
    async def training_catalog_refresh(payload: SpecialistCatalogRequest):
        from .training_topics import TOPICS
        from .catalog import verified_platforms
        if payload.topic_id not in TOPICS:
            raise HTTPException(422, '请选择支持的训练专题')
        if not verified_platforms(account_config):
            raise HTTPException(409, '请先在连接与设置中检测至少一个平台，确认本人的登录身份后再补抓专题题库')
        return jobs.start('specialist_catalog', {'topic_id': payload.topic_id})

    @app.post('/api/training/method-check')
    async def training_method_check(payload: TopicMethodRequest):
        from .training_topics import TOPICS
        from .catalog import verified_platforms
        if payload.topic_id not in TOPICS:
            raise HTTPException(422, '请选择支持的训练专题')
        if payload.cf_minimum is not None and payload.cf_maximum is not None and payload.cf_minimum > payload.cf_maximum:
            raise HTTPException(422, 'CF rating 下限不能大于上限')
        if not verified_platforms(account_config):
            raise HTTPException(409, '请先在连接与设置检测本人的平台登录，再核查专题方法')
        selection = ai.selection()
        if not selection['ready']:
            raise HTTPException(400, '请先在连接与设置中保存可用的默认 AI 模型；方法核查只使用这套配置')
        return jobs.start('topic_method_check', {**payload.model_dump(exclude_none=True),
            'provider': selection['channel'], 'model': selection['model']})

    async def run_topic_method_check(payload):
        selection = ai.selection()
        if (not selection['ready'] or selection['channel'] != payload['provider']
                or selection['model'] != payload['model']):
            raise AIError('默认 AI 配置已更改或不可用，请重新发起方法核查')
        return await app.state.topic_feasibility.check(payload['topic_id'], payload['provider'], payload['model'],
            cf_minimum=payload.get('cf_minimum'), cf_maximum=payload.get('cf_maximum'))

    jobs.run_topic_method_check = run_topic_method_check

    app.state.training_assessment = TrainingAssessment(
        database, ai, app.state.workbench, app.state.problem_submissions, app.state.specialist_training)
    register_training_assessment(app, app.state.training_assessment)

    from .problem_records import ProblemRecords, register as register_problem_records
    app.state.problem_records = ProblemRecords(database)
    register_problem_records(app, app.state.problem_records)

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

    if settings.enable_hermes and settings.translation_owner != 'assets':
        from acmhermes.service import register as register_hermes
        register_hermes(app)

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
