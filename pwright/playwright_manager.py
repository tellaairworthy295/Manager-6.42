# playwright_manager.py
import threading
import sys

# restore real stdio (critical on Windows)
sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
from playwright.sync_api import sync_playwright

class PlaywrightManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, pw):
        self._playwright = pw
        self._browser = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(sync_playwright().start())
        return cls._instance

    def get_browser(self, args=None, headless=False):
        """
        Launch Chromium with flexible args and headless mode.
        Defaults to stealth-friendly flags if none provided.
        """
        if self._browser is None:
            launch_args = args or [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
            ]
            self._browser = self._playwright.chromium.launch(
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