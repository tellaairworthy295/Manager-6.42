# playwright_manager.py
import threading
from playwright.sync_api import sync_playwright

class PlaywrightManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._playwright = None
        self._browser = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def start(self):
        """
        Lazy startup — safe to call many times.
        Ensures Playwright is started only when needed.
        """
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        return self._playwright

    def get_browser(self, args=None, headless=False):
        """
        Launch Chromium with flexible args and headless mode.
        Defaults to stealth-friendly flags if none provided.
        """
        if self._browser is None:
            pw = self.start()
            launch_args = args or [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
            ]
            self._browser = pw.chromium.launch(
                headless=headless,
                args=launch_args,
            )
        return self._browser

    def close_browser(self):
        """
        Close browser only (safe for reuse of Playwright).
        """
        if self._browser:
            self._browser.close()
            self._browser = None

    def shutdown(self):
        """
        Full shutdown — closes browser and stops Playwright.
        """
        try:
            if self._browser:
                self._browser.close()
        finally:
            self._browser = None

        try:
            if self._playwright:
                self._playwright.stop()
        finally:
            self._playwright = None