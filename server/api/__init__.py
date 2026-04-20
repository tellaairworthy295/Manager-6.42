"""External API routers owned by the server package."""
from .news_api import router as news_router

__all__ = [
    "news_router",
]
