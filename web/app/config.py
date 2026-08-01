"""Настройки приложения из переменных окружения."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Док.Москва"
    secret_key: str = "dev-only-change-me-in-production"
    db_url: str = "sqlite+pysqlite:////tmp/dok_moscow_dev.db"
    session_cookie: str = "dok_session"
    session_max_age: int = 60 * 60 * 12  # 12 часов
    csrf_cookie: str = "dok_csrf"
    invite_ttl_hours: int = 72
    login_rate_limit: int = 5
    login_rate_window_sec: int = 15 * 60
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    files_root: str = str(_REPO_ROOT / "files")
    templates_dir: str = str(_REPO_ROOT / "core" / "Шаблоны")
    gotenberg_url: str = "http://127.0.0.1:3000"
    dadata_key: str = ""
    dadata_daily_limit: int = 200
    core_path: str = str(_REPO_ROOT / "core")


@lru_cache
def get_settings() -> Settings:
    return Settings()
