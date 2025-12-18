from playwright.async_api import async_playwright
import sys

# restore real stdio (critical on Windows)
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__

class AsyncPlaywrightManager:
    """
    Per-task lifecycle to avoid thread leaks and greenlet conflicts.
    You can later cache per-process if you run --threads 1 or ensure single-thread per process.
    """
    def __init__(self):
        self._pw = None
        self._browser = None

    async def start(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

    async def new_context(self, **kwargs):
        return await self._browser.new_context(**kwargs)

    async def shutdown(self):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()