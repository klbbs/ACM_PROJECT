"""Self tests are delegated only to an administrator's dedicated Judge0 sandbox.

Never execute submitted programs on the web host. The configured runner must enforce
its own isolation/resource limits; requests cannot override URLs, limits or flags.
"""
import asyncio
import base64
import binascii
import os
import math
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BYTES = 65536
RUN_TIMEOUT = 30
_slots = threading.BoundedSemaphore(2)


class SelfTestInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    code: str = Field(min_length=1, max_length=MAX_BYTES)
    language: str = Field(pattern=r'^(cpp17|cpp20|python3|java17)$')
    stdin: str = Field(default='', max_length=MAX_BYTES)
    expected: str | None = Field(default=None, max_length=MAX_BYTES)
    consent: bool = False

    @field_validator('code', 'stdin', 'expected')
    @classmethod
    def bounded_text(cls, value):
        if value is not None and (len(value.encode('utf-8')) > MAX_BYTES or '\x00' in value):
            raise ValueError('内容最多 64 KiB，且不能包含 NUL 字符')
        return value


def _encode(value):
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


def _decode(value):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > 4 * MAX_BYTES:
        raise ValueError('runner output too large')
    try:
        # Ruby/Judge0 wraps Base64 with whitespace. Reject all other invalid bytes.
        raw = base64.b64decode(re.sub(r'[ \t\r\n]', '', value), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('invalid runner output') from exc
    # Judge0 applies max_file_size; keep an additional transport/display ceiling.
    return raw[:MAX_BYTES].decode('utf-8', errors='replace')


def normalized_output(value):
    return '\n'.join(line.rstrip() for line in value.replace('\r\n', '\n').split('\n')).rstrip('\n')


def _memory(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise HTTPException(502, '隔离服务返回内存统计格式错误')
    return value


class SelfTester:
    def __init__(self, endpoint=None, key=None):
        endpoint = os.getenv('ACM_SELFTEST_URL', '') if endpoint is None else endpoint
        self.endpoint = endpoint.rstrip('/')
        self.key = os.getenv('ACM_SELFTEST_KEY', '') if key is None else key
        self._languages = None
        self._slot = threading.BoundedSemaphore(1)
        self._languages_at = 0
        if self.endpoint:
            parsed = urlsplit(self.endpoint)
            if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise RuntimeError('ACM_SELFTEST_URL 必须是管理员配置的 Judge0 服务地址')

    @property
    def enabled(self):
        return bool(self.endpoint)

    def client(self):
        return httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False,
                                 headers={'X-Auth-Token': self.key} if self.key else {})

    async def request(self, client, method, path, **kwargs):
        try:
            return await asyncio.wait_for(self._request(client, method, path, **kwargs), timeout=10)
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError, TypeError, asyncio.TimeoutError) as exc:
            raise HTTPException(502, '隔离自测服务暂不可用；代码未在网站服务器执行') from exc

    async def _request(self, client, method, path, **kwargs):
        async with client.stream(method, self.endpoint + path, **kwargs) as response:
            if response.status_code >= 300:
                raise HTTPException(502, '隔离自测服务拒绝请求，请联系管理员检查配置')
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 4 * MAX_BYTES:
                    raise HTTPException(502, '隔离自测服务输出超出上限')
            import json
            return json.loads(body)

    async def capabilities(self):
        if not self.enabled:
            return {'enabled': False, 'languages': {}, 'message': '管理员尚未配置隔离自测服务。可下载代码后在本地运行，网站不会直接执行代码。'}
        if self._languages is None or time.monotonic() - self._languages_at > 300:
            async with self.client() as client:
                languages = await self.request(client, 'GET', '/languages')
            if not isinstance(languages, list):
                raise HTTPException(502, '隔离服务语言列表格式错误')
            found = {}
            patterns = {'cpp17': r'C\+\+.*(?:\b17\b|GCC 9\.)', 'cpp20': r'C\+\+.*\b20\b',
                        'python3': r'Python.*\b3\.', 'java17': r'Java.*\b17\b'}
            for lang in languages:
                if not isinstance(lang, dict) or type(lang.get('id')) is not int or lang['id'] <= 0:
                    continue
                name = str(lang.get('name', ''))
                for key, pattern in patterns.items():
                    if re.search(pattern, name, re.I):
                        found[key] = {'id': lang['id'], 'name': name[:120]}
            self._languages = found
            self._languages_at = time.monotonic()
        return {'enabled': True, 'languages': self._languages,
                'message': '代码与输入将发送到管理员配置的专用 Judge0 隔离服务；自测不代表平台判题。'}

    async def run(self, body):
        if not body.consent:
            raise HTTPException(400, '请先同意将本次代码与输入发送到隔离自测服务')
        if not self.enabled:
            raise HTTPException(503, '管理员尚未配置隔离自测服务；不会在网站服务器运行代码')
        if not body.code.strip():
            raise HTTPException(400, '请先输入代码')
        if not self._slot.acquire(blocking=False):
            raise HTTPException(429, '您已有自测正在运行，请稍后再试')
        if not _slots.acquire(blocking=False):
            self._slot.release()
            raise HTTPException(429, '隔离自测正在繁忙，请稍后再试')
        try:
            return await asyncio.wait_for(self._run(body), timeout=RUN_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, '隔离服务未及时完成；运行仍受服务端时间限制，请稍后重试') from exc
        finally:
            _slots.release()
            self._slot.release()

    async def _run(self, body):
        capabilities = await self.capabilities()
        language = capabilities['languages'].get(body.language)
        if not language:
            raise HTTPException(400, '隔离服务不支持当前语言版本，请切换到支持的版本')
        payload = {'source_code': _encode(body.code), 'language_id': language['id'],
                   'stdin': _encode(body.stdin), 'cpu_time_limit': 2, 'cpu_extra_time': 0.5,
                   'wall_time_limit': 5, 'memory_limit': 131072,
                   # Runtime output stays 64 KiB; compilation uses the runner's
                   # independent global MAX_MAX_FILE_SIZE (>= 20480 KiB).
                   'max_processes_and_or_threads': 32, 'max_file_size': 64,
                   'stack_limit': 65536, 'number_of_runs': 1,
                   'enable_per_process_and_thread_time_limit': False,
                   'enable_per_process_and_thread_memory_limit': False,
                   'enable_network': False, 'redirect_stderr_to_stdout': False}
        async with self.client() as client:
            result = await self.request(client, 'POST', '/submissions?base64_encoded=true&wait=false', json=payload)
            token = result.get('token') if isinstance(result, dict) else None
            if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', token):
                raise HTTPException(502, '隔离服务未返回有效任务号')
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                result = await self.request(client, 'GET', '/submissions/' + token + '?base64_encoded=true&fields=status,stdout,stderr,compile_output,time,memory,exit_code')
                if not isinstance(result, dict) or not isinstance(result.get('status'), dict):
                    raise HTTPException(502, '隔离服务返回结果格式错误')
                status = result['status'].get('id')
                if status not in (1, 2):
                    if type(status) is not int or status not in range(3, 15):
                        raise HTTPException(502, '隔离服务返回未知运行状态')
                    try:
                        stdout, stderr, compile_output = (_decode(result.get(name)) for name in ('stdout', 'stderr', 'compile_output'))
                    except ValueError as exc:
                        raise HTTPException(502, '隔离自测结果超出上限或编码无效') from exc
                    return {'status_id': status, 'status': str(result['status'].get('description', '完成'))[:100],
                            'stdout': stdout, 'stderr': stderr, 'compile_output': compile_output,
                            'time': str(result.get('time') or '')[:30], 'memory': _memory(result.get('memory')),
                            'matched': normalized_output(stdout) == normalized_output(body.expected) if body.expected is not None and status == 3 else None,
                            'truncated': any(len(v.encode('utf-8')) >= MAX_BYTES for v in (stdout, stderr, compile_output))}
                await asyncio.sleep(0.5)
            raise HTTPException(504, '隔离服务未及时完成；运行仍受服务端时间限制，请稍后重试')


def register_selftest(app):
    runner = SelfTester()
    app.state.selftest = runner

    @app.get('/api/workbench/self-test/capabilities')
    async def capabilities():
        return await runner.capabilities()

    @app.post('/api/workbench/self-test/run')
    async def run(body: SelfTestInput):
        return await runner.run(body)
