from __future__ import annotations

from collections.abc import Generator
from contextlib import suppress
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.core.config import settings


class Base(DeclarativeBase):
    pass


def _build_connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False, "timeout": 30}
    if database_url.startswith("postgresql"):
        return {"connect_timeout": 5}
    return {}


engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    future=True,
    connect_args=_build_connect_args(settings.database_url),
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=40,
    pool_timeout=10,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        with suppress(Exception):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()


def init_database() -> None:
    from backend.models import saas  # noqa: F401

    if settings.database_url.startswith("sqlite:///"):
        sqlite_path = settings.database_url.removeprefix("sqlite:///")
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    # UNSPECIFIED: keep runtime bootstrap until Alembic is made the only production schema path.
    Base.metadata.create_all(bind=engine)
    _ensure_runtime_schema()


def _ensure_runtime_schema() -> None:
    dialect_name = str(engine.dialect.name or "").strip().lower()
    timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"
    schema_patches: dict[str, list[tuple[str, str]]] = {
        "portfolio_target_execution_runs": [
            ("working_count", "INTEGER DEFAULT 0"),
            ("partial_fill_count", "INTEGER DEFAULT 0"),
            ("filled_count", "INTEGER DEFAULT 0"),
            ("canceled_count", "INTEGER DEFAULT 0"),
            ("rejected_count", "INTEGER DEFAULT 0"),
            ("orphan_event_count", "INTEGER DEFAULT 0"),
            ("last_sync_at", timestamp_type),
        ],
        "portfolio_target_execution_items": [
            ("broker_order_id", "VARCHAR(120)"),
            ("broker_status", "VARCHAR(32)"),
            ("filled_quantity", "FLOAT DEFAULT 0"),
            ("remaining_quantity", "FLOAT DEFAULT 0"),
            ("average_fill_price", "FLOAT"),
            ("reconciliation_status", "VARCHAR(32)"),
            ("terminal_at", timestamp_type),
            ("last_seen_at", timestamp_type),
        ],
    }

    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for table_name, columns in schema_patches.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in columns:
                if column_name in existing_columns:
                    continue
                connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
