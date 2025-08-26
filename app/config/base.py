# app/config/base.py
from __future__ import annotations
from pathlib import Path
from typing import Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

class BaseConfig(BaseSettings):
    # ===== Core =====
    DATABASE_URL: str = f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"
    SECRET_KEY: str = "change-me"
    ADMIN_PASSWORD: str = ""
    SITE_TITLE: str = "Dich Foto"
    ENV: str = "base"  # تُغيّر في local/server

    # ===== Upload service =====
    UPLOAD_BASE_URL: str = "https://upload.dichfoto.com"

    # ===== CORS =====
    CORS_ALLOW_ORIGINS: List[str] = []

    # ===== Local storage =====
    STORAGE_DIR: Path = BASE_DIR / "storage"
    THUMBS_DIR: Path = STORAGE_DIR / "_thumbs"
    THUMB_MAX_WIDTH: int = 800

    # ===== Image / thumbnail options =====
    FORCE_JPEG: bool = True
    ENABLE_WEBP: bool = True
    ENABLE_AVIF: bool = False

    # ===== Google Drive =====
    USE_GDRIVE: bool = False
    GDRIVE_ROOT_FOLDER_ID: Optional[str] = None
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None

    # ===== Video Providers (NEW) =====
    # Vimeo
    VIMEO_ACCESS_TOKEN: Optional[str] = None

    # Cloudflare Stream
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = None
    CLOUDFLARE_API_TOKEN: Optional[str] = None
    # (اختياري) مفتاح توقيع لروابط التشغيل الموقّعة
    CLOUDFLARE_STREAM_SIGNING_KEY: Optional[str] = None

    # YouTube (للجلب بالـ API فقط – غير مطلوب للـ embed)
    YOUTUBE_API_KEY: Optional[str] = None

    # قصر عرض المشغّل على نطاقات معيّنة (قوائم سماح)
    VIDEO_DOMAIN_ALLOWLIST: List[str] = ["dichfoto.com", "upload.dichfoto.com", "www.dichfoto.com"]

    # Pydantic v2
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )
