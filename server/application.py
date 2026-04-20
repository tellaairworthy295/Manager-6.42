from contextlib import asynccontextmanager

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
from server.api.news_api import router as news_router
from server.config import APP_TITLE, MCP_PATH
from utils.logging_config import get_others_logger
from utils.redis_utils import close_loop_redis, create_aioredis

logger = get_others_logger()


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await create_aioredis()
        try:
            yield
        finally:
            await close_loop_redis()

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)

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
    app.include_router(news_router)

    @app.post("/ping")
    async def ping():
        return {"status": "pong"}

    return app
