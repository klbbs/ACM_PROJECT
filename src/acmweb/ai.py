import asyncio
import json
import os
import tempfile
import hashlib
import time
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .codex_bridge import AIError
from .providers import PRESETS
from .prompts import build_prompt, PROMPT_VERSION
from .runtime_logs import emit_event

AI_TIMEOUT_ERROR = 'AI 请求超时；未自动重试，请检查网络或调整超时设置'
AI_NETWORK_ERROR = 'AI 网络连接失败；未自动重试，请检查提供方地址和服务器网络'
AI_JSON_ERROR = 'AI 提供方返回的响应不是有效 JSON；未自动重试'
AI_STRUCTURE_ERROR = 'AI 提供方返回的 JSON 响应结构不符合协议；未自动重试'
MODEL_TIMEOUT_ERROR = '模型列表请求超时；请稍后重试或手动填写模型 ID'
MODEL_NETWORK_ERROR = '模型列表网络连接失败；请检查提供方地址和服务器网络'
MODEL_JSON_ERROR = '模型列表响应不是有效 JSON；请检查提供方 API 地址'
MODEL_STRUCTURE_ERROR = '模型列表 JSON 响应结构无效；请检查提供方协议或手动填写模型 ID'
AI_TRUNCATED_ERROR = ('AI 响应达到输出 token 上限而被截断，未保存；思考模型的预算可能同时包含思考与最终回答。'
                      '请在连接与设置中自行调整 token 上限或思考强度后重试；不会自动增加预算或重试')


def _ai_failure(message, reason):
    error = AIError(message)
    # Locally-owned codes only, shared by task logs and selection-report logs.
    error.reason = reason
    error.selection_reason = reason
    return error


@dataclass(frozen=True)
class AIResult:
    data: dict
    provenance: dict


class ConnectionResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ok: bool


class Weakness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    evidence: str
    action: str


class PlanDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str
    focus: str
    task: str


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reason: str


class CoachReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    summary: str
    confidence: str
    strengths: list[str]
    weaknesses: list[Weakness]
    plan: list[PlanDay]
    selected_problems: list[Selection]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(default="https://api.openai.com/v1",max_length=2048)
    protocol: Literal["responses","chat","anthropic"] = "responses"
    model: str = Field(default="",max_length=150)
    # The single active configuration every feature uses. It is chosen once in
    # settings, so no feature screen may override the channel or the model.
    channel: Literal['api','codex'] = 'api'
    codex_model: str = Field(default="",max_length=150)
    vendor: Literal['custom','openai','deepseek','anthropic','gemini','qwen','qwen_global','moonshot','siliconflow','glm'] = 'custom'
    output_mode: Literal['auto','schema','json','prompt'] = 'auto'
    thinking: Literal['auto','enabled','disabled'] = 'auto'
    reasoning_effort: Literal['auto','low','medium','high','max'] = 'auto'
    max_output_tokens: int = Field(default=8192,ge=256,le=131072)
    timeout: int = Field(default=180,ge=30,le=240)
    temperature: float | None = Field(default=None,ge=0,le=2)
    api_key: str | None = Field(default=None,repr=False,max_length=1000)

    @model_validator(mode='after')
    def output_budget_for_provider(self):
        if self.max_output_tokens > 32768 and self.vendor != 'deepseek':
            raise ValueError('当前提供方的输出 token 上限为 32768；DeepSeek 可自行设置至 131072')
        return self


class AIProvider:
    def __init__(self, database, settings, codex):
        self.db,self.settings,self.codex=database,settings,codex
        self.secret_file=settings.data_dir/"ai-secrets.json"
        self.config_lock=threading.Lock()
        self._request_config = ContextVar('acm_ai_config', default=None)
        self._request_key = ContextVar('acm_ai_key', default=None)
        self._request_metrics = ContextVar('acm_ai_metrics', default=None)
        self.event_log = None

    @staticmethod
    def scope(config):
        return hashlib.sha256(config['base_url'].rstrip('/').encode()).hexdigest()

    def configuration_fingerprint(self):
        """Request-local change detector; never persist or log this digest."""
        with self.config_lock:
            config = self.config()
            key = self.key(config)
        return hashlib.sha256(json.dumps({'config': config, 'key': key}, sort_keys=True).encode()).hexdigest()

    def key(self, config=None):
        config = config or self.config()
        if config is self._request_config.get() and self._request_key.get() is not None:
            return self._request_key.get()
        fallback = os.getenv('OPENAI_API_KEY','') if self.settings.allow_env_ai_key and config['base_url']=='https://api.openai.com/v1' else ''
        if self.secret_file.exists():
            saved = json.loads(self.secret_file.read_text())
            scope = self.scope(config)
            if scope in saved.get('keys', {}): return saved['keys'][scope]
            if saved.get('api_key') and saved.get('legacy_scope',scope)==scope: return saved['api_key']
        return fallback

    def config(self):
        raw=self.db.get('ai',{})
        config=ProviderConfig.model_validate(raw).model_dump(exclude={'api_key'})
        if 'vendor' not in raw:
            config['vendor']=next((p['id'] for p in PRESETS if p['id']!='custom' and p['base_url']==config['base_url'].rstrip('/') and p['protocol']==config['protocol']),'custom')
        self.authorize_host(config)
        return config

    def authorize_host(self, config):
        # Config/credentials are captured together for each request, while host
        # permission stays live so a queued request cannot bypass revocation.
        if self.settings.isolated and urlsplit(config['base_url']).hostname not in self.settings.ai_allowed_hosts:
            raise AIError('该 AI 提供方域名尚未加入服务器白名单，请联系管理员在管理后台添加')

    def presets(self):
        return {'items':[{**p,'allowed':not self.settings.isolated or urlsplit(p['base_url']).hostname in self.settings.ai_allowed_hosts} for p in PRESETS]}

    def active_model(self, config=None):
        """The saved model for the active channel; features never pick one."""
        config = config or self.config()
        field = 'codex_model' if config.get('channel') == 'codex' else 'model'
        return str(config.get(field) or '').strip()

    def selection(self, config=None):
        """Resolve the one AI configuration chosen in settings for every feature."""
        config = config or self.config()
        channel = 'codex' if config.get('channel') == 'codex' else 'api'
        model = self.active_model(config)
        codex_enabled = bool(self.settings.enable_codex)
        has_key = bool(self.key(config))
        return {'channel': channel, 'model': model,
                'api_model': str(config.get('model') or '').strip(),
                'codex_model': str(config.get('codex_model') or '').strip(),
                'has_key': has_key, 'codex_enabled': codex_enabled,
                'ready': bool(model) and (codex_enabled if channel == 'codex' else has_key)}

    def public_config(self):
        config = self.config()
        state = self.selection(config)
        return {**config, 'has_key': state['has_key'], 'codex_enabled': state['codex_enabled'],
                'codex_device_only': self.settings.production or self.settings.isolated,
                'active_model': state['model'], 'ready': state['ready']}

    @staticmethod
    def validate_url(url):
        parsed=urlsplit(url)
        if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AIError("AI API 地址必须为不含账号、查询参数的 HTTPS 地址")
        import ipaddress
        if parsed.hostname in {"localhost","metadata.google.internal"}:
            raise AIError("不能使用本机或云元数据 API 地址")
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                raise AIError("不能使用内网 AI API 地址")
        except ValueError:
            pass
        return url.rstrip("/")

    def save(self, config):
        with self.config_lock:
            return self._save(config)

    def _save(self, config):
        data=config.model_dump(exclude={"api_key"})
        data["base_url"]=self.validate_url(data["base_url"])
        self.authorize_host(data)
        if data['thinking']!='auto' and not (data['protocol']=='chat' and data['vendor'] in {'deepseek','qwen','qwen_global'}):
            raise AIError('思考开关仅适用于 DeepSeek / Qwen 的 Chat 协议；其他协议请使用模型默认')
        if data['reasoning_effort']!='auto' and not (data['vendor'] in {'openai','deepseek','moonshot','gemini'} and data['protocol'] in {'responses','chat'}):
            raise AIError('当前提供方/协议尚未适配推理强度，请选择 auto')
        if data['protocol']=='anthropic' and data['output_mode']=='json':
            raise AIError('Claude 原生接口请选择提示约束或 JSON Schema 模式')
        if data['channel']=='codex' and not self.settings.enable_codex:
            raise AIError('官方 Codex 通道未启用，请在设置中改用 API 通道')
        previous = ProviderConfig.model_validate(self.db.get('ai',{})).model_dump(exclude={'api_key'})
        saved = json.loads(self.secret_file.read_text()) if self.secret_file.exists() else {}
        if 'api_key' in saved:
            legacy_scope = saved.pop('legacy_scope',self.scope(previous))
            saved.setdefault('keys',{}).setdefault(legacy_scope,saved.pop('api_key'))
        if config.api_key is not None:
            saved.setdefault('keys',{})[self.scope(data)] = config.api_key.strip()
        if saved:
            fd,tmp=tempfile.mkstemp(dir=self.settings.data_dir,prefix=".ai-secret-")
            try:
                with os.fdopen(fd,"w") as f:json.dump(saved,f)
                os.chmod(tmp,0o600);os.replace(tmp,self.secret_file)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
        self.db.set("ai",data)
        return self.public_config()

    async def models(self, provider):
        if provider == "codex":
            if self.codex is None or not getattr(self.codex, 'enabled', True):
                raise AIError('官方 Codex 通道当前不可用')
            return {"items": await self.codex.models(), "note": "官方客户端目录可能包含缓存；实际调用成功才代表模型可用"}
        if provider != 'api':
            raise AIError('不支持的 AI 通道')
        # Capture endpoint and credential together, including across pagination.
        with self.config_lock:
            config = self.config()
            key = self.key(config)
        if not key:
            raise AIError("请先配置 API Key")
        gemini = config.get('vendor') == 'gemini'
        if gemini:
            if urlsplit(config['base_url']).hostname != 'generativelanguage.googleapis.com':
                raise AIError('Gemini 模型目录只允许访问 Google 官方 API 地址')
            url = 'https://generativelanguage.googleapis.com/v1beta/models'
            headers = {'x-goog-api-key': key}
        else:
            url = self.validate_url(config['base_url']) + '/models'
            headers = ({'x-api-key': key, 'anthropic-version': '2023-06-01'}
                       if config['protocol'] == 'anthropic' else {'Authorization': 'Bearer ' + key})
        try:
            items, cursor, seen, truncated = [], None, set(), False
            async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
                for page in range(10):
                    params = ({'pageToken' if gemini else 'after_id': cursor} if cursor else {})
                    self.authorize_host(config)
                    response = await client.get(url, headers=headers, **({'params': params} if params else {}))
                    if response.status_code != 200:
                        if page == 0 and response.status_code in {404, 405, 501} and config.get('model'):
                            return {'items': [{'id': config['model'], 'name': config['model']}], 'source': 'manual',
                                    'default_model': config['model'], 'note': '提供方未实现模型列表；这里只显示手动填写的模型，尚未验证生成权限'}
                        raise AIError(f"模型列表请求失败，HTTP {response.status_code}；可手动配置模型 ID")
                    body = response.json()
                    if not isinstance(body, dict): raise ValueError('invalid model list')
                    batch = body.get('models' if gemini else 'data', [])
                    if not isinstance(batch, list) or any(not isinstance(m, dict) for m in batch):
                        raise ValueError('invalid model entries')
                    if gemini:
                        batch = [{'id': m['name'].removeprefix('models/'),
                                  'display_name': m.get('displayName', m['name'])}
                                 for m in batch if isinstance(m.get('name'), str)
                                 and isinstance(m.get('supportedGenerationMethods'), list)
                                 and 'generateContent' in m['supportedGenerationMethods']]
                    for item in batch:
                        mid = item.get('id', item.get('slug'))
                        if not isinstance(mid, str) or not mid.strip() or len(mid) > 150 or any(ord(c) < 32 for c in mid):
                            continue
                        if mid in seen: continue
                        seen.add(mid)
                        name = item.get('display_name', mid)
                        items.append({'id': mid, 'name': name if isinstance(name, str) else mid})
                    cursor = (body.get('nextPageToken') if gemini else
                              body.get('last_id') if config['protocol'] == 'anthropic' and body.get('has_more') else None)
                    if not cursor: break
                    if not isinstance(cursor, str) or len(cursor) > 2048: raise ValueError('invalid model cursor')
                    if page == 9: truncated = True
                if config['model'] and config['model'] not in seen:
                    items.append({'id': config['model'], 'name': config['model'] + '（手动配置，未单独验证）'})
                note = '来自当前提供方的实时模型列表；列表权限不代表生成权限'
                if truncated: note += '；目录过大，仅展示前 10 页，可手动填写其他模型 ID'
                return {'items': items, 'note': note, 'source': 'remote', 'default_model': config['model']}
        except httpx.TimeoutException:
            raise AIError(MODEL_TIMEOUT_ERROR) from None
        except httpx.HTTPError:
            raise AIError(MODEL_NETWORK_ERROR) from None
        except json.JSONDecodeError:
            raise AIError(MODEL_JSON_ERROR) from None
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise AIError(MODEL_STRUCTURE_ERROR) from None

    async def complete(self, task, provider, model, payload: dict, schema: type[BaseModel]) -> AIResult:
        """Validate one task and invoke one transport with a captured server configuration."""
        if provider not in {'api', 'codex'}:
            raise AIError('不支持的 AI 通道')
        if not isinstance(model, str) or not model.strip() or len(model) > 150:
            raise AIError('请先选择可用模型')
        # Model IDs are part of provenance and shared-translation deduplication;
        # collapse accidental surrounding whitespace before invoking a provider.
        model = model.strip()
        if not isinstance(payload, dict):
            raise AIError('AI 任务材料必须为 JSON 对象')
        try:
            prompt = build_prompt(task, payload)
        except (ValueError, TypeError):
            raise AIError('不支持的 AI 任务或任务材料格式错误') from None
        if provider == 'api':
            with self.config_lock:
                config = self.config()
                key = self.key(config)
            provenance = {'channel': provider, 'provider': provider, 'endpoint': config['base_url'],
                          'vendor': config['vendor'], 'protocol': config['protocol'], 'model': model}
        else:
            config, key = None, None
            provenance = {'channel': provider, 'provider': provider, 'endpoint': 'codex://official',
                          'vendor': 'openai', 'protocol': 'codex', 'model': model}
        provenance.update(task=task, prompt_version=PROMPT_VERSION)
        config_token = self._request_config.set(config)
        key_token = self._request_key.set(key)
        metrics = {'ai_task': task, 'ai_vendor': provenance['vendor'], 'ai_protocol': provenance['protocol']}
        if config is not None:
            metrics['max_output_tokens'] = config['max_output_tokens']
        metrics_token = self._request_metrics.set(metrics)
        started = time.monotonic()
        try:
            if provider == 'codex':
                if self.codex is None or not getattr(self.codex, 'enabled', True):
                    raise AIError('官方 Codex 通道当前不可用，请先完成登录或改用 API 通道')
                text = await self.codex.generate(prompt, model, schema.model_json_schema())
            else:
                # Keep this three-argument seam for protocol adapters and offline test transports.
                text = await self._api_generate(model, prompt, schema.model_json_schema())
            try:
                clean = text.strip()
                if clean.startswith('```'):
                    clean = clean.split('\n', 1)[1].rsplit('```', 1)[0]
                data = schema.model_validate_json(clean).model_dump()
            except (ValueError, TypeError, AttributeError, IndexError):
                raise _ai_failure('AI 输出格式不符合任务约束，请检查 JSON 输出模式或换用支持 JSON 输出的模型后重试',
                                  'ai_invalid_output') from None
            emit_event(self.event_log, 'ai.completed', duration_ms=round((time.monotonic()-started)*1000), **metrics)
            return AIResult(data=data, provenance=provenance)
        except BaseException as error:
            reason = (getattr(error, 'reason', None) if isinstance(error, AIError) else None)
            if reason is None:
                reason = 'ai_cancelled' if isinstance(error, asyncio.CancelledError) else 'ai_task_failed'
            if isinstance(error, AIError):
                error.reason = reason
                error.selection_reason = reason
                error.ai_task = task
            emit_event(self.event_log, 'ai.failed', 'WARNING', reason=reason,
                       duration_ms=round((time.monotonic()-started)*1000), **metrics)
            raise
        finally:
            self._request_metrics.reset(metrics_token)
            self._request_key.reset(key_token)
            self._request_config.reset(config_token)

    def _capture_response_metrics(self, body):
        """Copy only bounded numeric usage; never retain upstream strings or content."""
        metrics = self._request_metrics.get()
        if metrics is None or not isinstance(body, dict):
            return
        usage = body.get('usage')
        if not isinstance(usage, dict):
            return
        for destination, sources in {
            'input_tokens': ('input_tokens', 'prompt_tokens'),
            'output_tokens': ('output_tokens', 'completion_tokens'),
        }.items():
            for source in sources:
                value = usage.get(source)
                if type(value) is int and 0 <= value <= 10**9:
                    metrics[destination] = value
                    break
        for field in ('output_tokens_details', 'completion_tokens_details'):
            details = usage.get(field)
            if isinstance(details, dict):
                value = details.get('reasoning_tokens')
                if type(value) is int and 0 <= value <= 10**9:
                    metrics['reasoning_tokens'] = value
                    break

    async def generate(self, provider, model, context, candidates=None, goal="", task='analysis'):
        if not model or len(model)>150:
            raise AIError("请先选择可用模型")
        candidates=candidates or []
        # Explicit allow-list: no cookies, raw profiles, sessions or source code.
        safe_context={k:context[k] for k in ("stats","topics","verdicts","coverage","days","suggested_cf_difficulty")}
        safe_context["recent"]=[{**{k:r[k] for k in ("platform","problem_id","submitted_at","verdict")},
            "problem":{k:(r.get("problem") or {}).get(k) for k in ("title","tags","difficulty")}} for r in context["recent"]]
        safe_context["workbench_evidence"]=context.get("workbench_evidence",{})
        safe_context["candidate_constraints"]=context.get("candidate_constraints",{})
        safe_context['actual_time_evidence'] = context.get('actual_time_evidence', [])[:100]
        safe_context["training_preferences"]=self.db.get("training",{})
        if task not in {'analysis', 'recommend'}:
            raise AIError('训练报告仅支持分析或推荐任务')
        completed = await self.complete(task, provider, model,
            {'statistics': safe_context, 'candidates': candidates, 'goal': goal[:1000]}, CoachReport)
        report=CoachReport.model_validate(completed.data)
        allowed={p["id"]:p for p in candidates}
        seen=set()
        selected=[]
        for row in report.selected_problems:
            if row.id not in allowed:
                raise AIError("AI 选择了候选列表之外的题目，结果未保存")
            if row.id not in seen:
                seen.add(row.id);selected.append({**allowed[row.id],"reason":row.reason})
        result=report.model_dump()
        result["selected_problems"]=selected[:6]
        return result

    def headers(self, config):
        if config['protocol']=='anthropic':
            return {'x-api-key':self.key(config),'anthropic-version':'2023-06-01'}
        return {'Authorization':'Bearer '+self.key(config)}

    async def test_connection(self):
        config=self.config()
        if not config['model']: raise AIError('请先保存默认模型 ID')
        started=time.monotonic()
        result=await self.complete('connection_test', 'api', config['model'], {}, ConnectionResult)
        if result.data['ok'] is not True: raise AIError('连接成功，但模型未通过 JSON 输出测试')
        return {'ok':True,'model':result.provenance['model'],'vendor':result.provenance['vendor'],'latency_ms':round((time.monotonic()-started)*1000),'note':'真实生成与 JSON 解析成功；不代表所有模型参数或训练任务都可用'}

    async def _api_generate(self, model, prompt, schema):
        config=self._request_config.get() or self.config()
        if not self.key(config):raise _ai_failure('尚未为当前 API 地址配置 Key，请保存该提供方的密钥', 'ai_key_missing')
        instruction='Return only a JSON object matching this schema: '+json.dumps(schema)
        mode=config['output_mode']
        if mode=='auto':mode='schema' if config['protocol']=='responses' else 'prompt' if config['protocol']=='anthropic' else 'json'
        cap=config['max_output_tokens']
        if config['protocol']=='responses':
            path='/responses'
            payload={'model':model,'input':instruction+'\n'+prompt,'store':False,'max_output_tokens':cap}
            if mode!='prompt':payload['text']={'format':{'type':'json_schema','name':'algorithm_coach','strict':True,'schema':schema} if mode=='schema' else {'type':'json_object'}}
            if config['reasoning_effort']!='auto':payload['reasoning']={'effort':config['reasoning_effort']}
        elif config['protocol']=='anthropic':
            path='/messages'
            payload={'model':model,'max_tokens':cap,'system':instruction,'messages':[{'role':'user','content':prompt}]}
            if mode=='schema':payload['output_config']={'format':{'type':'json_schema','schema':schema}}
            elif mode=='json':raise AIError('Claude 原生接口请选择提示约束或 JSON Schema 模式')
        else:
            path='/chat/completions'
            payload={'model':model,'messages':[{'role':'system','content':instruction},{'role':'user','content':prompt}]}
            payload['max_completion_tokens' if config['vendor']=='openai' else 'max_tokens']=cap
            if mode!='prompt':payload['response_format']={'type':'json_schema','json_schema':{'name':'algorithm_coach','strict':True,'schema':schema}} if mode=='schema' else {'type':'json_object'}
            if config['vendor']=='deepseek' and config['thinking']!='auto':payload['thinking']={'type':config['thinking']}
            if config['vendor'] in {'qwen','qwen_global'} and config['thinking']!='auto':payload['enable_thinking']=config['thinking']=='enabled'
            if config['vendor'] in {'openai','deepseek','moonshot','gemini'} and config['reasoning_effort']!='auto':payload['reasoning_effort']=config['reasoning_effort']
        if config['temperature'] is not None:payload['temperature']=config['temperature']
        metrics = self._request_metrics.get()
        if metrics is None:
            metrics = {}
        try:
            async with httpx.AsyncClient(timeout=config['timeout'],follow_redirects=False) as client:
                self.authorize_host(config)
                response=await client.post(self.validate_url(config['base_url'])+path,json=payload,headers=self.headers(config))
            if type(response.status_code) is int and 100 <= response.status_code <= 599:
                metrics['upstream_status'] = response.status_code
            if response.status_code!=200:
                reasons={401:'密钥无效或区域不匹配',403:'账号或模型权限不足',404:'地址或模型不存在',429:'额度不足或触发限流',400:'模型不支持当前参数，请检查输出模式、思考设置和 token 上限'}
                raise _ai_failure(f"AI 请求失败，HTTP {response.status_code}：{reasons.get(response.status_code,'服务暂不可用')}（未自动重试）", 'ai_upstream_http')
            body=response.json()
            if not isinstance(body,dict):raise ValueError('invalid response')
            self._capture_response_metrics(body)
            if path=='/responses':
                if body.get('status') not in {None,'completed'}:
                    metrics['finish_reason'] = 'incomplete'
                    details = body.get('incomplete_details')
                    if isinstance(details, dict) and details.get('reason') == 'max_output_tokens':
                        metrics['finish_reason'] = 'length'
                        raise _ai_failure(AI_TRUNCATED_ERROR, 'ai_output_truncated')
                    raise _ai_failure('AI 响应未完成，未保存报告；请检查提供方状态后手动重试', 'ai_response_incomplete')
                metrics['finish_reason'] = 'stop'
                text=''.join(c.get('text','') for item in body.get('output',[]) for c in item.get('content',[]) if c.get('type')=='output_text')
            elif path=='/messages':
                if body.get('stop_reason')!='end_turn':
                    if body.get('stop_reason') == 'max_tokens':
                        metrics['finish_reason'] = 'length'
                        raise _ai_failure(AI_TRUNCATED_ERROR, 'ai_output_truncated')
                    metrics['finish_reason'] = 'incomplete'
                    raise _ai_failure('Claude 响应未正常结束，未保存报告；请检查输出设置后手动重试', 'ai_response_incomplete')
                metrics['finish_reason'] = 'stop'
                text=''.join(c.get('text','') for c in body.get('content',[]) if c.get('type')=='text')
            else:
                choice=body['choices'][0]
                finish = choice.get('finish_reason')
                metrics['finish_reason'] = finish if finish in {'stop', 'length', 'content_filter', 'tool_calls'} else 'unknown'
                if finish == 'length':
                    raise _ai_failure(AI_TRUNCATED_ERROR, 'ai_output_truncated')
                if finish == 'content_filter':
                    raise _ai_failure('AI 提供方拒绝生成此次内容，未保存报告；请检查题面材料或联系提供方', 'ai_response_refused')
                if finish not in {None,'stop'}:
                    raise _ai_failure('AI 未返回正常结束的最终回答，未保存报告；当前任务需要直接返回 JSON，不支持工具调用', 'ai_response_incomplete')
                text=choice['message'].get('content')
                if isinstance(text,list):text=''.join(c.get('text','') for c in text if c.get('type')=='text')
            if not isinstance(text,str) or not text.strip():
                raise _ai_failure('模型没有返回最终文本，未保存报告（思考内容不会作为答案）；请检查输出 token 上限、思考设置和模型输出模式', 'ai_empty_final')
            clean=text.strip()
            if clean.startswith('```'):clean=clean.split('\n',1)[1].rsplit('```',1)[0]
            parsed=json.loads(clean)
            if not isinstance(parsed,dict):raise _ai_failure('AI 输出必须为 JSON 对象；请检查 JSON 输出模式后重试', 'ai_invalid_output')
            return json.dumps(parsed,ensure_ascii=False)
        except httpx.TimeoutException:
            raise _ai_failure(AI_TIMEOUT_ERROR, 'ai_timeout') from None
        except httpx.HTTPError:
            raise _ai_failure(AI_NETWORK_ERROR, 'ai_network') from None
        except json.JSONDecodeError:
            raise _ai_failure(AI_JSON_ERROR, 'ai_invalid_json') from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise _ai_failure(AI_STRUCTURE_ERROR, 'ai_invalid_structure') from None
