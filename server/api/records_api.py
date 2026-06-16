from datetime import datetime
from fastapi import APIRouter, BackgroundTasks
from scraper.utils.scrape_utils import scrape_all_records, scrape_history_records
#from tasks.records_tasks import scrape_history_records_task
from utils.logging_config import get_records_scraper_logger

logger = get_records_scraper_logger()
router = APIRouter(prefix="/records", tags=["Records"])


@router.post("/get_records")
async def scrape_records(background_tasks: BackgroundTasks):
    background_tasks.add_task(scrape_all_records)
    return {
        "status": "Job Accepted"
    }

@router.post("/get_historical_records/{start_str}/{end_str}")
async def scrape_historical_records(start_str: str, end_str: str, background_tasks: BackgroundTasks):
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    background_tasks.add_task(scrape_history_records, start, end)
    #message = scrape_history_records_task.send(start, end)
    return {
        "status": "Job Accepted"
    }
