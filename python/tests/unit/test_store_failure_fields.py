"""Structured fields on a Qdrant store failure (#427 W2a).

The incident's log line was a good *sentence* and nothing else — you could read
it, you could not branch on it. These tests pin the two things that make it
machine-readable without making it any less readable:

* ``kind`` — the three-value discriminator the 503 body and the UI need;
* ``elapsed_s`` — how much time the failing call actually burned, which is
  **not** inferable from the timeout value.

And they pin the thing that must NOT change: ``_describe_failure``'s sentence,
verbatim. It names the collection, the URL, the error type, the applied timeout
*and* the setting that governs it, and it is what made #427 diagnosable in one
grep. If a refactor ever quietly reduces it to ``kind=timeout``, the assertion
below fails.
"""
from __future__ import annotations

import pytest

pytest.importorskip("qdrant_client")

import httpx  # noqa: E402
from qdrant_client.http.exceptions import (  # noqa: E402
    ResponseHandlingException,
    UnexpectedResponse,
)

from ragstack.stores.errors import (  # noqa: E402
    KIND_ERROR,
    KIND_TIMEOUT,
    KIND_UNREACHABLE,
    STORE_FAILURE_KINDS,
    StoreUnavailable,
)
from ragstack.stores.qdrant import QdrantVectorStore  # noqa: E402


class _FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def query_points(self, **_: object) -> object:
        raise self.exc


def _store(exc: Exception, timeout: int | None = 30) -> QdrantVectorStore:
    s = QdrantVectorStore(url="http://qdrant.test:6333", collection="sfr_tok256", timeout=timeout)
    s._client = _FailingClient(exc)  # type: ignore[assignment]
    return s


async def _fail(exc: Exception, timeout: int | None = 30) -> StoreUnavailable:
    with pytest.raises(StoreUnavailable) as ei:
        await _store(exc, timeout).search([0.1, 0.2], top_k=3)
    return ei.value


@pytest.mark.asyncio
async def test_read_timeout_is_kind_timeout_and_keeps_the_whole_sentence():
    err = await _fail(ResponseHandlingException(httpx.ReadTimeout("timed out")))

    assert err.kind == KIND_TIMEOUT
    assert err.elapsed_s is not None and err.elapsed_s >= 0

    # The message is preserved VERBATIM — the exact string the incident produced,
    # rebuilt here rather than substring-sniffed, so a reworded sentence fails.
    assert str(err) == (
        "qdrant search on 'sfr_tok256' at http://qdrant.test:6333 failed — "
        "ReadTimeout: timed out; per-request timeout is 30s (QDRANT_TIMEOUT)"
    )


@pytest.mark.asyncio
async def test_connect_error_is_unreachable_not_timeout():
    err = await _fail(ResponseHandlingException(httpx.ConnectError("connection refused")))
    assert err.kind == KIND_UNREACHABLE


@pytest.mark.asyncio
async def test_connect_timeout_is_unreachable_even_though_it_is_a_timeout():
    """The distinction the UI copy depends on, and the one an implementer gets
    wrong: ``httpx.ConnectTimeout`` **subclasses** ``httpx.TimeoutException``, so
    a mapping that checks "is it a timeout?" first classifies it ``timeout`` and
    the UI then tells the user "retry, the second read will be warm" about a
    store the request never reached.
    """
    exc = httpx.ConnectTimeout("timed out connecting")
    assert isinstance(exc, httpx.TimeoutException), "premise of this test"

    err = await _fail(ResponseHandlingException(exc))
    assert err.kind == KIND_UNREACHABLE


@pytest.mark.asyncio
async def test_server_side_5xx_is_kind_error():
    exc = UnexpectedResponse(
        status_code=503, reason_phrase="Service Unavailable", content=b"busy", headers=None
    )
    err = await _fail(exc)
    assert err.kind == KIND_ERROR
    # `error` is also the constructor's DEFAULT kind, so asserting it alone
    # would pass even with the qdrant-side derivation removed. `elapsed_s` is
    # what proves this raise site was actually rebuilt.
    assert err.elapsed_s is not None


@pytest.mark.asyncio
async def test_elapsed_is_measured_not_inferred_from_the_timeout():
    """A ConnectError against a 30 s bound fails in milliseconds. Reporting the
    *setting* as the elapsed time would make every failure look like a timeout,
    which is exactly the confusion #427 could not resolve after the fact."""
    err = await _fail(ResponseHandlingException(httpx.ConnectError("refused")), timeout=30)
    assert err.elapsed_s is not None
    assert err.elapsed_s < 1.0


def test_kind_is_required_so_no_raise_site_can_default_into_a_claim():
    """``kind`` has no default, on purpose.

    Any default would have to be one of the three values, and each is a specific
    factual claim — ``error`` asserts the store *answered*. A raise site that
    never thought about it would put that claim in the 503 body and, once W6
    lands, into the advice the user reads.

    It also keeps every ``kind ==`` assertion in this tree honest. With a
    default of ``error``, a test asserting ``kind == "error"`` passed even with
    the whole derivation deleted — vacuous, and exactly the shape this repo has
    shipped before.
    """
    with pytest.raises(TypeError):
        StoreUnavailable("neo4j", "boom")  # type: ignore[call-arg]

    # `elapsed_s` DOES default: "I did not time this call" is a real and honest
    # state, unlike "I did not think about what happened".
    err = StoreUnavailable("neo4j", "boom", kind=KIND_UNREACHABLE)
    assert err.kind in STORE_FAILURE_KINDS
    assert err.elapsed_s is None
    assert str(err) == "boom"
