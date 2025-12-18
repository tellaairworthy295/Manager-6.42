

import asyncio
import os
import shutil
import time
from playwright.sync_api import Page as SyncPage
from playwright.async_api import Page as AsyncPage

def safe_click(page: SyncPage, locator: str, max_attempts: int = 3, timeout: int = 5_000):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            loc = page.locator(locator).first
            loc.scroll_into_view_if_needed()
            loc.click(timeout=timeout)  # normal click
            return
        except Exception as e:
            last_err = e
            if attempt == max_attempts:
                try:
                    loc = page.locator(locator).first
                    # Try force click first
                    try:
                        loc.click(timeout=timeout, force=True)
                        return
                    except Exception:
                        # JS fallback: dispatch full MouseEvent
                        page.evaluate(
                            """(el) => {
                                const evt = new MouseEvent('click', {
                                    bubbles: true,
                                    cancelable: true,
                                    view: window
                                });
                                el.dispatchEvent(evt);
                            }""",
                            loc
                        )
                        return
                except Exception as js_e:
                    last_err = f"{last_err}; JS click: {js_e}"
            time.sleep(attempt)
    raise RuntimeError(f"Failed to click {locator}: {last_err}")

def safe_fill(page: SyncPage, locator: str, text: str, max_attempts: int = 3,
              timeout: int = 5_000, clear_first: bool = True):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            loc = page.locator(locator).first
            loc.scroll_into_view_if_needed()
            if clear_first:
                loc.fill("", timeout=timeout)  # replace
                loc.fill(text, timeout=timeout)
            else:
                loc.type(text, timeout=timeout)  # append
            return
        except Exception as e:
            last_err = e
            if attempt == max_attempts:
                try:
                    handle = page.locator(locator).first
                    if clear_first:
                        page.evaluate(
                            """(el, value) => {
                                if ('value' in el) {
                                    el.value = value;
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                } else {
                                    el.textContent = value;
                                }
                            }""",
                            handle, text
                        )
                    else:
                        page.evaluate(
                            """(el, value) => {
                                if ('value' in el) {
                                    el.value += value;
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                } else {
                                    el.textContent += value;
                                }
                            }""",
                            handle, text
                        )
                    return
                except Exception as js_e:
                    last_err = f"{last_err}; JS fill: {js_e}"
            time.sleep(attempt)
    raise RuntimeError(f"Failed to fill {locator}: {last_err}")

async def async_safe_click(page: AsyncPage, locator: str,
                     max_attempts: int = 3, timeout: int = 5_000):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            loc = page.locator(locator).first
            await loc.scroll_into_view_if_needed()
            await loc.click(timeout=timeout)
            return
        except Exception as e:
            last_err = e
            if attempt == max_attempts:
                try:
                    loc = page.locator(locator).first
                    try:
                        await loc.click(timeout=timeout, force=True)
                        return
                    except Exception:
                        await page.evaluate(
                            """(el) => {
                                const evt = new MouseEvent('click', {
                                    bubbles: true,
                                    cancelable: true,
                                    view: window
                                });
                                el.dispatchEvent(evt);
                            }""",
                            loc
                        )
                        return
                except Exception as js_e:
                    last_err = f"{last_err}; JS click: {js_e}"
            await asyncio.sleep(attempt)
    raise RuntimeError(f"Failed to click {locator}: {last_err}")


async def async_safe_fill(page: AsyncPage, locator: str, text: str,
                    max_attempts: int = 3, timeout: int = 10_000,
                    clear_first: bool = True):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            loc = page.locator(locator).first
            await loc.scroll_into_view_if_needed()
            if clear_first:
                await loc.fill("", timeout=timeout)
                await loc.fill(text, timeout=timeout)
            else:
                await loc.type(text, timeout=timeout)
            return
        except Exception as e:
            last_err = e
            if attempt == max_attempts:
                try:
                    handle = page.locator(locator).first
                    if clear_first:
                        await page.evaluate(
                            """(el, value) => {
                                if ('value' in el) {
                                    el.value = value;
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                } else {
                                    el.textContent = value;
                                }
                            }""",
                            handle, text
                        )
                    else:
                        await page.evaluate(
                            """(el, value) => {
                                if ('value' in el) {
                                    el.value += value;
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                } else {
                                    el.textContent += value;
                                }
                            }""",
                            handle, text
                        )
                    return
                except Exception as js_e:
                    last_err = f"{last_err}; JS fill: {js_e}"
            await asyncio.sleep(attempt)
    raise RuntimeError(f"Failed to fill {locator}: {last_err}")
# -----------------------------
# Convert locators from JSON into (By.X, value)
# -----------------------------
def parse_locators(loc):
    """Convert JSON locator to Playwright locator string."""
    if not loc:
        return None
    by = loc["by"].lower()
    value = loc["value"]
    if by == "css":
        return value  # Playwright accepts CSS selectors directly
    if by == "xpath":
        return f"xpath={value}"  # Playwright XPath format
    raise ValueError(f"Unknown locator type: {by}")

def safe_delete(path, retries=2):
    for _ in range(retries):
        try:
            shutil.rmtree(path)
            return
        except Exception:
            time.sleep(0.5)
    # final fallback: forcibly clear contents
    for root, dirs, files in os.walk(path):
        for f in files:
            try: os.remove(os.path.join(root, f))
            except: pass
        for d in dirs:
            try: shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            except: pass