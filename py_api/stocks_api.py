
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


@router.get("/scrape_stocks")
async def scrape_stocks_api():
    """
    API endpoint for scraping A-share stock data.
    Returns immediately after scheduling the scrape as a background task.
    """
    date = datetime.now()
    # Fire-and-forget: caller gets instant response,
    # run_stocks_scrape handles its own threading internally
    await asyncio.get_event_loop().create_task(
        _scrape_stocks_task(date)
    )
    return JSONResponse({
        "status": "accepted",
        "msg": "Stock scrape task scheduled",
        "date": date.strftime("%Y-%m-%d"),
    })


async def _scrape_stocks_task(date: datetime):
    """Wrapper that adds error boundary for the fire-and-forget task."""
    try:
        result = await run_stocks_scrape(date)
        logger.info(f"[Stocks] Background task finished: {result}")
    except Exception as e:
        logger.error(f"[Stocks] Background task failed: {e}", exc_info=True)