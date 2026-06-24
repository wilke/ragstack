"""Unit tests for manifest building."""
from pathlib import Path

import pytest

from ragstack.ingestion.loaders import LoaderError, TextFileLoader
from ragstack.ingestion.manifest import build_manifest


def test_single_file_manifest(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    m = build_manifest(str(f))
    assert len(m) == 1
    assert m.items[0].source == str(f)


def test_item_id_matches_loader_doc_id(tmp_path: Path):
    # The manifest item_id must equal the document id the loader assigns, so
    # checkpoints/KG re-runs address the same id the vector store stores under.
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    item = build_manifest(str(f)).items[0]
    doc = TextFileLoader().load(str(f))[0]
    assert item.item_id == doc.id


def test_directory_manifest_recurses_and_sorts(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "sub" / "c.txt").write_text("c", encoding="utf-8")
    m = build_manifest(str(tmp_path))
    assert len(m) == 3
    # Deterministic order.
    assert m.items == sorted(m.items, key=lambda i: i.source)


def test_directory_manifest_filters_by_suffix(tmp_path: Path):
    (tmp_path / "keep.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "skip.png").write_text("x", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    m = build_manifest(str(tmp_path), suffixes=[".pdf", ".txt"])
    sources = {Path(i.source).name for i in m.items}
    assert sources == {"keep.pdf", "keep.txt"}


def test_build_manifest_confines_to_ingest_root(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "ok.txt").write_text("x", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")

    assert len(build_manifest(str(root / "ok.txt"), ingest_root=str(root))) == 1
    with pytest.raises(LoaderError):
        build_manifest(str(outside), ingest_root=str(root))


def test_directory_manifest_drops_symlink_escaping_root(tmp_path: Path):
    """rglob follows symlinks, so a link inside the root pointing outside must be
    dropped from the manifest — not enumerated with an out-of-root item_id."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "ok.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    m = build_manifest(str(root), ingest_root=str(root))
    names = {Path(i.source).name for i in m.items}
    assert names == {"ok.txt"}  # the escaping symlink is gone
