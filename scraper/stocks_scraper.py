import json
import os
import asyncio
import re
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from loguru import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from pwright.async_pm import AsyncPlaywrightManager
from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page
from utils.database import get_db_manager, StockRepository
from utils.interactive import async_safe_click


logger.add(
    "logs/stocks/stocks_scraper_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="15 days",
    encoding="utf-8"
)


async def main_scraper(date: str):
    # Load selectors & cookies once
    with open("json/selectors.json", "r", encoding="utf-8") as f:
        selectors_map = json.load(f)["stocks"]

    with open("json/cookies.json", "r", encoding="utf-8") as f:
        cookies_map = json.load(f)["common"]

    all_records: list[dict] = []

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

                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=100_000,
                    )
                    logger.info("Page loaded.")

                    records = await _scrape_stocks_async(
                        page=page,
                        date=date,
                        click_selector=selectors.get("click_selector"),
                        row_selector=selectors["row_selector"],
                        name_selector=selectors["name_selector"],
                        code_selector=selectors["code_selector"],
                        analysis_selector=selectors["analysis_selector"],
                    )

                    if records:
                        all_records.extend(records)
                        logger.info(f"Scraped {len(records)} records from {url}")
                    else:
                        logger.warning(f"No records scraped from {url}")

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

        result = repo.get_today_stocks()
        logger.info(f"Fetched {len(result)} stocks for today ({date})")

        await asyncio.to_thread(_write_analysis_file, result)

async def _scrape_stocks_async(
    *,
    page,
    date: str,
    click_selector: str | None,
    row_selector: str,
    name_selector: str,
    code_selector: str,
    analysis_selector: str,
) -> list[dict] | None:
    try:
        if click_selector:
            await async_safe_click(
                page,
                f"xpath={click_selector}",
                timeout=5_000,
                max_attempts=3,
            )

        # Wait for rows to appear
        await page.wait_for_selector(
            row_selector,
            state="attached",
            timeout=15_000,
        )

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        rows = soup.select(row_selector)
        records: list[dict] = []

        for row in rows:
            name_tag = row.select_one(name_selector)
            code_tag = row.select_one(code_selector)
            analysis_tag = row.select_one(analysis_selector)

            if not name_tag or not code_tag or not analysis_tag:
                continue

            name = name_tag.get_text(strip=True)
            code_raw = code_tag.get_text(strip=True)
            numbers = re.findall(r"\d{6}", code_raw)
            code = numbers[0] if numbers else code_raw

            analysis = analysis_tag.get_text(" ", strip=True)

            records.append(
                {
                    "date": date,
                    "stock": name,
                    "code": code,
                    "analysis": analysis,
                }
            )

        return records or None

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
            timeout=20_000,
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


def _write_analysis_file(result: list[dict]):
    with open("stocks_analysis.txt", "w", encoding="utf-8") as f:
        for item in result:
            f.write(
                f"{item['stock']}\t{item['code']}\n"
                f"{item['analysis']}\n\n"
            )
