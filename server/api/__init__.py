"""External API routers owned by the server package."""

from .agent_api import router as agent_router
from .news_api import router as news_router
from .records_api import router as records_router
from .stocks_api import router as stocks_router

__all__ = [
    "agent_router",
    "news_router",
    "records_router",
    "stocks_router",
]
