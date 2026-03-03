import undetected_chromedriver as uc
import random
import os
from selenium_stealth import stealth

_WINDOW_SIZES = [
    (1920, 1080),
    (1600, 900),
    (1536, 864),
    (1366, 768),
]

_AVAILABLE_VERSIONS = [141, 142, 143, 144, 145]


def _pick_window_size() -> str:
    width, height = random.choice(_WINDOW_SIZES)
    return f"--window-size={width},{height}"


def get_chrome_driver(
        base_bypass_ext_path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                                 "bypass-paywalls-chrome-clean-master"),
        base_chrome_dir: str = "chrome",
        base_chromedriver_dir: str = "chromedriver"
):
    selected_version = random.choice(_AVAILABLE_VERSIONS)
    print(f"[INFO] Randomly selected Chrome version: {selected_version}")

    # --- 1. VERSION-SPECIFIC PROFILE FOLDER ---
    # Construct path to the root of your project
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Name the profile folder based strictly on the version
    profile_dir_name = f"profile_v{selected_version}"
    profile_dir = os.path.abspath(os.path.join(project_root, "chrome_profiles", profile_dir_name))

    # Create the directory if it doesn't exist
    os.makedirs(profile_dir, exist_ok=True)
    # print(f"[INFO] Using persistent profile directory: {profile_dir}")
    # ------------------------------------------

    # Construct paths
    chrome_exe_rel_path = os.path.join(base_chrome_dir, f"chrome-{selected_version}", "chrome-win64", "chrome.exe")
    browser_executable_path = os.path.abspath(os.path.join(project_root, chrome_exe_rel_path))

    driver_exe_rel_path = os.path.join(base_chromedriver_dir, f"chromedriver-{selected_version}", "chromedriver-win64",
                                       "chromedriver.exe")
    driver_executable_path = os.path.abspath(os.path.join(project_root, driver_exe_rel_path))

    if not os.path.exists(browser_executable_path):
        raise FileNotFoundError(f"Chrome executable not found at: {browser_executable_path}")
    if not os.path.exists(driver_executable_path):
        raise FileNotFoundError(f"ChromeDriver executable not found at: {driver_executable_path}")

    # MUST use UC's option class
    chrome_options = uc.ChromeOptions()

    # Basic flags
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-software-rasterizer")

    # Inject the persistent profile into options
    # chrome_options.add_argument(f"--user-data-dir={profile_dir}")

    chrome_options.add_argument(_pick_window_size())
    chrome_options.add_argument("--lang=en-US,en;q=0.9")

    # Extension
    if base_bypass_ext_path and os.path.exists(base_bypass_ext_path):
        chrome_options.add_argument(f"--load-extension={base_bypass_ext_path}")

    try:
        driver = uc.Chrome(
            driver_executable_path=driver_executable_path,
            browser_executable_path=browser_executable_path,
            options=chrome_options,
            # user_data_dir=profile_dir,  # <-- CRITICAL for undetected_chromedriver
            version_main=selected_version,
            headless=False
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
        )
    except Exception as e:
        print(f"[WARNING] Stealth module failed: {e}")

    return driver