from ..models import Collection, CrawlError


class Adapter:
    platform = ""

    def __init__(self, client, account, *, max_pages=1000, page_size=100, enrich=False):
        self.client, self.account = client, account
        self.max_pages, self.page_size, self.enrich = max_pages, page_size, enrich

    def collect(self, since=0):
        result = Collection(self.platform, self.account)
        try:
            self.fetch(result, since)
        except (CrawlError, KeyError, TypeError, ValueError, IndexError) as exc:
            result.error = str(exc) if isinstance(exc, CrawlError) else f"响应结构不兼容 ({type(exc).__name__})"
            result.complete = False
        result.submissions = list({s.id: s for s in result.submissions}.values())
        return result

    def optional(self, result, label, fn):
        try:
            return fn()
        except (CrawlError, KeyError, TypeError, ValueError, IndexError) as exc:
            detail = str(exc) if isinstance(exc, CrawlError) else type(exc).__name__
            result.warnings.append(f"{label}: {detail}")
            return None

    def limit(self):
        raise CrawlError(f"达到 max_pages={self.max_pages}，采集不完整；增大上限后重试，检查点未推进")
