"""Bounded, structured operational logs. Never accept arbitrary log messages."""
import asyncio
import contextvars
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import uuid
import weakref

from fastapi import Query, Request
from typing import Literal

request_id_context = contextvars.ContextVar('acm_request_id', default=None)
user_id_context = contextvars.ContextVar('acm_log_user_id', default=None)
console = logging.getLogger('acmweb.runtime')

EVENTS = {
    'gym.sync_checked': ('platform', 'Gym 补充提交同步已检查'),
    'service.started': ('system', '服务已启动'),
    'service.stopped': ('system', '服务已停止'),
    'startup.checked': ('system', '启动配置巡检已完成'),
    'request.completed': ('request', '接口请求已完成'),
    'request.rejected': ('request', '接口请求未被接受'),
    'request.failed': ('request', '接口处理出现异常'),
    'request.cancelled': ('request', '接口请求已中断'),
    'security.action': ('security', '账号或权限操作已完成'),
    'security.denied': ('security', '账号或权限操作未被接受'),
    'job.queued': ('task', '后台任务已排队'),
    'job.completed': ('task', '后台任务已完成'),
    'job.partial': ('task', '后台任务部分完成'),
    'job.failed': ('task', '后台任务失败'),
    'job.interrupted': ('task', '后台任务已中断'),
    'daily.generated': ('task', '每日题单已保存'),
    'daily.failed': ('task', '每日题单生成失败，原有题单保留'),
    'training.selection_failed': ('task', '专项选题未完成，请检查候选筛选条件'),
    'training.selected': ('task', '专项题目已选定'),
    'selection_reports.failed': ('task', '选题阅读报告未能生成，原有题单保留'),
    'selection_reports.generated': ('task', '选题阅读报告已保存'),
    'platform.qr.retry': ('platform', '牛客扫码检测暂未完成，正在重试'),
    'platform.qr.failed': ('platform', '牛客扫码绑定未完成'),
    'platform.qr.verified': ('platform', '牛客扫码身份已验证并绑定'),
    'platform.qr.expired': ('platform', '牛客扫码流程已到期'),
}
LEVELS = ('INFO', 'WARNING', 'ERROR')
CATEGORIES = ('system', 'task', 'platform', 'security', 'request')
KINDS = {'crawl', 'catalog', 'analysis', 'recommend', 'ai_test', 'translation',
         'wb_statement', 'wb_sync', 'wb_hint', 'wb_review', 'wb_submission_review',
         'wb_remote_submit', 'wb_remote_refresh', 'wb_problem_submissions'}
REASONS = {'qr_creation_failed', 'account_mismatch', 'crawl_in_progress', 'binding_failed',
           'gym_access_restricted', 'gym_time_unverified', 'gym_login_or_permission',
           'gym_query_limit', 'gym_unreadable', 'gym_scope_deferred', 'gym_sync_incomplete',
           'identity_verified', 'qr_expired', 'identity_verification_timeout', 'identity_not_ready',
           'network_busy', 'upstream_timeout', 'upstream_connection_failed', 'upstream_http',
           'upstream_response_invalid', 'upstream_response_too_large', 'status_check_unavailable',
           'account_incomplete', 'invalid_ticket', 'upstream_code_invalid',
           'success_data_missing', 'success_data_invalid', 'upstream_status_rejected',
           'ai_model_missing', 'ai_key_missing', 'ai_host_not_allowed',
           'ai_timeout', 'ai_network', 'ai_invalid_json', 'ai_invalid_structure',
           'model_list_timeout', 'model_list_network', 'model_list_invalid_json', 'model_list_invalid_structure',
           'daily_invalid_output', 'daily_invalid_count', 'daily_candidates_unavailable',
           'daily_provider_unavailable', 'daily_provider_configuration', 'daily_interrupted',
           'daily_generation_failed', 'training_template_unavailable',
           'training_candidates_unavailable', 'training_selection_invalid',
           'selection_statement_unavailable', 'selection_report_invalid',
           'selection_report_rejected', 'selection_report_timeout', 'selection_report_failed'}


def known_failure_reason(error):
    # Exact, application-owned messages only. Never reflect an upstream body.
    from .ai import (AI_TIMEOUT_ERROR, AI_NETWORK_ERROR, AI_JSON_ERROR, AI_STRUCTURE_ERROR,
                     MODEL_TIMEOUT_ERROR, MODEL_NETWORK_ERROR, MODEL_JSON_ERROR, MODEL_STRUCTURE_ERROR)
    return {
        '请先保存默认模型 ID': 'ai_model_missing',
        '请先选择可用模型': 'ai_model_missing',
        '请先配置 API Key': 'ai_key_missing',
        '尚未为当前 API 地址配置 Key，请保存该提供方的密钥': 'ai_key_missing',
        '该 AI 提供方域名尚未加入服务器白名单，请联系管理员在管理后台添加': 'ai_host_not_allowed',
        AI_TIMEOUT_ERROR: 'ai_timeout', AI_NETWORK_ERROR: 'ai_network',
        AI_JSON_ERROR: 'ai_invalid_json', AI_STRUCTURE_ERROR: 'ai_invalid_structure',
        MODEL_TIMEOUT_ERROR: 'model_list_timeout', MODEL_NETWORK_ERROR: 'model_list_network',
        MODEL_JSON_ERROR: 'model_list_invalid_json', MODEL_STRUCTURE_ERROR: 'model_list_invalid_structure',
    }.get(str(error))


def record_failure(scope, error):
    context = scope.get('_acm_runtime_context')
    reason = known_failure_reason(error)
    if context is not None and reason:
        context['reason'] = reason


COMPONENTS = {'auth', 'admin', 'accounts', 'workbench', 'remote-submissions', 'problem-submissions',
              'platform-login', 'platforms', 'translations', 'daily', 'ai', 'catalog', 'social',
              'crawl', 'analysis', 'recommend', 'training', 'selection-reports', 'shared', 'other'}
HEX = re.compile(r'[a-f0-9]{32}')
_registry = weakref.WeakValueDictionary()
_registry_lock = threading.RLock()
_directory_locks = {}


def safe_details(details):
    """Drop unknown fields and untrusted text, rather than guessing how to redact them."""
    clean = {}
    for key, value in details.items():
        if key in {'job_id', 'request_id', 'user_id'}:
            if isinstance(value, str) and HEX.fullmatch(value):
                clean[key] = value
        elif key == 'kind' and isinstance(value, str) and value in KINDS:
            clean[key] = value
        elif key == 'reason' and isinstance(value, str) and value in REASONS:
            clean[key] = value
        elif key == 'component' and isinstance(value, str) and value in COMPONENTS:
            clean[key] = value
        elif key == 'job_status' and isinstance(value, str) and value in {'queued', 'completed', 'partial', 'failed', 'interrupted'}:
            clean[key] = value
        elif key == 'platform' and isinstance(value, str) and value in {'codeforces', 'atcoder', 'luogu', 'nowcoder'}:
            clean[key] = value
        elif key == 'mode' and isinstance(value, str) and value in {'single', 'multi'}:
            clean[key] = value
        elif key == 'train_mode' and isinstance(value, str) and value in {'learn', 'consolidate', 'variation', 'strengthen'}:
            clean[key] = value
        elif key == 'method' and isinstance(value, str) and value in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}:
            clean[key] = value
        elif key == 'route' and isinstance(value, str) and len(value) <= 200 and re.fullmatch(r'/[a-zA-Z0-9_/{:}.-]*', value):
            clean[key] = value
        elif key in {'status', 'upstream_status'} and type(value) is int and 100 <= value <= 599:
            clean[key] = value
        elif key == 'upstream_code' and type(value) is int and abs(value) <= 10**9:
            clean[key] = value
        elif key in {'requested_count', 'actual_count'} and type(value) is int and 0 <= value <= 1000:
            clean[key] = value
        elif key in {'template_count', 'candidate_count', 'eligible_count'} and type(value) is int and 0 <= value <= 100000:
            clean[key] = value
        elif key in {'duration_ms', 'warning_count', 'finding_count'} and type(value) is int and 0 <= value <= 86400000:
            clean[key] = value
    return clean


def emit_event(store, event, level='INFO', **details):
    if store is None:
        return
    try:
        store.emit(event, level=level, **details)
    except Exception:
        # Logging must never turn an otherwise successful operation into a failure.
        pass


class RuntimeLogs:
    def __init__(self, directory, max_bytes=1048576, backup_count=4):
        self.directory = Path(directory).absolute()
        self.max_bytes = max(1024, min(int(max_bytes), 20 * 1048576))
        self.backup_count = max(1, min(int(backup_count), 10))
        with _registry_lock:
            self.lock = _directory_locks.setdefault(str(self.directory), threading.RLock())
        self.last_error = None

    def _path(self, index=0):
        return self.directory / ('runtime.jsonl' + (f'.{index}' if index else ''))

    def _prepare(self):
        if self.directory.is_symlink():
            raise OSError('unsafe log directory')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        for index in range(self.backup_count + 1):
            if self._path(index).is_symlink():
                raise OSError('unsafe log file')
            if self._path(index).exists():
                self._path(index).chmod(0o600)

    def emit(self, event, level='INFO', **details):
        if event not in EVENTS or level not in LEVELS:
            return
        request_id = request_id_context.get()
        user_id = user_id_context.get()
        if request_id is not None:
            details['request_id'] = request_id
        if user_id is not None:
            details['user_id'] = user_id
        metadata = safe_details(details)
        category, message = EVENTS[event]
        record = {'id': uuid.uuid4().hex, 'timestamp': int(time.time()), 'level': level,
                  'category': category, 'event': event, 'message': message,
                  'request_id': metadata.pop('request_id', None), 'details': metadata}
        line = (json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
        if len(line) > self.max_bytes:
            record['details'] = {}
            line = (json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
        with self.lock:
            try:
                self._prepare()
                path = self._path()
                if path.exists() and path.stat().st_size + len(line) > self.max_bytes:
                    self._path(self.backup_count).unlink(missing_ok=True)
                    for i in range(self.backup_count - 1, 0, -1):
                        if self._path(i).exists():
                            self._path(i).replace(self._path(i + 1))
                    path.replace(self._path(1))
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                with os.fdopen(fd, 'ab') as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(line)
                self.last_error = None
            except OSError:
                if self.last_error is None:
                    console.error('运行日志暂时无法保存，请检查数据目录权限和磁盘空间')
                self.last_error = 'storage_unavailable'
        console.log(getattr(logging, level), '%s %s %s', event, message,
                    json.dumps(metadata, ensure_ascii=False, separators=(',', ':')))

    def listing(self, *, level=None, category=None, q='', before=None, limit=50):
        with self.lock:
            records, file_count, available = [], 0, self.last_error is None
            try:
                if self.directory.is_symlink():
                    raise OSError('unsafe log directory')
                # File order is newest first; each file is read in reverse line order.
                for i in range(self.backup_count + 1):
                    path = self._path(i)
                    if path.is_symlink():
                        raise OSError('unsafe log file')
                    if not path.exists():
                        continue
                    file_count += 1
                    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    with os.fdopen(fd, 'rb') as source:
                        offset = max(0, os.fstat(source.fileno()).st_size - self.max_bytes)
                        source.seek(offset)
                        if offset:
                            source.readline()
                        lines = source.read(self.max_bytes).splitlines()
                    for line in reversed(lines):
                        try:
                            value = json.loads(line)
                            event = value['event']
                            if (event not in EVENTS or value['level'] not in LEVELS or
                                    not HEX.fullmatch(value['id']) or type(value['timestamp']) is not int):
                                continue
                            # Reapply the allowlist when reading: never reflect arbitrary file contents.
                            value = {'id': value['id'], 'timestamp': value['timestamp'], 'level': value['level'],
                                     'category': EVENTS[event][0], 'event': event, 'message': EVENTS[event][1],
                                     'request_id': safe_details({'request_id': value.get('request_id')}).get('request_id'),
                                     'details': safe_details(value.get('details', {}))}
                            records.append(value)
                        except (ValueError, TypeError, KeyError, AttributeError):
                            continue
            except OSError:
                available = False
            expired = False
            if before:
                position = next((i for i, item in enumerate(records) if item['id'] == before), None)
                expired = position is None
                records = [] if expired else records[position + 1:]
            if level:
                records = [r for r in records if r['level'] == level]
            if category:
                records = [r for r in records if r['category'] == category]
            if q:
                records = [r for r in records if q.casefold() in json.dumps(r, ensure_ascii=False).casefold()]
            page = records[:limit]
            return {'items': page, 'has_more': len(records) > limit,
                    'next_cursor': page[-1]['id'] if len(records) > limit and page else None,
                    'cursor_expired': expired,
                    'storage': {'available': available, 'error': None if available else 'storage_unavailable',
                                'max_bytes': self.max_bytes, 'backup_count': self.backup_count, 'file_count': file_count}}


def get_event_log(directory):
    path = str(Path(directory).absolute())
    with _registry_lock:
        store = _registry.get(path)
        if store is None:
            store = RuntimeLogs(path)
            _registry[path] = store
        return store


def register_admin_logs(app, store, authorize=None):
    @app.get('/api/admin/logs')
    def logs(request: Request, level: Literal['INFO', 'WARNING', 'ERROR'] | None = None,
             category: Literal['system', 'task', 'platform', 'security', 'request'] | None = None,
             q: str = Query(default='', max_length=100),
             before: str | None = Query(default=None, pattern=r'^[a-f0-9]{32}$'),
             limit: int = Query(default=50, ge=1, le=100)):
        if authorize:
            authorize(request)
        return store.listing(level=level, category=category, q=q, before=before, limit=limit)


class RuntimeLogMiddleware:
    def __init__(self, app, store):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if scope.get('_acm_runtime_log'):
            try:
                return await self.app(scope, receive, send)
            finally:
                # dispatch copies the scope, so propagate only the matched
                # template through a shared context, never raw path parameters.
                context = scope.get('_acm_runtime_context')
                route = getattr(scope.get('route'), 'path', None)
                if context is not None and route:
                    context['route'] = route
        scope['_acm_runtime_log'] = True
        scope['_acm_runtime_context'] = {}
        request_id = uuid.uuid4().hex
        token = request_id_context.set(request_id)
        user_token = user_id_context.set(None)
        started = time.monotonic()
        status, failed, cancelled = 500, False, False

        async def observe(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'x-request-id']
                message = {**message, 'headers': headers + [(b'x-request-id', request_id.encode())]}
            await send(message)

        try:
            return await self.app(scope, receive, observe)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            failed = True
            raise
        finally:
            try:
                path = scope.get('path', '')
                duration = max(0, int((time.monotonic() - started) * 1000))
                if path.startswith('/api/') and not (path == '/api/admin/logs' and status < 400 and not failed and not cancelled) and (
                        failed or cancelled or status >= 400 or scope['method'] not in {'GET', 'HEAD', 'OPTIONS'} or duration >= 1500):
                    context = scope['_acm_runtime_context']
                    route = context.get('route') or getattr(scope.get('route'), 'path', '/unmatched')
                    component = path.split('/')[2] if len(path.split('/')) > 2 else 'other'
                    if component not in COMPONENTS:
                        component = 'other'
                    user = scope.get('state', {}).get('user') or {}
                    user_id_context.set(user.get('id'))
                    if cancelled:
                        event, level = 'request.cancelled', 'WARNING'
                    elif failed or status >= 500:
                        event, level = 'request.failed', 'ERROR'
                    elif component in {'auth', 'admin'}:
                        event, level = ('security.denied', 'WARNING') if status >= 400 else ('security.action', 'INFO')
                    else:
                        event, level = ('request.rejected', 'WARNING') if status >= 400 else ('request.completed', 'INFO')
                    emit_event(self.store, event, level=level, method=scope['method'], route=route,
                               component=component, status=status, duration_ms=duration,
                               reason=context.get('reason'))
            finally:
                request_id_context.reset(token)
                user_id_context.reset(user_token)


def configure_console():
    if not any(getattr(handler, '_acm_runtime', False) for handler in console.handlers):
        handler = logging.StreamHandler()
        handler._acm_runtime = True
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        console.addHandler(handler)
    console.setLevel(logging.INFO)
