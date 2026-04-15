from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import IntegrityError

from ..manager import DatabaseManager
from ..models import NewsAnalysis, NewsArticle


def _recent_article_candidates(session, cutoff_time: datetime):
    cutoff_date = cutoff_time.date()
    today = date.today()
    if cutoff_date == today:
        candidate_dates = [today]
    else:
        candidate_dates = [today - timedelta(days=1), today]
    return session.query(NewsArticle).filter(NewsArticle.scraped_date.in_(candidate_dates))


def _recent_articles_since(session, cutoff_time: datetime) -> list[NewsArticle]:
    return [
        article
        for article in _recent_article_candidates(session, cutoff_time).all()
        if article.scraped_at >= cutoff_time
    ]


class NewsArticleRepository:
    """Repository for news article operations with safe upsert."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def insert_or_update_article(
        self,
        url: str,
        publish_at,
        source: str,
        title: str,
        content: str,
        xml_content,
        content_fully_loaded: bool,
        title_zh: Optional[str] = None,
        content_zh: Optional[str] = None,
    ) -> NewsArticle:
        with self.db_manager.get_session() as session:
            if not publish_at:
                publish_at = datetime.now(timezone.utc)

            stmt = insert(NewsArticle).values(
                url=url,
                source=source,
                title=title,
                content=content,
                title_zh=title_zh,
                content_zh=content_zh,
                xml_content=xml_content,
                content_fully_loaded=content_fully_loaded,
                publish_at=publish_at,
                scraped_date=date.today(),
                scraped_at=datetime.now(),
            )
            stmt = stmt.on_duplicate_key_update(
                source=stmt.inserted.source,
                title=stmt.inserted.title,
                content=stmt.inserted.content,
                title_zh=stmt.inserted.title_zh,
                content_zh=stmt.inserted.content_zh,
                xml_content=xml_content,
                content_fully_loaded=stmt.inserted.content_fully_loaded,
                publish_at=stmt.inserted.publish_at,
                scraped_date=stmt.inserted.scraped_date,
                scraped_at=stmt.inserted.scraped_at,
            )

            try:
                result = session.execute(stmt)
                session.commit()
            except IntegrityError:
                session.rollback()
                raise

            return result

    def fetch_recent_articles(self, hours: int = 6) -> list[dict[str, Any]]:
        if hours == 0:
            cutoff_time = datetime.now() - timedelta(minutes=15)
        else:
            cutoff_time = datetime.now() - timedelta(hours=hours)

        with self.db_manager.get_session() as session:
            recent_articles = _recent_articles_since(session, cutoff_time)
            return [
                {"url": article.url, "title": article.title, "content": article.content}
                for article in recent_articles
            ]

    def get_recent_urls(self, hours: int = 1) -> list[str]:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        with self.db_manager.get_session() as session:
            return [article.url for article in _recent_articles_since(session, cutoff_time)]


class NewsAnalysisRepository:
    """Repository for news analysis operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def save_analysis(self, data: dict) -> NewsAnalysis | None:
        if not data:
            return None

        with self.db_manager.get_session() as session:
            analysis = NewsAnalysis(
                url=data["url"],
                scraped_date=data["scraped_date"],
                stocks=data["stocks"],
            )
            session.add(analysis)
            session.commit()
            return analysis
