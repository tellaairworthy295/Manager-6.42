"""
Database module with SQLAlchemy models and connection pooling for MySQL.
Provides modular, extensible, and maintainable database operations.
"""
import json
from datetime import datetime, timedelta
import re
from typing import List, Optional, Dict, Any
from sqlalchemy import Float, create_engine, Column, Integer, String, Text, DateTime, Boolean, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, scoped_session
from sqlalchemy.pool import QueuePool
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, case

from contextlib import contextmanager

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

class StockStats(Base):
    __tablename__ = "stock_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(20), nullable=False, index=True)
    up = Column(Integer, nullable=False)
    down = Column(Integer, nullable=False)
    even = Column(Integer, nullable=False)
    break_rate = Column(Float, nullable=False)
    up_number = Column(Integer, nullable=False)
    down_number = Column(Integer, nullable=False)
    max_even = Column(Integer, nullable=False)
    max_break = Column(Integer, nullable=False)

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

class User(Base):
    """Model for users table."""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    email = Column(String(254), unique=True, nullable=False)
    source = Column(String(100), nullable=False)
    phone = Column(String(40))
    password = Column(String(128))
    
    __table_args__ = (
        UniqueConstraint('user_id', 'source', name='uq_userid_source'),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "source": self.source,
            "phone": self.phone,
            "password": self.password,
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
    
    @contextmanager
    def get_session(self) -> Session:
        """Get a database session with automatic cleanup."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()
    
    def create_tables(self):
        """Create all tables if they don't exist."""
        Base.metadata.create_all(self.engine)
    
    def close(self):
        """Close all database connections."""
        self.session_factory.remove()
        self.engine.dispose()


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
            except IntegrityError as e:
                session.rollback()
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


class StockRepository:
    """Repository for stock operations with safe upsert."""

    def __init__(self, db_manager):
        self.db_manager = db_manager

    def get_recent_stock_name_code(self, days):
        """
        Fetch unique stock names and codes from the database within the past 'days'.
        Returns a list of dicts: [{"stock": ..., "code": ...}, ...]
        """
        cutoff_date = (datetime.now() - timedelta(days=days)).date()
        with self.db_manager.get_session() as session:
            stocks = (
                session.query(Stock.stock, Stock.code)
                .filter(Stock.date >= cutoff_date)
                .distinct()
                .all()
            )
            results = []
            seen = set()
            for stock, code in stocks:
                key = stock+code
                if key not in seen:
                    results.append(key)
                    seen.add(key)
            return results

    def get_analysis_by_stock(self, stocks: list[str], days: int) -> list[dict]:
        """
        Fetch date, stock, code, analysis from database based on a list of stocks and days.
        Returns a list of dicts: [{"date": ..., "stock": ..., "code": ..., "analysis": ...}, ...]
        """
        names=[]
        codes = []
        pattern = r'^([\w\s\*\u4e00-\u9fff]+?)(\d+)$'
        for stock in stocks:
            match = re.match(pattern, stock)
            if match:
                names.append(match.group(1).strip())
                codes.append(match.group(2).strip())
        cutoff_date = (datetime.now() - timedelta(days=days)).date()
        with self.db_manager.get_session() as session:
            records = (
                session.query(Stock.date, Stock.stock, Stock.code, Stock.analysis)
                .filter(
                    Stock.stock.in_(names),
                    Stock.code.in_(codes),
                    Stock.date >= cutoff_date,
                )
                .all()
            )
            return [
                {
                    "date": record.date,
                    "stock": record.stock,
                    "code": record.code,
                    "analysis": record.analysis,
                }
                for record in records
            ]

    def get_today_stocks(self):
        """
        Retrieve today's stocks and their analysis.
        Returns a list of dicts: [{"stock": ..., "code": ..., "analysis": ...}, ...]
        """
        today = datetime.now().date()
        with self.db_manager.get_session() as session:
            stocks = (
                session.query(Stock)
                .filter(Stock.date == str(today))
                .all()
            )
            result = [
                {
                    "stock": s.stock,
                    "code": s.code,
                    "analysis": s.analysis,
                }
                for s in stocks
            ]
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

            session.commit()
            return count

class StockStatsRepository:
    """Repository for stock operations with safe upsert."""

    def __init__(self, db_manager):
        self.db_manager = db_manager
    
    def add_market_stats(self, market_stats: dict):
        """
        Insert or update a row in the stock_stats table for a given date. 
        If the row for date exists, update fields. If not, insert new.
        `market_stats` must include: date, up, down, even, break_rate, up_number, down_number, max_even, max_break
        """
        with self.db_manager.get_session() as session:
            date = str(market_stats.get("date"))
            existing = (
                session.query(StockStats)
                .filter(StockStats.date == date)
                .first()
            )
            if existing:
                # Update fields
                existing.up = market_stats.get("up")
                existing.down = market_stats.get("down")
                existing.even = market_stats.get("even")
                existing.break_rate = market_stats.get("break_rate")
                existing.up_number = market_stats.get("up_number")
                existing.down_number = market_stats.get("down_number")
                existing.max_even = market_stats.get("max_even")
                existing.max_break = market_stats.get("max_break")
            else:
                # Insert new
                new_stats = StockStats(
                    date = market_stats.get("date"),
                    up = market_stats.get("up"),
                    down = market_stats.get("down"),
                    even = market_stats.get("even"),
                    break_rate = market_stats.get("break_rate"),
                    up_number = market_stats.get("up_number"),
                    down_number = market_stats.get("down_number"),
                    max_even = market_stats.get("max_even"),
                    max_break = market_stats.get("max_break"),
                )
                session.add(new_stats)
            session.commit()
    
    def get_market_stats(self, days: int):
        with self.db_manager.get_session() as session:
            all_stats = (
                session.query(StockStats)
                .order_by(StockStats.date.asc())
                .limit(days)
                .all()
            )
            # 转成字典列表，避免 detached instance
            return [
                {
                    "date": s.date,
                    "up": s.up,
                    "down": s.down,
                    "even": s.even,
                    "break_rate": s.break_rate,
                    "up_number": s.up_number,
                    "down_number": s.down_number,
                    "max_even": s.max_even,
                    "max_break": s.max_break,
                }
                for s in all_stats
            ]


class NewsAnalysisRepository:
    """Repository for news analysis operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def save_analysis(self, analysis_result: str) -> NewsAnalysis:
        """
        Save analysis result.

        Converts the current datetime to compatible MySQL date and time strings.
        Ensures time is stored as a full datetime ('YYYY-MM-DD HH:MM:SS') if necessary,
        to avoid incorrect time or date format issues (e.g., see DataError 1292).
        """
        now = datetime.now()
        # Save as MySQL DATETIME to avoid DataError for TIME-only fields
        datetime_str = now.strftime("%Y-%m-%d %H:%M:%S")
        date_str = now.strftime('%Y-%m-%d')

        with self.db_manager.get_session() as session:
            analysis = NewsAnalysis(
                date=date_str,
                time=datetime_str,  # Store full datetime string
                analysis=analysis_result,
            )
            session.add(analysis)
            session.flush()
            return analysis

class UsersRepository:
    """Repository for users table operations."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def insert_or_update_user(
        self,
        user_id: str,
        source: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        password: Optional[str] = None,
    ) -> User:
        """Insert or update a user record with user_id and source as unique key."""
        with self.db_manager.get_session() as session:
            stmt = insert(User).values(
                user_id=user_id,
                email=email,
                source=source,
                phone=phone,
                password=password,
            )
            stmt = stmt.on_duplicate_key_update(
                email=stmt.inserted.email,
                phone=stmt.inserted.phone,
                password=stmt.inserted.password,
            )
            try:
                result = session.execute(stmt)
                session.commit()
            except IntegrityError as e:
                session.rollback()
                raise
            return result

    def get_user(self, user_id: str, source: str) -> Optional[Dict[str, Any]]:
        """Get a user record by user_id and source."""
        with self.db_manager.get_session() as session:
            user = (
                session.query(User)
                .filter(User.user_id == user_id, User.source == source)
                .first()
            )
            if user:
                return user.to_dict()
            return None

    def get_email_by_user_id(self, user_id: str) -> Optional[str]:
        """
        Get a valid email address for a specific user_id.
        Since email is unique, return the first valid one for the user_id.
        """
        with self.db_manager.get_session() as session:
            user = (
                session.query(User)
                .filter(User.user_id == user_id)
                .first()
            )
            if user:
                email = getattr(user, "email", None)
                if email and email.strip() and email.lower() not in {"null", "nan"}:
                    return email
            return None
    
    def get_user_id_by_email(self, email: str) -> Optional[str]:
        with self.db_manager.get_session() as session:
            user = (
                session.query(User)
                .filter(User.email == email)
                .first()
            )
            if user:
                user_id = getattr(user, "user_id", None)
                if user_id and user_id.strip() and email.lower() not in {"null", "nan"}:
                    return user_id
            return None
            
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users."""
        with self.db_manager.get_session() as session:
            users = session.query(User).all()
            return [u.to_dict() for u in users]
        
    def get_all_unique_valid_emails(self) -> List[str]:
        """
        Get all unique, valid emails from the users table.
        Filters out emails that are None, "", "null", or "NaN" (case insensitive).
        """
        with self.db_manager.get_session() as session:
            query = session.query(User.email).distinct()
            raw_emails = [row[0] for row in query if row[0] is not None]
            filtered_emails = []
            seen = set()
            for email in raw_emails:
                email_str = str(email).strip()
                # Exclude invalid
                if not email_str or email_str.lower() in {"null", "nan"}:
                    continue
                if email_str not in seen:
                    seen.add(email_str)
                    filtered_emails.append(email_str)
            return filtered_emails

