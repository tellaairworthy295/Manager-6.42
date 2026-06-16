from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from exception.exception_handler import (
    NetworkException,
    SelectorException,
    ValidationError,
    generic_exception_handler,
    network_exception_handler,
    selection_exception_handler,
    validation_exception_handler,
)
from server.api.agent_api import router as agent_router
from server.api.news_api import router as news_router
from server.api.records_api import router as records_router
from server.api.stocks_api import router as stocks_router
from server.config import APP_TITLE, MCP_PATH
from server.mcp_tools import build_mcp
from server.routers.previews import router as preview_router
from server.routers.realtime import router as realtime_router
from server.scheduler import build_scheduler
from utils.logging_config import get_others_logger
from utils.redis_utils import close_loop_redis, create_aioredis

logger = get_others_logger()


def create_app() -> FastAPI:
    mcp = build_mcp()
    mcp_app = mcp.http_app(path=MCP_PATH)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        os.environ['NO_PROXY'] = '*'
        scheduler = build_scheduler()
        async with mcp_app.lifespan(app):
            await create_aioredis()
            scheduler.start()
            logger.info("[Scheduler] Started. Jobs: %s", scheduler.get_jobs())
            try:
                yield
            finally:
                scheduler.shutdown(wait=False)
                logger.info("[Scheduler] Shut down.")
                await close_loop_redis()

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.mount("/mcp", mcp_app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(ValidationError, validation_exception_handler)
    app.add_exception_handler(NetworkException, network_exception_handler)
    app.add_exception_handler(SelectorException, selection_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    app.include_router(realtime_router)
    app.include_router(news_router)
    app.include_router(stocks_router)
    app.include_router(agent_router)
    app.include_router(records_router)
    app.include_router(preview_router)

    @app.post("/ping")
    async def ping():
        return {"status": "pong"}

    return app
