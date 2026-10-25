"""In-process background worker. Page state lives in the database, so jobs resume after a restart."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from .config import settings
from .db import Document, Page, session_scope
from .ingest import ingest_document
from .ocr.router import run_page

log = logging.getLogger("worker")


class Worker:
    def __init__(self) -> None:
        self.ocr_pool = ThreadPoolExecutor(max_workers=settings.ocr_concurrency, thread_name_prefix="ocr")
        self.ingest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
        self._inflight: set[int] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle
    @staticmethod
    def requeue_interrupted() -> tuple[int, list[str]]:
        """Anything that was mid-flight when the process died goes back in the queue."""
        with session_scope() as s:
            n = 0
            for p in s.scalars(select(Page).where(Page.status == "running")):
                p.status = "queued"
                n += 1
            ingesting = [d.id for d in s.scalars(select(Document).where(Document.status == "ingesting"))]
        return n, ingesting

    def start(self) -> None:
        if self._stop.is_set():  # restarted within the same process (tests, reloads): fresh pools
            self.ocr_pool = ThreadPoolExecutor(max_workers=settings.ocr_concurrency, thread_name_prefix="ocr")
            self.ingest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
            self._stop = threading.Event()
            self._inflight = set()
        _n, ingesting = self.requeue_interrupted()
        for doc_id in ingesting:
            self.submit_ingest(doc_id)
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log.info("worker started with %d OCR threads", settings.ocr_concurrency)

    def stop(self) -> None:
        self._stop.set()
        self.ocr_pool.shutdown(wait=False, cancel_futures=True)
        self.ingest_pool.shutdown(wait=False, cancel_futures=True)

    def submit_ingest(self, doc_id: str) -> None:
        self.ingest_pool.submit(ingest_document, doc_id)

    # ---- scheduling
    def _claim(self) -> int | None:
        with session_scope() as s:
            q = (
                select(Page)
                .join(Document, Document.id == Page.doc_id)
                .where(Page.status == "queued", Document.status == "processing")
                .order_by(Page.id)
                .limit(1)
            )
            page = s.scalars(q).first()
            if page is None:
                return None
            page.status = "running"
            return page.id

    def _finalize_docs(self) -> None:
        with session_scope() as s:
            for doc in s.scalars(select(Document).where(Document.status == "processing")):
                pending = s.scalar(
                    select(func.count()).select_from(Page)
                    .where(Page.doc_id == doc.id, Page.status.in_(("queued", "running")))
                )
                if not pending:
                    doc.status = "review"

    def _run(self, page_id: int) -> None:
        try:
            run_page(page_id)
        except Exception:
            log.exception("unexpected error on page %s", page_id)
        finally:
            with self._lock:
                self._inflight.discard(page_id)

    def _loop(self) -> None:
        last_finalize = 0.0
        while not self._stop.is_set():
            claimed = False
            try:
                with self._lock:
                    room = len(self._inflight) < settings.ocr_concurrency
                if room:
                    pid = self._claim()
                    if pid is not None:
                        with self._lock:
                            self._inflight.add(pid)
                        self.ocr_pool.submit(self._run, pid)
                        claimed = True
                if time.monotonic() - last_finalize > 2:
                    self._finalize_docs()
                    last_finalize = time.monotonic()
            except Exception:
                log.exception("scheduler loop error")
            if not claimed:
                self._stop.wait(0.5)


worker = Worker()
