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


