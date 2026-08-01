"""Запись событий аудита (входы, сброс пароля и т.п.)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Event


def record_event(
    db: Session,
    *,
    type: str,
    org_id: int | None = None,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> Event:
    event = Event(
        org_id=org_id,
        user_id=user_id,
        type=type,
        details=details or {},
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    return event
