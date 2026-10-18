"""Single admin password, signed session cookie."""
from __future__ import annotations

import hmac
import time

from fastapi import HTTPException, Request

from .config import settings

_failures: dict[str, list[float]] = {}


def is_authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_auth(request: Request) -> None:
    if not is_authed(request):
        raise HTTPException(status_code=401, detail="Not signed in")


def check_password(request: Request, password: str) -> bool:
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < 900]
    if len(recent) >= 10:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="ADMIN_PASSWORD is not configured on the server.")
    ok = hmac.compare_digest(password.encode(), settings.admin_password.encode())
    if not ok:
        recent.append(now)
        _failures[ip] = recent
        time.sleep(0.8)
    else:
        _failures.pop(ip, None)
    return ok
