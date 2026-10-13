"""Search job schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SearchCreateRequest(BaseModel):
    """Either free_text or structured fields; both validated server-side."""

    free_text: Optional[str] = Field(default=None, max_length=300)
    category: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=200)
    max_results: int = Field(default=50, ge=1, le=200)
    radius_m: Optional[int] = Field(default=None, ge=100, le=50000)
    country: str = Field(default="in", min_length=2, max_length=2)
    language: str = Field(default="en", min_length=2, max_length=8)
    provider: Optional[str] = Field(default=None, max_length=32)

    @field_validator("free_text", "category", "location")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v

    @field_validator("provider")
    @classmethod
    def _provider_allowed(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("mock", "google", "serper", "nominatim", "geoapify"):
            raise ValueError("provider must be 'mock', 'google', 'serper', 'nominatim' or 'geoapify'")
        return v


class InterpretedQuery(BaseModel):
    raw_query: str
    category: Optional[str] = None
    category_slug: Optional[str] = None
    location: Optional[str] = None
    confidence: str = "high"
    notes: list[str] = []


class SearchInterpretResponse(BaseModel):
    """Response of the cheap preview endpoint (no job created)."""

    interpreted: InterpretedQuery
    estimated_max_results: int


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str = "queued"


class JobProgress(BaseModel):
    total: int = 0
    completed: int = 0
    failed: int = 0
    emails_found: int = 0
    phones_found: int = 0
    websites_found: int = 0


class JobOut(BaseModel):
    id: str
    query_raw: str
    parsed_category: str
    parsed_location: str
    status: str
    stage_detail: str
    progress: int
    provider: str
    max_results: int
    requested_results: int = 0
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_message: str = ""
    progress_stats: JobProgress = JobProgress()

    model_config = {"from_attributes": True}


class BusinessOut(BaseModel):
    id: str
    name: str
    category: str
    address: str
    phone: str
    phone_normalized: str
    phone_source: str
    email: str
    email_confidence: str
    website: str
    website_normalized: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    maps_url: str
    dedup_status: str
    source: str
    crawl_summary: str = ""
    # Every distinct contact discovered for this lead, primary first then by
    # confidence. The single `email`/`phone` fields above stay as the headline
    # value; these carry the rest so no discovered contact is dropped by the API.
    emails: list[str] = []
    phones: list[str] = []
    # Number of distinct contact values behind this lead (len(emails) +
    # len(phones)); lets the UI sort/surface the richest records.
    contact_count: int = 0

    model_config = {"from_attributes": True}


class BusinessDetailOut(BusinessOut):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    opening_hours: str = ""
    contacts: list["ContactOut"] = []
    crawl_pages: list["CrawlPageOut"] = []


class ContactOut(BaseModel):
    id: str
    email: str
    phone: str
    phone_normalized: str
    contact_name: str
    source_url: str
    source: str
    confidence: str
    verified: bool

    model_config = {"from_attributes": True}


class CrawlPageOut(BaseModel):
    id: str
    url: str
    page_type: str
    http_status: Optional[int]
    status: str
    emails_found: int
    phones_found: int
    duration_ms: int

    model_config = {"from_attributes": True}


class PaginatedBusinesses(BaseModel):
    items: list[BusinessOut]
    total: int
    page: int
    page_size: int
    pages: int
    # The user's originally requested count (before server-side clamping), so
    # the frontend can show "Requested 100 leads, but only 45 are available."
    # 0 → unknown (legacy jobs); frontend falls back to job.max_results.
    requested_results: int = 0


class ResultsSummary(BaseModel):
    job: JobOut
    summary: dict
