import asyncio
import os
import shutil
import time
from playwright.sync_api import Page as SyncPage
from playwright.async_api import Page as AsyncPage


# -----------------------------
# Shared helpers
# -----------------------------
def _js_click_script():
    return """(el) => {
        const evt = new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            view: window
        });
        el.dispatchEvent(evt);
    }"""


def _js_fill_script(clear=True):
    if clear:
        return """(el, value) => {
            if ('value' in el) {
                el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            } else {
                el.textContent = value;
            }
        }"""
    else:
        return """(el, value) => {
            if ('value' in el) {
                el.value += value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            } else {
                el.textContent += value;
            }
        }"""


# -----------------------------
# Sync versions
# -----------------------------
def safe_click(page: SyncPage, locator: str, max_attempts=3, timeout=5000):
    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            loc = page.locator(locator).first
            loc.wait_for(state="visible", timeout=timeout)
            loc.scroll_into_view_if_needed()
            loc.click(timeout=timeout)
            return
        except Exception as e:
            last_err = e

        if attempt == max_attempts:
            try:
                loc = page.locator(locator).first
                loc.wait_for(state="attached", timeout=timeout)

                # force click
                try:
                    loc.click(timeout=timeout, force=True)
                    return
                except Exception:
                    handle = loc.element_handle()
                    if handle:
                        page.evaluate(_js_click_script(), handle)
                        return

            except Exception as js_e:
                last_err = f"{last_err}; fallback failed: {js_e}"

        time.sleep(attempt)

    raise RuntimeError(f"[safe_click] Failed: {locator} -> {last_err}")


def safe_fill(page: SyncPage, locator: str, text: str,
              max_attempts=3, timeout=5000, clear_first=True):

    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            loc = page.locator(locator).first
            loc.wait_for(state="visible", timeout=timeout)
            loc.scroll_into_view_if_needed()

            if clear_first:
                loc.fill("", timeout=timeout)
                loc.fill(text, timeout=timeout)
            else:
                loc.type(text, timeout=timeout)

            return

        except Exception as e:
            last_err = e

        if attempt == max_attempts:
            try:
                loc = page.locator(locator).first
                handle = loc.element_handle()

                if handle:
                    page.evaluate(_js_fill_script(clear_first), handle, text)
                    return

            except Exception as js_e:
                last_err = f"{last_err}; JS fallback failed: {js_e}"

        time.sleep(attempt)

    raise RuntimeError(f"[safe_fill] Failed: {locator} -> {last_err}")


# -----------------------------
# Async versions
# -----------------------------
async def async_safe_click(
    page: AsyncPage,
    locator: str,
    max_attempts=3,
    timeout=5000,
    multiple=False
):
    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            if multiple:
                elements = await page.query_selector_all(locator)
                if not elements:
                    raise RuntimeError(f"No elements found: {locator}")

                success = 0

                for el in elements:
                    try:
                        await el.scroll_into_view_if_needed()
                    except:
                        pass

                    try:
                        await el.click(timeout=timeout)
                        success += 1
                    except:
                        try:
                            await page.evaluate(_js_click_script(), el)
                            success += 1
                        except:
                            pass

                    await asyncio.sleep(0.5)
                if success == 0:
                    raise RuntimeError("All elements failed to click")

                return

            else:
                loc = page.locator(locator).first
                await loc.wait_for(state="visible", timeout=timeout)
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
                except:
                    handle = await loc.element_handle()
                    if handle:
                        await page.evaluate(_js_click_script(), handle)
                        return

            except Exception as js_e:
                last_err = f"{last_err}; fallback failed: {js_e}"

        await asyncio.sleep(attempt)

    raise RuntimeError(
        f"[async_safe_click] Failed: {locator} "
        f"{'(multiple)' if multiple else ''} -> {last_err}"
    )


async def async_safe_fill(
    page: AsyncPage,
    locator: str,
    text: str,
    max_attempts=3,
    timeout=10000,
    clear_first=True
):
    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            loc = page.locator(locator).first
            await loc.wait_for(state="visible", timeout=timeout)
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
                loc = page.locator(locator).first
                handle = await loc.element_handle()

                if handle:
                    await page.evaluate(_js_fill_script(clear_first), handle, text)
                    return

            except Exception as js_e:
                last_err = f"{last_err}; JS fallback failed: {js_e}"

        await asyncio.sleep(attempt)

    raise RuntimeError(f"[async_safe_fill] Failed: {locator} -> {last_err}")


# -----------------------------
# Locator parser (unchanged)
# -----------------------------
def parse_locators(loc):
    if not loc:
        return None
    by = loc["by"].lower()
    value = loc["value"]

    if by == "css":
        return value
    if by == "xpath":
        return f"xpath={value}"

    raise ValueError(f"Unknown locator type: {by}")


# -----------------------------
# Safe delete (improved)
# -----------------------------
def safe_delete(path, retries=2):
    if not os.path.exists(path):
        return

    for _ in range(retries):
        try:
            shutil.rmtree(path)
            return
        except Exception:
            time.sleep(0.5)

    # fallback: best-effort cleanup
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
            except:
                pass
        for d in dirs:
            try:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            except:
                pass