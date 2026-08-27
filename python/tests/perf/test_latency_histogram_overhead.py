"""What the #427 W4 latency histogram costs per request.

**Why this budget exists at all.** The histogram runs in the middleware's
``finally``, on **every** request to an allowlisted route — which is exactly the
two routes that matter. An observability feature that adds measurable latency to
the query path would be self-defeating: it exists to explain a latency incident.

**What is measured.** Two arms, because they answer different questions.

* ``histogram_record`` — the recording call in isolation, with a full request's
  worth of stages, over enough iterations that the per-call cost is readable.
  It is one dict lookup plus one ``bisect`` and three adds per series.
* ``histogram_added`` — the end-to-end delta through the real middleware between
  an allowlisted route (records) and a non-allowlisted one (returns after two
  string compares). That difference IS the feature's cost on a live request, and
  it is what a reviewer should read.

**What it is NOT evidence of.** It does not measure the rollup emission (a
background task, once per five minutes, off the request path), it says nothing
about memory (bounded by ``MAX_SERIES`` × ~200 B ≈ 100 KB at the cap), and like
``test_request_middleware_overhead`` it is a *comparison*: an ASGI round trip
over ``ASGITransport`` costs far more than the thing under test, so only the
delta is meaningful.
"""
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ragstack.observability.histogram import LatencyHistogram, reset_for_tests, route_key
from ragstack.observability.middleware import RequestContextMiddleware
from tests.perf._budget import _percentile, assert_budget

#: A realistic request's stage set — what ``/v1/query`` actually emits on the
#: fully-wired path, so the measured call does the most work it ever does.
STAGES = {
    "authz": (0.001, 1),
    "rewrite": (0.02, 1),
    "embed": (0.04, 1),
    "vector": (0.30, 3),
    "text": (0.05, 3),
    "graph": (0.01, 1),
    "fuse": (0.0004, 1),
    "rerank": (0.08, 1),
    "expand": (0.005, 1),
    "generate": (1.2, 1),
}

#: Per-`record()` budget. Measured at ~4 µs for eleven series (11 dict lookups,
#: 11 bisects, 44 adds); budgeted at 100 µs — twenty-five times that — because
#: this is a shared box with no CI and a perf test that flakes gets deleted
#: rather than investigated.
RECORD_BUDGET_S = 0.0001

#: p95 of (allowlisted route) minus p95 of (non-allowlisted route), end to end.
#: Same 0.5 ms shape as ``test_request_middleware_overhead.ADDED_BUDGET_S``, and
#: for the same reason.
ADDED_BUDGET_S = 0.0005

N = 60


@pytest.mark.perf
def test_recording_one_request_is_microseconds():
    hist = LatencyHistogram()

    def _record() -> None:
        # 200 requests per sample, so the timer resolution is not what is being
        # measured; the budget below is per-request, divided back out.
        for i in range(200):
            hist.record("POST /v1/query", f"coll_{i % 4}", 0.42, STAGES, is_error=False)

    assert_budget(
        "histogram_record_x200",
        _record,
        budget_s=RECORD_BUDGET_S * 200,
        n=20,
    )
    print(f"PERF histogram_record: series={hist.size} overflow={hist.overflow}")
    assert hist.overflow == 0, "the fixture must stay under the cap or it measures the cap"


def _app(*, path: str) -> tuple[FastAPI, str]:
    app = FastAPI()

    @app.post(path)
    async def _handler() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(RequestContextMiddleware)
    return app, path


async def _p95(app: FastAPI, path: str, label: str) -> float:
    samples: list[float] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(path)  # untimed warm-up (route compilation, lazy imports)
        for _ in range(N):
            start = time.perf_counter()
            await client.post(path)
            samples.append(time.perf_counter() - start)
    samples.sort()
    p50, p95 = _percentile(samples, 0.50), _percentile(samples, 0.95)
    print(f"PERF {label}: p50={p50:.6f}s p95={p95:.6f}s n={N}")
    return p95


@pytest.mark.perf
@pytest.mark.asyncio
async def test_the_histogram_adds_no_measurable_latency_to_a_recorded_route():
    """The recorded route's p95 must be within 0.5 ms of an identical route that
    the allowlist skips.

    ``/v1/queryx`` is the control arm on purpose: it is one character from the
    allowlisted path and travels every other line of the same middleware, so the
    difference between the arms is the histogram and nothing else.

    Order matters: the control arm runs first, so any one-off process warm-up is
    charged to it rather than to the arm under test — the conservative direction.
    """
    reset_for_tests()
    assert route_key("POST", "/v1/queryx") is None, "the control arm must not record"
    assert route_key("POST", "/v1/query") is not None, "the measured arm must record"

    control, control_path = _app(path="/v1/queryx")
    recorded, recorded_path = _app(path="/v1/query")

    baseline = await _p95(control, control_path, "histogram_absent")
    instrumented = await _p95(recorded, recorded_path, "histogram_present")
    added = instrumented - baseline
    print(f"PERF histogram_added: p95_added={added:.6f}s budget={ADDED_BUDGET_S:.6f}s n={N}")

    assert added <= ADDED_BUDGET_S, (
        f"the histogram added {added:.6f}s to p95, over the {ADDED_BUDGET_S:.6f}s budget"
    )
    reset_for_tests()
