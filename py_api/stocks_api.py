import ast
import asyncio
from datetime import datetime
import json

import pandas as pd
import pandas_market_calendars as mcal
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from scraper.cookies_getter import update_common_cookies
from scraper.stocks_scraper import main_scraper
from image_process import main_flow
from utils.database import StockStatsRepository, get_db_manager
from utils.logging_config import get_others_logger


logger = get_others_logger()

router = APIRouter(prefix="/stocks", tags=["Stocks"])

@router.post("/refresh_cookies")
async def refresh_cookies(request: Request):
    data = await request.json()
    sources = data.get("sources")
    # Make sure sources is a list
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except Exception:
            try:
                sources = ast.literal_eval(sources)
            except Exception:
                if "，" in sources:
                    sources = sources.split("，")
                else:
                    sources = sources.split(",")
    sources = [str(s).strip(" []'\"") for s in sources if s and str(s).strip()]
    if not sources:
        return JSONResponse({"error": "sources are required"}, status_code=400)
    await update_common_cookies(sources, False)
    return {"status": "done"}

@router.get("/trendings")
def fetch_market_stats(days: int = 30):
    db_manager = get_db_manager()
    repo = StockStatsRepository(db_manager)
    df_records = repo.get_market_stats(days)
    return df_records


@router.get("/scrape_stocks")
async def scrape_stocks_api():
    """
    API endpoint for scraping A股 stock data.
    Only runs on trading days.
    """
    try:
        date = datetime.now()

        def is_a_share_trading_day(date_obj=None):
            """Check if a date is a trading day for Chinese A-shares"""
            if date_obj is None:
                date_obj = datetime.now()

            # Get Shanghai Stock Exchange calendar (same as Shenzhen for A-shares)
            exchange = mcal.get_calendar("XSHG")

            # Convert to pandas Timestamp
            if isinstance(date_obj, datetime):
                timestamp = pd.Timestamp(date_obj.date())
            else:
                timestamp = pd.Timestamp(date_obj)

            # Check if this date is in the trading schedule
            # Get schedule for a small window to minimize API calls
            start_date = timestamp - pd.Timedelta(days=1)
            end_date = timestamp + pd.Timedelta(days=1)

            schedule = exchange.schedule(start_date=start_date, end_date=end_date)

            # Return True if the date is in the schedule
            return timestamp in schedule.index

        # Check if today is an A-share trading day
        if not is_a_share_trading_day(date):
            return JSONResponse(
                {
                    "status": "skipped",
                    "msg": "Today is not an A-share trading day, skipping",
                    "date": date.strftime("%Y-%m-%d"),
                }
            )

        # Today is a trading day, proceed with normal scraping
        date = date.strftime("%Y-%m-%d")
        flag, market_number = await main_scraper(date)

        if not flag:
            return JSONResponse(
                {
                    "status": "success",
                    "msg": "No data found",
                    "date": date,
                }
            )

        await asyncio.to_thread(main_flow, market_number, date, flag)

        return JSONResponse(
            {
                "status": "success",
                "date": date,
                "market": "A-share",
                "today_is_trading_day": True,
            }
        )

    except Exception as e:
        logger.error(f"Error in scrape_stocks_api: {str(e)}")

        return JSONResponse(
            {
                "status": "error",
                "msg": f"Internal server error: {str(e)}",
            },
            status_code=500,
        )


