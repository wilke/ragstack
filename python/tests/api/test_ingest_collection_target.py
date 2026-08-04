"""Phase 3 Step 4: POST /v1/ingest honors an optional `collection` target.

A targeted ingest indexes documents with that collection's bound
embedder/chunker/stores; an unknown id is a 404; omitting `collection` (or naming
the default) keeps the prebuilt app ingestor (backward compatible).
"""
from types import SimpleNamespace

import httpx
import pytest

from ragstack.api.collections import CollectionEntry
from ragstack.api.deps import _chunker_for, build_ingestor_for


def _entry(method: str = "fixed") -> CollectionEntry:
    return CollectionEntry(
        id="acme", label="acme", collection="physical_acme", model="m", dim=8,
        chunk_method=method, chunk_size=200, chunk_overlap=20, chunk_params={},
        is_default=False, retriever=object(),
        vector_store=object(), text_index=object(), embedder=object(),
    )


# --- builders (unit, offline) ---------------------------------------------- #

def test_chunker_for_rejects_semantic_without_an_embed_fn():
    # semantic methods embed while chunking, so they need a sync embed_fn.
    # build_ingestor_for now supplies a per-collection bridge (see
    # test_chunk_choice.py); called bare this must still raise rather than
    # silently chunk some other way.
    for m in ("semantic", "semantic_pooled"):
        with pytest.raises(ValueError):
            _chunker_for(_entry(m))


def test_chunker_for_fixed_builds():
    assert _chunker_for(_entry("fixed")) is not None


def test_build_ingestor_binds_collection_stores():
    e = _entry("fixed")
    app_state = SimpleNamespace(
        graph_store=None, kg_extractor=None, http_client=httpx.AsyncClient(), job_store=None
    )
    ing = build_ingestor_for(app_state, e)
    p = ing._pipeline
    # the pipeline writes into THIS collection's embedder + stores, not the defaults
    assert p.embedder is e.embedder
    assert p.vector_store is e.vector_store
    assert p.text_index is e.text_index
    # ...and it knows WHICH collection it writes into. The graph store is shared
    # across collections, so without this the triples it extracts would be
    # unstamped and its delete-prior would cross the collection boundary (#209).
    assert p.collection == e.collection


# --- endpoint --------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ingest_unknown_collection_is_404(client):
    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": "ghost"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ingest_default_collection_passes_through(client):
    # naming the default id uses the prebuilt ingestor (no per-collection build)
    r = await client.post("/v1/ingest", json={"source": "x.txt", "collection": "default"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ingest_without_collection_still_works(client):
    # backward compatibility: omitting `collection` is unchanged behavior
    r = await client.post("/v1/ingest", json={"source": "x.txt"})
    assert r.status_code == 200
