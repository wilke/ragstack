"""``observability.fallbacks`` and ``observability.dependencies`` (#751 PR-3):
the counter, the rate-limited warning and the shared dependency verdict."""
from __future__ import annotations

import asyncio
import logging

import pytest

from ragstack.observability.dependencies import (
    STATE_DISABLED,
    STATE_DOWN,
    STATE_OK,
    DependencyHealth,
)
from ragstack.observability.fallbacks import (
    FALLBACK_COMPONENTS,
    REASON_MAX_CHARS,
    RateLimitedLog,
    degraded_reason,
    fallback_total,
    record_fallback,
    reset_fallbacks,
)

# --------------------------------------------------------------------------- #
# the counter
# --------------------------------------------------------------------------- #


def test_counter_is_complete_and_monotonic():
    assert set(fallback_total()) == set(FALLBACK_COMPONENTS)
    assert all(v == 0 for v in fallback_total().values())
    assert record_fallback("rerank") == 1
    assert record_fallback("rerank") == 2
    assert record_fallback("generation") == 1
    assert fallback_total() == {**dict.fromkeys(FALLBACK_COMPONENTS, 0), "rerank": 2, "generation": 1}
    reset_fallbacks()
    assert fallback_total()["rerank"] == 0


def test_counter_refuses_an_unknown_component():
    """A typo must be a KeyError in a test, never a new metric label."""
    with pytest.raises(KeyError):
        record_fallback("reranker")


def test_components_mirror_the_contract_enum():
    import json
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts/schemas/degraded_entry.json").read_text()
    )
    assert tuple(schema["properties"]["component"]["enum"]) == FALLBACK_COMPONENTS
    assert schema["properties"]["reason"]["maxLength"] == REASON_MAX_CHARS


# --------------------------------------------------------------------------- #
# the reason string
# --------------------------------------------------------------------------- #


def test_reason_is_class_plus_first_line_bounded_and_scrubbed():
    exc = RuntimeError("first line\nsecond line with a traceback")
    assert degraded_reason(exc) == "RuntimeError: first line"
    long = RuntimeError("x" * 500)
    out = degraded_reason(long)
    assert len(out) == REASON_MAX_CHARS and out.endswith("…")
    creds = ConnectionError("GET http://user:s3cret@host:9200/x failed")
    assert "s3cret" not in degraded_reason(creds)
    assert "http://***@host:9200/x" in degraded_reason(creds)
    assert degraded_reason(ValueError("")) == "ValueError:"


# --------------------------------------------------------------------------- #
# the rate-limited warning
# --------------------------------------------------------------------------- #


def test_rate_limited_log_emits_once_per_window_and_carries_the_suppressed_count(caplog):
    now = [0.0]
    limiter = RateLimitedLog(interval_s=60.0, clock=lambda: now[0])
    log = logging.getLogger("test.ratelimit")
    caplog.set_level(logging.WARNING, logger="test.ratelimit")

    assert limiter.warning(log, "k", "rerank unavailable") is True
    assert limiter.warning(log, "k", "rerank unavailable") is False
    assert limiter.warning(log, "k", "rerank unavailable") is False
    assert limiter.suppressed("k") == 2
    assert [r.getMessage() for r in caplog.records] == ["rerank unavailable"]

    now[0] = 61.0
    assert limiter.warning(log, "k", "rerank unavailable") is True
    assert limiter.suppressed("k") == 0
    assert caplog.records[-1].getMessage() == (
        "rerank unavailable (2 similar warnings suppressed in the last 60s)"
    )
    # Another key has its own window.
    assert limiter.warning(log, "other", "something else") is True


def test_rate_limited_log_keeps_the_traceback_on_the_emitted_line(caplog):
    limiter = RateLimitedLog(interval_s=60.0)
    log = logging.getLogger("test.ratelimit.exc")
    caplog.set_level(logging.WARNING, logger="test.ratelimit.exc")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        limiter.warning(log, "k", "failed", exc_info=True)
    assert caplog.records[-1].exc_info is not None


# --------------------------------------------------------------------------- #
# the dependency verdict cache
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_probe_runs_once_inside_the_ttl_and_again_after_it():
    now = [100.0]
    health = DependencyHealth(ttl_s=10.0, clock=lambda: now[0])
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        return STATE_OK, "fine"

    v1 = await health.probe("reranker", op)
    v2 = await health.probe("reranker", op)
    assert calls == 1 and v1 is v2 and v1.state == STATE_OK and v1.source == "probe"
    now[0] = 110.0
    await health.probe("reranker", op)
    assert calls == 2
    assert health.probes_run == 2


@pytest.mark.asyncio
async def test_concurrent_probes_share_one_run():
    health = DependencyHealth(ttl_s=10.0)
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return STATE_OK, "fine"

    await asyncio.gather(*(health.probe("embedder", op) for _ in range(5)))
    assert calls == 1


@pytest.mark.asyncio
async def test_probe_exception_and_timeout_are_down_with_the_class_named():
    health = DependencyHealth(ttl_s=0.0, timeout_s=0.05)

    async def boom():
        raise ConnectionRefusedError("refused\nmore")

    v = await health.probe("llm", boom)
    assert v.state == STATE_DOWN and v.detail == "ConnectionRefusedError: refused"
    assert v.latency_ms is not None

    async def slow():
        await asyncio.sleep(1)
        return STATE_OK, "late"

    v = await health.probe("ingest_engine", slow)
    assert v.state == STATE_DOWN and v.detail.startswith("TimeoutError")


@pytest.mark.asyncio
async def test_request_path_record_is_served_by_probe_while_fresh():
    """Principle 4: what the request path learned IS the health verdict —
    a probe inside the window never runs."""
    health = DependencyHealth(ttl_s=10.0)
    v = health.record("reranker", STATE_DOWN, "ConnectError: refused")
    assert v.source == "request" and not v.ok

    async def must_not_run():
        raise AssertionError("probe ran although a fresh request verdict exists")

    assert await health.probe("reranker", must_not_run) is v
    assert health.get("reranker") is v


def test_record_rejects_an_unknown_state_and_ok_is_true_for_disabled():
    health = DependencyHealth()
    with pytest.raises(ValueError):
        health.record("x", "broken")
    assert health.record("x", STATE_DISABLED).ok is True
    assert health.record("x", STATE_DOWN).ok is False
