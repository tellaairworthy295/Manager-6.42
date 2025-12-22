#utils/redis_utils.py

import urllib.parse
import redis

REDIS_URL = "redis://localhost:6379/0"
def get_redis_client_raw():
    parsed = urllib.parse.urlparse(REDIS_URL)
    db_index = int(parsed.path[1:]) if parsed.path else 0
    return redis.Redis(
        host=parsed.hostname,
        port=parsed.port,
        db=db_index,
    )

def get_redis_client():
    parsed = urllib.parse.urlparse(REDIS_URL)
    db_index = int(parsed.path[1:]) if parsed.path else 0
    return redis.Redis(
        host=parsed.hostname,
        port=parsed.port,
        db=db_index,
        decode_responses=True,  # store JSON as strings
    )

# ⚠️ Optional: clean Redis (DANGEROUS – same as your Celery logic)
def clean_redis_db(redis_url: str, flush_all: bool = True):
    """
    Flush Redis so that all Dramatiq tasks and results are removed.
    By default, flushes *all* databases (flushall).
    If flush_all=False, only flushes the specific DB index in redis_url.
    """
    try:
        parsed = urllib.parse.urlparse(redis_url)
        db_index = int(parsed.path[1:]) if parsed.path else 0
        client = redis.Redis(
            host=parsed.hostname,
            port=parsed.port,
            db=db_index,
        )
        if flush_all:
            # ⚠️ This clears *every* Redis DB on this server
            client.flushall()
        else:
            # Only clear the single DB index specified in redis_url
            client.flushdb()
    except Exception as e:
        print(f"Failed to flush Redis: {e}")