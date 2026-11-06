"""``same_record_family`` (#828 PR 5a): a JATS article and its unit records
are one work for the duplicate guard; nothing else is."""
import pytest

from ragstack.ingestion.work_identity import same_record_family


@pytest.mark.parametrize("a, b, expected", [
    ("PMC1", "PMC1#table-1", True),
    ("PMC1#figure-2", "PMC1#table-1", True),
    ("PMC1#table-1", "PMC1#table-1", True),
    ("PMC1", "PMC2#table-1", False),
    ("PMC1", "PMC1", False),             # no unit record: the doc-id rule decides
    ("", "", False),                     # two path-less records are not siblings
    ("", "#x", False),
    ("/data/a#b.pdf", "/data/a", False),  # a file path is never split
    ("/data/PMC1.xml", "PMC1#table-1", False),
    # #975 F2: only the unit grammar jats.py emits is a family member.
    ("PMC1", "PMC1#table-2-part-3", True),
    ("PMC1#figure-1-part-2", "PMC1#table-1", True),
    (".hidden", ".hidden#table-1", True),
    ("journal.pone.0012345", "journal.pone.0012345#figure-1", True),
    ("corpus#0001", "corpus#0002", False),       # JSONL records sharing a DOI
    ("corpus", "corpus#0001", False),
    ("PMC1#supp.pdf", "PMC1", False),            # an uploaded file name
    ("PMC1#supp.pdf", "PMC1#table-1", False),
    ("PMC1#table-x", "PMC1", False),
    ("PMC1#table-1-part-", "PMC1", False),
    ("PMC1#tables-1", "PMC1", False),
    # #975 round 2: a file with no PMC id is keyed on its resolved path
    # (jats.record_key step 3), so a unit root may hold "/".
    ("/corpus/a/paper.xml", "/corpus/a/paper.xml#table-1", True),
    ("/corpus/a/paper.xml#figure-1", "/corpus/a/paper.xml#table-2-part-1", True),
    ("/corpus/a/paper.xml", "/corpus/b/paper.xml#table-1", False),
    ("/corpus/a/paper.xml", "/corpus/a/paper.xml", False),
    ("/corpus/a/paper.pdf#page-1", "/corpus/a/paper.pdf", False),
    ("/corpus/a/notes#x.md", "/corpus/a/notes", False),
])
def test_same_record_family(a, b, expected):
    assert same_record_family(a, b) is expected
    assert same_record_family(b, a) is expected


@pytest.mark.asyncio
async def test_admit_refuses_a_jats_family_whole_and_leaves_other_records_alone():
    """#975 round 2 (argo N1a): within one ``admit`` call (a JSONL shard of
    several articles, or one JatsLoader file), a refused record refuses its
    family — article and units — and nothing else; ``seen`` records only what
    was finally admitted. MUTATION: drop ``_refuse_whole_families`` → the
    units of the refused article are admitted."""
    from ragstack.ingestion.loaders import deterministic_doc_id
    from ragstack.ingestion.work_identity import (
        POLICY_ALLOW,
        POLICY_REFUSE,
        WorkIdentityStage,
        format_refusal,
        refusal_of,
    )
    from ragstack.models import Chunk, Document
    from ragstack.stores.memory import InMemoryTextIndex

    ix = InMemoryTextIndex()
    await ix.index([Chunk(id="c0", doc_id="held", content="x",
                          metadata={"tenant_id": "t", "doi": "10.1234/held"})])

    def doc(src: str, **meta) -> Document:
        return Document(id=deterministic_doc_id(src), content="body", source=src,
                        metadata=dict(meta))

    a = "/c/a.xml"
    refused = [doc(a, doi="10.1234/held"), doc(f"{a}#table-1"), doc(f"{a}#figure-1", pmid="9")]
    other = [doc("/c/b.xml", doi="10.1234/b"), doc("/c/b.xml#table-1", doi="10.1234/b")]
    lone = doc("/c/notes.md")
    seen: dict = {}
    admitted = await WorkIdentityStage(ix).admit(
        refused + other + [lone], tenants=["t"], policy=POLICY_REFUSE, seen=seen)
    assert [d.source for d in admitted] == ["/c/b.xml", "/c/b.xml#table-1", "/c/notes.md"]
    assert {refusal_of(d) for d in refused} == {format_refusal(("doi", "10.1234/held"), "held")}
    assert ("pmid", "9") not in seen  # the refused unit's keys were never committed
    assert seen[("doi", "10.1234/b")] == deterministic_doc_id("/c/b.xml")
    # allow: nothing refused, nothing propagated
    for d in refused:
        d.metadata.pop("_duplicate_refusal", None)
        d.metadata.pop("duplicate_of", None)
    admitted = await WorkIdentityStage(ix).admit(refused, tenants=["t"], policy=POLICY_ALLOW)
    assert len(admitted) == 3
    assert "duplicate_of" not in admitted[1].metadata


@pytest.mark.asyncio
async def test_a_refused_unit_refuses_only_itself_never_its_article():
    """#975 round 3 (argo S1b): the family rule runs article → units only. A
    unit that carries a foreign id (here a pmid held by another work) is
    refused alone; its article and its other units are admitted. Before, the
    unit's refusal was copied onto the article and every sibling, so a table
    citing an indexed paper took the whole work down. MUTATION: let any
    family member's refusal propagate (``first.setdefault(family(d), m)``
    for units too) → the article and the figure are refused."""
    from ragstack.ingestion.loaders import deterministic_doc_id
    from ragstack.ingestion.work_identity import (
        POLICY_REFUSE,
        WorkIdentityStage,
        format_refusal,
        refusal_of,
    )
    from ragstack.models import Chunk, Document
    from ragstack.stores.memory import InMemoryTextIndex

    ix = InMemoryTextIndex()
    await ix.index([Chunk(id="c0", doc_id="held", content="x",
                          metadata={"tenant_id": "t", "pmid": "23456789"})])

    def doc(src: str, **meta) -> Document:
        return Document(id=deterministic_doc_id(src), content="body", source=src,
                        metadata=dict(meta))

    a = "/c/a.xml"
    art = doc(a, doi="10.1234/a")
    table = doc(f"{a}#table-1", doi="10.1234/a", pmid="23456789")
    fig = doc(f"{a}#figure-1", doi="10.1234/a")
    seen: dict = {}
    admitted = await WorkIdentityStage(ix).admit(
        [table, art, fig], tenants=["t"], policy=POLICY_REFUSE, seen=seen)
    assert [d.source for d in admitted] == [a, f"{a}#figure-1"]
    assert refusal_of(table) == format_refusal(("pmid", "23456789"), "held")
    assert not refusal_of(art) and not refusal_of(fig)
    assert seen[("doi", "10.1234/a")] == art.id
    assert ("pmid", "23456789") not in seen


def test_fill_ids_gives_units_their_articles_ids_never_ids_from_their_own_text():
    """#975 round 3 (argo S1a): a JATS unit is not a source of identity.
    ``fill_ids`` scans an ARTICLE's header text, then copies the article's
    final doi/pmid/pmcid onto its units; a unit's own text (a table listing
    the studies it cites) is never scanned. A unit whose article is not in
    the call keeps what its record carried. MUTATION: scan units' text too →
    the table carries pmid 23456789; drop the copy → the units lack the
    article's labelled pmid."""
    from ragstack.ingestion.work_identity import WorkIdentityStage
    from ragstack.models import Document

    a = "/c/a.xml"
    art = Document(id="a", source=a, metadata={"doi": "10.1234/a"},
                   content="PMID: 11111111 The abstract.")
    table = Document(id="t", source=f"{a}#table-1", metadata={"doi": "10.1234/a"},
                     content="Table 1. Included studies. Smith, PMID 23456789, "
                             "DOI: 10.5555/cited.study, PMCID: PMC7654321")
    orphan = Document(id="o", source="/c/b.xml#figure-1", metadata={},
                      content="Figure 1. From PMID: 23456789")
    WorkIdentityStage(text_index=None).fill_ids([table, art, orphan])
    assert art.metadata["pmid"] == "11111111"
    assert {k: table.metadata.get(k) for k in ("doi", "pmid", "pmcid")} == {
        "doi": "10.1234/a", "pmid": "11111111", "pmcid": None}
    assert orphan.metadata == {}
