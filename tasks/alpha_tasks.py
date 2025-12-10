
import json
import os
import tempfile
from celery import chord, group
from docx import Document
from celery_app import celery_app
from datetime import datetime
from config import get_chrome_driver, setup_logging
from scraper.alpha_scraper import inject_cookies, process_single_stock
from utils.sender import load_email_config_from_json, send_email_with_attachments
from utils.interactive import zip_files, safe_delete

logger = setup_logging("logs/alpha_task", "alpha_task")

BASE_DIR = os.path.join(os.getcwd(), "alphaStockAnalysis")

def get_user_dirs(user_id: str):
    user_base = os.path.join(BASE_DIR, "users", user_id)
    tmp_dir = os.path.join(user_base, "tmp")
    final_dir = os.path.join(user_base, "final_docs")
    output_dir = os.path.join(user_base, "outputs")

    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    return user_base, tmp_dir, final_dir, output_dir

# --- Subtask: process ONE stock ---
@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=6,
    name="tasks.alpha_tasks.process_single_stock_task"
)
def process_single_stock_task(self, stock: str, user_id, locators_map, user_config, prompt: list[str]):
    logger.info(f"Processing stock: {stock} for user={user_id}")

    # user-specific folders
    _, TMP_DIR, FINAL_DOC_DIR, _ = get_user_dirs(user_id)

    # Create unique temp folder inside user's tmp/
    import random
    rand_num = random.randint(100000, 999999)
    temp_download_dir = tempfile.mkdtemp(
        prefix=f"{user_id}_{stock}_{rand_num}_",
        dir=TMP_DIR,
    )
    logger.info(f"Chrome temp folder: {temp_download_dir}")

    driver = get_chrome_driver(temp_download_dir, "")
    try:
        # Process — returns downloaded file path INSIDE temp_download_dir
        for url, locators in locators_map.items():
            site_name = locators.get("name")
            inject_cookies(driver, user_config[site_name], url)
            temp_file = process_single_stock(driver, stock, prompt, temp_download_dir, locators)

            # Move/copy to user's final_doc folder
            ext = os.path.splitext(temp_file)[1]
            final_name = f"{stock}{ext}"
            final_path = os.path.join(FINAL_DOC_DIR, final_name)

            os.replace(temp_file, final_path)
            logger.info(f"✔ Renamed file stored → {final_path}")

        # After processing, append all TXT files into the .docx file
        # Find final docx file and txt files
        docx_file = os.path.join(FINAL_DOC_DIR, f"{stock}.docx")
        txt_files = [
            os.path.join(FINAL_DOC_DIR, f)
            for f in os.listdir(FINAL_DOC_DIR)
            if f.endswith('.txt') and f.startswith(stock)
        ]

        # Only append if docx exists and there are txt files
        if os.path.exists(docx_file) and txt_files:
            try:
                doc = Document(docx_file)
                for txt_path in txt_files:
                    with open(txt_path, "r", encoding="utf-8") as fin:
                        txt_content = fin.read()
                        doc.add_paragraph(txt_content)
                doc.save(docx_file)
                logger.info(f"✔ Appended text files into docx: {docx_file}")
                # Remove txt files after appending
                for txt_path in txt_files:
                    try:
                        os.remove(txt_path)
                        logger.debug(f"Deleted txt file after appending: {txt_path}")
                    except Exception as e_del:
                        logger.warning(f"Failed deleting txt file {txt_path}: {e_del}")
            except Exception as e:
                logger.warning(f"Failed appending txt to docx: {e}")

        
        return {
            "user_id": user_id,
            "stock": stock,
            "status": "success",
            "file": final_path
        }

    except Exception as e:
        logger.exception(f"❌ Failed {stock}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {
            "user_id": user_id,
            "stock": stock,
            "status": "failed",
            "error": str(e)
        }

    finally:
        try:
            driver.quit()
        except:
            pass
        safe_delete(temp_download_dir)


# --- Callback: aggregate results, zip, and email ---
@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=6,
    name="tasks.alpha_tasks.finalize_and_email"
)
def finalize_and_email(self, results):

    user_id = results[0]["user_id"]  # safe, all subtasks share same user_id
    date = datetime.today().strftime("%Y-%m-%d")

    _, _, FINAL_DOC_DIR, OUTPUT_DIR = get_user_dirs(user_id)

    success_files = [r["file"] for r in results if r.get("status") == "success"]
    failed = [r["stock"] for r in results if r.get("status") == "failed"]

    logger.info(f"Finalizing {user_id}: {len(success_files)} success, {len(failed)} failed")

    zip_file = None

    if success_files:
        try:
            # Collect user's final docs
            all_docs = [
                os.path.join(FINAL_DOC_DIR, f)
                for f in os.listdir(FINAL_DOC_DIR)
                if f.lower().endswith((".doc", ".docx"))
            ]

            zip_file = os.path.join(OUTPUT_DIR, f"{date}.zip")
            zip_files(all_docs, zip_file)
            # try remove originals
            for f in all_docs:
                try:
                    os.remove(f)
                    logger.debug(f"Deleted original file after zipping: {f}")
                except Exception as e:
                    logger.warning(f"Failed deleting '{f}': {e}")

            # Email setup
            config = load_email_config_from_json("json/config.json")
            config.ATTACHMENTS = [zip_file]
            config.BODY = (
                f"Excel 股票 AlphaPai 分析（{date}）\n"
                f"成功 {len(success_files)} 只，失败 {len(failed)} 只。"
            )

            send_email_with_attachments(**config.as_dict())
            logger.info(f"📧 Email sent to user {user_id}")

        except Exception as e:
            logger.exception(f"Email failed: {e}")
            if self.request.retries < self.max_retries:
                raise self.retry(exc=e)

    return {
        "user_id": user_id,
        "status": "DONE",
        "success_count": len(success_files),
        "failed_count": len(failed),
        "failed_stocks": failed,
        "zip_file": zip_file,
    }


# --- Parent task: create group & chord ---
@celery_app.task(name="tasks.alpha_tasks.scrape_alpha_task")
def scrape_alpha_task(stocks: list[str], user_id: str = None):
    # Load user config
    with open("json/prompts.json", "r", encoding="utf-8") as f:
        prompt_config = json.load(f)
    with open("json/selectors.json", "r") as f:
        locators_map = json.load(f)["agent"]
    with open("json/cookies.json", "r") as f:
        user_config = json.load(f)[user_id]
    # Determine which prompt to use (user-specific or default)
    if user_id is not None and str(user_id) in prompt_config and "prompts" in prompt_config[str(user_id)]:
        prompts = prompt_config[str(user_id)]["prompts"]
    else:
        prompts = prompt_config["default"]["prompts"]

    job = group(
        process_single_stock_task.s(stock, user_id, locators_map, user_config, prompts)
        for stock in stocks
    )

    result = chord(job)(finalize_and_email.s())
    logger.info(f"Started job: {result.id}")
    return {"chord_id": result.id}