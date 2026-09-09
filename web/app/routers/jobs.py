"""Прогресс и выдача результатов фоновых задач (W-30)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.models import Job, JobStatus
from app.nav_context import cabinet_nav
from app.org_scope import get_org_for_user, require_org_id
from app.security import get_csrf_token
from app.services.jobs import job_download_path
from app.templating import templates

router = APIRouter(prefix="/cabinet/jobs", tags=["jobs"])


def _get_job(db: Session, user: CurrentUser, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.org_id != require_org_id(user):
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


def _wants_job_fragment(request: Request) -> bool:
    """Фрагмент только для поллинга панели (#job-body).

    hx-boost после 303 на /cabinet/jobs/N тоже шлёт HX-Request — если отдать
    fragment без hx-trigger, страница «Подготовка файла» зависает навсегда.
    """
    target = (request.headers.get("hx-target") or "").lstrip("#").strip()
    return target == "job-body"


@router.get("/{job_id}", response_class=HTMLResponse)
def job_status_page(
    job_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    job = _get_job(db, user, job_id)
    org = get_org_for_user(db, user)
    use_fragment = _wants_job_fragment(request)
    template = "cabinet/job_progress_fragment.html" if use_fragment else "cabinet/job_progress.html"
    response = templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "request": request,
            "csrf_token": get_csrf_token(request),
            "app_name": get_settings().app_name,
            "user": user,
            "org": org,
            "nav": cabinet_nav(db, user),
            "active": "documents",
            "job": job,
            "JobStatus": JobStatus,
        },
    )
    # Поллинг: после успеха уводим на карточку PDF (не оставляем «Готово» в фрагменте)
    if (
        use_fragment
        and job.status == JobStatus.succeeded
        and isinstance(job.result, dict)
        and job.result.get("view_url")
    ):
        response.headers["HX-Redirect"] = str(job.result["view_url"])
    return response


@router.get("/{job_id}/download")
def job_download(
    job_id: int,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    job = _get_job(db, user, job_id)
    if job.status != JobStatus.succeeded:
        raise HTTPException(status_code=409, detail="Файл ещё не готов")
    # document_pdf — редирект на документ
    if job.result and job.result.get("view_url"):
        from fastapi.responses import RedirectResponse

        return RedirectResponse(job.result["view_url"], status_code=303)
    path = job_download_path(job)
    if path is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    media = "application/pdf" if path.suffix.lower() == ".pdf" else "application/zip"
    return FileResponse(path, filename=path.name, media_type=media)
