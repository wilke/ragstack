"""Unit tests for QdrantVectorStore.count_tenants — the tenant-scoped count that
backs /v1/stats/stores.

The load-bearing invariant: it must use a FILTERED ``client.count(count_filter=,
exact=True)``, never the whole-collection ``get_collection().points_count`` (which
would leak every tenant's chunk total to a non-admin), and it must fail closed on
an empty tenant list rather than issuing an unfiltered global count.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("qdrant_client")

from ragstack.stores.qdrant import QdrantVectorStore  # noqa: E402


class _CountClient:
    """Async stand-in recording count() calls; ``get_collection`` is a trap — if
    count_tenants ever reaches for the global points_count this test fails."""

    def __init__(self, count: int = 0) -> None:
        self._count = count
        self.count_calls: list[dict] = []

    async def count(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(count=self._count)

    async def get_collection(self, *a, **k):  # pragma: no cover - must not be called
        raise AssertionError(
            "count_tenants must not read get_collection().points_count "
            "(that is the whole-collection total — a cross-tenant leak)"
        )


def _store(client: _CountClient, *, collection: str = "c") -> QdrantVectorStore:
    store = QdrantVectorStore(collection=collection)
    store._client = client  # type: ignore[assignment]
    return store


@pytest.mark.asyncio
async def test_count_tenants_uses_filtered_exact_count():
    client = _CountClient(count=7)
    store = _store(client)

    n = await store.count_tenants(["acme", "public"])

    assert n == 7
    assert len(client.count_calls) == 1
    kwargs = client.count_calls[0]
    assert kwargs["exact"] is True
    assert kwargs["collection_name"] == "c"
    # A real filter is passed (scoped to the tenant_id terms), not None.
    assert kwargs["count_filter"] is not None


@pytest.mark.asyncio
async def test_count_tenants_empty_fails_closed_without_querying():
    client = _CountClient(count=999)
    store = _store(client)

    # An empty tenant list must NOT issue an (unfiltered → global) count.
    assert await store.count_tenants([]) == 0
    assert client.count_calls == []
