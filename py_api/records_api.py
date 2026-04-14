import asyncio
from datetime import datetime

from fastapi import APIRouter
from utils.logging_config import get_records_scraper_logger
from scraper.utils.scrape_utils import scrape_all_records
from tasks.records_tasks import scrape_history_records_task

logger = get_records_scraper_logger()
router = APIRouter(prefix="/records", tags=["Records"])


@router.post("/get_records")
async def scrape_records():
    # Fire-and-forget without blocking the response
    await asyncio.get_event_loop().create_task(
        scrape_all_records()
    )
    return "ok"


@router.post("/get_historical_records/{start}/{end}")
async def scrape_records(start, end):
    # Fire-and-forget without blocking the response
    message = scrape_history_records_task.send(start, end)

    # 2. Return immediately
    return {
        "status": "Job Accepted",
        "message_id": message.message_id,  # Give this ID to the user to track progress
        "note": "Processing will continue in the background for up to a week."
    }