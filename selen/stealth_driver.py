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

# 定义可用的 Chrome 主版本号列表
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
    # 随机选择一个版本号
    selected_version = random.choice(_AVAILABLE_VERSIONS)
    print(f"[INFO] Randomly selected Chrome version: {selected_version}")

    # 构建 Chrome 可执行文件路径
    # 格式: chrome/chrome-141/chrome-win64/chrome.exe
    chrome_exe_rel_path = os.path.join(base_chrome_dir, f"chrome-{selected_version}", "chrome-win64", "chrome.exe")
    browser_executable_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), chrome_exe_rel_path)

    # 构建 Chromedriver 可执行文件路径
    # 格式: chromedriver/chromedriver-141/chromedriver-win64/chromedriver.exe
    driver_exe_rel_path = os.path.join(base_chromedriver_dir, f"chromedriver-{selected_version}", "chromedriver-win64",
                                       "chromedriver.exe")
    driver_executable_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), driver_exe_rel_path)

    # 验证文件是否存在，避免启动报错
    if not os.path.exists(browser_executable_path):
        raise FileNotFoundError(f"Chrome executable not found at: {browser_executable_path}")
    if not os.path.exists(driver_executable_path):
        raise FileNotFoundError(f"ChromeDriver executable not found at: {driver_executable_path}")

    # MUST use UC's option class, not Selenium's
    chrome_options = uc.ChromeOptions()

    # Basic performance flags
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-default-apps")

    # 防止浏览器更新弹窗干扰
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-software-rasterizer")

    # Viewport
    chrome_options.add_argument(_pick_window_size())

    # headers
    chrome_options.add_argument("--lang=en-US,en;q=0.9")

    # Extension
    if base_bypass_ext_path and os.path.exists(base_bypass_ext_path):
        chrome_options.add_argument(f"--load-extension={base_bypass_ext_path}")

    try:
        driver = uc.Chrome(
            driver_executable_path=driver_executable_path,
            browser_executable_path=browser_executable_path,
            options=chrome_options,
            version_main=selected_version,  # 必须与选择的版本一致
            headless=False,
            use_subprocess=True,  # 推荐开启子进程模式以增加稳定性
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


if __name__ == "__main__":
    try:
        driver = get_chrome_driver()
        driver.get("https://www.google.com")
        print("Success! Browser launched with randomized version.")
        # 保持浏览器打开以便观察，实际使用时可根据需要关闭
        # input("Press Enter to close...")
        # driver.quit()
    except Exception as e:
        print(f"Failed to start browser: {e}")