"""Automatic Alembic upgrades for the local application database."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from reel_lab.config import BACKEND_ROOT, DATABASE_PATH, ensure_data_directories


def upgrade_database(database_url: str | None = None) -> None:
    """Upgrade a fresh database and adopt the pre-Alembic development schema."""
    ensure_data_directories()
    url = database_url or f"sqlite:///{DATABASE_PATH}"
    configuration = Config()
    configuration.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    configuration.set_main_option("sqlalchemy.url", url)
    inspector = inspect(create_engine(url))
    tables = set(inspector.get_table_names())
    if tables and "alembic_version" not in tables:
        command.stamp(configuration, "head")
    else:
        command.upgrade(configuration, "head")
