
import json
from bs4 import BeautifulSoup
import time
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from utils.translation_service import translate_article_to_chinese
from config import setup_logging, get_chrome_driver #, cleanup_driver_dirs
from utils.database import get_db_manager, NewsArticleRepository
from selenium.webdriver.support.ui import WebDriverWait

logger = setup_logging("logs/news", "news_scraper")

def fetch_urls_from_db():
    """Fetch recent articles from database."""
    db_manager = get_db_manager()
    repo = NewsArticleRepository(db_manager)
    return repo.fetch_recent_articles(hours=999)


def fetch_urls_from_page(query: str, site: str):
    """Fetch URLs from Google News search for a specific site."""
    try:
        search_url = f"https://www.google.com/search?q={query}&tbs=qdr:h,sbd:1&tbm=nws"
        logger.info(search_url)
        driver = get_chrome_driver()
        driver.get(search_url)
        # Wait until at least one link containing the site appears
        try:
            WebDriverWait(driver, 30).until(
                lambda d: any(
                    site in (a.get_attribute("href") or "")
                    for a in d.find_elements(By.CSS_SELECTOR, "a")
                )
            )
        except TimeoutException:
            raise RuntimeError("No links found")

        # Collect all matching links
        links = [
            a.get_attribute("href")
            for a in driver.find_elements(By.CSS_SELECTOR, "a")
            if a.get_attribute("href") and site in a.get_attribute("href")
        ]

        # Remove duplicates and filter by site
        links = [link for link in links if link.startswith(f"https://{site}")]
        links = list(set(links))

        # Filter out recently scraped URLs
        db_manager = get_db_manager()
        repo = NewsArticleRepository(db_manager)
        recent_links = repo.get_recent_urls(hours=1)
        links = [link for link in links if link not in recent_links]

        logger.info(f"Found {len(links)} {query} article links")
        return links
    finally:
        try:
            driver.quit()
        except:
            pass

def get_target_articles():
    # Fetch articles from database
    db_manager = get_db_manager()
    repo = NewsArticleRepository(db_manager)
    articles = repo.fetch_articles_for_export(hours=12)
    return articles

# ============================== Helper Functions ==============================================

import time
from bs4 import BeautifulSoup

def _wait_for_progressive_content(driver, 
                                 timeout=60, 
                                 min_paragraphs=5, 
                                 content_locator=None):
    """
    Parameters:
        driver (WebDriver): Selenium WebDriver instance.
        timeout (int): Maximum time to wait in seconds.
        min_paragraphs (int): Minimum number of paragraphs required.
        content_locator (dict): Dictionary specifying how to locate content.
            Example:
                {"selector": "article p, div.body-content p"}
                {"selector": "div.article-body-module__content__bnXL1 div[data-testid*='paragraph']"}
    """
    start = time.time()
    last_count = 0
    stable_count = 0
    content_fully_loaded = False
    # Default fallback selector if none provided
    selector = content_locator.get("selector") if content_locator else "article p, div.body-content p"

    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    while time.time() - start < timeout:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        paragraphs = soup.select(selector)
        count = len(paragraphs)

        if count > last_count:
            last_count = count
            stable_count = 0
        else:
            stable_count += 1

        if count > min_paragraphs and stable_count >= 2:
            content_fully_loaded = True
            break

        # If not already at bottom, scroll down
        at_bottom = driver.execute_script(
            "return (window.innerHeight + window.scrollY) > (document.body.scrollHeight - 300);"
        )
        if not at_bottom:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(4)
    logger.info(f"content fully loaded: {content_fully_loaded}")
    return content_fully_loaded

def _extract_article_content(soup, site_config, unwanted_content=None):
    """
    Parameters:
        soup (BeautifulSoup): Parsed HTML page.
        site_config (dict): Site configuration dictionary.
            Can contain:
            - "selector": CSS selector string (e.g., "article p, div.body-content p")
            - "tag", "class", "paragraph_tag": For more complex extraction
        unwanted_content (list): List of substrings to filter out from paragraphs.
    """
    paragraphs = []
    
    # Handle simple selector-based extraction (most common case)
    if "selector" in site_config:
        selector = site_config["selector"]
        paragraphs_elements = soup.select(selector)
        paragraphs = [p.get_text(" ", strip=True) for p in paragraphs_elements if p.get_text(strip=True)]
    # Handle complex tag/class-based extraction (legacy support)
    elif "tag" in site_config and "class" in site_config:
        containers = soup.find_all(site_config.get("tag"), class_=site_config.get("class"))
        for container in containers:
            if "paragraph_tag" in site_config:
                if "paragraph_attr" in site_config and "paragraph_attr_contains" in site_config:
                    # Special case: attribute-based filtering
                    for p in container.find_all(
                        lambda tag: tag.name == site_config["paragraph_tag"]
                        and site_config["paragraph_attr_contains"] in tag.get(site_config["paragraph_attr"], "")
                    ):
                        paragraphs.append(p.get_text(" ", strip=True))
                else:
                    # Simple tag-based extraction
                    for p in container.find_all(site_config["paragraph_tag"]):
                        paragraphs.append(p.get_text(" ", strip=True))
    else:
        # Fallback to default
        paragraphs_elements = soup.select("article p, div.body-content p")
        paragraphs = [p.get_text(" ", strip=True) for p in paragraphs_elements if p.get_text(strip=True)]

    # Filter unwanted content
    if unwanted_content:
        paragraphs = [p for p in paragraphs if not any(pattern in p for pattern in unwanted_content)]

    # Join paragraphs
    content = "\n\n".join(paragraphs)

    # Extract title
    title = soup.find("h1")
    title_text = title.get_text(strip=True) if title else "No title found"
    content_text = content if content else "No article content found"

    return {"title": title_text, "content": content_text}

def save_agent_data(analysis_result: str):
    """Save agent analysis result to database."""
    from utils.database import NewsAnalysisRepository
    db_manager = get_db_manager()
    repo = NewsAnalysisRepository(db_manager)
    repo.save_analysis(analysis_result)
        
# ============ Main Scraping Function =============
def scrape_news(url: str, site: str):
    """Scrape a single news article from a URL."""
    driver = None
    try:
        driver = get_chrome_driver()
        logger.info("Driver launched")
        
        # Load site-specific configuration
        with open("json/selectors.json", "r", encoding="utf-8") as f:
            news_configs = json.load(f)["news"]
        
        # Get config for this specific site
        site_config = news_configs.get(site, {})
        selector = site_config.get("selector", "article p")
        min_paragraphs = site_config.get("min_paragraphs", 5)
        unwanted_content = site_config.get("unwanted_content", [])
        
        # Convert selector string to dict format if needed
        if isinstance(selector, str):
            content_locator = {"selector": selector}
        else:
            content_locator = selector

        logger.info(f"Scraping URL: {url}")
        driver.get(url)
        logger.info("Page loaded")

        content_fully_loaded = _wait_for_progressive_content(
            driver,
            timeout=60,
            min_paragraphs=min_paragraphs,
            content_locator=content_locator
        )

        soup = BeautifulSoup(driver.page_source, "html.parser")
        result = _extract_article_content(soup, site_config, unwanted_content)
        result["content_fully_loaded"] = content_fully_loaded

        title = result.get("title", "")
        content = result.get("content", "")
        translation_status = 0
        title_zh = None
        content_zh = None

        if title or content:
            logger.info("Starting Chinese translation...")
            try:
                translation_result = translate_article_to_chinese(title, content)
                title_zh = translation_result.get('title_zh', '')
                content_zh = translation_result.get('content_zh', '')
                translation_status = 1
                logger.info("Chinese translation completed")
            except Exception as e:
                logger.error(f"Translation failed for {url}: {e}")
                translation_status = 0
                title_zh = ""
                content_zh = ""
        else:
            logger.info("Translation is disabled in configuration")
            translation_status = 0
            title_zh = ""
            content_zh = ""

        try:
            # Save to database using repository
            db_manager = get_db_manager()
            repo = NewsArticleRepository(db_manager)
            repo.insert_or_update_article(
                url=url,
                source=site,
                title=result.get("title", ""),
                content=result.get("content", ""),
                content_fully_loaded=content_fully_loaded,
                title_zh=title_zh,
                content_zh=content_zh,
                translation_status=translation_status
            )
        except Exception as db_e:
            logger.error(f"DB save failed for {url}: {db_e}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

# # === Example Usage ===
# if __name__ == "__main__":

#     export_content_to_dify()
