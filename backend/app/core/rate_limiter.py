"""Redis-backed sliding-window rate limiter (in-memory fallback for dev/tests).

Never fails open silently on Redis errors: falls back to a per-process
limiter so limits still apply approximately in single-process deployments.
"""
from __future__ import annotations

from typing import Optional

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.logging_config import event_logger

log = event_logger("locallead.ratelimit")

_memory_lock = threading.Lock()
_memory_buckets: dict[str, deque[float]] = defaultdict(deque)

_redis_client = None
_redis_checked = False


def _get_redis():
    global _redis_client, _redis_checked  # noqa: PLW0603
    if not _redis_checked:
        _redis_checked = True
        try:
            import redis  # type: ignore

            from app.core.config import get_settings

            url = get_settings().redis_url
            if url and not url.startswith("memory://"):
                _redis_client = redis.Redis.from_url(
                    url, socket_connect_timeout=1, socket_timeout=1
                )
                _redis_client.ping()
        except Exception as exc:  # pragma: no cover - depends on env
            log.warning("rate_limit_redis_unavailable", error=str(exc))
            _redis_client = None
    return _redis_client


@dataclass
class RateLimit:
    """A named limit: max *window* per key."""

    name: str
    max_requests: int
    window_seconds: int


def check_rate_limit(key: str, limit: RateLimit) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds)."""
    now = time.monotonic()
    r = _get_redis()
    if r is not None:
        redis_key = f"rl:{limit.name}:{key}"
        window = limit.window_seconds
        try:
            pipe = r.pipeline()
            pipe.zremrangebyscore(redis_key, 0, now - window)
            pipe.zadd(redis_key, {str(now): now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, window)
            count = pipe.execute()[2]
            if count > limit.max_requests:
                oldest = r.zrange(redis_key, 0, 0, withscores=True)
                retry = int(oldest[0][1] + window - now) + 1 if oldest else window
                return False, max(retry, 1)
            return True, 0
        except Exception as exc:  # pragma: no cover
            log.warning("rate_limit_redis_error", error=str(exc))
            # fall through to memory limiter

    with _memory_lock:
        bucket = _memory_buckets[f"{limit.name}:{key}"]
        cutoff = now - limit.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit.max_requests:
            retry = int(bucket[0] + limit.window_seconds - now) + 1
            return False, max(retry, 1)
        bucket.append(now)
        return True, 0


def rate_limit_dependency(limit: "RateLimit | Callable[[], RateLimit]"):
    """Build a FastAPI dependency enforcing *limit* keyed by user (or IP).

    ``limit`` may be a RateLimit instance or a zero-argument factory; a
    factory is resolved per request so settings changes and test overrides
    take effect without recreating the app.
    """

    def _dependency(request: Request, user_id: Optional[str] = None) -> None:
        import os  # local import to avoid cycle
        # prefer authenticated principal; fall back to client IP
        principal = None
        try:
            principal = getattr(request.state, "user_id", None)
        except Exception:
            principal = None
        if not principal:
            principal = request.client.host if request.client else "anonymous"
        resolved = limit() if callable(limit) and not isinstance(limit, RateLimit) else limit
        # Dev/load-test harness opt-in (RATE_LIMIT_DISABLE_LOOPBACK=true): skip
        # per-IP limits for loopback clients so local scripts aren't throttled.
        # Default is OFF — production limits always apply unless explicitly
        # overridden by the operator.
        if os.environ.get("RATE_LIMIT_DISABLE_LOOPBACK", "false") == "true" and principal in ("127.0.0.1", "::1", "testclient"):
            return
        allowed, retry_after = check_rate_limit(principal, resolved)
        if not allowed:
            log.warning(
                "rate_limit_exceeded",
                limit=resolved.name,
                principal_hash=hash(principal) % 100000,
                retry_after=retry_after,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please retry later.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency


def build_search_limit() -> RateLimit:
    """Search-creation limit from settings (resolved lazily per request)."""
    from app.core.config import get_settings

    s = get_settings()
    # tests can lower this via env; production uses configured values
    return RateLimit("search", s.rate_limit_search_per_min, 60)


def search_limit_from_env() -> RateLimit:
    """Like build_search_limit but honors LOCALLEAD_TEST_SEARCH_LIMIT env.
    Exists so integration tests can lower the limit without patching
    import-time state."""
    import os

    limit = os.getenv("LOCALLEAD_TEST_SEARCH_LIMIT")
    if limit:
        return RateLimit("search", int(limit), 60)
    return build_search_limit()


def build_api_limit() -> RateLimit:
    """General API limit from settings."""
    from app.core.config import get_settings

    s = get_settings()
    return RateLimit("api", s.rate_limit_api_per_min, 60)
