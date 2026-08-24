"""Tiny helper for perf-marked tests: run a callable N times, report p50/p95,
and assert p95 stays within budget.

Kept dependency-free (no numpy) — percentiles are computed by sorting samples
and indexing, which is exact enough for the small N these tests use.

``assert_budget`` is sync-only and ``assert_budget_async`` is for ``async def``
tests — use whichever matches the function under test; each raises ``TypeError``
if handed the wrong kind of callable.

Note: ``pyproject.toml`` sets ``addopts = "-m 'not perf'"``, so an explicit run
of a perf file needs `-m perf` appended — CLI `-m` overrides addopts.
"""
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any

_MIN_N = 20


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list of samples."""
    if not sorted_samples:
        raise ValueError("no samples to compute a percentile over")
    idx = min(len(sorted_samples) - 1, int(round(pct * (len(sorted_samples) - 1))))
    return sorted_samples[idx]


def _report_and_check(name: str, samples: list[float], budget_s: float, n: int) -> None:
    samples.sort()
    p50 = _percentile(samples, 0.50)
    p95 = _percentile(samples, 0.95)

    print(f"PERF {name}: p50={p50:.4f}s p95={p95:.4f}s budget={budget_s:.4f}s n={n}")

    assert p95 <= budget_s, (
        f"{name}: p95={p95:.4f}s exceeded budget={budget_s:.4f}s (p50={p50:.4f}s, n={n})"
    )


def assert_budget(
    name: str,
    fn: Callable[[], Any],
    *,
    budget_s: float,
    n: int = 20,
) -> None:
    """Run the sync no-arg callable ``fn`` ``n`` times, timing each call.

    Prints a grep-able ``PERF <name>: p50=<x>s p95=<y>s budget=<z>s n=<n>`` line
    and asserts the p95 latency is within ``budget_s`` seconds. Never asserts on
    a single sample — always p50/p95 over >= n repetitions.

    Raises ``TypeError`` if ``fn`` is a coroutine function or returns an
    awaitable (use ``assert_budget_async`` for those) — a lambda wrapping a
    coroutine function is not itself a coroutine function, so
    ``inspect.iscoroutinefunction`` alone can't catch it; checking the return
    value does.
    """
    if n < _MIN_N:
        raise ValueError(f"{name}: n must be >= {_MIN_N} for a meaningful p95 (got {n})")
    if inspect.iscoroutinefunction(fn):
        raise TypeError(f"{name}: fn is a coroutine function — use assert_budget_async")

    samples: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - start)
        if inspect.isawaitable(result):
            raise TypeError(f"{name}: fn() returned an awaitable — use assert_budget_async")

    _report_and_check(name, samples, budget_s, n)


async def assert_budget_async(
    name: str,
    afn: Callable[[], Awaitable[Any]],
    *,
    budget_s: float,
    n: int = 20,
) -> None:
    """Async counterpart to ``assert_budget``: awaits ``afn()`` ``n`` times in
    the caller's already-running event loop (no nested ``asyncio.run``, so it's
    safe to call from an ``async def`` test — the pattern anything holding
    loop-bound resources, like an httpx client or a DB connection, needs).

    Same p50/p95 math and PERF line as ``assert_budget``.
    """
    if n < _MIN_N:
        raise ValueError(f"{name}: n must be >= {_MIN_N} for a meaningful p95 (got {n})")

    samples: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await afn()
        samples.append(time.perf_counter() - start)

    _report_and_check(name, samples, budget_s, n)
