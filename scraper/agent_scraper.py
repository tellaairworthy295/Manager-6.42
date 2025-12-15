
import os
import time
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from loguru import logger
from utils.interactive import parse_locators, safe_click, safe_fill

# Configure loguru for agent scraper module
logger.add("logs/agent_scraper/agent_scraper_{time:YYYY-MM-DD}.log", rotation="00:00", retention="15 days", encoding="utf-8")

# -----------------------
# Wait-for-answer / download button detector
# -----------------------
def process_single_stock(page: Page, stock: str, prompt: list[str], download_dir: str, locators) -> str:
    """
    Entire flow for one stock:
      - wait for dynamically loaded content (if any)
      - send multi-part prompt (with {stock} replaced)
      - wait for answer (download btn)
      - trigger download/scrape + wait/rename file
    Returns: path to downloaded file
    """
    logger.info("processing stock: %s", stock)
    url = locators.get("url")
    page.goto(url, wait_until="domcontentloaded", timeout=8000)
    # 0) Wait for progressively loaded content BEFORE any other process
    wait_for_dynamical = locators.get("wait_for_dynamical")
    if wait_for_dynamical:
        logger.info(f"Waiting for progressively loaded content: {wait_for_dynamical}")
        dyn_locator = parse_locators(wait_for_dynamical)
        try:
            page.wait_for_selector(dyn_locator, timeout=8000, state="visible")
            logger.info("Progressively loaded content is ready.")
        except PlaywrightTimeoutError:
            logger.warning("Wait for progressive content timed out. Proceeding anyway.")

    click_locator = parse_locators(locators.get("click_locator"))
    if click_locator:
        safe_click(page, click_locator, max_attempts=3, timeout=5_000)
    # 1) Prepare prompt with correct {stock} substitution for each part
    if prompt and any("{stock}" in p for p in prompt):
        prompt_for_stock = [p.replace("{stock}", stock) for p in prompt]
    else:
        prompt_for_stock = prompt
    # 2) Send prompt to textarea safely
    textarea_locator = parse_locators(locators.get("text_box_locator"))
    safe_click(page, textarea_locator, max_attempts=3, timeout=5_000)
    for part in prompt_for_stock:
        safe_fill(page, textarea_locator, part, max_attempts=3, timeout=5_000, clear_first=False)
        time.sleep(1)
    page.keyboard.press("Enter")
    logger.info(f"Prompt sent for {stock}")
    time.sleep(15)

    # 3) Wait for answer & get download locator
    finish_locator = parse_locators(locators.get("finish_locator"))
    logger.info("Waiting for answer to finish (waiting for download button)...")
    try:
        page.wait_for_selector(finish_locator, timeout=300 * 1000, state="visible")
        logger.info("Finish locator appeared — answer finished.")
    except PlaywrightTimeoutError:
        raise PlaywrightTimeoutError("Download button did not appear — answer not finished in time.")
    time.sleep(1)

    # 4) Get file
    content_locator = parse_locators(locators.get("content_locator"))
    if content_locator:
        element = page.locator(content_locator).first
        element.wait_for(state="visible", timeout=30_000)
        full_text = element.inner_text()
        txt_filename = os.path.join(download_dir, f"{stock}.txt")
        with open(txt_filename, "w", encoding="utf-8") as f:
            f.write(full_text)
        result_file = txt_filename
    else:
        with page.expect_download(timeout=300_000) as download_info:
            safe_click(page, finish_locator, max_attempts=3, timeout=3_000)
        download = download_info.value

        ext = os.path.splitext(download.suggested_filename)[1]
        dest = os.path.join(download_dir, f"{stock}{ext}")
        download.save_as(dest)   # Playwright handles the move
        result_file = dest
    
    logger.info(f"Fetch result file for {stock}: {result_file}")
    return result_file
