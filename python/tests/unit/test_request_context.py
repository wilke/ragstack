"""The contextvar mechanic the whole observability package rests on.

``context.py`` states one rule: the middleware ``set()``s a ``RequestContext``
once, and everything below it **mutates that object in place and never calls
set()**. Every later work item depends on it — W3's stage timings are recorded
from inside ``asyncio.gather`` children on the query path, and if a producer
there re-``set()``s the var instead of mutating, its numbers vanish silently and
every subsequent measurement is quietly wrong.

Nothing in the API-level tests actually pins that distinction, and it is easy to
believe they do: ``resolve_principal`` is an async dependency running in the
request's own context chain, so a ``set()`` *there* is visible and converting it
changes nothing. The difference only appears across a task boundary — which is
exactly where W3 writes: ``observability.stages.stage`` records from inside the
``asyncio.gather`` children of the query path's three (nested) fan-out sites. So
it is pinned here, directly, rather than inferred from those.
"""
import asyncio
import contextvars
import dataclasses
import logging

import pytest

from ragstack.observability.context import (
    CONTEXT_FIELDS,
    MISSING,
    RequestContext,
    RequestContextFilter,
    clear_context,
    current_context,
    set_context,
)


@pytest.fixture(autouse=True)
def _clean_context():
    """No request context before or after each test.

    The middleware never resets the contextvar (an exception handler runs after
    its ``finally`` and needs the id), so a context installed by one test
    survives into the next in the same thread. Without this, whether a test
    asserting "no context" passes depends on collection order — which is how it
    first showed up: green alone, red in the full suite.
    """
    clear_context()
    yield
    clear_context()


@pytest.mark.asyncio
async def test_in_place_mutation_in_a_gather_child_is_visible_to_the_parent():
    """The half the design relies on."""
    set_context(RequestContext(request_id="a" * 16))

    async def _child(value: str) -> None:
        ctx = current_context()
        assert ctx is not None, "a gather child should inherit the context"
        ctx.tenant = value

    await asyncio.gather(_child("acme"))

    parent = current_context()
    assert parent is not None
    assert parent.tenant == "acme", "an in-place mutation in a child did not reach the parent"


@pytest.mark.asyncio
async def test_set_in_a_gather_child_is_INVISIBLE_to_the_parent():
    """The half that makes the rule necessary rather than stylistic.

    This is the failure mode the module docstring warns about, demonstrated. If
    this test ever starts failing, ``ContextVar`` semantics changed and the
    single-mutable-object design can be simplified — but until then, a producer
    that re-``set()``s below the middleware silently loses its data.
    """
    original = RequestContext(request_id="b" * 16)
    set_context(original)

    async def _child() -> None:
        ctx = current_context()
        assert ctx is not None
        set_context(dataclasses.replace(ctx, tenant="acme"))
        assert current_context() is not original  # it worked, locally

    await asyncio.gather(_child())

    parent = current_context()
    assert parent is original, "a child's set() escaped its context"
    assert parent.tenant == "", "set() in a child must NOT propagate up (it did)"


@pytest.mark.asyncio
async def test_all_children_of_one_gather_share_the_same_object():
    """Concurrent legs accumulate into one place — the property that makes
    per-request totals across parallel retrieval legs possible at all."""
    set_context(RequestContext(request_id="c" * 16))
    seen: list[int] = []

    async def _child() -> None:
        ctx = current_context()
        assert ctx is not None
        seen.append(id(ctx))

    await asyncio.gather(*(_child() for _ in range(5)))

    parent = current_context()
    assert parent is not None
    assert set(seen) == {id(parent)}, "gather children saw different context objects"


@pytest.mark.asyncio
async def test_to_thread_propagates_the_context():
    """A3's fifth mechanic, first half: ``asyncio.to_thread`` copies the context,
    so FastAPI's sync-endpoint path (``anyio.to_thread.run_sync``) sees it."""
    set_context(RequestContext(request_id="d" * 16, tenant="acme"))

    def _sync() -> str | None:
        ctx = current_context()
        return None if ctx is None else ctx.tenant

    assert await asyncio.to_thread(_sync) == "acme"


@pytest.mark.asyncio
async def test_raw_run_in_executor_does_NOT_propagate_the_context():
    """A3's fifth mechanic, second half, and the reason it is written down.

    A raw ``loop.run_in_executor(ThreadPoolExecutor(), …)`` does **not** copy the
    context: the var reads as unset, anything context-dependent silently no-ops,
    and in W3 that time would vanish into ``self_ms`` with nothing to indicate it
    had. There are currently **zero** such calls in ``ragstack`` — this test
    exists so the behaviour is a documented, demonstrated fact rather than a
    claim in a docstring, for whoever is debugging a missing measurement later.
    """
    from concurrent.futures import ThreadPoolExecutor

    set_context(RequestContext(request_id="e" * 16, tenant="acme"))

    def _sync() -> str | None:
        ctx = current_context()
        return None if ctx is None else ctx.tenant

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert await loop.run_in_executor(pool, _sync) is None

        # And the documented workaround, so the fix is next to the trap.
        carried = contextvars.copy_context()
        assert await loop.run_in_executor(pool, carried.run, _sync) == "acme"


@pytest.mark.asyncio
async def test_a_fresh_context_per_request_does_not_leak_the_previous_one():
    """Risk 1 in the plan: a context installed at module scope, or reused, puts
    request N's data on request N+1 and every field is then quietly wrong."""
    first = RequestContext(request_id="f" * 16)
    set_context(first)
    first.tenant = "acme"

    second = RequestContext(request_id="0" * 16)
    set_context(second)

    ctx = current_context()
    assert ctx is second
    assert ctx.tenant == "", "the new request inherited the previous request's tenant"


def test_the_filter_fills_every_field_it_claims_to():
    """A record that never saw a request context must still carry every
    attribute the formatters read, or the formatter raises inside the logging
    machinery — where the traceback is nearly untraceable to its cause."""
    record = logging.LogRecord("x", logging.INFO, "f.py", 1, "m", (), None)
    assert RequestContextFilter().filter(record) is True

    for name in CONTEXT_FIELDS:
        assert getattr(record, name) == MISSING, f"{name} was not filled"
