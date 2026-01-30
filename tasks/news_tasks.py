# news_tasks.py
import json
import random
import time
import dramatiq
from utils.redis_utils import get_redis_client
from scraper.news_scraper import scrape_news, fetch_urls_from_page
from utils.upload_knowledge import upload_dify_knowledge, clean_dify_knowledge
from utils.logging_config import get_news_task_logger

logger = get_news_task_logger()

# ================= Main per-site scraping task ==================
@dramatiq.actor(
    queue_name="news",
    time_limit=25*60*1000,
    max_retries=0,  # Fail fast: a failed site is considered failed
)
def scrape_news_task(*, query: str, site: str = None, source: str = None, group_key: str = None, now_str: str = None):
    """
    Scrape news articles for ONE website.
    Records results straight to Redis.
    group_key: used for aggregator/stream sync (see scrape_all_news)
    """
    logger.info(f"Starting scraping task for site: {source}, query: {query}")

    urls = None
    try:
        urls = fetch_urls_from_page(query, site)
    except Exception as e:
        logger.error(f"Failed to fetch URLs for {source}: {e}")
        result = {
            "source": source,
            "total_content": [],
            "success_count": 0,
            "failed_count": 0,
            "failed_urls": [],
            "message": f"Error fetching URLs: {str(e)}"
        }
        _record_site_result(group_key, source, result, now_str)
        logger.info(f"Task completed for {source} with error: {result}")
        return

    if not urls:
        logger.info(f"No new URLs found for {source}")
        result = {
            "source": source,
            "total_content": [],
            "success_count": 0,
            "failed_count": 0,
            "failed_urls": [],
            "message": "No new URLs to scrape"
        }
        _record_site_result(group_key, source, result, now_str)
        logger.info(f"Task completed for {source}: {result}")
        return

    total = len(urls)
    logger.info(f"Scraping {total} URLs for {source}")

    total_content = []
    success_count = 0
    failed_count = 0
    failed_urls = []

    for url in urls:
        time.sleep(random.uniform(2.0, 10.0))
        content = ""
        for attempt in range(3):
            try:
                content = scrape_news(url, source)
            except Exception as e:
                content = ""
                logger.warning(f"Failed to scrape {url} (attempt {attempt+1}/3): {e}")
            if content:
                success_count += 1
                break
            else:
                if attempt == 2:
                    failed_count += 1
                    failed_urls.append(url)
                    logger.warning(f"Giving up on {url} after 3 attempts")
                else:
                    logger.warning(f"Content empty for {url} (attempt {attempt+1}/3), will retry")
                    time.sleep(random.uniform(3.0, 5.5))
        if content:
            total_content.append(content)
        time.sleep(random.uniform(2.0, 4.0))

    final_result = {
        "source": source,
        "total_content": total_content,
        "success_count": success_count,
        "failed_count": failed_count,
        "failed_urls": failed_urls,
        "message": "Done"
    }
    _record_site_result(group_key, source, final_result, now_str)
    logger.info(f"Completed scraping task for {source}: {success_count} success, {failed_count} failed")

def _record_site_result(group_key: str, source: str, result: dict, now_str: str):
    """
    Internal: persist site result as JSON under Redis aggregator group.
    """
    if not group_key:
        # Fallback path, skip aggregation
        return
    r = get_redis_client()
    try:
        r.rpush(f"newsagg:{group_key}:results", json.dumps(result))
        task_id = source or "unknown"
        r.sadd(f"newsagg:{group_key}:completed", task_id)
    except Exception as e:
        logger.error(f"Failed to update aggregator for {group_key}: {e}")
        return
    # Optimistic: always trigger aggregator, as in agent_tasks.py
    finalize_and_update_dify.send(group_key=group_key, now_str=now_str)

# ================= Non-blocking streaming aggregator ==================
@dramatiq.actor(queue_name="news", max_retries=0)
def finalize_and_update_dify(group_key: str, now_str: str):
    """
    Aggregate partial results and update Dify KB after all site scraping tasks are done.
    Called many times, but Dify KB is only actually updated *once* (idempotent).
    """
    r = get_redis_client()
    flag = False
    try:
        expected = int(r.get(f"newsagg:{group_key}:expected") or 0)
        completed = r.scard(f"newsagg:{group_key}:completed")
        if completed < expected:
            return  # Not ready yet

        # Guard: run only once
        if not r.setnx(f"newsagg:{group_key}:finalized", "1"):
            return

        flag = True
        # Fetch results
        results_raw = r.lrange(f"newsagg:{group_key}:results", 0, -1)
        results = [json.loads(x) for x in results_raw]
        if not results:
            logger.warning("No scraping results found")
            return

        total_sites = len(results)
        successes = sum(rr.get("success_count", 0) for rr in results)
        failures = sum(rr.get("failed_count", 0) for rr in results)

        all_content = [
            {"source": rr["source"], "content": rr["total_content"]}
            for rr in results
            if rr.get("total_content")
        ]
        # clean_dify_knowledge()
        # try:
        #     upload_dify_knowledge(all_content, now_str, max_retries=3, retry_delay=5)
        #     logger.info("✅ Dify KB updated after scraping all sources")
        # except Exception as e:
        #     logger.error(f"❌ Failed to update Dify KB: {e}")

        # logger.info(
        #     f"Dify KB updated: {total_sites} sites, {successes} success, {failures} failed"
        # )
    except Exception as e:
        logger.exception(f"Aggregator failed: {e}")
    finally:
        if flag:
            # Cleanup aggregator keys
            try:
                r.delete(
                    f"newsagg:{group_key}:expected",
                    f"newsagg:{group_key}:results",
                    f"newsagg:{group_key}:completed",
                    f"newsagg:{group_key}:finalized",
                )
            except Exception as e:
                logger.warning(f"Aggregator cleanup failed: {e}")

# ================= Main entry point ==================
@dramatiq.actor(queue_name="news")
def scrape_all_news(requests: list[dict], now_str):
    """
    Parent task: run scraping subtasks for each site concurrently,
    then update Dify KB once all are finished (non-blocking streaming aggregator).
    """
    # Unique key for this run
    group_key = f"{now_str}-{random.randint(10000, 99999)}"
    messages = []
    for req in requests:
        q, s, src = req.get("query"), req.get("site"), req.get("source")
        if q and s and src:
            messages.append(
                scrape_news_task.message(
                    query=q,
                    site=s,
                    source=src,
                    group_key=group_key,
                    now_str=now_str
                )
            )
    if not messages:
        logger.warning("No tasks created for scrape_all_news")
        return {"status": "no_tasks"}

    r = get_redis_client()
    r.set(f"newsagg:{group_key}:expected", len(messages))
    r.delete(
        f"newsagg:{group_key}:results",
        f"newsagg:{group_key}:completed",
        f"newsagg:{group_key}:finalized"
    )

    # fire and forget: tasks will call aggregator
    dramatiq.group(messages).run()

    logger.info(f"[scrape_all_news] {len(messages)} site tasks dispatched, group_key={group_key}")
