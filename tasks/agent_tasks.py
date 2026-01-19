import json
import os
from typing import Any, Dict
import asyncio
import redis
from utils.redis_utils import close_loop_redis, get_redis_client
import zipfile
import dramatiq
from pwright.async_pf import new_stealth_page
from pwright.async_cm import PlaywrightContext
from pwright.async_pm import AsyncPlaywrightManager
from scraper.agent_scraper import display_agent, process_single_stock
from utils.sender import load_email_config_from_json, send_email_with_attachments
from utils.logging_config import get_agent_task_logger

logger = get_agent_task_logger()

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
def handle_failure(message_data: Dict[str, Any], exception_data: Dict[str, Any]):
    """
    Called ONCE when retries are exhausted for process_single_site.
    Safe for concurrent workers.
    This should be a normal function—not a dramatiq actor.
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
    queue_name="stocks",
    max_retries=0,
    time_limit=60*60*1000,
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
    asyncio.run(_async_process_single_stock_task(
        stock=stock,
        user_id=user_id,
        source=source,
        site_locators=site_locators,
        storage_state=storage_state,
        prompt=prompt,
    ))

# -------------------------------------------------------------
# Task 2 — Final user-level aggregator (zip + email)
# -------------------------------------------------------------
@dramatiq.actor(queue_name="stocks", max_retries=0)
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
@dramatiq.actor(queue_name="stocks", max_retries=0)
def scrape_agent_task(*, stocks: list[str], user_id: str, prompt: list[str], all_cookies: dict, s_locators_map: dict):

    # prepare user dirs
    init_user_dirs(user_id)

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

#===============================Task 4=========================================
@dramatiq.actor(queue_name="agents", max_retries=0)
def display_agent_task_main(
    *,
    user_id: str,
    prompt: list[str],
    all_cookies: dict,
    s_locators_map: dict,
    conversation_id: str,
    dia_count: int
):
    """
    One dramatiq task = one prompt, sources in asyncio.gather().
    """
    dir_path = os.path.join("snapshoots", user_id)
    os.makedirs(dir_path, exist_ok=True)

    # Run the actual async logic with asyncio.run to combine sources
    asyncio.run(_gather_display_agent_sources(
        user_id=user_id,
        prompt=prompt,
        all_cookies=all_cookies,
        s_locators_map=s_locators_map,
        conversation_id=conversation_id,
        dia_count=dia_count,
    ))


async def _process_one_source(
    *,
    source: str,
    storage_state: dict,
    site_locators: dict,
    user_id: str,
    prompt: list[str],
    conversation_id: str,
    dia_count: int,
):
    logger.info(f"Processing website={source} for displaying in iframe, user={user_id}")

    manager = AsyncPlaywrightManager()
    await manager.start()
    try:
        async with PlaywrightContext(
            manager,
            storage_state=storage_state,
            accept_downloads=True,
            ignore_https_errors=True,
        ) as context:
            page = await new_stealth_page(context)
            await display_agent(
                page=page,
                user_id=user_id,
                prompt=prompt,
                locators=site_locators,
                source=source,
                conversation_id=conversation_id,
                dia_count=dia_count
            )
    finally:
        await manager.shutdown()
        await close_loop_redis()


async def _gather_display_agent_sources(
    *,
    user_id: str,
    prompt: list[str],
    all_cookies: dict,
    s_locators_map: dict,
    conversation_id: str,
    dia_count: int,
):
    """
    Run display_agent for all sources concurrently.
    """
    tasks = []
    for source, storage_state in all_cookies.items():
        site_locators = s_locators_map.get(source)
        if not site_locators:
            logger.warning(f"No site locators for source={source}")
            continue
        tasks.append(
            _process_one_source(
                source=source,
                storage_state=storage_state,
                site_locators=site_locators,
                user_id=user_id,
                prompt=prompt,
                conversation_id=conversation_id,
                dia_count=dia_count,
            )
        )

    if not tasks:
        logger.warning("No tasks created for display_agent_task_main")
        return

    logger.info(f"[display_agent_task_main] user={user_id}, tasks={len(tasks)} dispatched")
    await asyncio.gather(*tasks)
