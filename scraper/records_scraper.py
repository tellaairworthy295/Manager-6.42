from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page
from pwright.async_pm import AsyncPlaywrightManager
from playwright.async_api import Page
from typing import List, Dict
import asyncio


async def scrape_website(storage_state: str) -> List[Dict]:
    """
    Scrape the website with the given storage_state (cookies)

    Args:
        storage_state: Path to storage_state file or a dict containing cookies

    Returns:
        List of scraped data
    """
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
            list_items = await page.query_selector_all('.list-item')

            # Step 4: Iterate through each record
            for i, list_item in enumerate(list_items):
                # Find the content element within the list-item
                content_element = await list_item.query_selector('div.content')
                if content_element:
                    # Directly click the element (no need for extra wrapper)
                    await content_element.click()

                    # Wait for the popup to appear
                    await page.wait_for_selector('.el-dialog__body', timeout=5000)

                    # Step 5: Scrape content from the popup
                    scraped_data = await scrape_popup(page)
                    results.append(scraped_data)

                    # Close the popup window
                    await close_popup(page)

                    # Add a small delay between clicks
                    await asyncio.sleep(0.5)
                else:
                    print(f"Warning: Could not find 'div.content' in list item {i}")

            return results

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
    """Scrape content from the popup window"""
    # Wait for the popup to be visible
    await page.wait_for_selector('.el-dialog__body', state='visible')

    # Extract title
    title_element = await page.query_selector('.el-dialog__body .title')
    title = await title_element.text_content() if title_element else ""

    # Extract author and time
    author_element = await page.query_selector('.el-dialog__body .name')
    author = await author_element.text_content() if author_element else ""

    time_element = await page.query_selector('.el-dialog__body .time')
    time = await time_element.text_content() if time_element else ""

    # Extract content
    content_element = await page.query_selector('.el-dialog__body .content')
    content = await content_element.text_content() if content_element else ""

    # Extract stock themes
    stock_themes = []
    theme_elements = await page.query_selector_all('.el-dialog__body .sector-item-container .name')
    for theme_element in theme_elements:
        theme = await theme_element.text_content()
        stock_themes.append(theme.strip())

    return {
        "title": title,
        "author": author,
        "time": time,
        "content": content,
        "stock_themes": stock_themes
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
            print(f"Record {i + 1}:")
            print(f"Title: {item['title']}")
            print(f"Author: {item['author']}")
            print(f"Time: {item['time']}")
            print(f"Content: {item['content'][:100]}...")  # Print first 100 characters
            print(f"Stock Themes: {item['stock_themes']}")
            print("-" * 50)


    asyncio.run(main())