import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

MAX_USERS = 2
ACTIVE_KEY = "scrape:active_users"


def try_acquire_slot() -> bool:
    """
    Try to acquire a global scraping slot.
    Returns True if allowed, False if system is busy.
    """
    with r.pipeline() as pipe:
        while True:
            try:
                pipe.watch(ACTIVE_KEY)
                current = int(pipe.get(ACTIVE_KEY) or 0)
                if current >= MAX_USERS:
                    pipe.unwatch()
                    return False
                pipe.multi()
                pipe.incr(ACTIVE_KEY)
                pipe.execute()
                return True
            except redis.WatchError:
                continue


def release_slot():
    r.decr(ACTIVE_KEY)
