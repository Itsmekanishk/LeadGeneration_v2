"""FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.logging_config import event_logger, setup_logging
from app.core.startup_checks import run_startup_checks

log = event_logger("locallead.app")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add hardened response headers to every response."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; form-action 'none'; "
            "sandbox",
        )
        if get_settings().is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup validation: fail fast on bad configuration.

    Delegates to ``run_startup_checks`` which refuses to boot in production on
    missing credentials/secrets and prints an actionable banner in development.
    """
    settings = get_settings()
    setup_logging()
    run_startup_checks(settings)
    if settings.database_url.startswith("sqlite"):
        from app.models.base import Base
        from app.models.database import get_engine
        Base.metadata.create_all(get_engine())
    log.info("app_startup", env=settings.app_env, provider=settings.business_provider)
    yield
    log.info("app_shutdown")


def create_app() -> FastAPI:
    """Build the FastAPI app with middleware and routers."""
    settings = get_settings()
    app = FastAPI(
        title="LocalLead Intelligence API",
        version="1.0.0",
        description=(
            "Commercial lead-generation platform: search businesses by industry "
            "and location, enrich with public website contacts, export reports.\n\n"
            "Authentication: JWT Bearer (login via /api/auth/login)."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware)

    # rate limit general API traffic
    from app.core.rate_limiter import build_api_limit, rate_limit_dependency

    general_limit = rate_limit_dependency(build_api_limit())

    from app.api import admin, auth, business, exports, health, search

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(search.router)
    app.include_router(business.router)
    app.include_router(exports.router)
    app.include_router(admin.router, dependencies=[Depends(general_limit)])

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        """Return clean 422s without leaking internals.

        ``exc.errors()`` may contain non-serializable context objects, so we
        project each error to safe primitives before returning it.
        """
        safe_errors = [
            {
                "loc": [str(x) for x in e.get("loc", [])],
                "msg": str(e.get("msg", "invalid")),
                "type": str(e.get("type", "value_error")),
            }
            for e in list(exc.errors())[:10]
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Validation failed", "errors": safe_errors},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        log.error("unhandled_exception", path=request.url.path, error=str(exc)[:300])
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()
