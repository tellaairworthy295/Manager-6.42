from __future__ import annotations

from typing import Optional

from ..manager import DatabaseManager
from ..models import User


def _is_valid_scalar(value) -> bool:
    if value is None:
        return False
    value_str = str(value).strip()
    return bool(value_str) and value_str.lower() not in {"null", "nan"}


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
        with self.db_manager.get_session() as session:
            existing_user = (
                session.query(User)
                .filter(User.user_id == user_id, User.source == source)
                .first()
            )

            if existing_user:
                if email is not None:
                    existing_user.email = email
                if phone is not None:
                    existing_user.phone = phone
                if password is not None:
                    existing_user.password = password
            else:
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
            except Exception:
                session.rollback()
                raise

    def get_user(self, user_id: str, source: str) -> dict | None:
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
        with self.db_manager.get_session() as session:
            user = session.query(User).filter(User.user_id == user_id).first()
            if user and _is_valid_scalar(user.email):
                return user.email
            return None

    def get_user_id_by_email(self, email: str) -> Optional[str]:
        with self.db_manager.get_session() as session:
            user = session.query(User).filter(User.email == email).first()
            if user and _is_valid_scalar(user.user_id):
                return user.user_id
            return None

    def get_all_users(self) -> list[dict]:
        with self.db_manager.get_session() as session:
            return [user.to_dict() for user in session.query(User).all()]

    def get_all_unique_valid_emails(self) -> list[str]:
        with self.db_manager.get_session() as session:
            raw_emails = [row[0] for row in session.query(User.email).distinct() if row[0] is not None]
            filtered_emails = []
            seen = set()
            for email in raw_emails:
                email_str = str(email).strip()
                if not _is_valid_scalar(email_str):
                    continue
                if email_str not in seen:
                    seen.add(email_str)
                    filtered_emails.append(email_str)
            return filtered_emails
