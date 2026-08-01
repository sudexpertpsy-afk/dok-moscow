"""Настройки приложения из переменных окружения."""

from datetime import date
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
    # Лендинг / заявки (W-08)
    public_base_url: str = "https://dok.moscow"
    app_base_url: str = "https://app.dok.moscow"
    yandex_metrika_id: str = ""
    admin_notify_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    lead_rate_limit: int = 5
    lead_rate_window_sec: int = 60 * 60
    password_reset_ttl_hours: int = 2
    # True — backup.sh завершится ошибкой без AGE_RECIPIENT (прод)
    backup_require_age: bool = False
    # W-10: окончание бета-подписки «Специалист» для существующих организаций
    beta_trial_until: date = date(2026, 10, 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
