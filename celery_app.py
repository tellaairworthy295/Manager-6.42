# celery_app.py
from celery import Celery

# Optional: Clean Redis backend and broker keys at init (dangerous: clears ALL keys in that db index!)
def clean_redis_db(redis_url):
    try:
        import redis
        import urllib.parse
        parsed = urllib.parse.urlparse(redis_url)
        db_index = int(parsed.path[1:]) if parsed.path else 0
        client = redis.Redis(host=parsed.hostname, port=parsed.port, db=db_index)
        client.flushdb()
        print(f"[celery_app] Redis DB {db_index} at {parsed.hostname}:{parsed.port} cleared.")
    except Exception as e:
        print(f"[celery_app] WARNING: Failed to clean Redis: {e}")

BROKER_URL = "redis://localhost:6379/0"
BACKEND_URL = "redis://localhost:6379/0"

# Clean Redis on startup (if you want this, but be careful with side effects!)
clean_redis_db(BROKER_URL)
# If backend is same as broker, only need to clear once.

celery_app = Celery(
    "worker",
    broker=BROKER_URL,
    backend=BACKEND_URL,
)

celery_app.conf.update(
    task_routes={
        "tasks.news_tasks.*": {"queue": "news"},
        "tasks.alpha_tasks.*": {"queue": "alpha"},
    },
    result_expires=3600,  # results expire in 1 hour
    task_track_started=True,    
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)
# 👇 Import task modules so they register on app startup
import tasks.alpha_tasks
import tasks.news_tasks