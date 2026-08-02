"""Мастер «Новый комплект» — 4 шага (W-04)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.models import Counterparty, Document, JobType
from app.org_scope import get_document_for_org, get_org_for_user, list_counterparties, require_org_id
from app.nav_context import cabinet_nav
from app.security import check_csrf, get_csrf_token
from app.services.jobs import enqueue_job
from app.services.package_generate import (
    generate_package,
)
from app.services.package_master import (
    CP_TO_TYPE,
    SESSION_KEY,
    TYPE_LABELS,
    TYPE_TO_CP,
    collect_fields,
    contract_options,
    core_from_counterparty,
    display_name,
    extra_options,
    field_defaults,
    infer_type,
    selected_templates,
)
from app.services.party_check import refresh_stale_egrul
from app.templating import templates

router = APIRouter(prefix="/cabinet/package", tags=["package"])


def _wizard(request: Request) -> dict:
    data = request.session.get(SESSION_KEY)
    if not isinstance(data, dict):
        data = {}
        request.session[SESSION_KEY] = data
    return data


def _save(request: Request, data: dict) -> None:
    request.session[SESSION_KEY] = data


def _clear(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def _page(request: Request, user: CurrentUser, org, db, step: int, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "package",
        "step": step,
        "steps": [
            (1, "Контрагент"),
            (2, "Документы"),
            (3, "Поля"),
            (4, "Готово"),
        ],
        "flash_error": None,
        "flash_ok": None,
        "types": TYPE_LABELS,
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
def package_start(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    data = _wizard(request)
    q_type = request.query_params.get("тип")
    if q_type in TYPE_LABELS:
        data["тип"] = q_type
        _save(request, data)

    cp_id_raw = str(request.query_params.get("counterparty_id") or "").strip()
    egrul_warning = None
    if cp_id_raw.isdigit():
        cp = db.get(Counterparty, int(cp_id_raw))
        if cp is not None and cp.org_id == org.id:
            data["counterparty_id"] = cp.id
            data["тип"] = CP_TO_TYPE.get(cp.type, data.get("тип") or "Юрлицо")
            data["core_values"] = {
                **(data.get("core_values") or {}),
                **core_from_counterparty(data["тип"], cp),
            }
            _save(request, data)
            _, egrul_warning = refresh_stale_egrul(
                db, org_id=org.id, user_id=user.id, cp=cp
            )

    тип = data.get("тип") or "Физлицо"
    cps = list_counterparties(db, org.id)
    cps = [c for c in cps if c.type == TYPE_TO_CP.get(тип, c.type)]
    core_fields = _core_field_names(тип)
    assist = _assist_ctx(db, org.id, core_fields, data.get("core_values") or {})
    return templates.TemplateResponse(
        request=request,
        name="cabinet/package_step1.html",
        context=_page(
            request,
            user,
            org,
            db,
            1,
            wizard=data,
            counterparties=cps,
            core_fields=core_fields,
            egrul_warning=egrul_warning,
            **assist,
        ),
    )


def _core_field_names(тип: str) -> list[str]:
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.master import core_fields

    return core_fields(тип)


def _assist_ctx(db, org_id: int, field_names: list[str], values: dict | None = None) -> dict:
    from app.services.form_assist import enrich_form_context

    assist = enrich_form_context(db, org_id, field_names, values or {})
    return {
        "values": assist["values"],
        "field_meta": assist["field_meta"],
        "number_peeks": assist["number_peeks"],
    }


@router.post("/step1", response_class=HTMLResponse)
async def package_step1(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")

    тип = str(form.get("тип") or "Физлицо")
    if тип not in TYPE_LABELS:
        тип = "Физлицо"

    data = _wizard(request)
    data["тип"] = тип
    cp_id_raw = str(form.get("counterparty_id") or "").strip()
    core = {f: str(form.get(f) or "").strip() for f in _core_field_names(тип)}

    if cp_id_raw and cp_id_raw != "new":
        cp = db.get(Counterparty, int(cp_id_raw))
        if cp is None or cp.org_id != org.id:
            raise HTTPException(status_code=404, detail="Контрагент не найден")
        data["counterparty_id"] = cp.id
        # дополнить пустые поля из карточки
        from_cp = core_from_counterparty(тип, cp)
        for k, v in from_cp.items():
            if not core.get(k):
                core[k] = v
    else:
        data["counterparty_id"] = None

    core_names = _core_field_names(тип)
    first = core_names[0] if core_names else ""
    if first and not core.get(first):
        cps = [c for c in list_counterparties(db, org.id) if c.type == TYPE_TO_CP[тип]]
        assist = _assist_ctx(db, org.id, core_names, core)
        return templates.TemplateResponse(
            request=request,
            name="cabinet/package_step1.html",
            context=_page(
                request,
                user,
                org,
                db,
                1,
                wizard=data,
                counterparties=cps,
                core_fields=core_names,
                flash_error="Заполните основные данные контрагента.",
                prefill=core,
                **assist,
            ),
            status_code=400,
        )

    data["core_values"] = core
    egrul_warning = None
    if data.get("counterparty_id"):
        cp = db.get(Counterparty, int(data["counterparty_id"]))
        if cp is not None and cp.org_id == org.id:
            _, egrul_warning = refresh_stale_egrul(
                db, org_id=org.id, user_id=user.id, cp=cp
            )
    data["egrul_warning"] = egrul_warning
    _save(request, data)
    return RedirectResponse("/cabinet/package/step2", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/step2", response_class=HTMLResponse)
def package_step2_get(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    data = _wizard(request)
    if not data.get("тип") or not data.get("core_values"):
        return RedirectResponse("/cabinet/package/", status_code=303)
    тип = data["тип"]
    contracts = contract_options(тип, org.id)
    chosen = data.get("contract_template") or (contracts[0] if contracts else "")
    extras = extra_options(тип, chosen, org.requisites)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/package_step2.html",
        context=_page(request, user, org, db, 2,
            wizard=data,
            contracts=contracts,
            chosen=chosen,
            extras=extras,
            display_name=display_name,
        ),
    )


@router.post("/step2", response_class=HTMLResponse)
async def package_step2_post(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    data = _wizard(request)
    if not data.get("тип"):
        return RedirectResponse("/cabinet/package/", status_code=303)

    contract = str(form.get("contract_template") or "")
    action = str(form.get("action") or "next")
    extras_meta = extra_options(data["тип"], contract, org.requisites)
    if action == "refresh":
        data["contract_template"] = contract
        _save(request, data)
        return RedirectResponse("/cabinet/package/step2", status_code=303)

    checked = [name for name, _ in extras_meta if form.get(f"extra_{name}")]
    data["contract_template"] = contract
    data["extras"] = checked
    selected = selected_templates(contract, checked)
    if not selected:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/package_step2.html",
            context=_page(request, user, org, db, 2,
                wizard=data,
                contracts=contract_options(data["тип"], org.id),
                chosen=contract,
                extras=extras_meta,
                display_name=display_name,
                flash_error="Выберите договор или сопутствующие документы.",
            ),
            status_code=400,
        )
    data["selected"] = selected
    _save(request, data)
    return RedirectResponse("/cabinet/package/step3", status_code=303)


@router.get("/step3", response_class=HTMLResponse)
def package_step3_get(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    data = _wizard(request)
    selected = data.get("selected") or []
    if not selected:
        return RedirectResponse("/cabinet/package/step2", status_code=303)
    core_names, additional = collect_fields(selected, data["тип"], org.id)
    all_fields = list(dict.fromkeys([*core_names, *additional]))
    from app.services.form_assist import peek_numbers_for

    next_numbers = peek_numbers_for(db, org.id, all_fields)
    defaults = field_defaults(
        data.get("contract_template") or "",
        data.get("core_values") or {},
        data.get("extras") or [],
        next_numbers=next_numbers,
    )
    values = {}
    values.update(defaults)
    values.update(data.get("core_values") or {})
    values.update(data.get("additional_values") or {})
    assist = _assist_ctx(db, org.id, all_fields, values)
    values = assist["values"]
    egrul_warning = data.get("egrul_warning")
    if data.get("counterparty_id"):
        cp = db.get(Counterparty, int(data["counterparty_id"]))
        if cp is not None and cp.org_id == org.id:
            _, egrul_warning = refresh_stale_egrul(
                db, org_id=org.id, user_id=user.id, cp=cp
            )
            data["egrul_warning"] = egrul_warning
            _save(request, data)

    return templates.TemplateResponse(
        request=request,
        name="cabinet/package_step3.html",
        context=_page(request, user, org, db, 3,
            wizard=data,
            core_names=core_names,
            additional=additional,
            values=values,
            field_meta=assist["field_meta"],
            number_peeks=assist["number_peeks"],
            selected=selected,
            display_name=display_name,
            egrul_warning=egrul_warning,
        ),
    )


@router.post("/step3", response_class=HTMLResponse)
async def package_step3_post(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    data = _wizard(request)
    selected = data.get("selected") or []
    if not selected:
        return RedirectResponse("/cabinet/package/step2", status_code=303)

    core_names, additional = collect_fields(selected, data["тип"], org.id)
    core_values = {f: str(form.get(f) or "").strip() for f in core_names}
    additional_values = {f: str(form.get(f) or "").strip() for f in additional}
    data["core_values"] = core_values
    data["additional_values"] = additional_values
    _save(request, data)

    from app.services.limits import assert_can_generate
    from app.services.settings_svc import bank_is_complete, ensure_requisites, is_bill_template

    assert_can_generate(db, org.id)

    if any(is_bill_template(t) for t in selected) and not bank_is_complete(ensure_requisites(org)):
        all_fields = list(dict.fromkeys([*core_names, *additional]))
        values = {**core_values, **additional_values}
        assist = _assist_ctx(db, org.id, all_fields, values)
        return templates.TemplateResponse(
            request=request,
            name="cabinet/package_step3.html",
            context=_page(
                request,
                user,
                org,
                db,
                3,
                wizard=data,
                core_names=core_names,
                additional=additional,
                values=assist["values"],
                field_meta=assist["field_meta"],
                number_peeks=assist["number_peeks"],
                selected=selected,
                display_name=display_name,
                flash_error=(
                    "В комплекте есть счёт на оплату, но в «Настройки → Банк» не заполнены "
                    "реквизиты получателя. Заполните банк и повторите генерацию."
                ),
            ),
            status_code=400,
        )

    try:
        result = generate_package(
            db=db,
            org=org,
            user_id=user.id,
            тип=data["тип"],
            contract_template=data.get("contract_template") or "",
            extras=data.get("extras") or [],
            core_values=core_values,
            additional_values=additional_values,
            counterparty_id=data.get("counterparty_id"),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/package_step3.html",
            context=_page(request, user, org, db, 3,
                wizard=data,
                core_names=core_names,
                additional=additional,
                values={**core_values, **additional_values},
                selected=selected,
                display_name=display_name,
                flash_error=f"Ошибка генерации: {exc}",
            ),
            status_code=400,
        )

    data["document_ids"] = result["document_ids"]
    data["counterparty_id"] = result["counterparty_id"]
    data["core_values"] = result["core_values"]
    data["additional_values"] = result["additional_values"]
    _save(request, data)
    return RedirectResponse("/cabinet/package/done", status_code=303)


@router.get("/done", response_class=HTMLResponse)
def package_done(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    data = _wizard(request)
    ids = data.get("document_ids") or []
    if not ids:
        return RedirectResponse("/cabinet/package/", status_code=303)
    org_id = require_org_id(user)
    docs = []
    for i in ids:
        docs.append(get_document_for_org(db, org_id, int(i)))
    return templates.TemplateResponse(
        request=request,
        name="cabinet/package_done.html",
        context=_page(request, user, org, db, 4,
            wizard=data,
            documents=docs,
            display_name=display_name,
        ),
    )


@router.get("/zip")
def package_zip(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    data = _wizard(request)
    ids = data.get("document_ids") or []
    if not ids:
        raise HTTPException(status_code=404, detail="Комплект не найден")
    org_id = require_org_id(user)
    for i in ids:
        get_document_for_org(db, org_id, int(i))
    job = enqueue_job(
        db,
        org_id=org_id,
        user_id=user.id,
        job_type=JobType.package_zip,
        payload={"document_ids": [int(i) for i in ids]},
    )
    return RedirectResponse(f"/cabinet/jobs/{job.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/pdf")
async def package_pdf(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    data = _wizard(request)
    ids = data.get("document_ids") or []
    if not ids:
        raise HTTPException(status_code=404, detail="Комплект не найден")
    org_id = require_org_id(user)
    for i in ids:
        get_document_for_org(db, org_id, int(i))
    job = enqueue_job(
        db,
        org_id=org_id,
        user_id=user.id,
        job_type=JobType.package_pdf,
        payload={"document_ids": [int(i) for i in ids]},
    )
    return RedirectResponse(f"/cabinet/jobs/{job.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/reset")
async def package_reset(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    _clear(request)
    return RedirectResponse("/cabinet/package/", status_code=303)


@router.get("/repeat/{doc_id}", response_class=HTMLResponse)
def package_repeat(
    doc_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    """«Повторить» из журнала — предзаполнение мастера из контекста документа."""
    org = get_org_for_user(db, user)
    doc = get_document_for_org(db, require_org_id(user), doc_id)
    тип = infer_type(doc.template)
    core_names = _core_field_names(тип)
    ctx = dict(doc.context or {})
    core_values = {k: str(ctx.get(k) or "") for k in core_names}
    # подобрать extras по исходному договору, если это не договор — как contract
    contract = doc.template if str(doc.template).startswith("Договор_") else ""
    extras_meta = extra_options(тип, contract, org.requisites) if contract else []
    extras = [n for n, on in extras_meta if on]
    if contract:
        selected = selected_templates(contract, extras)
    else:
        selected = [doc.template]
        contract = ""
        extras = []

    additional_all = collect_fields(selected, тип, org.id)[1] if selected else []
    additional_values = {k: str(ctx.get(k) or "") for k in additional_all}

    data = {
        "тип": тип,
        "counterparty_id": doc.counterparty_id,
        "core_values": core_values,
        "additional_values": additional_values,
        "contract_template": contract or (selected[0] if selected else ""),
        "extras": extras,
        "selected": selected,
        "from_repeat": doc_id,
    }
    if doc.counterparty_id:
        cp = db.get(Counterparty, doc.counterparty_id)
        if cp and cp.org_id == org.id:
            for k, v in core_from_counterparty(тип, cp).items():
                if not data["core_values"].get(k):
                    data["core_values"][k] = v
    _save(request, data)
    return RedirectResponse("/cabinet/package/step3", status_code=303)
