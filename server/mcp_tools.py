import asyncio

from fastmcp import FastMCP
from pydantic import Field

from server.config import MCP_NAME
from utils.database import (
    NewsArticleRepository,
    StockRepository,
    get_db_manager,
)
from utils.logging_config import get_others_logger

logger = get_others_logger()


def build_mcp() -> FastMCP:
    mcp = FastMCP(name=MCP_NAME)

    @mcp.tool(name="fetch_stocks", description="Fetch stocks(name and code) from past N days.")
    async def fetch_stocks(
        days: int = Field(gt=0, le=30, description="Number of recent days(1-30) to fetch. (e.g., 7)")
    ):
        logger.info("fetch_stocks: %s", days)
        stock_repo = StockRepository(get_db_manager())
        data = await asyncio.to_thread(stock_repo.get_recent_stock_name_code, days)
        return {"data": data}

    @mcp.tool(
        name="fetch_stocks_and_analyses",
        description="Fetch relevant stocks and their per-day analyses for the past N days.",
    )
    async def fetch_stocks_and_analyses(
        stocks: list[str] = Field(
            min_length=1,
            description="List of stock identifiers (names and codes, at least one stock).",
        ),
        days: int = Field(
            ge=0,
            le=30,
            description="Number of recent days(1-30) to fetch analyses, e.g., 5",
        ),
    ):
        logger.info("fetch_stocks_and_analyses: stocks=%s days=%s", stocks, days)
        stock_repo = StockRepository(get_db_manager())
        data = await asyncio.to_thread(stock_repo.get_analysis_by_stock, stocks, days)
        return {"data": data}

    @mcp.tool(name="fetch_news", description="Fetch recent news articles from local MySQL database.")
    async def fetch_news():
        news_repo = NewsArticleRepository(get_db_manager())
        data = await asyncio.to_thread(news_repo.fetch_recent_articles, 0)
        return {"data": data}

    return mcp
