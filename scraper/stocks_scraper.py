

import json
import re
import requests
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin
import os
from config import setup_logging, get_chrome_driver
from utils.interactive import safe_click
from utils.database import get_db_manager, StockRepository
logger = setup_logging("logs/stocks", "stocks_scraper")

def main_scraper(date):
    # Load selectors and cookies once
    with open("json/selectors.json", "r") as f:
        selectors_map = json.load(f)["stocks"]
    with open("json/cookies.json", "r") as f:
        cookies_map = json.load(f)["common"]

    all_records = []

    # Iterate all URLs in the stocks field of selectors.json
    for url, selectors in selectors_map.items():
        driver = get_chrome_driver(base_bypass_ext_path="")
        logger.info(f"Driver launched for {url}")
        try:
            driver.get(url)
            logger.info("Page loaded.")

            # Handle cookies if needed
            if selectors.get("cookies"):
                cookies = cookies_map.get(selectors["name"], [])
                for cookie in cookies["cookies"]:
                    driver.add_cookie(cookie)
                driver.refresh()
                logger.info("Cookies added and page refreshed.")

            # Scrape records
            records = _scrape_stocks(
                driver,
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
                _scrape_images(driver, url, date)

        except Exception as e:
            logger.error(f"Error scraping {url}: {e}")
        finally:
            try:
                driver.quit()
            except:
                pass

    # Save all records at once
    if all_records:
        db_manager = get_db_manager()
        repo = StockRepository(db_manager)
        count = repo.insert_or_update_stocks(all_records)
        logger.info(f"Data saved to database for {date}, total {count} records")
        result = repo.get_today_stocks()
        with open(f"stocks_{date}.txt", "w", encoding="utf-8") as f:
            for item in result:
                line = f"{item['stock']}\t{item['code']}\n{item['analysis']}\n\n"
                f.write(line)
        

def _scrape_stocks(driver, date, click_selector, row_selector, name_selector, code_selector, analysis_selector) -> list[dict] | None:
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
            safe_click(driver, (By.XPATH, click_selector))

        # Wait until at least one row is present
        for _ in range(3):
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, row_selector))
                )
                # Now get the updated DOM
                html = driver.page_source
                soup = BeautifulSoup(html, "html.parser")
                rows = soup.select(row_selector)
                break
            except:
                pass

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
        print(f"Error scraping stocks: {e}")
        return None

    
def _scrape_images(driver, url, date) -> str | None:
    logger.info(f"Scraping 涨停简图 from: {url}")
    button_locator = (
        By.XPATH,
        "//div[contains(@class,'yd-tabs_item')]/div[text()='涨停简图']"
    )
    safe_click(driver, button_locator)
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#QR-code img")))
        logger.info("Image area loaded.")
    except:
        return None
       
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
    TARGET_URL = ["https://xuangutong.com.cn/dingpan"]
    main_scraper(TARGET_URL, "2025-12-02")
    #print(result)