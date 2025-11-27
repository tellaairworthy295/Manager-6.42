
import os
import random
import re
import time
import glob
import zipfile
from typing import Tuple, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver import ActionChains
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    InvalidSelectorException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from config import get_chrome_driver, setup_logging

DOWNLOAD_DIR = os.path.join(os.getcwd(), "oneStockPage")
logger = setup_logging("logs/alphapai", "alphapai_scraper")
# ========================= CONFIG =========================
LOGIN_URL = "https://alphapai-web.rabyte.cn/reading/paipai/index"
PHONE_NUMBER = "18516208815"     # 👈 replace with your real phone number
PASSWORD = "lmh021152LMH"        # 👈 replace with your real password
DEFAULT_TIMEOUT = 20
# -----------------------
# Custom Exceptions
# -----------------------
class ClickException(Exception):
    pass

def sanitize_filename(name: str) -> str:
    """
    Remove or replace characters illegal in Windows filenames.
    """
    # Replace illegal characters with empty string
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    # Strip leading/trailing spaces and dots
    return name.strip(" .")
# -----------------------
# Utility: fresh lookup
# -----------------------
def find_fresh(driver: WebDriver, locator: Tuple[By, str], timeout: int = DEFAULT_TIMEOUT):
    """Return a fresh element reference using explicit wait (presence)."""
    wait = WebDriverWait(driver, timeout)
    return wait.until(EC.presence_of_element_located(locator))

# -----------------------
# Robust click
# -----------------------
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
        time.sleep(0.5 * attempt)

    raise ClickException(f"All click attempts failed for {locator}: {last_err}")

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
            time.sleep(0.4)
    raise RuntimeError(f"Failed to send keys to {locator}: {last_err}")

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
# Downloads: wait+rename with retries
# -----------------------
def wait_for_download_complete(driver: WebDriver, download_dir: str, bond_code: str,
                               download_btn_locator: Tuple[str, str], max_attempts: int = 5):
    """
    Robust download wait:
    - Re-find and click the download button
    - Wait for .doc/.docx file to appear
    - If timeout, re-click and retry
    """

    for attempt in range(1, max_attempts + 1):

        logger.info(f"Attempt {attempt}/{max_attempts}: Clicking download button...")

        try:
            safe_click(driver, download_btn_locator)   # ✔ FIX: pass WebElement, not locator
        except Exception as click_err:
            logger.warning(
                f"Failed to click download button on attempt {attempt}: {click_err}"
            )

        # Now wait for file to appear
        logger.info("Waiting for downloaded file to appear...")
        time_waited = 0
        found_file = None

        while time_waited < 30:
            files = (glob.glob(os.path.join(download_dir, "*.doc")) +
                     glob.glob(os.path.join(download_dir, "*.docx")))

            doc_files = []
            for f in files:
                base = os.path.basename(f)
                if base.endswith(".crdownload"):
                    continue
                if re.search(r"（\d{6}\.[A-Z]{2}）", base):
                    continue
                doc_files.append(f)

            if doc_files:
                found_file = doc_files[0]
                break

            time.sleep(1)
            time_waited += 1

        if found_file:
            # rename
            ext = os.path.splitext(found_file)[1]
            new_file = os.path.join(download_dir, f"{sanitize_filename(bond_code)}{ext}")
            try:
                os.rename(found_file, new_file)
                logger.info(f"Download complete → {new_file}")
            except Exception as e:
                logger.warning(f"Rename failed: {e}")
                return found_file

            return new_file

        logger.warning("No downloaded file found — retrying button click...")

    # After max attempts
    raise TimeoutException("Download did not complete after multiple attempts.")


# -----------------------
# Zip and cleanup
# -----------------------
def zip_files(d_files: list[str], out_dir: str, date_str: str) -> Optional[str]:
    if not d_files:
        logger.warning("zip_files called with empty list")
        return None
    docx_files = [f for f in d_files if os.path.exists(f) and (f.lower().endswith('.docx') or f.lower().endswith('.doc'))]
    if not docx_files:
        logger.warning("No doc/docx files found to zip")
        return None
    zip_filename = os.path.join(out_dir, f"{date_str}.zip")
    with zipfile.ZipFile(zip_filename, "w") as zf:
        for f in docx_files:
            zf.write(f, arcname=os.path.basename(f))
    logger.info(f"Created zip: {zip_filename}")
    # try remove originals
    for f in docx_files:
        try:
            os.remove(f)
            logger.debug(f"Deleted original file after zipping: {f}")
        except Exception as e:
            logger.warning(f"Failed deleting '{f}': {e}")
    return zip_filename

# -----------------------
# Wait-for-answer / download button detector
# -----------------------
def wait_for_answer(driver: WebDriver, timeout: int = 600) -> Tuple[By, str]:
    """
    Wait until the answer is finished by waiting for the download button to appear.
    DOES NOT CLICK the button. Only returns the locator.
    """
    download_btn_locator = (By.CSS_SELECTOR, "div.btn-download-file")

    logger.info("Waiting for answer to finish (waiting for download button)...")

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(download_btn_locator)
        )
        logger.info("Download button appeared — answer finished.")
    except TimeoutException:
        raise TimeoutException("Download button did not appear — answer not finished in time.")

    return download_btn_locator

# -----------------------
# Login flow
# -----------------------
def _login(driver: WebDriver):
    wait = WebDriverWait(driver, 30)
    try:
        tab_locator = (By.XPATH, "//a[contains(text(),'账号密码登录')]")
        safe_click(driver, tab_locator)
        logger.info("Clicked 账号密码登录")
        time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Could not click account-password tab: {e}")

    phone_locator = (By.XPATH, "//input[@placeholder='请输入手机号']")
    pwd_locator = (By.XPATH, "//input[@placeholder='请输入密码']")

    safe_send_keys(driver, phone_locator, PHONE_NUMBER)
    logger.info("Entered phone number")
    time.sleep(0.5)
    safe_send_keys(driver, pwd_locator, PASSWORD)
    logger.info("Entered password")
    time.sleep(0.5)

    login_btn_locator = (By.XPATH, "//div[contains(text(),'登录') or contains(text(),'登 录') or contains(@class,'login-btn')]")
    safe_click(driver, login_btn_locator)
    logger.info("Clicked 登录 button")
    time.sleep(1)

# -----------------------
# The main process_single_stock function
# -----------------------
def process_single_stock(driver: WebDriver, stock: str, download_dir: str = DOWNLOAD_DIR) -> str:
    """
    Entire flow for one stock:
      - click '联网搜索' (retries)
      - send multi-part prompt
      - wait for answer (download btn)
      - trigger download + wait/rename file
    Returns: path to downloaded file
    """
    # 1) Click '联网搜索'
    net_search_locator = (By.XPATH, "//div[contains(@class, 'btn-item')]//span[text()='联网搜索']/..")
    max_net_search_attempts = 5
    for attempt in range(1, max_net_search_attempts + 1):
        try:
            safe_click(driver, net_search_locator, max_attempts=3, wait_time=10)
            logger.info("Clicked '联网搜索'")
            time.sleep(0.8)
            break
        except Exception as e:
            logger.warning(f"'联网搜索' click failed (attempt {attempt}/{max_net_search_attempts}): {e}")
            time.sleep(0.5)
    else:
        logger.warning("Proceeding even though '联网搜索' couldn't be clicked.")

    # 2) Prepare prompt parts (split to reduce accidental auto-submit truncation)
    PROMPT_PARTS = [
        f"你是一位专业的股票研究员，同时兼具很强的批判性思维和对各种可能性的广泛接纳价值观，你目前的主要工作是深入挖掘上市公司{stock}最新的业务进展和可能的潜在的业务机会，以回答为什么近期该股票涨幅明显。你的任务包括：",
        "1. 收集该股票的基本信息（公司名称、行业、主营业务、同类公司、创始人简介等）。",
        "2. 挖掘现有业务中，近期可能公布或已公开的最新业务进展情况，请给出具体信息与来源。",
        "3. 挖掘正在筹备或刚开始推进的业务，且有望在基础业务之外形成新的业绩增长点，请给出具体信息与来源。",
        "4. 如果有新的业务订单，也请提供相关信息与来源。",
        "5. 挖掘新闻网页、公众号、雪球论坛等各种网络信息中提到的该公司的某些新的业务潜力。",
        "6. 总结以上业务进展和可能的业务增长点，与近期股票市场的投资热点作比对，回答近期股价上涨可能是跟哪些现有业务或潜在业务有关系。",
        "以上每一条查找的信息能找到相关信息的发布日期的，在该信息后按以下格式注明（该信息发布日期为yyyymmdd）。",
        "若现有公开渠道无法查询到相关信息，请明确说明“不清楚”，不要进行推测或凭空补充。",
        "输出内容需有条理、逻辑清晰，用简洁易懂的语言呈现。",
    ]

    # 3) Send prompt to textarea safely
    textarea_locator = (By.CSS_SELECTOR, "textarea.el-textarea__inner")
    max_prompt_attempts = 6
    for attempt in range(1, max_prompt_attempts + 1):
        try:
            # re-find, click, clear, send parts
            safe_click(driver, textarea_locator, max_attempts=3, wait_time=10)
            elem = find_fresh(driver, textarea_locator, timeout=10)
            try:
                elem.clear()
            except Exception:
                pass
            for part in PROMPT_PARTS:
                elem.send_keys(part)
                time.sleep(0.6)
            # submit
            elem.send_keys(Keys.ENTER)
            logger.info(f"Prompt sent for {stock}")
            break
        except Exception as e:
            logger.warning(f"Failed to send prompt (attempt {attempt}/{max_prompt_attempts}): {e}")
            time.sleep(1)
    else:
        raise RuntimeError("Failed to send prompt after multiple attempts")

    # 4) Wait for answer & get download locator
    download_locator = wait_for_answer(driver, timeout=600)
    time.sleep(0.8)

    # 5) Trigger download & wait_for_download (rename to stock)
    downloaded_file = wait_for_download_complete(driver, download_dir, stock, download_btn_locator=download_locator)
    logger.info(f"Downloaded file for {stock}: {downloaded_file}")
    return downloaded_file

# -----------------------
# Navigation / login helper
# -----------------------
def to_paipai(driver: WebDriver):
    driver.get(LOGIN_URL)
    logger.info("Loaded login URL, waiting for login box...")

    # Add random wait to avoid login conflict
    wait_secs = random.uniform(1, 8)
    logger.info(f"Random wait before login ({wait_secs:.2f} seconds) to avoid conflicts")
    time.sleep(wait_secs)
    # Wait for login box presence
    max_login_box_wait = 10
    for attempt in range(max_login_box_wait):
        try:
            WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.login-box")))
            logger.info("Login box detected.")
            break
        except TimeoutException:
            logger.info("No login box yet, retrying...")
            time.sleep(2)
    else:
        raise RuntimeError("Login box did not appear after several attempts")

    # Try logging in and wait for dialog to disappear
    max_login_attempts = 10
    for attempt in range(1, max_login_attempts + 1):
        try:
            # If login box gone, assume success
            if WebDriverWait(driver, 8).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.login-box"))):
                logger.info("Login box invisible — login success")
                break
        except TimeoutException:
            logger.info("Performing login attempt...")
            _login(driver)
            time.sleep(1)
    else:
        raise RuntimeError("Login did not complete after multiple attempts")

    # Post-login popups
    close_popup(driver, "div.search-switch-guide-close-btn")
    close_popup(driver, "div.img-box .btn-bottom")
    # Add any other popup selectors you commonly encounter:
    # close_popup(driver, "button:contains('知道了')")

# -----------------------
# Example: run as script
# -----------------------

if __name__ == "__main__":
    """
    Example usage:
      python paipai_scraper.py
    This will open a browser, login, and process a sample stock.
    """
    sample_stock = "*ST张股（000430.SZ）"  # replace with your target
    driver = None
    try:
        driver = get_chrome_driver(DOWNLOAD_DIR, "")
        to_paipai(driver)
        downloaded = process_single_stock(driver, sample_stock, download_dir=DOWNLOAD_DIR)
        logger.info(f"Finished. Downloaded file: {downloaded}")
    except Exception as e_main:
        logger.exception(f"Main flow failed: {e_main}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass