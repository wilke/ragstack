"""Unit coverage for the perf-budget helper itself (tests/perf/_budget.py).

Deliberately NOT marked ``perf`` — this exercises the helper's own correctness
(guard rails, math, error messages), not a system's performance, so it must
run as part of the ordinary ``make test-python`` suite.
"""
import asyncio

import pytest

from tests.perf._budget import assert_budget, assert_budget_async


def test_awaitable_returning_callable_raises_type_error():
    async def _coro() -> None:
        pass

    with pytest.raises(TypeError, match="assert_budget_async"):
        assert_budget("bad", lambda: _coro(), budget_s=1.0, n=20)


def test_coroutine_function_raises_type_error():
    async def _afn() -> None:
        pass

    with pytest.raises(TypeError, match="assert_budget_async"):
        assert_budget("bad", _afn, budget_s=1.0, n=20)


def test_n_below_floor_raises_value_error_sync():
    with pytest.raises(ValueError, match="n must be >= 20"):
        assert_budget("too-few", lambda: None, budget_s=1.0, n=19)


@pytest.mark.asyncio
async def test_n_below_floor_raises_value_error_async():
    async def _afn() -> None:
        pass

    with pytest.raises(ValueError, match="n must be >= 20"):
        await assert_budget_async("too-few", _afn, budget_s=1.0, n=19)


def test_exceeded_budget_raises_assertion_with_message():
    def _slow() -> None:
        pass

    with pytest.raises(AssertionError, match="exceeded budget"):
        assert_budget("too-slow", _slow, budget_s=1e-9, n=20)


@pytest.mark.asyncio
async def test_assert_budget_async_works_from_an_async_test():
    async def _fast() -> None:
        await asyncio.sleep(0)

    # Must not raise "cannot be called from a running event loop" — this test
    # itself runs inside a live loop (pytest-asyncio), which is exactly the
    # case assert_budget's own asyncio.run() used to blow up on.
    await assert_budget_async("fast", _fast, budget_s=1.0, n=20)
