"""Admin endpoints: jobs, users, usage, provider configuration."""
from __future__ import annotations

from typing import Optional

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.core.deps import AdminUser
from app.models.database import get_db
from app.models.models import ApiUsage, AuditLog, ProviderConfig, SearchJob, User
from app.schemas.admin import (
    AdminJobOut,
    AdminUserOut,
    AdminUserUpdate,
    ProviderConfigOut,
    ProviderConfigUpdate,
    UsageSummary,
)
from app.services import auth_service
from app.utils.datetime_utils import iso, utcnow

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/jobs", response_model=list[AdminJobOut])
def all_jobs(user: AdminUser, db: DBSession = Depends(get_db),
             limit: int = Query(default=100, ge=1, le=500),
             status_filter: Optional[str] = Query(default=None, alias="status")) -> list[AdminJobOut]:
    """All users' jobs (admin only)."""
    query = db.query(SearchJob, User.email).join(User, SearchJob.user_id == User.id)
    if status_filter:
        query = query.filter(SearchJob.status == status_filter)
    rows = query.order_by(SearchJob.created_at.desc()).limit(limit).all()
    return [
        AdminJobOut(
            id=job.id, user_email=email, query_raw=job.query_raw,
            status=job.status, progress=job.progress, total=job.total,
            emails_found=job.emails_found, provider=job.provider,
            created_at=job.created_at, finished_at=job.finished_at,
        )
        for job, email in rows
    ]


@router.get("/users", response_model=list[AdminUserOut])
def list_users(user: AdminUser, db: DBSession = Depends(get_db)) -> list[AdminUserOut]:
    """All users with job counts."""
    counts = dict(
        db.query(SearchJob.user_id, func.count(SearchJob.id))
        .group_by(SearchJob.user_id).all()
    )
    users = db.query(User).order_by(User.created_at.asc()).all()
    out = []
    for u in users:
        item = AdminUserOut.model_validate(u)
        item.job_count = counts.get(u.id, 0)
        out.append(item)
    return out


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(user_id: str, body: AdminUserUpdate, admin: AdminUser,
                db: DBSession = Depends(get_db)) -> AdminUserOut:
    """Change a user's role/status/name. Admin cannot demote or disable self."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == admin.id and (body.role == "USER" or body.is_active is False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Admins cannot demote or disable themselves")
    if body.role:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
        if not body.is_active:
            auth_service.revoke_all_user_tokens(db, target.id)
    if body.full_name is not None:
        target.full_name = body.full_name
    auth_service.audit(db, user_id=admin.id, action="admin_update_user",
                       entity="user", entity_id=user_id,
                       detail={"role": body.role, "is_active": body.is_active})
    db.commit()
    db.refresh(target)
    return AdminUserOut.model_validate(target)


@router.get("/usage", response_model=UsageSummary)
def usage(user: AdminUser, db: DBSession = Depends(get_db)) -> UsageSummary:
    """Provider API usage: today, month, and per-provider breakdown."""
    now = utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _agg(since):
        rows = (
            db.query(
                ApiUsage.provider,
                func.count(ApiUsage.id),
                func.coalesce(func.sum(ApiUsage.estimated_cost), 0.0),
            )
            .filter(ApiUsage.timestamp >= since)
            .group_by(ApiUsage.provider)
            .all()
        )
        return rows

    today_rows = _agg(start_of_day)
    month_rows = _agg(start_of_month)
    by_provider = [
        {"provider": p, "requests": c, "estimated_cost": round(float(cost), 4)}
        for p, c, cost in month_rows
    ]
    return UsageSummary(
        today_requests=sum(c for _, c, _ in today_rows),
        today_cost=round(sum(float(cost) for _, _, cost in today_rows), 4),
        month_requests=sum(c for _, c, _ in month_rows),
        month_cost=round(sum(float(cost) for _, _, cost in month_rows), 4),
        by_provider=by_provider,
    )


@router.get("/providers", response_model=list[ProviderConfigOut])
def list_providers(user: AdminUser, db: DBSession = Depends(get_db)) -> list[ProviderConfigOut]:
    """Provider registry (API keys never returned)."""
    rows = db.query(ProviderConfig).all()
    return [
        ProviderConfigOut(
            name=p.name, enabled=p.enabled,
            has_api_key=bool(p.encrypted_api_key),
            config_json=p.config_json,
        )
        for p in rows
    ]


@router.put("/providers/{name}", response_model=ProviderConfigOut)
def update_provider(name: str, body: ProviderConfigUpdate, admin: AdminUser,
                    db: DBSession = Depends(get_db)) -> ProviderConfigOut:
    """Enable/disable a provider or set its API key (stored encrypted)."""
    row = db.query(ProviderConfig).filter(ProviderConfig.name == name).first()
    if row is None:
        row = ProviderConfig(name=name)
        db.add(row)
    if body.api_key is not None:
        from app.utils.crypto import encrypt

        row.encrypted_api_key = encrypt(body.api_key) if body.api_key else ""
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.config_json is not None:
        import json

        try:
            json.loads(body.config_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="config_json must be valid JSON") from exc
        row.config_json = body.config_json
    row.updated_by = admin.id
    auth_service.audit(db, user_id=admin.id, action="admin_update_provider",
                       entity="provider", entity_id=name,
                       detail={"enabled": body.enabled, "key_set": body.api_key is not None})
    db.commit()
    db.refresh(row)
    return ProviderConfigOut(
        name=row.name, enabled=row.enabled,
        has_api_key=bool(row.encrypted_api_key), config_json=row.config_json,
    )


@router.get("/audit-logs")
def audit_logs(user: AdminUser, db: DBSession = Depends(get_db),
               limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    """Recent audit trail entries."""
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id, "user_id": r.user_id, "action": r.action,
            "entity": r.entity, "entity_id": r.entity_id,
            "ip_address": r.ip_address, "detail": r.detail,
            "created_at": iso(r.created_at),
        }
        for r in rows
    ]
