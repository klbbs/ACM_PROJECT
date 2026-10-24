"""Persistent, administrator-managed host additions for multi-user AI APIs."""
import ipaddress
import json
import re
import threading
from collections.abc import Sequence

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

KEY = 'ai.allowed_hosts.custom'
HOST_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')


def normalize_host(value):
    if not isinstance(value, str):
        raise ValueError('主机名格式无效')
    host = value.strip().rstrip('.').lower()
    if not host or len(host) > 253 or '*' in host or '/' in host or ':' in host:
        raise ValueError('请输入纯域名主机名，不含协议、端口、路径或通配符')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('不能将 IP 地址加入 AI API 白名单')
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError:
        raise ValueError('域名格式无效') from None
    labels = host.split('.')
    if len(labels) < 2 or any(not HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError('请输入完整的公开域名，例如 api.example.com')
    if host in {'localhost', 'metadata.google.internal'} or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('不能将本机或内部域名加入白名单')
    return host


class HostList(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hosts: list[str] = Field(max_length=100)

    @field_validator('hosts')
    @classmethod
    def validate_hosts(cls, values):
        result = []
        for value in values:
            host = normalize_host(value)
            if host not in result:
                result.append(host)
        return result


class _LiveHosts(Sequence):
    """Shared by Settings copies; publish one immutable snapshot after commit."""
    def __init__(self, base_hosts):
        self.base_hosts = tuple(dict.fromkeys(base_hosts))
        self.lock = threading.RLock()
        self.replace(())

    def replace(self, custom):
        custom = tuple(custom)
        self.snapshot = (tuple(dict.fromkeys((*self.base_hosts, *custom))), custom)

    def __len__(self):
        return len(self.snapshot[0])

    def __getitem__(self, index):
        return self.snapshot[0][index]

    def __iter__(self):
        return iter(self.snapshot[0])

    def __contains__(self, host):
        return host in self.snapshot[0]

    def __eq__(self, other):
        return self.snapshot[0] == other


class AIHostAllowlist:
    def __init__(self, identities, settings):
        self.identities = identities
        self.settings = settings
        self.view = (settings.ai_allowed_hosts if isinstance(settings.ai_allowed_hosts, _LiveHosts)
                     else _LiveHosts(settings.ai_allowed_hosts))
        self.base_hosts = self.view.base_hosts
        self.lock = self.view.lock
        with self.lock:
            with identities.connect() as db:
                row = db.execute('SELECT value FROM site_settings WHERE key=?', (KEY,)).fetchone()
            try:
                custom = HostList.model_validate({'hosts': json.loads(row['value']) if row else []}).hosts
            except (ValueError, TypeError):
                custom = []
            self.view.replace(host for host in custom if host not in self.base_hosts)
            self.settings.ai_allowed_hosts = self.view

    @property
    def allowed_hosts(self):
        return self.view.snapshot[0]

    @property
    def custom_hosts(self):
        return self.view.snapshot[1]

    def public(self):
        return {'default_hosts': list(self.base_hosts), 'custom_hosts': list(self.custom_hosts)}

    def update(self, hosts, actor_id=None, recheck=None):
        with self.lock:
            custom = [host for host in HostList(hosts=hosts).hosts if host not in self.base_hosts]
            with self.identities.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if recheck is not None:
                    recheck(db)
                if actor_id is not None:
                    actor = db.execute('SELECT role,active FROM users WHERE id=?', (actor_id,)).fetchone()
                    if not actor or not actor['active'] or actor['role'] != 'admin':
                        raise HTTPException(403, '需要管理员权限')
                db.execute('INSERT INTO site_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (KEY, json.dumps(custom, ensure_ascii=True)))
                if actor_id is not None:
                    self.identities.audit('ai.allowed_hosts.updated', actor_id, str(len(custom)), db)
            self.view.replace(custom)
            return self.public()


def register(app, allowlist, identities, workspaces, require_admin, recheck_admin):
    @app.get('/api/admin/ai-hosts')
    def get_hosts(request: Request):
        require_admin(request)
        return allowlist.public()

    @app.put('/api/admin/ai-hosts')
    def set_hosts(payload: HostList, request: Request):
        actor = require_admin(request)
        identities.limit('admin-ai-hosts:' + actor['id'], 20, 60)
        return allowlist.update(payload.hosts, actor_id=actor['id'],
                                recheck=lambda db: recheck_admin(db, actor, request))
