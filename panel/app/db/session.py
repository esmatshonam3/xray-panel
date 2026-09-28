"""SQLAlchemy engine / session wiring with SQLite and Postgres support."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _prepare_sqlite_path(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    _, _, tail = url.partition("///")
    if tail and tail != ":memory:":
        Path(tail).expanduser().parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite_path(settings.sqlalchemy_url)

_engine_kwargs: dict = {
    "echo": settings.db_echo,
    "pool_pre_ping": True,
    "future": True,
}
if settings.is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
else:
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
    )

engine: Engine = create_engine(settings.sqlalchemy_url, **_engine_kwargs)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background jobs and the Telegram bot."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(*, create_all: bool = True) -> None:
    from app.db import models  # noqa: F401  (register mappers)
    from app.db.base import Base

    if create_all:
        Base.metadata.create_all(bind=engine)
        log.info("database schema ready", extra={"url_scheme": settings.sqlalchemy_url.split("://")[0]})
