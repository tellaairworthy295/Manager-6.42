from __future__ import annotations

from datetime import date

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError

from ..manager import DatabaseManager
from ..models import RecordComment, RecordMeeting


class RecordCommentRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def insert_or_update_comment(self, data: dict) -> RecordComment | None:
        with self.db_manager.get_session() as session:
            try:
                comment = RecordComment(
                    date=data["date"],
                    author=data["author"],
                    team=data["team"],
                    comment_time=data["comment_time"],
                    industry=data["industry"],
                    category=data["category"],
                    comment=data["comment"],
                    title=data["title"],
                    scraped_at=data["scraped_at"],
                )
                session.add(comment)
                session.commit()
                return comment
            except IntegrityError:
                session.rollback()
                print(
                    f"Conflict found for comment with title '{data['title']}' "
                    f"on date '{data['date']}'. Skipping."
                )
                return None

    def insert_or_update_comments_batch(self, data_list: list[dict]):
        if not data_list:
            return

        with self.db_manager.get_session() as session:
            stmt = mysql_insert(RecordComment).values(data_list)
            upsert_stmt = stmt.on_duplicate_key_update(
                author=stmt.inserted.author,
                team=stmt.inserted.team,
                comment_time=stmt.inserted.comment_time,
                industry=stmt.inserted.industry,
                category=stmt.inserted.category,
                comment=stmt.inserted.comment,
                scraped_at=stmt.inserted.scraped_at,
            )
            session.execute(upsert_stmt)
            session.commit()
            print(f"Batch upsert completed for {len(data_list)} records.")

    def get_comment_by_date(self, date_: date) -> list[RecordComment]:
        with self.db_manager.get_session() as session:
            return (
                session.query(RecordComment)
                .filter_by(date=date_)
                .order_by(RecordComment.title)
                .all()
            )

    def get_all_titles_today(self) -> list[str]:
        with self.db_manager.get_session() as session:
            results = (
                session.query(RecordComment.title)
                .filter_by(date=date.today())
                .distinct()
                .all()
            )
            return [row[0] for row in results]

    def get_titles_in_range(self, start_date: date, end_date: date) -> list[str]:
        with self.db_manager.get_session() as session:
            results = (
                session.query(RecordComment.title)
                .filter(RecordComment.date >= start_date)
                .filter(RecordComment.date <= end_date)
                .distinct()
                .all()
            )
            return [row[0] for row in results]

    def delete_comment_by_date(self, date_: date) -> int:
        with self.db_manager.get_session() as session:
            result = (
                session.query(RecordComment)
                .filter_by(date=date_)
                .delete(synchronize_session=False)
            )
            session.commit()
            return result


class RecordMeetingRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def insert_or_update_meeting(self, data: dict) -> RecordMeeting | None:
        with self.db_manager.get_session() as session:
            try:
                meeting = RecordMeeting(
                    date=data["date"],
                    title=data["title"],
                    institution=data.get("institution"),
                    sector=data.get("sector"),
                    stock_name=data.get("stock_name"),
                    meeting_time=data.get("meeting_time"),
                    host_personnel=data.get("host_personnel"),
                    guest_speaker=data.get("guest_speaker"),
                    summary=data["summary"],
                    QA=data.get("QA"),
                    scraped_at=data.get("scraped_at"),
                )
                session.add(meeting)
                session.commit()
                return meeting
            except IntegrityError:
                session.rollback()
                print(
                    f"Conflict found for meeting with title '{data['title']}' "
                    f"on date '{data['date']}'. Skipping."
                )
                return None

    def insert_or_update_meetings_batch(self, data_list: list[dict]):
        if not data_list:
            return

        with self.db_manager.get_session() as session:
            prepared_data_list = []
            for data in data_list:
                prepared_data_list.append(
                    {
                        "date": data["date"],
                        "title": data["title"],
                        "institution": data.get("institution"),
                        "sector": data.get("sector"),
                        "stock_name": data.get("stock_name"),
                        "meeting_time": data.get("meeting_time"),
                        "host_personnel": data.get("host_personnel"),
                        "guest_speaker": data.get("guest_speaker"),
                        "summary": data["summary"],
                        "QA": data.get("QA"),
                        "scraped_at": data.get("scraped_at"),
                    }
                )

            stmt = mysql_insert(RecordMeeting).values(prepared_data_list)
            upsert_stmt = stmt.on_duplicate_key_update(
                institution=stmt.inserted.institution,
                sector=stmt.inserted.sector,
                stock_name=stmt.inserted.stock_name,
                meeting_time=stmt.inserted.meeting_time,
                host_personnel=stmt.inserted.host_personnel,
                guest_speaker=stmt.inserted.guest_speaker,
                summary=stmt.inserted.summary,
                QA=stmt.inserted.QA,
                scraped_at=stmt.inserted.scraped_at,
            )
            session.execute(upsert_stmt)
            session.commit()
            print(f"Batch upsert completed for {len(data_list)} records.")

    def get_meeting_by_date(self, date_: date) -> list[RecordMeeting]:
        with self.db_manager.get_session() as session:
            return (
                session.query(RecordMeeting)
                .filter_by(date=date_)
                .order_by(RecordMeeting.title)
                .all()
            )

    def get_all_titles_today(self) -> list[str]:
        with self.db_manager.get_session() as session:
            results = (
                session.query(RecordMeeting.title)
                .filter_by(date=date.today())
                .distinct()
                .all()
            )
            return [row[0] for row in results]

    def get_titles_in_range(self, start_date: date, end_date: date) -> list[str]:
        with self.db_manager.get_session() as session:
            results = (
                session.query(RecordMeeting.title)
                .filter(RecordMeeting.date >= start_date)
                .filter(RecordMeeting.date <= end_date)
                .distinct()
                .all()
            )
            return [row[0] for row in results]

    def delete_meeting_by_date(self, date_: date) -> int:
        with self.db_manager.get_session() as session:
            result = (
                session.query(RecordMeeting)
                .filter_by(date=date_)
                .delete(synchronize_session=False)
            )
            session.commit()
            return result
