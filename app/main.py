"""Scriptorium OCR — FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api import router as api_router
from .auth import is_authed
from .config import settings
from .db import init_db
from .worker import worker

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
for noisy in ("weasyprint", "fontTools", "httpx", "google_genai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if not settings.admin_password:
        log.warning("ADMIN_PASSWORD is not set — nobody will be able to sign in.")
    if settings.secret_key_is_ephemeral:
        log.warning("SECRET_KEY is not set — sessions will reset on every restart.")
    if not settings.gemini_enabled:
        log.warning("GEMINI_API_KEY is not set — running Tesseract only.")
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Scriptorium OCR", lifespan=lifespan, docs_url=None, redoc_url=None)

_secure = settings.cookie_secure == "true" or (
    settings.cookie_secure == "auto" and bool(os.environ.get("RAILWAY_ENVIRONMENT"))
)
app.add_middleware(
    SessionMiddleware, secret_key=settings.secret_key, session_cookie="scriptorium_session",
    max_age=60 * 60 * 24 * 30, same_site="lax", https_only=_secure,
)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


def _page(name: str, request: Request):
    if not is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index(request: Request):
    return _page("index.html", request)


@app.get("/doc/{doc_id}", include_in_schema=False)
def doc_page(doc_id: str, request: Request):
    return _page("doc.html", request)


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if is_authed(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    log.exception("unhandled error", exc_info=exc)
    return JSONResponse({"detail": f"Server error: {exc}"}, status_code=500)
