"""
Database module with SQLAlchemy models and connection pooling for MySQL.
Provides modular, extensible, and maintainable database operations.
"""
import json
import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, scoped_session
from sqlalchemy.pool import QueuePool
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, case

from contextlib import contextmanager
from config import setup_logging

logger = setup_logging("logs/database", "database")

from sqlalchemy.orm import declarative_base
Base = declarative_base()


# ==================== Database Models ====================

class NewsArticle(Base):
    """Model for news articles table."""
    __tablename__ = "news_articles"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(500), unique=True, nullable=False, index=True)
    source = Column(String(100))
    title = Column(Text)
    content = Column(Text)
    title_zh = Column(Text)
    content_zh = Column(Text)
    content_fully_loaded = Column(Boolean, default=False)
    translation_status = Column(Integer, default=0)
    scraped_at = Column(DateTime, default=datetime.now, index=True)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "title_zh": self.title_zh,
            "content_zh": self.content_zh,
            "content_fully_loaded": self.content_fully_loaded,
            "translation_status": self.translation_status,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }


class Stock(Base):
    """Model for stocks table."""
    __tablename__ = "stocks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(20), nullable=False, index=True)
    stock = Column(String(100), nullable=False)
    code = Column(String(50))
    analysis = Column(Text)
    
    __table_args__ = (
        UniqueConstraint('date', 'stock', name='uq_date_stock'),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "date": self.date,
            "stock": self.stock,
            "code": self.code,
            "analysis": self.analysis,
        }


class NewsAnalysis(Base):
    """Model for news analysis table."""
    __tablename__ = "news_analysis"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(20), nullable=False, index=True)
    time = Column(DateTime)
    analysis = Column(Text)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "date": self.date,
            "time": self.time.isoformat() if self.time else None,
            "analysis": self.analysis,
        }


# ==================== Database Connection Setup ====================

class DatabaseManager:
    """Manages database connections with connection pooling."""
    def __init__(self):
        self.engine = None
        self.session_factory = None
        self._initialize_connection()
    
    def _initialize_connection(self):
        """
        Initialize MySQL connection with connection pooling.
        
        Environment variables (with defaults):
        - DB_HOST: MySQL host (default: "localhost")
        - DB_PORT: MySQL port (default: "3306")
        - DB_USER: MySQL username (default: "muheng")
        - DB_PASSWORD: MySQL password (default: "")
        - DB_NAME: Database name (default: "scraped_data")
        """
        # Load config.json once at module load
        if not hasattr(self, "_config_cache"):
            with open("json/config.json", "r", encoding="utf-8") as f:
                self._config_cache = json.load(f)
        db_config = self._config_cache.get("DatabaseConfig", {})
        db_host = db_config.get("DB_HOST", "localhost")
        db_port = db_config.get("DB_PORT", 3306)
        db_user = db_config.get("DB_USER", "muheng")
        db_password = db_config.get("DB_PASSWORD", "123456")
        db_name = db_config.get("DB_NAME", "scraped_data")
        
        # Create connection string
        connection_string = (
            f"mysql+mysqlconnector://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            f"?charset=utf8mb4&collation=utf8mb4_unicode_ci"
        )
        
        # Create engine with connection pooling
        self.engine = create_engine(
            connection_string,
            poolclass=QueuePool,
            pool_size=10,  # Number of connections to maintain
            max_overflow=20,  # Maximum number of connections beyond pool_size
            pool_pre_ping=True,  # Verify connections before using
            pool_recycle=3600,  # Recycle connections after 1 hour
            echo=False,  # Set to True for SQL query logging
        )
        
        # Create session factory with scoped_session for thread safety
        self.session_factory = scoped_session(
            sessionmaker(
                bind=self.engine,
                autocommit=False,
                autoflush=False,
            )
        )
        
        logger.info(f"Database connection initialized: {db_host}:{db_port}/{db_name}")
    
    @contextmanager
    def get_session(self) -> Session:
        """Get a database session with automatic cleanup."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()
    
    def create_tables(self):
        """Create all tables if they don't exist."""
        Base.metadata.create_all(self.engine)
        logger.info("Database tables created/verified")
    
    def close(self):
        """Close all database connections."""
        self.session_factory.remove()
        self.engine.dispose()
        logger.info("Database connections closed")


# Global database manager instance
_db_manager = None


def get_db_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
        _db_manager.create_tables()
    return _db_manager


# ==================== Database Operations ====================

class NewsArticleRepository:
    """Repository for news article operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
class NewsArticleRepository:
    """Repository for news article operations with safe upsert."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def insert_or_update_article(
        self,
        url: str,
        source: str,
        title: str,
        content: str,
        content_fully_loaded: bool,
        title_zh: Optional[str] = None,
        content_zh: Optional[str] = None,
        translation_status: int = 0,
    ) -> NewsArticle:
        """Insert or update a news article safely with upsert logic."""
        with self.db_manager.get_session() as session:
            stmt = insert(NewsArticle).values(
                url=url,
                source=source,
                title=title,
                content=content,
                title_zh=title_zh,
                content_zh=content_zh,
                content_fully_loaded=content_fully_loaded,
                translation_status=translation_status,
                scraped_at=datetime.now(),
            )

            # On duplicate key (same URL), update fields
            stmt = stmt.on_duplicate_key_update(
                source=stmt.inserted.source,
                title=stmt.inserted.title,
                content=stmt.inserted.content,
                title_zh=stmt.inserted.title_zh,
                content_zh=stmt.inserted.content_zh,
                content_fully_loaded=stmt.inserted.content_fully_loaded,
                translation_status=stmt.inserted.translation_status,
                scraped_at=stmt.inserted.scraped_at,
            )

            try:
                result = session.execute(stmt)
                session.commit()
                logger.info(f"Saved article to database: {url}")
            except IntegrityError as e:
                session.rollback()
                logger.warning(f"Upsert failed for article {url}: {e}")
                raise
            
            return result

    
    def fetch_recent_articles(self, hours: int = 6) -> List[Dict[str, Any]]:
        """Fetch articles scraped within the last N hours."""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        with self.db_manager.get_session() as session:
            articles = (
                session.query(NewsArticle)
                .filter(
                    NewsArticle.scraped_at >= cutoff_time
                )
                .all()
            )
            
            result = [
                {"url": a.url, "title": a.title, "content": a.content}
                for a in articles
            ]
            logger.info(f"Fetched {len(result)} articles from database")
            return result
    
    def get_recent_urls(self, hours: int = 1) -> List[str]:
        """Get URLs that were scraped within the last N hours."""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        with self.db_manager.get_session() as session:
            urls = (
                session.query(NewsArticle.url)
                .filter(
                    NewsArticle.content_fully_loaded == True,
                    NewsArticle.scraped_at >= cutoff_time
                )
                .all()
            )
            return [url[0] for url in urls]
    
    def fetch_articles_for_export(self, hours: int = 12) -> List[Dict[str, Any]]:
        """Fetch articles for export (scraped within last N hours)."""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        with self.db_manager.get_session() as session:
            articles = (
                session.query(NewsArticle)
                .filter(
                    NewsArticle.content_fully_loaded == True,
                    NewsArticle.scraped_at >= cutoff_time
                )
                .all()
            )
            
            return [
                {"title": a.title, "content": a.content}
                for a in articles
                if a.title and a.content
            ]


class StockRepository:
    """Repository for stock operations with safe upsert."""

    def __init__(self, db_manager):
        self.db_manager = db_manager

    def get_today_stocks(self):
        """
        Retrieve today's stocks and their analysis.
        Returns a list of dicts: [{"stock": ..., "code": ..., "analysis": ...}, ...]
        """
        today = datetime.now().date()
        with self.db_manager.get_session() as session:
            stocks = (
                session.query(Stock)
                .filter(Stock.date == today)
                .all()
            )
            result = [
                {
                    "stock": s.stock,
                    "code": s.code,
                    "analysis": s.analysis
                }
                for s in stocks
            ]
            logger.info(f"Fetched {len(result)} stocks for today ({today}) from database")
            return result

    def insert_or_update_stocks(self, records: List[Dict[str, Any]]) -> int:
        """
        Insert or update multiple stock records.

        - If a record exists (same date + stock), merge only *new* analysis text.
        - If not exists, insert new.
        """
        if not records:
            return 0

        with self.db_manager.get_session() as session:
            count = 0
            for rec in records:
                stmt = insert(Stock).values(
                    date=rec["date"],
                    stock=rec["stock"],
                    code=rec.get("code"),
                    analysis=rec.get("analysis"),
                )

                # On duplicate key, merge analysis only if new
                stmt = stmt.on_duplicate_key_update(
                                analysis=case(
                                    (
                                        func.locate(
                                            stmt.inserted.analysis,
                                            func.ifnull(Stock.analysis, "")
                                        ) == 0,
                                        func.trim(
                                            func.concat_ws("\n", Stock.analysis, stmt.inserted.analysis)
                                        ),
                                    ),
                                    else_=Stock.analysis,
                                )
                            )

                try:
                    session.execute(stmt)
                    count += 1
                except IntegrityError:
                    session.rollback()
                    logger.warning(f"Duplicate handling failed for {rec['stock']} on {rec['date']}")

            session.commit()
            logger.info(f"Saved {count} stock records to database (inserted or analysis-merged)")
            return count


class NewsAnalysisRepository:
    """Repository for news analysis operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def save_analysis(self, analysis_result: str) -> NewsAnalysis:
        """Save analysis result."""
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        time = now.strftime('%H:%M:%S')
        
        with self.db_manager.get_session() as session:
            analysis = NewsAnalysis(
                date=date_str,
                time=time,
                analysis=analysis_result,
            )
            session.add(analysis)
            session.flush()
            logger.info(f"Saved analysis to database for {date_str}")
            return analysis

