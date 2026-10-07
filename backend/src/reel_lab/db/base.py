"""SQLite engine and session helpers."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from reel_lab.config import DATABASE_PATH, ensure_data_directories


class Base(DeclarativeBase):
    """Declarative base shared by all database models."""


ensure_data_directories()
engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _configure_sqlite(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_schema() -> None:
    """Create the local schema for a fresh installation."""
    from reel_lab.db import models

    assert models.User.__tablename__ == "users"
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Generator[Session]:
    """Provide a short transaction for workers and startup tasks."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
