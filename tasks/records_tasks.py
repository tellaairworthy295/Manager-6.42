import dramatiq
import asyncio
from datetime import datetime
# Import your actual async logic
from scraper.utils.scrape_utils import scrape_history_records


@dramatiq.actor(
    queue_name="records",
    max_retries=3,
    min_backoff=60000,
    max_backoff=3600000,
    # Ensure this is high enough for a week-long job
    time_limit=86400000 * 7,
)
def scrape_history_records_task(start_str: str, end_str: str):
    """
    Synchronous wrapper for the async scraper.
    """
    print(f"🚀 Starting job: {start_str} to {end_str}")

    try:
        start = datetime.strptime(start_str, "%Y-%m-%d")
        end = datetime.strptime(end_str, "%Y-%m-%d")

        # --- THE FIX ---
        # asyncio.run() creates a new event loop, runs the coroutine,
        # and shuts down the loop when finished.
        asyncio.run(scrape_history_records(start, end))

        print("✅ Job Completed")

    except Exception as e:
        print(f"❌ Job Failed: {e}")
        # Dramatiq catches this and handles retries
        raise e