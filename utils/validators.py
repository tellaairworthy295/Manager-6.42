import os
import json
import ast
import pandas as pd
from fastapi import Request

from .database import get_db_manager, UsersRepository

class ValidationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


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

    # ---------- user checks ----------
    db_manager = get_db_manager()
    user_repo = UsersRepository(db_manager)

    user_email = user_repo.get_email_by_user_id(str(user_id))
    if not user_email:
        raise ValidationError("您的账号还未注册")

    # ---------- load agent config ----------
    # with open("json/config.json") as f:
    #     agent_site_config = json.load(f)["AgentSitesConfig"]

    # common_sources = {
    #     a["site_name"]
    #     for a in agent_site_config
    #     if a["field"] == "common"
    # }

    common_sources = ["gangtise"]
    user_credentials = []
    missing_sources = []
    commons = []

    for src in sources:
        if src in common_sources:
            commons.append(src)
            continue

        user_entry = user_repo.get_user(str(user_id), str(src))
        if not user_entry:
            missing_sources.append(src)
        else:
            user_credentials.append({
                "source": src,
                "phone": user_entry.get("phone"),
                "password": user_entry.get("password"),
            })

    if missing_sources:
        raise ValidationError(
            f"您的账号未注册以下Agent来源: {','.join(missing_sources)}"
        )

    # ---------- common credentials ----------
    for src in commons:
        common_entry = user_repo.get_user("common", src)
        if not common_entry:
            raise ValidationError(f"系统未配置 {src} 通用账号，请联系管理员")
        user_credentials.append({
            "source": src,
            "phone": common_entry.get("phone"),
            "password": common_entry.get("password"),
        })

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
        "credentials": user_credentials,
        "sources": sources,
        "prompt": prompt,
    }

from filelock import FileLock

def save_user_prompt(user_id: str, prompt: str):
    if not prompt or not prompt.strip():
        return

    prompts_path = "json/prompts.json"
    lock_path = prompts_path + ".lock"

    def split_prompt_to_list(prompt_text: str) -> list[str]:
        import re
        if not prompt_text or not prompt_text.strip():
            return []

        parts = re.split(r'(?<=[。？])|\n', prompt_text)
        return [p.strip() for p in parts if p.strip()]

    with FileLock(lock_path, timeout=5):
        if os.path.exists(prompts_path):
            with open(prompts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        entry = data.get(user_id, {})
        entry["prompt"] = split_prompt_to_list(prompt)
        data[user_id] = entry

        with open(prompts_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

