# dramatiq_app.py
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit,
    TimeLimit,
    Retries,
    ShutdownNotifications,
)
import urllib.parse
import redis
import atexit


# ------------------------------------------------------------
# Redis config
# ------------------------------------------------------------
REDIS_URL = "redis://localhost:6379/0"


# ⚠️ Optional: clean Redis (DANGEROUS – same as your Celery logic)
def clean_redis_db(redis_url: str):
    try:
        parsed = urllib.parse.urlparse(redis_url)
        db_index = int(parsed.path[1:]) if parsed.path else 0
        client = redis.Redis(
            host=parsed.hostname,
            port=parsed.port,
            db=db_index,
        )
        client.flushdb()
    except Exception:
        pass


# Enable only if you REALLY want this
clean_redis_db(REDIS_URL)


# ------------------------------------------------------------
# Dramatiq broker
# ------------------------------------------------------------
broker = RedisBroker(
    url=REDIS_URL,
)

broker.add_middleware(
    # Similar to Celery result_expires
    AgeLimit(max_age=3600_000),  # ms → 1 hour

    # Per-task retry support
    Retries(max_retries=1, min_backoff=5000),

    # Kill stuck tasks
    TimeLimit(time_limit=60 * 60 * 1000),

    # Graceful worker shutdown
    ShutdownNotifications(),
)

dramatiq.set_broker(broker)

import atexit
from pwright.playwright_manager import PlaywrightManager


@atexit.register
def shutdown_playwright():
    PlaywrightManager.instance().shutdown()

# ------------------------------------------------------------
# Task imports (register actors)
# ------------------------------------------------------------
import tasks.agent_tasks
import tasks.news_tasks
