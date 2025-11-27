from celery_app import celery_app
from config import setup_logging
from scraper.rb_scraper import scrape_article  # your existing function

logger = setup_logging("logs/news_task", "news_task")

@celery_app.task(bind=True, name="tasks.news_tasks.scrape_news_task")
def scrape_news_task(self, urls: list[str]):
    total = len(urls)
    success = 0
    failed = 0
    failed_urls = []

    for i, url in enumerate(urls, start=1):
        try:
            scrape_article(url)
            success += 1
            status = "success"
        except Exception as e:
            logger.warning(f"Failed to scrape {url}: {e}")
            failed += 1
            failed_urls.append(url)
            status = "failed"

        # 🔵 Update progress after each URL
        progress = {
            "current": i,
            "total": total,
            "percent": round(i / total * 100, 2),
            "url": url,
            "status": status,
            "success_count": success,
            "failed_count": failed,
        }
        self.update_state(state="PROGRESS", meta=progress)

    final_result = {
        "status": "DONE",
        "success_count": success,
        "failed_count": failed,
        "failed_urls": failed_urls,
    }

    self.update_state(state="SUCCESS", meta=final_result)
    return final_result

