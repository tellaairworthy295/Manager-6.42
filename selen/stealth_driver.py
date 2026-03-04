
import undetected_chromedriver as uc
import random
import os

_WINDOW_SIZES = [
    (1920, 1080),
    (1600, 900),
    (1536, 864),
    (1366, 768),
]

_AVAILABLE_VERSIONS = [141, 142, 143, 144, 145]

# Realistic plugin counts to spoof
_PLUGIN_COUNTS = [3, 4, 5, 6, 7]

# Realistic WebGL renderers
_WEBGL_RENDERERS = [
    "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)",
]

# Realistic screen resolutions (matching window sizes)
_SCREEN_CONFIGS = [
    {"width": 1920, "height": 1080, "window": "1920,1080"},
    {"width": 1600, "height": 900,  "window": "1600,900"},
    {"width": 1536, "height": 864,  "window": "1536,864"},
    {"width": 1366, "height": 768,  "window": "1366,768"},
]


def _build_stealth_script(screen: dict, renderer: str, plugin_count: int) -> str:
    """
    Build a targeted CDP injection script.
    Only patches properties UC does NOT already handle,
    or reinforces them without conflicting.
    """
    return f"""
        // ── navigator.plugins ──────────────────────────────────────────
        // UC doesn't spoof this; an empty list is a strong bot signal
        Object.defineProperty(navigator, 'plugins', {{
            get: () => {{
                const arr = new Array({plugin_count});
                for (let i = 0; i < {plugin_count}; i++) {{
                    arr[i] = {{ name: 'Plugin ' + i, filename: 'plugin' + i + '.dll' }};
                }}
                return arr;
            }},
        }});

        // ── navigator.languages ─────────────────────────────────────────
        Object.defineProperty(navigator, 'languages', {{
            get: () => ['en-US', 'en'],
        }});

        // ── screen resolution ───────────────────────────────────────────
        // Must match the --window-size flag to avoid mismatch detection
        Object.defineProperty(screen, 'width',       {{ get: () => {screen['width']}  }});
        Object.defineProperty(screen, 'height',      {{ get: () => {screen['height']} }});
        Object.defineProperty(screen, 'availWidth',  {{ get: () => {screen['width']}  }});
        Object.defineProperty(screen, 'availHeight', {{ get: () => {screen['height']} }});

        // ── WebGL renderer ──────────────────────────────────────────────
        // Randomise so repeated sessions don't share an identical fingerprint
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) return 'Google Inc. (Intel)';   // VENDOR
            if (parameter === 37446) return '{renderer}';            // RENDERER
            return getParameter.call(this, parameter);
        }};

        // ── chrome runtime ──────────────────────────────────────────────
        // Ensure window.chrome exists (headless sometimes strips it)
        if (!window.chrome) {{
            window.chrome = {{ runtime: {{}} }};
        }}

        // ── permission query ────────────────────────────────────────────
        // Headless returns 'denied' for notifications; real browsers return 'default'
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({{ state: Notification.permission }})
                : originalQuery(parameters)
        );
    """


def get_chrome_driver(
        base_bypass_ext_path: str = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..",
            "bypass-paywalls-chrome-clean-master"
        ),
        base_chrome_dir: str = "chrome",
        base_chromedriver_dir: str = "chromedriver"
):
    selected_version = random.choice(_AVAILABLE_VERSIONS)
    selected_screen  = random.choice(_SCREEN_CONFIGS)
    selected_renderer = random.choice(_WEBGL_RENDERERS)
    selected_plugins  = random.choice(_PLUGIN_COUNTS)

    print(f"[INFO] Chrome version : {selected_version}")
    print(f"[INFO] Window size    : {selected_screen['window']}")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ── Executable paths ────────────────────────────────────────────────
    chrome_exe_rel = os.path.join(
        base_chrome_dir,
        f"chrome-{selected_version}",
        "chrome-win64", "chrome.exe"
    )
    browser_executable_path = os.path.abspath(os.path.join(project_root, chrome_exe_rel))

    driver_exe_rel = os.path.join(
        base_chromedriver_dir,
        f"chromedriver-{selected_version}",
        "chromedriver-win64", "chromedriver.exe"
    )
    driver_executable_path = os.path.abspath(os.path.join(project_root, driver_exe_rel))

    if not os.path.exists(browser_executable_path):
        raise FileNotFoundError(f"Chrome executable not found at: {browser_executable_path}")
    if not os.path.exists(driver_executable_path):
        raise FileNotFoundError(f"ChromeDriver not found at: {driver_executable_path}")

    # ── Chrome options ──────────────────────────────────────────────────
    chrome_options = uc.ChromeOptions()

    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--password-store=basic")
    chrome_options.add_argument(f"--window-size={selected_screen['window']}")
    chrome_options.add_argument("--lang=en-US,en;q=0.9")
    chrome_options.add_argument("--disable-features=IsolateOrigins,site-per-process")
    chrome_options.add_argument("--disable-site-isolation-trials")

    # ── Extension ───────────────────────────────────────────────────────
    if base_bypass_ext_path and os.path.exists(base_bypass_ext_path):
        chrome_options.add_argument(f"--load-extension={base_bypass_ext_path}")

    # ── Launch driver ───────────────────────────────────────────────────
    try:
        driver = uc.Chrome(
            driver_executable_path=driver_executable_path,
            browser_executable_path=browser_executable_path,
            options=chrome_options,
            version_main=selected_version,
            headless=False          # Bloomberg detects headless reliably
        )
    except Exception as e:
        raise RuntimeError(f"Failed to launch Chrome driver: {e}")

    # ── Targeted CDP patches (no selenium_stealth) ──────────────────────
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _build_stealth_script(selected_screen, selected_renderer, selected_plugins)}
        )
    except Exception as e:
        print(f"[WARNING] CDP stealth injection failed: {e}")

    return driver