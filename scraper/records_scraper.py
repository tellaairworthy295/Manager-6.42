
from datetime import date, datetime
from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page
from pwright.async_pm import AsyncPlaywrightManager
from playwright.async_api import Page
from typing import Dict
from utils.database import get_db_manager, RecordCommentRepository, RecordMeetingRepository
import asyncio
from utils.logging_config import get_records_scraper_logger

logger = get_records_scraper_logger()


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

    db_manager = get_db_manager()

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


def _extract_title_set(raw_titles):
    """Helper to extract a set of titles from the DB query result."""
    if raw_titles and isinstance(raw_titles[0], tuple):
        return set(title_tuple[0] for title_tuple in raw_titles if title_tuple and title_tuple[0])
    else:
        return set(title for title in raw_titles if title)


async def scrape_website(storage_state: str, url: str, locators: dict):
    record_type = locators.get("record_type", "unknown")
    db_manager = get_db_manager()

    if record_type.lower() == "comment":
        repo_record = RecordCommentRepository(db_manager)
        raw_titles = repo_record.get_all_titles_today()
        titles = _extract_title_set(raw_titles)
    elif record_type.lower() == "meeting":
        repo_record = RecordMeetingRepository(db_manager)
        raw_titles = repo_record.get_all_titles_today()
        titles = _extract_title_set(raw_titles)
    else:
        raise ValueError(f"Unsupported record_type: {record_type}. Expected 'comment' or 'meeting'.")

    manager = AsyncPlaywrightManager()
    await manager.start()

    result = []
    try:
        async with PlaywrightContext(
                manager,
                storage_state=storage_state,
                accept_downloads=True,
                ignore_https_errors=True,
        ) as context:
            page = await new_stealth_page(context)
            await page.goto(url, wait_until="networkidle")
            await page.click(locators["filter_click"])
            await scroll_to_bottom(page, locators["scroll_container"], locators["bottom_flag"])

            # For meetings: iterate by index, re-query each time to avoid stale handles
            if record_type.lower() == "meeting":
                # Get total count first
                total_items = len(await page.query_selector_all(locators["record_list_item"]))
                logger.info(f"Total meeting items found: {total_items}")

                for i in range(total_items):
                    # Re-query every iteration because go_back() re-renders the DOM
                    current_items = await page.query_selector_all(locators["record_list_item"])
                    if i >= len(current_items):
                        logger.warning(f"Item index {i} out of range after re-query (total={len(current_items)})")
                        break

                    list_item = current_items[i]
                    title_element = await list_item.query_selector(locators["title_click"])
                    if not title_element:
                        logger.warning(f"No title element in item {i}")
                        continue

                    title = await title_element.text_content()
                    if title and title.strip() in titles:
                        logger.info(f"Skipping existing title: '{title.strip()}'")
                        continue

                    logger.info(f"Scraping meeting item {i}: '{title.strip() if title else 'N/A'}'")
                    scraped = await scrape_meeting_new_tab(context, page, list_item, locators["properties"])
                    if scraped:
                        result.append(scraped)

                    await asyncio.sleep(0.7)

            elif record_type.lower() == "comment":
                list_items = await page.query_selector_all(locators["record_list_item"])
                for i, list_item in enumerate(list_items):
                    title_element = await list_item.query_selector(locators["title_click"])
                    if not title_element:
                        logger.warning(f"Could not find title element in list item {i}")
                        continue

                    title = await title_element.text_content()
                    if title and title.strip() in titles:
                        logger.info(f"Skipping existing title: '{title.strip()}'")
                        continue

                    await title_element.click()
                    result.append(
                        await scrape_popup(page, locators["record_property_wrapper"], locators["properties"])
                    )
                    await close_popup(page, locators["close_click"])

                    await asyncio.sleep(0.7)

    finally:
        await insert_scraped_data(result, record_type)
        await manager.shutdown()


async def scrape_meeting_new_tab(context, page, list_item, properties: dict) -> Dict | None:
    """
    Click a meeting row, wait for Vue Router SPA navigation,
    scrape the detail page, then navigate back.
    """
    try:
        # The real clickable element is the innermost span, not the outer wrapper
        # el-tooltip span is what Vue's event delegation actually tracks
        inner_span = await list_item.query_selector("span.el-tooltip")
        if not inner_span:
            # fallback to the row itself
            inner_span = list_item

        # Scroll the element into view within its custom scroll container
        await inner_span.evaluate("""el => {
            // Walk up to find the scrollable container and scroll el into view within it
            el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'nearest' });
        }""")
        await page.wait_for_timeout(400)

        # Verify it's now in a positive Y position before clicking
        bbox = await inner_span.bounding_box()
        logger.debug(f"Inner span bbox before click: {bbox}")

        if bbox and bbox["y"] < 0:
            logger.warning(f"Element still has negative Y after scrollIntoView: {bbox}")

        # Wait for Vue Router navigation (URL will change)
        async with page.expect_navigation(wait_until="networkidle", timeout=300000):
            await inner_span.click()

        logger.debug(f"Navigated to: {page.url}")

        data = await scrape_meeting_page(page, properties)
        return data

    except Exception as e:
        logger.error(f"Failed to scrape meeting page: {e}")
        return None

    finally:
        # Navigate back to the listing page
        try:
            await page.go_back(wait_until="networkidle", timeout=10000)
        except Exception as e:
            logger.warning(f"go_back failed: {e}")


async def scrape_meeting_page(page: Page, properties: dict) -> Dict:
    """
    Scrape all relevant fields from a meeting detail page.
    Schema fields (from RecordMeeting table):
        title, summary, q_a_section, institution, sector,
        stock_name, meeting_time, host_personnel, guest_speaker, scraped_at
    """
    # Wait for a stable anchor element before scraping
    await page.wait_for_selector(properties["title"], state="visible", timeout=10000)

    title           = await get_element_content(page, properties["title"])
    summary         = await get_element_content(page, properties["summary"])
    q_a_section     = await get_element_content(page, properties["q_a_section"], required=False)
    institution     = await get_element_content(page, properties["institution"], required=False)
    stock_name      = await get_element_content(page, properties["stock_name"], required=False)
    meeting_time    = await get_element_content(page, properties["meeting_time"])
    host_personnel  = await get_element_content(page, properties["host_personnel"])
    guest_speaker   = await get_element_content(page, properties["guest_speaker"])

    # sector can be multi-valued (same pattern as comment's category)
    sector_elements = await page.query_selector_all(properties["sector"])
    sector_list = []
    for se in sector_elements:
        txt = await se.text_content()
        txt = txt.strip() if txt else ""
        if txt:
            sector_list.append(txt)
    sector = "|".join(sector_list)

    return {
        "title":          title,
        "summary":        summary,
        "q_a_section":    q_a_section,
        "institution":    institution,
        "sector":         sector,
        "stock_name":     stock_name,
        "meeting_time":   meeting_time,
        "host_personnel": host_personnel,
        "guest_speaker":  guest_speaker,
        "scraped_at":     datetime.now(),
        "date":           date.today(),
    }


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


async def get_element_content(page: Page, selector: str, required: bool = True):
    try:
        # wait_for_selector waits (up to 5s) for the element to actually exist in the DOM
        element = await page.wait_for_selector(selector, state='attached', timeout=5000)
        if element:
            raw = await element.text_content()

            # Single Page Applications sometimes render the element, then inject the text a split second later.
            # If it's empty, we wait half a second and check one more time.
            if not raw or not raw.strip():
                await page.wait_for_timeout(500)
                raw = await element.text_content()

            if not raw or not raw.strip():
                raise Exception(f"{selector}: no content")

            logger.info(f'{raw.strip()}')
            return raw.strip()

        raise Exception(f'{selector} not found')
    except Exception as e:
        if required:
            raise Exception(f'{selector} not found. Details: {str(e)}')
        return None


async def intercept_meeting_api(page: Page, list_item, properties: dict) -> Dict | None:
    """
    Capture the API call triggered by clicking a meeting row,
    then call that API directly to get the data.
    """
    captured_requests = []
    captured_responses = []

    async def on_request(request):
        # Capture XHR/fetch calls (not static assets)
        if request.resource_type in ("xhr", "fetch"):
            url = request.url
            # Filter for likely detail API endpoints
            if any(kw in url for kw in ["detail", "meeting", "report", "article", "paipai"]):
                captured_requests.append({
                    "url": url,
                    "method": request.method,
                    "headers": request.headers,
                    "post_data": request.post_data,
                })
                logger.debug(f"Captured request: {request.method} {url}")

    async def on_response(response):
        if response.request.resource_type in ("xhr", "fetch"):
            url = response.url
            if any(kw in url for kw in ["detail", "meeting", "report", "article", "paipai"]):
                try:
                    body = await response.json()
                    captured_responses.append({
                        "url": url,
                        "status": response.status,
                        "body": body,
                    })
                    logger.debug(f"Captured response: {response.status} {url}")
                except Exception:
                    pass  # Not JSON

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        # Use JS click to bypass Sensors Data click-coordinate tracking
        title_span = await list_item.query_selector("span.el-tooltip")
        if title_span:
            await title_span.evaluate("el => el.click()")
        else:
            await list_item.evaluate("el => el.click()")

        # Wait for the API call to complete
        await page.wait_for_timeout(3000)

        logger.debug(f"Total captured responses: {len(captured_responses)}")
        for r in captured_responses:
            logger.debug(f"  {r['url']}: {str(r['body'])[:200]}")

        return captured_responses  # Return raw for inspection first

    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)


async def scrape_popup(page: Page, dialog_selector: str, properties: dict) -> Dict:
    """
    Scrape all relevant data in the popup for insertion into record_comment table.
    Schema fields (from RecordComment table):
        date, author, team, industry, category, comment, title, scraped_at
    """

    # Wait for the popup wrapper to be visible
    await page.wait_for_selector(dialog_selector, state='visible')

    # The updated `get_element_content` will now automatically wait for these to render
    title = await get_element_content(page, f"{dialog_selector} {properties['title']}")
    author = await get_element_content(page, f"{dialog_selector} {properties['author']}")
    team = await get_element_content(page, f"{dialog_selector} {properties['team']}", False)
    industry = await get_element_content(page, f"{dialog_selector} {properties['industry']}", False)
    comment = await get_element_content(page, f"{dialog_selector} {properties['comment']}")

    # Category logic
    category_selector = f"{dialog_selector} {properties['category']}"
    try:
        # Wait for at least one category to render so query_selector_all doesn't return empty instantly
        await page.wait_for_selector(category_selector, state='attached', timeout=3000)
    except:
        pass  # Proceed anyway in case a comment legitimately has no categories

    category_elements = await page.query_selector_all(category_selector)
    category_list = []
    for ce in category_elements:
        txt = await ce.text_content()
        txt = txt.strip() if txt else ""
        if txt:
            category_list.append(txt)
    category = "|".join(category_list)

    return {
        "date": date.today(),
        "author": author,
        "team": team,
        "industry": industry,
        "category": category,
        "comment": comment,
        "title": title,
        "scraped_at": datetime.now(),
    }


async def close_popup(page: Page, close_click_selector: str):
    """Close the popup window by clicking the close button"""
    close_button = await page.wait_for_selector(close_click_selector, timeout=5000)
    if close_button:
        await close_button.click()
        # Wait for the popup to disappear
        await page.wait_for_selector(f"{close_click_selector}", state='hidden', timeout=2000)