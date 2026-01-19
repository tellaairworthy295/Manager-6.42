import os
import json
import ast
import re
import pandas as pd
from fastapi import Request

from scraper.cookies_getter import update_agent_cookies
from utils.database import UsersRepository, get_db_manager

# Use central exception class
from exception.exception_handler import ValidationError

async def validate_scrape_agent_request(request: Request):
    """
    Parse, validate and normalize scrape_agent inputs.
    Returns:
        stocks, user_id, user_credentials
    Raises:
        ValidationError
    """
    try:
        form = await request.form()
        file = form.get("excel")
        prompt = form.get("prompt")
        user_id = form.get("user_id")
        bonds = form.get("stocks")
        sources = form.get("agents")
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

    # ---------- parse stocks ----------
    stocks = []

    if file:
        try:
            temp_path = f"temp_{user_id}.xlsx"
            with open(temp_path, "wb") as f:
                f.write(await file.read())

            df = pd.read_excel(temp_path)
            for col in ["stocks", "stock", "股票", "代码", "名称"]:
                if col in df.columns:
                    stocks = df[col].dropna().astype(str).tolist()
                    break
        finally:
            try:
                os.remove(temp_path)
            except Exception:
                pass
    else:
        if not bonds:
            raise ValidationError("未提供股票列表")
        stocks = bonds.replace("，", ",").split(",")

    if not stocks:
        raise ValidationError("未解析到任何股票")

    return {
        "user_id": str(user_id),
        "stocks": stocks,
        "sources": sources,
        "prompt": prompt,
    }

def split_prompt_to_list(prompt_text: str) -> list[str]:
    if not prompt_text or not prompt_text.strip():
        return []
    parts = re.split(r'(?<=[。？])|\n', prompt_text)
    return [p.strip() for p in parts if p.strip()]


def save_user_prompt(user_id: str, prompt: str) -> str:
    # Save each user's prompt in their own json/{user_id}/prompt.json
    user_dir = os.path.join("json", str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    if os.path.exists(f"json/{user_id}/prompt.json"):
        with open(f"json/{user_id}/prompt.json", "r", encoding="utf-8") as f:
            prompt_config = json.load(f)
    else:
        with open(f"json/common/prompt.json", "r", encoding="utf-8") as f:
            prompt_config = json.load(f)

    # If no new prompt provided, return stored prompt, fallback to "default" if present in this file
    if not prompt or not prompt.strip():
        return prompt_config['prompt']

    # Update key for this user (just set 'prompt' key)
    prompt_config["prompt"] = split_prompt_to_list(prompt)
    with open(f"json/{user_id}/prompt.json", "w", encoding="utf-8") as f:
        json.dump(prompt_config, f, ensure_ascii=False, indent=2)

    return prompt_config["prompt"]

async def validate_and_prepare_cookies(user_id:str, sources: list[str], validation: bool = False):
        db_manager = get_db_manager()
        user_repo = UsersRepository(db_manager)

        user_email = user_repo.get_email_by_user_id(user_id)
        if not user_email:
            print(user_id)
            raise ValidationError("error: 您的账号还未注册(no email), http://localhost/explore/installed/b0a8a438-af97-489d-8810-7eb916135547")
        
        user_credentials = []
        for source in sources:
            if source == "gangtise":
                user_entry = user_repo.get_user("common", "gangtise")
            else:
                user_entry = user_repo.get_user(user_id, source)

            if not user_entry:
                print(user_entry)
                raise ValidationError("error: 您的账号还未注册(no entry), http://localhost/explore/installed/b0a8a438-af97-489d-8810-7eb916135547")
            else:
                user_credentials.append({
                    "source": source,
                    "phone": user_entry.get("phone"),
                    "password": user_entry.get("password"),
                })

        all_cookies = await update_agent_cookies(user_credentials, user_id, validation)
        return all_cookies

# def redis_validation(user_id: str, sources: list[str]):
#     r = get_redis_client()
#     # 1️⃣ Per-user lock
#     if not r.set(f"agent:lock:user:{user_id}", "1", nx=True, ex=3600):
#         raise ValidationError("您的任务正在处理中，请稍后再试。")
#     # 2️⃣ Global concurrency limit
#     if "gangtise" in sources:
#         current = r.incr("agent:lock:global")
#         if current > GLOBAL_LIMIT:
#             r.decr("agent:lock:global")
#             r.delete(f"agent:lock:user:{user_id}")
#             raise ValidationError("Gangtise賬號被占用，请稍后再试。")