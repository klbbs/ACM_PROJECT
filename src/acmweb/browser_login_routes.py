"""Authenticated, fixed-platform access to an ephemeral official login page."""
import base64
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .browser_login import BrowserLogin


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: Literal['luogu']
    expected_handle: str | None = Field(default=None, max_length=100,
                                       pattern=r'^[1-9][0-9]*$')


class Flow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    login_id: str = Field(min_length=20, max_length=100)


class Input(Flow):
    kind: Literal['click', 'key', 'text']
    x: int | None = Field(default=None, ge=0, lt=1000, strict=True)
    y: int | None = Field(default=None, ge=0, lt=720, strict=True)
    key: str | None = Field(default=None, max_length=30)
    text: SecretStr | None = Field(default=None, max_length=512, repr=False)


def register(app, accounts):
    manager = BrowserLogin(accounts, app.state.jobs, app.state.credential_checks)
    app.state.browser_login = manager

    def owner(request):
        value = request.scope.get('state', {}).get('browser_login_owner')
        if getattr(app.state, 'multi_child', False) and not value:
            raise HTTPException(401, '请重新登录 ACM Lab')
        return value or 'local-workspace'

    @app.post('/api/platform-login/browser/start')
    async def start(payload: Start, request: Request):
        return await manager.start(owner(request), payload.platform, payload.expected_handle)

    @app.post('/api/platform-login/browser/status')
    async def status(payload: Flow, request: Request):
        return await manager.status(owner(request), payload.login_id)

    @app.post('/api/platform-login/browser/image')
    async def image(payload: Flow, request: Request):
        png = await manager.screenshot(owner(request), payload.login_id)
        return {'image': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')}

    @app.post('/api/platform-login/browser/input')
    async def interact(payload: Input, request: Request):
        # Never return validation input, official HTML, or a password in an error.
        return await manager.interact(owner(request), payload.login_id, payload.kind,
                                      x=payload.x, y=payload.y, key=payload.key,
                                      text=payload.text.get_secret_value() if payload.text is not None else None)

    @app.post('/api/platform-login/browser/confirm')
    async def confirm(payload: Flow, request: Request):
        return await manager.confirm(owner(request), payload.login_id)

    @app.post('/api/platform-login/browser/cancel')
    async def cancel(payload: Flow, request: Request):
        return await manager.cancel(owner(request), payload.login_id)
