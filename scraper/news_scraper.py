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
from selenium.webdriver.common.action_chains import ActionChains

logger = get_news_task_logger()

captcha_signatures = [
    "We've detected unusual activity from your computer network",
    "px-captcha",
    "cf-browser-verification"
]

# Realistic referrers to spoof navigation origin
_BLOOMBERG_REFERRERS = [
    "https://www.google.com/",
    "https://news.google.com/",
    "https://www.twitter.com/",
    "https://www.reddit.com/",
    "https://www.linkedin.com/",
]

# Warm-up pages — low-JS Bloomberg pages that build session trust
_BLOOMBERG_WARMUP_PAGES = [
    "https://www.bloomberg.com/",
    "https://www.bloomberg.com/markets",
    "https://www.bloomberg.com/technology",
    "https://www.bloomberg.com/politics",
]


def _human_pause(min_delay: float = 1.5, max_delay: float = 3.8):
    """Sleep with jitter to mimic human dwell time."""
    time.sleep(random.uniform(min_delay, max_delay))


def _is_blocked(page_source: str) -> bool:
    """Check if the current page is a bot challenge/CAPTCHA page."""
    return any(sig in page_source for sig in captcha_signatures)


def _warm_up_session(driver, target_domain: str = "bloomberg.com"):
    """
    Visit 1–2 low-risk pages on the target domain before hitting article URLs.
    Builds session cookies and trust score with Akamai/PerimeterX.
    Real users don't cold-open article pages — they browse first.
    """
    warmup_pages = random.sample(_BLOOMBERG_WARMUP_PAGES, k=random.randint(1, 2))

    for page_url in warmup_pages:
        try:
            logger.info(f"[Warmup] Visiting: {page_url}")
            driver.get(page_url)
            _human_pause(3.0, 6.0)

            # Scroll slightly to mimic reading
            scroll_amount = random.randint(200, 600)
            driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
            _human_pause(1.5, 3.0)

            # Check if even the warmup page is blocked
            if _is_blocked(driver.page_source):
                logger.warning(f"[Warmup] Blocked on {page_url}, aborting warmup early.")
                return False

        except Exception as e:
            logger.warning(f"[Warmup] Failed on {page_url}: {e}")
            return False

    logger.info("[Warmup] Session warm-up complete.")
    return True


def _set_referrer_and_navigate(driver, url: str, referrer: str = None):
    """
    Navigate to a URL while spoofing a realistic referrer.
    Direct navigation (no referrer) to paywalled articles is a strong bot signal.
    """
    if referrer is None:
        referrer = random.choice(_BLOOMBERG_REFERRERS)

    # Use CDP to override the referrer for this navigation
    # This makes Bloomberg think you arrived from Google/Twitter/etc.
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": f"""
            Object.defineProperty(document, 'referrer', {{
                get: () => '{referrer}',
                configurable: true
            }});
        """
    })

    driver.get(url)


def fetch_news_from_db():
    """Fetch recent articles from database."""
    db_manager = get_db_manager()
    repo = NewsArticleRepository(db_manager)
    articles = repo.fetch_recent_articles(hours=6)
    logger.info(f"Fetched {len(articles)} articles from database")
    return articles


def fetch_urls_from_page(query: str, site: str, rate_limit: int):
    """
    Fetch URLs using a randomized cascade of strategies.
    """

    def normalize_url(link):
        if link is None:
            return None
        processed_link = codecs.decode(link, 'unicode_escape')
        m = re.search(r'([?&])srnd(?:=|%3D|\\u003d)', processed_link, re.IGNORECASE)
        if m:
            cleaned_link = processed_link[:m.start()]
            cleaned_link = re.sub(r'[?&]$', '', cleaned_link)
            return cleaned_link
        return processed_link

    def decode_google_news_url(jslog_str):
        if not jslog_str:
            return None
        m = re.search(r'5:([A-Za-z0-9+/\-_]+)', jslog_str)
        if not m:
            return None
        b64_str = m.group(1)
        b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
        b64_str = b64_str.replace('-', '+').replace('_', '/')
        try:
            decoded = base64.b64decode(b64_str).decode('utf-8')
            url_m = re.search(r'"(https?://[^"]+)"', decoded)
            if url_m:
                return url_m.group(1)
        except Exception:
            pass
        return None

    def fetch_and_parse_sitemap():
        sitemap_url = "https://www.bloomberg.com/sitemaps/news/latest.xml"
        logger.info(f"Fetching sitemap from {sitemap_url}")
        try:
            response = requests.get(sitemap_url, impersonate="chrome")
            if response.status_code != 200:
                logger.error(f"Failed to fetch sitemap: {response.status_code}")
                return []
            sitemap_content = response.text
        except Exception as e:
            logger.error(f"Sitemap fetch failed: {e}")
            return []

        try:
            root = ET.fromstring(sitemap_content)
        except ET.ParseError as e:
            logger.error(f"Sitemap XML parse failed: {e}")
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
                except ValueError:
                    continue
                urls_with_dates.append((url, pub_date_obj))

        urls_with_dates.sort(key=lambda x: x[1], reverse=True)
        sorted_urls = [item[0] for item in urls_with_dates[:20]]
        logger.info(f"Sitemap: parsed {len(sorted_urls)} URLs.")
        return sorted_urls

    news_search_url = f"https://news.google.com/search?q={query} when:1h&hl=en-US&gl=US&ceid=US:en"
    bloomberg_search_url = "https://www.bloomberg.com/latest"

    pages_to_scrape = [
        {
            "name": "google_news",
            "url": news_search_url,
            "wait_css": "a[jslog]"
        },
        {
            "name": "bloomberg_latest",
            "url": bloomberg_search_url,
            "wait_css": "a[href*='/news/articles/']"
        }
    ]

    sitemap_strategy = {"name": "sitemap_search"}
    all_strategies = pages_to_scrape.copy()
    sitemap_index = random.randint(0, len(all_strategies))
    all_strategies.insert(sitemap_index, sitemap_strategy)
    random.shuffle(all_strategies)

    driver = None
    site_domain = site.replace("www.", "")
    extracted_raw_links = []
    successful_strategy = None

    try:
        for strategy in all_strategies:
            logger.info(f"Trying strategy: {strategy['name']}")
            raw_links = []

            if strategy['name'] == "sitemap_search":
                sitemap_urls = fetch_and_parse_sitemap()
                if sitemap_urls:
                    raw_links = sitemap_urls
                    logger.info(f"Sitemap yielded {len(raw_links)} URLs.")
                else:
                    logger.info("Sitemap yielded no URLs.")
                    continue

            else:
                max_attempts = 3 if strategy['name'] == "bloomberg_latest" else 1

                for attempt in range(max_attempts):
                    if driver is None:
                        driver = get_chrome_driver()
                        # ── Warm up the session before scraping ──────────────
                        # Only warm up if we're hitting Bloomberg directly
                        if strategy['name'] == "bloomberg_latest":
                            _warm_up_session(driver)

                    try:
                        _set_referrer_and_navigate(driver, strategy['url'])

                        _human_pause(1.5, 3.0)
                        page_source = driver.page_source
                        if _is_blocked(page_source):
                            raise Exception("Bot challenge detected on search page!")

                        WebDriverWait(driver, 30).until(
                            lambda d: len(d.find_elements(By.CSS_SELECTOR, strategy['wait_css'])) > 0
                        )

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

                        if raw_links:
                            break

                    except Exception as e:
                        logger.warning(
                            f"[{strategy['name']}] Attempt {attempt + 1}/{max_attempts} failed: {e}"
                        )
                        if attempt < max_attempts - 1:
                            try:
                                driver.quit()
                            except Exception:
                                pass
                            driver = None
                            _human_pause(3.0, 7.0)
                        else:
                            logger.warning(f"[{strategy['name']}] All attempts exhausted.")

            if raw_links:
                logger.info(f"Success via {strategy['name']}: {len(raw_links)} raw URLs.")
                extracted_raw_links = raw_links
                successful_strategy = strategy['name']
                break
            else:
                logger.info(f"{strategy['name']} yielded 0 URLs, trying next strategy...")

        if not extracted_raw_links:
            logger.warning(f"All strategies failed for query: {query}")
            return []

        logger.info(f"Post-processing {len(extracted_raw_links)} links from {successful_strategy}...")

        links = [normalize_url(link) for link in extracted_raw_links]
        links = [link for link in links if link and (site_domain in link)]
        links = list(set(links))

        db_manager = get_db_manager()
        repo = NewsArticleRepository(db_manager)
        recent_links_raw = repo.get_recent_urls(hours=6)
        recent_links = set(filter(None, [normalize_url(link) for link in recent_links_raw]))
        links = [link for link in links if link not in recent_links]

        logger.info(f"Post-processing done: {len(links)} new unique URLs for {site}.")
        return links[:rate_limit]

    except Exception as e:
        logger.error(f"Critical error in fetch_urls_from_page: {e}")
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ============================== Helper Functions ==============================================

def _wait_for_progressive_content(driver, selector, xml_selector, unwanted_content, timeout=15, min_paragraphs=4):
    """Wait for content to load with human-like scrolling."""
    start = time.time()
    last_count = 0
    stable_count = 0
    content_fully_loaded = False

    best_xml_content = ""
    best_result = {"title": "", "content": ""}
    best_publish_at = None
    best_count = 0

    while time.time() - start < timeout:
        page_source = driver.page_source

        if _is_blocked(page_source):
            return False, "", {"title": "", "content": ""}, None

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

        this_content_ok = bool(result.get("content", "").strip()) and count > 0
        if this_content_ok and count >= best_count:
            best_count = count
            best_xml_content = xml_content
            best_result = result
            best_publish_at = publish_at

        if count > min_paragraphs and stable_count > 1:
            content_fully_loaded = True
            break

        at_bottom = driver.execute_script(
            "return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 500);"
        )
        if not at_bottom:
            scroll_amount = random.randint(300, 800)
            driver.execute_script(f"""
                window.scrollBy({{
                    top: {scroll_amount},
                    left: 0,
                    behavior: 'smooth'
                }});
            """)

        try:
            paragraphs_elems = driver.find_elements(By.CSS_SELECTOR, "p")
            if paragraphs_elems:
                target = random.choice(paragraphs_elems[:5])
                ActionChains(driver).move_to_element(target).perform()
        except Exception:
            pass

        _human_pause(1.5, 2.5)

    return content_fully_loaded, best_xml_content, best_result, best_publish_at


def extract_and_format_time(soup):
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
                if element.has_attr('content'):
                    time_str = element['content']
                    break
            else:
                if element.has_attr('datetime'):
                    time_str = element['datetime']
                    break

    if not time_str:
        return None

    try:
        clean_time = re.sub(r'\.\d+', '', time_str)
        clean_time = re.sub(r'Z$', '', clean_time)
        if 'T' in clean_time:
            dt = datetime.strptime(clean_time, "%Y-%m-%dT%H:%M:%S")
        else:
            dt = datetime.strptime(clean_time, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.warning(f"Time parse error: {e}, raw: {time_str}")
        return None


def _extract_article_content(soup, selector, unwanted_content=None):
    paragraphs_elements = soup.select(selector)
    paragraphs = [p.get_text(" ", strip=True) for p in paragraphs_elements if p.get_text(strip=True)]

    if unwanted_content:
        paragraphs = [p for p in paragraphs if not any(pattern in p for pattern in unwanted_content)]

    paragraphs = [
        re.sub(r'^\d+:\d+\s*', '', p)
        for p in paragraphs
        if not re.match(r'^\d+:\d+\s*$', p)
    ]
    paragraphs = [p for p in paragraphs if p.strip()]
    content = "\n\n".join(paragraphs)

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    return {"title": title, "content": content}


def extract_xml_content(soup: BeautifulSoup, xml_selector: str | None) -> str:
    if not xml_selector:
        return ""
    try:
        elements = soup.select(xml_selector)
        if not elements:
            return ""
        return "\n".join(el.decode() for el in elements)
    except Exception as e:
        logger.error(f"XML extraction failed: {e}")
        return ""


# ============ Stateful driver wrapper ============

class ArticleScrapeSession:
    """
    Holds a single warm Chrome driver across multiple article scrapes.
    Rotates the driver automatically when blocked or after max_articles_per_driver.
    
    Why: A browser that visits exactly one page and dies is a textbook bot pattern.
    Real users browse multiple pages in one session. Reusing the driver also
    preserves session cookies and trust score built during warm-up.
    """

    def __init__(self, max_articles_per_driver: int = 4):
        self.driver = None
        self.articles_scraped = 0
        self.max_articles_per_driver = max_articles_per_driver
        self._warmed_up = False

    def _ensure_driver(self):
        """Create + warm up a fresh driver if we don't have one."""
        if self.driver is None:
            self.driver = get_chrome_driver()
            self._warmed_up = False

        if not self._warmed_up:
            _warm_up_session(self.driver)
            self._warmed_up = True

    def _rotate_driver(self, reason: str = ""):
        """Destroy current driver and reset state — forces a fresh fingerprint next call."""
        logger.info(f"[Session] Rotating driver. Reason: {reason}")
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.driver = None
        self.articles_scraped = 0
        self._warmed_up = False
        # Cool down before launching new browser
        _human_pause(4.0, 9.0)

    def scrape(self, url: str, source: str) -> str:
        """
        Scrape a single article URL, reusing the warm driver.
        Rotates driver if blocked or article limit reached.
        """
        # Rotate proactively if we've hit the per-driver article limit
        if self.articles_scraped >= self.max_articles_per_driver:
            self._rotate_driver(reason=f"Reached {self.max_articles_per_driver} articles per driver")

        self._ensure_driver()

        content = ""
        try:
            with open("json/selectors.json", "r", encoding="utf-8") as f:
                news_configs = json.load(f)["news"]

            site_config = news_configs.get(source)
            selector = site_config.get("selector")
            xml_selector = site_config.get("xml_selector")
            min_paragraphs = site_config.get("min_paragraphs")
            unwanted_content = site_config.get("unwanted_content", [])

            logger.info(f"[Session] Scraping ({self.articles_scraped + 1}/{self.max_articles_per_driver}): {url}")

            # Navigate with a realistic referrer instead of cold direct access
            _set_referrer_and_navigate(self.driver, url, referrer="https://www.google.com/")
            _human_pause(2.0, 4.0)

            # Fast-fail bot check
            if _is_blocked(self.driver.page_source):
                logger.warning(f"[Session] Blocked on {url}, rotating driver...")
                self._rotate_driver(reason="Blocked on article page")
                return ""

            content_fully_loaded, xml_content, result, publish_at = _wait_for_progressive_content(
                self.driver,
                selector=selector,
                xml_selector=xml_selector,
                unwanted_content=unwanted_content,
                timeout=15,
                min_paragraphs=min_paragraphs,
            )

            result["content_fully_loaded"] = content_fully_loaded
            title = result.get("title")
            content = result.get("content")

            self.articles_scraped += 1

            if content and title:
                logger.info("[Session] Translating to Chinese...")
                try:
                    translation_result = translate_article_to_chinese(title, content)
                    title_zh = translation_result.get('title_zh', '')
                    content_zh = translation_result.get('content_zh', '')
                except Exception as e:
                    logger.error(f"Translation failed: {e}")
                    title_zh = ""
                    content_zh = ""

                try:
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
                    logger.info(f"[Session] Saved to DB: {url}")
                except Exception as db_e:
                    logger.error(f"[Session] DB save failed: {db_e}")

        except Exception as e:
            logger.error(f"[Session] Error scraping {url}: {e}")
            # Rotate on unexpected errors — the session may be poisoned
            self._rotate_driver(reason=f"Unexpected error: {e}")

        finally:
            logger.info(f"[Session] Content length: {len(content)}")

        if content.strip():
            return content.replace('\n\n', '\n').replace('\n', ' ')
        return ""

    def close(self):
        """Explicit cleanup."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


# Keep the original scrape_news for backward compatibility
def scrape_news(url: str, source: str):
    """Single-shot scrape (used for backward compatibility / standalone calls)."""
    session = ArticleScrapeSession(max_articles_per_driver=1)
    try:
        result = session.scrape(url, source)
        if not result:
            raise ValueError(f"Empty content from {url}")
        return result
    finally:
        session.close()