# news_scraper.py
import codecs
from datetime import datetime
import json
import random
import re
from bs4 import BeautifulSoup
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from utils.translation_service import translate_article_to_chinese
from selen.stealth_driver import get_chrome_driver
from utils.database import get_db_manager, NewsArticleRepository
from utils.logging_config import get_news_task_logger
import base64
from xml.etree import ElementTree as ET
from rotate_proxies import force_rotate

logger = get_news_task_logger()

captcha_signatures = [
    "We've detected unusual activity from your computer network",
    "px-captcha",
    "cf-browser-verification",
    "Are you a robot?"
]


def _is_blocked(page_source: str) -> bool:
    """Check if the current page is a bot challenge/CAPTCHA page."""
    return any(sig in page_source for sig in captcha_signatures)


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

    def fetch_and_parse_sitemap(driver):
        """Fetches the sitemap using Selenium driver and returns sorted URLs based on publication date."""
        sitemap_url = "https://www.bloomberg.com/sitemaps/news/latest.xml"
        logger.info(f"Fetching sitemap from {sitemap_url}")

        try:
            driver.get(sitemap_url)
            _human_pause(1.5, 3.0)

            if _is_blocked(driver.page_source):
                logger.warning("Bot challenge detected on sitemap page!")
                # force_rotate()
                # time.sleep(30)
                return []

            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, "webkit-xml-viewer-source-xml"))
            )

            # Use innerHTML to get the actual XML markup, not stripped plain text
            xml_content = driver.execute_script(
                "return document.getElementById('webkit-xml-viewer-source-xml').innerHTML;"
            )

            if not xml_content:
                logger.error("Empty content from webkit-xml-viewer-source-xml.")
                return []

        except Exception as e:
            logger.error(f"An error occurred while fetching the sitemap with Selenium: {e}")
            return []

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.error(f"Failed to parse sitemap XML: {e}")
            return []

        urls_with_dates = []
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
                    if pub_date_str.endswith('Z'):
                        pub_date_str = pub_date_str[:-1] + '+00:00'
                    pub_date_obj = datetime.fromisoformat(pub_date_str)
                except ValueError as e:
                    logger.warning(f"Could not parse date '{pub_date_str}' for URL {url}: {e}")
                    continue

                urls_with_dates.append((url, pub_date_obj))
            else:
                continue

        urls_with_dates.sort(key=lambda x: x[1], reverse=True)
        sorted_urls = [item[0] for item in urls_with_dates[:30]]

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
                if driver is None:
                    driver = get_chrome_driver()
                sitemap_urls = fetch_and_parse_sitemap(driver)
                if sitemap_urls:
                    raw_links = sitemap_urls
                    logger.info(f"Sitemap search yielded {len(raw_links)} URLs.")
                else:
                    logger.info("Sitemap search yielded no URLs.")
                    continue

            else:  # It's a Selenium-based strategy (google_news, bloomberg_latest)
                # Apply 3 retries specifically for bloomberg_latest, 1 for others
                max_attempts = 3 if strategy['name'] == "bloomberg_latest" else 1

                for attempt in range(max_attempts):
                    if driver is None:
                        driver = get_chrome_driver()

                    try:
                        driver.get(strategy['url'])
                        # Check for CAPTCHA first to "Fast-Fail" instead of waiting 30 seconds
                        _human_pause(1.5, 3.0)  # Wait a moment for JS challenge to load
                        _handle_cookies_notification(driver)
                        if _is_blocked(driver.page_source):
                            # force_rotate()
                            # time.sleep(30)
                            raise Exception("CAPTCHA or Anti-Bot challenge detected on search page!")

                        # Wait for target links to populate
                        WebDriverWait(driver, 30).until(
                            lambda d: len(d.find_elements(By.CSS_SELECTOR, strategy['wait_css'])) > 0
                        )

                        # Fetch only the relevant links using our robust selectors
                        elements = driver.find_elements(By.CSS_SELECTOR, strategy['wait_css'])

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

                        # If we successfully got links, break out of the retry loop
                        if raw_links:
                            break

                    except Exception as e:
                        logger.warning(
                            f"Error extracting links from {strategy['name']} (Attempt {attempt + 1}/{max_attempts}): {e}")

                        if attempt < max_attempts - 1:
                            logger.info(
                                f"Retrying {strategy['name']}... Quitting current driver to rotate proxy/fingerprint.")
                            try:
                                driver.quit()  # Destroy the burned session
                            except Exception:
                                pass
                            driver = None  # Force a new driver creation on the next loop iteration
                            _human_pause(3.0, 7.0)  # Cool down before retry
                        else:
                            logger.warning(
                                f"Failed {strategy['name']} after {max_attempts} attempts. Moving to next strategy...")
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
import time
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By


def _handle_cookies_notification(driver):
    # 1. Handle Cookie Consent Banner (Overlay & Iframe safe)
    try:
        def _try_click_cookie():
            # Target the button by the unique Sourcepoint class, the exact title, or the new "Accept all" button
            selectors = [
                'button.sp_choice_type_11',
                'button[title="Yes, I Accept"]',
                'button[jsname="b3VHJd"][aria-label="Accept all"]'  # Selector for the new button
            ]

            for selector in selectors:
                btns = driver.find_elements(By.CSS_SELECTOR, selector)
                if btns:
                    # Execute JS click unconditionally (bypassing potential overlays)
                    driver.execute_script("arguments[0].click();", btns[0])
                    return True
            return False

        # First attempt: Check the main HTML document
        if _try_click_cookie():
            time.sleep(3.0)  # Wait for overlay to fade out
        else:
            # Second attempt: Look inside ALL iframes (in case the element is in an iframe)
            iframes = driver.find_elements(By.TAG_NAME, 'iframe')
            for iframe in iframes:
                try:
                    driver.switch_to.frame(iframe)
                    clicked = _try_click_cookie()
                    driver.switch_to.default_content()  # Always switch back to main page

                    if clicked:
                        time.sleep(1.0)
                        break
                except WebDriverException:
                    # If switching frames fails, ensure we reset back to the main document
                    driver.switch_to.default_content()

    except Exception:
        # Failsafe: ensure context is at default if a random error occurs
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


def _wait_for_progressive_content(driver, selector, xml_selector, unwanted_content, timeout=15, min_paragraphs=4):
    """Wait for content to load with human-like scrolling behavior, preserving most-complete non-empty result against anti-bot blocking."""
    try:
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

            # 1. Handle Cookie Consent Banner
            _handle_cookies_notification(driver)

            # Fetch page source ONCE per loop to optimize performance
            page_source = driver.page_source

            # 2. Fast-fail Anti-Bot / CAPTCHA check
            if _is_blocked(driver.page_source) and not best_result["content"]:
                # Break immediately to save time and return empty markers
                return False, "", {"title": "", "content": ""}, None

            # 3. Parse and evaluate content
            soup = BeautifulSoup(page_source, "html.parser")
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

            # 4. Human-like interactions: Smooth scrolling
            at_bottom = driver.execute_script(
                "return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 500);"
            )

            if not at_bottom:
                driver.execute_script("window.scrollBy(600, 1000);")  # Scroll down

            # 6. Human dwell time before next check
            _human_pause(1.5, 2.5)

        # On exit, always use the best version found, even if content_fully_loaded is False or anti-bot blanked page
        return content_fully_loaded, best_xml_content, best_result, best_publish_at
    finally:
        if driver:
            driver.quit()


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

        result["content_fully_loaded"] = content_fully_loaded

        title = result.get("title")
        content = result.get("content")

        if content and title:
            logger.info("Starting Chinese translation...")
            try:
                trans = translate_article_to_chinese(title, content)
                title_zh=trans.get('title_zh')
                content_zh=trans.get('content_zh')
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


if __name__ == "__main__":
    scrape_news(
        "https://www.bloomberg.com/news/articles/2026-03-05/china-top-lawmakers-meeting-is-smallest-ever-under-xi-amid-purge",
        "bloomberg")
