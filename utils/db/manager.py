from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.pool import QueuePool

from .base import Base
from .config import build_connection_string


class DatabaseManager:
    """Manage database connections and scoped SQLAlchemy sessions."""

    def __init__(self, db_name: str = None):
        self.db_name = db_name
        self.engine = None
        self.session_factory = None
        self._initialize_connection()

    def _initialize_connection(self):
        self.engine = create_engine(
            build_connection_string(self.db_name),
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=3,
            pool_pre_ping=True,
            pool_recycle=1800,
            echo=False,
        )
        self.session_factory = scoped_session(
            sessionmaker(
                bind=self.engine,
                autocommit=False,
                autoflush=False,
            )
        )

    @contextmanager
    def get_session(self) -> Session:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_tables(self):
        Base.metadata.create_all(self.engine)

    def close(self):
        self.session_factory.remove()
        self.engine.dispose()


_db_manager: dict[str, DatabaseManager] = {}


def get_db_manager(db_name:str = None) -> DatabaseManager:
    global _db_manager
    if db_name not in _db_manager:
        _db_manager[db_name] = DatabaseManager(db_name)
    return _db_manager[db_name]

