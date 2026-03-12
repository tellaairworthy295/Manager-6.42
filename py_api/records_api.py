
import threading
from fastapi import APIRouter, Request
from scraper.records_scraper import scrape_website
from utils.logging_config import get_records_scraper_logger
from utils.validators import validate_and_prepare_cookies

logger = get_records_scraper_logger()

router = APIRouter(prefix="/records", tags=["Records"])

@router.post("/get_comment")
async def scrape_comment(request: Request):
    # data = await request.json()
    user_id = "1eeeb1dc-34c8-446c-879e-456f69762bf7"
    # Replace with your actual storage state path
    all_cookies = await validate_and_prepare_cookies(user_id.split("_")[-1], ["alphapai"], True)
    # with open("json/selectors.json", "r", encoding="utf-8") as f:
    #     s_locators_map = json.load(f)["agent"]
    for _, storage_state in all_cookies.items():
        # site_locators = s_locators_map.get(source)
        # if not site_locators:
        #     logger.warning(f"No site locators for source={source}")
        
        def run_scrape_in_thread(storage_state):
            for _ in range(3):
                try:
                    import asyncio
                    asyncio.run(scrape_website(storage_state=storage_state))
                    break
                except Exception as e:
                    logger.error(f"Error scraping website in thread: {e}")
                    continue

        thread = threading.Thread(target=run_scrape_in_thread, args=(storage_state,))
        thread.start()
