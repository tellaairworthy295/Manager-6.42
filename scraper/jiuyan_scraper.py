
import re
from docx import Document
import requests
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains  # Add this import
from urllib.parse import urljoin
import time
import sqlite3
import os
from datetime import datetime
from config import setup_logging, get_chrome_driver

logger = setup_logging("logs/jiuyan", "jiuyan_scraper")

# You need to provide these details. Replace with your actual credentials.
USERNAME = "18282219269"
PASSWORD = "a123456BBC"

def safe_click(driver, element):
    """Try JS click first, fallback to ActionChains click."""
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        try:
            ActionChains(driver).move_to_element(element).click().perform()
            return True
        except Exception as e:
            logger.warning(f"Safe click failed: {e}")
            return False
    
def _login(driver, username, password):
    logger.info("Attempting to log in via dialog.")
    try:
        wait = WebDriverWait(driver, 2)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.el-dialog__wrapper")))
    except:
        logger.warning("No dialog detected.")
        return
    logger.info("Login dialog detected.")
    time.sleep(0.7)

    # Click the '账号密码登录' tab using safe_click
    account_login_tab = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div#tab-accounts")))
    if not safe_click(driver, account_login_tab):
        logger.error("Failed to click '账号密码登录' tab")
        return
    logger.info("Clicked '账号密码登录' tab.")
    time.sleep(0.7)

    # Enter username (phone number field in the account login tab)
    username_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#pane-accounts input[name='phone']")))
    username_input.clear()  # Clear the field before sending keys
    username_input.send_keys(username)
    logger.info("Username entered.")
    time.sleep(0.7)

    # Enter password
    password_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#pane-accounts input[name='password']")))
    password_input.clear()  # Clear the field before sending keys
    password_input.send_keys(password)
    logger.info("Password entered.")
    time.sleep(0.7)

    # Click login button using safe_click
    login_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div#pane-accounts button.el-button--primary")))
    safe_click(driver, login_button)
        
def get_stocks_from_db():
    stocks = []
    conn = None
    try:
        conn = sqlite3.connect('scraped_data.db')
        cursor = conn.cursor()
        cursor.execute("SELECT stocks FROM jiuyan_data")
        row = cursor.fetchone()
        if row and row[0]:
            stocks = [stock.strip() for stock in row[0].split('\n') if stock.strip()]
    except Exception as e:
        logger.error(f"Error retrieving stocks from DB: {e}")
    finally:
        if conn:
            conn.close()
    return stocks[10:12]
    
def _insert_jiuyan_data(date, image_path, stocks, article_content):
    """
    Insert or update a row in jiuyan_data based on the unique date.
    """
    conn = sqlite3.connect('scraped_data.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jiuyan_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            image_path TEXT,
            stocks TEXT,
            article_content TEXT,
            alpha_result TEXT,
            scraped_at Datetime
        )
    ''')

    cursor.execute('''
        INSERT INTO jiuyan_data (date, image_path, stocks, article_content, scraped_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            image_path=excluded.image_path,
            stocks=excluded.stocks,
            article_content=excluded.article_content,
            scraped_at=excluded.scraped_at
    ''', (date, image_path, stocks, article_content, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()

def update_jiuyan_results(date, files):
    """
    Update article_result and scraped_at for the given date.
    """
    content = []
    for file_path in files:
        # Read docx file content
        try:
            doc = Document(file_path)
            content.append('\n'.join([para.text for para in doc.paragraphs]))
        except Exception as e:
            logger.error(f"Failed to process docx file {file_path}: {e}")
    
    # Join all contents into a single string separated by double newlines
    content_str = "\n\n".join(content)

    conn = sqlite3.connect('scraped_data.db')
    cursor = conn.cursor()

    # This update assumes the row exists for the specified date.
    # If the row does not exist, nothing is updated.
    cursor.execute('''
        UPDATE jiuyan_data SET alpha_result=?, scraped_at=? WHERE date=?
    ''', (content_str, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), date))
    conn.commit()
    conn.close()
    logger.info("Saved jiuyan alpha_result to SQLite database")

def scrape_jiuyan(url, date):
    driver = get_chrome_driver("")
    logger.info("Driver launched")
    
    driver.get(url)
    logger.info("Page loaded.")

    # Check if the login dialog is already present
    try:
        WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.el-dialog__wrapper")))
        logger.info("Login dialog already present, skipping '登录注册' button click.")
    except TimeoutException:
        # Click the '登录注册' button using safe_click
        login_register_button = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.name.lf")))
        if not safe_click(driver, login_register_button):
            logger.error("Failed to click '登录注册' button")
            return None
        logger.info("Clicked '登录注册' button to open login dialog.")
    
    while True:
        try:
            WebDriverWait(driver, 8).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.el-dialog__wrapper")))
            logger.info("Login dialog disappeared, successful login.")
            break
        except TimeoutException:
            logger.warning("waiting for login")
            _login(driver, USERNAME, PASSWORD)
    
    content = _scrape_article_content(driver)
    image_path = _scrape_images(driver, url, date)
    article_content = content.get("articles", "")
    stocks = content.get("stocks", "")
    driver.quit()
    
    if not image_path or not article_content or not stocks:
        logger.error(f"image: {image_path}\n stocks: {len(stocks)} \n articles: {len(article_content)}")
        return None
    # Save data to SQLite database
    _insert_jiuyan_data(date, image_path, "\n".join(stocks), "\n\n".join(article_content))
    logger.info(f"Data saved to SQLite database: {date}")
    
    return stocks

def _scrape_article_content(driver) -> dict | None:
    articles = []
    try:
        tab_button = driver.find_element(
            By.XPATH,
            "//div[contains(@class,'yd-tabs_item')]/div[contains(text(),'全部异动解析')]"
        )
        safe_click(driver, tab_button)
        while True:
            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a.color-444"))
                )
                logger.info("articles loaded")
                break
            except:
                logger.warning("waiting for articles")
                safe_click(driver, tab_button)
                
        soup_articles = BeautifulSoup(driver.page_source, "html.parser")
        article_blocks = soup_articles.select("a.color-444")
        stock_blocks = soup_articles.select("div.hsh-flex-many-center.td")
        
        stocks = []
        for block in stock_blocks:
            # Extract all visible texts inside this block
            texts = [t.strip() for t in block.stripped_strings if t.strip()]
            if not texts:
                continue
            # Detect code (usually like sz000407 / sh600519 / 000001)
            code = next((t for t in texts if re.search(r"^(?:[a-zA-Z]{2})?\d{6}(?:\.[a-zA-Z]{2})?$", t)), None)
            if not code:
                logger.warning("no code")
                continue
            # Convert to 000001.SH format if it's like sz000001 or sh600519
            # Match sh000001, sz000001, etc.
            m = re.match(r'([a-zA-Z]{2})(\d{6})', code)
            if m:
                code_formatted = f"{m.group(2)}.{m.group(1).upper()}"
            else:
                # If code is already in 000001.SH or has different format, leave unchanged
                code_formatted = code
            # Everything else considered as name
            name = next((t for t in texts if t != code), None)
            stock = f"{name}（{code_formatted}）"
            stocks.append(stock)
        logger.info(f"Found {len(stocks)} stocks")
        for article_block in article_blocks:
            article = article_block.get_text()
            if article:
                articles.append(article)
            
    except Exception as click_e:
        logger.info(f"Error clicking '全部异动解析' tab: {click_e}")
            
    # Remove duplicate URLs while preserving order
    if not articles or not stocks:
        logger.error(f"articles: {len(articles)/2}\n stocks: {len(stocks)}")
        return None
    
    seen = set()
    unique_articles = []
    for u in articles:
        if u not in seen:
            unique_articles.append(u)
            seen.add(u)
    logger.info(f"Found {len(unique_articles)} unique articles (from {len(articles)} total).")
    # Group every 10 paragraphs into one new content block
    group_size = 10
    return {
            "articles":   [
                "\n\n".join(unique_articles[i:i + group_size])
                for i in range(0, len(unique_articles), group_size)
                ],
            "stocks": stocks
            }
    
def _scrape_images(driver, url, date) -> str | None:
    logger.info(f"Scraping 涨停简图 from: {url}")
    button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//div[contains(@class,'yd-tabs_item')]/div[text()='涨停简图']"
                ))
            )
    while True:
        try:
            WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#QR-code img")))
            logger.info("Image area loaded.")
            break
        except:
            logger.warning("waiting for image")
            safe_click(driver, button)
       
    soup = BeautifulSoup(driver.page_source, "html.parser")

    # Find the specific image element
    target_img_tag = soup.select_one("div#QR-code img")

    if target_img_tag:
        img_url = target_img_tag.get("src")
        if img_url:
            # Construct absolute URL if img_url is relative
            if not img_url.startswith(("http", "https")):
                img_url = urljoin(url, img_url)
            try:
                img_data = requests.get(img_url).content
                # Ensure the scraped_images directory exists
                os.makedirs("scraped_images", exist_ok=True)
                # Use a more descriptive name for the single image
                img_name = os.path.join("scraped_images", f"{date}.png") 
                with open(img_name, "wb") as handler:
                    handler.write(img_data)
                logger.info(f"Downloaded target image: {img_url}")
                return img_url
            except requests.exceptions.RequestException as e:
                logger.info(f"Error downloading {img_url}: {e}")
                return None
        else:
            logger.info("Target image found, but no src attribute.")
            return None
    else:
        logger.info("Target image ('涨停简图') not found after clicking the button.")
        return None


if __name__ == "__main__":
    TARGET_URL = "https://www.jiuyangongshe.com/action"
    result = scrape_jiuyan(TARGET_URL, "2025-10-24")
    print(result)