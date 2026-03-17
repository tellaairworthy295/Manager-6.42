import asyncio
import json
from fastapi import APIRouter
from utils.logging_config import get_records_scraper_logger
from utils.validators import validate_and_prepare_cookies
from scraper.utils.scrape_utils import scrape_all_records

logger = get_records_scraper_logger()
router = APIRouter(prefix="/records", tags=["Records"])


@router.post("/get_records")
async def scrape_records():
    # Fire-and-forget without blocking the response
    await asyncio.get_event_loop().create_task(
        scrape_all_records()
    )
    return "ok"
