"""#828 PR 5a / #833 item 2: one JATS file, one set of documents, on every path.

Three routes take a JATS article into a collection:

* **A** — in process: ``default_loader_registry().load("PMC77.xml")`` →
  :class:`~ragstack.ingestion.loaders.JatsLoader` (by-reference and upload
  ingest);
* **B** — the GoWe extract step: ``scripts/pdf_extract.py`` dispatches ``.xml``
  through the SAME registry and writes a shard, which the ingest step loads
  with :class:`~ragstack.ingestion.loaders.JsonlLoader` (reaches GoWe only at
  the next tools-image repin, ADR-0010);
* **C** — the bulk JATS workflow: ``scripts/jats_extract.py`` writes a shard,
  loaded by ``JsonlLoader(passthrough_keys=JATS_PASSTHROUGH_KEYS)`` (the
  ``cwl/jats-ingest.cwl`` default).

Per document, by id: the same id set, byte-identical ``content``, the same
``jats/1`` structure field for field, the same index metadata subset, and the
JATS passthrough keys wherever the loader asks for them. Path C runs with
``--no-token-budget``: ``jats_extract`` defaults to an HF tokenizer budget
while ``JatsLoader`` uses character budgets only, so an oversized table splits
differently — :func:`test_an_oversized_table_is_where_the_token_budget_diverges`
pins exactly that, so the flag cannot hide a wider divergence.

Path B through a PLAIN ``JsonlLoader()`` — what ``cwl/pdf-ingest-scatter.cwl``
runs today, no ``--metadata-passthrough`` — keeps the index subset but loses
the passthrough keys (#833 item 4, out of scope for 5a): recorded here so its
fix is a visible test change.

The duplicate guard across a JATS file's records (``same_record_family``) is
covered end to end through the API in
``tests/api/test_ingest_duplicate_guard.py::test_a_fresh_jats_ingest_keeps_its_tables_and_figures_under_refuse``.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion import jats_structure, loaders
from ragstack.ingestion.jats import JATS_PASSTHROUGH_KEYS
from ragstack.ingestion.loaders import (
    JSONL_UNSUPPORTED_ERROR,
    JsonlLoader,
    default_loader_registry,
    deterministic_doc_id,
)
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.models import Document
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1] / "scripts"))
import jats_extract  # noqa: E402
import pdf_extract  # noqa: E402

import tests.jats_support as t  # noqa: E402
from tests.ingestion.test_extract_text_parity import (  # noqa: E402
    _chunker,
    _Embedder,
    _stamps,
)

#: ``enrich.index_metadata``'s keys: what every path stamps on every chunk.
INDEX_KEYS = ("doc_type", "doi", "doi_source", "title", "authors", "keywords",
              "year", "n_citations", "source_path", "filename")

# Nested and untitled <sec>, a declared sec-type (two roles), a table inline in
# a paragraph and a figure in <floats-group>: every shape the adapter and the
# lift-out handle differently.
BODY = f"""
<sec sec-type="intro"><title>Introduction</title>
  <p>Intro prose for the parity fixture, QQINTROQQ.</p></sec>
<sec sec-type="materials|methods"><title>Materials and Methods</title>
  <p>Methods overview paragraph.</p>
  <sec><title>Cohort</title><p>The nested cohort subsection describes enrolment.</p></sec>
  <sec><p>An untitled subsection with its own paragraph.</p></sec>
</sec>
<sec><title>Results</title><p>Results prose that cites the table. {t.TABLE_1}</p></sec>
"""

IDS = ["PMC77", "PMC77#table-1", "PMC77#figure-1"]


@pytest.fixture
def article(tmp_path: Path) -> Path:
    src = tmp_path / "in"
    src.mkdir()
    p = src / "PMC77.xml"
    p.write_text(t._article("77", BODY, floats_group=t.FIG_1), encoding="utf-8")
    return p


def _path_a(xml: Path) -> list[Document]:
    return default_loader_registry(ingest_root=str(xml.parent)).load(str(xml))


def _shard_b(xml: Path, out: Path) -> Path:
    assert pdf_extract.main([str(xml), "--out", str(out)]) == 0
    return out


def _shard_c(xml: Path, out: Path, *extra: str) -> Path:
    listing = out.with_suffix(".list")
    listing.write_text(f"{xml}\n", encoding="utf-8")
    assert jats_extract.main(["--shard", str(listing), "--out", str(out), *extra]) == 0
    return out


def _jats_jsonl() -> JsonlLoader:
    return JsonlLoader(passthrough_keys=JATS_PASSTHROUGH_KEYS)


def _by_id(docs: list[Document]) -> dict[str, Document]:
    out = {d.id: d for d in docs}
    assert len(out) == len(docs), "duplicate doc ids"
    return out


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


def test_three_paths_give_the_same_documents(tmp_path, article) -> None:
    a = _by_id(_path_a(article))
    b = _by_id(_jats_jsonl().load(str(_shard_b(article, tmp_path / "b.jsonl"))))
    c = _by_id(_jats_jsonl().load(
        str(_shard_c(article, tmp_path / "c.jsonl", "--no-token-budget"))))

    # Ids are keyed on the record id (filename stem), never the file path.
    assert set(a) == {deterministic_doc_id(i) for i in IDS}
    assert set(a) == set(b) == set(c)
    assert sorted(d.source for d in a.values()) == sorted(IDS)

    for doc_id, da in a.items():
        db, dc = b[doc_id], c[doc_id]
        assert da.source == db.source == dc.source
        assert da.content == db.content == dc.content, da.source  # byte for byte
        assert da.structure is not None and da.structure.adapter == "jats/1"
        assert db.structure is not None and dc.structure is not None
        assert da.structure.model_dump() == db.structure.model_dump() \
            == dc.structure.model_dump(), da.source
        for k in INDEX_KEYS:
            assert da.metadata.get(k) == db.metadata.get(k) == dc.metadata.get(k), (da.source, k)
        for k in JATS_PASSTHROUGH_KEYS:
            assert da.metadata.get(k) == db.metadata.get(k) == dc.metadata.get(k), (da.source, k)
        assert da.metadata["content_type"] in ("article", "table", "figure")
        assert da.metadata["pmcid"] == "PMC77"

    # The fixture exercises what it claims to (non-vacuity): a declared
    # two-role section, a nested titled and a nested untitled section, a float
    # lifted from <floats-group>.
    secs = a[deterministic_doc_id("PMC77")].structure.sections  # type: ignore[union-attr]
    assert any(s.roles == ["materials", "methods"] for s in secs)
    assert any(s.level == 2 and s.title == "Cohort" for s in secs)
    assert any(s.level == 2 and s.title is None for s in secs)
    assert a[deterministic_doc_id("PMC77#figure-1")].metadata["content_type"] == "figure"
    assert "ZZCELLZZ" in a[deterministic_doc_id("PMC77#table-1")].content
    assert "ZZCELLZZ" not in a[deterministic_doc_id("PMC77")].content  # lifted out


@pytest.mark.parametrize("name, pmc, key", [
    ("journal.pone.0012345.xml", False, "journal.pone.0012345"),  # record_key step 3
    ("journal.pone.0012345.xml", True, "PMC77"),                  # step 2: the article id
    (".hidden.nxml", False, ".hidden"),
])
def test_three_paths_key_a_non_pmc_name_alike(tmp_path, name, pmc, key) -> None:
    """#975 F1: the record key of a file NOT named for a PMC id is the same on
    all three paths — jats_extract resolves it at convert time from the
    article's own ``<article-id pub-id-type="pmc">`` or the full file name,
    as JatsLoader does. MUTATION: first-dot keys on any one path → the id sets
    differ (or collapse to ``journal`` / ``""``)."""
    src = tmp_path / "in"
    src.mkdir()
    xml = src / name
    xml.write_text(t._article("77", BODY, floats_group=t.FIG_1, pmc=pmc), encoding="utf-8")
    ids = {deterministic_doc_id(f"{key}{sfx}") for sfx in ("", "#table-1", "#figure-1")}
    a = _by_id(_path_a(xml))
    b = _by_id(_jats_jsonl().load(str(_shard_b(xml, tmp_path / "b.jsonl"))))
    c = _by_id(_jats_jsonl().load(
        str(_shard_c(xml, tmp_path / "c.jsonl", "--no-token-budget"))))
    assert set(a) == set(b) == set(c) == ids
    for doc_id, da in a.items():
        assert da.content == b[doc_id].content == c[doc_id].content
        pmcids = {d.metadata.get("pmcid") or "" for d in (da, b[doc_id], c[doc_id])}
        assert pmcids == {"PMC77" if pmc else ""}  # a file-name key is not a PMCID


def test_plain_jsonl_loader_on_b_keeps_the_index_subset_but_not_passthrough(
    tmp_path, article
) -> None:
    """``cwl/pdf-ingest-scatter.cwl`` loads the extract shard with a bare
    ``JsonlLoader()`` today: index metadata equal, JATS passthrough keys
    ABSENT (#833 item 4, out of scope for 5a). When the CWL gains
    ``--metadata-passthrough`` this test changes on purpose."""
    a = _by_id(_path_a(article))
    plain = _by_id(JsonlLoader().load(str(_shard_b(article, tmp_path / "b.jsonl"))))
    assert set(plain) == set(a)
    for doc_id, da in a.items():
        dp = plain[doc_id]
        assert dp.content == da.content
        assert dp.structure is not None and da.structure is not None
        assert dp.structure.model_dump() == da.structure.model_dump()
        assert {k: dp.metadata.get(k) for k in INDEX_KEYS} \
            == {k: da.metadata.get(k) for k in INDEX_KEYS}
        assert not set(JATS_PASSTHROUGH_KEYS) & set(dp.metadata), da.source
        assert set(JATS_PASSTHROUGH_KEYS) & set(da.metadata)  # A has them


def test_an_invalid_structure_is_dropped_the_same_way_on_every_path(
    tmp_path, article, monkeypatch
) -> None:
    """An article structure that breaks an invariant is dropped by the shared
    ``record_structure`` helper — the record kept, the reason counted — so
    neither shard carries a ``structure`` key for it and no path ends up with a
    structure. MUTATION: drop ``validate_structure`` from ``record_structure``
    → the shards carry the stale structure."""
    real = jats_structure.article_structure

    def stale(text, spans):  # noqa: ANN001
        return real(text + "x", spans)  # bound to another text

    monkeypatch.setattr(jats_structure, "article_structure", stale)
    registry = default_loader_registry(ingest_root=str(article.parent))
    a = _by_id(registry.load(str(article)))
    jl = registry.loader_for(str(article))
    assert isinstance(jl, loaders.JatsLoader)
    assert dict(jl.dropped.structure_invalid) == {"content_sha256": 1}

    shard_b = _shard_b(article, tmp_path / "b.jsonl")
    shard_c = _shard_c(article, tmp_path / "c.jsonl", "--no-token-budget")
    for shard in (shard_b, shard_c):
        recs = {r["path"]: r for r in _lines(shard)}
        assert "structure" not in recs["PMC77"], shard.name
        assert recs["PMC77#table-1"]["structure"]["adapter"] == "jats/1"
    b = _by_id(_jats_jsonl().load(str(shard_b)))
    c = _by_id(_jats_jsonl().load(str(shard_c)))
    art = deterministic_doc_id("PMC77")
    assert a[art].structure is None and b[art].structure is None and c[art].structure is None
    assert a[art].content == b[art].content == c[art].content


def test_an_oversized_table_is_where_the_token_budget_diverges(tmp_path, monkeypatch) -> None:
    """Why path C runs with ``--no-token-budget`` above, and the only place the
    flag matters: a dense table under the 1,800-CHAR cap but over the token
    budget is ONE unit in process (char budgets only) and on C without a
    tokenizer, but row-split into ``-part-N`` units (other ids) on C with its
    default tokenizer budget. The HF tokenizer is not in the test env, so it is
    replaced by a dense counter (~1.6 chars/token, the measured p10 for tables)
    for BOTH C runs — the flag, not the env, is what turns it off."""
    from ragstack.ingestion import tokenization

    class _DenseCounter:
        def __init__(self, model: str) -> None:
            self.model = model

        def count(self, s: str) -> int:
            return max(1, int(len(s) / 1.6))

    monkeypatch.setattr(tokenization, "HFTokenCounter", _DenseCounter)
    body = "<sec><title>Results</title><p>Prose.</p>" + t._big_table(40) + "</sec>"
    xml = tmp_path / "PMC9.xml"
    xml.write_text(t._article("9", body), encoding="utf-8")

    a_sources = sorted(d.source for d in _path_a(xml))
    flat = sorted(r["path"] for r in _lines(
        _shard_c(xml, tmp_path / "c_flat.jsonl", "--no-token-budget")))
    assert a_sources == flat == ["PMC9", "PMC9#table-1"]

    budgeted = sorted(r["path"] for r in _lines(_shard_c(xml, tmp_path / "c_tok.jsonl")))
    assert "PMC9#table-1" not in budgeted
    assert len([p for p in budgeted if p.startswith("PMC9#table-1-part-")]) > 1
    assert budgeted != a_sources


# --------------------------------------------------------------------------- #
# chunk stamps: prepare_source (A) vs ingest_shard.py on the B shard
# --------------------------------------------------------------------------- #


async def test_stamped_metadata_is_identical_in_process_and_on_the_extract_shard(
    tmp_path, article, monkeypatch
) -> None:
    import ingest_shard

    pipe = IngestionPipeline(loader=default_loader_registry(ingest_root=str(article.parent)),
                             chunker=_chunker(), embedder=_Embedder(),
                             vector_store=InMemoryVectorStore(),
                             text_index=InMemoryTextIndex(), structure_metadata=True)
    a = _stamps((await pipe.prepare_source(str(article))).chunks)

    shard = _shard_b(article, tmp_path / "b.jsonl")
    emb_b, receipt = tmp_path / "b.emb.jsonl", tmp_path / "b.receipt.json"
    monkeypatch.setattr(ingest_shard, "_build_embedder", lambda args, http: _Embedder())
    assert await asyncio.to_thread(ingest_shard.main, [
        str(shard), "--vector-backend", "memory", "--text-backend", "memory",
        "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
        "--chunk-method", "fixed", "--chunk-size", "200", "--chunk-overlap", "20",
        "--embedding-model", "", "--chunk-token-counter", "estimate",
        "--chunk-max-tokens", "4096", "--symbol-glossary", "off",
        "--structure-metadata", "on", "--embedding-file", str(emb_b), "--out", str(receipt),
    ]) == 0
    b = _stamps([json.loads(x) for x in emb_b.read_text(encoding="utf-8").splitlines()[1:]])

    assert a and a == b
    stamped = [v for v in a.values() if v]
    assert stamped and all(v["structure_version"].startswith("jats/1;") for v in stamped)
    assert any("methods" in v.get("section_roles", []) for v in stamped)
    assert any(v.get("caption_labels") for v in stamped)


# --------------------------------------------------------------------------- #
# unknown suffixes: one refusal on A and B
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["x.rst", "LICENSE", "x.csv"])
def test_an_unknown_suffix_is_refused_identically_on_a_and_b(tmp_path, name) -> None:
    f = tmp_path / name
    f.write_text("Title\n=====\n\nbody text\n", encoding="utf-8")
    with pytest.raises(loaders.NoLoader) as exc:
        default_loader_registry(ingest_root=str(tmp_path)).load(str(f))
    records, skipped = pdf_extract.extract_documents([f])
    assert records == []
    assert [s["error"] for s in skipped] == [exc.value.job_error]
    assert exc.value.job_error == loaders.no_loader_error(Path(name).suffix)


def test_jsonl_stays_refused_on_b(tmp_path) -> None:
    f = tmp_path / "batch.jsonl"
    f.write_text(json.dumps({"text": "t", "path": "PMC1"}) + "\n", encoding="utf-8")
    records, skipped = pdf_extract.extract_documents([f])
    assert records == []
    assert [s["error"] for s in skipped] == [JSONL_UNSUPPORTED_ERROR]
    # …while A loads it (a batch file is the in-process JSONL route).
    assert [d.source for d in default_loader_registry(ingest_root=str(tmp_path))
            .load(str(f))] == ["PMC1"]
