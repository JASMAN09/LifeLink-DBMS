"""Application configuration loaded from environment variables."""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _stable_secret_key():
    configured = (os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY") or "").strip()
    if configured and not configured.lower().startswith(("replace-", "change-me", "your-")):
        return configured
    # Local development must keep signing cookies stable across Flask restarts.
    # The ignored instance directory keeps this fallback out of source control.
    secret_file = BASE_DIR / "instance" / "secret_key"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = secret_file.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
    except FileNotFoundError:
        pass
    generated = secrets.token_urlsafe(48)
    try:
        with secret_file.open("x", encoding="utf-8") as stream:
            stream.write(generated)
        return generated
    except FileExistsError:
        return secret_file.read_text(encoding="utf-8").strip()


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    LOCAL_DEMO_ACCESS = APP_ENV != "production" and os.getenv("LOCAL_DEMO_ACCESS", "false").strip().lower() in {"1", "true", "yes"}
    SECRET_KEY = _stable_secret_key()
    DEBUG = APP_ENV != "production" and os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"}
    PORT = int(os.getenv("PORT", "5050"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true" if APP_ENV == "production" else "false").lower() in {"1", "true", "yes"}
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    MYSQL_HOST = os.getenv("DB_HOST", "localhost")
    MYSQL_PORT = int(os.getenv("DB_PORT", "3306"))
    MYSQL_USER = os.getenv("DB_USER", "root")
    MYSQL_PASSWORD = os.getenv("DB_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("DB_NAME", "lifelink")
    MYSQL_SSL_CA = os.getenv("DB_SSL_CA", "").strip() or None
    MYSQL_SETUP_USER = os.getenv("DB_SETUP_USER", "").strip() or MYSQL_USER
    MYSQL_SETUP_PASSWORD = os.getenv("DB_SETUP_PASSWORD", "") or MYSQL_PASSWORD
    LOW_STOCK_THRESHOLD = max(0, int(os.getenv("LOW_STOCK_THRESHOLD", "2")))

    @classmethod
    def validate_production(cls):
        """Fail closed when production is missing essential secret/database settings."""
        if cls.APP_ENV != "production":
            return
        secret_key = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY", "")
        if len(secret_key) < 32 or secret_key.lower().startswith(("replace-", "change-me", "your-")):
            raise RuntimeError("Production requires FLASK_SECRET_KEY with at least 32 characters.")
        if (not cls.MYSQL_PASSWORD or cls.MYSQL_PASSWORD.lower().startswith(("replace-", "change-me", "your-"))
                or cls.MYSQL_USER.lower() in {"root", "your_mysql_username"}):
            raise RuntimeError("Production requires a non-root MySQL account and a non-empty DB_PASSWORD.")
        if not cls.SESSION_COOKIE_SECURE:
            raise RuntimeError("Production requires COOKIE_SECURE=true so session cookies are sent only over HTTPS.")
        if cls.MYSQL_DATABASE != "lifelink":
            raise RuntimeError("DB_NAME must be 'lifelink'; schema.sql initializes that database name.")
        if not cls.MYSQL_SSL_CA or not Path(cls.MYSQL_SSL_CA).is_file():
            raise RuntimeError("Production requires DB_SSL_CA to point to the managed MySQL server CA certificate file.")
