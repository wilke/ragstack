"""Per-collection overview: GET /v1/collections reports both the vector count and
the text-index count (tenant-filtered), so the UI can show a vector↔text parity
check alongside each collection's provenance."""
import pytest

from ragstack.api import security
from ragstack.api.collections import CollectionEntry, CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN


class _CountStore:
    """A store double exposing only the tenant-filtered count probe."""

    def __init__(self, n: int) -> None:
        self._n = n

    async def count_tenants(self, tenants: list[str]) -> int:
        return self._n


@pytest.fixture(autouse=True)
def _keyless(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


@pytest.mark.asyncio
async def test_collection_reports_vector_and_text_counts(client):
    app.state.collections = CollectionRegistry(
        [
            CollectionEntry(
                id="default", label="d", collection="c", model="m", dim=8,
                chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
                is_shared_surface=True, retriever=None,
                vector_store=_CountStore(1000), text_index=_CountStore(998), embedder=None,
            )
        ],
        default_id="default",
    )
    body = (await client.get("/v1/collections")).json()
    c = body["collections"][0]
    assert c["count"] == 1000  # vector store
    assert c["text_count"] == 998  # text index — enables the parity badge (drift = 2)


@pytest.mark.asyncio
async def test_text_count_null_when_index_unavailable(client):
    # a text index without the probe (or that errors) degrades to null, not a 500
    app.state.collections = CollectionRegistry(
        [
            CollectionEntry(
                id="default", label="d", collection="c", model="m", dim=8,
                chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
                is_shared_surface=True, retriever=None,
                vector_store=_CountStore(5), text_index=None, embedder=None,
            )
        ],
        default_id="default",
    )
    c = (await client.get("/v1/collections")).json()["collections"][0]
    assert c["count"] == 5 and c["text_count"] is None
