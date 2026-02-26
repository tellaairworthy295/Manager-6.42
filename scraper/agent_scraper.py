import os
import asyncio
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from exception.exception_handler import NetworkException
from utils.interactive import parse_locators, async_safe_click, async_safe_fill
from utils.logging_config import get_agent_task_logger
from utils.snapshoot import stream_via_mutation_observer, stream_via_selector_polling

logger = get_agent_task_logger()


async def display_agent(page: Page, user_id: str, prompt: list[str], locators, source: str, conversation_id: str, dia_count: int) -> str:

    logger.info(f"snapshooting: {user_id}: {source}")
    url = locators.get("url")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=50000)
    except Exception as e:
        raise NetworkException(f"Network Error. Failed to load {url}: {str(e)}")

    textarea_locator = parse_locators(locators.get("text_box_locator"))
    try:
        await page.wait_for_selector(textarea_locator, timeout=5000, state="visible")
    except:
        wait_for_dynamical = locators.get("wait_for_dynamical")
        if wait_for_dynamical:
            dyn_locator = parse_locators(wait_for_dynamical)
            try:
                await page.wait_for_selector(dyn_locator, timeout=5000, state="visible")
                logger.info("Progressively loaded content is ready.")
            except PlaywrightTimeoutError:
                logger.warning("Wait for progressive content timed out. Proceeding anyway.")

        click_locator = parse_locators(locators.get("click_locator"))
        if click_locator:
            await async_safe_click(page, click_locator, max_attempts=3, timeout=5_000)

    for part in prompt:
        await async_safe_fill(page, textarea_locator, part,
                        max_attempts=3, timeout=3_000, clear_first=False)
        await asyncio.sleep(0.7)

    await page.keyboard.press("Enter")

    finish_locator = parse_locators(locators.get("finish_locator"))
    logger.info(f"Waiting for answer to finish ({source})...")
    if source == 'alphapai':
        await stream_via_mutation_observer(page, source, user_id, conversation_id, finish_locator, dia_count, prompt[0])
    else:
        await stream_via_selector_polling(page, source, user_id, conversation_id, finish_locator, dia_count, prompt[0])


async def process_single_stock(page: Page, stock: str, prompt: list[str],
                               download_dir: str, locators, source: str) -> str:
    """
    Entire flow for one stock:
      - wait for dynamically loaded content (if any)
      - send multi-part prompt (with {stock} replaced)
      - wait for answer (download btn)
      - trigger download/scrape + wait/rename file
    Returns: path to downloaded file
    """
    logger.info(f"processing stock: {stock}")
    url = locators.get("url")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=50000)
    except Exception as e:
        raise NetworkException(f"Network Error. Failed to load {url}: {str(e)}")
    # 0) Wait for progressively loaded content BEFORE any other process
    wait_for_dynamical = locators.get("wait_for_dynamical")
    if wait_for_dynamical:
        logger.info(f"Waiting for progressively loaded content: {wait_for_dynamical}")
        dyn_locator = parse_locators(wait_for_dynamical)
        try:
            await page.wait_for_selector(dyn_locator, timeout=8000, state="visible")
            logger.info("Progressively loaded content is ready.")
        except PlaywrightTimeoutError:
            logger.warning("Wait for progressive content timed out. Proceeding anyway.")

    click_locator = parse_locators(locators.get("click_locator"))
    if click_locator:
        await async_safe_click(page, click_locator, max_attempts=3, timeout=5_000)

    # 1) Prepare prompt with correct {stock} substitution for each part
    if prompt and any("{stock}" in p for p in prompt):
        prompt_for_stock = [p.replace("{stock}", stock) for p in prompt]
    else:
        prompt_for_stock = prompt

    # 2) Send prompt to textarea safely
    textarea_locator = parse_locators(locators.get("text_box_locator"))
    await async_safe_click(page, textarea_locator, max_attempts=3, timeout=3_000)

    for part in prompt_for_stock:
        await async_safe_fill(page, textarea_locator, part,
                        max_attempts=3, timeout=3_000, clear_first=False)
        await asyncio.sleep(1)

    await page.keyboard.press("Enter")
    logger.info(f"Prompt sent for {stock}")
    await asyncio.sleep(15)

    # 3) Wait for answer & get download locator
    finish_locator = parse_locators(locators.get("finish_locator"))
    logger.info("Waiting for answer to finish (waiting for download button)...")
    try:
        await page.wait_for_selector(finish_locator, timeout=1080 * 1000, state="visible")
        logger.info("Finish locator appeared — answer finished.")
    except PlaywrightTimeoutError as e:
        logger.error(f"Timeout waiting for {finish_locator}: {e}")
        raise RuntimeError("Download button did not appear — answer not finished in time.") from e
    await asyncio.sleep(1)

    # 4) Get file
    content_locator = parse_locators(locators.get("content_locator"))
    if content_locator:
        element = page.locator(content_locator).first
        await element.wait_for(state="visible", timeout=30_000)
        full_text = await element.inner_text()
        txt_filename = os.path.join(download_dir, f"{stock}_{source}.txt")
        await asyncio.to_thread(lambda: open(txt_filename, "w", encoding="utf-8").write(full_text))
        result_file = txt_filename
    else:
        async with page.expect_download(timeout=300_000) as download_info:
            await async_safe_click(page, finish_locator, max_attempts=3, timeout=3_000)
        download = await download_info.value
        ext = os.path.splitext(download.suggested_filename)[1]
        dest = os.path.join(download_dir, f"{stock}_{source}{ext}")
        await download.save_as(dest)
        result_file = dest

    logger.info(f"Fetch result file for {stock}: {result_file}")
    return result_file