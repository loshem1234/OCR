"""Database models and session handling (Postgres on Railway, SQLite locally)."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


DEFAULT_LAYOUT: dict[str, Any] = {"main": None, "notes": None, "mode": "single", "split": 0.5}

DEFAULT_DOC_SETTINGS: dict[str, Any] = {
    "languages": ["eng"],
    "engine": "auto",            # auto | tesseract | gemini | embedded
    "footnote_mode": "drop",     # drop | separate
    "preserve_refs": False,      # keep Stephanus/Bekker/line refs as inline markers (Gemini)
    "use_text_layer": False,     # use an existing PDF text layer when present
    "hint": "",                  # free-text note passed to Gemini (e.g. "Teubner edition of Proclus")
}


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(500), default="Untitled")
    author: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="ingesting")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    source_files: Mapped[list] = mapped_column(JSON, default=list)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    ingest_done: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    images_purged: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_DOC_SETTINGS))
    layouts: Mapped[dict] = mapped_column(
        JSON, default=lambda: {"global": dict(DEFAULT_LAYOUT), "odd": None, "even": None, "use_odd_even": False}
    )
    export_options: Mapped[dict] = mapped_column(JSON, default=dict)

    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.index"
    )


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(16), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)  # 0-based
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=False)

    skip: Mapped[bool] = mapped_column(Boolean, default=False)
    override: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

    status: Mapped[str] = mapped_column(String(12), default="new", index=True)  # new|queued|running|done|error
    forced_engine: Mapped[str] = mapped_column(String(12), default="")
    engine: Mapped[str] = mapped_column(String(12), default="")
    route_reason: Mapped[str] = mapped_column(String(200), default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    right: Mapped[str] = mapped_column(Text, default="")   # facing-text right column
    notes: Mapped[str] = mapped_column(Text, default="")   # footnotes / apparatus
    low_conf: Mapped[list] = mapped_column(JSON, default=list)
    printed_page: Mapped[str] = mapped_column(String(40), default="")
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    warning: Mapped[str] = mapped_column(Text, default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document: Mapped[Document] = relationship(back_populates="pages")


_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
