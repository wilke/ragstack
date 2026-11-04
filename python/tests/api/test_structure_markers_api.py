"""#828 PR 4: the document-structure markers on the API.

* ``POST /v1/collections`` validates the four markers through THE one reader
  (``chunker_config.structure_from_params``): ``text_render="markdown-plain/1"``
  is recorded and forks the content address; an unknown value is 400; a marker
  this build cannot apply yet (``structure_cuts``, ``embed_heading_path``,
  ``structure_infer``) is 400, never recorded and ignored; a marker sent as
  ``null`` hashes like an absent one.
* Path A: the per-collection ingestor carries the entry's markers, and an
  in-process ingest into a ``text_render`` collection stores the stripped
  render; without the ``[markdown]`` extra it is refused with 503 whatever
  ``STRUCTURE_METADATA`` says.
* The GoWe dispatch backstop (§7 item 3) refuses a marked collection on a
  workflow whose ``embed_shard.py`` step does not bind ``collection_id`` —
  before anything is submitted — and is dormant on the shipped workflow
  (``pdf-ingest-scatter.cwl`` chunks with ``ingest_shard.py``).

In-memory doubles and a fake engine (``tests/api/conftest.py``,
``test_ingest_gowe_path``); no store, no network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ragstack.api import security
from ragstack.api.collections import CollectionRegistry
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN
from ragstack.config import settings
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from tests.api.test_chunk_cap_status import (  # noqa: F401 — fixtures
    _cleanup,
    principals,
)
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    AUTH,
    SUBJECT,
    _upload,
    gowe,
)
from tests.structure_support import hide_markdown_extra
from tests.unit.test_chunker_factory_parity import _entry

_HAS_EXTRA = importlib.util.find_spec("mdit_py_plugins") is not None
CWL_DIR = Path(__file__).resolve().parents[3] / "cwl"
EMB = {
    "id": "emb-sfr", "task": "embedding", "provider": "vllm",
    "base_urls": ["http://localhost:9100"], "model": "test/sfr", "dim": 8,
}
PLAIN = {"text_render": "markdown-plain/1"}


@pytest.fixture
def _admin(monkeypatch):
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", ROLE_ADMIN)


async def _create(client, monkeypatch, tmp_path, params, *, cid: str | None = None,
                  method: str = "fixed_token"):
    """Create a collection; return ``(response, recorded chunk_params | None)``."""
    f = tmp_path / "libs.json"
    monkeypatch.setattr("ragstack.config.settings.collections_file", str(f))
    reg = await client.post("/v1/admin/models/registry", json=EMB)
    assert reg.status_code == 201 or "already exists" in reg.text, reg.text
    chunk: dict = {"method": method, "params": params}
    if method not in ("semantic", "semantic_pooled"):
        chunk |= {"size": 256, "overlap": 32}
    body: dict = {"embedding": "emb-sfr", "chunk": chunk}
    if cid is not None:
        body["id"] = cid
    r = await client.post("/v1/collections", json=body)
    recorded = None
    if r.status_code == 201:
        name = r.json()["id"]
        recorded = next(d for d in json.loads(f.read_text())
                        if d["id"] == name)["chunk_params"]
    return r, recorded


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("method", ["fixed", "fixed_token", "sentence", "words", "semantic"])
async def test_text_render_is_accepted_on_every_method_and_recorded(
    client, _admin, monkeypatch, tmp_path, method
) -> None:
    r, recorded = await _create(client, monkeypatch, tmp_path, dict(PLAIN), cid=f"tr-{method}",
                                method=method)
    assert r.status_code == 201, r.text
    assert recorded is not None and recorded["text_render"] == "markdown-plain/1"


async def test_the_marker_forks_the_content_address_and_null_does_not(
    client, _admin, monkeypatch, tmp_path
) -> None:
    """Identity proven at the API: a marked spec is a new physical store; a
    marker sent as null is the unmarked spec (the SAME content address, so the
    second create is the existing collection: 409)."""
    plain = await _create(client, monkeypatch, tmp_path, {})
    assert plain[0].status_code == 201, plain[0].text
    nulled = await _create(client, monkeypatch, tmp_path, {"text_render": None})
    assert nulled[0].status_code == 409, nulled[0].text
    assert plain[0].json()["id"] in nulled[0].json()["detail"]
    marked = await _create(client, monkeypatch, tmp_path, dict(PLAIN))
    assert marked[0].status_code == 201, marked[0].text
    assert marked[0].json()["id"] != plain[0].json()["id"]
    assert plain[1] == {} and marked[1] == PLAIN


@pytest.mark.parametrize(("params", "needle"), [
    ({"text_render": "markdown-plain/2"}, "text_render"),
    ({"text_render": "html"}, "text_render"),
    ({"text_render": True}, "text_render"),
    ({"structure_cuts": "sections/2"}, "structure_cuts"),
    ({"embed_heading_path": "suffix/1"}, "embed_heading_path"),
    ({"structure_infer": "llm/1"}, "structure_infer"),
])
async def test_an_unknown_marker_value_is_a_400(client, _admin, monkeypatch, tmp_path,
                                                params, needle) -> None:
    """MUTATION: drop the reader call from ``_validate_chunk`` → the junk value
    is recorded and the create answers 201 → fails."""
    r, _ = await _create(client, monkeypatch, tmp_path, params, cid="bad-marker")
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]


@pytest.mark.parametrize("params", [
    {"structure_cuts": "sections/1"},
    {"embed_heading_path": "prefix/1"},
    {"structure_infer": "text-heuristic/1"},
])
async def test_a_marker_this_build_cannot_apply_is_a_400(client, _admin, monkeypatch, tmp_path,
                                                         params) -> None:
    """Present = applied (#829): a marker that would be ignored now and applied
    after #828 PR 7/8 would give one spec two chunkings, so it is refused."""
    r, _ = await _create(client, monkeypatch, tmp_path, params, cid="unapplied")
    assert r.status_code == 400, r.text
    assert "not yet applied" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# path A: the in-process ingest
# --------------------------------------------------------------------------- #

class _Embedder:
    async def embed(self, texts):  # noqa: ANN001
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _register_entry(monkeypatch, **over):
    monkeypatch.setattr(settings, "chunk_max_tokens", 4096)
    monkeypatch.setattr(settings, "chunk_token_counter", "estimate")
    monkeypatch.setattr(app.state, "kg_extractor", None, raising=False)
    entry = _entry("fixed", id="mdcol", embedding_endpoints=[],
                   vector_store=InMemoryVectorStore(), text_index=InMemoryTextIndex(),
                   embedder=_Embedder(), **over)
    registry = app.state.collections
    monkeypatch.setattr(app.state, "collections",
                        CollectionRegistry([*registry.entries(), entry],
                                           default_id=registry.default_id))
    return entry


def test_the_per_collection_pipeline_carries_the_entrys_markers(monkeypatch) -> None:
    from types import SimpleNamespace

    from ragstack.api import deps

    monkeypatch.setattr(settings, "chunk_max_tokens", 4096)
    monkeypatch.setattr(settings, "chunk_token_counter", "estimate")
    state = SimpleNamespace(graph_store=None, kg_extractor=None, http_client=None,
                            job_store=None)
    marked = deps.build_ingestor_for(state, _entry("fixed", chunk_params=dict(PLAIN),
                                                   embedding_endpoints=[]))._pipeline
    plain = deps.build_ingestor_for(state, _entry("fixed", embedding_endpoints=[]))._pipeline
    assert marked.structure_markers.text_render == "markdown-plain/1"
    assert not plain.structure_markers
    # text_render is not a "needs structure" marker: the setting still decides stamping.
    assert marked.structure_metadata is False
    with pytest.raises(ValueError, match="not yet applied"):
        deps.build_ingestor_for(state, _entry("fixed", chunk_params={
            "structure_cuts": "sections/1"}, embedding_endpoints=[]))


@pytest.mark.skipif(not _HAS_EXTRA, reason="the [markdown] extra is not installed")
async def test_an_in_process_ingest_stores_the_stripped_render(client, monkeypatch,
                                                               tmp_path) -> None:
    entry = _register_entry(monkeypatch, chunk_params=dict(PLAIN))
    src = tmp_path / "page.md"
    src.write_text("# Title\n\nSee [docs](https://d.example/x) and **bold**.\n",
                   encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src), "collection": "mdcol"})
    assert r.status_code == 200, r.text
    stored = [c.content for c in entry.vector_store._chunks]
    assert stored == ["Title\n\nSee docs (https://d.example/x) and bold."]


async def test_a_marked_in_process_ingest_is_503_without_the_extra_whatever_the_setting(
    client, monkeypatch, tmp_path
) -> None:
    """``text_render`` needs the parser whatever ``STRUCTURE_METADATA`` says
    (it IS the adapter's output). MUTATION: read only ``structure_metadata`` in
    ``_refuse_missing_structure_backend`` → the job is accepted → fails."""
    hide_markdown_extra(monkeypatch)
    monkeypatch.setattr(settings, "structure_metadata", "off")
    _register_entry(monkeypatch, chunk_params=dict(PLAIN))
    src = tmp_path / "notes.txt"  # configuration-level: the suffix does not matter
    src.write_text("plain text\n", encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src), "collection": "mdcol"})
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "[markdown]" in detail and "text_render" in detail


# --------------------------------------------------------------------------- #
# the GoWe dispatch backstop (§7 item 3)
# --------------------------------------------------------------------------- #

_EMBED_STEP = (
    "cwlVersion: v1.2\nclass: Workflow\ninputs:\n  pdfs: File[]\n"
    "  collection_id: ['null', string]\noutputs: {{}}\nsteps:\n  embed:\n"
    "    in: {in_}\n    out: []\n    run:\n      class: CommandLineTool\n"
    "      baseCommand: [python, /opt/ragstack/scripts/embed_shard.py]\n"
    "      inputs:\n        shard: File\n        collection_id:\n"
    "          type: ['null', string]\n          inputBinding: {{prefix: --collection-id}}\n"
    "      outputs: {{}}\n"
)
UNWIRED = _EMBED_STEP.format(in_="{shard: pdfs}")
WIRED = _EMBED_STEP.format(in_="{shard: pdfs, collection_id: collection_id}")


def _mark(monkeypatch, params) -> None:
    entry = app.state.collections.resolve("lib1")
    monkeypatch.setattr(entry, "chunk_params", params)


async def test_a_marked_collection_on_an_unwired_embed_workflow_is_refused_before_submit(
    client, gowe, monkeypatch  # noqa: F811
) -> None:
    """MUTATION: drop ``_refuse_marked_on_unwired_workflow`` from the upload
    path → the submission is made → fails."""
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", UNWIRED)
    _mark(monkeypatch, dict(PLAIN))
    r = await _upload(client, "a.pdf")
    assert r.status_code == 422, r.text
    assert "collection_id" in r.json()["detail"] and "text_render" in r.json()["detail"]
    assert not gowe["engine"].submissions


@pytest.mark.parametrize("case", ["wired", "unmarked", "shipped"])
async def test_the_backstop_lets_through_what_it_cannot_refuse(
    client, gowe, monkeypatch, case  # noqa: F811
) -> None:
    """A wired embed step, an unmarked collection, and — dormant today — the
    shipped API workflow, whose chunk step is ingest_shard.py."""
    if case == "shipped":
        path = CWL_DIR / "pdf-ingest-scatter.cwl"
        if not path.exists():
            pytest.skip("no cwl/ in this checkout")
        cwl = path.read_text(encoding="utf-8")
    else:
        cwl = WIRED if case == "wired" else UNWIRED
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", cwl)
    if case != "unmarked":
        _mark(monkeypatch, dict(PLAIN))
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    assert gowe["engine"].submissions


async def test_the_by_reference_ingest_is_refused_the_same_way_before_submit(
    client, gowe, monkeypatch  # noqa: F811
) -> None:
    """The twin of the upload test, on ``POST /v1/ingest`` with a Workspace
    reference. MUTATION: drop the gate call from that endpoint → the
    submission is made → fails."""
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", UNWIRED)
    _mark(monkeypatch, dict(PLAIN))
    r = await client.post("/v1/ingest", headers=AUTH, json={
        "source": f"ws:///{SUBJECT}/home/papers/x.pdf", "collection": "lib1"})
    assert r.status_code == 422, r.text
    assert "collection_id" in r.json()["detail"]
    assert not gowe["engine"].submissions
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", WIRED)
    r = await client.post("/v1/ingest", headers=AUTH, json={
        "source": f"ws:///{SUBJECT}/home/papers/x.pdf", "collection": "lib1"})
    assert r.status_code == 200, r.text
    assert gowe["engine"].submissions


# --------------------------------------------------------------------------- #
# GoWe deployments refuse markers at create until the repin step R
# --------------------------------------------------------------------------- #

async def test_a_gowe_deployment_refuses_a_marker_at_create_until_the_repin(
    client, _admin, monkeypatch, tmp_path
) -> None:
    """Until R, the GoWe workers' image predates the markers and would ingest a
    marked collection unmarked. Flip ``GOWE_STRUCTURE_MARKERS_SUPPORTED`` (and
    this pin) at R. MUTATION: drop the create-time refusal → 201 → fails."""
    from ragstack.api.routers import collections as coll

    assert coll.GOWE_STRUCTURE_MARKERS_SUPPORTED is False
    monkeypatch.setattr(settings, "ingest_backend", "gowe")
    r, recorded = await _create(client, monkeypatch, tmp_path, dict(PLAIN), cid="gowe-marked")
    assert r.status_code == 400, r.text
    assert "repin" in r.json()["detail"] and recorded is None
    plain_r, _ = await _create(client, monkeypatch, tmp_path, {}, cid="gowe-plain")
    assert plain_r.status_code == 201, plain_r.text
    monkeypatch.setattr(coll, "GOWE_STRUCTURE_MARKERS_SUPPORTED", True)  # what R does
    r, recorded = await _create(client, monkeypatch, tmp_path, dict(PLAIN), cid="gowe-r")
    assert r.status_code == 201 and recorded == PLAIN
