# playwright_storage.py
import asyncio
from playwright.async_api import async_playwright

async def dump_storage(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)

        # Dump cookies + localStorage + sessionStorage
        storage = await context.storage_state()
        await browser.close()
        return storage

if __name__ == "__main__":
    import sys, json
    url = sys.argv[1]
    result = asyncio.run(dump_storage(url))
    print(json.dumps(result))