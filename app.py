
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import re
import shutil
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import Field
import uvicorn
from fastmcp import FastMCP
import redis.asyncio as aioredis
# ===== Local imports =====
from exception.exception_handler import NetworkException, ValidationError, generic_exception_handler, \
    validation_exception_handler, network_exception_handler, SelectorException, selection_exception_handler
from py_api.news_api import router as news_router
from py_api.stocks_api import router as stocks_router
from py_api.agent_api import router as agent_router
from py_api.records_api import router as records_router
from utils.database import get_db_manager, StockRepository, NewsArticleRepository
from utils.redis_utils import create_aioredis, close_loop_redis, get_aioredis_client
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ===== existing imports below =====
import json
from utils.validators import validate_and_prepare_cookies
from utils.logging_config import get_others_logger
from scraper.utils.scrape_utils import scrape_all_records, run_stocks_scrape
logger = get_others_logger()

# =====================================================
# Scheduled job logic
# =====================================================


async def scheduled_scrape_records_job():
    """
    Records job: fully async (Playwright under the hood).
    No threading needed — scrape_website is already async.
    APScheduler runs this as a coroutine on the existing event loop.
    """
    logger.info("[Scheduler] Triggering scheduled_scrape_records_job...")
    await scrape_all_records()


async def scheduled_scrape_stocks_job():
    """
    Stocks job: delegates to run_stocks_scrape which handles
    its own to_thread() calls for blocking/CPU-bound sections.
    """
    logger.info("[Scheduler] Triggering scheduled_scrape_stocks_job...")
    try:
        await run_stocks_scrape(datetime.now())
    except Exception as e:
        logger.error(f"[Scheduler] scheduled_scrape_stocks_job failed: {e}", exc_info=True)


async def scheduled_update_common_cookies():
    from scraper.cookies_getter import update_common_cookies
    await update_common_cookies(["jiuyan", "xuangutong"], False)


# =====================================================
# Lifespan: MCP + Redis + Scheduler
# =====================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        scheduled_scrape_records_job,
        trigger=CronTrigger(hour="8,12,15,20,23", minute=45),
        id="scrape_records",
        name="Scheduled Record Scraper",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )

    # The new job for 22:30
    scheduler.add_job(
        scheduled_scrape_records_job,
        trigger=CronTrigger(hour="3, 6, 18, 22", minute=30),
        id="scrape_records_22_30",
        name="Scheduled Record Scraper at 22:30",
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
    async with mcp_app.lifespan(app):
        await create_aioredis()
        scheduler.start()
        logger.info("[Scheduler] Started. Jobs: %s", scheduler.get_jobs())
        yield
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Shut down.")
        await close_loop_redis()

# === MCP integration ===
mcp = FastMCP(name="News MCP")
mcp_app = mcp.http_app(path="/tools")

# --- Create app with combined lifespan ---
app = FastAPI(title="My Local Dify Server", lifespan=lifespan)

# Mount MCP app
app.mount("/mcp", mcp_app)

# Allow your Next.js frontend (localhost:3000) to call FastAPI (localhost:5000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["*"] for all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(ValidationError, validation_exception_handler)
app.add_exception_handler(NetworkException, network_exception_handler)
app.add_exception_handler(SelectorException, selection_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)


# ====================================================

# --- Helper to get global Redis client ---
async def get_redis():
    return get_aioredis_client()


# --- SSE router ---
sse_router = APIRouter(prefix="/sse", tags=["Real-time"])


@sse_router.get("/{channel}")
async def sse_stream(
        channel: str,
        redis: aioredis.Redis = Depends(get_redis),
):
    stream_key = f"sse:{channel}"

    async def event_gen():
        last_id = "$"  # live-only tail mode
        logger.info("SSE connected: %s", stream_key)

        # adaptive blocking parameters
        block_ms = 1000
        max_block_ms = 5000
        idle_rounds = 0
        backoff_threshold = 3
        task = asyncio.current_task()

        try:
            while not task.cancelled():
                entries = await redis.xread(
                    {stream_key: last_id},
                    block=block_ms,
                    count=50,
                )

                if not entries:
                    # idle: back off gradually
                    idle_rounds += 1
                    if idle_rounds >= backoff_threshold:
                        block_ms = min(block_ms * 2, max_block_ms)

                    # SSE heartbeat
                    yield ": ping\n\n"
                    continue

                # data arrived → reset to low latency
                idle_rounds = 0
                block_ms = 1000

                for _, messages in entries:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        payload = fields.get("data")
                        if not payload:
                            continue

                        logger.info("RECEIVE %s", payload[:20])

                        event_type = "final" if '"file"' in payload else "text"

                        yield (
                            f"id: {msg_id}\n"
                            f"event: {event_type}\n"
                            f"data: {payload}\n\n"
                        )

        finally:
            logger.info("SSE disconnected: %s", stream_key)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Mount routers
app.include_router(sse_router)
app.include_router(news_router)
app.include_router(stocks_router)
app.include_router(agent_router)
app.include_router(records_router)


# ================== Health Check ====================
@app.post("/ping")
async def ping():
    return {"status": "pong"}


# NEW: list all html files for toggle
@app.get("/list/{user}/{conversation_id}")
async def list_html_files(user: str, conversation_id: str):
    html_dir_alpha = Path("snapshoots") / user / conversation_id / "alphapai"
    html_dir_gangtise = Path("snapshoots") / user / conversation_id / "gangtise"

    # Both do not exist
    if not html_dir_alpha.exists() and not html_dir_gangtise.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")

    def sort_key_by_number(f):
        # Extract the trailing number before .html at the end, after underscore
        m = re.search(r'_(\d+)\.html', f.name)
        return int(m.group(1)) if m else -1

    html_files_alpha = list(html_dir_alpha.glob("*.html")) if html_dir_alpha.exists() else []
    html_files_gangtise = list(html_dir_gangtise.glob("*.html")) if html_dir_gangtise.exists() else []
    html_files_alpha = sorted(html_files_alpha, key=sort_key_by_number)
    html_files_gangtise = sorted(html_files_gangtise, key=sort_key_by_number)

    # If no html files in both
    if not html_files_alpha and not html_files_gangtise:
        raise HTTPException(status_code=404, detail="No HTML file found")

    files_alpha = []
    files_gangtise = []
    for f in html_files_alpha:
        files_alpha.append({
            "name": f.name,
            "src": f"/preview/{user}/{conversation_id}/alphapai/{f.name}"
        })
    for f in html_files_gangtise:
        files_gangtise.append({
            "name": f.name,
            "src": f"/preview/{user}/{conversation_id}/gangtise/{f.name}"
        })

    return JSONResponse(content=[files_alpha, files_gangtise])


# UPDATED: allow selecting specific file
@app.get("/preview/{user}/{conversation_id}/{source}/{filename}")
async def preview_file(user: str, conversation_id: str, source: str, filename: str):
    html_path = Path("snapshoots") / user / conversation_id / source / filename
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(html_path, media_type="text/html")


@app.delete("/delete/{user}/{conversation_id}")
async def delete_html_dir(user: str, conversation_id: str):
    html_dir = Path("snapshoots") / user / conversation_id

    if not html_dir.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        # Use shutil.rmtree to delete the directory and all its contents
        shutil.rmtree(html_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting directory: {str(e)}")

    return JSONResponse(content={"status": "deleted"})


# =====================================================
# MCP SERVER SECTION (mounted via FastMCP)
# =====================================================
@mcp.tool(name="fetch_stocks", description="Fetch stocks(name and code) from past N days.")
async def fetch_stocks(days: int = Field(gt=0, le=30, description="Number of recent days(1-30) to fetch. (e.g., 7)")):
    logger.info(f"fetch_stocks: {days}\n")
    db_manager = get_db_manager()
    stock_repo = StockRepository(db_manager)
    data = await asyncio.to_thread(stock_repo.get_recent_stock_name_code, days)
    return {"data": data}


@mcp.tool(
    name="fetch_stocks_and_analyses",
    description="Fetch relevant stocks and their per-day analyses for the past N days.",
)
async def fetch_stocks_and_analyses(stocks: list[str] = Field(min_length=1,
                                                              description="List of stock identifiers (names and codes, at least one stock). (e.g., ['中国一重601106','国泰集团603977'])"),
                                    days: int = Field(ge=0, le=30,
                                                      description="Number of recent days(1-30) to fetch analyses, e.g., 5")):
    logger.info(f"fetch_stocks_and_analyses:\n stocks: {stocks}\n days: {days}\n")
    db_manager = get_db_manager()
    stock_repo = StockRepository(db_manager)
    data = await asyncio.to_thread(stock_repo.get_analysis_by_stock, stocks, days)
    return {"data": data}


@mcp.tool(name="fetch_news", description="Fetch recent news articles from local MySQL database.")
async def fetch_news():
    db_manager = get_db_manager()
    news_repo = NewsArticleRepository(db_manager)
    data = await asyncio.to_thread(news_repo.fetch_recent_articles, 0)
    return {"data": data}

# =====================================================
# Entry point
# =====================================================
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5000)
