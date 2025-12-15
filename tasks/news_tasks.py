import random
import time
from celery_app import celery_app
from loguru import logger
from scraper.news_scraper import scrape_news, fetch_urls_from_page
from utils.upload_knowledge import upload_dify_knowledge, clean_dify_knowledge
from celery import group, chord

# Configure loguru for news tasks module
logger.add("logs/news_task/news_task_{time:YYYY-MM-DD}.log", rotation="00:00", retention="15 days", encoding="utf-8")

@celery_app.task(
    bind=True,
    name="tasks.news_tasks.scrape_news_task",
    max_retries=1,
    default_retry_delay=5,  # seconds between retries
)
def scrape_news_task(self, query: str, site: str = None, source: str = None):
    """
    Scrape news articles for ONE website.
    Parameters:
        query: Google News search query (e.g., "site:bloomberg.com/news/articles")
        site: Optional site name (e.g., "bloomberg.com").
    
    Returns:
        Dictionary with scraping results for this website.
    """
    logger.info(f"Starting scraping task for site: {source}, query: {query}")
    # Fetch URLs for this website, apply retry logic for the top-level fetch
    try:
        urls = fetch_urls_from_page(query, site)
    except Exception as e:
        logger.error(f"Failed to fetch URLs for {source}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)   # retry whole task
        return {"status": "failed", "source": source, "error": str(e)}

    
    if not urls:
        logger.info(f"No new URLs found for {source}")
        result = {
            "status": "DONE",
            "source": source,
            "success_count": 0,
            "failed_count": 0,
            "failed_urls": [],
            "message": "No new URLs to scrape"
        }
        self.update_state(state="SUCCESS", meta=result)
        return result
    
    total = len(urls)
    success = 0
    failed = 0
    failed_urls = []

    logger.info(f"Scraping {total} URLs for {source}")

    total_content = []
    for i, url in enumerate(urls, start=1):
        url_success = False
        content = ""
        # Brief dwell before opening a new article to avoid rapid-fire requests
        time.sleep(random.uniform(2.5, 5.0))
        for attempt in range(1, 4):
            try:
                content = scrape_news(url, source)
                if content:
                    success += 1
                    url_success = True
                    break
                else:
                    # If content is empty, treat like a failure and allow retry
                    logger.warning(f"Content empty for {url} (attempt {attempt}/3)")
                    if attempt == 3:
                        failed += 1
                        failed_urls.append(url)
                    else:
                        time.sleep(random.uniform(3.0, 5.5))
            except Exception as e:
                logger.warning(f"Failed to scrape {url} (attempt {attempt}/2): {e}")
                if attempt == 2:
                    failed += 1
                    failed_urls.append(url)
                else:
                    time.sleep(random.uniform(3.0, 5.5))
        # If scraping succeeded, set status, otherwise already marked as failed above
        if content:
            total_content.append(content)
        # Small pause between URLs to avoid hammering the target site
        time.sleep(random.uniform(2.0, 4.0))
        # Update progress after each URL
        progress = {
            "source": source,
            "current": i,
            "total": total,
            "url": url,
            "status": "success" if url_success else "failed",
            "success_count": success,
            "failed_count": failed,
        }
        self.update_state(state="PROGRESS", meta=progress)

    final_result = {
        "source": source,
        "status": "DONE",
        "total_content": total_content,
        "success_count": success,
        "failed_count": failed,
        "failed_urls": failed_urls,
    }

    self.update_state(state="SUCCESS", meta=final_result)
    logger.info(f"Completed scraping task for {source}: {success} success, {failed} failed")
    return final_result


@celery_app.task(
    bind=True,
    name="tasks.news_tasks.finalize_and_update_dify",
    max_retries=2,
    default_retry_delay=10,
)
def finalize_and_update_dify(self, results, now_str):
    """
    Callback after all site scraping tasks are done.
    Aggregate results and update Dify KB with retry-aware uploads.
    """
    total_sites = len(results)
    successes = sum(r.get("success_count", 0) for r in results)
    failures = sum(r.get("failed_count", 0) for r in results)

    # Aggregate all content
    all_content = [
        {"source": r.get("source"), "content": r.get("total_content")}
        for r in results
        if r.get("total_content")  # skip empty
    ]

    logger.info(
        f"All scraping tasks finished: {total_sites} sources, "
        f"{successes} success, {failures} failed"
    )
    # Cleanup old/failed docs
    clean_dify_knowledge()

    try:
        # Upload with per-document retry logic (already refactored in upload_dify_knowledge)
        upload_dify_knowledge(all_content, now_str, max_retries=3, retry_delay=5)
        logger.info("✅ Dify KB updated after scraping all sources")
    except Exception as e:
        # Catastrophic failure: retry the whole task
        logger.error(f"❌ Failed to update Dify KB: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

    return {
        "status": "DONE",
        "total_sites": total_sites,
        "successes": successes,
        "failures": failures,
    }


@celery_app.task(name="tasks.news_tasks.scrape_all_news")
def scrape_all_news(requests: list[dict], now_str):
    """
    Parent task: run scraping subtasks for each site concurrently,
    then update Dify KB once all are finished.
    """
    job = group(
        scrape_news_task.s(req.get("query"), req.get("site"), req.get("source"))
        for req in requests
        if req.get("query") and req.get("site") and req.get("source")
    )

    result = chord(job)(finalize_and_update_dify.s(now_str))
    logger.info(f"Started scraping chord: {result.id}")
    return {"chord_id": result.id}
