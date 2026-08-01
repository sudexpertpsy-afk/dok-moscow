"""W-16: модель НПА, реестр, одна опубликованная редакция на акт."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import ActVersion, ActVersionStatus, LegalAct, LegalActMode
from app.services.legal_registry import (
    INITIAL_REGISTRY,
    create_draft_version,
    ensure_legal_registry,
    publish_version,
    published_version,
)


def test_registry_seeded_on_startup(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        count = db.scalar(select(func.count()).select_from(LegalAct))
        assert count == len(INITIAL_REGISTRY)
        slugs = set(db.scalars(select(LegalAct.slug)).all())
        assert "73-fz-sudebno-ekspertnaya-deyatelnost" in slugs
        assert "gpk-ekspertiza" in slugs
        assert "gost-r-57344-2016" in slugs
        gpk = db.scalar(select(LegalAct).where(LegalAct.slug == "gpk-ekspertiza"))
        assert gpk is not None
        assert gpk.mode == LegalActMode.fragments
        assert "ст. 79" in gpk.tracked_articles
        assert len(gpk.fragments) == len(gpk.tracked_articles)
    finally:
        db.close()


def test_ensure_legal_registry_idempotent(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        first = ensure_legal_registry(db)
        db.commit()
        second = ensure_legal_registry(db)
        db.commit()
        assert len(first) == len(second) == len(INITIAL_REGISTRY)
        assert db.scalar(select(func.count()).select_from(LegalAct)) == len(INITIAL_REGISTRY)
    finally:
        db.close()


def test_one_published_version_per_act(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost"))
        assert act is not None
        v1 = create_draft_version(
            db,
            act_id=act.id,
            body_html="<p>редакция 1</p>",
            revision_date=date(2020, 1, 1),
            change_basis="исходная",
        )
        publish_version(db, v1)
        db.commit()

        assert published_version(db, act.id).id == v1.id
        assert v1.status == ActVersionStatus.published

        v2 = create_draft_version(
            db,
            act_id=act.id,
            body_html="<p>редакция 2</p>",
            revision_date=date(2024, 6, 1),
            change_basis="ФЗ-тест",
        )
        publish_version(db, v2)
        db.commit()

        db.refresh(v1)
        db.refresh(v2)
        assert v1.status == ActVersionStatus.archived
        assert v2.status == ActVersionStatus.published
        pubs = db.scalars(
            select(ActVersion).where(
                ActVersion.act_id == act.id,
                ActVersion.status == ActVersionStatus.published,
            )
        ).all()
        assert len(pubs) == 1
        assert pubs[0].id == v2.id
        assert act.last_verified_at is not None
    finally:
        db.close()


def test_partial_unique_blocks_two_published(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "plenum-vs-28-2010"))
        assert act is not None
        a = create_draft_version(db, act_id=act.id, body_html="<p>a</p>")
        publish_version(db, a)
        db.commit()

        b = ActVersion(
            act_id=act.id,
            body_html="<p>b</p>",
            status=ActVersionStatus.published,
        )
        db.add(b)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()


def test_drafts_not_counted_as_published(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "mvd-511-2005"))
        create_draft_version(db, act_id=act.id, body_html="<p>черновик</p>")
        create_draft_version(db, act_id=act.id, body_html="<p>ещё черновик</p>")
        db.commit()
        assert published_version(db, act.id) is None
        drafts = db.scalars(
            select(ActVersion).where(
                ActVersion.act_id == act.id,
                ActVersion.status == ActVersionStatus.draft,
            )
        ).all()
        assert len(drafts) == 2
    finally:
        db.close()
