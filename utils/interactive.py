
import json
import os
import shutil
import time
from typing import Tuple
import zipfile
from click import ClickException
from selenium.webdriver.common.by import By
from selenium.webdriver import ActionChains
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    InvalidSelectorException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from config import setup_logging
logger = setup_logging("logs/cookies", "cookies")
DEFAULT_TIMEOUT = 10

def find_fresh(driver: WebDriver, locator: Tuple[By, str], timeout: int = DEFAULT_TIMEOUT):
    """Return a fresh element reference using explicit wait (presence)."""
    wait = WebDriverWait(driver, timeout)
    return wait.until(EC.presence_of_element_located(locator))

# -----------------------
# Robust send keys
# -----------------------
def safe_send_keys(driver: WebDriver, locator: Tuple[By, str], text: str, max_attempts: int = 5, clear_first: bool = True):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            elem = find_fresh(driver, locator)
            if clear_first:
                try:
                    elem.clear()
                except Exception:
                    # some inputs don't support clear; ignore
                    pass
            elem.send_keys(text)
            return
        except (StaleElementReferenceException, TimeoutException, InvalidSelectorException, Exception) as e:
            last_err = e
            logger.warning(f"safe_send_keys attempt {attempt}/{max_attempts} failed for {locator}: {e}")
            time.sleep(1)
    raise RuntimeError(f"Failed to send keys to {locator}: {last_err}")

    
def safe_click(driver: WebDriver, locator: Tuple[By, str], max_attempts: int = 5, wait_time: int = DEFAULT_TIMEOUT):
    """
    Robust click:
      - re-finds element each attempt (avoids stale)
      - scrolls into view
      - tries JS click -> ActionChains -> element.click()
      - raises ClickException on repeated failure
    locator: tuple like (By.CSS_SELECTOR, "div.foo")
    """
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            elem = find_fresh(driver, locator, timeout=wait_time)
            # scroll center
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            except Exception:
                pass
            time.sleep(0.15)

            # Strategy 1: JS click
            try:
                driver.execute_script("arguments[0].click();", elem)
                logger.debug(f"safe_click: JS click succeeded on {locator}")
                return
            except Exception as e_js:
                last_err = e_js

            # Strategy 2: ActionChains
            try:
                ActionChains(driver).move_to_element(elem).pause(0.05).click().perform()
                logger.debug(f"safe_click: ActionChains click succeeded on {locator}")
                return
            except Exception as e_ac:
                last_err = e_ac

            # Strategy 3: direct click()
            try:
                elem.click()
                logger.debug(f"safe_click: direct click succeeded on {locator}")
                return
            except Exception as e_click:
                last_err = e_click

        except (StaleElementReferenceException, TimeoutException, InvalidSelectorException) as e:
            last_err = e

        logger.warning(f"safe_click failed (attempt {attempt}/{max_attempts}) for {locator}: {last_err}")
        time.sleep(1 * attempt)

    raise ClickException(f"All click attempts failed for {locator}: {last_err}")

# -----------------------
# Popup closing helper
# -----------------------
def close_popup(driver: WebDriver, css_selector: str, max_attempts: int = 5):
    locator = (By.CSS_SELECTOR, css_selector)
    for attempt in range(1, max_attempts + 1):
        try:
            # short wait for clickable
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable(locator))
            safe_click(driver, locator)
            time.sleep(0.25)
            logger.info(f"Closed popup {css_selector}")
            return
        except TimeoutException:
            logger.info(f"No popup {css_selector} found (attempt {attempt}).")
            return
        except InvalidSelectorException:
            logger.warning(f"Invalid selector for popup: {css_selector}")
            return
        except Exception as e:
            logger.warning(f"close_popup attempt {attempt} failed for {css_selector}: {e}")
            time.sleep(0.5)
    logger.warning(f"Failed to close popup {css_selector} after {max_attempts} attempts")


# -----------------------
# Navigation / login helper
# -----------------------
def generate_localstorage_js(_dict):
    def js_str(s):
        # Escape string as JavaScript string literal
        return json.dumps(s, ensure_ascii=False)

    js_lines = []
    for key, value in _dict.items():
        js_key = js_str(key)
        if isinstance(value, bool):
            js_value = "true" if value else "false"
            js_lines.append(f"window.localStorage.setItem({js_key}, {js_value});")
        elif isinstance(value, (int, float)):
            js_lines.append(f"window.localStorage.setItem({js_key}, {value});")
        elif isinstance(value, dict):
            json_str = json.dumps(value, ensure_ascii=False)
            # Use js_str to ensure correct escaping of quotes
            js_lines.append(
                f"window.localStorage.setItem({js_key}, JSON.stringify({json_str}));"
            )
        else:  # string or token
            js_value = js_str(value)
            js_lines.append(
                f"window.localStorage.setItem({js_key}, {js_value});"
            )
    return "\n".join(js_lines)

# -----------------------
# Zip and cleanup
# -----------------------
def zip_files(d_files: list[str], out_file: str):
    if not d_files:
        logger.warning("zip_files called with empty list")
        return None
    docx_files = [f for f in d_files if os.path.exists(f) and (f.lower().endswith('.docx') or f.lower().endswith('.doc'))]
    if not docx_files:
        logger.warning("No doc/docx files found to zip")
        return None
    with zipfile.ZipFile(out_file, "w") as zf:
        for f in docx_files:
            zf.write(f, arcname=os.path.basename(f))
    logger.info(f"Created zip: {out_file}")


def safe_delete(path, retries=3):
    for i in range(retries):
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

    # -----------------------------
    # Convert locators from JSON into (By.X, value)
    # -----------------------------
def parse_locators(loc):
    if not loc:
        return None
    by = loc["by"].lower()
    value = loc["value"]
    if by == "css":
        return (By.CSS_SELECTOR, value)
    if by == "xpath":
        return (By.XPATH, value)
    raise ValueError(f"Unknown locator type: {by}")