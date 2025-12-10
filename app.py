import asyncio
import json
import os
import pandas as pd
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn
from fastmcp import FastMCP
# ===== Local imports =====
from scraper.news_scraper import fetch_urls_from_db, save_agent_data
from scraper.stocks_scraper import main_scraper
from utils.sender import send_email_with_attachments
from utils.to_excel import excel_flow
from config import setup_logging

from celery_app import celery_app
from tasks.news_tasks import scrape_all_news
from tasks.alpha_tasks import scrape_alpha_task
from scraper.cookies_getter import update_all_cookies
from utils.sender import send_email_with_attachments, load_email_config_from_json
# ===== Setup =====
logger = setup_logging("logs/app", "app")

mcp = FastMCP(name="News MCP")
mcp_app = mcp.http_app(path='/tools')
app = FastAPI(title="My Local Dify Server", lifespan=mcp_app.lifespan)
app.mount("/mcp", mcp_app)

# ================== Health Check ====================
@app.get("/ping")
async def ping():
    return {"status": "pong"}

# ================== Cookies Update ==================
@app.get("/api/refresh_cookies")
async def refresh_cookies():
    update_all_cookies()
    return {"status": "done"}

@app.post("/api/add_user")
async def add_users(request: Request):
    """
    API endpoint to register a new user's cookies config to cookies.json.
    - Receives JSON with user_id and a list of sites to add (site name, phone, passwd fields for each)
    - If user_id already exists, does nothing (optional: you may allow updating phones/passwords by overwriting corresponding entries)
    - Otherwise, copies all authen fields and relevant keys as in cookies.json for other users.
    """

    data = await request.json()
    user_id = data.get("user_id")
    sites = data.get("sites")  # Example: [{"name": "alphapai", "phone": "...", "passwd": "..."}]

    if not user_id or not sites:
        return JSONResponse({"error": "Missing user_id or sites list"}, status_code=400)

    # Sanity: Only allow valid site names that exist in at least one other user's "authen" list
    try:
        with open("json/cookies.json", "r", encoding="utf-8") as f:
            cookies_json = json.load(f)
    except Exception as e:
        return JSONResponse({"error": f"Failed to open cookies.json: {e}"}, status_code=500)

    # Find a template "authen" for all possible user-level (non-"common") sites
    # We'll use the keys other than "common" as users
    user_templates = [v for k, v in cookies_json.items() if k != "common" and "authen" in v]
    if not user_templates:
        return JSONResponse({"error": "No user template found in cookies.json"}, status_code=500)

    # Find one user (arbitrarily pick first) for template structure
    template_auth = user_templates[0]["authen"]

    # Validate requested site names
    req_site_names = set(site_info["name"] for site_info in sites)
    template_site_names = set(auth["name"] for auth in template_auth)

    for name in req_site_names:
        if name not in template_site_names:
            return JSONResponse({"error": f"Unsupported site: {name}"}, status_code=400)

    # If user already exists, optionally skip or update only phones/passwords
    if user_id in cookies_json:
        # We'll update only phone/password in user->authen block
        orig_authen = cookies_json[user_id]["authen"]
        for site in sites:
            for auth in orig_authen:
                if auth["name"] == site["name"]:
                    auth["phone"]  = site["phone"]
                    auth["passwd"] = site["passwd"]
        try:
            with open("json/cookies.json", "w", encoding="utf-8") as f:
                json.dump(cookies_json, f, indent=2, ensure_ascii=False)
        except Exception as e:
            return JSONResponse({"error": f"Failed to update cookies.json: {e}"}, status_code=500)
        return JSONResponse({"status": "updated", "user_id": user_id, "sites": list(req_site_names)})

    # Create user block with top-level keys (besides "authen") and all site cookie fields from the template user.
    template_user = user_templates[0]

    # Copy all keys except "authen" from template user
    new_user = {k: v for k, v in template_user.items() if k != "authen"}
    new_user_auth = []

    # Deep copy of all authen entries, overriding phone/passwd as needed
    for auth in template_auth:
        auth_entry = dict(auth)
        for site in sites:
            if auth_entry["name"] == site["name"]:
                auth_entry["phone"] = site["phone"]
                auth_entry["passwd"] = site["passwd"]
        new_user_auth.append(auth_entry)
    new_user["authen"] = new_user_auth

    # Copy all site keys (besides "authen") representing cookies/fields for each "name"
    # These are at the same level as "authen", e.g., "alphapai": {cookies, ...}
    for site in sites:
        site_name = site["name"]
        if site_name in template_user:
            # Deep copy cookie field/blocks for the site from template
            from copy import deepcopy
            new_user[site_name] = deepcopy(template_user[site_name])

    cookies_json[user_id] = new_user

    try:
        with open("json/cookies.json", "w", encoding="utf-8") as f:
            json.dump(cookies_json, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return JSONResponse({"error": f"Failed to write cookies.json: {e}"}, status_code=500)

    return JSONResponse({"status": "created", "user_id": user_id, "sites": list(req_site_names)})
    
#========================Celery tasks==========================
@app.post("/api/scrape_news")
async def scrape_news_api(request: Request):
    data = await request.json() or {}
    requests = data.get("requests", [])
    if not requests:
        return JSONResponse({"error": "No queries or sites provided"}, status_code=400)

    result = scrape_all_news.delay(requests)
    return JSONResponse({"status": "queued", "chord_id": result.id})
    

@app.post("/api/scrape_alpha")
async def scrape_agent_api(request: Request):
    # Parse the multipart form data manually for broader compatibility
    try:
        form = await request.form()
        file = form.get("excel")
        prompt = form.get("prompt")
        user_id = form.get("user_id")
    except Exception as e:
        return JSONResponse({"error": f"Form parsing failed: {e}"}, status_code=400)

    # Check required fields
    if not file:
        return JSONResponse({"error": "Missing file"}, status_code=422)

    stocks = []
    filename = f"{user_id}.xlsx"
    try:
        # Save uploaded file to disk temporarily
        temp_path = f"temp_{filename}"
        with open(temp_path, "wb") as temp_f:
            temp_f.write(await file.read())
        # Read excel file
        df = pd.read_excel(temp_path)
        # Try various column names for stocks
        for col in ["stocks", "stock", "股票", "代码", "名称"]:
            if col in df.columns:
                stocks = df[col].dropna().astype(str).tolist()
                break
        # Remove temp file
        try:
            os.remove(temp_path)
        except Exception:
            pass
    except Exception as e:
        return JSONResponse({"error": f"Failed to read excel file: {e}"}, status_code=400)

    # SAVE prompt to prompts.json under user_id if prompt is not empty
    # 1. Using a threading.Lock() for file locking does *not* actually lock across async threads, processes, or requests—it's per Python process/thread only, so parallel FastAPI requests could still race.
    if prompt and str(prompt).strip():
        from filelock import FileLock
        prompts_path = "json/prompts.json"
        lock_path = prompts_path + ".lock"
        try:
            with FileLock(lock_path, timeout=10):
                # If file doesn't exist, create a blank dict.
                if not os.path.exists(prompts_path):
                    existing = {}
                else:
                    try:
                        with open(prompts_path, "r", encoding="utf-8") as f:
                            existing = json.load(f)
                    except Exception:
                        existing = {}

                entry = existing.get(str(user_id), {})
                # Set new prompts as list of non-empty lines
                entry["prompts"] = [line for line in str(prompt).split("\n") if line.strip()]
                # Update entry in main dict
                existing[str(user_id)] = entry

                with open(prompts_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as _pe:
            logger.error(f"Failed to save prompt for {user_id}: {_pe}")
            
    if not stocks:
        return JSONResponse({"error": "No stocks provided or detected in uploaded excel file."}, status_code=400)
    logger.info({"user_id": user_id, "stocks": stocks})
    result = scrape_alpha_task.delay(stocks, user_id)
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

@app.post("/api/news_analysis")
async def news_analyzer(request: Request):
    # Receive JSON with {"result": "content"}
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)

    analysis_result = body.get("result")
    await asyncio.to_thread(save_agent_data, analysis_result)

    config = load_email_config_from_json("json/config.json")
    config.BODY = (
        f"AI分析(Agent):\n\n{analysis_result}\n\n"
    )
    send_email_with_attachments(**config.as_dict())
    return JSONResponse({"status": "200", "message": "Sent results successfully."})

@app.post("/api/scrape_stocks")
async def scrape_stocks_api(request: Request):
    data = await request.json()
    date = data.get("date") or datetime.today().strftime("%Y-%m-%d")
    days = data.get("days", 10)
    
    try:
        await asyncio.to_thread(main_scraper, date)
        await asyncio.to_thread(excel_flow, date, days)

        config = load_email_config_from_json("json/config.json")
        img_path = f"scraped_images/{date}.png"
        txt_path = f"stocks_{date}.txt"
        if not os.path.isfile(img_path) or not os.path.isfile(txt_path):
            # Send warning email if image does not exist
            config.BODY = f"警告：{date} 的异动图或股票分析不存在，路径: {img_path}, {txt_path}。"
            await asyncio.to_thread(send_email_with_attachments, **config.as_dict())
            return JSONResponse(
                {"status": "error", "message": f"Image for {date} does not exist: {img_path}. sent warnings"},
                status_code=400
            )
        config.ATTACHMENTS = [
            f"excel/{date}.xlsx",
            txt_path,
            img_path,
            "excel/trendings_trend_break.png",
            "excel/trendings_trend_down.png",
            "excel/trendings_trend_even.png",
            "excel/trendings_trend_up.png"
        ]
        config.BODY = "个股信息和韭研公社涨停简图相关信息，见附件。"
        await asyncio.to_thread(send_email_with_attachments, **config.as_dict())
        try:
            os.remove(txt_path)
            os.remove(img_path)
        except:
            pass
        return JSONResponse(
            {"status": "ok", "date": date}
        )
    except Exception as e:
        logger.error(f"scrape_stocks_api error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================
# 🧩 MCP SERVER SECTION (mounted via FastMCP)
# =====================================================
@mcp.tool(name="fetch_data", description="Fetch new articles from local SQLite database")
async def fetch_rb_data():
    """Fetch Bloomberg articles data from the local SQLite database."""
    try:
        data = await asyncio.to_thread(fetch_urls_from_db)
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
