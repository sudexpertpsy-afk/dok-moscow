"""Настройки приложения из переменных окружения."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Первый администратор сервиса (создаётся при старте, если ещё нет)
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
