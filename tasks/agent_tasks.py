import json
import os
from datetime import datetime
import zipfile
import dramatiq
from loguru import logger
from pwright.page_factory import new_stealth_page
from pwright.context_manager import playwright_context

from pwright.playwright_manager import PlaywrightManager
from scraper.agent_scraper import process_single_stock
from utils.agent_limit import release_slot
from utils.sender import load_email_config_from_json, send_email_with_attachments

# Configure loguru for agent tasks module
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
# Task 1 — Per-site task (website-level retry)
# -------------------------------------------------------------
@dramatiq.actor(
    queue_name="agent",
    max_retries=1,
    retry_when=lambda exc: True,
)
def process_single_site(
    stock: str,
    user_id: str,
    source: str,
    site_locators: dict,
    storage_state: dict,
    prompt: str,
):
    logger.info(f"[{stock}] Processing website={source}, user={user_id}")

    _, final_docs, _ = get_user_dirs(user_id)

    try:
        with playwright_context(storage_state=storage_state) as context:
            page = new_stealth_page(context)
            final_path = process_single_stock(
                page,
                stock,
                prompt,
                final_docs,
                site_locators,
            )

        return {
            "status": "success",
            "stock": stock,
            "user_id": user_id,
            "site": source,
            "file": final_path,
        }

    except Exception as e:
        logger.exception(
            f"❌ Website failed for stock={stock}, site={source}: {e}"
        )
        # Dramatiq retry is automatic when exception is raised
        raise


# -------------------------------------------------------------
# Task 2 — Final user-level aggregator (zip + email)
# -------------------------------------------------------------
@dramatiq.actor(queue_name="agent", max_retries=1)
def finalize_and_email(all_site_results: list[dict]):
    """
    all_site_results = list of results from process_single_site
    """
    try:
        if not all_site_results:
            return {"status": "nothing_to_send"}

        user_id = all_site_results[0]["user_id"]
        date = datetime.today().strftime("%Y-%m-%d")

        _, _, output_dir = get_user_dirs(user_id)

        success_files = []
        failed = []

        for result in all_site_results:
            if result.get("status") == "success":
                success_files.append(result["file"])
            else:
                failed.append(
                    f"{result.get('stock')} @ {result.get('site')}"
                )

        logger.info(
            f"[finalize] user={user_id}: "
            f"{len(success_files)} success, {len(failed)} failed"
        )

        zip_path = os.path.join(output_dir, f"{date}_{user_id}.zip")

        if success_files:
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in success_files:
                    zf.write(f, arcname=os.path.basename(f))

            config = load_email_config_from_json("json/config.json")
            config.ATTACHMENTS = [zip_path]
            config.BODY = (
                f"Agent 分析（{date}）\n"
                f"成功文件 {len(success_files)} 个。\n"
                f"失败 {len(failed)} 个。\n\n"
                f"失败详情:\n" + ("\n".join(failed) if failed else "无")
            )

            send_email_with_attachments(**config.as_dict())
            logger.info(f"📧 Email sent to user {user_id}")

        return {
            "user_id": user_id,
            "success_count": len(success_files),
            "failed_sites": failed,
            "zip_file": zip_path,
            "status": "DONE",
        }

    finally:
        # IMPORTANT: release global slot
        PlaywrightManager.instance().close_browser()
        release_slot()



# -------------------------------------------------------------
# Task 3 — Entry point: create all chords
# -------------------------------------------------------------
@dramatiq.actor(queue_name="agent")
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
                process_single_site.message(
                    stock,
                    user_id,
                    source,
                    locators,
                    storage_state,
                    prompt,
                )
            )

    if not messages:
        logger.warning("No tasks created for scrape_agent_task")
        return

    # Dramatiq replacement for Celery chord
    dramatiq.group(messages).then(
        finalize_and_email.message()
    ).run()

    logger.info(
        f"[scrape_agent_task] user={user_id}, "
        f"tasks={len(messages)} dispatched"
    )
