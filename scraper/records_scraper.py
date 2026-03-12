from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page
from pwright.async_pm import AsyncPlaywrightManager
from playwright.async_api import Page
from typing import List, Dict
from utils.database import get_db_manager, RecordCommentRepository
import asyncio
from utils.logging_config import get_records_scraper_logger
logger = get_records_scraper_logger()

async def scrape_website(storage_state: str):
    """
    Scrape the website with the given storage_state (cookies)

    Args:
        storage_state: Path to storage_state file or a dict containing cookies

    Returns:
        List of scraped data
    """
    db_manager = get_db_manager()
    repo_record_comment = RecordCommentRepository(db_manager)
    # get_all_titles_today returns a list of tuples: [(title,), ...] if using SQLAlchemy's distinct().all() 
    # So unpack these to get just the strings
    raw_titles = repo_record_comment.get_all_titles_today()
    # handle both [(title,), ...] and [title, ...]
    if raw_titles and isinstance(raw_titles[0], tuple):
        titles = set(title_tuple[0] for title_tuple in raw_titles if title_tuple and title_tuple[0])
    else:
        titles = set(title for title in raw_titles if title)
    
    results = []

    # Create an AsyncPlaywrightManager instance
    manager = AsyncPlaywrightManager()
    await manager.start()

    try:
        # Use the PlaywrightContext as an async context manager
        async with PlaywrightContext(
                manager,
                storage_state=storage_state,
                accept_downloads=True,
                ignore_https_errors=True,
        ) as context:
            # Get a stealth page
            page = await new_stealth_page(context)

            # Navigate to the website
            await page.goto("https://alphapai-web.rabyte.cn/reading/home/comment", wait_until="networkidle")

            # Step 1: Click the "今天" button
            await page.click('span.tab:has-text("今天")')

            # Step 2: Scroll to the bottom of the specific scrollable element until "没有更多了" is found
            await scroll_to_bottom(page)

            # Step 3: Find all list-item elements
            list_items = await page.query_selector_all('div.comment-list-item .list-item')

            # Step 4: Iterate through each record
            for i, list_item in enumerate(list_items):
                # Find the content element within the list-item
                title_element = await list_item.query_selector('div.comment-title .title')
                if title_element:
                    # Directly click the element (no need for extra wrapper)
                    title = await title_element.text_content()
                    # Check if title already in today's titles
                    if title and title.strip() in titles:
                        # skip this item
                        logger.info(f"Skipping existing title in today's comments: '{title.strip()}'")
                        continue

                    await title_element.click()

                    # Wait for the popup to appear
                    await page.wait_for_selector('.el-dialog__body', timeout=5000)

                    # Step 5: Scrape content from the popup
                    scraped_data = await scrape_popup(page)
                    repo_record_comment.insert_or_update_comment(scraped_data)

                    # Close the popup window
                    await close_popup(page)

                    # Add a small delay between clicks
                    await asyncio.sleep(0.7)
                else:
                    logger.warning(f"Warning: Could not find 'div.content' in list item {i}")

    finally:
        await manager.shutdown()


async def scroll_to_bottom(page: Page):
    """Scroll to the bottom of the specific scrollable element until '没有更多了' is found"""
    # Target the specific scrollable container
    scroll_container = await page.query_selector(
        '#app > div > div.app-layout-container > div.app-layout-body > div.app-right-content > div > div.comment-body > div.comment-left'
    )

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
            no_more_element = await page.query_selector('div.all-comment-container > div.no-more')
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


async def scrape_popup(page: Page) -> Dict:
    """
    Scrape all relevant data in the popup for insertion into record_comment table.
    Schema fields (from RecordComment table): 
        date, author, team, industry, category, comment, title, scraped_at
    - category: ALL texts from .sector-item-container .name (joined with |)
    """
    from datetime import datetime, date

    # Wait for popup
    await page.wait_for_selector('.el-dialog__body, .comment-list-detail-dialog-body', state='visible')

    dialog_selector = '.el-dialog__body'
    found = await page.query_selector('.el-dialog__body')
    if not found:
        found = await page.query_selector('.comment-list-detail-dialog-body')
        if found:
            dialog_selector = '.comment-list-detail-dialog-body'
    if not found:
        raise Exception("Popup dialog not found.")

    # Title
    title = ""
    title_element = await page.query_selector(f'{dialog_selector} .title')
    if title_element:
        raw = await title_element.text_content()
        title = raw.strip() if raw else ""

    # Author
    author = ""
    author_element = await page.query_selector(f'{dialog_selector} .name')
    if author_element:
        raw = await author_element.text_content()
        author = raw.strip() if raw else ""

    # Team
    team = ""
    team_element = await page.query_selector(f'{dialog_selector} .team')
    if team_element:
        raw = await team_element.text_content()
        team = raw.strip() if raw else ""

    # Industry
    industry = ""
    industry_element = await page.query_selector(f'{dialog_selector} .industry')
    if industry_element:
        raw = await industry_element.text_content()
        industry = raw.strip() if raw else ""

    # Comment
    comment = ""
    comment_element = await page.query_selector(f'{dialog_selector} .content')
    if comment_element:
        raw = await comment_element.inner_text()
        comment = raw.strip() if raw else ""

    # Category
    category_elements = await page.query_selector_all(f'{dialog_selector} .sector-item-container .name')
    category_list = []
    for ce in category_elements:
        txt = await ce.text_content()
        txt = txt.strip() if txt else ""
        if txt:
            category_list.append(txt)
    category = "|".join(category_list)

    # Date: always use today
    today = date.today()

    # scraped_at: now
    scraped_at = datetime.now()

    return {
        "date": today,
        "author": author,
        "team": team,
        "industry": industry,
        "category": category,
        "comment": comment,
        "title": title,
        "scraped_at": scraped_at,
    }


async def close_popup(page: Page):
    """Close the popup window by clicking the close button"""
    close_button = await page.wait_for_selector('div.right > i.iconfont.icon-guanbi', timeout=5000)
    if close_button:
        await close_button.click()
        # Wait for the popup to disappear
        await page.wait_for_selector('.el-dialog__body', state='hidden', timeout=2000)


# Usage example
if __name__ == "__main__":
    async def main():
        storage_state = "cookies.json"  # Replace with your actual storage state path
        results = await scrape_website(storage_state)

        # Print results
        for i, item in enumerate(results):
            logger.info(f"Record {i + 1}:")
            logger.info(f"Title: {item['title']}")
            logger.info(f"Author: {item['author']}")
            logger.info(f"Time: {item['time']}")
            logger.info(f"Content: {item['content'][:100]}...")  # Print first 100 characters
            logger.info(f"Stock Themes: {item['stock_themes']}")
            logger.info("-" * 50)


    asyncio.run(main())