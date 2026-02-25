"""
Database module with SQLAlchemy models and connection pooling for MySQL.
Provides modular, extensible, and maintainable database operations.
"""
import json
from datetime import datetime, timedelta, date, timezone
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from sqlalchemy import TEXT, VARCHAR, Date, Float, create_engine, Column, Integer, String, DateTime, Boolean, \
    UniqueConstraint, Index, DECIMAL, Time, BigInteger
from sqlalchemy.orm import sessionmaker, Session, scoped_session
from sqlalchemy.pool import QueuePool
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, case
from sqlalchemy.dialects.mysql import insert as mysql_insert
from contextlib import contextmanager

from sqlalchemy.orm import declarative_base
import urllib

Base = declarative_base()


# ==================== Database Models ====================

class NewsArticle(Base):
    """Model for news articles table."""
    __tablename__ = "news_articles"
    __table_args__ = {
        'mysql_charset': 'utf8mb4',
        'mysql_collate': 'utf8mb4_unicode_ci'
    }
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(VARCHAR(255), unique=True, nullable=False)
    source = Column(VARCHAR(20))
    title = Column(VARCHAR(255))
    content = Column(TEXT)
    title_zh = Column(VARCHAR(255))
    content_zh = Column(TEXT)
    xml_content = Column(TEXT)
    content_fully_loaded = Column(Boolean, default=False)
    publish_at = Column(DateTime, nullable=False)
    scraped_date = Column(Date, default=date.today, index=True, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now)

class HistoryKChart(Base):
    __tablename__ = "history_k_chart"
    __table_args__ = (
        UniqueConstraint('date', 'code', name='uq_date_code'),
        {
            'mysql_charset': 'utf8mb4',
            'mysql_collate': 'utf8mb4_unicode_ci'
        }
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    code = Column(VARCHAR(20), nullable=False)
    open = Column(DECIMAL(10, 3), nullable=False)
    close = Column(DECIMAL(10, 3), nullable=False)
    high = Column(DECIMAL(10, 3), nullable=False)
    low = Column(DECIMAL(10, 3), nullable=False)
    volume = Column(BigInteger, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now())


class RealTimeChart(Base):
    __tablename__ = "real_time_chart"
    __table_args__ = (
        UniqueConstraint('data_time', 'code', name='uq_data_time_code'),
        {
            'mysql_charset': 'utf8mb4',
            'mysql_collate': 'utf8mb4_unicode_ci'
        }
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    data_time = Column(DateTime, nullable=False, index=True)
    code = Column(VARCHAR(20), nullable=False)
    close = Column(DECIMAL(10, 3), nullable=False)
    pre_close = Column(DECIMAL(10, 3), nullable=False)
    change_rate = Column(DECIMAL(9, 6), nullable=False)
    volume = Column(BigInteger, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now())


class Stock(Base):
    """Model for stocks table."""
    __tablename__ = "stocks"
    __table_args__ = (
        UniqueConstraint('date', 'code', name='uq_date_code'),
        {
            'mysql_charset': 'utf8mb4',
            'mysql_collate': 'utf8mb4_unicode_ci'
        }
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)  # Changed from String to Date
    stock = Column(VARCHAR(64), nullable=False)
    code = Column(VARCHAR(20), nullable=False)
    section = Column(VARCHAR(255), nullable=False)
    # 价格数据
    last_price = Column(DECIMAL(10, 4), nullable=False)  # 最新价
    change_rate = Column(VARCHAR(32), nullable=False)  # 涨跌幅

    # 技术指标
    turnover = Column(DECIMAL(7, 2), nullable=True)  # 换手率 (允许空值)
    market_capital = Column(DECIMAL(20, 2), nullable=True)  # 流通市值

    # 时间数据
    up_time = Column(Time, nullable=True)  # 封板时间
    analysis = Column(TEXT, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now())

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "date": self.date.isoformat() if isinstance(self.date, date) else self.date,  # Convert Date to string
            "stock": self.stock,
            "code": self.code,
            "analysis": self.analysis,
        }


class StockStats(Base):
    __tablename__ = "stock_stats"
    __table_args__ = (
            UniqueConstraint('date', name='uq_date'),
            {
                'mysql_charset': 'utf8mb4',
                'mysql_collate': 'utf8mb4_unicode_ci'
            }
        )
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    up_limit = Column(Integer, nullable=False)
    down_limit = Column(Integer, nullable=False)
    up_limit_st = Column(Integer, nullable=False)
    down_limit_st = Column(Integer, nullable=False)
    even = Column(Integer, nullable=False)
    break_rate = Column(Float, nullable=False)
    up_fluctuation = Column(Integer, nullable=False)
    down_fluctuation = Column(Integer, nullable=False)
    max_even = Column(Integer, nullable=False)
    max_break = Column(Integer, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now())


class NewsAnalysis(Base):
    """Model for news analysis table."""
    __tablename__ = "news_analysis"
    __table_args__ = (
            {
                'mysql_charset': 'utf8mb4',
                'mysql_collate': 'utf8mb4_unicode_ci'
            }
        )
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(VARCHAR(255), unique=True, nullable=False)
    stocks = Column(VARCHAR(255))
    scraped_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now())


class SectionReason(Base):
    __tablename__ = "section_reason"
    __table_args__ = (
        UniqueConstraint('date', 'section', name='uq_date_section'),
        {
            'mysql_charset': 'utf8mb4',
            'mysql_collate': 'utf8mb4_unicode_ci'
        }
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)  # 交易日
    section = Column(VARCHAR(64), nullable=False)  # 板块/主题
    change_rate = Column(VARCHAR(32), nullable=True)  # 涨跌幅
    reason = Column(TEXT, nullable=True)
    scraped_at = Column(DateTime, default=datetime.now())


class ActionLimitData(Base):
    """Model for action data table."""
    __tablename__ = "action_limit_data"
    __table_args__ = (
        UniqueConstraint('date', 'code', name='uq_date_code'),  # 更精确的复合唯一约束
        Index('idx_section_date', 'section', 'date'),
        {
            'mysql_charset': 'utf8mb4',
            'mysql_collate': 'utf8mb4_unicode_ci'
        }
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)  # 交易日
    section = Column(VARCHAR(255), nullable=False)  # 板块/主题
    board = Column(VARCHAR(32), nullable=True)  # x天x板
    code = Column(VARCHAR(32), nullable=False, index=True)  # 股票代码
    stock = Column(VARCHAR(64), nullable=False)  # 股票名称 (长度扩展)

    # 价格数据
    last_price = Column(DECIMAL(10, 4), nullable=True)  # 最新价
    change_rate = Column(VARCHAR(32), nullable=True)  # 涨跌幅

    # 技术指标
    lock_ratio = Column(DECIMAL(5, 2), nullable=True)  # 封单比
    turnover = Column(DECIMAL(7, 2), nullable=True)  # 换手率
    turnover_abs = Column(DECIMAL(7, 2), nullable=False)  # 换手率
    market_capital = Column(DECIMAL(20, 2), nullable=False)  # 流通市值
    total_capital = Column(DECIMAL(20, 2), nullable=True)  # 总市值

    # 时间数据
    d_time_first = Column(Time, nullable=True)  # 首次封板时间
    d_time_last = Column(Time, nullable=False)  # 最后封板时间

    # 其它信息
    analysis = Column(TEXT, nullable=False)
    highlight = Column(Boolean, default=False)
    scraped_at = Column(DateTime, default=datetime.now())


class User(Base):
    """Model for users table."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    email = Column(String(254), nullable=False)
    source = Column(String(100), nullable=False)
    phone = Column(String(40))
    password = Column(String(128))
    updated_at = Column(DateTime, default=datetime.now())

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
        """
        # Load config.json once at module load
        if not hasattr(self, "_config_cache"):
            PROJECT_ROOT = Path(__file__).parent.parent
            with open(PROJECT_ROOT / "json/config.json", "r", encoding="utf-8") as f:
                self._config_cache = json.load(f)
        db_config = self._config_cache.get("DatabaseConfig", {})
        db_host = db_config.get("DB_HOST", "localhost")
        db_port = db_config.get("DB_PORT", 35300)
        db_user = db_config.get("DB_USER", "root")
        db_password = db_config.get("DB_PASSWORD", "112358@gh")
        db_name = db_config.get("DB_NAME", "dify_data")

        # Create connection string
        connection_string = (
            f"mysql+pymysql://{db_user}:{urllib.parse.quote_plus(db_password)}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
        )

        # Create engine with connection pooling
        self.engine = create_engine(
            connection_string,
            poolclass=QueuePool,
            pool_size=5,  # Number of connections to maintain
            max_overflow=3,  # Maximum number of connections beyond pool_size
            pool_pre_ping=True,  # Verify connections before using
            pool_recycle=1800,  # Recycle connections after 1 hour
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
        """Insert or update a news article safely with upsert logic."""
        with self.db_manager.get_session() as session:
            if not publish_at:
                # Get current UTC time (no timezone adjustment needed)
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

            # On duplicate key (same URL), update fields
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
            except IntegrityError as e:
                session.rollback()
                raise

            return result

    def fetch_recent_articles(self, hours: int = 6) -> List[Dict[str, Any]]:
        """Fetch articles scraped within the last N hours."""
        from datetime import datetime, timedelta, date
        if hours == 0:
            cutoff_time = datetime.now() - timedelta(minutes=15)
        else:
            cutoff_time = datetime.now() - timedelta(hours=hours)

        cutoff_date = cutoff_time.date()
        today = date.today()

        with self.db_manager.get_session() as session:
            # 获取今天和昨天的数据（覆盖凌晨跨日情况）
            if cutoff_date == today:
                # 如果 cutoff_time 是今天，只需要今天的数据
                articles_query = session.query(NewsArticle).filter(
                    NewsArticle.scraped_date == today
                )
            else:
                # 需要今天和昨天的数据
                yesterday = today - timedelta(days=1)
                articles_query = session.query(NewsArticle).filter(
                    NewsArticle.scraped_date.in_([yesterday, today])
                )

            # 获取候选文章
            candidate_articles = articles_query.all()

            # 在内存中精确过滤时间戳
            recent_articles = [
                a for a in candidate_articles
                if a.scraped_at >= cutoff_time
            ]

            result = [
                {"url": a.url, "title": a.title, "content": a.content}
                for a in recent_articles
            ]
            return result

    def get_recent_urls(self, hours: int = 1) -> List[str]:
        """Get URLs that were scraped within the last N hours."""
        from datetime import datetime, timedelta, date

        cutoff_time = datetime.now() - timedelta(hours=hours)
        cutoff_date = cutoff_time.date()
        today = date.today()

        with self.db_manager.get_session() as session:
            # 获取今天和昨天的数据（覆盖凌晨跨日情况）
            if cutoff_date == today:
                # 只需要今天的数据
                articles_query = session.query(NewsArticle).filter(
                    NewsArticle.scraped_date == today
                )
            else:
                # 需要今天和昨天的数据
                yesterday = today - timedelta(days=1)
                articles_query = session.query(NewsArticle).filter(
                    NewsArticle.scraped_date.in_([yesterday, today])
                )

            # 获取所有文章
            candidate_articles = articles_query.all()

            # 在内存中过滤时间戳
            recent_articles = [
                a for a in candidate_articles
                if a.scraped_at >= cutoff_time
            ]

            return [a.url for a in recent_articles]


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
                key = stock + code
                if key not in seen:
                    results.append(key)
                    seen.add(key)
            return results

    def get_analysis_by_stock(self, stocks: list[str], days: int) -> list[dict]:
        """
        Fetch date, stock, code, analysis from database based on a list of stocks and days.
        Returns a list of dicts: [{"date": ..., "stock": ..., "code": ..., "analysis": ...}, ...]
        """
        names = []
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
                    "date": record.date.isoformat() if isinstance(record.date, date) else record.date,
                    "stock": record.stock,
                    "code": record.code,
                    "analysis": record.analysis,
                }
                for record in records
            ]

    def get_codes_by_date(self, date_: date):
        """
        Retrieve today's stocks and their analysis.
        Returns a list of dicts: [{"stock": ..., "code": ..., "analysis": ...}, ...]
        """
        with self.db_manager.get_session() as session:
            stocks = (
                session.query(Stock)
                .filter(Stock.date == date_)
                .all()
            )
            result = [
                s.code
                for s in stocks
            ]
            return result

    def get_code_section(self, _date: date):
        """
        Select code and section for the given date.
        Returns a list of dicts: [{"code": ..., "section": ...}, ...]
        """
        with self.db_manager.get_session() as session:
            results = (
                session.query(Stock.code, Stock.section)
                .filter(Stock.date == _date)
                .all()
            )
            return [
                {
                    "code": code,
                    "section": section
                }
                for code, section in results
            ]

    def insert_or_update_stocks(self, _date: date, records: List[Dict[str, Any]]) -> int:
        """
        Insert or update multiple stock records.

        - Insert all column values from records (all at once on insert).
        - If a record exists (same date + stock), update only columns where new values are not None.
        - Merge only *new* analysis text if analysis is provided and not already present.
        - On conflict, append the new section only if it does not already exist
        in the comma-separated list of existing sections.
        """
        if not records:
            return 0

        with self.db_manager.get_session() as session:
            count = 0
            # Gather all column names except id and scraped_at (let scraped_at default/update separately)
            updatable_fields = [
                "section", "stock", "code", "last_price", "change_rate",
                "turnover", "market_capital", "up_time", "analysis"
            ]
            for rec in records:
                # Prepare insertion values (default to None when missing)
                insert_values = {
                    "date": _date,
                }
                for field in updatable_fields:
                    insert_values[field] = rec.get(field)

                stmt = insert(Stock).values(**insert_values)

                # Prepare ON DUPLICATE KEY UPDATE conditional logic
                ondup_kwargs = {}

                # Always merge analysis text if a new non-None analysis is present
                if rec.get("analysis") is not None:
                    ondup_kwargs["analysis"] = case(
                        (
                            func.locate(
                                stmt.inserted.analysis,
                                func.ifnull(Stock.analysis, "")
                            ) == 0,
                            func.trim(
                                func.concat_ws("\n", Stock.analysis, stmt.inserted.analysis)
                            )
                        ),
                        else_=Stock.analysis,
                    )

                # Always merge section only if new section is present and not already in set
                if rec.get("section") is not None:
                    ondup_kwargs["section"] = case(
                        (
                            func.find_in_set(stmt.inserted.section, Stock.section) == 0,
                            func.concat_ws(",", Stock.section, stmt.inserted.section)
                        ),
                        else_=Stock.section
                    )

                # All other fields: update only if value is not None in the record
                # Avoid use of _proxies; use getattr(stmt.inserted, field)
                for field in [
                    "last_price", "change_rate", "turnover", "market_capital", "up_time"
                ]:
                    if rec.get(field) is not None:
                        ondup_kwargs[field] = getattr(stmt.inserted, field)

                if not ondup_kwargs:
                    # If only inserting, and nothing to update, just do insert
                    try:
                        session.execute(stmt)
                        count += 1
                    except IntegrityError as e:
                        raise RuntimeError(f"error when inserting into stocks: {e}")
                    continue

                try:
                    stmt = stmt.on_duplicate_key_update(**ondup_kwargs)
                    session.execute(stmt)
                    count += 1
                except IntegrityError as e:
                    raise RuntimeError(f"error when inserting into stocks: {e}")

            session.commit()
            return count


class StockStatsRepository:
    """Repository for stock_stats operations with safe upsert."""

    def __init__(self, db_manager):
        self.db_manager = db_manager

    def add_market_stats(self, market_stats: dict):
        """
        Insert or update a row in the stock_stats table for a given date.
        If the row for date exists, update fields. If not, insert new.
        `market_stats` must include: date, up_limit, down_limit, up_limit_st,
        down_limit_st, even, break_rate, up_fluctuation, down_fluctuation, max_even, max_break
        """
        with self.db_manager.get_session() as session:
            stats_date = market_stats.get("date")
            if isinstance(stats_date, str):
                stats_date = datetime.strptime(stats_date, "%Y-%m-%d").date()
            existing = (
                session.query(StockStats)
                .filter(StockStats.date == stats_date)
                .first()
            )
            if existing:
                existing.up_limit = market_stats.get("up_limit")
                existing.down_limit = market_stats.get("down_limit")
                existing.up_limit_st = market_stats.get("up_limit_st")
                existing.down_limit_st = market_stats.get("down_limit_st")
                existing.even = market_stats.get("even")
                existing.break_rate = market_stats.get("break_rate")
                existing.up_fluctuation = market_stats.get("up_fluctuation")
                existing.down_fluctuation = market_stats.get("down_fluctuation")
                existing.max_even = market_stats.get("max_even")
                existing.max_break = market_stats.get("max_break")
                existing.scraped_at = datetime.now()
            else:
                new_stats = StockStats(
                    date=stats_date,
                    up_limit=market_stats.get("up_limit"),
                    down_limit=market_stats.get("down_limit"),
                    up_limit_st=market_stats.get("up_limit_st"),
                    down_limit_st=market_stats.get("down_limit_st"),
                    even=market_stats.get("even"),
                    break_rate=market_stats.get("break_rate"),
                    up_fluctuation=market_stats.get("up_fluctuation"),
                    down_fluctuation=market_stats.get("down_fluctuation"),
                    max_even=market_stats.get("max_even"),
                    max_break=market_stats.get("max_break"),
                )
                session.add(new_stats)
            session.commit()

    def get_market_stats(self, days: int):
        with self.db_manager.get_session() as session:
            all_stats = (
                session.query(StockStats)
                .order_by(StockStats.date.desc())
                .limit(days)
                .all()[::-1]
            )
            # 转成字典列表，避免 detached instance
            return [
                {
                    "date": s.date.isoformat() if isinstance(s.date, date) else s.date,
                    "up_limit": s.up_limit,
                    "down_limit": s.down_limit,
                    "up_limit_st": s.up_limit_st,
                    "down_limit_st": s.down_limit_st,
                    "even": s.even,
                    "break_rate": s.break_rate,
                    "up_fluctuation": s.up_fluctuation,
                    "down_fluctuation": s.down_fluctuation,
                    "max_even": s.max_even,
                    "max_break": s.max_break,
                }
                for s in all_stats
            ]


class SectionReasonRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def upsert_section_reason(self, date_: date, records: list[dict]):
        """批量插入或更新板块原因数据（新内容只追加不重复，change_rate如有非None才更新）"""
        if not records:
            return

        with self.db_manager.get_session() as session:
            stock_date = date_

            # 准备批量数据
            values = [
                {
                    "date": stock_date,
                    "section": rec["section"],
                    "change_rate": rec.get("change_rate"),
                    "reason": rec["reason"],
                }
                for rec in records
            ]

            stmt = mysql_insert(SectionReason).values(values)

            # ON DUPLICATE KEY UPDATE for reason (append if new & non-empty) and change_rate (update if not None from inserted)
            # 这里的stmt.inserted.change_rate是插入数据，只有在不为None时才更新
            stmt = stmt.on_duplicate_key_update(
                reason=case(
                    (
                        (stmt.inserted.reason != None) & (stmt.inserted.reason != ''),
                        case(
                            (
                                ~func.find_in_set(
                                    stmt.inserted.reason, func.ifnull(SectionReason.reason, "")
                                ),
                                func.trim(
                                    func.concat_ws("\n", SectionReason.reason, stmt.inserted.reason)
                                ),
                            ),
                            else_=SectionReason.reason,
                        )
                    ),
                    else_=SectionReason.reason,
                ),
                change_rate=case(
                    (stmt.inserted.change_rate != None, stmt.inserted.change_rate),
                    else_=SectionReason.change_rate
                ),
                scraped_at=func.now()  # update scraped_at as current datetime
            )

            session.execute(stmt)
            session.commit()

    def delete_by_date(self, date_: date) -> int:
        """
        Delete all records for a specific date.
        Returns number of deleted records.
        """
        with self.db_manager.get_session() as session:
            result = session.query(SectionReason) \
                .filter_by(date=date_) \
                .delete(synchronize_session=False)
            session.commit()
            return result


class ActionLimitDataRepository:
    """
    Repository for upserting action_limit_data table.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def insert_action_data_batch(self, rows: list[dict]) -> int:
        """
        Insert full ActionLimitData rows.
        All NOT NULL columns MUST be present.
        """
        if not rows:
            return 0

        required_fields = {"date", "code"}

        for row in rows:
            missing = required_fields - row.keys()
            if missing:
                raise ValueError(f"missing required fields {missing}, row={row}")

        with self.db_manager.get_session() as session:
            session.execute(
                mysql_insert(ActionLimitData),
                rows,
            )
            session.commit()

        return len(rows)

    def update_action_data_batch(self, rows: list[dict]) -> int:
        """
        Partial UPDATE for existing rows.
        Unique key: (date, code)
        """
        if not rows:
            return 0

        with self.db_manager.get_session() as session:
            for row in rows:
                if "date" not in row or "code" not in row:
                    raise ValueError(
                        f"update requires 'date' and 'code': {row}"
                    )

                where = {
                    "date": row["date"],
                    "code": row["code"],
                }

                updates = {
                    k: v for k, v in row.items()
                    if k not in ("date", "code")
                }

                if not updates:
                    continue

                # ---- analysis: append if new ----
                if "analysis" in updates:
                    updates["analysis"] = case(
                        (
                            func.locate(
                                updates["analysis"],
                                func.ifnull(ActionLimitData.analysis, "")
                            ) == 0,
                            func.trim(
                                func.concat_ws(
                                    "\n",
                                    ActionLimitData.analysis,
                                    updates["analysis"]
                                )
                            ),
                        ),
                        else_=ActionLimitData.analysis,
                    )

                # ---- section: append CSV if not present ----
                if "section" in updates:
                    updates["section"] = case(
                        (
                            func.find_in_set(
                                updates["section"],
                                ActionLimitData.section
                            ) == 0,
                            func.concat_ws(
                                ",",
                                ActionLimitData.section,
                                updates["section"]
                            ),
                        ),
                        else_=ActionLimitData.section,
                    )

                # ---- market_capital: update only if existing is 0 ----
                if "market_capital" in updates:
                    updates["market_capital"] = case(
                        (
                            ActionLimitData.market_capital == 0,
                            updates["market_capital"],
                        ),
                        else_=ActionLimitData.market_capital,
                    )

                session.query(ActionLimitData) \
                    .filter_by(**where) \
                    .update(updates, synchronize_session=False)

            session.commit()

        return len(rows)

    def get_by_date(self, date_: date) -> List[ActionLimitData]:
        """
        Get all records for a specific date.
        """
        with self.db_manager.get_session() as session:
            return session.query(ActionLimitData) \
                .filter_by(date=date_) \
                .order_by(ActionLimitData.section, ActionLimitData.code) \
                .all()

    def get_by_section_and_date(self, section: str, date_: date) -> List[ActionLimitData]:
        """
        Get records by section and date.
        """
        with self.db_manager.get_session() as session:
            return session.query(ActionLimitData) \
                .filter_by(section=section, date=date_) \
                .order_by(ActionLimitData.code) \
                .all()

    def delete_by_date(self, date_: date) -> int:
        """
        Delete all records for a specific date.
        Returns number of deleted records.
        """
        with self.db_manager.get_session() as session:
            result = session.query(ActionLimitData) \
                .filter_by(date=date_) \
                .delete(synchronize_session=False)
            session.commit()
            return result

class HistoryKChartRepository:
    """Repository for HistoryKChart operations."""

    def __init__(self, db_manager: 'DatabaseManager'):
        self.db_manager = db_manager

    def save_kcharts(self, kcharts: list[dict]):
        if not kcharts:
            return 0

        with self.db_manager.get_session() as session:
            session.execute(
                mysql_insert(HistoryKChart),
                kcharts,
            )
            session.commit()

        return len(kcharts)

    def delete_kchart_by_code(self, code: str):
        with self.db_manager.get_session() as session:
            result = (
                session.query(HistoryKChart)
                .filter_by(code=code)
                .delete(synchronize_session=False)
            )
            session.commit()
            return result


class RealTimeChartRepository:
    """Repository for RealTimeChart operations."""

    def __init__(self, db_manager: 'DatabaseManager'):
        self.db_manager = db_manager

    def save_realtime_charts(
            self,
            charts: list[dict],
    ):
        if not charts:
            return 0

        with self.db_manager.get_session() as session:
            stmt = mysql_insert(RealTimeChart)
            session.execute(stmt, charts)
            session.commit()

        return len(charts)

    def delete_rt_by_date(self, date_: date):
        with self.db_manager.get_session() as session:
            result = (
                session.query(RealTimeChart)
                .filter_by(date=date_)
                .delete(synchronize_session=False)
            )
            session.commit()
            return result


class NewsAnalysisRepository:
    """Repository for news analysis operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def save_analysis(self, data: dict) -> NewsAnalysis | None:
        """
        Save analysis result.
        """
        if not data:
            return

        with self.db_manager.get_session() as session:
            analysis = NewsAnalysis(
                url=data['url'],
                scraped_date=data['scraped_date'],
                stocks=data['stocks'],
            )
            session.add(analysis)
            session.commit()  # or session.flush()
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
            # First check if user exists
            existing_user = session.query(User).filter(
                User.user_id == user_id,
                User.source == source
            ).first()

            if existing_user:
                # Update existing record
                if email is not None:
                    existing_user.email = email
                if phone is not None:
                    existing_user.phone = phone
                if password is not None:
                    existing_user.password = password
            else:
                # Create new user
                existing_user = User(
                    user_id=user_id,
                    email=email or "nan",
                    source=source,
                    phone=phone,
                    password=password,
                )
                session.add(existing_user)

            try:
                session.commit()
                session.refresh(existing_user)
                return existing_user
            except Exception as e:
                session.rollback()
                raise

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
