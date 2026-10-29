"""Render local statement HTML only. No page navigation or external requests."""
import html
from pathlib import Path
from bs4 import BeautifulSoup
from acmweb.codex_bridge import AIError

ASSETS = Path(__file__).parent / 'assets'
MAX_PAGES = 16
PAGE_HEIGHT = 3072


def document(statement):
    body = statement.get('body_html')
    if not body:
        from markdown_it import MarkdownIt
        body = MarkdownIt('commonmark', {'html': False}).render('\n\n'.join(statement.get('sections', [])))
    soup = BeautifulSoup(body, 'html.parser')
    from acmweb.statement_math import preserve_math_markup
    preserve_math_markup(soup)
    if statement.get('samples'):
        for node in soup.select('.sample-tests, .sample-test, .sample-input, .sample-output'):
            if node.parent is not None:
                node.decompose()
    # Never fetch arbitrary upstream images (including private/internal URLs).
    # Their exact HTTPS URLs remain visible in the screenshot for manual use.
    images = []
    for image in soup.find_all('img'):
        src = image.get('src', '')
        placeholder = soup.new_tag('p')
        placeholder.string = '图片（未加载）：' + (src if src.startswith('https://') else '地址不可用')
        image.replace_with(placeholder)
        if src.startswith('https://'):
            images.append(src)
    allowed = {'p','div','span','h1','h2','h3','h4','h5','h6','ul','ol','li','pre','code','strong','em','b','i','sup','sub','br','hr','table','thead','tbody','tr','td','th','blockquote','a'}
    for node in list(soup.find_all(True)):
        if node.name is None or node.parent is None:
            continue
        if node.name in {'script','style','iframe','object','embed','svg','math','link','meta','form','input'}:
            node.decompose()
        elif node.name not in allowed:
            node.unwrap()
        else:
            node.attrs = {}
    samples = ''.join('<section><h3>样例输入</h3><pre>' + html.escape(sample['input']) +
                      '</pre><h3>样例输出</h3><pre>' + html.escape(sample['output']) + '</pre></section>'
                      for sample in statement.get('samples', []))
    title = html.escape(statement.get('title', '题目'))
    return ('<!doctype html><meta charset="utf-8"><style>'
            'body{font:18px/1.8 sans-serif;margin:0;padding:40px;width:1020px;box-sizing:border-box;color:#172033;background:#fff;overflow-wrap:anywhere}'
            'pre{white-space:pre-wrap;background:#f3f5f8;padding:16px;border-radius:8px;font:16px/1.6 monospace}'
            'table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:6px}h1{font-size:28px}.katex{font-size:1.1em}'
            '</style><h1>'+title+'</h1><p>AI 中文题目说明 · 请结合官方题面核对，原题插图仅提供链接</p>' + str(soup) + samples), images


class ScreenshotRenderer:
    async def available(self):
        try:
            import markdown_it  # noqa: F401 - required for non-HTML statements
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                return Path(p.chromium.executable_path).is_file() and (ASSETS/'katex.min.js').is_file()
        except ImportError:
            return False

    async def preflight(self):
        if not await self.available():
            raise AIError('Hermes 截图组件未安装：请安装项目 hermes-image 可选依赖并运行 playwright install chromium；尚未调用 AI')
        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel='chromium', headless=True, chromium_sandbox=True)
                await browser.close()
        except Exception:
            raise AIError('Hermes 截图浏览器无法启动，请检查 Chromium 依赖与沙盒；尚未调用 AI') from None

    async def render(self, statement, directory):
        from playwright.async_api import async_playwright
        markup, images = document(statement)
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chromium', headless=True, chromium_sandbox=True)
            try:
                context = await browser.new_context(viewport={'width':1020,'height':PAGE_HEIGHT}, device_scale_factor=1, service_workers='block')
                await context.route('**/*', lambda route: route.abort())
                page = await context.new_page()
                await page.set_content(markup, wait_until='domcontentloaded')
                # Fonts are embedded locally so even file/network URLs remain blocked.
                import base64, re
                css = (ASSETS/'katex.min.css').read_text()
                def font(match):
                    name = match.group(1).split('/')[-1]
                    path = ASSETS/'fonts'/name
                    if path.suffix != '.woff2' or not path.is_file():
                        return 'url(data:application/octet-stream;base64,)'
                    return 'url(data:font/woff2;base64,' + base64.b64encode(path.read_bytes()).decode() + ')'
                css = re.sub(r'url\((?:[\"\']?)([^)\"\']+)(?:[\"\']?)\)', font, css)
                await page.add_style_tag(content=css)
                await page.add_script_tag(content=(ASSETS/'katex.min.js').read_text())
                await page.add_script_tag(content=(ASSETS/'auto-render.min.js').read_text())
                await page.evaluate('''() => renderMathInElement(document.body, {throwOnError:false, trust:false,
                    delimiters:[{left:'$$$',right:'$$$',display:false},{left:'$$',right:'$$',display:true},
                    {left:'$',right:'$',display:false},{left:'\\\\(',right:'\\\\)',display:false},
                    {left:'\\\\[',right:'\\\\]',display:true}]})''')
                await page.evaluate('document.fonts.ready')
                height = await page.evaluate('document.body.scrollHeight')
                if height > MAX_PAGES * PAGE_HEIGHT:
                    raise AIError('题目说明超过截图分页上限，未截断交付；可改用文字接口')
                directory.mkdir(parents=True, mode=0o700, exist_ok=False)
                files=[]
                for i, top in enumerate(range(0,height,PAGE_HEIGHT)):
                    path=directory/f'{i}.png'
                    await page.screenshot(path=str(path), full_page=True,
                        clip={'x':0,'y':top,'width':1020,'height':min(PAGE_HEIGHT,height-top)})
                    path.chmod(0o600); files.append(path.name)
                return files, images
            finally:
                await browser.close()
