import json
import os
from typing import Any, Dict
import asyncio
import redis
from utils.redis_utils import get_redis_client
import zipfile
import dramatiq
from loguru import logger
from pwright.async_pf import new_stealth_page
from pwright.async_cm import PlaywrightContext
from pwright.async_pm import AsyncPlaywrightManager
from scraper.agent_scraper import process_single_stock
from utils.sender import load_email_config_from_json, send_email_with_attachments


# Your utilities assumed to exist:
# - get_redis_client()
# - send_email_with_attachments(...)
# - load_email_config_from_json(...)
# - new_stealth_page(context) -> Page (async version expected)
# - process_single_stock(page, stock, prompt, final_docs, site_locators) -> str (async)
#   NOTE: If process_single_stock was sync, convert it to async or wrap its blocking parts in run_in_executor.

logger.add("logs/agent_task/agent_task_{time:YYYY-MM-DD}.log", rotation="00:00", retention="15 days", encoding="utf-8")

BASE_DIR = os.path.join(os.getcwd(), "agentStockAnalysis")

# -------------------------------------------------------------
# Directory helpers
# -------------------------------------------------------------
def get_user_dirs(user_id: str):
    user_base = os.path.join(BASE_DIR, "users", user_id)
    final_docs = os.path.join(user_base, "final_docs")
    outputs = os.path.join(user_base, "outputs")
    os.makedirs(final_docs, exist_ok=True)
    os.makedirs(outputs, exist_ok=True)
    return user_base, final_docs, outputs

def init_user_dirs(user_id: str):
    """
    Remove all files inside final_docs and outputs for the user.
    (Subdirectories remain, only files are deleted.)
    """
    _, final_docs, outputs = get_user_dirs(user_id)
    for folder in [final_docs, outputs]:
        if os.path.exists(folder):
            for fname in os.listdir(folder):
                fpath = os.path.join(folder, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                    except Exception as e:
                        logger.warning(f"Failed to delete file {fpath}: {e}")

# -------------------------------------------------------------
# handle_failure
# -------------------------------------------------------------
@dramatiq.actor(queue_name="agents", max_retries=0)
def handle_failure(message_data: Dict[str, Any], exception_data: Dict[str, Any]):
    """
    Called ONCE when retries are exhausted for process_single_site.
    Safe for concurrent workers.
    """
    kwargs = message_data.get("kwargs", {})
    stock = kwargs.get("stock")
    user_id = kwargs.get("user_id")
    source = kwargs.get("source")

    result = {
        "status": "failed",
        "stock": stock or "unknown",
        "user_id": user_id,
        "site": source or "unknown",
        "file": None,
        "error": exception_data.get("message"),
        "traceback": exception_data.get("traceback"),
    }

    r = get_redis_client()
    base_key = f"agent:{user_id}"

    try:
        r.rpush(f"{base_key}:results", json.dumps(result))
        task_id = f"{stock}:{source}"
        r.sadd(f"{base_key}:completed_tasks", task_id)
    except redis.RedisError as e:
        logger.exception(f"Redis failure in handle_failure: {e}")
        return

    # Idempotent trigger; aggregator will decide if it should run
    finalize_and_email.send(user_id=user_id)

# -------------------------------------------------------------
# Internal async implementation for a single site
# -------------------------------------------------------------
async def _async_process_single_stock_task(*, stock: str, user_id: str, source: str,
                                     site_locators: dict, storage_state: dict, prompt: str):
    logger.info(f"[{stock}] Processing website={source}, user={user_id}")
    _, final_docs, _ = get_user_dirs(user_id)

    manager = AsyncPlaywrightManager()
    await manager.start()

    try:
        async with PlaywrightContext(
            manager,
            storage_state=storage_state,
            accept_downloads=True,
            ignore_https_errors=True,
        ) as context:
            # new_stealth_page should be async and return an async Page
            page = await new_stealth_page(context)

            # process_single_stock should be async
            final_path = await process_single_stock(
                page=page,
                stock=stock,
                prompt=prompt,
                download_dir=final_docs,
                locators=site_locators,
                source=source,
            )

        # ✅ WRITE SUCCESS RESULT
        r = get_redis_client()
        base_key = f"agent:{user_id}"

        result = {
            "status": "success",
            "stock": stock,
            "user_id": user_id,
            "site": source,
            "file": final_path,
        }

        r.rpush(f"{base_key}:results", json.dumps(result))
        task_id = f"{stock}:{source}"
        r.sadd(f"{base_key}:completed_tasks", task_id)

        # Trigger aggregator
        finalize_and_email.send(user_id=user_id)

    finally:
        await manager.shutdown()

# -------------------------------------------------------------
# Task 1 — Per-site task (website-level retry)
# -------------------------------------------------------------
@dramatiq.actor(
    queue_name="agents",
    max_retries=0,
    min_backoff=5000,
    on_retry_exhausted="handle_failure",
)
def process_single_stock_task(
    *,
    stock: str,
    user_id: str,
    source: str,
    site_locators: dict,
    storage_state: dict,
    prompt: str,
):
    """
    Dramatiq actor stays sync; we run the async Playwright flow inside asyncio.run
    to avoid greenlet/thread issues.
    """
    try:
        asyncio.run(_async_process_single_stock_task(
            stock=stock,
            user_id=user_id,
            source=source,
            site_locators=site_locators,
            storage_state=storage_state,
            prompt=prompt,
        ))
    except Exception:
        logger.exception(f"❌ Website failed for stock={stock}, site={source}")
        # Re-raise to let Dramatiq apply retry policy and eventually call handle_failure
        raise

# -------------------------------------------------------------
# Task 2 — Final user-level aggregator (zip + email)
# -------------------------------------------------------------
@dramatiq.actor(queue_name="agents", max_retries=0)
def finalize_and_email(user_id: str):
    r = None
    base_key = f"agent:{user_id}"
    flag = False
    try:
        r = get_redis_client()

        expected_raw = r.get(f"{base_key}:expected")
        expected = int(expected_raw or 0)
        completed = r.scard(f"{base_key}:completed_tasks")

        if completed < expected:
            flag = False
            return

        # 🛑 Idempotency guard
        if not r.setnx(f"{base_key}:finalized", "1"):
            flag = False
            return

        flag = True
        results_raw = r.lrange(f"{base_key}:results", 0, -1)
        results = [json.loads(x) for x in results_raw]

        # Check if any task's site contains 'gangtise', then decr
        if any('gangtise' in rr.get('site', '') for rr in results) and r.get("agent:lock:global") == "1":
            r.decr("agent:lock:global")
            logger.warning("DECRE GANTISE GLOBAL")

        success_files = [rr["file"] for rr in results if rr["status"] == "success"]
        failed = [f"{rr['stock']} @ {rr['site']}" for rr in results if rr["status"] != "success"]

        logger.info(f"[finalize] user={user_id}: {len(success_files)} success, {len(failed)} failed")

        _, _, output_dir = get_user_dirs(user_id)
        zip_path = os.path.join(output_dir, f"{user_id}.zip")

        config = load_email_config_from_json("json/config.json")
        if success_files:
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in success_files:
                    # Defensive: ensure file exists
                    if os.path.isfile(f):
                        zf.write(f, arcname=os.path.basename(f))
                    else:
                        logger.warning(f"Skipping missing file in zip: {f}")
            config.ATTACHMENTS = [zip_path]
            config.BODY = (
                f"Agent 分析\n"
                f"成功文件 {len(success_files)} 个。\n"
                f"失败 {len(failed)} 个。\n\n"
                f"失败详情:\n" + ("\n".join(failed) if failed else "无")
            )   
        else:
            config.BODY = f"All scrape failed"
        send_email_with_attachments(**config.as_dict(), user_id=user_id)

    except Exception as e:
        logger.exception(f"email sent failed: {e}")
    finally:
        if flag and r:
            # 🧹 Cleanup
            try:
                r.delete(
                    f"{base_key}:expected",
                    f"{base_key}:results",
                    f"{base_key}:completed_tasks",
                    f"{base_key}:finalized",
                    f"agent:lock:user:{user_id}",
                )
                r.decr("agent:global:processing")
            except Exception as e:
                logger.warning(f"Final cleanup failed: {e}")

# -------------------------------------------------------------
# Task 3 — Entry point: create all chords
# -------------------------------------------------------------
@dramatiq.actor(queue_name="agents", max_retries=0)
def scrape_agent_task(stocks: list[str], user_id: str, all_cookies: dict):
    with open("json/prompts.json", "r", encoding="utf-8") as f:
        prompt_config = json.load(f)

    with open("json/selectors.json", "r", encoding="utf-8") as f:
        s_locators_map = json.load(f)["agent"]

    # prepare user dirs
    init_user_dirs(user_id)

    # select prompt
    if user_id in prompt_config and "prompt" in prompt_config[user_id]:
        prompt = prompt_config[user_id]["prompt"]
    else:
        prompt = prompt_config["default"]["prompt"]

    messages = []

    for stock in stocks:
        for source, storage_state in all_cookies.items():
            locators = s_locators_map.get(source)
            if not locators:
                continue

            messages.append(
                process_single_stock_task.message(
                    stock=stock,
                    user_id=user_id,
                    source=source,
                    site_locators=locators,
                    storage_state=storage_state,
                    prompt=prompt,
                )
            )

    if not messages:
        logger.warning("No tasks created for scrape_agent_task")
        return

    r = get_redis_client()
    base_key = f"agent:{user_id}"

    r.set(f"{base_key}:expected", len(messages))
    r.delete(
        f"{base_key}:results",
        f"{base_key}:completed_tasks",
        f"{base_key}:finalized",
    )

    dramatiq.group(messages).run()
    logger.info(f"[scrape_agent_task] user={user_id}, tasks={len(messages)} dispatched")
    logger.info(f"current tasks: {int(r.get("agent:global:processing"))}")