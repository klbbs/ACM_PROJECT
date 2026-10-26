"""Recognize access gates without executing them or retaining upstream pages."""
from bs4 import BeautifulSoup

from .models import CrawlError


class PlatformAccessError(CrawlError):
    def __init__(self, host, reason, status=None):
        self.reason, self.status = reason, status
        labels = {'challenge': '平台要求浏览器或验证码验证，当前读取入口已停止',
                  'access_restricted': '平台拒绝此读取入口（HTTP 403/418），不能据此认定 Cookie 失效',
                  'rate_limited': '平台限制请求频率，请等待后重试'}
        super().__init__(f'{host}: {labels[reason]}')


def protection_reason(text, status=200):
    """Inspect gate markup, never phrases inside problem statements or code."""
    if status == 429:
        return 'rate_limited'
    stripped = text.lstrip('\ufeff \t\r\n') if isinstance(text, str) else ''
    if stripped.startswith('<'):
        soup = BeautifulSoup(text, 'html.parser')
        title = soup.title.get_text(' ', strip=True).casefold() if soup.title else ''
        gate = soup.select_one('#challenge-form,#challenge-running,#cf-challenge-running,'
            '.cf-turnstile,.g-recaptcha,.h-captcha,#geetest,#captcha,form[action*="captcha"],'
            'script[src*="/cdn-cgi/challenge-platform/"][src*="/orchestrate/chl_page/"]')
        if gate or title.rstrip(' .…!') in {'just a moment', 'checking your browser',
                                            'attention required! | cloudflare', '人机验证', '安全验证'}:
            return 'challenge'
    if status in (403, 418):
        return 'access_restricted'
    return None
