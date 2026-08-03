"""Регрессия: каталог документов не падает без PYTHONPATH=/app/core."""

from __future__ import annotations

import sys
from pathlib import Path

from app.defaults import empty_requisites
from app.models import Organization, User, UserRole
from app.security import hash_password
from app.services.templates import list_templates_for_org, templates_grouped
from conftest import login


def _seed(dbmod, email="corepath@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="CorePathOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        db.add(user)
        db.commit()
        return org.id
    finally:
        db.close()


def _strip_core_from_path() -> None:
    """Имитация uvicorn с PYTHONPATH=/app без /app/core."""
    cleaned: list[str] = []
    for p in sys.path:
        try:
            resolved = Path(p).resolve()
        except OSError:
            cleaned.append(p)
            continue
        # только каталог ядра (/workspace/core, /app/core), не site-packages
        if resolved.name == "core" and resolved.parent.name in {"workspace", "app", ""}:
            continue
        if str(resolved).endswith("/app/core") or str(resolved).endswith("/workspace/core"):
            continue
        cleaned.append(p)
    sys.path[:] = cleaned
    for key in list(sys.modules):
        if key == "docfiller_core" or key.startswith("docfiller_core."):
            del sys.modules[key]


def test_list_templates_for_org_without_core_on_sys_path(app):
    """Без ensure_core_on_path импорт template_manifest давал 500 на /cabinet/documents/."""
    client, dbmod = app
    org_id = _seed(dbmod)
    _strip_core_from_path()
    items = list_templates_for_org(org_id)
    assert len(items) >= 1
    groups = templates_grouped(items)
    assert groups
    assert login(client, "corepath@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/documents/")
    assert r.status_code == 200, r.text[:800]
