"""Check the optional sandboxed runtime without visiting external websites."""
import asyncio
import re
import tempfile

from playwright.async_api import async_playwright
from acmweb.browser_runtime import browser_environment


async def main():
    with tempfile.TemporaryDirectory(prefix='acm-browser-smoke-') as runtime_dir:
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(
                headless=True, channel='chromium', chromium_sandbox=True,
                env=browser_environment(runtime_dir))
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto('chrome://sandbox')
                status = await page.locator('body').inner_text()
                for label in ('PID namespaces', 'Network namespaces', 'Seccomp-BPF sandbox'):
                    if not re.search(re.escape(label) + r'\s+Yes\b', status):
                        raise RuntimeError('Chromium sandbox verification failed: ' + label)
                print('Chromium PID/network namespaces and Seccomp-BPF enabled; no external requests.')
            finally:
                await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
