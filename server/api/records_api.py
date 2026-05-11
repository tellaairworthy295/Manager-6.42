import asyncio

from fastapi import APIRouter

from scraper.utils.scrape_utils import scrape_all_records, scrape_history_records
from tasks.records_tasks import scrape_history_records_task
from utils.logging_config import get_records_scraper_logger

logger = get_records_scraper_logger()
router = APIRouter(prefix="/records", tags=["Records"])


@router.post("/get_records")
async def scrape_records():
    await asyncio.get_event_loop().create_task(scrape_all_records())
    return "ok"


@router.post("/get_historical_records/{start}/{end}")
async def scrape_historical_records(start, end):
    await asyncio.get_event_loop().create_task(scrape_history_records(start, end))
    #message = scrape_history_records_task.send(start, end)
    return {
        "status": "Job Accepted"
    }
