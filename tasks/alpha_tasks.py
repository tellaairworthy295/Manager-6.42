import os
from celery import chord, group
from celery_app import celery_app
from datetime import datetime
from config import get_chrome_driver, setup_logging
from scraper.jiuyan_scraper import update_jiuyan_results
from scraper.alpha_scraper import process_single_stock, to_paipai, zip_files
from config import EmailConfig
from utils.sender import send_email_with_attachments

logger = setup_logging("logs/alpha_task", "alpha_task")

DOWNLOAD_DIR = os.path.join(os.getcwd(), "oneStockPage")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Subtask: process ONE stock ---
@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=10, max_retries=3, name="tasks.alpha_tasks.process_single_stock_task")
def process_single_stock_task(self, stock: str):
    """Celery subtask: process one stock, retry on failure automatically."""
    logger.info(f"Processing stock: {stock}")
    driver = get_chrome_driver(DOWNLOAD_DIR, "")
    try:
        to_paipai(driver)
        docx_path = process_single_stock(driver, stock)
        logger.info(f"✅ Done {stock}")
        return {"stock": stock, "status": "success", "file": docx_path}
    except Exception as e:
        logger.exception(f"❌ Failed {stock}: {e}")
        return {"stock": stock, "status": "failed", "error": str(e)}
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# --- Callback: aggregate results, zip, and email ---
@celery_app.task(bind=True, name="tasks.alpha_tasks.finalize_and_email")
def finalize_and_email(self, results, date=None):
    date = date or datetime.today().strftime("%Y-%m-%d")
    success_files = [r["file"] for r in results if r.get("status") == "success"]
    failed_stocks = [r["stock"] for r in results if r.get("status") == "failed"]

    logger.info(f"Finalizing job: {len(success_files)} success, {len(failed_stocks)} failed")

    zip_file = None
    if success_files:
        try:
            update_jiuyan_results(date, success_files)
            # Instead of only zipping success_files, zip all files in oneStockPage (DOWNLOAD_DIR)
            all_doc_files = [
                os.path.join(DOWNLOAD_DIR, f)
                for f in os.listdir(DOWNLOAD_DIR)
                if f.lower().endswith(('.docx', '.doc')) and os.path.isfile(os.path.join(DOWNLOAD_DIR, f))
            ]
            zip_file = zip_files(all_doc_files, DOWNLOAD_DIR, date)
            EmailConfig.ATTACHMENTS = [f"{zip_file}"]
            EmailConfig.BODY = (
                f"韭研公社股票AlphaPai分析（{date}），"
                f"成功 {len(success_files)} 只，失败 {len(failed_stocks)} 只。"
            )
            send_email_with_attachments(**EmailConfig.as_dict())
            logger.info("📨 Email sent successfully.")
        except Exception as e:
            logger.exception(f"Email sending failed: {e}")

    return {
        "status": "DONE",
        "success_count": len(success_files),
        "failed_count": len(failed_stocks),
        "failed_stocks": failed_stocks,
        "zip_file": zip_file,
    }


# --- Parent task: create group & chord ---
@celery_app.task(bind=True, name="tasks.alpha_tasks.scrape_alpha_task")
def scrape_alpha_task(self, stocks: list[str], date):
    """Launch per-stock subtasks and run finalization when all complete."""
    job = group(process_single_stock_task.s(stock) for stock in stocks)
    result = chord(job)(finalize_and_email.s(date))

    logger.info(f"Started chord job: {result.id}")
    return {"chord_id": result.id}