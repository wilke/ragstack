"""#828 PR 4: the four document-structure markers and THE one reader.

``chunker_config.structure_from_params(method, params)`` reads ``text_render``,
``structure_cuts``, ``embed_heading_path`` and ``structure_infer`` out of a
collection's ``chunk_params`` under the #829 discipline: absent = today's
behaviour, present = applied, unknown = refused; a marker this build cannot
apply yet is refused rather than ignored. Here: the reader itself, that a
marker is collection identity (``chunk_descriptor`` → ``spec_hash`` → the
physical name), the pipeline seam PR 7 builds on (``needs_structure`` turns the
structure step on), and the bulk tools' configuration refusals — an unknown or
unapplied marker exits 2, and a marker that needs the parser exits 2 without
the ``[markdown]`` extra WHATEVER ``--structure-metadata`` says.

No parser needed (the extra is hidden where it matters); the registry is a
scratch sqlite file selected by name; store URLs are the dead port.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ragstack.collection_store import CollectionSpec
from ragstack.ingestion.chunker_config import (
    NO_STRUCTURE_MARKERS,
    STRUCTURE_MARKER_PARAMS,
    STRUCTURE_MARKER_VALUES,
    UNAPPLIED_STRUCTURE_MARKERS,
    StructureMarkers,
    build_chunker,
    structure_from_params,
)
from ragstack.ingestion.chunkers import CHUNK_METHODS
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.structure import (
    StructureBackendUnavailable,
    apply_text_render,
    require_structure_backends,
    require_structure_backends_or_exit,
    structure_step_on,
)
from ragstack.models import Document
from ragstack.provenance import chunk_descriptor
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.qdrant import collection_name
from tests.structure_support import hide_markdown_extra, record, write_shard
from tests.unit.test_embed_shard_registry import (  # noqa: F401 — the `registry` fixture
    CID,
    DEAD,
    REG,
    _spec,
    registry,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import embed_shard  # noqa: E402
import ingest_jsonl  # noqa: E402
import ingest_shard  # noqa: E402

ALL = {"text_render": "markdown-plain/1", "structure_cuts": "sections/1",
       "embed_heading_path": "prefix/1", "structure_infer": "text-heuristic/1"}


# --------------------------------------------------------------------------- #
# the reader
# --------------------------------------------------------------------------- #

def test_the_marker_table_is_the_designed_one() -> None:
    """§7 / §9.5, written out by hand."""
    assert STRUCTURE_MARKER_VALUES == {
        "text_render": ("markdown-plain/1",), "structure_cuts": ("sections/1",),
        "embed_heading_path": ("prefix/1",), "structure_infer": ("text-heuristic/1",),
    }
    assert STRUCTURE_MARKER_PARAMS == ("text_render", "structure_cuts",
                                       "embed_heading_path", "structure_infer")
    # PR 7 applies structure_cuts and embed_heading_path; structure_infer is PR 8.
    assert UNAPPLIED_STRUCTURE_MARKERS == {"structure_infer"}


@pytest.mark.parametrize("params", [None, {}, {"size_unit": "tokens"},
                                    dict.fromkeys(ALL)])
def test_absent_or_null_is_no_marker(params) -> None:
    m = structure_from_params("fixed_token", params)
    assert m == NO_STRUCTURE_MARKERS and not m and m.applied() == {}
    assert not m.needs_structure and not m.requires_parser


def test_all_four_are_parsed_and_returned_in_order() -> None:
    m = structure_from_params("words", dict(ALL), unapplied=())
    assert m == StructureMarkers(**ALL)
    assert list(m.applied()) == list(STRUCTURE_MARKER_PARAMS)
    assert m.needs_structure and m.requires_parser


@pytest.mark.parametrize("method", CHUNK_METHODS)
def test_every_method_accepts_text_render(method) -> None:
    m = structure_from_params(method, {"text_render": "markdown-plain/1"})
    assert m.text_render == "markdown-plain/1" and m.applied() == {"text_render":
                                                                   "markdown-plain/1"}
    # text_render changes the text, not the need for structure: the setting
    # still decides stamping, but the parser is required.
    assert not m.needs_structure and m.requires_parser


@pytest.mark.parametrize("key", STRUCTURE_MARKER_PARAMS)
@pytest.mark.parametrize("value", ["bogus", "", 1, True, ["markdown-plain/1"], {"v": 1}])
def test_an_unknown_value_is_refused_naming_the_key(key, value) -> None:
    with pytest.raises(ValueError, match=key):
        structure_from_params("fixed", {key: value}, unapplied=())


@pytest.mark.parametrize("key", sorted(UNAPPLIED_STRUCTURE_MARKERS))
def test_a_marker_this_build_cannot_apply_is_refused_by_default(key) -> None:
    """MUTATION: empty ``UNAPPLIED_STRUCTURE_MARKERS`` → a structure_cuts
    collection is accepted and chunked without cuts → fails."""
    with pytest.raises(ValueError, match="not yet applied"):
        structure_from_params("fixed", {key: ALL[key]})
    assert structure_from_params("fixed", {key: ALL[key]}, unapplied=()).applied() == {
        key: ALL[key]}


@pytest.mark.parametrize(("markers", "needs"), [
    (StructureMarkers(), False),
    (StructureMarkers(text_render="markdown-plain/1"), False),
    (StructureMarkers(structure_infer="text-heuristic/1"), False),
    (StructureMarkers(structure_cuts="sections/1"), True),
    (StructureMarkers(embed_heading_path="prefix/1"), True),
])
def test_needs_structure_is_cuts_or_prefix(markers, needs) -> None:
    assert markers.needs_structure is needs
    assert structure_step_on("off", markers) is needs
    assert structure_step_on("on", markers) is True


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #

def test_a_marker_changes_the_chunk_descriptor_spec_hash_and_physical_name() -> None:
    """``chunk_params`` is inside ``chunk_descriptor`` → ``spec_hash`` → the
    content-addressed store name, so a marked collection is a new collection
    with no schema change (§7)."""
    plain = CollectionSpec(id="c", collection="", embedding_model="m", embedding_model_dim=4,
                           chunk_method="fixed_token", chunk_size=256, chunk_overlap=32)
    marked = plain.model_copy(update={"chunk_params": {"text_render": "markdown-plain/1"}})
    assert plain.chunk_descriptor() == chunk_descriptor("fixed_token", 256, 32, None)
    assert marked.chunk_descriptor() != plain.chunk_descriptor()
    assert "markdown-plain/1" in marked.chunk_descriptor()
    assert marked.spec_hash() != plain.spec_hash()
    assert collection_name("ragstack", "m", 4, chunk=marked.chunk_descriptor()) != \
        collection_name("ragstack", "m", 4, chunk=plain.chunk_descriptor())


# --------------------------------------------------------------------------- #
# the pipeline seam
# --------------------------------------------------------------------------- #

class _Embedder:
    async def embed(self, texts):  # noqa: ANN001
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _pipeline(**kw) -> IngestionPipeline:
    chunker, _, _ = build_chunker("fixed", chunk_size=200, chunk_overlap=20, model="m",
                                  token_backend="estimate", max_tokens=4096)
    return IngestionPipeline(loader=JsonlLoader(), chunker=chunker, embedder=_Embedder(),
                             vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(), **kw)


def test_a_marker_that_needs_structure_turns_the_step_on_whatever_the_setting() -> None:
    """§6: "a marker overrides the setting" — the seam PR 7's cuts and prefix
    rely on (built directly here: this build refuses those markers at read)."""
    assert _pipeline().structure_metadata is False
    assert _pipeline(structure_markers=StructureMarkers(
        structure_cuts="sections/1")).structure_metadata is True
    assert _pipeline(structure_markers=StructureMarkers(
        text_render="markdown-plain/1")).structure_metadata is False
    assert _pipeline(structure_metadata=True).structure_metadata is True
    assert _pipeline().structure_markers == NO_STRUCTURE_MARKERS


async def test_an_unmarked_pipeline_never_renders(tmp_path) -> None:
    md = "# Title\n\nSome **bold** body text.\n"
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(md, None, path="/docs/page.md")])
    chunks = (await _pipeline().prepare_source(str(shard))).chunks
    assert "".join(c.content for c in chunks).startswith("# Title")


# --------------------------------------------------------------------------- #
# a missing parser is a refusal when a marker asks for it (§6)
# --------------------------------------------------------------------------- #

PLAIN = StructureMarkers(text_render="markdown-plain/1")


def test_the_render_raises_naming_the_marker_and_leaves_content_alone(monkeypatch) -> None:
    """MUTATION: let the render fall back to the raw text when the parser is
    missing → the collection mixes raw and stripped documents → fails."""
    hide_markdown_extra(monkeypatch)
    doc = Document(id="d", content="# T\n\nbody\n", source="/d/p.md")
    with pytest.raises(StructureBackendUnavailable) as e:
        apply_text_render([doc], "markdown-plain/1")
    assert "text_render" in e.value.client_detail and "[markdown]" in e.value.client_detail
    assert doc.content == "# T\n\nbody\n"


def test_require_names_the_marker_and_exits_2_whatever_the_setting(monkeypatch,
                                                                   capsys) -> None:
    hide_markdown_extra(monkeypatch)
    with pytest.raises(StructureBackendUnavailable) as e:
        require_structure_backends(markers=PLAIN)
    assert "text_render='markdown-plain/1'" in e.value.client_detail
    with pytest.raises(SystemExit) as ex:
        require_structure_backends_or_exit("off", PLAIN)
    assert ex.value.code == 2 and "text_render" in capsys.readouterr().err
    require_structure_backends_or_exit("off", NO_STRUCTURE_MARKERS)  # nothing asks


# --------------------------------------------------------------------------- #
# the bulk tools: refusals at configuration, read from the registry entry
# --------------------------------------------------------------------------- #

def _shard(tmp_path: Path) -> Path:
    shard = tmp_path / "s0.jsonl"
    write_shard(shard, [record("# T\n\nBody of a synthetic page.\n", None,
                               path="/docs/page.md")])
    return shard


def _run_ingest_shard(tmp_path, monkeypatch):
    out = tmp_path / "receipt.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    monkeypatch.setattr(ingest_shard, "amain",
                        lambda *a, **k: pytest.fail("reached amain"))
    argv = [str(_shard(tmp_path)), "--collection-id", CID, "--registry", REG,
            "--qdrant-url", DEAD, "--es-url", DEAD, "--chunk-method", "sentence",
            "--chunk-size", "400", "--chunk-overlap", "0", "--embedding-model", "",
            "--chunk-token-counter", "estimate", "--symbol-glossary", "off",
            "--out", str(out)]
    with pytest.raises(SystemExit) as ex:
        ingest_shard.main(argv)
    return ex.value.code, out


def _run_embed_shard(tmp_path, monkeypatch):
    out = tmp_path / "s0.emb.jsonl"
    monkeypatch.setattr(embed_shard, "make_embedder_auto",
                        lambda **kw: pytest.fail("built an embedder"))
    argv = [str(_shard(tmp_path)), "--out", str(out), "--receipt", str(tmp_path / "r.json"),
            "--collection-id", CID, "--registry", REG, "--chunk-method", "sentence",
            "--chunk-size", "400", "--chunk-overlap", "0", "--embedding-model", "",
            "--chunk-token-counter", "estimate", "--symbol-glossary", "off"]
    with pytest.raises(SystemExit) as ex:
        embed_shard.main(argv)
    return ex.value.code, out


def _run_ingest_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_jsonl, "run", lambda *a, **k: pytest.fail("reached run"))
    monkeypatch.setattr(sys, "argv", [
        "ingest_jsonl.py", str(_shard(tmp_path)), "--collection-id", CID, "--registry", REG,
        "--qdrant-url", DEAD, "--es-url", DEAD, "--es-index", "", "--chunk-method",
        "sentence", "--chunk-size", "400", "--chunk-overlap", "0", "--embedding-model", "",
        "--chunk-token-counter", "estimate"])
    with pytest.raises(SystemExit) as ex:
        ingest_jsonl.main()
    return ex.value.code, None


TOOLS = {"ingest_shard": _run_ingest_shard, "embed_shard": _run_embed_shard,
         "ingest_jsonl": _run_ingest_jsonl}


@pytest.mark.parametrize("tool", sorted(TOOLS))
@pytest.mark.parametrize(("params", "needle"), [
    ({"text_render": "markdown-plain/9"}, "text_render"),
    ({"structure_cuts": "sections/2"}, "structure_cuts"),
    ({"embed_heading_path": "prefix/2"}, "embed_heading_path"),
    ({"structure_infer": "text-heuristic/1"}, "not yet applied"),
])
def test_an_unknown_or_unapplied_marker_exits_2_before_any_work(
    registry, tmp_path, monkeypatch, capsys, tool, params, needle  # noqa: F811
) -> None:
    registry(_spec(chunk_params=params))
    code, out = TOOLS[tool](tmp_path, monkeypatch)
    assert code == 2
    assert needle in capsys.readouterr().err
    assert out is None or not out.exists()


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_text_render_without_the_extra_exits_2_whatever_the_setting(
    registry, tmp_path, monkeypatch, capsys, tool  # noqa: F811
) -> None:
    """``--structure-metadata`` is off (the default) in every run here: the
    marker alone asks for the parser. MUTATION: gate the tools' check on the
    setting only → the shard is embedded raw (or fails mid-run) → fails."""
    hide_markdown_extra(monkeypatch)
    registry(_spec(chunk_params={"text_render": "markdown-plain/1"}))
    code, out = TOOLS[tool](tmp_path, monkeypatch)
    assert code == 2
    err = capsys.readouterr().err
    assert "[markdown]" in err and "text_render" in err
    assert out is None or not out.exists()


# --------------------------------------------------------------------------- #
# path D turns the structure step on from the markers, not only the setting
# --------------------------------------------------------------------------- #

def test_ingest_jsonl_turns_the_structure_step_on_from_a_marker(tmp_path, monkeypatch) -> None:
    """Fable M6. No marker that needs structure is applicable in this build, so
    the entry's reading is stubbed with the markers PR 7 will produce; the
    setting stays off. MUTATION: pass ``None`` (or nothing) to
    ``structure_step_on`` in ``ingest_jsonl.run`` → nothing is stamped → fails."""
    import asyncio

    from ragstack.ingestion.structure import STAMP_KEYS
    from tests.structure_support import paper
    from tests.unit.test_ingest_jsonl_reingest import _args, _FakeEmbedder, _FakeStore

    content, structure = paper()
    shard = tmp_path / "s.jsonl"
    write_shard(shard, [record(content, structure)])

    def run(markers):
        store = _FakeStore()
        monkeypatch.setattr(ingest_jsonl, "QdrantVectorStore", lambda **kw: store)
        monkeypatch.setattr(ingest_jsonl, "make_embedder", lambda **kw: _FakeEmbedder())
        monkeypatch.setattr(ingest_jsonl, "collection_name", lambda *a, **kw: "test")
        monkeypatch.setattr(ingest_jsonl, "_structure_markers", lambda args, target: markers)
        asyncio.run(ingest_jsonl.run(_args(shard, checkpoint=tmp_path / f"{id(markers)}.ckpt",
                                           structure_metadata="off")))
        return list(store.points.values())

    off = run(NO_STRUCTURE_MARKERS)
    on = run(StructureMarkers(structure_cuts="sections/1"))
    assert off and not any(set(c.metadata) & set(STAMP_KEYS) for c in off)
    assert on and all("structure_version" in c.metadata for c in on)
