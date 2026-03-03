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
    time_limit=25 * 60 * 1000,
    max_retries=0,
)
def scrape_news_task(
        *,
        query: str,
        site: str = None,
        source: str = None,
        rate_limit: int = 4,
        group_key: str = None,
        now_str: str = None
):
    logger.info(f"[Task] Starting: source={source}, query={query}")

    # ── 1. Fetch URLs ────────────────────────────────────────────────────
    urls = None
    try:
        urls = fetch_urls_from_page(query, site, rate_limit)
    except Exception as e:
        logger.error(f"[Task] URL fetch failed for {source}: {e}")
        _record_site_result(group_key, source, {
            "source": source,
            "total_content": [],
            "success_count": 0,
            "failed_count": 0,
            "failed_urls": [],
            "message": f"Error fetching URLs: {e}"
        }, now_str)
        return

    if not urls:
        logger.info(f"[Task] No new URLs for {source}")
        _record_site_result(group_key, source, {
            "source": source,
            "total_content": [],
            "success_count": 0,
            "failed_count": 0,
            "failed_urls": [],
            "message": "No new URLs to scrape"
        }, now_str)
        return

    logger.info(f"[Task] Scraping {len(urls)} URLs for {source}")

    # ── 2. Scrape articles using a shared warm session ───────────────────
    # max_articles_per_driver: rotate the browser after N articles
    # so no single session fingerprint is overexposed.
    # 3–4 is a sweet spot: enough to amortize warm-up cost, few enough
    # to avoid pattern detection.
    session = ArticleScrapeSession(max_articles_per_driver=4)

    total_content = []
    success_count = 0
    failed_count = 0
    failed_urls = []

    try:
        for url in urls:
            # Inter-article human delay — varies to avoid rhythmic patterns
            _human_pause_inter = random.uniform(4.0, 12.0)
            logger.info(f"[Task] Waiting {_human_pause_inter:.1f}s before next article...")
            time.sleep(_human_pause_inter)

            content = ""
            for attempt in range(3):
                try:
                    content = session.scrape(url, source)
                except Exception as e:
                    logger.error(f"[Task] scrape() raised on attempt {attempt + 1}: {e}")
                    content = ""

                if content:
                    success_count += 1
                    total_content.append(content)
                    break
                else:
                    if attempt == 2:
                        failed_count += 1
                        failed_urls.append(url)
                        logger.warning(f"[Task] Giving up on {url} after 3 attempts")
                    else:
                        logger.warning(f"[Task] Empty content for {url}, retrying...")
                        time.sleep(random.uniform(3.0, 8.0))

    finally:
        # Always clean up the browser, even if an exception occurs mid-loop
        session.close()

    final_result = {
        "source": source,
        "total_content": total_content,
        "success_count": success_count,
        "failed_count": failed_count,
        "failed_urls": failed_urls,
        "message": "Done"
    }
    _record_site_result(group_key, source, final_result, now_str)
    logger.info(f"[Task] Done: {source} — {success_count} success, {failed_count} failed")
    

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
        q, s, src, rl = req.get("query"), req.get("site"), req.get("source"), req.get("rate_limit")
        if q and s and src:
            messages.append(
                scrape_news_task.message(
                    query=q,
                    site=s,
                    source=src,
                    group_key=group_key,
                    rate_limit=rl,
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
