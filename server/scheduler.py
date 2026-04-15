from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from server.jobs import (
    scheduled_scrape_records_job,
    scheduled_scrape_stocks_job,
    scheduled_update_common_cookies,
)


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scheduled_scrape_records_job,
        trigger=CronTrigger(hour="8,12,15,17,20,23", minute=45),
        id="scrape_records",
        name="Scheduled Record Scraper",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_scrape_stocks_job,
        trigger=CronTrigger(hour="12,15", minute=40),
        id="scrape_stocks",
        name="Scheduled Stocks Scraper",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_update_common_cookies,
        trigger=CronTrigger(hour="23", minute=55),
        id="update_common_cookies",
        name="Scheduled cookies updater",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )
    return scheduler
