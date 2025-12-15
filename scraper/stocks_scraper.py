
import json
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from urllib.parse import urljoin
import os
from loguru import logger
from pwright.context_manager import playwright_context
from pwright.page_factory import new_stealth_page
from utils.database import get_db_manager, StockRepository
from utils.interactive import safe_click

# Configure loguru for stocks scraper module
logger.add("logs/stocks/stocks_scraper_{time:YYYY-MM-DD}.log", rotation="00:00", retention="15 days", encoding="utf-8")

def main_scraper(date):
    # Load selectors and cookies once
    with open("json/selectors.json", "r") as f:
        selectors_map = json.load(f)["stocks"]
    with open("json/cookies.json", "r") as f:
        cookies_map = json.load(f)["common"]

    all_records = []

    # Iterate all URLs in the stocks field of selectors.json
    for url, selectors in selectors_map.items():
        storage_state = cookies_map.get(selectors["name"]) if selectors.get("cookies") else None
        with playwright_context(storage_state=storage_state, bypass_ext_path="") as context:
            page = new_stealth_page(context)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=100_000)
                logger.info("Page loaded.")
                
                # Scrape records
                records = _scrape_stocks(
                    page,
                    date,
                    click_selector=selectors["click_selector"],
                    row_selector=selectors["row_selector"],
                    name_selector=selectors["name_selector"],
                    code_selector=selectors["code_selector"],
                    analysis_selector=selectors["analysis_selector"]
                )

                if records:
                    all_records.extend(records)
                    logger.info(f"Scraped {len(records)} records from {url}")
                else:
                    logger.warning(f"No records scraped from {url}")
                
                # Scrape images if configured
                if selectors.get("image"):
                    scraped_dir = "images"
                    if not os.path.isdir(scraped_dir):
                        os.makedirs(scraped_dir, exist_ok=True)
                    else:
                        for fname in os.listdir(scraped_dir):
                            file_path = os.path.join(scraped_dir, fname)
                            try:
                                if os.path.isfile(file_path):
                                    os.remove(file_path)
                            except Exception as e:
                                logger.warning(f"Could not delete {file_path}: {e}")
                    _scrape_images(page, url)

            except Exception as e:
                logger.error(f"Error scraping {url}: {e}")

    # Save all records at once
    if all_records:
        db_manager = get_db_manager()
        repo = StockRepository(db_manager)
        count = repo.insert_or_update_stocks(all_records)
        logger.info(f"Saved {count} stock records to database (inserted or analysis-merged) for {date}")
        result = repo.get_today_stocks()
        logger.info(f"Fetched {len(result)} stocks for today ({date}) from database")
        with open(f"stocks_analysis.txt", "w", encoding="utf-8") as f:
            for item in result:
                line = f"{item['stock']}\t{item['code']}\n{item['analysis']}\n\n"
                f.write(line)
        

def _scrape_stocks(page, date, click_selector, row_selector, name_selector, code_selector, analysis_selector) -> list[dict] | None:
    """
    Generic scraper for stock tables.
    Parameters:
        driver          : Selenium driver (already loaded on target URL)
        date            : current day
        row_selector    : CSS selector for each stock row (e.g. "ul.td-box li.row" or "tbody.hit-pool__table-body tr")
        name_selector   : CSS selector inside row for stock name (e.g. ".fs15-bold" or ".stock-title-name")
        code_selector   : CSS selector inside row for stock code (e.g. ".fs12-bold-ash" or ".stock-title a")
        analysis_selector: CSS selector inside row for analysis text (e.g. "pre" or ".stock-reason span.line-clamp")
    Returns:
        List of dicts with keys: date, stock, code, analysis
    """
    try:
        if click_selector:
            safe_click(page, f"xpath={click_selector}")

        # Wait until at least one row is present
        page.wait_for_selector(row_selector, state="attached", timeout=15_000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select(row_selector)
        records = []
        for row in rows:
            name_tag = row.select_one(name_selector)
            code_tag = row.select_one(code_selector)
            analysis_tag = row.select_one(analysis_selector)

            if not name_tag or not code_tag or not analysis_tag:
                continue

            name = name_tag.get_text(strip=True)
            code_raw = code_tag.get_text(strip=True)
            numbers = re.findall(r'\d{6}', code_raw)
            code = numbers[0] if numbers else code_raw

            analysis = analysis_tag.get_text(" ", strip=True)

            records.append({
                "date": date,
                "stock": name,
                "code": code,
                "analysis": analysis
            })

        return records if records else None

    except Exception as e:
        logger.error(f"Error scraping stocks: {e}")
        return None

    
def _scrape_images(page, url) -> str | None:
    logger.info(f"Scraping 涨停简图 from: {url}")
    button_locator = "xpath=//div[contains(@class,'yd-tabs_item')]/div[text()='涨停简图']"
    safe_click(page, button_locator)
    try:
        page.wait_for_selector("div#QR-code img", timeout=20_000)
        logger.info("Image area loaded.")
    except PlaywrightTimeoutError:
        return None
       
    soup = BeautifulSoup(page.content(), "html.parser")

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
                # Use a more descriptive name for the single image
                img_name = os.path.join("images", f"涨停简图.png") 
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
