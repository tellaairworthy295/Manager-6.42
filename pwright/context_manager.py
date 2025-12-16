# context_manager.py
import os
from contextlib import contextmanager
from .playwright_manager import PlaywrightManager

@contextmanager
def playwright_context(
    storage_state=None,
    bypass_ext_path=None,
    headless=False,
):
    ext_path = os.path.abspath(bypass_ext_path) if bypass_ext_path else None

    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled", "--disable-infobars"]

    if ext_path and os.path.exists(ext_path):
        args += [
            f"--disable-extensions-except={ext_path}",
            f"--load-extension={ext_path}",
        ]

    manager = PlaywrightManager.instance()
    browser = manager.get_browser(args=args, headless=headless)

    context = browser.new_context(
        storage_state=storage_state,
        accept_downloads=True,  
        ignore_https_errors=True,
    )

    # ---- Anti-popup hardening ----
    context.add_init_script("window.open = () => null;")

    main_page = None

    def on_new_page(page):
        nonlocal main_page
        if main_page is None:
            main_page = page
            return
        page.close()

    context.on("page", on_new_page)

    try:
        yield context
    finally:
        try:
            context.close()
        except Exception:
            pass