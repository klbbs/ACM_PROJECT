"""Uniform response hardening outside Starlette's server-error middleware.

The application remains a FastAPI instance: routes, state and lifespan APIs are
unchanged. Pure ASGI wrapping preserves streaming and also covers middleware
rejections and the generic response produced for an unhandled exception.
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders


SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'same-origin',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Content-Security-Policy': (
        "default-src 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


class SecurityHeaders:
    def __init__(self, app, *, production):
        self.app, self.production = app, production

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message['type'] == 'http.response.start':
                # Assignment collapses duplicates, including nested workspace
                # responses, instead of appending another copy of each policy.
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if scope.get('path', '').startswith('/api/'):
                    headers['Cache-Control'] = 'no-store'
                if self.production:
                    headers['Strict-Transport-Security'] = 'max-age=31536000'
            await send(message)
        await self.app(scope, receive, secured_send)


async def safe_server_error(request, exception):
    # Never interpolate the exception: it can contain private configuration,
    # source code or upstream payloads. Starlette still re-raises for server
    # logging; response streaming already started cannot be replaced safely.
    return JSONResponse({'detail': '服务器暂时无法完成请求，请稍后重试'}, status_code=500)


class SecureFastAPI(FastAPI):
    def __init__(self, *args, production=False, **kwargs):
        if production:
            kwargs['debug'] = False
        super().__init__(*args, **kwargs)
        self.security_production = production
        if production:
            self.add_exception_handler(Exception, safe_server_error)

    def build_middleware_stack(self):
        secured = SecurityHeaders(super().build_middleware_stack(), production=self.security_production)
        store = getattr(self.state, 'event_log', None)
        if store is not None:
            from .runtime_logs import RuntimeLogMiddleware
            return RuntimeLogMiddleware(secured, store)
        return secured
