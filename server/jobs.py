from datetime import datetime

from scraper.utils.scrape_utils import scrape_all_records, run_stocks_scrape, scrape_history_records
from utils.logging_config import get_others_logger

logger = get_others_logger()


async def scheduled_scrape_records_job():
    """Run the records scrape on the current event loop."""
    logger.info("[Scheduler] Triggering scheduled_scrape_records_job...")
    await scrape_all_records()

async def scheduled_scrape_history_records_job():
    """Run the historical records scrape on the current event loop."""
    logger.info("[Scheduler] Triggering scheduled_scrape_history_records_job...")
    start = datetime.strptime("2026-01-01", "%Y-%m-%d")
    end = datetime.strptime("2026-01-15", "%Y-%m-%d")
    await scrape_history_records(start, end)

async def scheduled_scrape_stocks_job():
    """Run the stocks scrape and keep scheduler failures contained."""
    logger.info("[Scheduler] Triggering scheduled_scrape_stocks_job...")
    await run_stocks_scrape(datetime.now())


async def scheduled_update_common_cookies():
    from scraper.cookies_getter import update_common_cookies

    await update_common_cookies(["jiuyan", "xuangutong"], False)
