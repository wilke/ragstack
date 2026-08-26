"""Elasticsearch failure parity with Qdrant (#427 W2b).

Before this, ``ElasticsearchTextIndex`` had **no error handling on any read**.
An ES timeout on the BM25 leg of a hybrid query became a bare HTTP 500 with a
raw traceback, while the identical failure on the vector leg of the *same*
query produced a 503 naming the index, the URL, the error type and the timeout
knob. The #427 incident happened to hit the instrumented leg; had it hit this
one there would have been no line to grep and no issue to file.

These tests pin both halves of parity: the message *shape* and the ``kind``
derivation. They also pin what must NOT be converted — a 4xx ``ApiError`` is the
caller's problem and turning it into "elasticsearch unavailable, retry in 5s"
would be a regression dressed up as a fix.
"""
from __future__ import annotations

import pytest

pytest.importorskip("elasticsearch")

from elastic_transport import ApiResponseMeta, HttpHeaders  # noqa: E402
from elasticsearch import (  # noqa: E402
    ApiError,
    ConnectionTimeout,
    SerializationError,
)
from elasticsearch import (
    ConnectionError as EsConnectionError,
)

from ragstack.stores.elasticsearch import ElasticsearchTextIndex  # noqa: E402
from ragstack.stores.errors import (  # noqa: E402
    KIND_ERROR,
    KIND_TIMEOUT,
    KIND_UNREACHABLE,
    StoreUnavailable,
)


class _FailingES:
    """Raises on every outward call — one fake for every guarded method."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def search(self, **_: object) -> dict:
        raise self.exc

    async def count(self, **_: object) -> dict:
        raise self.exc

    async def bulk(self, **_: object) -> dict:
        raise self.exc

    async def close(self) -> None:  # pragma: no cover - not exercised
        pass


def _index(exc: BaseException, timeout: float | None = None) -> ElasticsearchTextIndex:
    ix = ElasticsearchTextIndex(
        url="http://es.test:9200", index="ragstack_lib_open_access", timeout=timeout
    )
    ix._es = _FailingES(exc)  # type: ignore[assignment]
    return ix


def _api_error(status: int) -> ApiError:
    meta = ApiResponseMeta(
        status=status, http_version="1.1", headers=HttpHeaders(), duration=0.0, node=None
    )
    return ApiError(message="boom", meta=meta, body={})


#: The index is a tenancy boundary — ``search`` fails closed without it.
TENANT_FILTER = {"tenant_id": ["t1"]}


async def _fail(exc: BaseException, timeout: float | None = None) -> StoreUnavailable:
    with pytest.raises(StoreUnavailable) as ei:
        await _index(exc, timeout).search("what is RAG?", top_k=3, filters=TENANT_FILTER)
    return ei.value


@pytest.mark.asyncio
async def test_connection_timeout_names_the_index_the_url_and_the_knob():
    err = await _fail(ConnectionTimeout("Connection timeout caused by: TimeoutError()"))

    assert err.store == "elasticsearch"
    assert err.kind == KIND_TIMEOUT
    assert err.elapsed_s is not None

    msg = str(err)
    # The same four things Qdrant's sentence carries, so one grep pattern reads
    # both legs: operation, index, instance, cause, applied bound + its setting.
    assert "elasticsearch search" in msg
    assert "'ragstack_lib_open_access'" in msg
    assert "es.test:9200" in msg
    assert "ConnectionTimeout" in msg
    assert "client default 10s (ELASTICSEARCH_TIMEOUT unset)" in msg
    # The causal detail lives on `.message`, not on str(exc) — losing it would
    # make this line strictly worse than the traceback it replaces.
    assert "TimeoutError" in msg


@pytest.mark.asyncio
async def test_configured_timeout_is_named_instead_of_the_client_default():
    err = await _fail(ConnectionTimeout("slow"), timeout=30)
    assert "30s (ELASTICSEARCH_TIMEOUT)" in str(err)
    assert "client default" not in str(err)


@pytest.mark.asyncio
async def test_connection_error_is_unreachable():
    err = await _fail(EsConnectionError("Connection refused"))
    assert err.kind == KIND_UNREACHABLE


@pytest.mark.asyncio
async def test_server_side_5xx_is_kind_error():
    err = await _fail(_api_error(503))
    assert err.kind == KIND_ERROR


@pytest.mark.asyncio
async def test_serialization_failure_is_kind_error():
    err = await _fail(SerializationError("unparseable"))
    assert err.kind == KIND_ERROR


@pytest.mark.asyncio
async def test_a_4xx_is_the_callers_problem_and_is_not_converted():
    """``index_not_found_exception`` (404) and a malformed query (400) are not
    outages. Reporting them as 503 "retry in 5s" would send the caller round a
    loop that can never succeed — so they propagate exactly as they did before.
    """
    for status in (400, 404):
        with pytest.raises(ApiError):
            await _index(_api_error(status)).search("q", top_k=1, filters=TENANT_FILTER)


@pytest.mark.asyncio
async def test_a_bug_in_our_own_parsing_still_surfaces_as_itself():
    # The guard is scoped to the round trip. Anything it does not positively
    # recognise as a store outage must keep reaching the 500 path, or a real bug
    # hides behind a soothing "retry later".
    with pytest.raises(TypeError):
        await _index(TypeError("payload shape")).search("q", top_k=1, filters=TENANT_FILTER)


@pytest.mark.asyncio
async def test_count_is_guarded_too():
    ix = _index(ConnectionTimeout("slow"))
    with pytest.raises(StoreUnavailable) as ei:
        await ix.count_tenants(["t1"])
    assert "elasticsearch count" in str(ei.value)


@pytest.mark.asyncio
async def test_list_documents_is_guarded_too():
    ix = _index(EsConnectionError("refused"))
    with pytest.raises(StoreUnavailable) as ei:
        await ix.list_documents(["t1"])
    assert ei.value.kind == KIND_UNREACHABLE
    assert "elasticsearch list_documents" in str(ei.value)


@pytest.mark.asyncio
async def test_bulk_index_is_guarded_too():
    """POST /v1/ingest is a user-facing request path; an ES outage mid-ingest was
    a bare 500 here as well."""
    from ragstack.models import Chunk

    ix = _index(ConnectionTimeout("slow"))
    chunk = Chunk(id="c1", doc_id="d1", content="hello", start_char=0, end_char=5, metadata={})
    with pytest.raises(StoreUnavailable) as ei:
        await ix.index([chunk])
    assert ei.value.kind == KIND_TIMEOUT
    assert "elasticsearch bulk index" in str(ei.value)


def test_timeout_reaches_the_client_and_deps_passes_it_through():
    import inspect

    from ragstack.api import deps
    from ragstack.config import Settings

    # Unset by default: the client keeps its own 10s, and no behaviour changes
    # for anyone who does not set the new knob.
    assert Settings().elasticsearch_timeout is None

    # The client stores it as a per-request default and applies it to every call
    # (it is NOT copied onto the node config, which keeps its own 10s — assert on
    # the value that actually governs a request).
    ix = ElasticsearchTextIndex(url="http://es.test:9200", index="i", timeout=30)
    assert ix._es._request_timeout == 30
    assert ElasticsearchTextIndex(url="http://es.test:9200", index="i")._timeout is None

    # A constructor without it silently reverts to the client default — the
    # failure mode the Qdrant side already guards against this way.
    assert "timeout=settings.elasticsearch_timeout" in inspect.getsource(deps)


def test_a_float_setting_renders_like_qdrants_int_one():
    """``ELASTICSEARCH_TIMEOUT`` is typed ``float | None``, so 30 arrives as
    ``30.0``. Matching the Qdrant message shape was the point of the whole
    method — one grep pattern reading both legs — and ``30.0s`` next to ``30s``
    quietly breaks it."""
    ix = _index(ConnectionTimeout("slow"), timeout=30.0)
    assert "30s (ELASTICSEARCH_TIMEOUT)" in ix._describe_failure("search", ConnectionTimeout("x"))
    assert "30.0s" not in ix._describe_failure("search", ConnectionTimeout("x"))


def test_the_named_client_default_is_read_from_the_library_not_hardcoded():
    """The message must name the **applied** bound, never a number we believe.

    ``_client_default_timeout_s`` reads ``NodeConfig``'s field default, so a
    library bump changes the message rather than making it false. This test is
    the other half: it also makes a bump *noticed*, instead of silently
    absorbed while every assertion stays green.
    """
    import dataclasses

    from elastic_transport import NodeConfig

    from ragstack.stores.elasticsearch import (
        _CLIENT_DEFAULT_TIMEOUT_FALLBACK_S,
        _client_default_timeout_s,
    )

    field = next(f for f in dataclasses.fields(NodeConfig) if f.name == "request_timeout")
    assert _client_default_timeout_s() == float(field.default)
    # If this fails, elastic_transport changed its default: update the fallback
    # constant and the `.env.example` note. The message itself is already right.
    assert _client_default_timeout_s() == _CLIENT_DEFAULT_TIMEOUT_FALLBACK_S
