
import json
import os
import time
import glob
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from config import setup_logging
from utils.interactive import safe_click, safe_send_keys, generate_localstorage_js

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
def _wait_for_answer(driver: WebDriver, timeout: int = 360):
    """
    Wait until the answer is finished by waiting for the download button to appear.
    If the button appears, clicks it.
    """
    finish_locator = (By.CSS_SELECTOR, "div.btn-download-file")

    logger.info("Waiting for answer to finish (waiting for download button)...")

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(finish_locator)
        )
        logger.info("Download button appeared — answer finished.")
        # Now click the button
        safe_click(driver, finish_locator, max_attempts=3, wait_time=3)
        logger.info("Clicked the download button.")
    except TimeoutException:
        raise TimeoutException("Download button did not appear — answer not finished in time.")

# -----------------------
# The main process_single_stock function
# -----------------------
def process_single_stock(driver: WebDriver, stock: str, prompt: list[str], download_dir: str) -> str:
    """
    Entire flow for one stock:
      - send multi-part prompt (with {stock} replaced)
      - wait for answer (download btn)
      - trigger download + wait/rename file
    Returns: path to downloaded file
    """
    logger.info("Refreshed page, processing stock: %s", stock)

    # 1) Prepare prompt with correct {stock} substitution for each part
    if prompt and any("{stock}" in p for p in prompt):
        prompt_for_stock = [p.replace("{stock}", stock) for p in prompt]
    else:
        prompt_for_stock = prompt
    logger.info(prompt_for_stock)
    # 2) Send prompt to textarea safely
    textarea_locator = (By.CSS_SELECTOR, "textarea.el-textarea__inner")
    safe_click(driver, textarea_locator, max_attempts=3, wait_time=5)
    for part in prompt_for_stock:
        safe_send_keys(driver, textarea_locator, part, max_attempts=3, clear_first=False)
        time.sleep(0.6)
    safe_send_keys(driver, textarea_locator, Keys.ENTER, max_attempts=3, clear_first=False)
    logger.info(f"Prompt sent for {stock}")

    # 3) Wait for answer & get download locator
    _wait_for_answer(driver, timeout=600)
    time.sleep(1)

    # 4) Trigger download & wait_for_download (rename to stock)
    downloaded_file = _wait_for_download_complete(download_dir)
    logger.info(f"Downloaded file for {stock}: {downloaded_file}")
    return downloaded_file

def inject_rabyte_login(driver, user_id, TARGET_URL):
    driver.get(TARGET_URL)
    time.sleep(2)

    with open("json/cookies.json", "r") as f:
        config = json.load(f)
    user_config = config[user_id]
    alphapai_config = user_config["alphapai"]
    cookies = alphapai_config["cookies"]
    local_storage = alphapai_config["local_storage"]

    for ck in cookies:
        try:
            driver.add_cookie({
                "name": ck["name"],
                "value": ck["value"],
                "domain": ck["domain"],
                "path": ck["path"]
            })
        except Exception as e:
            print("Cookie injection error:", ck["name"], e)

    print("✓ Device cookies injected")

    # Keys stored in alphapai root
    local_storage_keys = [
        "USER_AUTH_TOKEN",
        "hasShowpaipaiAnswerRangeGuide",
        "search-to-paipai-guide",
        "paipai-agent-fastsheet-us-guide",
        "pc-admin-side-adv-modal-storage",
        "search-to-paipai-guide-date",
        "version-market-tip"
    ]

    localstorage_script = generate_localstorage_js(local_storage, local_storage_keys)

    # Inject LocalStorage
    driver.execute_script(localstorage_script)
    print("✓ LocalStorage flags & token injected")
    time.sleep(1)
    driver.refresh()
    time.sleep(2)
    print("✓ Rabyte login session fully injected (cookies + localStorage)")
