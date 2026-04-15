import asyncio
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from scraper.news_scraper import fetch_news_from_db
from tasks.news_tasks import scrape_all_news

router = APIRouter(prefix="/news", tags=["News"])


@router.post("/scrape_news")
async def scrape_news_api(request: Request):
    data = await request.json() or {}
    requests = data.get("requests", [])
    if not requests:
        return JSONResponse({"error": "No queries or sites provided"}, status_code=400)
    now_str = datetime.now().strftime("%Y%m%d%H%M%S")
    result = scrape_all_news.send(requests, now_str)
    return JSONResponse({"status": "queued", "task_id": result.message_id})


@router.get("/get_news")
async def fetch_news():
    data = await asyncio.to_thread(fetch_news_from_db)
    combined = []
    chunk = []
    for article in data:
        block = (
            f"url: {article.get('url', '')}\n"
            f"title: {article.get('title', '')}\n"
            f"content: {article.get('content', '')}"
        )
        chunk.append(block)
        if len(chunk) == 15:
            combined.append("\n\n".join(chunk))
            chunk = []
    if chunk:
        combined.append("\n\n".join(chunk))
    return combined
