import undetected_chromedriver as uc
import random
import os
from selenium_stealth import stealth

# Your Webshare API Key
WEBSHARE_API_KEY = "arazreewnhmcfhfbmgcbfwtlqecbnlsscxxgyyzf"
WEBSHARE_API_URL = "https://proxy.webshare.io/api/v2/proxy/list/download/arazreewnhmcfhfbmgcbfwtlqecbnlsscxxgyyzf/-/any/username/direct/-/?plan_id=12692553"

_USER_AGENTS = [
    # Updated Windows user agents
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6470.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6540.50 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Updated Mac user agents
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6480.120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6532.10 Safari/537.36",
    # Updated Linux user agents
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6502.5 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    # Updated Mobile user agents
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-G998U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6480.94 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 15; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6532.97 Mobile Safari/537.36",
    # More recent generic Chrome and Safari variations
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6480.76 Safari/537.36"
]

_WINDOW_SIZES = [
    (1920, 1080),
    (1600, 900),
    (1536, 864),
    (1366, 768),
]


def _pick_user_agent(user_agent_override: str | None = None) -> str:
    return user_agent_override or random.choice(_USER_AGENTS)


def _pick_window_size() -> str:
    width, height = random.choice(_WINDOW_SIZES)
    return f"--window-size={width},{height}"


def get_chrome_driver(
    base_bypass_ext_path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bypass-paywalls-chrome-clean-master"),
    user_agent: str | None = None,
    proxy_url: str | None = None,
):

    ua = _pick_user_agent(user_agent)
    proxy = proxy_url or os.getenv("SCRAPER_PROXY")

    # MUST use UC's option class, not Selenium's
    chrome_options = uc.ChromeOptions()

    # Basic performance flags
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-default-apps")

    # Viewport
    chrome_options.add_argument(_pick_window_size())

    # UA + headers
    chrome_options.add_argument(f"--user-agent={ua}")
    chrome_options.add_argument("--lang=en-US,en;q=0.9")

    if proxy:
        chrome_options.add_argument(f"--proxy-server={proxy}")

    # Extension
    if base_bypass_ext_path and os.path.exists(base_bypass_ext_path):
        chrome_options.add_argument(f"--load-extension={base_bypass_ext_path}")

    try:
        driver = uc.Chrome(
            driver_executable_path="chromedriver-win64/chromedriver.exe",
            browser_executable_path="chrome-win64/chrome.exe",
            options=chrome_options,
            version_main=143,
            headless=False,
        )
    except Exception as e:
        raise RuntimeError(f"driver error: {e}")

    # Stealth
    try:
        stealth(
            driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL",
            fix_hairline=False,
            user_agent=ua,
        )
    except:
        pass

    return driver


if __name__ == "__main__":
    driver = get_chrome_driver()
    driver.get("https://www.google.com")
