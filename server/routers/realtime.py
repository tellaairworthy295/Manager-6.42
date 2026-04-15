import asyncio

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from server.dependencies import get_redis
from utils.logging_config import get_others_logger

logger = get_others_logger()
router = APIRouter(prefix="/sse", tags=["Real-time"])


@router.get("/{channel}")
async def sse_stream(channel: str, redis: aioredis.Redis = Depends(get_redis)):
    stream_key = f"sse:{channel}"

    async def event_gen():
        last_id = "$"
        block_ms = 1000
        max_block_ms = 5000
        idle_rounds = 0
        backoff_threshold = 3
        task = asyncio.current_task()

        logger.info("SSE connected: %s", stream_key)

        try:
            while task and not task.cancelled():
                entries = await redis.xread({stream_key: last_id}, block=block_ms, count=50)

                if not entries:
                    idle_rounds += 1
                    if idle_rounds >= backoff_threshold:
                        block_ms = min(block_ms * 2, max_block_ms)
                    yield ": ping\n\n"
                    continue

                idle_rounds = 0
                block_ms = 1000

                for _, messages in entries:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        payload = fields.get("data")
                        if not payload:
                            continue

                        logger.info("RECEIVE %s", payload[:20])
                        event_type = "final" if '"file"' in payload else "text"
                        yield f"id: {msg_id}\nevent: {event_type}\ndata: {payload}\n\n"
        finally:
            logger.info("SSE disconnected: %s", stream_key)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
