"""Database models and session handling (Postgres on Railway, SQLite locally)."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event, inspect, text,
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
    "profile": "",               # "" = automatic | "scholarly_greek" | "standard"
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

    # --- added for the scholarly Greek profile (all optional; old rows read as "prose"/empty) ---
    text_format: Mapped[str] = mapped_column(String(10), default="prose", server_default="prose")  # prose | lines
    profile: Mapped[str] = mapped_column(String(24), default="", server_default="")
    witness_status: Mapped[str] = mapped_column(String(24), default="", server_default="")
    # column named collation_data: COLLATION is a reserved word in PostgreSQL
    collation: Mapped[dict | None] = mapped_column("collation_data", JSON, nullable=True, default=None)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    image_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

    document: Mapped[Document] = relationship(back_populates="pages")
    recognitions: Mapped[list["Recognition"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", passive_deletes=True, order_by="Recognition.id")
    revisions: Mapped[list["PageRevision"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", passive_deletes=True, order_by="PageRevision.id")


class Recognition(Base):
    """One engine's untouched reading of one region of a page (a 'witness'). Never overwritten."""
    __tablename__ = "recognitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(Integer, ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    engine: Mapped[str] = mapped_column(String(16))           # tesseract | gemini | embedded | reconcile
    region: Mapped[str] = mapped_column(String(12), default="main")  # main | left | right | notes
    status: Mapped[str] = mapped_column(String(16), default="ok")    # ok | failed | unavailable | invalid | skipped
    model: Mapped[str] = mapped_column(String(80), default="")
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw: Mapped[str] = mapped_column(Text, default="")        # exact engine output (JSON text / TSV), pre-normalization
    lines: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{n, text, bbox?, words?, ...}]
    text: Mapped[str] = mapped_column(Text, default="")       # NFC lines joined with \n
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)

    page: Mapped[Page] = relationship(back_populates="recognitions")


class PageRevision(Base):
    """Snapshot of a page's text taken before anything overwrites it (e.g. an OCR re-run)."""
    __tablename__ = "page_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(Integer, ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str] = mapped_column(String(40), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    right: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    text_format: Mapped[str] = mapped_column(String(10), default="prose")
    printed_page: Mapped[str] = mapped_column(String(40), default="")
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    engine: Mapped[str] = mapped_column(String(12), default="")
    witness_status: Mapped[str] = mapped_column(String(24), default="")
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    page: Mapped[Page] = relationship(back_populates="revisions")


_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    **({} if _is_sqlite else {"pool_size": 10, "max_overflow": 30, "pool_timeout": 20, "pool_recycle": 1800}),
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
    """Create missing tables, then add any missing columns to existing tables (additive only).

    Rollback is safe: older code ignores the extra columns and tables. Nothing is ever dropped or renamed.
    """
    Base.metadata.create_all(engine)
    migrate_columns()


def migrate_columns(eng=None) -> list[str]:
    eng = eng or engine
    added = []
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl_type = col.type.compile(dialect=eng.dialect)
                default = ""
                if col.server_default is not None:
                    default = f" DEFAULT '{col.server_default.arg}'"
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl_type}{default}'))
                added.append(f"{table.name}.{col.name}")
    return added


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
