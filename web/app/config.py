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
    # Для продакшена с HTTPS (Caddy): SESSION_HTTPS_ONLY=true
    session_https_only: bool = False
    # Опционально: общий домен cookie (например ".dok.moscow") для поддоменов
    session_cookie_domain: str = ""
    # CSRF — signed token в session (см. app.security), отдельной cookie нет (T3).
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
    # W-25: Яндекс ID OAuth (секрет только из .env, не из репозитория).
    # Redirect URI должен быть на том же хосте, что и /login (app.dok.moscow),
    # и совпадать с настройкой в консоли Яндекс OAuth.
    yandex_client_id: str = "453c829f555c4e94a1f77c16313854a9"
    yandex_client_secret: str = ""
    yandex_redirect_uri: str = "https://app.dok.moscow/auth/yandex/callback"
    # W-30: выполнять jobs в процессе web (тесты/dev без отдельного worker)
    jobs_inline: bool = False
    # Пул SQLAlchemy (PostgreSQL); для SQLite игнорируется
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # T1: create_all только по явному флагу (тесты/быстрый локальный sqlite).
    # Прод и docker: alembic upgrade head в entrypoint; DB_AUTO_CREATE не задавать.
    db_auto_create: bool = False
    # W-34: ops-agent (внутренний контейнер; порт наружу не публикуем)
    ops_agent_enabled: bool = False
    ops_agent_url: str = "http://ops-agent:9100"
    ops_agent_token: str = ""
    # Hostland (мосты без API)
    hostland_panel_url: str = "https://hostland.ru/"
    hostland_pay_url: str = "https://hostland.ru/"
    hostland_console_url: str = "https://hostland.ru/"
    # Лендинг: полоса цифр (ENV, без правки кода). docs=-1 → считать из БД.
    landing_stats_templates: int = 26
    landing_stats_docs: int = -1
    landing_stats_practice_since: int = 2014
    # W-43: глобальный флаг факсимиле (FAKSIMILE_ENABLED)
    faksimile_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
