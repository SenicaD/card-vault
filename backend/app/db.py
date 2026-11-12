from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

_settings = get_settings()

if _settings.database_url.startswith("sqlite:///"):
    Path(_settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {},
)


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all never alters existing tables. Add columns introduced after a database was first created."""
    additions = {
        "pricesnapshot": {"top_listings": "JSON", "grade": "VARCHAR", "price_refs": "JSON",
                          "parallel": "VARCHAR", "spread": "FLOAT", "trimmed": "INTEGER"},
        "card": {"grade_counts": "JSON", "grade_counts_at": "DATETIME", "parallel": "VARCHAR",
                 "cost": "FLOAT", "reviewed": "BOOLEAN DEFAULT 0"},
        "listingseen": {"excluded": "BOOLEAN DEFAULT 0"},
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            if not inspect(conn).has_table(table):
                continue
            have = {c["name"] for c in inspect(conn).get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
