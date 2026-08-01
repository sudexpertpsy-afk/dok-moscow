"""Фильтрация запросов по org_id текущего пользователя."""

from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.models import (
    Contract,
    Counter,
    Counterparty,
    Document,
    Event,
    Invite,
    Organization,
)

T = TypeVar("T")


def require_org_id(user: CurrentUser) -> int:
    if user.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет организации",
        )
    return user.org_id


def scoped(stmt: Select[tuple[T]], model, org_id: int) -> Select[tuple[T]]:
    """Добавить WHERE model.org_id = org_id (обязательно для данных организации)."""
    if not hasattr(model, "org_id"):
        raise TypeError(f"{model} не содержит org_id")
    return stmt.where(model.org_id == org_id)


def get_org_for_user(db: Session, user: CurrentUser) -> Organization:
    org_id = require_org_id(user)
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    return org


def get_counterparty_for_org(db: Session, org_id: int, counterparty_id: int) -> Counterparty:
    row = db.scalar(
        select(Counterparty).where(
            Counterparty.id == counterparty_id,
            Counterparty.org_id == org_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return row


def get_contract_for_org(db: Session, org_id: int, contract_id: int) -> Contract:
    row = db.scalar(
        select(Contract).where(Contract.id == contract_id, Contract.org_id == org_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return row


def get_document_for_org(db: Session, org_id: int, document_id: int) -> Document:
    row = db.scalar(
        select(Document).where(Document.id == document_id, Document.org_id == org_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return row


def list_counterparties(db: Session, org_id: int) -> list[Counterparty]:
    return list(
        db.scalars(
            select(Counterparty)
            .where(Counterparty.org_id == org_id)
            .order_by(Counterparty.id.desc())
        ).all()
    )


def list_counterparty_options(db: Session, org_id: int, limit: int = 300) -> list[Counterparty]:
    """Лёгкий список для фильтров (журнал) — без полной картотеки на каждый запрос сверх лимита."""
    return list(
        db.scalars(
            select(Counterparty)
            .where(Counterparty.org_id == org_id)
            .order_by(Counterparty.id.desc())
            .limit(limit)
        ).all()
    )


def list_contracts(db: Session, org_id: int) -> list[Contract]:
    return list(
        db.scalars(
            select(Contract).where(Contract.org_id == org_id).order_by(Contract.id.desc())
        ).all()
    )


def list_documents(db: Session, org_id: int) -> list[Document]:
    return list(
        db.scalars(
            select(Document).where(Document.org_id == org_id).order_by(Document.id.desc())
        ).all()
    )


def list_events(db: Session, org_id: int, limit: int = 100) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(Event.org_id == org_id)
            .order_by(Event.ts.desc())
            .limit(limit)
        ).all()
    )


def list_invites(db: Session, org_id: int) -> list[Invite]:
    return list(
        db.scalars(select(Invite).where(Invite.org_id == org_id).order_by(Invite.id.desc())).all()
    )


def get_counter(db: Session, org_id: int, key: str) -> Counter | None:
    return db.get(Counter, {"org_id": org_id, "key": key})
