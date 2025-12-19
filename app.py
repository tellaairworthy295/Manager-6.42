import ast
import asyncio
import json
import os
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn
#from fastmcp import FastMCP
# ===== Local imports =====
from utils.redis_utils import get_redis_client
from scraper.news_scraper import fetch_news_from_db, save_agent_data
from scraper.stocks_scraper import main_scraper
from utils.sender import send_email_with_attachments
from utils.to_excel import excel_flow

from tasks.news_tasks import scrape_all_news
from tasks.agent_tasks import scrape_agent_task
from scraper.cookies_getter import update_common_cookies, update_agent_cookies
from utils.sender import send_email_with_attachments, load_email_config_from_json
from utils.database import get_db_manager, UsersRepository
from utils.validators import save_user_prompt, validate_scrape_agent_request, ValidationError
# ===== Setup =====

# mcp = FastMCP(name="News MCP")
# mcp_app = mcp.http_app(path='/tools')
# app = FastAPI(title="My Local Dify Server", lifespan=mcp_app.lifespan)
# app.mount("/mcp", mcp_app)
GLOBAL_LIMIT = 1
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
    except Exception as e:
        return JSONResponse({"error": f"Failed to add user: {e}"}, status_code=500)

    
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
    

@app.post("/api/scrape_agent")
async def scrape_agent_api(request: Request):
    try:
        data = await validate_scrape_agent_request(request)

        user_id = data["user_id"]
        sources = data["sources"]
        r = get_redis_client()
        # 1️⃣ Per-user lock
        if not r.set(f"agent:lock:user:{user_id}", "1", nx=True, ex=3600):
            raise ValidationError("您的任务正在处理中，请稍后再试。")
        # 2️⃣ Global concurrency limit
        if "gangtise" in sources:
            current = r.incr("agent:lock:global")
            if current > GLOBAL_LIMIT:
                r.decr("agent:lock:global")
                r.delete(f"agent:lock:user:{user_id}")
                raise ValidationError("Gangtise賬號被占用，请稍后再试。")


        try:
            save_user_prompt(data["user_id"], data["prompt"])
        except Exception as e:
            if r.get("agent:lock:global") == 1 and "gangtise" in data["sources"]:
                r.decr("agent:lock:global")
            return JSONResponse({"error": f"prompt保存失敗: {e}"}, status_code=500)

        try:
            cks = await asyncio.to_thread(
                update_agent_cookies,
                data["credentials"]
            )
        except Exception as e:
            if r.get("agent:lock:global") == 1 and "gangtise" in data["sources"]:
                r.decr("agent:lock:global")
            return JSONResponse({"error": f"cookies更新失敗: {e}"}, status_code=500)

        scrape_agent_task.send(
                    data["stocks"],
                    data["user_id"],
                    cks
                )

        r.incr("agent:global:processing")
        tasks = int(r.get("agent:global:processing") or 0)
        return JSONResponse(
            {
                "已有任務": tasks,
                "detail": "您的任务提交成功，请耐心等待。"
            },
            status_code=202,
        )
    
    except ValidationError as e:
        return JSONResponse({"error": e.message}, status_code=e.status_code)
    except Exception as e:
        return JSONResponse({"error": f"系统错误: {e}"}, status_code=500)

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
        img_path = f"images/Image.png"
        txt_path = f"stocks_analysis.txt"
        config.ATTACHMENTS = [
            f"excel/ImageToExcel.xlsx",
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
        raise HTTPException(status_code=500, detail=f"Failed to fetch Bloomberg data from database.{e}")

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
