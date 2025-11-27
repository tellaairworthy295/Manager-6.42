
import os
import re
from bs4 import BeautifulSoup
import time
import sqlite3
from docx import Document
from selenium.webdriver.common.by import By
from utils.translation_service import translate_article_to_chinese
from datetime import datetime, timedelta
from config import setup_logging, get_chrome_driver, TranslationConfig as tc, EmailConfig #, cleanup_driver_dirs
from utils.upload_knowledge import upload_flow
from utils.sender import send_email_with_attachments

logger = setup_logging("logs/rb", "rb_scraper")

def fetch_data():
    conn = sqlite3.connect("scraped_data.db")
    cursor = conn.cursor()
    six_hours_ago = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("SELECT url, title, content FROM rb_articles WHERE content_fully_loaded=1 AND scraped_at >= ?", (six_hours_ago,))
    results = cursor.fetchall()
    logger.info(f"fetched {len(results)} articles")
    conn.close()
    combined = [{"url": url, "title": title, "content": content} for url, title, content in results]
    return combined
    
def fetch_urls_from_page(search_url: str = "https://www.google.com/search?q=site:bloomberg.com/news/articles&tbs=qdr:h,sbd:1&tbm=nws"):
    driver = get_chrome_driver()
    driver.get(search_url)

    # Wait for at least one article link with "bloomberg.com/news/articles" to appear
    count = 0
    while count < 100:
        try:
            # Extract hrefs
            links = []
            for a in driver.find_elements(By.CSS_SELECTOR, "a"):
                href = a.get_attribute("href")
                if not href:
                    continue
                if "bloomberg.com/news/articles" in href:
                    links.append(href)
            break
        except:
            time.sleep(1)
            count = count + 1
            logger.warning("waiting for article links to load")

    driver.quit()

    # Remove duplicates
    links = [link for link in links if link.startswith("https://www.bloomberg.com")]
    links = list(set(links))
    
    # Fetch URLs that were scraped within the last hour from the database
    recent_links = []
    conn = sqlite3.connect('scraped_data.db')
    _ensure_articles_table(conn)
    # There are no major problems, but here is a simplified, slightly more robust rewrite:
    with conn:
        cursor = conn.cursor()
        one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            "SELECT url FROM rb_articles WHERE scraped_at >= ? AND content_fully_loaded = 1",
            (one_hour_ago,)
        )
        recent_links = [row[0] for row in cursor.fetchall()]
    
    links = [link for link in links if not any(link == u for u in recent_links)]
    print("Found", len(links), "Bloomberg article links:")
    return links

def export_content_to_dify(db_path: str = 'scraped_data.db', output_dir: str = 'docx'):
    # Create output directory if not exists
    os.makedirs(output_dir, exist_ok=True)

    # Delete all existing .docx files in the directory first
    for f in os.listdir(output_dir):
        if f.lower().endswith('.docx'):
            try:
                os.remove(os.path.join(output_dir, f))
                logger.info(f"removed {f}")
            except Exception as e:
                logger.warning(f"Failed to delete {f}: {e}")

    def sanitize_filename(name):
        # Remove or replace characters not allowed in filenames
        name = name.strip()
        name = re.sub(r'[\\/*?:"<>|]', "_", name)
        return name[:100]  # trim long names to avoid OS issues

    # Connect to the SQLite database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Read all content from the content column
    cursor.execute("""
        SELECT title, content 
        FROM rb_articles 
        WHERE content_fully_loaded = 1 AND scraped_at >= datetime('now', '-15 minutes')
    """)
    rows = cursor.fetchall()
    exported_files = []
    for title, content in rows:
        if title and content:
            safe_title = sanitize_filename(title)
            docx_path = os.path.join(output_dir, f"{safe_title}.docx")
            doc = Document()
            doc.add_paragraph(title)
            doc.add_paragraph()  # Blank line
            doc.add_paragraph(content)
            doc.save(docx_path)
            exported_files.append(docx_path)

    cursor.close()
    conn.close()
    
    upload_flow("docx")

# ============================== Helper Functions ==============================================

def _wait_for_progressive_content(driver, timeout=60, min_paragraphs=5, paragraph_selector="article p, div.body-content p"):
    start = time.time()
    last_count = 0
    stable_count = 0
    content_fully_loaded = False
    
    while time.time() - start < timeout:
        # Scroll to the bottom of the page one time
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(4)
            
        soup = BeautifulSoup(driver.page_source, "html.parser")
        paragraphs = soup.select(paragraph_selector)
        count = len(paragraphs)
        if count > last_count:
            last_count = count
            stable_count = 0
        else:
            stable_count += 1
        if count > min_paragraphs and stable_count > 2:
            content_fully_loaded = True
            break
    logger.info(f"content fully loaded: {content_fully_loaded}")
    return content_fully_loaded

def _extract_bloomberg_content(soup):
    content_div = soup.find("div", class_="body-content")
    if content_div:
        paragraphs = [p.get_text(" ", strip=True) for p in content_div.find_all("p")]
    else:
        article = soup.find("article")
        if article:
            paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")]
        else:
            paragraphs = []

    unwanted_patterns = [
        "Bloomberg may send me offers and promotions.",
        "By submitting my information, I agree to the Privacy Policy and Terms of Service"
    ]
    filtered_paragraphs = [p for p in paragraphs if not any(pattern in p for pattern in unwanted_patterns)]
    content = "\n\n".join(filtered_paragraphs)
    title = soup.find("h1")
    title_text = title.get_text(strip=True) if title else "No title found"
    content_text = content if content else "No article content found"
    return {"title": title_text, "content": content_text}

def _extract_reuters_content(soup):
    paragraphs = []
    for p_tag in soup.find_all("div", class_="article-body-module__content__bnXL1"):
        for paragraph_text in p_tag.find_all(lambda tag: tag.name == "div" and "paragraph" in tag.get("data-testid", "")):
            paragraphs.append(paragraph_text.get_text(" ", strip=True))
    content = "\n\n".join(paragraphs)
    title = soup.find("h1")
    title_text = title.get_text(strip=True) if title else "No title found"
    content_text = content if content else "No article content found"
    return {"title": title_text, "content": content_text}

def _ensure_articles_table(conn):
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS rb_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            source TEXT,
            title TEXT,
            content TEXT,
            title_zh TEXT,
            content_zh TEXT,
            content_fully_loaded INTEGER,
            translation_status INTEGER,
            scraped_at Datetime
        )
        '''
    )
    conn.commit()
    
def _ensure_aB_table(conn):
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS aB_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titles TEXT,
            analyze_result TEXT,
            analyze_time Datetime
        )
        '''
    )
    conn.commit()

def save_agent_data(analyze_result: str):
    analyze_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = sqlite3.connect('scraped_data.db')
        _ensure_aB_table(conn)
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO aB_result (titles, analyze_result, analyze_time)
            VALUES (?, ?, ?)
            ''',
            (
                "NA",
                analyze_result,
                analyze_time
            )
        )
        conn.commit()
        logger.info(f"Saved {cursor.rowcount} records to aB_result table(agent) in SQLite DB")
    finally:
        conn.close()
    
    EmailConfig.BODY = (
        f"AI分析(Agent):\n\n{analyze_result}\n\n"
    )
    send_email_with_attachments(**EmailConfig.as_dict())
    
def save_aB_data(titles: list[str], analyze_result: str, analyze_time=None):
    conn = sqlite3.connect('scraped_data.db')
    try:
        _ensure_aB_table(conn)
        if analyze_time is None:
            analyze_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Convert titles list to a string for storage (e.g., join with newlines)
        titles_str = "\n".join(titles) if isinstance(titles, list) else str(titles)

        # Insert into aB_result
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO aB_result (titles, analyze_result, analyze_time)
            VALUES (?, ?, ?)
            ''',
            (
                titles_str,
                analyze_result,
                analyze_time
            )
        )
        conn.commit()
        logger.info(f"Saved {cursor.rowcount} records to aB_result table in SQLite DB")

        # Get URLs corresponding to the titles in rb_articles
        title_list = [t.split(".")[0].strip() for t in titles if t.strip()] if isinstance(titles, list) else [t.strip() for t in titles.split("\n") if t.strip()]
        if title_list:
            article_info = []
            for t in title_list:
                cursor.execute(
                    "SELECT url, title, article_result FROM rb_articles WHERE title LIKE ?",
                    (t + '%',)
                )
                for row in cursor.fetchall():
                    # row: (url, title, article_result)
                    url = row[0] or ""
                    title = row[1] or ""
                    article_result = row[2] or ""
                    article_info.append(f"[{title}]\nURL: {url}\n摘要: {article_result}\n" + ("-" * 30))
    finally:
        conn.close()

    articles_block = "\n".join(article_info) if article_info else "(未找到相关文章信息)"
    EmailConfig.BODY = (
        f"AI分析:\n{analyze_result}\n\n"
        f"分析的文章为:\n{titles_str}\n\n"
        f"对应文章详情:\n{articles_block}"
    )
    send_email_with_attachments(**EmailConfig.as_dict())
        
          
def _insert_article(url, source, title, content, content_fully_loaded, title_zh=None, content_zh=None, translation_status=0):
    conn = sqlite3.connect('scraped_data.db')
    try:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO rb_articles (url, source, title, content, title_zh, content_zh, content_fully_loaded, translation_status, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                source=excluded.source,
                title=excluded.title,
                content=excluded.content,
                title_zh=excluded.title_zh,
                content_zh=excluded.content_zh,
                content_fully_loaded=excluded.content_fully_loaded,
                translation_status=excluded.translation_status,
                scraped_at=excluded.scraped_at
            ''',
            (
                url,
                source,
                title,
                content,
                title_zh,
                content_zh,
                1 if content_fully_loaded else 0,
                translation_status,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
        )
        conn.commit()
        logger.info(f"Saved {cursor.rowcount} rb_articles to SQLite DB")
    finally:
        conn.close()
        
# ============ Main Scraping Function =============

def scrape_article(url: str, min_paragraphs=5):
    
    driver = get_chrome_driver()
    logger.info("Driver launched")

    try:
        logger.info(f"Scraping URL: {url}")
        driver.get(url)
        logger.info("Page loaded")

        content_fully_loaded = False
        if "bloomberg.com" in url:
            content_fully_loaded = _wait_for_progressive_content(driver, timeout=60, min_paragraphs=min_paragraphs, paragraph_selector="article p, div.body-content p")
        elif "reuters.com" in url:
            content_fully_loaded = _wait_for_progressive_content(driver, timeout=60, min_paragraphs=min_paragraphs, paragraph_selector="div.article-body-module__content__bnXL1 div[data-testid^='paragraph-']")
        else:
            content_fully_loaded = _wait_for_progressive_content(driver, timeout=60, min_paragraphs=min_paragraphs)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        if "bloomberg.com" in url:
            result = _extract_bloomberg_content(soup)
        elif "reuters.com" in url:
            result = _extract_reuters_content(soup)
        else:
            logger.warning("Unknown domain, unable to scrape.")
            result = {"title": "Unknown", "content": "Unknown"}

        result["content_fully_loaded"] = content_fully_loaded

        title = result.get("title", "")
        content = result.get("content", "")
        translation_status = 0
        title_zh = None
        content_zh = None

        if tc.ENABLE_TRANSLATION and (title or content):
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

        source = "bloomberg" if "bloomberg.com" in url else ("reuters" if "reuters.com" in url else "unknown")
        try:
            _insert_article(url, source, result.get("title", ""), result.get("content", ""), content_fully_loaded, 
                        title_zh, content_zh, translation_status)
            
        except Exception as db_e:
            logger.error(f"DB save failed for {url}: {db_e}")

        # result["title_zh"] = title_zh
        # result["content_zh"] = content_zh
        # result["translation_status"] = translation_status
        # return result
    finally:
        driver.quit()

# === Example Usage ===
if __name__ == "__main__":

    export_content_to_dify()
