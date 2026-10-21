"""Check the optional sandboxed runtime without visiting external websites."""
import asyncio

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(
            headless=True, channel='chromium', chromium_sandbox=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto('about:blank')
            print('Sandboxed Chromium runtime available; no external requests.')
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
