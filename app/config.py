"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(_env("DATA_DIR", "./data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.admin_password = _env("ADMIN_PASSWORD")
        # A missing SECRET_KEY still works but logs everyone out on each restart.
        self.secret_key = _env("SECRET_KEY") or secrets.token_hex(32)
        self.secret_key_is_ephemeral = not _env("SECRET_KEY")
        self.cookie_secure = _env("COOKIE_SECURE", "auto")  # auto | true | false

        db_url = _env("DATABASE_URL")
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        if db_url.startswith("postgresql://"):
            db_url = "postgresql+psycopg://" + db_url[len("postgresql://"):]
        self.database_url = db_url or f"sqlite:///{self.data_dir / 'scriptorium.db'}"

        self.gemini_api_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
        self.gemini_model = _env("GEMINI_MODEL", "gemini-3.6-flash")
        # Gemini 3.x uses thinking_level; 2.5 models use thinking_budget. Either may be blank.
        self.gemini_thinking_level = _env("GEMINI_THINKING_LEVEL", "low")
        self.gemini_thinking_budget = _env("GEMINI_THINKING_BUDGET", "")
        self.gemini_media_resolution = _env("GEMINI_MEDIA_RESOLUTION", "MEDIA_RESOLUTION_HIGH")
        self.gemini_max_side = _int("GEMINI_MAX_IMAGE_SIDE", 3072)

        self.ocr_concurrency = max(1, _int("OCR_CONCURRENCY", 3))
        self.hybrid_conf_threshold = _float("HYBRID_CONF_THRESHOLD", 80.0)
        self.low_conf_word = _float("LOW_CONF_WORD", 60.0)
        self.render_dpi = _int("RENDER_DPI", 300)
        self.page_image_format = _env("PAGE_IMAGE_FORMAT", "jpeg").lower()  # jpeg | png
        self.jpeg_quality = _int("JPEG_QUALITY", 92)
        self.max_upload_mb = _int("MAX_UPLOAD_MB", 1500)
        self.storage_limit_gb = _float("STORAGE_LIMIT_GB", 0.0)  # 0 = use disk capacity

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key)


settings = Settings()
