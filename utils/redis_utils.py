#utils/redis_utils.py
import asyncio
import urllib.parse
import redis
import redis.asyncio as aioredis

REDIS_URL = "redis://localhost:6379/0"

_redis_by_loop: dict[int, aioredis.Redis] = {}

def create_aioredis() -> aioredis.Redis:
    parsed = urllib.parse.urlparse(REDIS_URL)
    return aioredis.Redis(
        host=parsed.hostname,
        port=parsed.port,
        db=int(parsed.path[1:]) if parsed.path else 0,
        decode_responses=True,
    )

def get_aioredis_client() -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    redis = _redis_by_loop.get(loop_id)
    if redis is None:
        redis = create_aioredis()
        _redis_by_loop[loop_id] = redis

    return redis

async def close_loop_redis():
    loop = asyncio.get_running_loop()
    redis = _redis_by_loop.pop(id(loop), None)
    if redis:
        await redis.close()
        await redis.connection_pool.disconnect()



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