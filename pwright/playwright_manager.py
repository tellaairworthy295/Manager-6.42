# playwright_manager.py
from playwright.sync_api import sync_playwright
import threading

class PlaywrightManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._playwright = sync_playwright().start()
        self._browser = None

    @classmethod
    def instance(cls):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = cls()
        return cls._instance

    def get_browser(self, args=None, headless=False):
        if self._browser is None:
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=args or [],
            )
        return self._browser

    def shutdown(self):
        if self._browser:
            self._browser.close()
        self._playwright.stop()
        self._browser = None
