import asyncio
import json
from datetime import datetime, date

import pandas as pd
import pandas_market_calendars as mcal

from scraper.stocks_scraper import main_scraper
from image_process import main_flow
from scraper.records_scraper import scrape_website, scrape_history
from utils.logging_config import get_others_logger
from utils.validators import validate_and_prepare_cookies
import random

logger = get_others_logger()


# =====================================================
# Shared: Records scraping
# =====================================================

async def scrape_all_records():
    """
    Run record scrapers sequentially.
    scrape_website is async (Playwright), so it stays on the event loop.
    """
    #"564b3391-510f-4b50-a038-7df413bec15d"
    with open("json/locators.json", "r", encoding="utf-8") as f:
        record_maps = json.load(f)["alphapai_record"]
    user_id_list = ["1eeeb1dc-34c8-446c-879e-456f69762bf7", "weixue123", "564b3391-510f-4b50-a038-7df413bec15d"]
    random.shuffle(user_id_list)
    for url, locators in record_maps.items():
        for user_id in user_id_list:
            all_cookies = await validate_and_prepare_cookies(
                user_id, ["alphapai"], True
            )
            try:
                await scrape_website(
                    storage_state=all_cookies["alphapai"], url=url, locators=locators
                )
                break
            except Exception as e:
                logger.error(
                    f"[Records] {user_id} Scraper failed for {url} "
                    f"retry with another account: {e}"
                )


async def scrape_history_records(start: date, end: date):
    with open("json/locators.json", "r", encoding="utf-8") as f:
        record_maps = json.load(f)["alphapai_record"]
    for url, locators in record_maps.items():
        all_cookies = await validate_and_prepare_cookies(
            "564b3391-510f-4b50-a038-7df413bec15d", ["alphapai"], True
        )
        try:
            await scrape_history(
                storage_state=all_cookies["alphapai"], url=url, locators=locators, start=start, end=end
            )
            
        except Exception as e:
            logger.error(
                f"[Records] 564b3391-510f-4b50-a038-7df413bec15d Scraper failed for {url}: {e} ")


# =====================================================
# Shared: Stocks scraping
# =====================================================

def _is_a_share_trading_day(date_obj: datetime) -> bool:
    """
    Pure sync check — pandas_market_calendars is blocking.
    Called via asyncio.to_thread() by the caller.
    """
    exchange = mcal.get_calendar("XSHG")
    timestamp = pd.Timestamp(date_obj.date())
    start_date = timestamp - pd.Timedelta(days=1)
    end_date = timestamp + pd.Timedelta(days=1)
    schedule = exchange.schedule(start_date=start_date, end_date=end_date)
    return timestamp in schedule.index


async def run_stocks_scrape(date: datetime) -> dict:
    """
    Full stocks scrape pipeline. Returns a result dict consumed by both
    the API endpoint and the scheduler.

    Threading strategy:
    - _is_a_share_trading_day  → to_thread (sync, pandas blocking call)
    - main_scraper             → native async, runs on event loop
    - main_flow                → to_thread (CPU-bound image processing)
    """
    date_str = date.strftime("%Y-%m-%d")

    # Sync pandas call → offload to thread pool
    is_trading = await asyncio.to_thread(_is_a_share_trading_day, date)

    if not is_trading:
        logger.info(f"[Stocks] {date_str} is not a trading day, skipping.")
        return {
            "status": "skipped",
            "msg": "Not an A-share trading day",
            "date": date_str,
        }

    # Async scraper — stays on event loop
    flag, market_number = await main_scraper(date_str)

    if not flag:
        logger.warning(f"[Stocks] No data found for {date_str}.")
        return {
            "status": "success",
            "msg": "No data found",
            "date": date_str,
        }

    # CPU-bound image processing → offload to thread pool
    await asyncio.to_thread(main_flow, market_number, date_str, flag)

    logger.info(f"[Stocks] Scrape complete for {date_str}.")
    return {
        "status": "success",
        "date": date_str,
        "market": "A-share",
        "today_is_trading_day": True,
    }
