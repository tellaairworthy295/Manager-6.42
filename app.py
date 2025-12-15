import ast
import asyncio
import json
import os
import pandas as pd
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn
#from fastmcp import FastMCP
# ===== Local imports =====
from scraper.news_scraper import fetch_news_from_db, save_agent_data
from scraper.stocks_scraper import main_scraper
from utils.sender import send_email_with_attachments
from utils.to_excel import excel_flow

from celery_app import celery_app
from tasks.news_tasks import scrape_all_news
from tasks.agent_tasks import scrape_agent_task
from scraper.cookies_getter import update_common_cookies, update_agent_cookies
from utils.sender import send_email_with_attachments, load_email_config_from_json
from utils.database import get_db_manager, UsersRepository
# ===== Setup =====

# mcp = FastMCP(name="News MCP")
# mcp_app = mcp.http_app(path='/tools')
# app = FastAPI(title="My Local Dify Server", lifespan=mcp_app.lifespan)
# app.mount("/mcp", mcp_app)

app = FastAPI(title="My Local Dify Server")
# ================== Health Check ====================
@app.get("/ping")
async def ping():
    return {"status": "pong"}

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
    
    try:
        await asyncio.to_thread(update_common_cookies, sources)
    except Exception as e:
        return JSONResponse({"error": "common cookies failed: {e}"}, status_code=400) 
    return {"status": "done"}

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
    # Here DatabaseManager assumes environment variable config or set up, adjust as needed
    db_manager = get_db_manager()
    user_repo = UsersRepository(db_manager)

    data = await request.json()
    user_id = data.get("user_id")
    email = data.get("email")
    sites = data.get("sites")  # [{"source": "alphapai", "phone": "...", "passwd": "..."}]

    if not user_id or not sites:
        return JSONResponse({"error": "user_id and sites are required"}, status_code=400)
    resp = []
    try:
        for site in sites:
            source = site.get("source")
            phone = site.get("phone")
            passwd = site.get("passwd") or site.get("password")  # support both keys
            if not source:
                continue
            user_record = user_repo.insert_or_update_user(
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
    except Exception as e:
        return JSONResponse({"error": f"Failed to add user: {e}"}, status_code=500)

    
#========================Celery tasks==========================
@app.post("/api/scrape_news")
async def scrape_news_api(request: Request):
    data = await request.json() or {}
    requests = data.get("requests", [])
    if not requests:
        return JSONResponse({"error": "No queries or sites provided"}, status_code=400)
    now_str = datetime.now().strftime("%Y%m%d%H%M%S")
    result = scrape_all_news.delay(requests, now_str)
    return JSONResponse({"status": "queued", "chord_id": result.id})
    

@app.post("/api/scrape_agent")
async def scrape_agent_api(request: Request):
    try:
        form = await request.form()
        file = form.get("excel")
        prompt = form.get("prompt")
        user_id = form.get("user_id")
        bonds = form.get("stocks")
        sources = form.get("agents")
    except Exception as e:
        return JSONResponse({"error": f"Form parsing failed: {e}"}, status_code=400)
    # Now we use the database instead of config file for user checks
    db_manager = get_db_manager()
    user_repo = UsersRepository(db_manager)

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

    # Step 1: Check if user_id exists in any source
    user_email = user_repo.get_email_by_user_id(str(user_id))
    if not user_email:
        return JSONResponse({"error": "您的账号还未注册"}, status_code=400)

    # Step 2: Check user_id has all corresponding sources except those in "common"
    with open("json/config.json") as f:
        agent_site_config = json.load(f)["AgentSitesConfig"]
    common_src = [agent_site["site_name"] for agent_site in agent_site_config if agent_site["field"] == "common"]
    missing_sources = []
    common_sources = []
    user_credentials = []  # list of dicts: [{source, phone, password}]
    for src in sources:
        if src in common_src:
            common_sources.append(src)
            continue
        user_entry = user_repo.get_user(str(user_id), str(src))
        if not user_entry:
            missing_sources.append(src)
        else:
            cred = {
                "source": src,
                "phone": user_entry.get("phone"),
                "password": user_entry.get("password")
            }
            user_credentials.append(cred)
    if missing_sources:
        return JSONResponse({"error": f"您的账号未注册以下Agent来源: {','.join(missing_sources)}"}, status_code=400)

    # Step 3: If 'gangtise' is present, fetch phone/passwd from user_id="common" and source="gangtise"
    common_info = None
    for src in common_sources:
        common_info = user_repo.get_user("common", src)
        if not common_info:
            return JSONResponse({"error": f"系统未配置 {src} 通用账号，请联系管理员"}, status_code=400)
        else:
            cred = {
                "source": src,
                "phone": common_info.get("phone"),
                "password": common_info.get("password")
            }
            user_credentials.append(cred)
            
    stocks = []
    filename = f"{user_id}.xlsx"
    if file:
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
    else:
        if "，" in bonds:
            stocks = bonds.split("，")
        else:
            stocks = bonds.split(",")
    if not stocks:
        return JSONResponse({"error": "No stocks provided or detected in uploaded excel file."}, status_code=400)
    
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
                entry["prompt"] = prompt
                # Update entry in main dict
                existing[str(user_id)] = entry

                with open(prompts_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as _pe:
            return JSONResponse({"error": f"prompt保存失败: {e}"}, status_code=400)
    try:
        cks = await asyncio.to_thread(update_agent_cookies, user_credentials)
    except Exception as e:
        return JSONResponse({"error": f"cookies获取失败: {e}"}, status_code=400)
    result = scrape_agent_task.delay(stocks, user_id, cks)
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
    # Receive form-data with a "result" key
    try:
        form = await request.form()
        analysis_result = form.get("result")
        if analysis_result is None:
            return JSONResponse({"error": "No 'result' field found in form-data."}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Form parsing failed: {e}"}, status_code=400)

    await asyncio.to_thread(save_agent_data, analysis_result)

    # Write content to a temporary txt file
    txt_path = "news_analysis.txt"
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(str(analysis_result))
        config = load_email_config_from_json("json/config.json")
        config.BODY = "AI分析(Agent)结果见附件。"
        config.ATTACHMENTS = [txt_path]
        send_email_with_attachments(**config.as_dict())
    except Exception as e:
        return JSONResponse({"error": f"Failed to write or send: {e}"}, status_code=500)

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
        img_path = f"images/涨停简图.png"
        txt_path = f"stocks_analysis.txt"
        if not os.path.isfile(img_path) or not os.path.isfile(txt_path):
            # Send warning email if image does not exist
            config.BODY = f"警告：{date} 的异动图或股票分析不存在，路径: {img_path}, {txt_path}。"
            await asyncio.to_thread(send_email_with_attachments, **config.as_dict())
            return JSONResponse(
                {"status": "error", "message": f"Image for {date} does not exist: {img_path}. sent warnings"},
                status_code=400
            )
        config.ATTACHMENTS = [
            f"excel/涨停简图.xlsx",
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
        except:
            pass
        return JSONResponse(
            {"status": "ok", "date": date}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/get_news")
async def fetch_news():
    """
    Fetch Bloomberg articles data from the local SQLite database.
    Returns a list[str], each string combines 5 articles (url/title/content, one per line).
    """
    try:
        data = await asyncio.to_thread(fetch_news_from_db)  # list[dict] with keys: url, title, content
        combined = []
        chunk = []
        for i, article in enumerate(data):
            block = f"url: {article.get('url', '')}\ntitle: {article.get('title', '')}\ncontent: {article.get('content', '')}"
            chunk.append(block)
            if len(chunk) == 10:
                combined.append('\n\n'.join(chunk))
                chunk = []
        if chunk:
            combined.append('\n\n'.join(chunk))
        return combined
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch Bloomberg data from database.")

# =====================================================
# 🧩 MCP SERVER SECTION (mounted via FastMCP)
# =====================================================
# @mcp.tool(name="fetch_data", description="Fetch new articles from local SQLite database")
# async def fetch_rb_data():
#     """Fetch Bloomberg articles data from the local SQLite database."""
#     try:
#         data = await asyncio.to_thread(fetch_news_from_db)
#         return {"data": data}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail="Failed to fetch Bloomberg data from database.")

# =====================================================
# Entry point
# =====================================================
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5000)
