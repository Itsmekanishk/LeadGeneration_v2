"""Report export endpoints (PDF / CSV / XLSX)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from app.core.deps import CurrentUser
from app.models.database import get_db
from app.models.models import SearchJob
from app.services.export_service import ExportError, generate_export, resolve_download

router = APIRouter(prefix="/api/search", tags=["exports"])


def _job_or_404(db: DBSession, job_id: str, user) -> SearchJob:
    job = db.get(SearchJob, job_id)
    if job is None or (job.user_id != user.id and not user.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Search not found")
    return job


def _export(job_id: str, fmt: str, user: CurrentUser,
            db: DBSession = Depends(get_db)) -> dict:
    job = _job_or_404(db, job_id, user)
    if job.status != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Search is not completed yet")
    try:
        filename, rows = generate_export(db, job, fmt, user.id)
    except ExportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"filename": filename, "rows": rows, "format": fmt}


@router.post("/{job_id}/export/pdf")
def export_pdf(job_id: str, user: CurrentUser,
               db: DBSession = Depends(get_db)) -> dict:
    """Generate the PDF report; returns the private download filename."""
    return _export(job_id, "pdf", user, db)


@router.post("/{job_id}/export/csv")
def export_csv(job_id: str, user: CurrentUser,
               db: DBSession = Depends(get_db)) -> dict:
    """Generate the CSV export; returns the private download filename."""
    return _export(job_id, "csv", user, db)


@router.post("/{job_id}/export/xlsx")
def export_xlsx(job_id: str, user: CurrentUser,
                db: DBSession = Depends(get_db)) -> dict:
    """Generate the XLSX export; returns the private download filename."""
    return _export(job_id, "xlsx", user, db)


@router.get("/{job_id}/export/pdf")
def download_pdf(job_id: str, filename: str, user: CurrentUser,
                 db: DBSession = Depends(get_db)) -> FileResponse:
    """Download a previously generated PDF (ownership enforced)."""
    path = resolve_download(db, job_id, filename, user)
    return FileResponse(path, media_type="application/pdf",
                        filename=filename, headers={"Cache-Control": "private, no-store"})


@router.get("/{job_id}/export/csv")
def download_csv(job_id: str, filename: str, user: CurrentUser,
                 db: DBSession = Depends(get_db)) -> FileResponse:
    """Download a previously generated CSV (ownership enforced)."""
    path = resolve_download(db, job_id, filename, user)
    return FileResponse(path, media_type="text/csv",
                        filename=filename, headers={"Cache-Control": "private, no-store"})


@router.get("/{job_id}/export/xlsx")
def download_xlsx(job_id: str, filename: str, user: CurrentUser,
                  db: DBSession = Depends(get_db)) -> FileResponse:
    """Download a previously generated XLSX (ownership enforced)."""
    path = resolve_download(db, job_id, filename, user)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename, headers={"Cache-Control": "private, no-store"},
    )
