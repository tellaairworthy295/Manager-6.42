from datetime import datetime

from scraper.utils.scrape_utils import scrape_all_records, run_stocks_scrape
from utils.logging_config import get_others_logger

logger = get_others_logger()


async def scheduled_scrape_records_job():
    """Run the records scrape on the current event loop."""
    logger.info("[Scheduler] Triggering scheduled_scrape_records_job...")
    await scrape_all_records()


async def scheduled_scrape_stocks_job():
    """Run the stocks scrape and keep scheduler failures contained."""
    logger.info("[Scheduler] Triggering scheduled_scrape_stocks_job...")
    try:
        await run_stocks_scrape(datetime.now())
    except Exception as exc:
        logger.error("[Scheduler] scheduled_scrape_stocks_job failed: %s", exc, exc_info=True)


async def scheduled_update_common_cookies():
    from scraper.cookies_getter import update_common_cookies

    await update_common_cookies(["jiuyan", "xuangutong"], False)
