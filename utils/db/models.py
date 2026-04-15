from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    TEXT,
    VARCHAR,
    BigInteger,
    Boolean,
    Column,
    DECIMAL,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
)

from .base import Base, mysql_table_args


class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = mysql_table_args()

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
    __table_args__ = mysql_table_args(UniqueConstraint("date", "code", name="uq_date_code"))

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    code = Column(VARCHAR(20), nullable=False)
    open = Column(DECIMAL(10, 3), nullable=False)
    close = Column(DECIMAL(10, 3), nullable=False)
    high = Column(DECIMAL(10, 3), nullable=False)
    low = Column(DECIMAL(10, 3), nullable=False)
    volume = Column(BigInteger, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now)


class RealTimeChart(Base):
    __tablename__ = "real_time_chart"
    __table_args__ = mysql_table_args(UniqueConstraint("data_time", "code", name="uq_data_time_code"))

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    data_time = Column(DateTime, nullable=False, index=True)
    code = Column(VARCHAR(20), nullable=False)
    close = Column(DECIMAL(10, 3), nullable=False)
    pre_close = Column(DECIMAL(10, 3), nullable=False)
    change_rate = Column(DECIMAL(9, 6), nullable=False)
    volume = Column(BigInteger, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now)


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = mysql_table_args(UniqueConstraint("date", "code", name="uq_date_code"))

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    stock = Column(VARCHAR(64), nullable=False)
    code = Column(VARCHAR(20), nullable=False)
    section = Column(VARCHAR(255), nullable=False)
    last_price = Column(DECIMAL(10, 4), nullable=False)
    change_rate = Column(VARCHAR(32), nullable=False)
    turnover = Column(DECIMAL(7, 2), nullable=True)
    market_capital = Column(DECIMAL(20, 2), nullable=True)
    up_time = Column(Time, nullable=True)
    analysis = Column(TEXT, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat() if isinstance(self.date, date) else self.date,
            "stock": self.stock,
            "code": self.code,
            "analysis": self.analysis,
        }


class StockStats(Base):
    __tablename__ = "stock_stats"
    __table_args__ = mysql_table_args(UniqueConstraint("date", name="uq_date"))

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
    scraped_at = Column(DateTime, default=datetime.now)


class NewsAnalysis(Base):
    __tablename__ = "news_analysis"
    __table_args__ = mysql_table_args()

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(VARCHAR(255), unique=True, nullable=False)
    stocks = Column(VARCHAR(255))
    scraped_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now)


class SectionReason(Base):
    __tablename__ = "section_reason"
    __table_args__ = mysql_table_args(UniqueConstraint("date", "section", name="uq_date_section"))

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    section = Column(VARCHAR(64), nullable=False)
    change_rate = Column(VARCHAR(32), nullable=True)
    reason = Column(TEXT, nullable=True)
    scraped_at = Column(DateTime, default=datetime.now)


class ActionLimitData(Base):
    __tablename__ = "action_limit_data"
    __table_args__ = mysql_table_args(
        UniqueConstraint("date", "code", name="uq_date_code"),
        Index("idx_section_date", "section", "date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    section = Column(VARCHAR(255), nullable=False)
    board = Column(VARCHAR(32), nullable=True)
    code = Column(VARCHAR(32), nullable=False, index=True)
    stock = Column(VARCHAR(64), nullable=False)
    last_price = Column(DECIMAL(10, 4), nullable=True)
    change_rate = Column(VARCHAR(32), nullable=True)
    lock_ratio = Column(DECIMAL(5, 2), nullable=True)
    turnover = Column(DECIMAL(7, 2), nullable=True)
    turnover_abs = Column(DECIMAL(7, 2), nullable=False)
    market_capital = Column(DECIMAL(20, 2), nullable=False)
    total_capital = Column(DECIMAL(20, 2), nullable=True)
    d_time_first = Column(Time, nullable=True)
    d_time_last = Column(Time, nullable=False)
    analysis = Column(TEXT, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now)


class RecordComment(Base):
    __tablename__ = "record_comment"
    __table_args__ = mysql_table_args(UniqueConstraint("date", "title", name="uq_date_title"))

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    title = Column(VARCHAR(255), nullable=False)
    author = Column(VARCHAR(255), nullable=False)
    team = Column(VARCHAR(255), nullable=True)
    comment_time = Column(DateTime, nullable=False)
    industry = Column(VARCHAR(128), nullable=True)
    category = Column(VARCHAR(255), nullable=True)
    comment = Column(TEXT, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now)


class RecordMeeting(Base):
    __tablename__ = "record_meeting"
    __table_args__ = mysql_table_args(UniqueConstraint("date", "title", name="uq_date_title"))

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    title = Column(VARCHAR(255), nullable=False)
    institution = Column(VARCHAR(128), nullable=True)
    sector = Column(VARCHAR(255), nullable=True)
    stock_name = Column(VARCHAR(255), nullable=True)
    meeting_time = Column(DateTime, nullable=True)
    host_personnel = Column(VARCHAR(128), nullable=True)
    guest_speaker = Column(VARCHAR(255), nullable=True)
    summary = Column(TEXT, nullable=True)
    QA = Column(TEXT, nullable=True)
    scraped_at = Column(DateTime, default=datetime.now)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("user_id", "source", name="uq_userid_source"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    email = Column(String(254), nullable=False)
    source = Column(String(100), nullable=False)
    phone = Column(String(40))
    password = Column(String(128))
    updated_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "source": self.source,
            "phone": self.phone,
            "password": self.password,
        }
