
# news_scraper.py
import json
import random
import time
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from utils.translation_service import translate_article_to_chinese
from selen.stealth_driver import get_chrome_driver
from utils.database import get_db_manager, NewsArticleRepository
from utils.logging_config import get_news_task_logger

logger = get_news_task_logger()

def _human_pause(min_delay: float = 1.5, max_delay: float = 3.8):
    """Sleep with jitter to mimic human dwell time."""
    time.sleep(random.uniform(min_delay, max_delay))


def fetch_news_from_db():
    """Fetch recent articles from database."""
    db_manager = get_db_manager()
    repo = NewsArticleRepository(db_manager)
    articles = repo.fetch_recent_articles(hours=6)
    logger.info(f"Fetched {len(articles)} articles from database")
    return articles


def fetch_urls_from_page(query: str, site: str):
    """Fetch URLs from Google News search for a specific site with anti-CAPTCHA measures.

    When scraping and checking urls, if the URL contains 'srnd', remove 'srnd' and everything after it (including "?srnd...", "&srnd...", etc).
    """
    import re

    def normalize_url(link):
        # Remove everything starting from 'srnd' and following, including srnd itself
        if link is None:
            return None
        # Find '?srnd' or '&srnd' and remove it and everything after
        m = re.search(r'([?&])srnd=', link)
        if m:
            link = link[:m.start()]
            # Remove trailing ? or & if left behind
            link = re.sub(r'[?&]$', '', link)
        return link

    search_url = f"https://www.google.com/search?q={query}&tbs=qdr:d,sbd:1&tbm=nws"
    logger.info(f"Fetching URLs from Google News: {query}")
    driver = None
    try:
        driver = get_chrome_driver("")
        driver.get(search_url)
        # Wait until at least one link containing the site appears
        try:
            WebDriverWait(driver, 15).until(
                lambda d: any(
                    site in (a.get_attribute("href") or "")
                    for a in d.find_elements(By.CSS_SELECTOR, "a")
                )
            )
        except TimeoutException:
            raise RuntimeError("No links found - page may not have loaded correctly")
        # Collect all matching links, removing everything after srnd
        links = [
            normalize_url(a.get_attribute("href"))
            for a in driver.find_elements(By.CSS_SELECTOR, "a")
            if a.get_attribute("href") and site in a.get_attribute("href")
        ]

        # Remove duplicates and filter by site after normalization
        links = [link for link in links if link and link.startswith(f"https://{site}")]
        links = list(set(links))

        # Filter out recently scraped URLs after normalizing them
        db_manager = get_db_manager()
        repo = NewsArticleRepository(db_manager)
        recent_links_raw = repo.get_recent_urls(hours=2)
        recent_links = set(filter(None, [normalize_url(link) for link in recent_links_raw]))
        links = [link for link in links if link not in recent_links]

        logger.info(f"Found {len(links)} {query} article links")
        return links
    except Exception as e:
        logger.error(f"Error fetching URLs: {e}")
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
# ============================== Helper Functions ==============================================

def _wait_for_progressive_content(driver, selector, timeout=15, min_paragraphs=5):
    """Wait for content to load with human-like scrolling behavior."""
    start = time.time()
    last_count = 0
    stable_count = 0
    content_fully_loaded = False
    
    while time.time() - start < timeout:        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        paragraphs = soup.select(selector)
        count = len(paragraphs)

        if count > last_count:
            last_count = count
            stable_count = 0
        else:
            stable_count += 1

        if count > min_paragraphs and stable_count > 1:
            content_fully_loaded = True
            break

        # Human-like scrolling: sometimes scroll down gradually, sometimes jump
        at_bottom = driver.execute_script(
            "return (window.innerHeight + window.scrollY) > (document.body.scrollHeight - 500);"
        )
        if not at_bottom:
            # Vary scroll behavior: sometimes smooth, sometimes jump
            scroll_amount = random.randint(500, 1000)
            driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
            _human_pause(1.5, 2.5)
        else:
            # At bottom, wait a bit more in case content loads
            _human_pause(2.5, 4.0)
        
    return content_fully_loaded

def _extract_article_content(soup, selector, unwanted_content=None):
    paragraphs = []
    
    paragraphs_elements = soup.select(selector)
    paragraphs = [p.get_text(" ", strip=True) for p in paragraphs_elements if p.get_text(strip=True)]

    # Filter unwanted content
    if unwanted_content:
        paragraphs = [p for p in paragraphs if not any(pattern in p for pattern in unwanted_content)]

    # Join paragraphs
    content = "\n\n".join(paragraphs)
    # Extract title
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    return {"title": title, "content": content}

def save_agent_data(analysis_result: str):
    """Save agent analysis result to database."""
    from utils.database import NewsAnalysisRepository
    try:
        db_manager = get_db_manager()
        repo = NewsAnalysisRepository(db_manager)
        repo.save_analysis(analysis_result)
        logger.info(f"Saved analysis to database")
    except Exception as e:
        logger.info(f"Failed to save analysis to database, {e}")
        
# ============ Main Scraping Function =============
def scrape_news(url: str, source: str):
    """Scrape a single news article from a URL with comprehensive anti-CAPTCHA measures."""
    content = ""
    driver = None
    try:
        # Load site-specific configuration
        with open("json/selectors.json", "r", encoding="utf-8") as f:
            news_configs = json.load(f)["news"]
        # Get config for this specific site
        site_config = news_configs.get(source)
        selector = site_config.get("selector")
        min_paragraphs = site_config.get("min_paragraphs")
        unwanted_content = site_config.get("unwanted_content", [])

        logger.info(f"Scraping URL: {url}")
        
        # Create driver with fresh fingerprint
        driver = get_chrome_driver()
        # Now navigate to URL
        driver.get(url)
        
        logger.info("Page loaded, waiting for content...")

        # Wait for progressive content with human-like scrolling
        content_fully_loaded = _wait_for_progressive_content(
            driver,
            timeout=15,
            min_paragraphs=min_paragraphs,
            selector=selector
        )
        
        if not content_fully_loaded:
            logger.info("Content not fully loaded, refreshing page and retrying ...")
            driver.refresh()
            _human_pause(2.0,4.0)
            content_fully_loaded = _wait_for_progressive_content(
            driver,
            timeout=15,
            min_paragraphs=min_paragraphs,
            selector=selector
        )
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        result = _extract_article_content(soup, selector, unwanted_content)
        result["content_fully_loaded"] = content_fully_loaded

        title = result.get("title")
        content = result.get("content")
        translation_status = 0
        title_zh = None
        content_zh = None

        if content and title:
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

            try:
                # Save to database using repository
                db_manager = get_db_manager()
                repo = NewsArticleRepository(db_manager)
                repo.insert_or_update_article(
                    url=url,
                    source=source,
                    title=title,
                    content=content,
                    content_fully_loaded=content_fully_loaded,
                    title_zh=title_zh,
                    content_zh=content_zh,
                    translation_status=translation_status
                )
                logger.info(f"Saved article to database: {url}")
            except Exception as db_e:
                logger.error(f"DB save failed for {url}: {db_e}")
    except Exception as e:
        logger.error(f"error when scraping {url}: {e}")
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        logger.info(f"content length: {len(content)}")
        if content.strip():
            return content.replace('\n\n', '\n').replace('\n', ' ')
        else:
            raise
