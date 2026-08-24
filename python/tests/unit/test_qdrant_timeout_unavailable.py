"""A slow or unreachable Qdrant must surface as StoreUnavailable, not leak the
client's exception (which the API turned into a bare 500), and the configured
timeout must actually reach the client (unset, qdrant-client falls back to
httpx's 5 s default — the failure mode this guards against)."""
from __future__ import annotations

import pytest

pytest.importorskip("qdrant_client")

import httpx  # noqa: E402
from qdrant_client.http.exceptions import (  # noqa: E402
    ResponseHandlingException,
    UnexpectedResponse,
)

from ragstack.stores import qdrant as qdrant_mod  # noqa: E402
from ragstack.stores.errors import StoreUnavailable  # noqa: E402
from ragstack.stores.qdrant import QdrantVectorStore  # noqa: E402


class _TimingOutClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def query_points(self, **_: object) -> object:
        raise self.exc


def _store(exc: Exception, timeout: int | None = 30) -> QdrantVectorStore:
    s = QdrantVectorStore(url="http://qdrant.test:6333", collection="sfr_tok256", timeout=timeout)
    s._client = _TimingOutClient(exc)  # type: ignore[assignment]
    return s


@pytest.mark.asyncio
async def test_read_timeout_becomes_store_unavailable_with_the_reason():
    exc = ResponseHandlingException(httpx.ReadTimeout("timed out"))
    with pytest.raises(StoreUnavailable) as ei:
        await _store(exc).search([0.1, 0.2], top_k=3)
    err = ei.value
    assert err.store == "qdrant"
    msg = str(err)
    # Names the collection, the instance, the underlying cause and the knob.
    assert "sfr_tok256" in msg and "qdrant.test:6333" in msg
    assert "ReadTimeout" in msg
    assert "30s (QDRANT_TIMEOUT)" in msg
    assert err.__cause__ is exc


@pytest.mark.asyncio
async def test_server_side_error_also_becomes_store_unavailable():
    exc = UnexpectedResponse(status_code=503, reason_phrase="Service Unavailable", content=b"busy", headers=None)
    with pytest.raises(StoreUnavailable) as ei:
        await _store(exc).search([0.1, 0.2], top_k=3)
    assert "503" in str(ei.value) or "Service Unavailable" in str(ei.value)


@pytest.mark.asyncio
async def test_unset_timeout_is_named_as_the_client_default():
    exc = ResponseHandlingException(httpx.ReadTimeout("timed out"))
    with pytest.raises(StoreUnavailable) as ei:
        await _store(exc, timeout=None).search([0.1], top_k=1)
    assert "client default 5s" in str(ei.value)


@pytest.mark.asyncio
async def test_unrelated_errors_are_not_masked():
    # Only the client's ApiException family is "the store didn't answer"; a bug
    # in our own code must still propagate as itself.
    with pytest.raises(TypeError):
        await _store(TypeError("payload shape")).search([0.1], top_k=1)


def test_timeout_reaches_the_client(monkeypatch):
    seen: dict[str, object] = {}

    class _Capture:
        def __init__(self, **kw: object) -> None:
            seen.update(kw)

    monkeypatch.setattr(qdrant_mod, "AsyncQdrantClient", _Capture)
    QdrantVectorStore(url="http://x:1", collection="c", timeout=30)
    assert seen["timeout"] == 30


def test_settings_default_and_wiring():
    from ragstack.config import Settings

    assert Settings().qdrant_timeout == 30
    # deps.py must pass it through — a constructor without it silently reverts
    # to the 5 s default this whole change exists to remove.
    import inspect

    from ragstack.api import deps

    src = inspect.getsource(deps)
    assert src.count("timeout=settings.qdrant_timeout") == 2
