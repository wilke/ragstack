"""The duplicate-paper guard's pure core and its pipeline hook (#747).

No live store: the lookup is the in-memory text index (or a fake that raises),
the embedder is a constant fake. The six metadata shapes below are the ones
the dev ``openchia`` collection carries for the Bazant & Bush PNAS article
(plan §0): three enriched copies, two with the malformed ``…/-/`` DOI and no
title, one Europe PMC rendering with no id fields at all.
"""
from __future__ import annotations

import logging

import pytest

from ragstack.config import Settings
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.work_identity import (
    DUPLICATE_OF,
    DUPLICATE_WORK,
    POLICIES,
    DuplicateWork,
    WorkIdentityStage,
    format_refusal,
    ids_from_filename,
    ids_from_text,
    is_duplicate_refusal,
    normalize_doi,
    normalize_pmcid,
    normalize_pmid,
    refusal_of,
    work_keys,
)
from ragstack.models import Document
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

DOI = "10.1073/pnas.2018995118"
ENRICHED = {"doi": DOI, "pmcid": "PMC8092463", "pmid": "33858987", "doi_source": "text"}
MALFORMED = {"doi": f"{DOI}/-/", "doi_source": "text"}
EUROPE_PMC_HEADER = (
    "# A guideline to limit indoor airborne transmission of COVID-19\n\n"
    "Martin Z. Bazant, John W. M. Bush\n\n"
    "PMCID: PMC8092463 · PMID: 33858987 · DOI: 10.1073/\npnas.2018995118\n\n"
    "Abstract. The current revival of the American economy ..."
)


# --------------------------------------------------------------------------- #
# normalisers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", [
    f"{DOI}/-/DCSupplemental", f"{DOI}/-/", f"{DOI}/-", f"{DOI}/",
    f"https://doi.org/{DOI.upper()}", f"doi:{DOI}",
])
def test_normalize_doi_canonical_forms(raw: str) -> None:
    assert normalize_doi(raw) == DOI


def test_normalize_doi_keeps_a_dash_inside_the_suffix() -> None:
    assert normalize_doi("10.1000/abc/-xyz") == "10.1000/abc/-xyz"
    assert normalize_doi("10.1016/S0140-6736(98)01085-X") == "10.1016/s0140-6736(98)01085-x"


@pytest.mark.parametrize("raw,want", [
    ("PMC8092463", "PMC8092463"), ("pmc8092463", "PMC8092463"),
    ("PMC8092463.2", "PMC8092463"), ("PMCID: PMC8092463", "PMC8092463"),
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8092463/", "PMC8092463"),
    ("8092463", "PMC8092463"), ("", ""), (None, ""), (8092463, ""), ("PMC", ""),
    ("not-an-id", ""),
])
def test_normalize_pmcid(raw, want) -> None:
    assert normalize_pmcid(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("33858987", "33858987"), ("PMID:33858987", "33858987"), ("PMID: 33858987", "33858987"),
    (33858987, "33858987"), ("PMC8092463", ""), ("", ""), (None, ""), (True, ""),
    ("0", ""), ("1234567890", ""),
])
def test_normalize_pmid(raw, want) -> None:
    assert normalize_pmid(raw) == want


def test_work_keys_on_the_dev_shapes() -> None:
    assert work_keys(ENRICHED) == [("doi", DOI), ("pmcid", "PMC8092463"), ("pmid", "33858987")]
    assert work_keys(MALFORMED) == [("doi", DOI)]
    assert work_keys({"filename": "PMC8092463.md"}) == []  # no id FIELDS: not a key
    assert work_keys({"pmid": 33858987}) == [("pmid", "33858987")]  # the #594 int


def test_ids_from_filename() -> None:
    assert ids_from_filename("PMC8092463.md") == {"pmcid": "PMC8092463"}
    assert ids_from_filename("/stage/sub_1/PMC8092463.pdf") == {"pmcid": "PMC8092463"}
    assert ids_from_filename("sha-487426f38505525d.md") == {}
    assert ids_from_filename("") == {}


def test_ids_from_text_reads_the_europe_pmc_header_including_the_wrapped_doi() -> None:
    assert ids_from_text(EUROPE_PMC_HEADER) == {
        "pmcid": "PMC8092463", "pmid": "33858987", "doi": DOI,
    }


def test_ids_from_text_ignores_an_unlabelled_doi_and_text_past_the_window() -> None:
    assert ids_from_text(f"cited as 10.1000/xyz123 in passing. {DOI}") == {}
    assert ids_from_text("x" * 3000 + "PMCID: PMC1") == {}


def test_refusal_format_is_recognisable_by_prefix() -> None:
    msg = format_refusal(("doi", DOI), "abc")
    assert msg.startswith(f"{DUPLICATE_WORK}: key=doi:{DOI} existing_doc_id=abc ")
    assert "duplicate_policy=allow" in msg
    assert is_duplicate_refusal(msg) and not is_duplicate_refusal("chunk_cap_exceeded: x")
    assert not is_duplicate_refusal(None)
    assert DuplicateWork(msg).job_error == msg


def test_settings_policies_match_the_module() -> None:
    # config.py spells the tuple out (it must not import ingestion at load).
    assert POLICIES == ("refuse", "allow", "off")
    assert Settings(duplicate_policy="ALLOW").duplicate_policy == "allow"
    with pytest.raises(ValueError):
        Settings(duplicate_policy="maybe")
    assert Settings().duplicate_policy == "refuse"            # D1: the API default
    assert Settings().gowe_worker_duplicate_policy is False   # pinned image safety


# --------------------------------------------------------------------------- #
# fill_ids
# --------------------------------------------------------------------------- #


def test_fill_ids_fills_missing_from_name_and_header_and_never_overwrites() -> None:
    bare = Document(id="d1", content=EUROPE_PMC_HEADER, source="/x/PMC8092463.md")
    mine = Document(id="d2", content=EUROPE_PMC_HEADER, source="/x/PMC8092463.md",
                    metadata={"pmcid": "PMC1", "doi": "10.9999/keep"})
    WorkIdentityStage.fill_ids([bare, mine])
    assert bare.metadata == {"pmcid": "PMC8092463", "pmid": "33858987", "doi": DOI,
                             "doi_source": "text"}
    assert mine.metadata["pmcid"] == "PMC1" and mine.metadata["doi"] == "10.9999/keep"
    assert mine.metadata["pmid"] == "33858987" and "doi_source" not in mine.metadata


# --------------------------------------------------------------------------- #
# the pipeline hook (prepare_documents / prepare_source / embed_documents)
# --------------------------------------------------------------------------- #


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class _Loader:
    """Returns pre-built documents for a source name (the loader seam)."""

    def __init__(self) -> None:
        self.docs: dict[str, list[Document]] = {}

    def load(self, source: str) -> list[Document]:
        return [d.model_copy(deep=True) for d in self.docs[source]]


def _pipe(text_index=None) -> tuple[IngestionPipeline, _Loader]:
    loader = _Loader()
    pipe = IngestionPipeline(
        loader=loader, chunker=RecursiveCharacterChunker(), embedder=_FakeEmbedder(),
        vector_store=InMemoryVectorStore(), text_index=text_index or InMemoryTextIndex(),
    )
    return pipe, loader


def _doc(doc_id: str, meta: dict, text: str = "Body text about indoor air. " * 8) -> Document:
    return Document(id=doc_id, content=text, metadata=dict(meta), source=f"/c/{doc_id}.md")


async def _seed(pipe: IngestionPipeline, loader: _Loader, doc: Document,
                tenant: str = "alice") -> None:
    loader.docs[doc.id] = [doc]
    await pipe.ingest(doc.id, tenant_id=tenant)


@pytest.mark.asyncio
async def test_refuse_fails_the_source_with_the_formatted_refusal() -> None:
    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("first", ENRICHED))
    loader.docs["second"] = [_doc("second", MALFORMED)]
    with pytest.raises(DuplicateWork) as ei:
        await pipe.ingest("second", tenant_id="alice", duplicate_policy="refuse")
    assert ei.value.job_error == format_refusal(("doi", DOI), "first")
    # nothing of the refused copy reached the index
    assert {c.doc_id for c in pipe.text_index._chunks} == {"first"}


@pytest.mark.asyncio
async def test_allow_admits_and_stamps_duplicate_of_on_every_chunk() -> None:
    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("first", ENRICHED))
    loader.docs["second"] = [_doc("second", {"pmcid": "pmc8092463"})]
    ids = await pipe.ingest("second", tenant_id="alice", duplicate_policy="allow")
    second = [c for c in pipe.text_index._chunks if c.doc_id == "second"]
    assert ids and second and all(c.metadata[DUPLICATE_OF] == "first" for c in second)


@pytest.mark.asyncio
async def test_off_makes_no_lookup_and_changes_nothing() -> None:
    class _Boom(InMemoryTextIndex):
        async def find_documents_by_key(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("off must not look up")

    pipe, loader = _pipe(_Boom())
    await _seed(pipe, loader, _doc("first", ENRICHED))
    loader.docs["second"] = [_doc("second", ENRICHED, text=EUROPE_PMC_HEADER * 3)]
    await pipe.ingest("second", tenant_id="alice")  # default: off
    second = [c for c in pipe.text_index._chunks if c.doc_id == "second"]
    assert second and all(DUPLICATE_OF not in c.metadata for c in second)


@pytest.mark.asyncio
async def test_reingest_of_the_same_document_is_a_replace_not_a_duplicate() -> None:
    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("first", ENRICHED))
    await pipe.ingest("first", tenant_id="alice", duplicate_policy="refuse")
    assert {c.doc_id for c in pipe.text_index._chunks} == {"first"}


@pytest.mark.asyncio
async def test_lookup_failure_admits_with_a_warning(caplog) -> None:
    class _Down(InMemoryTextIndex):
        async def find_documents_by_key(self, *a, **k):
            raise ConnectionError("es down")

    pipe, loader = _pipe(_Down())
    loader.docs["x"] = [_doc("x", ENRICHED)]
    with caplog.at_level(logging.WARNING, logger="ragstack.ingestion.work_identity"):
        assert await pipe.ingest("x", tenant_id="alice", duplicate_policy="refuse")
    assert "lookup failed" in caplog.text


@pytest.mark.asyncio
async def test_scope_is_own_tenant_plus_public_never_another_tenant() -> None:
    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("bobs", ENRICHED), tenant="bob")
    loader.docs["mine"] = [_doc("mine", ENRICHED)]
    assert await pipe.ingest("mine", tenant_id="alice", duplicate_policy="refuse")
    await _seed(pipe, loader, _doc("pub", {"pmid": "1"}), tenant="public")
    loader.docs["mine2"] = [_doc("mine2", {"pmid": "PMID: 1"})]
    with pytest.raises(DuplicateWork):
        await pipe.ingest("mine2", tenant_id="alice", duplicate_policy="refuse")


@pytest.mark.asyncio
async def test_header_ids_are_filled_before_the_lookup() -> None:
    """The Europe PMC copy carries no id fields: the guard reads them from its
    header (and the file name) and refuses it against the enriched copy."""
    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("first", ENRICHED))
    loader.docs["epmc"] = [Document(id="epmc", content=EUROPE_PMC_HEADER * 3,
                                    source="/c/PMC8092463.md")]
    with pytest.raises(DuplicateWork, match=f"key=doi:{DOI} existing_doc_id=first"):
        await pipe.ingest("epmc", tenant_id="alice", duplicate_policy="refuse")


@pytest.mark.asyncio
async def test_multi_document_batch_skips_only_the_refused_and_dedupes_within() -> None:
    """embed_documents (the worker's call) never raises for a refusal: a refused
    document simply has no chunks, and ``refusal_of`` names why. Two copies of
    one new work in the same batch: the first is admitted, the second refused."""
    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("first", ENRICHED))
    docs = [_doc("dup", MALFORMED), _doc("new1", {"pmid": "42"}),
            _doc("new2", {"pmid": "PMID:42"}), _doc("plain", {})]
    kept, _produced, _q = await pipe.embed_documents(
        docs, tenant_id="alice", duplicate_policy="refuse"
    )
    assert {c.doc_id for c in kept} == {"new1", "plain"}
    assert refusal_of(docs[0]) == format_refusal(("doi", DOI), "first")
    assert refusal_of(docs[2]) == format_refusal(("pmid", "42"), "new1")
    assert refusal_of(docs[1]) == "" and refusal_of(docs[3]) == ""
    assert len(docs) == 4  # the caller's list is never shortened


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_cap", [None, 10_000])
async def test_sharded_item_carries_the_refusal_on_both_local_paths(chunk_cap) -> None:
    """The uncapped per-item path and the cap-sized path (#291 ``_admit``)
    both turn ``DuplicateWork`` into the item's error via ``job_error``."""
    from ragstack.ingestion.backends import LocalAsyncIORunner
    from ragstack.ingestion.manifest import Manifest, WorkItem
    from ragstack.ingestion.sharded import ShardedIngestor
    from ragstack.jobstore import COMPLETED, FAILED

    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("first", ENRICHED))
    loader.docs["dup"] = [_doc("dup", MALFORMED)]
    loader.docs["new"] = [_doc("new", {"pmid": "7"})]
    ingestor = ShardedIngestor(pipe, LocalAsyncIORunner(max_concurrency=1))
    manifest = Manifest(items=[WorkItem(item_id="i-dup", source="dup"),
                               WorkItem(item_id="i-new", source="new")])
    results = await ingestor.ingest_manifest(
        manifest, tenant_id="alice", duplicate_policy="refuse",
        **({"chunk_cap": chunk_cap} if chunk_cap else {}),
    )
    by_id = {r.item_id: r for r in results}
    assert by_id["i-dup"].status == FAILED
    assert by_id["i-dup"].error == format_refusal(("doi", DOI), "first")
    assert by_id["i-new"].status == COMPLETED
