import asyncio
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from scraper.cookies_getter import update_common_cookies
from scraper.utils.scrape_utils import run_stocks_scrape
from utils.logging_config import get_others_logger

logger = get_others_logger()
router = APIRouter(prefix="/stocks", tags=["Stocks"])


@router.post("/refresh_cookies")
async def refresh_cookies():
    await update_common_cookies(["jiuyan", "xuangutong"], False)
    return {"status": "done"}


@router.get("/scrape_stocks/{date}")
async def scrape_stocks_api(date: str):
    date = datetime.strptime(date, "%Y-%m-%d")
    await asyncio.get_event_loop().create_task(_scrape_stocks_task(date))
    return JSONResponse(
        {
            "status": "accepted",
            "msg": "Stock scrape task scheduled",
            "date": date.strftime("%Y-%m-%d"),
        }
    )


async def _scrape_stocks_task(date: datetime):
    try:
        result = await run_stocks_scrape(date)
        logger.info("[Stocks] Background task finished: %s", result)
    except Exception as exc:
        logger.error("[Stocks] Background task failed: %s", exc, exc_info=True)
