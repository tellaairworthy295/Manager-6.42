import ast
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from exception.exception_handler import ValidationError
from scraper.cookies_getter import update_agent_cookies
from tasks.agent_tasks import display_agent_task_main, scrape_agent_task
from utils.database import UsersRepository, get_db_manager
from utils.logging_config import get_others_logger
from utils.redis_utils import get_redis_client
from utils.validators import split_prompt_to_list, validate_and_prepare_cookies, validate_scrape_agent_request, \
    save_user_prompt

logger = get_others_logger()

router = APIRouter(prefix="/agent", tags=["Agent"])

@router.post("/refresh_cookies")
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


@router.post("/lookup_user")
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


@router.post("/add_user")
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
        user_credentials = [
            {
                "source": source,
                "phone": phone,
                "password": passwd,
            }
        ]
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
        resp.append(
            {
                "source": source,
                "user_id": user_id,
                "email": email,
                "phone": phone,
                "status": "ok",
            }
        )
    return {"status": "ok", "users_added": resp}


@router.post("/display_agent")
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
    display_agent_task_main.send(
        user_id=user_id,
        prompt=prompt_list,
        all_cookies=all_cookies,
        s_locators_map=s_locators_map,
        conversation_id=conversation_id,
        dia_count=dia_count,
    )


@router.post("/scrape_agent")
async def scrape_agent_api(request: Request):
    data = await validate_scrape_agent_request(request)
    user_id = data["user_id"]
    sources = data["sources"]
    stocks = data["stocks"]
    prompt = data["prompt"]

    r = get_redis_client()
    # 1️⃣ Per-user lock
    if not r.set(f"agent:lock:user:{user_id}", "1", nx=True, ex=3600):
        raise ValidationError("您的任务正在处理中，请稍后再试。")

    current_prompt = save_user_prompt(user_id, prompt)
    with open("json/selectors.json", "r", encoding="utf-8") as f:
        s_locators_map = json.load(f)["agent"]
    all_cookies = await validate_and_prepare_cookies(user_id.split("_")[-1], sources, True)
    scrape_agent_task.send(
        stocks=stocks,
        user_id=user_id,
        prompt=current_prompt,
        all_cookies=all_cookies,
        s_locators_map=s_locators_map
    )

    r.incr("agent:global:processing")
    tasks = int(r.get("agent:global:processing") or 0)
    return JSONResponse(
        {
            "已有任務": tasks,
            "detail": "您的任务提交成功，请耐心等待。",
            "prompt": current_prompt,
        },
        status_code=202,
    )