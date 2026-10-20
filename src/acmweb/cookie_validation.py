"""Validate a request Cookie value without retaining or echoing its contents."""
import re

# RFC token names; values may contain '=' (for example padded session tokens).
_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def valid_cookie(value):
    if not isinstance(value, str) or not value or not value.isascii():
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    value = value.strip()
    if not value or value.lower().startswith(('cookie:', 'set-cookie:')) or '...' in value:
        return False
    parts = value.split(';')
    if not parts[-1].strip():
        parts.pop()  # A single trailing semicolon is harmless.
    for part in parts:
        part = part.strip()
        if '=' not in part:
            return False
        name, _ = part.split('=', 1)
        # Preserve printable values exactly as browsers sent them. In practice
        # analytics cookies contain commas, spaces and quoted values; these do
        # not inject HTTP headers (all control characters were rejected above).
        if not _NAME.fullmatch(name):
            return False
    return bool(parts)


COOKIE_ERROR = 'Cookie 格式不完整或非法，请复制完整的请求 Cookie 字段值；不要使用省略号、Cookie: / Set-Cookie: 前缀或整段请求头。'
