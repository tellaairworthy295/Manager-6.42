from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.dialects.mysql import insert, insert as mysql_insert
from sqlalchemy.exc import IntegrityError

from ..manager import DatabaseManager
from ..models import (
    ActionLimitData,
    HistoryKChart,
    RealTimeChart,
    SectionReason,
    Stock,
    StockStats,
)


class StockRepository:
    """Repository for stock operations with safe upsert."""

    def __init__(self, db_manager):
        self.db_manager = db_manager

    def get_recent_stock_name_code(self, days):
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
        names = []
        codes = []
        pattern = r"^([\w\s\*\u4e00-\u9fff]+?)(\d+)$"
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
        with self.db_manager.get_session() as session:
            stocks = session.query(Stock).filter(Stock.date == date_).all()
            return [stock.code for stock in stocks]

    def get_code_section(self, _date: date):
        with self.db_manager.get_session() as session:
            results = session.query(Stock.code, Stock.section).filter(Stock.date == _date).all()
            return [{"code": code, "section": section} for code, section in results]

    def delete_by_date(self, date_: date) -> int:
        with self.db_manager.get_session() as session:
            result = (
                session.query(Stock)
                .filter_by(date=date_)
                .delete(synchronize_session=False)
            )
            session.commit()
            return result

    def insert_or_update_stocks(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0

        with self.db_manager.get_session() as session:
            count = 0
            updatable_fields = [
                "section",
                "stock",
                "last_price",
                "change_rate",
                "turnover",
                "market_capital",
                "up_time",
                "analysis",
            ]

            for record in records:
                insert_values = {"date": record["date"], "code": record["code"]}
                for field in updatable_fields:
                    insert_values[field] = record.get(field)

                stmt = insert(Stock).values(**insert_values)
                ondup_kwargs = {}

                if record.get("analysis") is not None:
                    ondup_kwargs["analysis"] = case(
                        (
                            func.locate(stmt.inserted.analysis, func.ifnull(Stock.analysis, "")) == 0,
                            func.trim(func.concat_ws("\n", Stock.analysis, stmt.inserted.analysis)),
                        ),
                        else_=Stock.analysis,
                    )

                if record.get("section") is not None:
                    ondup_kwargs["section"] = case(
                        (
                            func.find_in_set(stmt.inserted.section, Stock.section) == 0,
                            func.concat_ws(",", Stock.section, stmt.inserted.section),
                        ),
                        else_=Stock.section,
                    )

                for field in ["last_price", "change_rate", "turnover", "market_capital", "up_time"]:
                    if record.get(field) is not None:
                        ondup_kwargs[field] = getattr(stmt.inserted, field)

                if not ondup_kwargs:
                    try:
                        session.execute(stmt)
                        count += 1
                    except IntegrityError as exc:
                        raise RuntimeError(f"error when inserting into stocks: {exc}") from exc
                    continue

                try:
                    stmt = stmt.on_duplicate_key_update(**ondup_kwargs)
                    session.execute(stmt)
                    count += 1
                except IntegrityError as exc:
                    raise RuntimeError(f"error when inserting into stocks: {exc}") from exc

            session.commit()
            return count


class StockStatsRepository:
    """Repository for stock_stats operations with safe upsert."""

    def __init__(self, db_manager):
        self.db_manager = db_manager

    def add_market_stats(self, market_stats: dict):
        with self.db_manager.get_session() as session:
            stats_date = market_stats.get("date")
            if isinstance(stats_date, str):
                stats_date = datetime.strptime(stats_date, "%Y-%m-%d").date()

            existing = session.query(StockStats).filter(StockStats.date == stats_date).first()
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
                session.add(
                    StockStats(
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
                )
            session.commit()

    def get_market_stats(self, days: int):
        with self.db_manager.get_session() as session:
            all_stats = (
                session.query(StockStats)
                .order_by(StockStats.date.desc())
                .limit(days)
                .all()[::-1]
            )
            return [
                {
                    "date": stats.date.isoformat() if isinstance(stats.date, date) else stats.date,
                    "up_limit": stats.up_limit,
                    "down_limit": stats.down_limit,
                    "up_limit_st": stats.up_limit_st,
                    "down_limit_st": stats.down_limit_st,
                    "even": stats.even,
                    "break_rate": stats.break_rate,
                    "up_fluctuation": stats.up_fluctuation,
                    "down_fluctuation": stats.down_fluctuation,
                    "max_even": stats.max_even,
                    "max_break": stats.max_break,
                }
                for stats in all_stats
            ]


class SectionReasonRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def upsert_section_reason(self, date_: date, records: list[dict]):
        if not records:
            return

        with self.db_manager.get_session() as session:
            values = [
                {
                    "date": date_,
                    "section": record["section"],
                    "change_rate": record.get("change_rate"),
                    "reason": record["reason"],
                }
                for record in records
            ]

            stmt = mysql_insert(SectionReason).values(values)
            stmt = stmt.on_duplicate_key_update(
                reason=case(
                    (
                        (stmt.inserted.reason != None) & (stmt.inserted.reason != ""),
                        case(
                            (
                                ~func.find_in_set(stmt.inserted.reason, func.ifnull(SectionReason.reason, "")),
                                func.trim(func.concat_ws("\n", SectionReason.reason, stmt.inserted.reason)),
                            ),
                            else_=SectionReason.reason,
                        ),
                    ),
                    else_=SectionReason.reason,
                ),
                change_rate=case(
                    (stmt.inserted.change_rate != None, stmt.inserted.change_rate),
                    else_=SectionReason.change_rate,
                ),
                scraped_at=func.now(),
            )

            session.execute(stmt)
            session.commit()

    def delete_by_date(self, date_: date) -> int:
        with self.db_manager.get_session() as session:
            result = (
                session.query(SectionReason)
                .filter_by(date=date_)
                .delete(synchronize_session=False)
            )
            session.commit()
            return result


class ActionLimitDataRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def insert_action_data_batch(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        required_fields = {"date", "code"}
        for row in rows:
            missing = required_fields - row.keys()
            if missing:
                raise ValueError(f"missing required fields {missing}, row={row}")

        with self.db_manager.get_session() as session:
            session.execute(mysql_insert(ActionLimitData), rows)
            session.commit()

        return len(rows)

    def update_action_data_batch(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        with self.db_manager.get_session() as session:
            for row in rows:
                if "date" not in row or "code" not in row:
                    raise ValueError(f"update requires 'date' and 'code': {row}")

                where = {"date": row["date"], "code": row["code"]}
                updates = {key: value for key, value in row.items() if key not in ("date", "code")}
                if not updates:
                    continue

                if "analysis" in updates:
                    updates["analysis"] = case(
                        (
                            func.locate(updates["analysis"], func.ifnull(ActionLimitData.analysis, "")) == 0,
                            func.trim(func.concat_ws("\n", ActionLimitData.analysis, updates["analysis"])),
                        ),
                        else_=ActionLimitData.analysis,
                    )

                if "section" in updates:
                    updates["section"] = case(
                        (
                            func.find_in_set(updates["section"], ActionLimitData.section) == 0,
                            func.concat_ws(",", ActionLimitData.section, updates["section"]),
                        ),
                        else_=ActionLimitData.section,
                    )

                if "market_capital" in updates:
                    updates["market_capital"] = case(
                        (ActionLimitData.market_capital == 0, updates["market_capital"]),
                        else_=ActionLimitData.market_capital,
                    )

                session.query(ActionLimitData).filter_by(**where).update(
                    updates,
                    synchronize_session=False,
                )

            session.commit()

        return len(rows)

    def get_by_date(self, date_: date) -> list[ActionLimitData]:
        with self.db_manager.get_session() as session:
            return (
                session.query(ActionLimitData)
                .filter_by(date=date_)
                .order_by(ActionLimitData.section, ActionLimitData.code)
                .all()
            )

    def get_by_section_and_date(self, section: str, date_: date) -> list[ActionLimitData]:
        with self.db_manager.get_session() as session:
            return (
                session.query(ActionLimitData)
                .filter_by(section=section, date=date_)
                .order_by(ActionLimitData.code)
                .all()
            )

    def delete_by_date(self, date_: date) -> int:
        with self.db_manager.get_session() as session:
            result = (
                session.query(ActionLimitData)
                .filter_by(date=date_)
                .delete(synchronize_session=False)
            )
            session.commit()
            return result


class HistoryKChartRepository:
    """Repository for HistoryKChart operations."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def save_kcharts(self, kcharts: list[dict]):
        if not kcharts:
            return 0

        with self.db_manager.get_session() as session:
            session.execute(mysql_insert(HistoryKChart), kcharts)
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

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def save_realtime_charts(self, charts: list[dict]):
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
