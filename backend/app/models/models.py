"""SQLAlchemy ORM models for LocalLead Intelligence.

Tables: users, search_jobs, businesses, business_contacts, crawl_pages,
crawl_events, exports, api_usage, audit_logs, refresh_tokens,
provider_configs, password_reset_tokens.
"""
from __future__ import annotations

from typing import Optional

import enum
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.utils.datetime_utils import utcnow


def _uuid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- 
# Enums (stored as native STRING for portability; values match API contracts)
# ---------------------------------------------------------------------------


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    DISCOVERING = "discovering"
    ENRICHING = "enriching"
    CRAWLING = "crawling"
    EXTRACTING = "extracting"
    DEDUPLICATING = "deduplicating"
    GENERATING_REPORT = "generating_report"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    USER = "USER"


class EmailConfidence(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PhoneSource(str, enum.Enum):
    PROVIDER = "provider"
    WEBSITE = "website"


class ExportFormat(str, enum.Enum):
    PDF = "pdf"
    CSV = "csv"
    XLSX = "xlsx"


class CrawlStatus(str, enum.Enum):
    PENDING = "pending"
    OK = "ok"
    TIMEOUT = "timeout"
    ROBOTS_DISALLOWED = "robots_disallowed"
    HTTP_ERROR = "http_error"
    DNS_ERROR = "dns_error"
    SSL_ERROR = "ssl_error"
    ERROR = "error"
    SKIPPED = "skipped"


# --------------------------------------------------------------------------- 
# Users & auth
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(
        String(10), default=UserRole.USER.value, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    jobs: Mapped[list["SearchJob"]] = relationship(back_populates="user")

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship()


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)


# --------------------------------------------------------------------------- 
# Search jobs
# ---------------------------------------------------------------------------


class SearchJob(Base):
    __tablename__ = "search_jobs"
    __table_args__ = (
        Index("ix_search_jobs_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    query_raw: Mapped[str] = mapped_column(String(512), nullable=False)
    parsed_category: Mapped[str] = mapped_column(String(255), default="")
    parsed_location: Mapped[str] = mapped_column(String(255), default="")
    parsed_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    parsed_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_results: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    # What the user originally asked for (before the server MAX_RESULTS clamp);
    # kept so the UI can honestly say "requested N, showing M available".
    requested_results: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    radius_m: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    country: Mapped[str] = mapped_column(String(2), default="in")
    language: Mapped[str] = mapped_column(String(8), default="en")
    provider: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.QUEUED.value, index=True)
    stage_detail: Mapped[str] = mapped_column(String(255), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    emails_found: Mapped[int] = mapped_column(Integer, default=0)
    phones_found: Mapped[int] = mapped_column(Integer, default=0)
    websites_found: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    started_at: Mapped[object] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[object] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="jobs")
    businesses: Mapped[list["Business"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    exports: Mapped[list["Export"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------- 
# Businesses & contacts
# ---------------------------------------------------------------------------


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (
        UniqueConstraint("job_id", "provider_place_id", name="uq_business_place_per_job"),
        Index("ix_businesses_job_name", "job_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("search_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)
    provider_place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(255), default="")
    address: Mapped[str] = mapped_column(String(512), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    phone_normalized: Mapped[str] = mapped_column(String(32), default="")
    phone_source: Mapped[str] = mapped_column(String(16), default="")
    website: Mapped[str] = mapped_column(String(512), default="")
    website_normalized: Mapped[str] = mapped_column(String(512), default="")
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    email_confidence: Mapped[str] = mapped_column(String(8), default="")
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    maps_url: Mapped[str] = mapped_column(String(512), default="")
    opening_hours: Mapped[str] = mapped_column(String(1024), default="")
    dedup_status: Mapped[str] = mapped_column(String(16), default="unique", index=True)
    duplicate_of_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    dedup_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    job: Mapped[SearchJob] = relationship(back_populates="businesses")
    contacts: Mapped[list["BusinessContact"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    crawl_pages: Mapped[list["CrawlPage"]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )


class BusinessContact(Base):
    __tablename__ = "business_contacts"
    __table_args__ = (
        # A contact's identity is its email *and* its number. Keying on email
        # alone capped each business at a single phone-only row (they all carry
        # email = ""), so a second number found on the website was discarded.
        UniqueConstraint("business_id", "email", "phone_normalized",
                         name="uq_contact_identity_per_business"),
        Index("ix_contacts_business", "business_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    phone: Mapped[str] = mapped_column(String(32), default="")
    phone_normalized: Mapped[str] = mapped_column(String(32), default="")
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    confidence: Mapped[str] = mapped_column(String(8), default="LOW")
    # Disify verdict for this email (True = disposable/temp host, False = not
    # disposable, NULL = check disabled or unreachable). Disposable emails
    # are downgraded to LOW confidence by the pipeline.
    email_disposable: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)

    business: Mapped[Business] = relationship(back_populates="contacts")


class CrawlPage(Base):
    __tablename__ = "crawl_pages"
    __table_args__ = (
        UniqueConstraint("business_id", "url", name="uq_crawl_page_url"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    business_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("businesses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), default="other")
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    emails_found: Mapped[int] = mapped_column(Integer, default=0)
    phones_found: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)

    business: Mapped[Business] = relationship(back_populates="crawl_pages")


class CrawlEvent(Base):
    __tablename__ = "crawl_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    business_id: Mapped[Optional[str]] = mapped_column(String(36), index=True, nullable=True)
    domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)


# --------------------------------------------------------------------------- 
# Exports, usage, audit, providers
# ---------------------------------------------------------------------------


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("search_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)  # uuid-based
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)

    job: Mapped[SearchJob] = relationship(back_populates="exports")


class ApiUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (
        Index("ix_api_usage_ts", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=1)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True, nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(36), index=True, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_ts", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow, nullable=False)


class ProviderConfig(Base):
    """Admin-managed provider registry. API keys are encrypted at rest."""

    __tablename__ = "provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(String(1024), default="")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[object] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
