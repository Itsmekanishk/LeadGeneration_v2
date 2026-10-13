"""Dispatch regression tests.

A search must never sit in ``queued`` forever: Celery is only used when the
broker is reachable **and** a worker is alive, otherwise the job runs inline.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workers import dispatch  # noqa: E402


class _FakeThread:
    """Records thread construction instead of spawning a real one."""

    instances: list["_FakeThread"] = []

    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True


def _install_fake_threads(monkeypatch):
    _FakeThread.instances = []
    monkeypatch.setattr(
        dispatch, "threading", types.SimpleNamespace(Thread=_FakeThread)
    )
    return _FakeThread


def test_uses_celery_when_broker_and_worker_alive(monkeypatch):
    sent = []
    _install_fake_threads(monkeypatch)
    monkeypatch.setattr(dispatch, "_is_redis_available", lambda: True)
    monkeypatch.setattr(dispatch, "_has_live_workers", lambda now=None: True)

    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app, "send_task", lambda name, args=None: sent.append((name, args)))

    dispatch.dispatch_search_job("job-celery")

    assert sent == [("locallead.run_search_job", ["job-celery"])]
    assert _FakeThread.instances == []  # no inline thread was started


def test_falls_back_inline_when_broker_up_but_no_worker(monkeypatch):
    """The regression: Redis up + worker down used to hang the job forever."""
    _install_fake_threads(monkeypatch)
    monkeypatch.setattr(dispatch, "_is_redis_available", lambda: True)
    monkeypatch.setattr(dispatch, "_has_live_workers", lambda now=None: False)

    from app.workers.celery_app import celery_app

    def _boom(*a, **k):
        raise AssertionError("celery must not be used without a live worker")

    monkeypatch.setattr(celery_app, "send_task", _boom)

    dispatch.dispatch_search_job("job-inline")

    assert len(_FakeThread.instances) == 1
    thread = _FakeThread.instances[0]
    assert thread.started is True
    assert thread.daemon is True
    assert thread.args == ("job-inline",)
    assert thread.target is dispatch._run_inline


def test_falls_back_inline_when_broker_down(monkeypatch):
    _install_fake_threads(monkeypatch)
    monkeypatch.setattr(dispatch, "_is_redis_available", lambda: False)
    monkeypatch.setattr(
        dispatch,
        "_has_live_workers",
        lambda now=None: (_ for _ in ()).throw(
            AssertionError("worker probe must not run without a broker")
        ),
    )

    dispatch.dispatch_search_job("job-noredis")

    assert len(_FakeThread.instances) == 1
    assert _FakeThread.instances[0].started is True


def test_worker_liveness_result_is_cached(monkeypatch):
    calls = {"n": 0}

    class _Inspect:
        def ping(self):
            calls["n"] += 1
            return {"celery@host": {"ok": "pong"}}

    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.control, "inspect", lambda timeout=None: _Inspect())
    dispatch._worker_cache["checked_at"] = 0.0
    dispatch._worker_cache["alive"] = False

    assert dispatch._has_live_workers(now=1000.0) is True
    assert dispatch._has_live_workers(now=1001.0) is True  # served from cache
    assert calls["n"] == 1

    assert dispatch._has_live_workers(now=1000.0 + dispatch._WORKER_CACHE_TTL_SECONDS + 1) is True
    assert calls["n"] == 2  # cache expired -> probed again
