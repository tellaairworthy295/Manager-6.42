import asyncio
import json
import os
import re
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
import uvicorn
from fastmcp import FastMCP
# ===== Local imports =====
#from utils.rb_logs_process import extract_json_from_content, process_article_data
from scraper.rb_scraper import fetch_urls_from_page, fetch_data, save_agent_data, save_aB_data #, export_content_to_dify
#from utils.upload_knowledge import delete_all_documents
from scraper.jiuyan_scraper import get_stocks_from_db, scrape_jiuyan
from utils.sender import send_email_with_attachments
from utils.to_excel import excel_flow
from config import EmailConfig, setup_logging

from celery_app import celery_app
from tasks.news_tasks import scrape_news_task
from tasks.alpha_tasks import scrape_alpha_task
# ===== Setup =====
logger = setup_logging("logs/app", "app")

mcp = FastMCP(name="News MCP")
mcp_app = mcp.http_app(path='/')
app = FastAPI(title="My Local Dify Server", lifespan=mcp_app.lifespan)
app.mount("/mcp", mcp_app)

# ================== Health Check ==================
@app.get("/ping")
async def ping():
    return {"status": "ok"}

#========================Celery tasks==========================
@app.post("/api/scrape_news")
async def scrape_news_api(request: Request):
    data = await request.json()
    url = data.get("url")
    urls = await asyncio.to_thread(fetch_urls_from_page, url)
    logger.info(urls)
    task = scrape_news_task.delay(urls)
    return JSONResponse({"task_id": task.id, "status": "queued"})


@app.post("/api/scrape_alpha")
async def scrape_alpha_api(file: Optional[UploadFile] = File(None)):
    # Read file and extract JSON object, even with leading/trailing junk
    date = None
    if file:
        content = await file.read()
        try:
            text = content.decode("utf-8")
        except Exception as e:
            return JSONResponse({"error": f"Failed to decode uploaded file: {e}"}, status_code=400)

        json_match = re.search(r"\{.*?\}", text, re.S)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                stocks = data.get("stocks", [])
                date = data.get("date")
            except Exception as e:
                return JSONResponse({"error": f"JSON decode error: {e}"}, status_code=400)
        else:
            stocks = get_stocks_from_db()
    else:
        stocks = get_stocks_from_db()

    if not stocks:
        return JSONResponse({"error": "No stocks provided"}, status_code=400)
    logger.info(stocks)
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    result = scrape_alpha_task.delay(stocks, date)
    return JSONResponse({"status": "accepted", "task_id": result.id})


@app.get("/api/task/status/{task_id}")
def get_task_status(task_id: str):
    async_result = celery_app.AsyncResult(task_id)

    response = {"state": async_result.state}

    if async_result.state == "PENDING":
        response["progress"] = None
    elif async_result.state == "PROGRESS":
        response["progress"] = async_result.info  # contains current progress info
    elif async_result.state == "SUCCESS":
        response["result"] = async_result.result
    elif async_result.state == "FAILURE":
        response["error"] = str(async_result.info)
    else:
        response["info"] = async_result.info

    return response

@app.post("/api/aB_saver")
async def b_analyzer(file: UploadFile = None):
    analysis_result = None
    titles = None
    if file:
        log_dir = "aB"
        os.makedirs(log_dir, exist_ok=True)
        save_path = os.path.join(log_dir, file.filename or "workflow.log")
        with open(save_path, "wb") as f:
            f.write(await file.read())
        with open(save_path, encoding="utf-8") as f:
            content = f.read()
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = {}
            analysis_result = data.get("result")
            titles = data.get("titles", "")
            agent = data.get("agent", 1)
        else:
            analysis_result = None
            titles = ""
            agent = 0
    if agent == 0:
        await asyncio.to_thread(save_aB_data, titles, analysis_result)
    else:
        await asyncio.to_thread(save_agent_data, analysis_result)
    #await asyncio.to_thread(delete_all_documents)
    return JSONResponse({"status": "200", "message": "Sent results successfully."})

@app.post("/api/scrape_jiuyan")
async def scrape_jiuyan_api(request: Request):
    data = await request.json()
    url = data.get("url")
    date = data.get("date") or datetime.today().strftime("%Y-%m-%d")
    days = data.get("days", 5)
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    try:
        stocks = await asyncio.to_thread(scrape_jiuyan, url, date)
        await asyncio.to_thread(excel_flow, date, days)
        
        EmailConfig.ATTACHMENTS = [
            f"excel/{date}.xlsx",
            "excel/trendings.xlsx",
            f"scraped_images/{date}.png",
            "excel/trendings_trend_break.png",
            "excel/trendings_trend_down.png",
            "excel/trendings_trend_even.png",
            "excel/trendings_trend_up.png"
        ]
        EmailConfig.BODY = "韭研公社涨停简图相关信息，见附件。"
        await asyncio.to_thread(send_email_with_attachments, **EmailConfig.as_dict())
        return JSONResponse(
            {"stocks": stocks, "date": date}
        )
    except Exception as e:
        logger.error(f"scrape_jiuyan_api error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================
# 🧩 MCP SERVER SECTION (mounted via FastMCP)
# =====================================================
@mcp.tool(name="fetch_data", description="Fetch new articles from local SQLite database")
async def fetch_rb_data():
    """Fetch Bloomberg articles data from the local SQLite database."""
    try:
        data = await asyncio.to_thread(fetch_data)
        logger.info("Fetched all news from database for MCP")
        return {"data": data}
    except Exception as e:
        logger.error(f"Failed to fetch rb_data for MCP: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch Bloomberg data from database.")

# =====================================================
# Entry point
# =====================================================
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
