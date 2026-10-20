"""Validate a request Cookie value without retaining or echoing its contents."""
import re

# RFC token names; values may contain '=' (for example padded session tokens).
_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def cookie_problem(value):
    """Describe syntax errors using fixed text only; never include a secret."""
    if not isinstance(value, str) or not value:
        return {'code': 'empty', 'message': '没有填写 Cookie。'}
    if len(value) > 16384:
        return {'code': 'too_long', 'message': 'Cookie 超过 16 KiB，请只复制请求中的 Cookie 字段。'}
    if not value.isascii():
        return {'code': 'non_ascii', 'message': '包含非 ASCII 字符，可能复制了中文标点或省略符号，请重新复制请求 Cookie。'}
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return {'code': 'control_character', 'message': '包含换行、制表符或控制字符；不要粘贴整段请求头或多行列表。'}
    value = value.strip()
    if not value:
        return {'code': 'empty', 'message': '没有填写 Cookie。'}
    if value.lower().startswith('cookie:'):
        return {'code': 'cookie_prefix', 'message': '保存值包含 Cookie: 前缀；请在编辑框粘贴新 Cookie 后保存，系统会整理单行前缀。'}
    if value.lower().startswith('set-cookie:'):
        return {'code': 'response_header', 'message': '复制的是响应 Set-Cookie，请改用请求标头中的 Cookie。'}
    parts = value.split(';')
    if not parts[-1].strip():
        parts.pop()  # A single trailing semicolon is harmless.
    for index, part in enumerate(parts, 1):
        part = part.strip()
        if '=' not in part:
            return {'code': 'missing_equals', 'message': f'第 {index} 个字段缺少等号；需要 name=value，用英文分号分隔。'}
        name, _ = part.split('=', 1)
        # Preserve printable values exactly as browsers sent them. In practice
        # analytics cookies contain commas, spaces and quoted values; these do
        # not inject HTTP headers (all control characters were rejected above).
        if not _NAME.fullmatch(name):
            return {'code': 'invalid_name', 'message': f'第 {index} 个字段名称格式无效；不要复制 JSON、curl 或完整请求头。'}
    return None


def valid_cookie(value):
    # Dots, including consecutive dots, are legal in a cookie value. Syntax
    # cannot establish completeness or prove authentication.
    return cookie_problem(value) is None


def prepare_cookie(value):
    """Allow a copied single Cookie header; reject response/multiple headers."""
    if not isinstance(value, str):
        raise ValueError(COOKIE_ERROR)
    prepared = value.strip(' ')
    if prepared.lower().startswith('cookie:'):
        prepared = prepared[len('cookie:'):].lstrip(' \t')
    problem = cookie_problem(prepared)
    if problem:
        raise ValueError(problem['message'])
    return prepared


def cookie_summary(value):
    """Safe save/edit diagnostics, not a claim of session completeness."""
    if not value:
        return {'status': 'missing', 'pair_count': 0, 'message': '未保存 Cookie，可同步公开资料。'}
    problem = cookie_problem(value)
    if problem:
        return {'status': 'invalid', 'pair_count': 0, **problem}
    pairs = value.strip().split(';')
    return {'status': 'valid', 'pair_count': len([p for p in pairs if p.strip()]),
            'message': '请求 Cookie 格式通过；是否完整、是否已登录仍需平台身份检测。'}


COOKIE_ERROR = 'Cookie 格式无效，请复制请求中完整的 Cookie 字段值；不要复制 Set-Cookie、整段请求头、JSON 或 curl。'
