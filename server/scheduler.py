from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from server.jobs import (
    scheduled_scrape_history_records_job,
    scheduled_scrape_records_job,
    scheduled_scrape_stocks_job,
    scheduled_update_common_cookies,
)


from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from utils.sender import send_email_with_attachments, load_email_config_from_json
from utils.logging_config import get_others_logger
from scraper.utils.scrape_utils import scrape_history_records

logger = get_others_logger()

def job_error_listener(event: JobExecutionEvent):
    if not event.exception:
        return

    try:
        config = load_email_config_from_json()

        subject = f"🚨 Scheduled Job Failed: {event.job_id}"

        body = f"""
                Job ID: {event.job_id}
                Scheduled Run Time: {event.scheduled_run_time}

                Exception:
                {str(event.exception)}

                Traceback:
                {event.traceback}
            """

        send_email_with_attachments(
            sender_email=config.SENDER_EMAIL,
            sender_password=config.SENDER_PASSWORD,
            subject=subject,
            body=body,
            receivers=["1026334385@qq.com"],
        )

        logger.error(f"[ALERT] Job {event.job_id} failed. Email sent.")

    except Exception as e:
        logger.error(f"[ALERT FAILED] Could not send failure email: {e}")
    
def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_listener(job_error_listener, EVENT_JOB_ERROR)
    scheduler.add_job(
        scheduled_scrape_history_records_job,
        trigger=CronTrigger(hour="9", minute=30),
        id="scrape_history_records",
        name="Scheduled Record Scraper",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )
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
        trigger=CronTrigger(hour="7", minute=00),
        id="update_common_cookies",
        name="Scheduled cookies updater",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )
    return scheduler
