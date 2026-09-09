"""W-49 B: настройки нумерации и онбординг-чеклист организации."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import Counter, Counterparty, Document, Organization
from app.services.audit import record_event
from app.services.numbering import (
    COUNTER_CATALOG,
    DEFAULT_TEMPLATE,
    CounterView,
    NumberingError,
    catalog_label,
    render_number,
    validate_template,
)
from app.services.settings_svc import ensure_requisites


def _onboarding_dict(org: Organization) -> dict[str, Any]:
    raw = org.onboarding if isinstance(org.onboarding, dict) else {}
    return dict(raw)


def save_onboarding(org: Organization, data: dict[str, Any]) -> None:
    org.onboarding = data
    flag_modified(org, "onboarding")


def list_counter_views(db: Session, org_id: int) -> list[CounterView]:
    existing = {
        c.key: c
        for c in db.scalars(select(Counter).where(Counter.org_id == org_id)).all()
    }
    keys = list(dict.fromkeys([k for k, _ in COUNTER_CATALOG] + sorted(existing.keys())))
    out: list[CounterView] = []
    for key in keys:
        c = existing.get(key)
        if c is None:
            tmpl = DEFAULT_TEMPLATE
            preview = render_number(tmpl, n=1, prefix="", suffix="")
            out.append(
                CounterView(
                    key=key,
                    label=catalog_label(key),
                    prefix="",
                    suffix="",
                    value=0,
                    template=tmpl,
                    reset_yearly=False,
                    preview=preview,
                    exists=False,
                )
            )
            continue
        tmpl = c.number_template or DEFAULT_TEMPLATE
        nxt = int(c.value) + 1
        preview = render_number(
            c.number_template,
            n=nxt,
            prefix=c.prefix or "",
            suffix=c.suffix or "",
        )
        out.append(
            CounterView(
                key=c.key,
                label=catalog_label(c.key),
                prefix=c.prefix or "",
                suffix=c.suffix or "",
                value=int(c.value),
                template=tmpl,
                reset_yearly=bool(c.reset_yearly),
                preview=preview,
                exists=True,
            )
        )
    return out


def update_counter_numbering(
    db: Session,
    org_id: int,
    key: str,
    *,
    prefix: str,
    suffix: str,
    start_from: int,
    template: str,
    reset_yearly: bool,
    user_id: int | None,
) -> Counter:
    """Сохранить настройки. start_from — следующий выдаваемый n (≥ value+1)."""
    key_n = (key or "").strip()
    if not key_n or len(key_n) > 64:
        raise NumberingError("Некорректный ключ счётчика.")
    if start_from < 1:
        raise NumberingError("«Начать с» должно быть ≥ 1.")
    tmpl = validate_template(template)
    # DEFAULT_TEMPLATE храним как NULL → байт-в-байт legacy.
    store_tmpl = None if tmpl == DEFAULT_TEMPLATE else tmpl

    counter = db.get(Counter, {"org_id": org_id, "key": key_n})
    if counter is None:
        counter = Counter(
            org_id=org_id,
            key=key_n,
            prefix=prefix or "",
            suffix=suffix or "",
            value=0,
            number_template=store_tmpl,
            reset_yearly=bool(reset_yearly),
            cycle_year=None,
        )
        db.add(counter)
        db.flush()

    current_max = int(counter.value)
    # start_from = следующий номер → value = start_from - 1
    new_value = int(start_from) - 1
    if new_value < current_max:
        raise NumberingError(
            f"Нельзя откатить нумерацию ниже уже выданного максимума "
            f"({current_max}). Следующий номер должен быть ≥ {current_max + 1}."
        )

    counter.prefix = prefix or ""
    counter.suffix = suffix or ""
    counter.value = new_value
    counter.number_template = store_tmpl
    counter.reset_yearly = bool(reset_yearly)
    if counter.reset_yearly and counter.cycle_year is None:
        counter.cycle_year = datetime.now(timezone.utc).date().year
    db.flush()
    record_event(
        db,
        type="numbering.updated",
        org_id=org_id,
        user_id=user_id,
        details={"key": key_n, "start_from": start_from, "reset_yearly": bool(reset_yearly)},
        commit=False,
    )
    return counter


def confirm_numbering_defaults(db: Session, org: Organization, user_id: int | None) -> None:
    data = _onboarding_dict(org)
    data["numbering_confirmed"] = True
    data["numbering_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    save_onboarding(org, data)
    record_event(
        db,
        type="onboarding.numbering_confirmed",
        org_id=org.id,
        user_id=user_id,
        details={},
        commit=False,
    )


def dismiss_onboarding(org: Organization) -> None:
    data = _onboarding_dict(org)
    data["dismissed"] = True
    save_onboarding(org, data)


@dataclass
class OnboardingStep:
    key: str
    title: str
    url: str
    done: bool
    hint: str = ""


@dataclass
class OnboardingChecklist:
    visible: bool
    steps: list[OnboardingStep]
    completed: bool
    can_dismiss: bool


def _requisites_done(org: Organization) -> bool:
    req = ensure_requisites(org)
    block = req.get("организация") or {}
    name = (block.get("короткое_название") or block.get("полное_название") or "").strip()
    inn = "".join(ch for ch in str(block.get("инн") or "") if ch.isdigit())
    return bool(name) and len(inn) in (10, 12)


def build_onboarding_checklist(
    db: Session,
    org: Organization,
    *,
    is_paid: bool = False,
) -> OnboardingChecklist:
    data = _onboarding_dict(org)
    if data.get("dismissed"):
        return OnboardingChecklist(visible=False, steps=[], completed=True, can_dismiss=True)

    cp_n = int(
        db.scalar(
            select(func.count())
            .select_from(Counterparty)
            .where(Counterparty.org_id == org.id)
        )
        or 0
    )
    doc_n = int(
        db.scalar(
            select(func.count()).select_from(Document).where(Document.org_id == org.id)
        )
        or 0
    )
    numbering_done = bool(data.get("numbering_confirmed")) or bool(
        db.scalar(select(Counter.org_id).where(Counter.org_id == org.id).limit(1))
    )

    steps = [
        OnboardingStep(
            key="requisites",
            title="Реквизиты организации",
            url="/cabinet/settings/",
            done=_requisites_done(org),
            hint="ИНН и название",
        ),
        OnboardingStep(
            key="numbering",
            title="Нумерация документов",
            url="/cabinet/settings/numbering",
            done=numbering_done,
            hint="Шаблон номера или «оставить как есть»",
        ),
        OnboardingStep(
            key="counterparties",
            title="Контрагенты",
            url="/cabinet/counterparties/",
            done=cp_n >= 1,
            hint="Добавить или импортировать",
        ),
        OnboardingStep(
            key="first_doc",
            title="Первый комплект",
            url="/cabinet/package/",
            done=doc_n >= 1,
            hint="Договор — счёт — акт",
        ),
    ]
    if is_paid:
        from app.services.facsimile import org_has_any_branding

        steps.append(
            OnboardingStep(
                key="branding",
                title="Печать и подпись",
                url="/cabinet/settings/branding",
                done=org_has_any_branding(org.id),
                hint="Для PDF с факсимиле",
            )
        )

    completed = all(s.done for s in steps)
    # Скрыть после полного завершения; dismiss с шага 2 (нумерация done или ≥2 шага)
    done_n = sum(1 for s in steps if s.done)
    can_dismiss = done_n >= 2 or numbering_done
    visible = not completed
    return OnboardingChecklist(
        visible=visible,
        steps=steps,
        completed=completed,
        can_dismiss=can_dismiss,
    )


def onboarding_admin_metrics(db: Session) -> dict[str, Any]:
    """Медиана «регистрация → первый документ» и доля дошедших до шага 4."""
    orgs = list(db.scalars(select(Organization)).all())
    if not orgs:
        return {
            "orgs_total": 0,
            "reached_first_doc": 0,
            "reached_first_doc_share": 0.0,
            "median_minutes_to_first_doc": None,
        }
    first_docs = dict(
        db.execute(
            select(Document.org_id, func.min(Document.created_at)).group_by(Document.org_id)
        ).all()
    )
    deltas_sec: list[float] = []
    reached = 0
    for org in orgs:
        first = first_docs.get(org.id)
        if first is None:
            continue
        reached += 1
        created = org.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        deltas_sec.append((first - created).total_seconds())
    med_min = None
    if deltas_sec:
        med_min = round(median(deltas_sec) / 60.0, 1)
    total = len(orgs)
    return {
        "orgs_total": total,
        "reached_first_doc": reached,
        "reached_first_doc_share": round(100.0 * reached / total, 1) if total else 0.0,
        "median_minutes_to_first_doc": med_min,
    }
