from utils.redis_utils import get_aioredis_client


async def get_redis():
    return get_aioredis_client()
