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
                  method: str = "fixed_token", overlap: int | None = 32):
    """Create a collection; return ``(response, recorded chunk_params | None)``."""
    f = tmp_path / "libs.json"
    monkeypatch.setattr("ragstack.config.settings.collections_file", str(f))
    reg = await client.post("/v1/admin/models/registry", json=EMB)
    assert reg.status_code == 201 or "already exists" in reg.text, reg.text
    chunk: dict = {"method": method, "params": params}
    if method not in ("semantic", "semantic_pooled"):
        chunk |= {"size": 256}
        if overlap is not None:
            chunk["overlap"] = overlap
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
    {"structure_infer": "text-heuristic/1"},
])
async def test_a_marker_this_build_cannot_apply_is_a_400(client, _admin, monkeypatch, tmp_path,
                                                         params) -> None:
    """Present = applied (#829): a marker that would be ignored now and applied
    after #828 PR 8 would give one spec two chunkings, so it is refused.
    (``structure_cuts`` and ``embed_heading_path`` are applied since PR 7.)"""
    r, _ = await _create(client, monkeypatch, tmp_path, params, cid="unapplied")
    assert r.status_code == 400, r.text
    assert "not yet applied" in r.json()["detail"]


async def test_structure_cuts_and_embed_heading_path_are_accepted_and_recorded(
        client, _admin, monkeypatch, tmp_path) -> None:
    """#828 PR 7 applies both: a create records them as sent."""
    params = {"structure_cuts": "sections/1", "embed_heading_path": "prefix/1"}
    r, recorded = await _create(client, monkeypatch, tmp_path, params, cid="cut-prefix")
    assert r.status_code == 201, r.text
    assert recorded == params


async def test_a_sentence_tolerance_under_cuts_is_recorded_concretely(
        client, _admin, monkeypatch, tmp_path) -> None:
    """Owner decision 2026-11-04: both tolerance keys recorded at create."""
    params = {"structure_cuts": "sections/1", "size_unit": "tokens", "size_tolerance": 0.25}
    r, recorded = await _create(client, monkeypatch, tmp_path, params, cid="sent-tol",
                                method="sentence", overlap=0)
    assert r.status_code == 201, r.text
    assert recorded == {**params, "min_piece": 64}


async def test_a_sentence_tolerance_with_overlap_omitted_records_0_and_ingests(
        client, _admin, monkeypatch, tmp_path) -> None:
    """Review S1: an omitted overlap used to be recorded as None; every ingest
    then filled in CHUNK_OVERLAP (64) and the chunker refused it. It is
    recorded as 0, and the per-collection chunker builds. MUTATION: drop the
    0 → the recorded overlap is None and the build raises → fails."""
    from ragstack.api import deps

    params = {"structure_cuts": "sections/1", "size_unit": "tokens", "size_tolerance": 0.25}
    r, recorded = await _create(client, monkeypatch, tmp_path, params, cid="sent-tol-noov",
                                method="sentence", overlap=None)
    assert r.status_code == 201, r.text
    [row] = [d for d in json.loads((tmp_path / "libs.json").read_text())
             if d["id"] == r.json()["id"]]
    assert row["chunk_overlap"] == 0
    monkeypatch.setattr(settings, "chunk_overlap", 64)
    monkeypatch.setattr(settings, "chunk_max_tokens", 4096)
    monkeypatch.setattr(settings, "chunk_token_counter", "estimate")
    entry = _entry("sentence", id="noov", chunk_size=row["chunk_size"],
                   chunk_overlap=row["chunk_overlap"], chunk_params=row["chunk_params"],
                   embedding_endpoints=[])
    chunker = deps._chunker_for(entry)
    assert chunker.size_tolerance == 0.25 and chunker.chunk_overlap == 0


@pytest.mark.parametrize(("params", "overlap", "needle"), [
    ({"size_unit": "tokens", "size_tolerance": 0.25}, 0, "applies only to"),
    ({"structure_cuts": "sections/1", "size_tolerance": 0.25}, 0, "size_unit='tokens'"),
    ({"structure_cuts": "sections/1", "size_unit": "tokens", "size_tolerance": 0.25}, 32,
     "overlap 0"),
    ({"structure_cuts": "sections/1", "size_unit": "tokens", "min_piece": 999}, 0,
     "min_piece"),
])
async def test_a_sentence_tolerance_it_cannot_honour_is_a_400(
        client, _admin, monkeypatch, tmp_path, params, overlap, needle) -> None:
    r, _ = await _create(client, monkeypatch, tmp_path, params, cid="sent-bad",
                         method="sentence", overlap=overlap)
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]


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
            "structure_infer": "text-heuristic/1"}, embedding_endpoints=[]))
    # #828 PR 7: structure_cuts is applied, and it forces the structure step.
    cut = deps.build_ingestor_for(state, _entry("fixed", chunk_params={
        "structure_cuts": "sections/1"}, embedding_endpoints=[]))._pipeline
    assert cut.structure_markers.structure_cuts == "sections/1"
    assert cut.structure_metadata is True


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


async def _ingest(client, endpoint: str):
    """Ingest one file through ``endpoint`` (``upload`` or ``reference``);
    return ``(status, the success status of that endpoint)``."""
    if endpoint == "upload":
        return (await _upload(client, "a.pdf")).status_code, 202
    r = await client.post("/v1/ingest", headers=AUTH, json={
        "source": f"ws:///{SUBJECT}/home/papers/x.pdf", "collection": "lib1"})
    return r.status_code, 200


def _before_the_repin(monkeypatch) -> None:
    """A tree whose shipped CWLs name a tools image older than the repin step R
    (the flag's value before R). The shipped default is ``True`` since R (pinned
    below); the refusals it controls stay in place for such a tree."""
    from ragstack.ingestion import chunker_config

    monkeypatch.setattr(chunker_config, "GOWE_STRUCTURE_MARKERS_SUPPORTED", False)


def _the_shipped_flag(monkeypatch) -> None:
    from ragstack.ingestion import chunker_config

    monkeypatch.setattr(chunker_config, "GOWE_STRUCTURE_MARKERS_SUPPORTED", True)


def test_the_shipped_tree_supports_markers_on_gowe_since_the_repin() -> None:
    """The repin step R (#828 §12) flipped this with the CWL wiring: every
    shipped workflow names a tools image whose shard tools apply the markers,
    and the operator workflows' embed steps bind ``collection_id``."""
    from ragstack.ingestion import chunker_config

    assert chunker_config.GOWE_STRUCTURE_MARKERS_SUPPORTED is True


def _shipped_scatter() -> str:
    path = CWL_DIR / "pdf-ingest-scatter.cwl"
    if not path.exists():
        pytest.skip("no cwl/ in this checkout")
    return path.read_text(encoding="utf-8")


#: A wired embed workflow that also builds and applies a glossary file.
WIRED_GLOSSARY = WIRED + "# glossary below\n" + (
    "  glossary:\n    in: {shard: pdfs}\n    out: []\n    run:\n"
    "      class: CommandLineTool\n"
    "      baseCommand: [python, /opt/ragstack/scripts/glossary_step.py]\n"
    "      inputs: {shard: File}\n      outputs: {}\n")
ENDPOINTS = ["upload", "reference"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_before_the_repin_any_marked_entry_is_refused_at_dispatch(
    client, gowe, monkeypatch, endpoint  # noqa: F811
) -> None:
    """An entry that did not come through create (seed_from_json, a
    collections file, a registry shared with an in-process deployment) is
    caught here, even on a wired workflow. MUTATION: drop the
    ``GOWE_STRUCTURE_MARKERS_SUPPORTED`` check from the gate → submitted → fails."""
    _before_the_repin(monkeypatch)
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", WIRED)
    _mark(monkeypatch, dict(PLAIN))
    status, _ok = await _ingest(client, endpoint)
    assert status == 422
    assert not gowe["engine"].submissions


@pytest.mark.parametrize("params", [{"structure_cuts": "sections/1"},
                                    {"embed_heading_path": "prefix/1"}],
                         ids=["structure_cuts", "embed_heading_path"])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_before_the_repin_the_pr7_markers_are_refused_at_dispatch(
    client, gowe, monkeypatch, endpoint, params  # noqa: F811
) -> None:
    """#828 PR 7: the dispatch gate refuses ``structure_cuts`` and
    ``embed_heading_path`` before R, on both endpoints, even on a wired
    workflow — and after R the wired workflow is dispatched. MUTATION: gate
    on ``text_render`` only → submitted → fails."""
    _before_the_repin(monkeypatch)
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", WIRED)
    _mark(monkeypatch, dict(params))
    status, ok = await _ingest(client, endpoint)
    assert status == 422
    assert not gowe["engine"].submissions
    _the_shipped_flag(monkeypatch)
    status, ok = await _ingest(client, endpoint)
    assert status == ok and gowe["engine"].submissions


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_an_unmarked_collection_is_dispatched_before_the_repin(
    client, gowe, monkeypatch, endpoint  # noqa: F811
) -> None:
    _before_the_repin(monkeypatch)
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", UNWIRED)
    status, ok = await _ingest(client, endpoint)
    assert status == ok and gowe["engine"].submissions


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_after_the_repin_an_unwired_embed_workflow_is_refused(
    client, gowe, monkeypatch, endpoint  # noqa: F811
) -> None:
    """MUTATION: drop the gate call from either endpoint, or its wiring check
    → the submission is made → fails."""
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", UNWIRED)
    _mark(monkeypatch, dict(PLAIN))
    status, _ok = await _ingest(client, endpoint)
    assert status == 422
    assert not gowe["engine"].submissions
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", WIRED)
    status, ok = await _ingest(client, endpoint)
    assert status == ok and gowe["engine"].submissions


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("which", ["fixture", "shipped"])
async def test_after_the_repin_text_render_on_a_glossary_workflow_is_refused(
    client, gowe, monkeypatch, endpoint, which  # noqa: F811
) -> None:
    """The API's own scatter workflow builds and applies a glossary file.
    MUTATION: drop the glossary check from the gate → submitted → fails."""
    cwl = WIRED_GLOSSARY if which == "fixture" else _shipped_scatter()
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", cwl)
    _mark(monkeypatch, dict(PLAIN))
    status, _ok = await _ingest(client, endpoint)
    assert status == 422
    assert not gowe["engine"].submissions


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_after_the_repin_the_wiring_check_is_dormant_on_the_shipped_scatter(
    client, gowe, monkeypatch, endpoint  # noqa: F811
) -> None:
    """Its chunk step is ingest_shard.py: no embed step to wire. (A marker
    other than text_render, from a hand-edited entry, so the glossary check
    does not apply.)"""
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", _shipped_scatter())
    _mark(monkeypatch, {"structure_cuts": "sections/1"})
    status, ok = await _ingest(client, endpoint)
    assert status == ok and gowe["engine"].submissions


async def test_the_refusals_name_their_reason(client, gowe, monkeypatch) -> None:  # noqa: F811
    _before_the_repin(monkeypatch)
    _mark(monkeypatch, dict(PLAIN))
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", WIRED)
    r = await _upload(client, "a.pdf")
    assert r.status_code == 422 and "repin" in r.json()["detail"]
    _the_shipped_flag(monkeypatch)
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", UNWIRED)
    r = await _upload(client, "a.pdf")
    assert r.status_code == 422 and "collection_id" in r.json()["detail"]
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", WIRED_GLOSSARY)
    r = await _upload(client, "a.pdf")
    assert r.status_code == 422 and "glossary file" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# GoWe deployments refused markers at create until the repin step R; a tree
# whose CWLs name an older image (the flag set back to False) still does
# --------------------------------------------------------------------------- #

async def test_a_gowe_deployment_accepts_a_marker_at_create_since_the_repin(
    client, _admin, monkeypatch, tmp_path
) -> None:
    """Before R, the GoWe workers' image predated the markers and would have
    ingested a marked collection unmarked, so create refused; R flipped
    ``chunker_config.GOWE_STRUCTURE_MARKERS_SUPPORTED``. With the flag set back
    (an older tree) the refusal still holds. MUTATION: drop the create-time
    refusal → 201 under the old flag → fails; leave the flag False → the
    shipped create is 400 → fails."""
    from ragstack.ingestion import chunker_config

    assert chunker_config.GOWE_STRUCTURE_MARKERS_SUPPORTED is True
    monkeypatch.setattr(settings, "ingest_backend", "gowe")
    _before_the_repin(monkeypatch)
    r, recorded = await _create(client, monkeypatch, tmp_path, dict(PLAIN), cid="gowe-marked")
    assert r.status_code == 400, r.text
    assert "repin" in r.json()["detail"] and recorded is None
    plain_r, _ = await _create(client, monkeypatch, tmp_path, {}, cid="gowe-plain")
    assert plain_r.status_code == 201, plain_r.text
    _the_shipped_flag(monkeypatch)
    r, recorded = await _create(client, monkeypatch, tmp_path, dict(PLAIN), cid="gowe-r")
    assert r.status_code == 201 and recorded == PLAIN


@pytest.mark.parametrize(("params", "method", "overlap"), [
    ({"text_render": "markdown-plain/1"}, "fixed_token", 32),
    ({"structure_cuts": "sections/1"}, "fixed_token", 32),
    ({"embed_heading_path": "prefix/1"}, "fixed_token", 32),
    ({"structure_infer": "text-heuristic/1"}, "fixed_token", 32),
    ({"structure_cuts": "sections/1", "size_unit": "tokens", "size_tolerance": 0.25,
      "min_piece": 64}, "sentence", 0),
], ids=["text_render", "structure_cuts", "embed_heading_path", "structure_infer",
        "sentence-tolerance"])
async def test_the_gowe_repin_refusal_covers_the_pr7_markers(
    client, _admin, monkeypatch, tmp_path, params, method, overlap
) -> None:
    """Every marker key is refused on a GoWe deployment under the pre-R flag —
    PR 7's two as well as text_render (and the sentence tolerance, which needs
    structure_cuts). ``structure_infer`` is still refused earlier, as not yet
    applied (PR 8). MUTATION: refuse ``text_render`` only → fails."""
    from ragstack.ingestion import chunker_config as cc

    assert cc.GOWE_STRUCTURE_MARKERS_SUPPORTED is True
    monkeypatch.setattr(settings, "ingest_backend", "gowe")
    _before_the_repin(monkeypatch)
    r, recorded = await _create(client, monkeypatch, tmp_path, dict(params), cid="gowe-pr7",
                                method=method, overlap=overlap)
    assert r.status_code == 400, r.text
    assert recorded is None
    want = "not yet applied" if "structure_infer" in params else "repin"
    assert want in r.json()["detail"]
    # What R did: the applied markers are then accepted on GoWe.
    _the_shipped_flag(monkeypatch)
    r, _ = await _create(client, monkeypatch, tmp_path, dict(params), cid="gowe-pr7-r",
                         method=method, overlap=overlap)
    assert (r.status_code == 400) is ("structure_infer" in params), r.text
