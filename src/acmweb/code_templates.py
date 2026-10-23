"""Private inert editor snippets. Existing workspace/session middleware owns ACLs."""
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

KEY = 'editor_code_templates'
LANGUAGES = {'cpp17', 'cpp20', 'python3', 'java17'}


def language_scope(value):
    return LANGUAGES if value == 'all' else {'cpp17', 'cpp20'} if value == 'cpp' else {value}


class CodeTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    title: str = Field(min_length=1, max_length=80)
    trigger: str = Field(min_length=1, max_length=40)
    language: Literal['all', 'cpp', 'cpp17', 'cpp20', 'python3', 'java17'] = 'all'
    body: str = Field(min_length=1, max_length=8192)

    @model_validator(mode='after')
    def check(self):
        if not self.title.strip() or not self.body.strip():
            raise ValueError('模板标题和正文不能为空')
        if not (self.trigger[0].isalpha() or self.trigger[0] == '_') or not all(c.isalnum() or c in '_-' for c in self.trigger):
            raise ValueError('触发词使用字母、中文、数字、下划线或连字符，以字母、中文或下划线开头')
        try:
            if len(self.body.encode('utf-8')) > 16384:
                raise ValueError('单个模板最多 16 KiB UTF-8')
        except UnicodeEncodeError:
            raise ValueError('模板包含无效字符') from None
        return self


class TemplateDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, le=2147483647, strict=True)
    items: list[CodeTemplate] = Field(default_factory=list, max_length=40)


class CodeTemplates:
    def __init__(self, db):
        self.db = db

    def get(self):
        return self.db.get(KEY, {'revision': 0, 'items': []})

    def save(self, data):
        ids, triggers = set(), {}
        for item in data.items:
            if item.id in ids:
                raise HTTPException(400, '模板 ID 重复，请重新新增模板')
            ids.add(item.id)
            for language in language_scope(item.language):
                key = (language, item.trigger)
                if key in triggers:
                    raise HTTPException(400, f'触发词「{item.trigger}」在同一语言内重复（包括“全部语言”和 C++ 通用范围）')
                triggers[key] = item.id
        document = data.model_dump()
        encoded = json.dumps(document, ensure_ascii=False)
        try:
            size = len(encoded.encode('utf-8'))
        except UnicodeEncodeError:
            raise HTTPException(400, '模板包含无效字符') from None
        if size > 131072:
            raise HTTPException(400, '全部模板合计最多 128 KiB UTF-8')
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT value FROM web_settings WHERE key=?', (KEY,)).fetchone()
            current = json.loads(row['value']) if row else {'revision': 0, 'items': []}
            if current['revision'] != data.revision:
                raise HTTPException(409, '模板已在其他窗口更新；你的编辑内容已保留，请先查看服务器版本再合并')
            document['revision'] += 1
            conn.execute('INSERT INTO web_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (KEY, json.dumps(document, ensure_ascii=False)))
        return document


def register(app, db):
    service = CodeTemplates(db)
    app.state.code_templates = service

    @app.get('/api/workbench/code-templates')
    def get_templates():
        return service.get()

    @app.put('/api/workbench/code-templates')
    def save_templates(payload: TemplateDocument):
        return service.save(payload)
