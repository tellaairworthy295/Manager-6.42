
import os
import time
import glob
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC, wait
from config import setup_logging
from utils.interactive import parse_locators, safe_click, safe_send_keys, generate_localstorage_js

logger = setup_logging("logs/alphapai", "alphapai_scraper")
# ========================= CONFIG =========================   
DEFAULT_TIMEOUT = 20

# -----------------------
# Downloads: wait+rename, only one attempt
# -----------------------
def _wait_for_download_complete(download_dir: str, timeout: int = 30):
    """
    Wait until a new .doc/.docx file finishes downloading in a UNIQUE folder.
    Since the folder is isolated, the first new file is the correct one.
    """

    logger.info(f"Waiting for download to complete in: {download_dir}")

    start_time = time.time()
    downloaded_file = None

    while time.time() - start_time < timeout:
        files = glob.glob(os.path.join(download_dir, "*.doc")) + \
                glob.glob(os.path.join(download_dir, "*.docx"))

        # exclude Chrome .crdownload files
        files = [f for f in files if not f.endswith(".crdownload")]

        if files:
            downloaded_file = files[0]
            break

        time.sleep(3)

    if not downloaded_file:
        raise TimeoutException("Download did not complete within time limit.")
    return downloaded_file

# -----------------------
# Wait-for-answer / download button detector
# -----------------------
def _wait_for_answer(driver: WebDriver, finish_locator, timeout: int = 360):
    """
    Wait until the answer is finished by waiting for the download button to appear.
    If the button appears, clicks it.
    """
    logger.info("Waiting for answer to finish (waiting for download button)...")

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(finish_locator)
        )
        logger.info("Finish locator appeared — answer finished.")
    except TimeoutException:
        raise TimeoutException("Download button did not appear — answer not finished in time.")

# -----------------------
# The main process_single_stock function
# -----------------------
def process_single_stock(driver: WebDriver, stock: str, prompt: list[str], download_dir: str, locators) -> str:
    """
    Entire flow for one stock:
      - send multi-part prompt (with {stock} replaced)
      - wait for answer (download btn)
      - trigger download + wait/rename file
    Returns: path to downloaded file
    """
    logger.info("Refreshed page, processing stock: %s", stock)
    click_locator = parse_locators(locators.get("click_locator"))
    if click_locator:
        safe_click(driver, click_locator, max_attempts=3, wait_time=5)
    # 1) Prepare prompt with correct {stock} substitution for each part
    if prompt and any("{stock}" in p for p in prompt):
        prompt_for_stock = [p.replace("{stock}", stock) for p in prompt]
    else:
        prompt_for_stock = prompt
    # 2) Send prompt to textarea safely
    textarea_locator = parse_locators(locators.get("text_box_locator"))
    safe_click(driver, textarea_locator, max_attempts=3, wait_time=5)
    for part in prompt_for_stock:
        safe_send_keys(driver, textarea_locator, part, max_attempts=3, clear_first=False)
        time.sleep(0.6)
    safe_send_keys(driver, textarea_locator, Keys.ENTER, max_attempts=3, clear_first=False)
    logger.info(f"Prompt sent for {stock}")

    # 3) Wait for answer & get download locator
    finish_locator = parse_locators(locators.get("finish_locator"))
    _wait_for_answer(driver, finish_locator, timeout=360)
    time.sleep(1)

    # 4) Get file
    content_locator = parse_locators(locators.get("content_locator"))
    if content_locator:
        element = wait.until(EC.presence_of_element_located(content_locator))
        logger.info("Located full text")
        full_text = element.text
        txt_filename = os.path.join(download_dir, f"{stock}.txt")
        with open(txt_filename, "w", encoding="utf-8") as f:
            f.write(full_text)
        result_file = txt_filename
    else:
        safe_click(driver, finish_locator, max_attempts=3, wait_time=3)
        logger.info("Clicked the download button.")
        result_file = _wait_for_download_complete(download_dir)
    
    logger.info(f"Fetch result file for {stock}: {result_file}")
    return result_file

def inject_cookies(driver, site_config, url):
    driver.get(url)
    time.sleep(2)
    cookies = site_config["cookies"]
    local_storage = site_config["local_storage"]

    # Add cookies via JavaScript to fix add_cookie failure (esp for cross-domain/format/httponly issues)
    for ck in cookies:
        # Fallback: inject via JavaScript (will only work for non-HttpOnly cookies)
        name = ck.get("name")
        value = ck.get("value")
        domain = ck.get("domain", "")
        path = ck.get("path", "/")
        domain_part = f"; domain={domain}" if domain else ""
        path_part = f"; path={path}" if path else "; path=/"
        js_code = f'document.cookie = "{name}={value}{domain_part}{path_part}";'
        try:
            driver.execute_script(js_code)
        except Exception as js_e:
            print(f"[WARNING] Cookie JS injection failed: {name} ({js_e})")

    print("✓ Device cookies injected")

    localstorage_script = generate_localstorage_js(local_storage)

    # Inject LocalStorage
    if localstorage_script:
        driver.execute_script(localstorage_script)
        print("✓ LocalStorage flags & token injected")
    time.sleep(1)
    driver.refresh()
    time.sleep(20000)
    print("✓ login session fully injected (cookies + localStorage)")


