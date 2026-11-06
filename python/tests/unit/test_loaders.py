"""Unit tests for document loaders — deterministic IDs (idempotent re-ingest),
extension dispatch, PDF extraction, and ingest-root confinement."""
from pathlib import Path

import pytest

from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.loaders import (
    LoaderError,
    PdfLoader,
    StringLoader,
    TextFileLoader,
    default_loader_registry,
)


def _write_pdf(path: Path, text: str = "Hello PDF world") -> None:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_stringloader_doc_id_is_deterministic():
    loader = StringLoader()
    a = loader.load("hello world")[0]
    b = loader.load("hello world")[0]
    assert a.id == b.id
    # Different content -> different ID.
    assert loader.load("different content")[0].id != a.id


def test_textfileloader_doc_id_stable_across_loads(tmp_path: Path):
    f = tmp_path / "doc.txt"
    f.write_text("some content", encoding="utf-8")
    loader = TextFileLoader()
    first = loader.load(str(f))[0]
    second = loader.load(str(f))[0]
    assert first.id == second.id
    # A different file path -> different ID.
    g = tmp_path / "other.txt"
    g.write_text("some content", encoding="utf-8")
    assert loader.load(str(g))[0].id != first.id


def test_reingest_produces_identical_chunk_ids(tmp_path: Path):
    """Load-bearing idempotency guard: same file -> same chunk IDs -> same Qdrant points."""
    f = tmp_path / "doc.txt"
    f.write_text("abcdefghijklmnopqrstuvwxyz" * 3, encoding="utf-8")
    loader = TextFileLoader()
    chunker = RecursiveCharacterChunker(chunk_size=10, chunk_overlap=2)

    def ingest_ids() -> list[str]:
        doc = loader.load(str(f))[0]
        return [c.id for c in chunker.chunk(doc)]

    assert ingest_ids() == ingest_ids()


def test_pdfloader_extracts_text(tmp_path: Path):
    pdf = tmp_path / "doc.pdf"
    _write_pdf(pdf, "The quick brown fox")
    docs = PdfLoader().load(str(pdf))
    assert len(docs) == 1
    assert "quick brown fox" in docs[0].content
    assert docs[0].metadata["filename"] == "doc.pdf"
    assert docs[0].metadata["pages"] == 1


def test_pdfloader_empty_pdf_raises(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()  # a page with no text
    doc.save(str(pdf))
    doc.close()
    with pytest.raises(LoaderError):
        PdfLoader().load(str(pdf))


def test_registry_dispatches_by_extension(tmp_path: Path):
    txt = tmp_path / "a.txt"
    txt.write_text("plain text", encoding="utf-8")
    pdf = tmp_path / "b.pdf"
    _write_pdf(pdf, "pdf body text")

    registry = default_loader_registry()
    assert registry.load(str(txt))[0].content == "plain text"
    assert "pdf body text" in registry.load(str(pdf))[0].content


def test_markdown_dispatches_to_the_text_loader_and_keeps_its_markup(tmp_path: Path):
    """`.md` is read by the plain text loader, so the markup survives verbatim.

    This is the LOCAL-backend half of a claim in ``docs/cookbook-users.md``
    recipe 4. Before #828 a GoWe deployment handed the same file to the PDF
    extractor, which (PyMuPDF 2) stored the RENDERED text — the heading marker
    gone, ``**bold**`` gone, ``-`` bullets turned into ``\u2022``. Since #828
    the GoWe extract step dispatches through this same registry; that half is
    pinned in ``tests/ingestion/test_extract_text_parity.py``.

    Anyone planning to filter or match on literal markup depends on this.
    """
    md = tmp_path / "notes.md"
    body = "# Probe\n\nA **markdown** probe file.\n\n- bullet one\n- bullet two\n"
    md.write_text(body, encoding="utf-8")

    content = default_loader_registry().load(str(md))[0].content

    assert content == body, "the text loader must not transform the source"
    assert "# Probe" in content          # heading marker intact
    assert "**markdown**" in content     # emphasis intact
    assert "- bullet one" in content     # list marker not rendered to a bullet glyph
    assert "\u2022" not in content       # ...specifically, no U+2022


def test_registry_confines_to_ingest_root(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    inside = root / "ok.txt"
    inside.write_text("inside", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("outside", encoding="utf-8")

    registry = default_loader_registry(ingest_root=str(root))
    assert registry.load(str(inside))[0].content == "inside"
    with pytest.raises(LoaderError):
        registry.load(str(outside))
    # Path-traversal attempt out of the root is rejected too.
    with pytest.raises(LoaderError):
        registry.load(str(root / ".." / "secret.txt"))


def test_registry_rejects_oversized(tmp_path: Path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 1000, encoding="utf-8")
    registry = default_loader_registry(max_bytes=100)
    with pytest.raises(LoaderError):
        registry.load(str(f))


def test_registry_missing_source_raises(tmp_path: Path):
    registry = default_loader_registry()
    with pytest.raises(LoaderError):
        registry.load(str(tmp_path / "does-not-exist.txt"))


def _write_pdf_with_info(path: Path, info: dict, text: str = "Body text") -> None:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.set_metadata(info)
    doc.save(str(path))
    doc.close()


def test_pdfloader_lifts_doi_from_embedded_metadata(tmp_path: Path):
    """The DOI is the one embedded field worth lifting — it's the key DOI
    enrichment turns into a real bibliographic record."""
    pdf = tmp_path / "paper.pdf"
    _write_pdf_with_info(pdf, {"subject": "doi:10.3390/Antibiotics14050475"})
    meta = PdfLoader().load(str(pdf))[0].metadata
    assert meta["doi"] == "10.3390/antibiotics14050475"  # normalized
    assert meta["doi_source"] == "pdf-metadata"


def test_pdfloader_omits_doi_when_the_pdf_has_none(tmp_path: Path):
    pdf = tmp_path / "plain.pdf"
    _write_pdf_with_info(pdf, {"subject": "A paper about things", "title": "untitled"})
    meta = PdfLoader().load(str(pdf))[0].metadata
    assert "doi" not in meta
    # Producer junk in `title` is deliberately NOT lifted: under the "existing
    # metadata wins" precedence it would permanently block the real remote title.
    assert "title" not in meta


# --------------------------------------------------------------------------- #
# #828 PR 5a / #833 items 1-2: no default loader; .xml is JATS
# --------------------------------------------------------------------------- #

def _jats_fixture():
    """The synthetic article of ``tests/ingestion/test_jats_extract.py``."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
    import test_jats_extract as t  # the fixture module, not a test of this file

    return t


@pytest.mark.parametrize("name, suffix", [
    ("notes.rst", ".rst"), ("LICENSE", ""), ("table.CSV", ".csv"),
])
def test_registry_refuses_an_unknown_suffix_without_reading_it(tmp_path: Path, name, suffix):
    """MUTATION: restore a TextFileLoader default → the .rst is read as text."""
    from ragstack.ingestion.loaders import NO_LOADER_LABEL, NoLoader, no_loader_error

    f = tmp_path / name
    f.write_text("would be ingested as text", encoding="utf-8")
    with pytest.raises(NoLoader) as exc:
        default_loader_registry().load(str(f))
    # The item error is the constant the upload and GoWe paths record.
    assert exc.value.job_error == no_loader_error(suffix)
    assert exc.value.job_error == (f"no loader for {suffix.lower()}" if suffix
                                   else "no loader for (no suffix)")
    assert isinstance(exc.value, LoaderError)
    assert NO_LOADER_LABEL in str(exc.value)


def test_unknown_suffix_lands_the_constant_on_the_item(tmp_path: Path):
    """The by-reference route end to end, below the API: the sharded ingestor
    records the refusal string on the item, not a class name.
    MUTATION: a bare LoaderError (no job_error) → the item says 'NoLoader'."""
    import asyncio

    from ragstack.ingestion.backends import LocalAsyncIORunner
    from ragstack.ingestion.manifest import build_manifest
    from ragstack.ingestion.pipeline import IngestionPipeline
    from ragstack.ingestion.sharded import ShardedIngestor
    from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore

    class _Embedder:
        async def embed(self, texts):
            return [[1.0, 2.0, 3.0] for _ in texts]

    f = tmp_path / "x.rst"
    f.write_text("Title\n=====\n\nbody\n", encoding="utf-8")
    pipeline = IngestionPipeline(
        loader=default_loader_registry(ingest_root=str(tmp_path)),
        chunker=RecursiveCharacterChunker(chunk_size=100, chunk_overlap=0),
        embedder=_Embedder(), vector_store=InMemoryVectorStore(),
        text_index=InMemoryTextIndex(),
    )
    (result,) = asyncio.run(ShardedIngestor(pipeline, LocalAsyncIORunner(max_concurrency=1)).ingest_manifest(
        build_manifest(str(f), ingest_root=str(tmp_path))))
    assert result.status == "failed"
    assert result.error == "no loader for .rst"


def test_registry_has_no_default_parameter():
    """The fallback is gone, not just unused (#828 PR 5a decision 5)."""
    import inspect

    from ragstack.ingestion.loaders import LoaderRegistry

    assert "default" not in inspect.signature(LoaderRegistry).parameters


def test_xml_dispatches_to_the_jats_loader():
    from ragstack.ingestion.loaders import DEFAULT_INGEST_SUFFIXES, JatsLoader

    reg = default_loader_registry()
    assert isinstance(reg.loader_for("PMC1.xml"), JatsLoader)
    assert isinstance(reg.loader_for("PMC1.XML"), JatsLoader)
    # MUTATION: drop .xml from DEFAULT_INGEST_SUFFIXES (registration kept) →
    # a directory ingest silently skips every .xml.
    assert ".xml" in DEFAULT_INGEST_SUFFIXES


def test_jats_loader_builds_the_article_and_its_units(tmp_path: Path):
    from ragstack.ingestion.jats_structure import ADAPTER
    from ragstack.ingestion.loaders import deterministic_doc_id

    t = _jats_fixture()
    xml = tmp_path / "PMC1.xml"
    xml.write_text(t._article("1", t.BODY_WITH_FLOATS), encoding="utf-8")
    docs = default_loader_registry(ingest_root=str(tmp_path)).load(str(xml))

    by_source = {d.source: d for d in docs}
    assert list(by_source) == ["PMC1", "PMC1#table-1", "PMC1#figure-1"]
    # Keyed on the filename stem, as jats_extract keys the record path —
    # MUTATION: key on the resolved file path → these ids change.
    for source, doc in by_source.items():
        assert doc.id == deterministic_doc_id(source)
    assert by_source["PMC1"].id == deterministic_doc_id("PMC1")

    article = by_source["PMC1"]
    assert article.structure is not None and article.structure.adapter == ADAPTER
    assert all(d.structure is not None and d.structure.adapter == ADAPTER for d in docs)
    meta = article.metadata
    # Enriched: authors is a list (MUTATION: raw "; "-joined string → fails).
    assert meta["authors"] == ["Jane Doe", "Rick Roe"]
    assert meta["keywords"] == ["synthetic", "jats"]
    assert meta["doi"] == "10.1234/synth.1"
    assert meta["year"] == 2024
    # JATS passthrough keys ride along.
    assert meta["pmcid"] == "PMC1"
    assert meta["content_type"] == "article"
    assert meta["journal"] == "J Synthetic Res"
    assert "abstract" not in meta  # heavy field, never on a chunk
    table = by_source["PMC1#table-1"].metadata
    assert table["content_type"] == "table" and table["section_title"] == "table-1"
    assert by_source["PMC1#figure-1"].metadata["graphic"] == "synth-f001.jpg"
    assert "ZZCELLZZ" not in article.content and "ZZCELLZZ" in by_source["PMC1#table-1"].content


def test_jats_loader_pmcid_is_the_stem_up_to_the_first_dot(tmp_path: Path):
    from ragstack.ingestion.loaders import JatsLoader, deterministic_doc_id

    t = _jats_fixture()
    xml = tmp_path / "PMC5.v2.xml"
    xml.write_text(t._article("5", "<p>Lead paragraph only.</p>"), encoding="utf-8")
    (doc,) = JatsLoader().load(str(xml))
    assert doc.source == "PMC5" and doc.id == deterministic_doc_id("PMC5")


def test_jats_loader_malformed_xml_is_a_loader_error(tmp_path: Path):
    from ragstack.ingestion.loaders import JatsLoader, NoJatsContent

    xml = tmp_path / "PMC9.xml"
    xml.write_text("<article><body><p>never closed", encoding="utf-8")
    with pytest.raises(LoaderError) as exc:
        JatsLoader().load(str(xml))
    assert not isinstance(exc.value, NoJatsContent)
    assert getattr(exc.value, "job_error", None) is None  # class-name label, as for a PDF
    assert str(exc.value) == "could not parse JATS XML 'PMC9.xml'"


def test_jats_loader_refuses_xml_with_no_jats_content(tmp_path: Path):
    """MUTATION: return [] for <config/> → no exception, a silent empty item."""
    from ragstack.ingestion.loaders import NO_JATS_CONTENT_ERROR, JatsLoader, NoJatsContent

    xml = tmp_path / "settings.xml"
    xml.write_text("<config><option name='a'>1</option></config>", encoding="utf-8")
    with pytest.raises(NoJatsContent) as exc:
        JatsLoader().load(str(xml))
    assert exc.value.job_error == NO_JATS_CONTENT_ERROR
    assert NO_JATS_CONTENT_ERROR == "no JATS article content (not a JATS article?)"


def test_jats_loader_counts_a_dropped_structure_and_keeps_the_document(
    tmp_path: Path, monkeypatch
):
    import ragstack.ingestion.jats_structure as js
    from ragstack.ingestion.loaders import JatsLoader

    def boom(text, spans):
        raise ValueError("synthetic")

    monkeypatch.setattr(js, "article_structure", boom)
    t = _jats_fixture()
    xml = tmp_path / "PMC1.xml"
    xml.write_text(t._article("1", t.BODY_WITH_FLOATS), encoding="utf-8")
    loader = JatsLoader()
    docs = {d.source: d for d in loader.load(str(xml))}
    assert docs["PMC1"].structure is None
    assert docs["PMC1#table-1"].structure is not None
    assert loader.dropped.structure_invalid == {"ValueError": 1}


def test_jats_passthrough_keys_are_the_cwl_default():
    """The in-process loader stamps the keys path C (cwl/jats-ingest.cwl) does."""
    from ragstack.ingestion.jats import JATS_PASSTHROUGH_KEYS

    cwl = (Path(__file__).resolve().parents[3] / "cwl" / "jats-ingest.cwl").read_text()
    default = cwl.split("metadata_passthrough:", 1)[1].split("default:", 1)[1]
    default = default.split("\n", 1)[0].strip().strip('"')
    assert tuple(default.split(",")) == JATS_PASSTHROUGH_KEYS
