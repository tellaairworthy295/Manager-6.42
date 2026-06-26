import re
import time
from datetime import date, datetime, timedelta
from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page
from pwright.async_pm import AsyncPlaywrightManager
from playwright.async_api import Page
from typing import Dict, Optional
from utils.database import get_db_manager, RecordCommentRepository, RecordMeetingRepository
import asyncio

from utils.interactive import async_safe_click
from utils.logging_config import get_records_scraper_logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = get_records_scraper_logger()
RATE_LIMIT = 210

class RateLimitException(Exception):
    def __init__(self, message: str, status_code: int = 429):
        self.message = message
        self.status_code = status_code

async def insert_scraped_data(data_list: list, record_type: str):
    """
    Inserts scraped data into the appropriate database repository based on the record type.

    Args:
        data_list: A list of dictionaries containing the scraped data.
        record_type: The type of record ('comment' or 'meeting').
    """
    if not data_list:
        logger.debug("No data to insert.")
        return

    db_manager = get_db_manager('innovationdb')

    # Select the appropriate repository based on the record type
    if record_type.lower() == "comment":
        repo = RecordCommentRepository(db_manager)
        insert_method = repo.insert_or_update_comments_batch
    elif record_type.lower() == "meeting":
        repo = RecordMeetingRepository(db_manager)
        insert_method = repo.insert_or_update_meetings_batch
    else:
        raise ValueError(f"Unsupported record_type: {record_type}. Expected 'comment' or 'meeting'.")

    logger.info(f"Inserting {len(data_list)} {record_type} records into the database.")
    insert_method(data_list)


async def scrape_history(storage_state: str, url: str, locators: dict, start: date, end: date):
    record_type = locators.get("record_type", "unknown")
    db_manager = get_db_manager('innovationdb')

    if record_type.lower() == "comment":
        repo_record = RecordCommentRepository(db_manager)
    elif record_type.lower() == "meeting":
        repo_record = RecordMeetingRepository(db_manager)
    else:
        raise ValueError(f"Unsupported record_type: {record_type}")

    manager = AsyncPlaywrightManager()
    await manager.start()

    result = []
    async with PlaywrightContext(
            manager,
            storage_state=storage_state,
            accept_downloads=True,
            ignore_https_errors=True,
            viewport={"width": 1920, "height": 1080},
    ) as context:
        page = await new_stealth_page(context)
        await page.goto(url, wait_until="networkidle")
        time.sleep(15)
        await scroll_to_bottom(page, locators["scroll_container"], locators["bottom_flag"])
        # Wait for the list container to have children with a specific timeout
        try:
            await page.wait_for_function(
                f"document.querySelector('{locators['record_list_item']}').parentElement.children.length > 0",
                timeout=10000  # 10 second timeout
            )
        except PlaywrightTimeoutError:
            logger.warning(f"[{record_type}] Timed out waiting for list items to appear.")
            return result  # Exit early if no items appear after timeout

        # Now query for the items
        list_items = await page.query_selector_all(locators["record_list_item"])

        # If still no items after the wait, return early
        if not list_items:
            logger.info(f"[{record_type}] No list items found after scrolling.")
            return result

        try:
            for _ in range(3):  # Retry loop to handle potential transient issues``
                try:
                    result = []
                    timeout_err = False
                    hit_limit = False
                    count = 0
                    titles = repo_record.get_titles_in_range(start, end)
                    for i, list_item in enumerate(list_items):
                        # if count >= RATE_LIMIT:
                        #     raise RateLimitException("custom rate limit hit, sleep one hour...")
                        title_element = await list_item.query_selector(locators["title_click"])
                        title_locator = f"{locators['record_list_item']}:nth-child({i + 1}) {locators['title_click']}"
                        if not title_element:
                            logger.warning(f"[{record_type}] No title element in item {i}")
                            continue

                        title = await title_element.text_content()
                        if title and title.strip() in titles:
                            logger.info(f"[{record_type}] Skipping: '{title.strip()}'")
                            continue

                        if record_type.lower() == "meeting":
                            scraped = await scrape_meeting_new_tab(context, title_element, locators["properties"])
                            if scraped:
                                result.append(scraped)
                                count += 1

                        elif record_type.lower() == "comment":

                            try:
                                await async_safe_click(page, title_locator, timeout=10000, max_attempts=3)
                            except Exception as e:
                                logger.warning(f"failed to click {title_locator}: {e}")
                                continue
                            data = await scrape_popup(page, locators["record_property_wrapper"], locators["properties"])
                            if data:
                                result.append(data)
                                count += 1

                        await asyncio.sleep(0.6)
                except PlaywrightTimeoutError:
                    break  # Exit the retry loop if a timeout error occurred
                # except RateLimitException:
                #     hit_limit = True
                finally:
                    await insert_scraped_data(result, record_type)
                    # elif hit_limit:
                    #     time.sleep(60*60)
        finally:
            await manager.shutdown()



async def scrape_website(storage_state: str, url: str, locators: dict):
    record_type = locators.get("record_type", "unknown")
    db_manager = get_db_manager('innovationdb')

    if record_type.lower() == "comment":
        repo_record = RecordCommentRepository(db_manager)
    elif record_type.lower() == "meeting":
        repo_record = RecordMeetingRepository(db_manager)
    else:
        raise ValueError(f"Unsupported record_type: {record_type}")

    yesterday = date.today() - timedelta(days=1)
    today = date.today()
    titles = repo_record.get_titles_in_range(yesterday, today)
    manager = AsyncPlaywrightManager()
    await manager.start()

    result = []
    try:
        async with PlaywrightContext(
                manager,
                storage_state=storage_state,
                accept_downloads=True,
                ignore_https_errors=True,
                viewport={"width": 1920, "height": 1080},
        ) as context:
            page = await new_stealth_page(context)
            await page.goto(url, wait_until="networkidle")
            time.sleep(5)
            try:
                await async_safe_click(page, locators["popup_click"])
            except:
                pass
            await page.click(locators["filter_click"])
            await scroll_to_bottom(page, locators["scroll_container"], locators["bottom_flag"])

            # Wait for the list container to have children with a specific timeout
            try:
                await page.wait_for_function(
                    f"document.querySelector('{locators['record_list_item']}').parentElement.children.length > 0",
                    timeout=10000  # 10 second timeout
                )
            except PlaywrightTimeoutError:
                logger.warning(f"[{record_type}] Timed out waiting for list items to appear.")
                return result  # Exit early if no items appear after timeout

            # Now query for the items
            list_items = await page.query_selector_all(locators["record_list_item"])

            # If still no items after the wait, return early
            if not list_items:
                logger.info(f"[{record_type}] No list items found after scrolling.")
                return result

            for i, list_item in enumerate(list_items):
                title_element = await list_item.query_selector(locators["title_click"])
                title_locator = f"{locators['record_list_item']}:nth-child({i + 1}) {locators['title_click']}"
                if not title_element:
                    logger.warning(f"[{record_type}] No title element in item {i}")
                    continue

                title = await title_element.text_content()
                if title and title.strip() in titles:
                    logger.info(f"[{record_type}] Skipping: '{title.strip()}'")
                    continue

                if record_type.lower() == "meeting":
                    scraped = await scrape_meeting_new_tab(context, title_element, locators["properties"])
                    if scraped:
                        result.append(scraped)

                elif record_type.lower() == "comment":

                    try:
                        await async_safe_click(page, title_locator, timeout=10000, max_attempts=3)
                    except Exception as e:
                        logger.warning(f"failed to click {title_locator}: {e}")
                        continue

                    data = await scrape_popup(page, locators["record_property_wrapper"], locators["properties"])
                    if data:
                        result.append(data)

                await asyncio.sleep(0.6)

    finally:
        await insert_scraped_data(result, record_type)
        await manager.shutdown()


async def scrape_meeting_new_tab(context, title_locator, properties: dict) -> Dict | None:
    """
    Click a meeting row, catch the new tab it opens via window.open(),
    scrape the detail page, then close the tab.
    """
    new_page = None
    try:
        # The click opens a new tab via window.open() — catch it at context level
        async with context.expect_page() as new_page_info:
            try:
                await async_safe_click(target=title_locator, max_attempts=3, timeout=10000)
            except:
                return None

        new_page = await new_page_info.value
        logger.debug(f"New tab opened, URL: {new_page.url}")

        # Wait for the new tab to finish loading
        await new_page.wait_for_load_state("domcontentloaded")
        await new_page.wait_for_load_state("networkidle", timeout=10000)
        logger.debug(f"New tab fully loaded: {new_page.url}")

        try:
            data = await scrape_meeting_page(new_page, properties)
            return data
        except PlaywrightTimeoutError as e:
            logger.warning(f"rate limit hit: {e}")
            raise
        except Exception as e:
            logger.warning(e)

    finally:
        # Always close the new tab — never touch the original page
        if new_page:
            try:
                await new_page.close()
                logger.debug("New tab closed.")
            except Exception as e:
                logger.warning(f"Failed to close new tab: {e}")


async def scrape_meeting_page(page: Page, properties: dict) -> Dict:
    """
    Scrape all relevant fields from a meeting detail page.
    Schema fields (from RecordMeeting table):
        title, summary, q_a_section, institution, sector,
        stock_name, meeting_time, host_personnel, guest_speaker, scraped_at
    """
    # Wait for a stable anchor element before scraping
    await page.wait_for_selector(properties["title"], state="visible", timeout=5000)

    # --- Standard content scraping ---
    title = await get_element_content(page, properties["title"])
    logger.info(f"scraping {title}")
    summary = await get_element_content(page, properties["summary"], required=False)
    q_a_section = await get_element_content(page, properties["q_a_section"], required=False)
    institution = await get_element_content(page, properties["institution"], required=False)
    sector = await get_element_content(page, properties["sector"], is_multiple=True, required=False)
    stock_name = await get_element_content(page, properties["stock_name"], is_multiple=True, required=False)
    # ---------------------------------

    # --- Personnel scraping (specific logic) ---
    host_personnel = ""
    guest_speaker = ""

    personnel_elements = await page.query_selector_all(properties["personnel_list"])
    for elem in personnel_elements:
        raw_text = await elem.text_content()
        if raw_text:
            clean_text = raw_text.strip()
            if clean_text.startswith("主持人员："):
                host_personnel = clean_text[len("主持人员："):].strip()
            elif clean_text.startswith("路演嘉宾："):
                guest_speaker = clean_text[len("路演嘉宾："):].strip()
    # ------------------------------------------

    # --- Meeting time scraping with parsing ---
    meeting_time_raw_text = await get_element_content(page, properties["meeting_time_raw"], required=True)
    time_match = re.search(r'会议时间：\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', meeting_time_raw_text)
    meeting_time_str = time_match.group(1) if time_match else ""

    meeting_time: datetime | None = None
    if meeting_time_str:
        try:
            if len(meeting_time_str) == 16:  # Format is "YYYY-MM-DD HH:MM"
                meeting_time_str += ":00"
            meeting_time = datetime.strptime(meeting_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print(f"Warning: Could not parse meeting time: '{meeting_time_str}'")
            meeting_time = None
    # ------------------------------------------

    return {
        "title": title,
        "summary": summary,
        "q_a_section": q_a_section,
        "institution": institution,
        "sector": sector,
        "stock_name": stock_name,
        "meeting_time": meeting_time,
        "host_personnel": host_personnel,
        "guest_speaker": guest_speaker,
        "scraped_at": datetime.now(),
        "date": meeting_time.date() if meeting_time else date.today(),
    }


async def scrape_popup(page: Page, dialog_selector: str, properties: dict) -> Dict:
    """
    Scrape all relevant data in the popup for insertion into record_comment table.
    Schema fields (from RecordComment table):
        date, author, team, industry, category, comment, title, comment_time, scraped_at
    """
    try:
        # Wait for the popup wrapper to be visible
        await page.wait_for_selector(dialog_selector, state='visible', timeout=8000)

        # The updated `get_element_content` will now automatically wait for these to render
        title = await get_element_content(page, f"{dialog_selector} {properties['title']}")
        logger.info(f"scraping {title}")
        author = await get_element_content(page, f"{dialog_selector} {properties['author']}")
        team = await get_element_content(page, f"{dialog_selector} {properties['team']}", required=False)
        industry = await get_element_content(page, f"{dialog_selector} {properties['industry']}", required=False)

        # Fixed variable assignments to match schema
        comment = await get_element_content(page, f"{dialog_selector} {properties['comment']}")
        category = await get_element_content(page, f"{dialog_selector} {properties['category']}", required=False,
                                             is_multiple=True)

        time_elapsed_raw = await get_element_content(page, f"{dialog_selector} {properties['time']}")

        # --- Process the relative time string ---
        comment_time = parse_relative_time(time_elapsed_raw)
        # ----------------------------------------

        return {
            "date": comment_time.date(),
            "author": author,
            "team": team,
            "comment_time": comment_time,
            "industry": industry,
            "category": category,
            "comment": comment,
            "title": title,
            "scraped_at": datetime.now(),
        }
    except PlaywrightTimeoutError as e:
        logger.warning(f"rate limit hit: {e}")
        raise
    except Exception as e:
        logger.warning(e)
        return {}
    finally:
        await close_popup(page)


async def scroll_to_bottom(page: Page, scroll_container_selector: str, bottom_flag: str):
    """Scroll to the bottom of the specific scrollable element until '没有更多了' is found"""
    # Target the specific scrollable container
    scroll_container = await page.query_selector(scroll_container_selector)
    if not scroll_container:
        raise Exception("Scroll container not found")

    # Get initial scroll height
    last_height = await scroll_container.evaluate("el => el.scrollHeight")

    timeout = 10000  # 10 seconds timeout
    start_time = asyncio.get_event_loop().time()

    while True:
        # Scroll the specific container to its bottom
        await scroll_container.evaluate("el => el.scrollTop = el.scrollHeight")

        # Wait for new content to load
        await page.wait_for_timeout(1000)

        # Get new scroll height
        new_height = await scroll_container.evaluate("el => el.scrollHeight")

        # Check if we've reached the end
        if new_height == last_height:
            # Check if "没有更多了" is visible under "all-comment-container"
            no_more_element = await page.query_selector(bottom_flag)
            if no_more_element and await no_more_element.is_visible():
                break
            else:
                # Check if we've exceeded the timeout
                if asyncio.get_event_loop().time() - start_time > timeout:
                    break
                continue
        else:
            last_height = new_height

    # Ensure we've scrolled to the bottom of the container
    await scroll_container.evaluate("el => el.scrollTop = el.scrollHeight")


async def get_element_content(
        page: Page,
        selector: str,
        required: bool = True,
        is_multiple: bool = False
) -> Optional[str]:
    """
    Unified function to get content from a single element or multiple elements.
    It waits for elements to appear and handles potential delays in text injection.

    Args:
        page (Page): The Playwright page object.
        selector (str): The CSS selector to search for.
        required (bool): If True, raises an exception if nothing is found.
        is_multiple (bool): If True, queries for multiple elements and joins their content with '|'.
    """
    try:
        element = await page.wait_for_selector(selector, state='attached', timeout=5000)
        if is_multiple:
            elements = await page.query_selector_all(selector)

            if not elements:
                if required:
                    raise Exception(f"No elements found for selector: {selector}")
                return None

            contents = []
            for element in elements:
                # Get initial content
                raw = await element.text_content()

                # If content is empty, wait and retry (SPA handling)
                if not raw or not raw.strip():
                    await page.wait_for_timeout(500)
                    raw = await element.text_content()

                if raw and raw.strip():
                    contents.append(raw.strip())

            if not contents:
                if required:
                    raise Exception(f"All elements found for selector '{selector}' had no content.")
                return None

            return "|".join(contents)

        else:  # Single element logic
            if not element:
                raise Exception(f'Selector not found: {selector}')

            raw = await element.text_content()

            # Handle potential delay in text injection
            if not raw or not raw.strip():
                await page.wait_for_timeout(500)
                raw = await element.text_content()

            if not raw or not raw.strip():
                raise Exception(f"{selector}: no content")

            # logger.info(f'{raw.strip()}') # Uncomment if logging is desired
            return raw.strip()

    except Exception as e:
        if required:
            raise Exception(f'{selector} not found or error occurred. Details: {str(e)}')
        return None


def parse_relative_time(time_str: str) -> datetime:
    """
    Parses relative time strings like '刚刚', 'xx分钟前', 'xx小时前' into a datetime object.
    """
    now = datetime.now()

    if "刚刚" in time_str:
        return now

    # Match patterns like "xx分钟前" or "xx小时前"
    minutes_match = re.search(r'(\d+)\s*分钟前', time_str)
    hours_match = re.search(r'(\d+)\s*小时前', time_str)
    md_time_match = re.search(r'(\d{1,2})[月\-](\d{1,2})\s+(\d{1,2}):(\d{2})', time_str)
    full_date_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?', time_str)
    if minutes_match:
        minutes_ago = int(minutes_match.group(1))
        return now - timedelta(minutes=minutes_ago)

    if hours_match:
        hours_ago = int(hours_match.group(1))
        return now - timedelta(hours=hours_ago)

    if md_time_match:
        month = int(md_time_match.group(1))
        day = int(md_time_match.group(2))
        hour = int(md_time_match.group(3))
        minute = int(md_time_match.group(4))
        return now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)

    if full_date_match:
        year = int(full_date_match.group(1))
        month = int(full_date_match.group(2))
        day = int(full_date_match.group(3))
        hour = int(full_date_match.group(4)) if full_date_match.group(4) else 0
        minute = int(full_date_match.group(5)) if full_date_match.group(5) else 0

        return datetime(year, month, day, hour, minute)
    # If no pattern matches, return the current time as a fallback
    print(f"Warning: Could not parse relative time string: '{time_str}'. Using current time.")
    return now


async def close_popup(page: Page):
    """Close the popup window by clicking the close button"""
    close_button = await page.wait_for_selector('div.right > i.iconfont.icon-guanbi', timeout=5000)
    if close_button:
        await async_safe_click(page, 'div.right > i.iconfont.icon-guanbi')
        # Wait for the popup to disappear
        await page.wait_for_selector('.el-dialog__body', state='hidden', timeout=5000)
