"""What ``RequestContextMiddleware`` costs per request.

**What this budget is evidence of:** that adding request correlation to every
request is not a latency regression anyone will notice. The measured pieces are
one ``uuid4().hex`` (~2 µs), one ``ContextVar.set``, one header scan of the
inbound headers, one ``MutableHeaders`` write on the response start message, and
one ``log.debug`` that is filtered out at INFO. Call it ~10 µs of real work; the
0.5 ms budget is **fifty times** that, sized so a loaded box with no CI does not
flake rather than sized to the expectation.

**What it is NOT evidence of.** It does not measure the middleware under
concurrency, it does not measure the ``logging`` handler's cost when a line is
actually emitted (the request path emits at DEBUG, which INFO discards), and it
says nothing about the query pipeline — the stage timers land in #427 W3 with
their own budget. It is also a *comparison* rather than an absolute: the delta
between two otherwise-identical apps is what matters, because an ASGI round trip
over ``ASGITransport`` costs far more than the thing under test.

Two minimal apps rather than surgery on the real ``api.main`` app: the real one
carries CORS, the root-path middleware and the upload guard, all of which would
be measured too, and mutating its built middleware stack mid-process is exactly
the kind of global-state edit that leaks into the rest of the suite.
"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ragstack.observability.middleware import RequestContextMiddleware
from tests.perf._budget import _percentile, assert_budget_async

#: p95 of (with-middleware) minus p95 of (without). See the module docstring for
#: the arithmetic: ~10 µs of expected work, budgeted at 500 µs.
ADDED_BUDGET_S = 0.0005

N = 60


def _app(*, with_middleware: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    if with_middleware:
        app.add_middleware(RequestContextMiddleware)
    return app


async def _p95(app: FastAPI, label: str) -> float:
    samples: list[float] = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # One untimed warm-up: the first request through an ASGI app pays for
        # route compilation and lazy imports, which would land entirely in the
        # instrumented arm and invent a difference that is not there.
        await client.get("/health")

        import time

        for _ in range(N):
            start = time.perf_counter()
            await client.get("/health")
            samples.append(time.perf_counter() - start)

    samples.sort()
    p50, p95 = _percentile(samples, 0.50), _percentile(samples, 0.95)
    print(f"PERF {label}: p50={p50:.6f}s p95={p95:.6f}s n={N}")
    return p95


@pytest.mark.perf
@pytest.mark.asyncio
async def test_request_middleware_added_p95_within_budget():
    """The instrumented app's p95 must be within 0.5 ms of the bare app's.

    Order matters: the bare arm runs first so any one-off process warm-up
    (imports, allocator growth) is charged to it rather than to the arm under
    test — the conservative direction.
    """
    bare_p95 = await _p95(_app(with_middleware=False), "request_middleware_absent")
    instrumented_p95 = await _p95(_app(with_middleware=True), "request_middleware_present")

    added = instrumented_p95 - bare_p95
    print(
        f"PERF request_middleware_added: p95_added={added:.6f}s "
        f"budget={ADDED_BUDGET_S:.6f}s n={N}"
    )
    assert added <= ADDED_BUDGET_S, (
        f"RequestContextMiddleware added p95={added:.6f}s per request, over the "
        f"{ADDED_BUDGET_S:.6f}s budget (bare p95={bare_p95:.6f}s, "
        f"instrumented p95={instrumented_p95:.6f}s, n={N})"
    )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_instrumented_request_absolute_budget():
    """A second, absolute bound so the comparison above cannot pass by the bare
    arm being slow. 5 ms for an in-process ASGI round trip is generous."""
    app = _app(with_middleware=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/health")

        async def _once() -> None:
            r = await client.get("/health")
            assert r.headers.get("x-request-id")

        await assert_budget_async(
            "request_middleware_absolute", _once, budget_s=0.005, n=N
        )
