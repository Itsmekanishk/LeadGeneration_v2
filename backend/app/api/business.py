"""Business detail endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.api.search import _attach_all_contacts
from app.core.deps import CurrentUser
from app.models.database import get_db
from app.models.models import Business, SearchJob
from app.schemas.search import BusinessDetailOut

router = APIRouter(prefix="/api/business", tags=["business"])


@router.get("/{business_id}", response_model=BusinessDetailOut)
def get_business(business_id: str, user: CurrentUser,
                 db: DBSession = Depends(get_db)) -> BusinessDetailOut:
    """Full business record with contacts and crawl-page provenance."""
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Business not found")
    job = db.get(SearchJob, biz.job_id)
    if job is None or (job.user_id != user.id and not user.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Business not found")
    out = BusinessDetailOut.model_validate(biz)
    # Reuse the list endpoint's helper so the detail payload carries every
    # distinct contact too (primary first, then by confidence). Without this
    # the drawer's contact lists would render empty while the rows exist.
    _attach_all_contacts(db, [out])
    return out
