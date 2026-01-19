import asyncio
import uuid

class RedisLock:
    def __init__(self, redis, key: str, ttl: int = 60, retry_delay: float = 0.5):
        self.redis = redis
        self.key = key
        self.ttl = ttl
        self.retry_delay = retry_delay
        self.token = str(uuid.uuid4())

    async def acquire(self):
        while True:
            acquired = await self.redis.set(
                self.key,
                self.token,
                nx=True,
                ex=self.ttl,
            )
            if acquired:
                return
            await asyncio.sleep(self.retry_delay)

    async def release(self):
        # Safe release: delete only if token matches
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await self.redis.eval(script, keys=[self.key], args=[self.token])
        except Exception:
            pass

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.release()
