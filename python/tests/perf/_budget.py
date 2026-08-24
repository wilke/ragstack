"""Tiny helper for perf-marked tests: run a callable N times, report p50/p95,
and assert p95 stays within budget.

Kept dependency-free (no numpy) — percentiles are computed by sorting samples
and indexing, which is exact enough for the small N these tests use.
"""
import asyncio
import inspect
import time
from collections.abc import Callable
from typing import Any


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list of samples."""
    if not sorted_samples:
        raise ValueError("no samples to compute a percentile over")
    idx = min(len(sorted_samples) - 1, int(round(pct * (len(sorted_samples) - 1))))
    return sorted_samples[idx]


def assert_budget(
    name: str,
    fn: Callable[[], Any],
    *,
    budget_s: float,
    n: int = 20,
) -> None:
    """Run ``fn`` (sync or async, no-arg) ``n`` times, timing each call.

    Prints a grep-able ``PERF <name>: p50=<x>s p95=<y>s budget=<z>s n=<n>`` line
    and asserts the p95 latency is within ``budget_s`` seconds. Never asserts on
    a single sample — always p50/p95 over >= n repetitions.
    """
    is_async = inspect.iscoroutinefunction(fn)
    samples: list[float] = []

    if is_async:
        async def _run_all() -> None:
            for _ in range(n):
                start = time.perf_counter()
                await fn()
                samples.append(time.perf_counter() - start)

        asyncio.run(_run_all())
    else:
        for _ in range(n):
            start = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - start)

    samples.sort()
    p50 = _percentile(samples, 0.50)
    p95 = _percentile(samples, 0.95)

    print(f"PERF {name}: p50={p50:.4f}s p95={p95:.4f}s budget={budget_s:.4f}s n={n}")

    assert p95 <= budget_s, (
        f"{name}: p95={p95:.4f}s exceeded budget={budget_s:.4f}s (p50={p50:.4f}s, n={n})"
    )
