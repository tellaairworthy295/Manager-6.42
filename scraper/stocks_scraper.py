import json
import os
import asyncio
import re
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from exception.exception_handler import NetworkException
from pwright.async_pm import AsyncPlaywrightManager
from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page
from utils.database import get_db_manager, StockRepository
from utils.interactive import async_safe_click
from utils.logging_config import get_stock_logger
from pathlib import Path

logger = get_stock_logger()


async def main_scraper(date: str, today: bool = True) -> bool:
    # Load selectors & cookies once
    # 获取项目根目录路径（假设项目根目录是 D:\dify_server）
    PROJECT_ROOT = Path(__file__).parent.parent  # 从 scraper/ 向上两级到 dify_server/

    # 使用绝对路径
    with open(PROJECT_ROOT / "json/selectors.json", "r", encoding="utf-8") as f:
        selectors_map = json.load(f)["stocks"]

    with open(PROJECT_ROOT / "json/common/cookies.json", "r", encoding="utf-8") as f:
        cookies_map = json.load(f)

    all_records: list[dict] = []
    market_number = None
    manager = AsyncPlaywrightManager()
    await manager.start()

    try:
        for url, selectors in selectors_map.items():
            storage_state = (
                cookies_map.get(selectors["name"])
                if selectors.get("cookies")
                else None
            )

            logger.info(f"Scraping stocks from {url}")

            try:
                async with PlaywrightContext(
                        manager,
                        storage_state=storage_state,
                        ignore_https_errors=True,
                ) as context:
                    page = await new_stealth_page(context)

                    try:
                        if not today and "jiuyan" in url:
                            url = url + f"/{date}"
                        elif not today:
                            break
                        await page.goto(url, wait_until="domcontentloaded", timeout=50000)
                    except Exception as e:
                        raise NetworkException(f"Network Error. Failed to load {url}: {str(e)}")
                    logger.info("Page loaded.")

                    data = await _scrape_stocks_async(
                        site=selectors["name"],
                        page=page,
                        date=date,
                        click_selector=selectors.get("click_selector"),
                        row_selector=selectors["row_selector"],
                        name_selector=selectors["name_selector"],
                        code_selector=selectors["code_selector"],
                        analysis_selector=selectors["analysis_selector"],
                        market_number_selector=selectors["market_number"]
                    )
                    if data and data["records"]:
                        all_records.extend(data["records"])
                        logger.info(f"Scraped {len(data["records"])} records from {url}")
                    else:
                        logger.warning(f"No records scraped from {url}")
                        return False, market_number

                    market_number = data["market_number"] if data["market_number"] else market_number
                    # Optional image scraping
                    if selectors.get("image"):
                        await _prepare_image_dir("images")
                        await _scrape_images_async(page, url)

            except Exception as e:
                logger.exception(f"Error scraping {url}: {e}")
    finally:
        await manager.shutdown()

    # Save DB results
    if all_records:
        db_manager = get_db_manager()
        repo = StockRepository(db_manager)

        count = repo.insert_or_update_stocks(all_records)
        logger.info(
            f"Saved {count} stock records to database for {date}"
        )

        # result = repo.get_today_stocks()
        # await asyncio.to_thread(_write_analysis_file, result, date)

    return True, market_number


async def _scrape_stocks_async(
        *,
        page,
        site: str,
        date: str,
        click_selector: str | None,
        row_selector: str,
        name_selector: str,
        code_selector: str,
        analysis_selector: str,
        market_number_selector: dict | None
) -> list[dict] | None:
    try:
        if click_selector:
            await async_safe_click(
                page,
                f"xpath={click_selector}",
                timeout=5_000,
                max_attempts=3,
            )
        # Wait for rows and the first .market-color--red within #fluctuation-title
        await page.wait_for_selector(
            row_selector,
            state="attached",
            timeout=30_000,
        )

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        market_number = {}
        if market_number_selector:
            for label, selector in market_number_selector.items():
                await page.wait_for_selector(
                    selector,
                    state="visible",
                    timeout=30_000,
                )
                elements = soup.select(selector)
                # Extract the plain number (text) from the first matching element,
                # or None if not found.
                if elements and elements[0]:
                    text = elements[0].get_text(strip=True)
                    # Try to extract int, fallback to text if not possible.
                    try:
                        number = int(re.sub(r"[^\d]", "", text))
                    except Exception:
                        number = text
                    market_number[label] = number
                else:
                    market_number[label] = None

        rows = soup.select(row_selector)
        records: list[dict] = []

        for row in rows:
            name_tag = row.select_one(name_selector)
            code_tag = row.select_one(code_selector)
            analysis_tag = row.select_one(analysis_selector)

            if not name_tag or not code_tag or not analysis_tag:
                continue

            name = name_tag.get_text(strip=True)
            name = re.sub(r'\s+', '', name)
            code_raw = code_tag.get_text(strip=True)
            numbers = re.findall(r"\d{6}", code_raw)
            code = numbers[0] if numbers else code_raw

            analysis = analysis_tag.get_text(" ", strip=True)
            analysis = "韭研:" + "\n" + analysis if site == "jiuyan" else "选股通:" + "\n" + analysis
            records.append(
                {
                    "date": date,
                    "stock": name,
                    "code": code,
                    "analysis": analysis,
                }
            )

        return {
            "market_number": market_number,
            "records": records
        }

    except Exception as e:
        logger.exception(f"Error scraping stocks: {e}")
        return None


async def _scrape_images_async(page, url: str) -> str | None:
    logger.info(f"Scraping 涨停简图 from: {url}")

    button_locator = (
        "xpath=//div[contains(@class,'yd-tabs_item')]/div[text()='涨停简图']"
    )

    await async_safe_click(
        page,
        button_locator,
        timeout=5_000,
        max_attempts=3,
    )

    try:
        await page.wait_for_selector(
            "div#QR-code img",
            timeout=30_000,
        )
        logger.info("Image area loaded.")
    except PlaywrightTimeoutError:
        return None

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    img_tag = soup.select_one("div#QR-code img")

    if not img_tag:
        logger.info("Target image not found.")
        return None

    img_url = img_tag.get("src")
    if not img_url:
        return None

    if not img_url.startswith(("http://", "https://")):
        img_url = urljoin(url, img_url)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(img_url) as resp:
                resp.raise_for_status()
                data = await resp.read()

        img_path = os.path.join("images", "Image.png")
        await asyncio.to_thread(
            lambda: open(img_path, "wb").write(data)
        )

        logger.info(f"Downloaded image: {img_url}")
        return img_url

    except Exception as e:
        logger.warning(f"Failed to download image: {e}")
        return None


async def _prepare_image_dir(path: str):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        return

    for fname in os.listdir(path):
        fp = os.path.join(path, fname)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
            except Exception as e:
                logger.warning(f"Could not delete {fp}: {e}")


# def _write_analysis_file(result: list[dict], date):
#     with open(f"excel/{date}.txt", "w", encoding="utf-8") as f:
#         for item in result:
#             f.write(
#                 f"{item['stock']}\t{item['code']}\n"
#                 f"{item['analysis']}\n\n"
#             )

# if __name__ == "__main__":
#     asyncio.run(main_scraper("2026-02-02", False))
