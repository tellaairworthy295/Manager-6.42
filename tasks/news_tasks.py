from celery_app import celery_app
from config import setup_logging
from scraper.news_scraper import scrape_news, fetch_urls_from_page
from utils.upload_knowledge import update_dify_knowledge
from celery import group, chord

logger = setup_logging("logs/news_task", "news_task")

@celery_app.task(
    bind=True,
    name="tasks.news_tasks.scrape_news_task",
    max_retries=2,
    default_retry_delay=5,  # seconds between retries
)
def scrape_news_task(self, query: str, site: str = None):
    """
    Scrape news articles for ONE website.
    
    Parameters:
        query: Google News search query (e.g., "site:bloomberg.com/news/articles")
        site: Optional site name (e.g., "bloomberg.com").
    
    Returns:
        Dictionary with scraping results for this website.
    """
    logger.info(f"Starting scraping task for site: {site}, query: {query}")
    
    # Fetch URLs for this website, apply retry logic for the top-level fetch
    try:
        urls = fetch_urls_from_page(query, site)
    except Exception as e:
        logger.error(f"Failed to fetch URLs for {site}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)   # retry whole task
        return {"status": "failed", "site": site, "error": str(e)}

    
    if not urls:
        logger.info(f"No new URLs found for {site}")
        result = {
            "status": "DONE",
            "site": site,
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

    logger.info(f"Scraping {total} URLs for {site}")

    for i, url in enumerate(urls, start=1):
        url_success = False
        # Try up to 3 times for EACH URL
        for attempt in range(1, 3):
            try:
                scrape_news(url, site)
                success += 1
                url_success = True
                break
            except Exception as e:
                logger.warning(f"Failed to scrape {url} (attempt {attempt}/3): {e}")
                if attempt == 2:
                    failed += 1
                    failed_urls.append(url)
        # If scraping succeeded, set status, otherwise already marked as failed above

        # Update progress after each URL
        progress = {
            "site": site,
            "current": i,
            "total": total,
            "percent": round(i / total * 100, 2),
            "url": url,
            "status": "success" if url_success else "failed",
            "success_count": success,
            "failed_count": failed,
        }
        self.update_state(state="PROGRESS", meta=progress)

    final_result = {
        "site": site,
        "status": "DONE",
        "success_count": success,
        "failed_count": failed,
        "failed_urls": failed_urls,
    }

    self.update_state(state="SUCCESS", meta=final_result)
    logger.info(f"Completed scraping task for {site}: {success} success, {failed} failed")
    return final_result


@celery_app.task(
    bind=True,
    name="tasks.news_tasks.finalize_and_update_dify",
    max_retries=2,
    default_retry_delay=10,
)
def finalize_and_update_dify(self, results):
    """
    Callback after all site scraping tasks are done.
    Aggregate results and update Dify KB.
    """
    total_sites = len(results)
    successes = sum(r.get("success_count", 0) for r in results)
    failures = sum(r.get("failed_count", 0) for r in results)

    logger.info(f"All scraping tasks finished: {total_sites} sites, {successes} success, {failures} failed")

    try:
        update_dify_knowledge()
        logger.info("✅ Dify KB updated after scraping all sites")
    except Exception as e:
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
def scrape_all_news(requests: list[dict]):
    """
    Parent task: run scraping subtasks for each site concurrently,
    then update Dify KB once all are finished.
    """
    job = group(
        scrape_news_task.s(req.get("query"), req.get("site"))
        for req in requests
        if req.get("query") and req.get("site")
    )

    result = chord(job)(finalize_and_update_dify.s())
    logger.info(f"Started scraping chord: {result.id}")
    return {"chord_id": result.id}
