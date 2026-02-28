# news_scraper.py
import codecs
from datetime import datetime
import json
import random
import re
import time
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from utils.translation_service import translate_article_to_chinese
from selen.stealth_driver import get_chrome_driver
from utils.database import get_db_manager, NewsArticleRepository
from utils.logging_config import get_news_task_logger
import base64
from xml.etree import ElementTree as ET
from curl_cffi import requests

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


def fetch_urls_from_page(query: str, site: str, rate_limit: int):
    """
    Fetch URLs using a randomized cascade of strategies (Google News, Sitemap Search, Site Latest).
    A successful scrape simply means valid URLs were extracted from the search page (bypassed bot checks).
    Post-processing (normalization, filtering, de-duplication) occurs after a successful scrape.
    """

    def normalize_url(link):
        if link is None:
            return None
        # 处理可能的Unicode转义序列，然后匹配srnd参数
        processed_link = codecs.decode(link, 'unicode_escape')
        m = re.search(r'([?&])srnd(?:=|%3D|\\u003d)', processed_link, re.IGNORECASE)
        if m:
            cleaned_link = processed_link[:m.start()]
            # Remove trailing ? or & if left behind
            cleaned_link = re.sub(r'[?&]$', '', cleaned_link)
            return cleaned_link
        return processed_link

    def decode_google_news_url(jslog_str):
        """Extracts and decodes the hidden URL from Google News jslog attribute."""
        if not jslog_str:
            return None
        # Google News embeds base64 string after '5:'
        m = re.search(r'5:([A-Za-z0-9+/\-_]+)', jslog_str)
        if not m:
            return None

        b64_str = m.group(1)
        # Fix missing padding
        b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
        # Handle both standard and url-safe base64 variations
        b64_str = b64_str.replace('-', '+').replace('_', '/')

        try:
            decoded = base64.b64decode(b64_str).decode('utf-8')
            # Extract the actual URL from the decoded JSON array string
            url_m = re.search(r'"(https?://[^"]+)"', decoded)
            if url_m:
                return url_m.group(1)
        except Exception:
            pass
        return None

    def fetch_and_parse_sitemap():
        """Fetches the sitemap using curl_cffi and returns sorted URLs based on publication date."""
        # Use the specific URL provided in the example
        sitemap_url = "https://www.bloomberg.com/sitemaps/news/latest.xml"
        logger.info(f"Fetching sitemap from {sitemap_url}")

        try:
            # Use curl_cffi with impersonate
            response = requests.get(sitemap_url, impersonate="chrome")

            if response.status_code != 200:
                logger.error(f"Failed to fetch sitemap from {sitemap_url}, Status Code: {response.status_code}")
                return []  # Return empty list if fetching fails

            # Get the text content
            sitemap_content = response.text

        except Exception as e:
            logger.error(f"An error occurred while fetching the sitemap with curl_cffi: {e}")
            return []  # Return empty list if fetching fails

        try:
            root = ET.fromstring(sitemap_content)
        except ET.ParseError as e:
            logger.error(f"Failed to parse sitemap XML from {sitemap_url}: {e}")
            return []  # Return empty list if parsing fails

        urls_with_dates = []
        # Define the namespace map if the XML uses one (common for sitemaps)
        # The default sitemap namespace is usually 'http://www.sitemaps.org/schemas/sitemap/0.9'
        # The news namespace might be 'http://www.google.com/schemas/sitemap-news/0.9'
        namespaces = {
            'sitemap': 'http://www.sitemaps.org/schemas/sitemap/0.9',
            'news': 'http://www.google.com/schemas/sitemap-news/0.9'
        }

        for url_elem in root.findall('sitemap:url', namespaces):
            loc_elem = url_elem.find('sitemap:loc', namespaces)
            pub_date_elem = url_elem.find('news:news/news:publication_date', namespaces)

            if loc_elem is not None and pub_date_elem is not None:
                url = loc_elem.text.strip()
                pub_date_str = pub_date_elem.text.strip()

                try:
                    # Parse the ISO format timestamp string into a datetime object
                    # Handle 'Z' suffix for UTC
                    if pub_date_str.endswith('Z'):
                        pub_date_str = pub_date_str[:-1] + '+00:00'
                    pub_date_obj = datetime.fromisoformat(pub_date_str)
                except ValueError as e:
                    logger.warning(f"Could not parse date '{pub_date_str}' for URL {url}: {e}")
                    continue  # Skip this entry if date parsing fails

                urls_with_dates.append((url, pub_date_obj))
            else:
                # If an entry doesn't have both loc and publication_date, skip it
                continue

        # Sort the list of tuples (URL, pub_date_obj) by the pub_date_obj in descending order (newest first)
        urls_with_dates.sort(key=lambda x: x[1], reverse=True)

        # Extract just the URLs from the sorted list, taking the first 20
        sorted_urls = [item[0] for item in urls_with_dates[:20]]

        logger.info(f"Parsed and sorted {len(sorted_urls)} URLs from the sitemap.")
        return sorted_urls

    # Define the target URLs for other strategies
    news_search_url = f"https://news.google.com/search?q={query} when:1h&hl=en-US&gl=US&ceid=US:en"
    bloomberg_search_url = "https://www.bloomberg.com/latest"
    google_search_url = f"https://www.google.com/search?q={query}&tbs=qdr:h,sbd:1&tbm=nws"
    # Define the strategies using robust semantic attributes and paths
    pages_to_scrape = [
        {
            "name": "google_news",
            "url": news_search_url,
            "wait_css": "a[jslog]"  # Reliable attribute used for tracking/routing
        },
        # {
        #     "name": "google_search",
        #     "url": google_search_url,
        #     "wait_css": "a[data-ved][ping]" # Ensures we target actual outbound search results
        # },
        # Sitemap search will be handled separately
        {
            "name": "bloomberg_latest",
            "url": bloomberg_search_url,
            "wait_css": "a[href*='/news/articles/']"  # Ensure we only pull actual articles
        }
    ]

    # 1. Prepare the Sitemap Strategy
    sitemap_strategy = {"name": "sitemap_search"}

    # Add the Sitemap strategy randomly into the list of existing strategies
    all_strategies = pages_to_scrape.copy()  # Copy the original list
    sitemap_index = random.randint(0, len(all_strategies))  # Generate a random index
    all_strategies.insert(sitemap_index, sitemap_strategy)  # Insert the sitemap strategy

    # Now, shuffle the entire combined list
    random.shuffle(all_strategies)

    driver = None
    site_domain = site.replace("www.", "")
    extracted_raw_links = []
    successful_strategy = None

    try:
        # --- SCRAPING PHASE with Sitemap Strategy Integration ---
        for strategy in all_strategies:
            logger.info(f"Trying {strategy['name']} strategy.")
            raw_links = []

            if strategy['name'] == "sitemap_search":
                # Execute the sitemap fetching and parsing logic
                sitemap_urls = fetch_and_parse_sitemap()
                if sitemap_urls:
                    raw_links = sitemap_urls
                    logger.info(f"Sitemap search yielded {len(raw_links)} URLs.")
                else:
                    logger.info("Sitemap search yielded no URLs.")
                    continue  # Move to the next strategy if sitemap fails/returns empty

            else:  # It's a Selenium-based strategy (google_news, bloomberg_latest)
                if driver is None:
                    driver = get_chrome_driver()  # Initialize driver only if needed

                try:
                    driver.get(strategy['url'])

                    # Wait for target links to populate
                    WebDriverWait(driver, 30).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, strategy['wait_css'])) > 0
                    )

                    # Fetch only the relevant links using our robust selectors
                    elements = driver.find_elements(By.CSS_SELECTOR, strategy['wait_css'])
                    # _human_pause(1.0, 3.0) # Consider if needed after API call
                    for a in elements:
                        href = a.get_attribute("href")

                        if strategy['name'] == "google_news":
                            jslog = a.get_attribute("jslog")
                            decoded_url = decode_google_news_url(jslog)
                            if decoded_url:
                                raw_links.append(decoded_url)
                            elif href and href.startswith("http"):
                                raw_links.append(href)

                        elif strategy['name'] == "bloomberg_latest":
                            if href:
                                if href.startswith("/"):
                                    domain = site if "bloomberg" in site else f"www.{site}"
                                    href = f"https://{domain}{href}"
                                raw_links.append(href)

                except TimeoutException:
                    logger.warning(f"Timeout waiting for links on {strategy['name']}. Trying next strategy...")
                    continue
                except Exception as e:
                    logger.warning(f"Error extracting links from {strategy['name']}: {e}. Trying next strategy...")
                    continue

            # 2. Check for scraping success (Did we get valid URLs?)
            if raw_links:
                logger.info(f"Scraping success! Extracted {len(raw_links)} raw URLs via {strategy['name']}.")
                extracted_raw_links = raw_links
                successful_strategy = strategy['name']
                break  # Exit the cascade immediately
            else:
                logger.info(f"{strategy['name']} yielded 0 valid URLs. Moving to next strategy...")

        # --- POST-PROCESSING PHASE ---
        if not extracted_raw_links:
            logger.warning(f"All scraping strategies (including sitemap_search) failed for query: {query}")
            return []

        logger.info(f"Beginning post-processing on {len(extracted_raw_links)} links from {successful_strategy}...")

        # 3. Stripping / Normalization
        links = [normalize_url(link) for link in extracted_raw_links]

        # 4. Filter strictly by site domain
        links = [
            link for link in links
            if link and (site_domain in link)  # More flexible than just startswith
        ]

        # 5. De-duplication
        links = list(set(links))

        # 6. Database Filtering
        db_manager = get_db_manager()
        repo = NewsArticleRepository(db_manager)
        recent_links_raw = repo.get_recent_urls(hours=6)
        recent_links = set(filter(None, [normalize_url(link) for link in recent_links_raw]))

        links = [link for link in links if link not in recent_links]

        logger.info(f"Post-processing complete. Found {len(links)} new, unique {query} article links matching {site}.")
        return links[:rate_limit]  # Return up to 4 links

    except Exception as e:
        logger.error(f"Critical error in fetch_urls_from_page: {e}")
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass  # Or log the exception if desired


# ============================== Helper Functions ==============================================

def _wait_for_progressive_content(driver, selector, xml_selector, unwanted_content, timeout=15, min_paragraphs=4):
    """Wait for content to load with human-like scrolling behavior, preserving most-complete non-empty result against anti-bot blocking."""
    start = time.time()
    last_count = 0
    stable_count = 0
    content_fully_loaded = False

    # Track most complete non-empty content so far to survive anti-bot blanking
    best_xml_content = ""
    best_result = {"title": "", "content": ""}
    best_publish_at = None
    best_count = 0

    while time.time() - start < timeout:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        paragraphs = soup.select(selector)
        count = len(paragraphs)

        if count > last_count:
            last_count = count
            stable_count = 0
        else:
            stable_count += 1

        xml_content = extract_xml_content(soup, xml_selector)
        result = _extract_article_content(soup, selector, unwanted_content)
        publish_at = extract_and_format_time(soup)

        # Consider only if nonempty, and "better" (more paragraphs = more complete)
        this_content_ok = bool(result.get("content", "").strip()) and count > 0
        if this_content_ok and count >= best_count:
            best_count = count
            best_xml_content = xml_content
            best_result = result
            best_publish_at = publish_at

        if count > min_paragraphs and stable_count > 1:
            content_fully_loaded = True
            # Use the current best, which should be this one
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

    # On exit, always use the best version found, even if content_fully_loaded is False or anti-bot blanked page
    return content_fully_loaded, best_xml_content, best_result, best_publish_at


def extract_and_format_time(soup):
    """
    从Bloomberg页面提取时间并格式化为UTC时间字符串
    返回格式: "2026-01-26 00:51:07"
    """
    # 尝试多种选择器
    selectors = [
        "time[datetime]",
        "[data-component='timestamp'] time[datetime]",
        ".ArticleTimestamp_articleTimestamp__zlcvt time[datetime]",
        "meta[property='article:published_time']"
    ]

    time_str = None

    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            if selector.startswith("meta"):
                # 处理meta标签
                if element.has_attr('content'):
                    time_str = element['content']
                    break
            else:
                # 处理time标签
                if element.has_attr('datetime'):
                    time_str = element['datetime']
                    break

    if not time_str:
        return None

    # 格式化时间
    try:
        # 移除时区信息，只保留基本时间
        clean_time = re.sub(r'\.\d+', '', time_str)  # 移除毫秒
        clean_time = re.sub(r'Z$', '', clean_time)  # 移除Z

        # 尝试解析为datetime
        if 'T' in clean_time:
            dt = datetime.strptime(clean_time, "%Y-%m-%dT%H:%M:%S")
        else:
            dt = datetime.strptime(clean_time, "%Y-%m-%d %H:%M:%S")

        # 格式化为需要的字符串
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    except Exception as e:
        print(f"时间解析错误: {e}, 原始时间: {time_str}")
        return None


def _extract_article_content(soup, selector, unwanted_content=None):
    paragraphs = []

    paragraphs_elements = soup.select(selector)

    # 先提取文本並進行基本過濾
    paragraphs = [p.get_text(" ", strip=True) for p in paragraphs_elements if p.get_text(strip=True)]

    # 過濾不想要的內容
    if unwanted_content:
        paragraphs = [p for p in paragraphs if not any(pattern in p for pattern in unwanted_content)]

    # 新增：過濾以數字:數字格式開頭的段落
    paragraphs = [
        re.sub(r'^\d+:\d+\s*', '', p)  # 去掉開頭的數字:數字格式
        for p in paragraphs
        if not re.match(r'^\d+:\d+\s*$', p)  # 完全匹配數字:數字格式的整行去掉
    ]

    # 再次過濾可能變空的段落
    paragraphs = [p for p in paragraphs if p.strip()]

    # 合併段落
    content = "\n\n".join(paragraphs)

    # 提取標題
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    return {"title": title, "content": content}


def save_agent_data(analysis_result: dict):
    """Save agent analysis result to database."""
    from utils.database import NewsAnalysisRepository
    try:
        db_manager = get_db_manager()
        repo = NewsAnalysisRepository(db_manager)
        repo.save_analysis(analysis_result)
        logger.info(f"Saved analysis to database")
    except Exception as e:
        logger.info(f"Failed to save analysis to database, {e}")


def extract_xml_content(
        soup: BeautifulSoup,
        xml_selector: str | None,
) -> str:
    """
    Extracts raw XML / HTML content using a selector.
    Returns serialized HTML/XML string for database storage.
    """

    if not xml_selector:
        return ""

    try:
        elements = soup.select(xml_selector)
        if not elements:
            return ""

        # Preserve full DOM structure
        xml_parts = []
        for el in elements:
            # Decode keeps inner tags intact (better than str(el))
            xml_parts.append(el.decode())

        return "\n".join(xml_parts)

    except Exception as e:
        logger.error(f"XML extraction failed: {e}")
        return ""


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
        xml_selector = site_config.get("xml_selector")
        min_paragraphs = site_config.get("min_paragraphs")
        unwanted_content = site_config.get("unwanted_content", [])

        logger.info(f"Scraping URL: {url}")

        # Create driver with fresh fingerprint
        driver = get_chrome_driver()
        # Now navigate to URL
        driver.get(url)

        logger.info("Page loaded, waiting for content...")

        # Wait for progressive content with human-like scrolling
        content_fully_loaded, xml_content, result, publish_at = _wait_for_progressive_content(
            driver,
            selector=selector,
            xml_selector=xml_selector,
            unwanted_content=unwanted_content,
            timeout=15,
            min_paragraphs=min_paragraphs,
        )

        # if not content_fully_loaded:
        #     logger.info("Content not fully loaded, refreshing page and retrying ...")
        #     driver.refresh()
        #     _human_pause(2.0,4.0)
        #     content_fully_loaded = _wait_for_progressive_content(
        #     driver,
        #     timeout=15,
        #     min_paragraphs=min_paragraphs,
        #     selector=selector
        # )

        result["content_fully_loaded"] = content_fully_loaded

        title = result.get("title")
        content = result.get("content")
        title_zh = None
        content_zh = None

        if content and title:
            logger.info("Starting Chinese translation...")
            try:
                translation_result = translate_article_to_chinese(title, content)
                title_zh = translation_result.get('title_zh', '')
                content_zh = translation_result.get('content_zh', '')
                logger.info("Chinese translation completed")
            except Exception as e:
                logger.error(f"Translation failed for {url}: {e}")
                title_zh = ""
                content_zh = ""

            try:
                # Save to database using repository
                db_manager = get_db_manager()
                repo = NewsArticleRepository(db_manager)
                repo.insert_or_update_article(
                    url=url,
                    publish_at=publish_at,
                    source=source,
                    title=title,
                    content=content,
                    xml_content=xml_content,
                    content_fully_loaded=content_fully_loaded,
                    title_zh=title_zh,
                    content_zh=content_zh,
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
