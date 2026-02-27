from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _get_database_url() -> str:
    default = "sqlite:///db/yc_bench.db"
    url = os.environ.get("DATABASE_URL", default)
    # Auto-create parent directory for sqlite:/// relative paths
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        import pathlib
        db_path = pathlib.Path(url[len("sqlite:///"):])
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return url


def _maybe_register_psycopg_enum_dumpers(url: str) -> None:
    """Register psycopg3 enum value dumpers — only needed for PostgreSQL.

    psycopg3 sends enum .name (uppercase) by default; the DB stores .value
    (lowercase). This adapter fixes that mismatch. SQLite doesn't need it
    because SQLAlchemy stores enums as plain VARCHAR using .value directly.
    """
    if not url.startswith("postgresql"):
        return

    import enum
    import psycopg
    from psycopg.adapt import Dumper
    from psycopg.pq import Format

    class _EnumValueDumper(Dumper):
        format = Format.TEXT

        def dump(self, obj):
            return obj.value.encode("utf-8") if isinstance(obj, enum.Enum) else str(obj).encode("utf-8")

    from .models.company import Domain
    from .models.event import EventType
    from .models.ledger import LedgerCategory
    from .models.task import TaskStatus

    for cls in (Domain, EventType, LedgerCategory, TaskStatus):
        psycopg.adapters.register_dumper(cls, _EnumValueDumper)


def build_engine(url: str | None = None):
    db_url = url or _get_database_url()
    _maybe_register_psycopg_enum_dumpers(db_url)

    kwargs: dict = {"echo": False, "future": True}
    if db_url.startswith("sqlite"):
        # Required for SQLAlchemy's connection pool when used across threads
        kwargs["connect_args"] = {"check_same_thread": False}

    return create_engine(db_url, **kwargs)


def build_session_factory(engine):
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )


@contextmanager
def session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine) -> None:
    """Create all tables that do not yet exist. Safe to call on every startup."""
    from .base import Base
    # Import all models so SQLAlchemy registers them with Base.metadata before create_all.
    from .models import company, employee, event, ledger, scratchpad, session, sim_state, task  # noqa: F401
    Base.metadata.create_all(engine)


__all__ = ["build_engine", "build_session_factory", "session_scope", "init_db"]
