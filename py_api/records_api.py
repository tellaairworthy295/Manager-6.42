import asyncio
import json
import threading
from fastapi import APIRouter, Request
from scraper.records_scraper import scrape_website
from utils.logging_config import get_records_scraper_logger
from utils.validators import validate_and_prepare_cookies

logger = get_records_scraper_logger()

router = APIRouter(prefix="/records", tags=["Records"])


@router.post("/get_records")
async def scrape_records():
    # data = await request.json()
    user_id = "1eeeb1dc-34c8-446c-879e-456f69762bf7"
    # Replace with your actual storage state path
    all_cookies = await validate_and_prepare_cookies(user_id.split("_")[-1], ["alphapai"], True)
    with open("json/locators.json", "r", encoding="utf-8") as f:
        record_maps = json.load(f)["alphapai_record"]

    def run_scrape_in_thread(storage_state, r_url, r_locators):
        for _ in range(1):
            try:
                asyncio.run(scrape_website(storage_state=storage_state, url=r_url, locators=r_locators))
                break
            except Exception as e:
                logger.error(f"Error scraping website in thread: {e}")
                continue

    for url, locators in record_maps.items():
        thread = threading.Thread(target=run_scrape_in_thread, args=(all_cookies['alphapai'], url, locators))
        thread.start()

    return "ok"
