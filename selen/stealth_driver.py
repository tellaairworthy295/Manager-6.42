import undetected_chromedriver as uc
import random
import os
from selenium_stealth import stealth

# Your Webshare API Key
WEBSHARE_API_KEY = "arazreewnhmcfhfbmgcbfwtlqecbnlsscxxgyyzf"
WEBSHARE_API_URL = "https://proxy.webshare.io/api/v2/proxy/list/download/arazreewnhmcfhfbmgcbfwtlqecbnlsscxxgyyzf/-/any/username/direct/-/?plan_id=12692553"

# 更改和强化user agents参数以提升绕过检测的概率
_USER_AGENTS = [
 # ---- Windows ----
    # Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.147 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.207 Safari/537.36",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.147 Safari/537.36 Edg/125.0.2535.51",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36 Edg/126.0.2592.56",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Brave
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Brave/1.66.115 Chrome/125.0.6422.147 Safari/537.36",
    # Opera
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36 OPR/109.0.5097.38",
    # ---- Mac ----
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.147 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36",
    # Edge (Mac)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36 Edg/126.0.2592.56",
    # Opera (Mac)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36 OPR/109.0.5097.38",
    # Brave (Mac)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Brave/1.66.115 Chrome/126.0.6478.31 Safari/537.36",
    # ---- Linux ----
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.147 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Brave/1.66.115 Chrome/126.0.6478.31 Safari/537.36",
    # Opera (Linux)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Safari/537.36 OPR/109.0.5097.38",
    # ---- Mobile / Tablet ----
    # iPhone (Safari)
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/605.1.15",
    # iPad (Safari)
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15F5347a Safari/605.1.15",
    # Android Chrome
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro Build/AP1A.240405.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.147 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Mobile Safari/537.36",
    # Samsung Browser (Android)
    "Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-G998N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/125.0.6422.147 Mobile Safari/537.36",
    # Edge Mobile
    "Mozilla/5.0 (Linux; Android 14; SM-G998N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.31 Mobile Safari/537.36 EdgA/126.0.2592.56",
    # Rare/Uncommon UAs for more diversity
    "Mozilla/5.0 (PlayStation 5 5.00) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.00 Safari/605.1.15",
    "Mozilla/5.0 (CrOS x86_64 16414.67.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.147 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
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
            version_main=145,
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
