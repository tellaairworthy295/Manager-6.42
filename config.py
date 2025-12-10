# logging_setup.py
import logging
import os
import builtins
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from playwright.sync_api import sync_playwright

# ========================= Logging Setup =========================
def setup_logging(dir: str, name: str):
    import sys

    LOG_DIR = dir
    os.makedirs(LOG_DIR, exist_ok=True)

    log_file = os.path.join(LOG_DIR, f"{name}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Custom color formatter for console
    class ColorFormatter(logging.Formatter):
        RESET = "\033[0m"
        RED = "\033[91m"
        YELLOW = "\033[93m"

        def format(self, record):
            msg = super().format(record)
            if record.levelno == logging.ERROR:
                return f"{self.RED}{msg}{self.RESET}"
            elif record.levelno == logging.WARNING:
                return f"{self.YELLOW}{msg}{self.RESET}"
            else:
                return msg

    if not logger.handlers:
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=15,
            encoding="utf-8"
        )
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Console handler with color
        console_handler = logging.StreamHandler(sys.stdout)
        color_formatter = ColorFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        console_handler.setFormatter(color_formatter)
        logger.addHandler(console_handler)

    builtins._original_print = builtins.print

    def log_print(*args, sep=" ", end="\n", level=logging.INFO, **kwargs):
        """
        Redirect all print() output to the shared logger.
        Behaves like normal print(), but logs to file.
        """
        message = sep.join(map(str, args)) + end
        logger.log(level, message.strip())

    builtins.print = log_print

    return logger

# ========================= Chrome Setup =========================  
def get_playwright_browser(
    download_dir: str = "",
    base_bypass_ext_path: str = "bypass-paywalls-chrome-clean-master"
):
    """
    Launch a Playwright Chromium browser with extension and download prefs.
    Equivalent to your Selenium driver setup.
    """

    # Ensure absolute paths
    download_dir = os.path.abspath(download_dir) if download_dir else None
    ext_path = os.path.abspath(base_bypass_ext_path) if base_bypass_ext_path else None

    # Persistent user data dir is required for extensions
    user_data_dir = os.path.abspath("./playwright-profile")

    with sync_playwright() as p:
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--remote-debugging-port=9222",
            "--auto-open-devtools-for-tabs",
        ]

        # Extension setup
        if ext_path and os.path.exists(ext_path):
            args.append(f"--disable-extensions-except={ext_path}")
            args.append(f"--load-extension={ext_path}")
            print(f"[INFO] Extension loaded from {ext_path}")
        else:
            if base_bypass_ext_path:
                print(f"[WARNING] Bypass extension not found at {ext_path}")
            else:
                print(f"[INFO] No bypass extension loaded")

        # Launch persistent context (needed for extensions)
        if ext_path:
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,  # must be headful for extensions
                args=args,
            )
        else:
            browser = p.chromium.launch(
                headless=False,
                args=args,
            )
            context = browser.new_context()

        # Download behavior
        if download_dir:
            context.set_default_downloads_path(download_dir)
            print(f"[INFO] Downloads will be saved to {download_dir}")

        return context


def get_playwright_page(context):
     # Apply stealth‑like settings manually
    page = context.new_page()
    page.set_extra_http_headers({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.7499.40 Safari/537.36"
        )
    })

    # You can also inject JS to spoof navigator values if needed
    page.add_init_script("""
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
    """)
    return page
