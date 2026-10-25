"""Private read-before-selection reports, generated only during explicit selection.

The caller owns plan replacement. A batch is saved atomically only after every
chosen problem has a real official statement and a matching suitability report.
GET never invokes AI, executes code, submits a solution, or changes completion.
"""
import asyncio
import copy
import hashlib
import json
import re
import time
import uuid
from typing import Annotated

from bs4 import BeautifulSoup
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .codex_bridge import AIError
from .problem_sources import normalize, source_url
from .runtime_logs import emit_event

DDL = '''CREATE TABLE IF NOT EXISTS ai_selection_reports (
 id TEXT PRIMARY KEY, problem_id TEXT NOT NULL, created_at INTEGER NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, statement_hash TEXT NOT NULL,
 data TEXT NOT NULL, provenance TEXT NOT NULL
);'''
Text = Annotated[str, Field(min_length=1, max_length=2400)]
MAX_BATCH = 50
MAX_PREPARE_SECONDS = 540
MAX_STATEMENT_BYTES = 512 * 1024
STATEMENT_FIELDS = ('id', 'title', 'url', 'source', 'render_version', 'sections',
                    'body_html', 'samples', 'images', 'has_images', 'time_limit',
                    'memory_limit', 'language', 'constraints', 'input_format', 'output_format')


class SelectionReadReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    problem_id: str = Field(min_length=1, max_length=200)
    suitable: bool = Field(strict=True)
    selection_fit: Text
    problem_summary: Text
    constraints: list[Text] = Field(min_length=1, max_length=16)
    approach: list[Text] = Field(min_length=1, max_length=10)
    implementation_notes: list[Text] = Field(min_length=1, max_length=12)
    complexity: Text
    limitations: list[Text] = Field(min_length=1, max_length=12)


def _item_id(item):
    value = item.get('id') or item.get('problem_id')
    if not isinstance(value, str):
        raise AIError('待阅读题目缺少有效题号，本次题单未保存')
    # Some catalog rows carry a raw problem_id alongside their platform.
    if not item.get('id') and ':' not in value and item.get('platform'):
        value = str(item['platform']) + ':' + value
    try:
        pid = normalize(value)
        if item.get('id') and item.get('problem_id'):
            other = item['problem_id']
            if not isinstance(other, str):
                raise ValueError('invalid problem id')
            if ':' not in other and item.get('platform'):
                other = str(item['platform']) + ':' + other
            if normalize(other) != pid:
                raise ValueError('mismatched problem id')
        return pid
    except (AIError, AttributeError, TypeError, ValueError):
        raise AIError('待阅读题目的题号格式无效或关联不一致，本次题单未保存') from None


def _statement_material(statement, pid):
    if not isinstance(statement, dict) or statement.get('source') != 'official':
        raise AIError('尚未取得官方完整题面，不能生成已读报告或确认选题')
    try:
        if normalize(statement.get('id', '')) != pid:
            raise AIError('题面与候选题号不匹配，本次题单未保存')
    except (AttributeError, TypeError, ValueError):
        raise AIError('题面缺少有效题号，本次题单未保存') from None
    if type(statement.get('render_version')) is not int or statement['render_version'] < 2:
        raise AIError('题面缓存格式过旧，请重新获取官方完整题面')
    sections = statement.get('sections')
    body = statement.get('body_html')
    has_sections = isinstance(sections, list) and any(isinstance(s, str) and s.strip() for s in sections)
    has_body = isinstance(body, str) and bool(BeautifulSoup(body, 'html.parser').get_text(strip=True))
    if not has_sections and not has_body:
        raise AIError('官方题面正文为空，尚未发送到 AI，本次题单未保存')
    if not isinstance(statement.get('samples'), list):
        raise AIError('官方题面的样例结构不完整，本次题单未保存')
    for sample in statement['samples']:
        if not isinstance(sample, dict) or not all(isinstance(sample.get(k), str) for k in ('input', 'output')):
            raise AIError('官方题面样例解析不完整，本次题单未保存')
    # Only official statement fields are sent, never workbench.state(), whose
    # response also contains private drafts, attempts and unrelated AI history.
    material = {key: copy.deepcopy(statement[key]) for key in STATEMENT_FIELDS if key in statement}
    material['id'], material['url'] = pid, source_url(pid)
    try:
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    except (TypeError, ValueError):
        raise AIError('题面数据格式无效，本次题单未保存') from None
    if len(encoded) > MAX_STATEMENT_BYTES:
        raise AIError('官方完整题面超出单题阅读上限，不会截断后假称已读，请减少选题范围或换题')
    return material, hashlib.sha256(encoded).hexdigest()


def _failure(message, reason):
    error = AIError(message)
    error.selection_reason = reason
    return error


class SelectionReports:
    def __init__(self, database, ai, workbench, *, event_log=None):
        self.db, self.ai, self.wb = database, ai, workbench
        self.event_log = event_log
        self.guard = None
        self.runtime_guard = None
        self.active_ai = False
        with self.db.connect() as connection:
            connection.executescript(DDL)

    def _cached(self, pid, connection=None):
        if connection is None:
            with self.db.connect() as db:
                return self._cached(pid, db)
        row = connection.execute('SELECT data FROM wb_problems WHERE id=?', (pid,)).fetchone()
        if not row:
            raise AIError('尚未取得官方完整题面，本次题单未保存')
        try:
            statement = json.loads(row['data'])
        except (ValueError, TypeError):
            raise AIError('题面缓存不可读取，请重新获取官方题面') from None
        return _statement_material(statement, pid)

    async def _read(self, pid):
        try:
            return self._cached(pid)
        except AIError:
            # statement() stores the full statement and returns only its ID.
            # Never treat that acknowledgement or user-imported HTML as a body.
            try:
                await asyncio.to_thread(self.wb.statement, pid)
            except Exception:
                raise _failure(f'{pid} 官方完整题面获取失败，未将此题发送到 AI，本次题单未保存', 'selection_statement_unavailable') from None
            try:
                return self._cached(pid)
            except AIError as error:
                # These are our fixed validation messages, not upstream errors.
                # Preserve a clear oversize/incomplete explanation for the UI.
                error.selection_reason = 'selection_statement_unavailable'
                raise

    async def prepare(self, items, provider, model):
        if not isinstance(items, list) or not items or len(items) > MAX_BATCH:
            raise AIError(f'逐题阅读仅支持 1 至 {MAX_BATCH} 道题，本次题单未保存')
        if provider not in {'api', 'codex'} or not isinstance(model, str) or not model.strip() or len(model.strip()) > 150:
            raise AIError('请先选择有效的 AI 通道和模型')
        if self.active_ai:
            raise AIError('正在逐题阅读并确认选题，请等待当前任务完成')
        if any(not isinstance(item, dict) for item in items):
            raise AIError('待阅读题目格式无效，本次题单未保存')
        selected = copy.deepcopy(items)
        ids = [_item_id(item) for item in selected]
        if len(set(ids)) != len(ids):
            raise AIError('待阅读题单包含重复题目，本次题单未保存')
        if self.guard:
            self.guard()
        self.active_ai = True
        try:
            result = await asyncio.wait_for(self._prepare(selected, ids, provider, model.strip()), MAX_PREPARE_SECONDS)
            emit_event(self.event_log, 'selection_reports.generated', requested_count=len(ids), actual_count=len(result))
            return result
        except asyncio.TimeoutError:
            emit_event(self.event_log, 'selection_reports.failed', 'WARNING', reason='selection_report_timeout', requested_count=len(ids), actual_count=0)
            raise AIError('逐题阅读耗时超过安全时限，本次题单和报告均未保存，请减少题量或稍后重试') from None
        except AIError as error:
            emit_event(self.event_log, 'selection_reports.failed', 'WARNING', reason=getattr(error, 'selection_reason', 'selection_report_failed'), requested_count=len(ids), actual_count=0)
            raise
        except Exception:
            emit_event(self.event_log, 'selection_reports.failed', 'WARNING', reason='selection_report_failed', requested_count=len(ids), actual_count=0)
            raise AIError('逐题阅读或报告保存未完成，本次题单未保存，请重试') from None
        finally:
            self.active_ai = False

    async def _prepare(self, selected, ids, provider, model):
        prepared = []
        # Sequential single-statement calls bound concurrent transport use
        # and avoid pretending a truncated multi-problem prompt is complete.
        for item, pid in zip(selected, ids):
            if self.runtime_guard:
                self.runtime_guard()
            statement, fingerprint = await self._read(pid)
            training = {key: copy.deepcopy(item[key]) for key in (
                'reason', 'topic_id', 'topic_title', 'mode', 'mode_title', 'training_mode',
                'stage', 'learning_level', 'difficulty', 'tags', 'template_evidence') if key in item}
            try:
                response = await self.ai.complete('selection_read_report', provider, model.strip(),
                    {'problem_id': pid, 'selection_context': training, 'statement': statement,
                     'statement_hash': fingerprint,
                     'reading_limitations': ['只读已提供文字、数学公式和样例；图片地址不表示已理解图片内容；未运行代码。']},
                    SelectionReadReport)
                result = SelectionReadReport.model_validate(response.data).model_dump()
            except ValidationError:
                raise _failure(f'{pid} 的 AI 阅读报告格式无效，本次题单未保存，请重试', 'selection_report_invalid') from None
            except AIError:
                raise
            except Exception:
                raise AIError(f'{pid} 的 AI 阅读未完成，本次题单未保存，请重试') from None
            if result['problem_id'] != pid:
                raise _failure(f'{pid} 的 AI 阅读报告题号不匹配，本次题单未保存', 'selection_report_invalid')
            if not result['suitable']:
                raise _failure(f'{pid} 经完整题面阅读后未确认适合当前训练，本次题单未保存，请重新选题', 'selection_report_rejected')
            if re.search(r'\b(?:int|void)\s+main\s*\(|public\s+static\s+void\s+main\s*\(|\bfunc\s+main\s*\(|if\s+__name__\s*==', json.dumps(result)):
                raise _failure(f'{pid} 的 AI 阅读报告包含疑似完整可提交程序，未保存，请重试', 'selection_report_invalid')
            report_id = uuid.uuid4().hex
            provenance = {key: value for key, value in getattr(response, 'provenance', {}).items()
                          if key in {'channel', 'provider', 'vendor', 'protocol', 'model', 'task', 'prompt_version'}
                          and isinstance(value, str) and len(value) <= 200}
            prepared.append((report_id, pid, int(time.time()), provider, model.strip(), fingerprint,
                             json.dumps(result, ensure_ascii=False), json.dumps(provenance, ensure_ascii=False)))
            item['ai_report_id'] = report_id
        if self.runtime_guard:
            self.runtime_guard()
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for report in prepared:
                _, current_hash = self._cached(report[1], connection)
                if current_hash != report[5]:
                    raise AIError('阅读期间官方题面已变化，本次报告和题单未保存，请重试')
            connection.executemany('INSERT INTO ai_selection_reports VALUES(?,?,?,?,?,?,?,?)', prepared)
        return selected

    def get(self, report_id):
        if not isinstance(report_id, str) or not re.fullmatch(r'[0-9a-f]{32}', report_id):
            raise HTTPException(404, '选题阅读报告不存在')
        with self.db.connect() as connection:
            row = connection.execute('SELECT * FROM ai_selection_reports WHERE id=?', (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, '选题阅读报告不存在')
        value = dict(row)
        value['result'] = json.loads(value.pop('data'))
        value['provenance'] = json.loads(value['provenance'])
        return value


def register(app, reports):
    @app.get('/api/selection-reports/{report_id}')
    def get_report(report_id: str):
        # Per-user child app authentication, CSRF policy and routing are shared
        # with the workbench. Only this child's database is ever consulted.
        return reports.get(report_id)
