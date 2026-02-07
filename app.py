import ast
import asyncio
from contextlib import asynccontextmanager
import json
import os
from datetime import datetime
from pathlib import Path
import re
import shutil
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import Field
import uvicorn
from fastmcp import FastMCP
import redis.asyncio as aioredis
# ===== Local imports =====
from exception.exception_handler import NetworkException, ValidationError, generic_exception_handler, \
    validation_exception_handler, network_exception_handler, SelectorException, selection_exception_handler
from scraper.news_scraper import fetch_news_from_db, save_agent_data
from scraper.stocks_scraper import main_scraper
from image_process import main_flow

from tasks.news_tasks import scrape_all_news
from tasks.agent_tasks import display_agent_task_main
from scraper.cookies_getter import update_common_cookies, update_agent_cookies
#from utils.sender import send_email_with_attachments, load_email_config_from_json
from utils.database import StockStatsRepository, get_db_manager, UsersRepository, StockRepository
from utils.validators import split_prompt_to_list, validate_and_prepare_cookies
from utils.logging_config import get_others_logger
from utils.redis_utils import create_aioredis, close_loop_redis, get_aioredis_client
from fastapi.middleware.cors import CORSMiddleware

# ===== Setup =====
logger = get_others_logger()

# === MCP integration ===
mcp = FastMCP(name="News MCP")
mcp_app = mcp.http_app(path="/tools")


# --- Lifespan handler: combine MCP lifespan + Redis lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run MCP startup first
    async with mcp_app.lifespan(app):
        # Startup: create Redis client
        create_aioredis()
        yield
        await close_loop_redis()


# --- Create app with combined lifespan ---
app = FastAPI(title="My Local Dify Server", lifespan=lifespan)

# Mount MCP app
app.mount("/mcp", mcp_app)

# Allow your Next.js frontend (localhost:3000) to call FastAPI (localhost:5000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://10.25.116.175:3000", "http://10.29.92.50:3000"],  # or ["*"] for all origins
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
router = APIRouter(prefix="/sse", tags=["Real-time"])


@router.get("/{channel}")
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


# Mount the SSE router
app.include_router(router)


# ================== Health Check ====================
@app.get("/ping")
async def ping():
    return {"status": "pong"}


#==============================APP===================================================
@app.get("/api/trendings")
def fetch_market_stats(days: int = 30):
    db_manager = get_db_manager()
    repo = StockStatsRepository(db_manager)
    df_records = repo.get_market_stats(days)
    return df_records


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


# @app.get("/api/rss")
# async def get_rss():
#     rss_dir = Path("rss") / "test.xml"

#     # Return with CORS headers
#     return FileResponse(
#         rss_dir, 
#         media_type="application/rss+xml",
#         headers={
#             "Access-Control-Allow-Origin": "*",
#             "Cache-Control": "no-cache, no-store, must-revalidate",
#             "Pragma": "no-cache",
#             "Expires": "0"
#         }
#     )

# ================== Cookies Update ==================
@app.post("/api/refresh_cookies")
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


@app.post("/api/agent_cookies")
async def refresh_agent_cookies(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    sources = data.get("sources")

    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except Exception:
            try:
                sources = ast.literal_eval(sources)
            except Exception:
                sources = sources.replace("，", ",").split(",")

    sources = [str(s).strip(" []'\"") for s in sources if s and str(s).strip()]
    await validate_and_prepare_cookies(user_id, sources, False)
    return JSONResponse("ok")


#===================================User Operation=============================================
@app.post("/api/lookup_user")
async def lookup_user(request: Request):
    db_manager = get_db_manager()
    user_repo = UsersRepository(db_manager)
    data = await request.json()
    email = data.get("email")
    if not email:
        return JSONResponse({"error": "email is required"}, status_code=400)
    user_id = user_repo.get_user_id_by_email(email)
    logger.info(user_id)
    return {"status": "200", "user_id": user_id}


@app.post("/api/add_user")
async def add_users(request: Request):
    """
    Add a new user (with multiple site credentials) into the database.
    Expected input: {
        "user_id": "...",
        "email": "...",
        "sites": [
            {"source": "alphapai", "phone": "...", "passwd": "..."},
            ...
        ]
    }
    """
    db_manager = get_db_manager()
    user_repo = UsersRepository(db_manager)

    data = await request.json()
    user_id = data.get("user_id")
    email = data.get("email")
    sites = data.get("sites")  # [{"source": "alphapai", "phone": "...", "passwd": "..."}]
    if not user_id or not sites:
        return JSONResponse({"error": "user_id and sites are required"}, status_code=400)
    resp = []
    for site in sites:
        source = site.get("source")
        phone = site.get("phone")
        passwd = site.get("passwd") or site.get("password")  # support both keys
        if not source:
            continue

        # Assuming update_agent_cookies handles external tasks but doesn't affect insert logic
        user_credentials = [{
            "source": source,
            "phone": phone,
            "password": passwd
        }]
        await update_agent_cookies(user_credentials, user_id, False)

        # Use the insert_or_update_user method to ensure upsert behavior
        user_repo.insert_or_update_user(
            user_id=user_id,
            source=source,
            email=email,
            phone=phone,
            password=passwd,
        )
        # Only include basic response info
        resp.append({
            "source": source,
            "user_id": user_id,
            "email": email,
            "phone": phone,
            "status": "ok"
        })
    return {"status": "ok", "users_added": resp}


#========================Dramatiq tasks==========================
@app.post("/api/scrape_news")
async def scrape_news_api(request: Request):
    data = await request.json() or {}
    requests = data.get("requests", [])
    if not requests:
        return JSONResponse({"error": "No queries or sites provided"}, status_code=400)
    now_str = datetime.now().strftime("%Y%m%d%H%M%S")
    result = scrape_all_news.send(requests, now_str)
    return JSONResponse({"status": "queued", "task_id": result.message_id})


@app.post("/api/display_agent")
async def display_agent_api(request: Request):
    try:
        form = await request.form()
        prompt = form.get("query")
        user_id = form.get("user_id")
        sources = form.get("sources")
        conversation_id = form.get("conversation_id")
        dia_count = form.get("dia_count")
    except Exception as e:
        raise ValidationError(f"Form parsing failed: {e}")
    # ---------- normalize sources ----------
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except Exception:
            try:
                sources = ast.literal_eval(sources)
            except Exception:
                sources = sources.replace("，", ",").split(",")

    sources = [str(s).strip(" []'\"") for s in sources if s and str(s).strip()]
    prompt_list = split_prompt_to_list(prompt)
    with open("json/selectors.json", "r", encoding="utf-8") as f:
        s_locators_map = json.load(f)["agent"]
    all_cookies = await validate_and_prepare_cookies(user_id.split("_")[-1], sources, True)
    display_agent_task_main.send(user_id=user_id, prompt=prompt_list,
                                 all_cookies=all_cookies, s_locators_map=s_locators_map,
                                 conversation_id=conversation_id, dia_count=dia_count)


@app.post("/api/news_analysis")
async def news_analyzer(request: Request):
    # Receive form-data with a "result" key
    form = await request.form()
    analysis_result = form.get("result")
    if analysis_result is None:
        return JSONResponse({"error": "No 'result' field found in form-data."}, status_code=400)

    await asyncio.to_thread(save_agent_data, analysis_result)

    # Write content to a temporary txt file
    now = datetime.now()
    formatted = now.strftime("%Y-%m-%d-%H")
    txt_path = f"news_analyses/{formatted}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(str(analysis_result))
    # config = load_email_config_from_json("json/config.json")
    # config.SUBJECT = "【新闻】彭博社最近6小时新闻AI总结"
    # config.BODY = "AI总结结果见附件。"
    # config.ATTACHMENTS = [txt_path]
    # await asyncio.to_thread(send_email_with_attachments, **config.as_dict())

    return JSONResponse({"status": "200", "message": "Sent results successfully."})


@app.get("/api/scrape_stocks")
async def scrape_stocks_api():
    from datetime import timedelta
    date = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    flag, market_number, all_records_limit = await main_scraper(date)
    if not flag:
        return JSONResponse(
            {"status": "ok", "msg": "Not a new date"}
        )
    await asyncio.to_thread(main_flow, market_number, all_records_limit, date)

    # config = load_email_config_from_json("json/config.json")
    # config.ATTACHMENTS = [
    #     f"excel/{date}.xlsx",
    #     f"excel/{date}.txt",
    #     f"images/Image.png",
    #     "excel/市场统计趋势.png",
    #     "excel/破板率趋势.png",
    # ]
    # config.BODY = "个股信息（已合并韭研和选股通）和韭研公社涨停简图相关信息，见附件。"
    # config.SUBJECT = "【韭研】个股分析与涨停简图"
    # await asyncio.to_thread(send_email_with_attachments, **config.as_dict())
    # try:
    #     os.remove(f"excel/{date}.xlsx")
    #     os.remove(f"excel/{date}.txt")
    # except Exception:
    #     pass
    return JSONResponse(
        {"status": "ok", "date": date}
    )


@app.get("/api/get_news")
async def fetch_news():
    """
    Fetch Bloomberg articles data from the local SQLite database.
    Returns a list[str], each string combines 15 articles (url/title/content, one per line).
    """
    data = await asyncio.to_thread(fetch_news_from_db)  # list[dict] with keys: url, title, content
    combined = []
    chunk = []
    for _, article in enumerate(data):
        block = f"url: {article.get('url', '')}\ntitle: {article.get('title', '')}\ncontent: {article.get('content', '')}"
        chunk.append(block)
        if len(chunk) == 15:
            combined.append('\n\n'.join(chunk))
            chunk = []
    if chunk:
        combined.append('\n\n'.join(chunk))
    return combined


# =====================================================
# 🧩 MCP SERVER SECTION (mounted via FastMCP)
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


# =====================================================
# Entry point
# =====================================================
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5000)
