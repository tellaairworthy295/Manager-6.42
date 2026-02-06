"""
Database module with SQLAlchemy models and connection pooling for MySQL.
Provides modular, extensible, and maintainable database operations.
"""
import json
from datetime import datetime, timedelta, date, timezone, time
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from sqlalchemy import VARCHAR, Date, Float, create_engine, Column, Integer, String, Text, DateTime, Boolean, \
    UniqueConstraint, Index, DECIMAL, Time
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
    url = Column(String(500), unique=True, nullable=False, index=True)
    source = Column(String(100))
    title = Column(Text)
    content = Column(Text)
    title_zh = Column(Text)
    content_zh = Column(Text)
    xml_content = Column(Text)
    content_fully_loaded = Column(Boolean, default=False)
    publish_at = Column(DateTime, nullable=False)
    scraped_date = Column(Date, default=date.today, index=True)
    scraped_at = Column(DateTime, default=datetime.now)


class Stock(Base):
    """Model for stocks table."""
    __tablename__ = "stocks"
    __table_args__ = (
        UniqueConstraint('date', 'stock', name='uq_date_stock'),
        {
            'mysql_charset': 'utf8mb4',
            'mysql_collate': 'utf8mb4_unicode_ci'
        }
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)  # Changed from String to Date
    stock = Column(VARCHAR(64), nullable=False)
    code = Column(VARCHAR(20), nullable=False)
    section_name = Column(VARCHAR(64), nullable=False)
    analysis = Column(Text, nullable=False)
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
    __table_args__ = {
        'mysql_charset': 'utf8mb4',
        'mysql_collate': 'utf8mb4_unicode_ci'
    }
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)  # Changed from String to Date
    time = Column(DateTime)
    analysis = Column(Text)
    created_at = Column(DateTime, default=datetime.now())

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "date": self.date.isoformat() if isinstance(self.date, date) else self.date,
            "time": self.time.isoformat() if self.time else None,
            "analysis": self.analysis,
        }


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
    reason = Column(Text, nullable=True)
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
    section = Column(VARCHAR(64), nullable=False)  # 板块/主题
    board = Column(VARCHAR(32), nullable=False)  # x天x板
    code = Column(VARCHAR(32), nullable=False, index=True)  # 股票代码
    stock = Column(VARCHAR(64), nullable=False)  # 股票名称 (长度扩展)

    # 价格数据
    last_price = Column(DECIMAL(10, 4), nullable=False)  # 最新价
    change_rate = Column(VARCHAR(32), nullable=False)  # 涨跌幅

    # 技术指标
    lock_ratio = Column(DECIMAL(5, 2), nullable=False)  # 封单比 (允许空值)
    turnover = Column(DECIMAL(7, 2), nullable=False)  # 换手率 (允许空值)
    market_capital = Column(DECIMAL(20, 2), nullable=False)  # 流通市值
    total_capital = Column(DECIMAL(20, 2), nullable=False)  # 总市值

    # 时间数据
    d_time_first = Column(Time, nullable=False)  # 首次封板时间
    d_time_last = Column(Time, nullable=False)  # 最后封板时间

    # 其它信息
    analysis = Column(Text, nullable=False)
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
                stock_date = rec["date"]
                # Accept both string or date, but always convert to datetime.date for DB
                if isinstance(stock_date, str):
                    stock_date = datetime.strptime(stock_date, "%Y-%m-%d").date()

                stmt = insert(Stock).values(
                    date=stock_date,
                    section_name=rec["section_name"],
                    stock=rec["stock"],
                    code=rec.get("code"),
                    analysis=rec.get("analysis"),
                )

                # On duplicate key, merge analysis and section_name
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
                    ),

                    section_name=case(
                        (
                            stmt.inserted.section_name.isnot(None)
                            & (stmt.inserted.section_name != ''),
                            # Check if the new section_name is already present in the existing section_name
                            case(
                                (
                                    # Split current section_name into individual sections
                                    # and check if the new section is unique
                                    func.locate(
                                        func.concat(",", Stock.section_name, ","),
                                        func.concat(",", stmt.inserted.section_name, ",")
                                    ) == 0,
                                    # If not found, append the new section_name
                                    func.concat(Stock.section_name, ",", stmt.inserted.section_name)
                                ),
                                else_=Stock.section_name
                            )
                        ),
                        else_=Stock.section_name
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

    def upsert_section_reason(self, date_: date, records: dict):
        """批量插入或更新板块原因数据"""
        if not records:
            return

        with self.db_manager.get_session() as session:
            stock_date = date_

            # 准备批量数据
            values = [
                {
                    "date": stock_date,
                    "section": sec_name,
                    "reason": reason
                }
                for sec_name, reason in records.items()
            ]

            # 使用 MySQL 的 INSERT ... ON DUPLICATE KEY UPDATE
            stmt = mysql_insert(SectionReason).values(values)
            stmt = stmt.on_duplicate_key_update(
                reason=stmt.inserted.reason
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


def validate_and_format_record(data: dict) -> dict:
    """
    Validate and format a single record to match database schema.
    """
    formatted_data = data.copy()

    # Ensure decimal fields are properly formatted
    decimal_fields = ["last_price", "lock_ratio", "turnover", "market_capital", "total_capital"]
    for field in decimal_fields:
        if field in formatted_data:
            if isinstance(formatted_data[field], str):
                # Remove Chinese characters and convert
                if field in ["market_capital", "total_capital"]:
                    # Handle "94.4亿" -> 94.4
                    value = formatted_data[field].replace('亿', '').strip()
                elif field == "lock_ratio" and '%' in formatted_data[field]:
                    # Handle "1.33%" -> 1.33
                    value = formatted_data[field].replace('%', '').strip()
                elif field == "turnover" and '%' in formatted_data[field]:
                    # Handle "3.71%" -> 3.71
                    value = formatted_data[field].replace('%', '').strip()
                else:
                    value = formatted_data[field]

                try:
                    formatted_data[field] = float(value)
                except ValueError:
                    raise ValueError(f"Invalid value for {field}: {formatted_data[field]}")

    # Handle time fields
    time_fields = ["d_time_first", "d_time_last"]
    for field in time_fields:
        if field in formatted_data and formatted_data[field]:
            time_str = formatted_data[field]
            if time_str and time_str != '-':
                try:
                    # Convert "09:45:28" to time object
                    if isinstance(time_str, str):
                        h, m, s = map(int, time_str.split(':'))
                        formatted_data[field] = time(h, m, s)
                except (ValueError, AttributeError):
                    # Keep as string if parsing fails, let SQLAlchemy handle it
                    pass

    # Ensure highlight has default value
    if "highlight" not in formatted_data:
        formatted_data["highlight"] = False

    return formatted_data


class ActionLimitDataRepository:
    """
    Repository for upserting action_limit_data table.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def upsert_action_data_batch(self, data_list: List[dict]):
        """
        Upsert (insert or update) batch for action_limit_data table.
        First deletes all records for the specific date, then inserts all new records.

        Args:
            data_list: List of dictionaries, each containing all columns needed for ActionLimitData

        Returns:
            List of inserted ActionLimitData objects
        """
        if not data_list:
            return []

        # Validate all records based on new table structure
        required_fields = [
            "date", "section", "board", "code", "stock", "last_price",
            "change_rate", "lock_ratio", "turnover", "market_capital",
            "total_capital", "d_time_first", "d_time_last", "analysis", "highlight"
        ]

        validated_data_list = []
        for i, data in enumerate(data_list):
            # Check required fields
            missing_fields = [f for f in required_fields if f not in data]
            if missing_fields:
                raise ValueError(f"Record {i} missing required fields: {missing_fields}")

            # Validate field types and formats
            validated_data = validate_and_format_record(data)
            validated_data_list.append(validated_data)

        with self.db_manager.get_session() as session:
            # 使用 MySQL 的 INSERT ... ON DUPLICATE KEY UPDATE
            # 批量插入
            stmt = mysql_insert(ActionLimitData).values(validated_data_list)

            # 正确的 ON DUPLICATE KEY UPDATE 语法
            stmt = stmt.on_duplicate_key_update(
                last_price=stmt.inserted.last_price,
                change_rate=stmt.inserted.change_rate,
                lock_ratio=stmt.inserted.lock_ratio,
                turnover=stmt.inserted.turnover,
                market_capital=stmt.inserted.market_capital,
                total_capital=stmt.inserted.total_capital,
                d_time_first=stmt.inserted.d_time_first,
                d_time_last=stmt.inserted.d_time_last,
                analysis=stmt.inserted.analysis,
                highlight=stmt.inserted.highlight,
                section=stmt.inserted.section,
                board=stmt.inserted.board,
                stock=stmt.inserted.stock
            )

            session.execute(stmt)
            session.commit()

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


class NewsAnalysisRepository:
    """Repository for news analysis operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def save_analysis(self, analysis_result: str) -> NewsAnalysis:
        """
        Save analysis result.
        """
        now = datetime.now()
        today_date = now.date()
        with self.db_manager.get_session() as session:
            analysis = NewsAnalysis(
                date=today_date,  # Now a Date column
                time=now,  # 直接使用 DateTime 对象
                analysis=analysis_result,
            )
            session.add(analysis)
            session.commit()  # 或 session.flush()
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
