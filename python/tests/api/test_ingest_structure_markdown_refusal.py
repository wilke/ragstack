"""#828 PR 2, the API half of "a missing parser is a refusal" (§6).

A LOCAL ingest whose pipeline has the structure step on, on a server without
the ``[markdown]`` extra, is refused with 503 naming the extra — before a job
exists, on the shared surface and on a per-collection ingestor alike, and
whatever suffix the source has (the check is configuration-level). Off, or
with the extra present, the ingest is accepted as before. The GoWe path does
not parse in the API process (its tools image refuses at configuration).

In-memory doubles only (``tests/api/conftest.py``); no store, no network.
"""
from __future__ import annotations

import importlib.util

import pytest

from ragstack.api.collections import CollectionRegistry
from ragstack.api.main import app
from ragstack.config import settings
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.structure_support import hide_markdown_extra
from tests.unit.test_chunker_factory_parity import _entry

_HAS_EXTRA = importlib.util.find_spec("mdit_py_plugins") is not None


def _md(tmp_path, name="page.md"):
    src = tmp_path / name
    src.write_text("# Title\n\nBody.\n", encoding="utf-8")
    return src


async def _jobs():
    return await app.state.job_store.list_jobs(limit=100)


@pytest.mark.parametrize("name", ["page.md", "notes.txt"])
async def test_shared_surface_ingest_is_503_when_the_extra_is_missing(
    client, monkeypatch, tmp_path, name
) -> None:
    """MUTATION: drop ``_refuse_missing_structure_backend`` from the surface
    branch → the job is accepted (and its ``.md`` item would fail later, or a
    ``.txt`` batch would pass by luck) → fails."""
    hide_markdown_extra(monkeypatch)
    monkeypatch.setattr(app.state.pipeline, "structure_metadata", True)
    before = len(await _jobs())
    r = await client.post("/v1/ingest", json={"source": str(_md(tmp_path, name))})
    assert r.status_code == 503, r.text
    assert "[markdown]" in r.json()["detail"]
    assert "retry-after" not in r.headers  # operator-only, like the tokenizer 503
    assert len(await _jobs()) == before, "no job may be accepted that cannot run"


async def test_shared_surface_ingest_with_the_step_off_is_accepted(
    client, monkeypatch, tmp_path
) -> None:
    hide_markdown_extra(monkeypatch)
    assert app.state.pipeline.structure_metadata is False
    r = await client.post("/v1/ingest", json={"source": str(_md(tmp_path))})
    assert r.status_code == 200, r.text


@pytest.mark.skipif(not _HAS_EXTRA, reason="the [markdown] extra is not installed")
async def test_shared_surface_ingest_with_the_extra_is_accepted(
    client, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(app.state.pipeline, "structure_metadata", True)
    r = await client.post("/v1/ingest", json={"source": str(_md(tmp_path))})
    assert r.status_code == 200, r.text


async def test_per_collection_ingest_is_503_when_the_extra_is_missing(
    client, monkeypatch, tmp_path
) -> None:
    """The per-collection ingestor ``build_ingestor_for`` builds reads
    ``STRUCTURE_METADATA``; the refusal reads the pipeline it built."""
    hide_markdown_extra(monkeypatch)
    monkeypatch.setattr(settings, "structure_metadata", "on")
    monkeypatch.setattr(settings, "chunk_max_tokens", 4096)
    monkeypatch.setattr(settings, "chunk_token_counter", "estimate")
    monkeypatch.setattr(app.state, "kg_extractor", None, raising=False)
    entry = _entry("fixed", id="mdcol", embedding_endpoints=[],
                   vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex())
    registry = app.state.collections
    monkeypatch.setattr(app.state, "collections",
                        CollectionRegistry([*registry.entries(), entry],
                                           default_id=registry.default_id))
    r = await client.post("/v1/ingest", json={"source": str(_md(tmp_path)),
                                              "collection": "mdcol"})
    assert r.status_code == 503, r.text
    assert "[markdown]" in r.json()["detail"]

    monkeypatch.setattr(settings, "structure_metadata", "off")
    r = await client.post("/v1/ingest", json={"source": str(_md(tmp_path)),
                                              "collection": "mdcol"})
    assert r.status_code == 200, r.text
