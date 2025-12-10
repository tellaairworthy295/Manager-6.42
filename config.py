# logging_setup.py
import logging
import os
import builtins
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
import shutil
import time
import uuid
from selenium_stealth import stealth
from selenium.webdriver.chrome.options import Options
import undetected_chromedriver as uc


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
def get_safe_temp_base(base_bypass_ext_path):
    """
    Place _temp_profiles as a sibling directory to the base extension folder
    (so we avoid copying temp dirs into new copies).
    """
    if base_bypass_ext_path and os.path.exists(base_bypass_ext_path):
        parent = os.path.dirname(os.path.abspath(base_bypass_ext_path))
        safe_base = os.path.join(parent, "_temp_profiles")
    else:
        # fallback to current working dir if base not found
        safe_base = os.path.join(os.getcwd(), "_temp_profiles")
    os.makedirs(safe_base, exist_ok=True)
    return safe_base

def copy_extension_once(src_ext_path, dest_ext_path):
    """
    Copy extension directory while ignoring any _temp_profiles folders (prevents recursion).
    Raises on failure.
    """
    if not os.path.exists(src_ext_path):
        raise FileNotFoundError(f"Source extension not found: {src_ext_path}")
    if os.path.exists(dest_ext_path):
        # unlikely but be safe: remove stale target first
        shutil.rmtree(dest_ext_path)
    # ignore _temp_profiles and macOS/hidden files that may cause trouble
    ignore = shutil.ignore_patterns("_temp_profiles", "._*", ".DS_Store")
    shutil.copytree(src_ext_path, dest_ext_path, ignore=ignore)

def get_chrome_driver(
    download_dir: str = "",
    base_bypass_ext_path: str = "bypass-paywalls-chrome-clean-master",
    browser_executable_path: str = "chrome-linux64/chrome",
    multi_instance: bool = False,
):
    ext_temp_path = None
    profile_dir = None

    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--auto-open-devtools-for-tabs")


    # ------------------------------------------------------------------
    # Safe base next to extension (sibling) to avoid recursive copying
    # ------------------------------------------------------------------
    if base_bypass_ext_path:
        safe_base = get_safe_temp_base(base_bypass_ext_path)

    # ------------------------------------------------------------------
    # Extension setup
    # ------------------------------------------------------------------
    if base_bypass_ext_path and os.path.exists(base_bypass_ext_path):
        if multi_instance:
            # ensure we always copy from the ORIGINAL base extension path,
            # not from an existing ext_temp_path (which prevents nesting).
            ext_temp_path = os.path.join(safe_base, f"ext_copy_{uuid.uuid4().hex}")
            try:
                copy_extension_once(base_bypass_ext_path, ext_temp_path)
                chrome_options.add_argument(f"--load-extension={ext_temp_path}")
                print(f"[INFO] Multi-instance: extension copied to {ext_temp_path}")
            except Exception as e:
                print(f"[WARNING] Failed to copy extension: {e}")
                ext_temp_path = None
        else:
            # Single-instance: load original extension directly (no copies)
            chrome_options.add_argument(f"--load-extension={base_bypass_ext_path}")
            print(f"[INFO] Single-instance: extension loaded from {base_bypass_ext_path}")
    else:
        if base_bypass_ext_path:
            print(f"[WARNING] Bypass extension not found at {base_bypass_ext_path}")
        else:
            print(f"[INFO] No bypass extension loaded")

    # ------------------------------------------------------------------
    # Profile setup (placed under safe_base as sibling)
    # ------------------------------------------------------------------
    if multi_instance:
        profile_dir = os.path.join(safe_base, f"profile_{uuid.uuid4().hex}")
        os.makedirs(profile_dir, exist_ok=True)
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")
        print(f"[INFO] Multi-instance: profile created at {profile_dir}")

    # ------------------------------------------------------------------
    # Download prefs
    # ------------------------------------------------------------------
    if download_dir:
        prefs = {
            "profile.default_content_settings.popups": 0,
            "download.prompt_for_download": False,
            "directory_upgrade": True,
            "safebrowsing.enabled": True,
            "download.default_directory": os.path.abspath(download_dir),
        }
        chrome_options.add_experimental_option("prefs", prefs)

    # ------------------------------------------------------------------
    # Launch Chrome
    # ------------------------------------------------------------------
    try:
        driver = uc.Chrome(
            driver_executable_path="chromedriver-linux64/chromedriver",
            browser_executable_path=browser_executable_path,
            options=chrome_options,
            version_main=143
        )
    except Exception as e:
        raise RuntimeError(f"driver errror!!!!{e}")
        driver = uc.Chrome(
            version_main=143,
            options=chrome_options,
            mirror="https://registry.npmmirror.com/-/binary/chromedriver/"
        )

    # ------------------------------------------------------------------
    # Minimize window after launch (most robust way)
    # ------------------------------------------------------------------
    # try:
    #     driver.minimize_window()
    # except Exception as e:
    #     print(f"[WARNING] Failed to minimize window: {e}")

    # ------------------------------------------------------------------
    # Ensure download behavior
    # ------------------------------------------------------------------
    if download_dir:
        try:
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": os.path.abspath(download_dir)},
            )
        except Exception as e:
            print(f"[WARNING] Failed to set download path: {e}")

    # ------------------------------------------------------------------
    # Apply stealth
    # ------------------------------------------------------------------
    try:
        stealth(
            driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.7499.40 Safari/537.36"
            ),
        )
    except Exception as e:
        print(f"[WARNING] stealth() failed: {e}")

    if multi_instance:
        return driver, ext_temp_path, profile_dir
    else:
        return driver

def cleanup_driver_dirs(ext_temp_path, profile_dir, retries=6, delay=0.5):
    """
    Try several times to remove the temp dirs. Logs clearly if eventual failure.
    """
    for path in (ext_temp_path, profile_dir):
        if not path:
            continue
        if not os.path.exists(path):
            # nothing to do
            continue
        for attempt in range(1, retries + 1):
            try:
                shutil.rmtree(path)
                print(f"[INFO] Removed temp path: {path}")
                break
            except FileNotFoundError:
                break
            except PermissionError as e:
                # common on Windows if some handle lingers; wait & retry
                if attempt < retries:
                    time.sleep(delay)
                else:
                    print(f"[WARNING] Could not remove {path} after {retries} attempts: {e}")
            except Exception as e:
                print(f"[WARNING] Unexpected error removing {path}: {e}")
                break