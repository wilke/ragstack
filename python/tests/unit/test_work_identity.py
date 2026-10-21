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


@pytest.mark.parametrize("name", [
    "fig.1.txt", "table.1.txt", "notes.2021-draft.md", "jvi.02415-06.md",
    "jvi.02415-06.txt", "report-PMC2020.pdf", "PMC123.md", "xPMC12345.pdf",
])
def test_ids_from_filename_false_positives(name: str) -> None:
    """Review fix 1/6: the ASM filename rule is a ``.pdf``-only rule (it turned
    ``fig.1.txt`` into ``10.1128/fig.1.txt``), and a PMCID token in a name needs
    at least five digits (``report-PMC2020`` is a year)."""
    assert ids_from_filename(name) == {}


def test_ids_from_filename_pdf_asm_rule_and_profile() -> None:
    from ragstack.ingestion.enrich import PublisherProfile

    assert ids_from_filename("jvi.02415-06.pdf") == {"doi": "10.1128/jvi.02415-06"}
    assert ids_from_filename("/x/JVI.02415-06.PDF") == {"doi": "10.1128/jvi.02415-06"}
    other = PublisherProfile(name="mbio", doi_prefix="10.9999")
    assert ids_from_filename("jvi.02415-06.pdf", other) == {"doi": "10.9999/jvi.02415-06"}
    assert ids_from_filename("PMC12345.pdf") == {"pmcid": "PMC12345"}


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


# --------------------------------------------------------------------------- #
# fill_ids
# --------------------------------------------------------------------------- #


def test_fill_ids_fills_missing_from_name_and_header_and_never_overwrites() -> None:
    bare = Document(id="d1", content=EUROPE_PMC_HEADER, source="/x/PMC8092463.md")
    mine = Document(id="d2", content=EUROPE_PMC_HEADER, source="/x/PMC8092463.md",
                    metadata={"pmcid": "PMC1", "doi": "10.9999/keep"})
    WorkIdentityStage(InMemoryTextIndex()).fill_ids([bare, mine])
    assert bare.metadata == {"pmcid": "PMC8092463", "pmid": "33858987", "doi": DOI,
                             "doi_source": "text"}
    assert mine.metadata["pmcid"] == "PMC1" and mine.metadata["doi"] == "10.9999/keep"
    assert mine.metadata["pmid"] == "33858987" and "doi_source" not in mine.metadata


def test_a_filename_doi_is_a_lookup_key_never_metadata() -> None:
    """Review fix 1: a ``.pdf`` name's DOI must not be written before
    enrichment (``document_doi`` would take it as authoritative and skip the
    text scan). The real DOI in the text is what the metadata keeps; the name's
    DOI only joins the lookup keys when the metadata has no DOI at all."""
    from ragstack.ingestion.doi_metadata import document_doi

    stage = WorkIdentityStage(InMemoryTextIndex())
    labelled = Document(id="a", content="DOI: 10.1234/real.1\n\nbody", source="/x/fig.1.txt")
    stage.fill_ids([labelled])
    assert labelled.metadata["doi"] == "10.1234/real.1"
    assert stage.keys_for(labelled) == [("doi", "10.1234/real.1")]

    asm = Document(id="b", content="body only", source="/x/jvi.02415-06.pdf")
    stage.fill_ids([asm])
    assert "doi" not in asm.metadata
    assert stage.keys_for(asm) == [("doi", "10.1128/jvi.02415-06")]

    unlabelled = Document(id="c", content=f"PNAS https://doi.org/{DOI} x " * 3,
                          source="/x/notes.2021-draft.md")
    stage.fill_ids([unlabelled])
    assert "doi" not in unlabelled.metadata
    assert stage.keys_for(unlabelled) == []
    assert document_doi(Document(id="d", content=unlabelled.content,
                                 source="/x/sha-1.md")) == (DOI, "text")


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


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_cap,shard_size", [(10_000, 64), (None, 1)])
async def test_two_copies_inside_one_job_second_is_refused(chunk_cap, shard_size) -> None:
    """Review fix 3: ONE ``DuplicateCheck`` per manifest run, its ``seen`` shared
    by every item. Covers the cap-sizing pass (every item prepared before any is
    indexed) and concurrent shards (shard_size=1, two at once)."""
    from ragstack.ingestion.backends import LocalAsyncIORunner
    from ragstack.ingestion.manifest import Manifest, WorkItem
    from ragstack.ingestion.sharded import ShardedIngestor
    from ragstack.jobstore import COMPLETED, FAILED

    pipe, loader = _pipe()
    loader.docs["c1"] = [_doc("c1", {"pmid": "77"})]
    loader.docs["c2"] = [_doc("c2", {"pmid": "PMID: 77"})]
    ingestor = ShardedIngestor(pipe, LocalAsyncIORunner(max_concurrency=2),
                               shard_size=shard_size)
    manifest = Manifest(items=[WorkItem(item_id="i1", source="c1"),
                               WorkItem(item_id="i2", source="c2")])
    results = await ingestor.ingest_manifest(
        manifest, tenant_id="alice", duplicate_policy="refuse",
        **({"chunk_cap": chunk_cap} if chunk_cap else {}),
    )
    by_id = {r.item_id: r for r in results}
    statuses = sorted((by_id["i1"].status, by_id["i2"].status))
    assert statuses == sorted([COMPLETED, FAILED])
    loser = by_id["i1"] if by_id["i1"].status == FAILED else by_id["i2"]
    winner_doc = "c2" if loser.item_id == "i1" else "c1"
    assert loser.error == format_refusal(("pmid", "77"), winner_doc)
    assert {c.doc_id for c in pipe.text_index._chunks} == {winner_doc}


@pytest.mark.asyncio
async def test_check_tenants_widen_the_scope_for_a_shared_collection() -> None:
    """Review fix 2: the lookup scope is the list the router resolved
    (``count_scope``), not ``readable_tenants`` of the writer — so the owner's
    copy in a collection shared with this caller is found."""
    from ragstack.ingestion.work_identity import DuplicateCheck

    pipe, loader = _pipe()
    await _seed(pipe, loader, _doc("owners", ENRICHED), tenant="owner")
    loader.docs["mine"] = [_doc("mine", ENRICHED)]
    with pytest.raises(DuplicateWork, match="existing_doc_id=owners"):
        await pipe.ingest("mine", tenant_id="collab", duplicate_policy=DuplicateCheck(
            policy="refuse", tenants=["collab", "public", "owner"]))
    # …and without the widening, the owner's copy is invisible to the caller
    assert await pipe.ingest("mine", tenant_id="collab", duplicate_policy="refuse")
