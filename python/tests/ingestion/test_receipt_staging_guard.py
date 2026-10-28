"""#852: receipts that are one receipt are refused, distinct ones still pass.

A scatter whose tasks all write ``--out receipt.json`` and glob
``receipt.json`` hands its gather step N receipt paths. cwltool stages
colliding basenames into separate directories, so the N paths are N files;
GoWe stages a gathered ``File[]`` flat by basename, so the N paths are ONE
surviving file. Packed as is, the version's ``receipt.json`` held N copies of
one task's receipt and the API read every other task's documents as failed
(``completed=10, failed=40`` on a 50-file upload).

The tools refuse that shape — the pack (``archive.write_version`` /
``archive_version.py``) and the gather (``merge_summary`` /
``merge_receipts.py``) — and keep passing the cwltool shape (same basename,
different directories, different receipts). The CWL half (per-task receipt
names) ships with the server release that stamps the image carrying this
guard; ``test_cwl_receipt_names_are_unique_once_the_image_guards`` holds that.

Offline: synthetic embedding files, no store, no engine.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.archive import RECEIPT_NAME, ArchiveError, write_version
from ragstack.ingestion.gowe_backend import NO_RECEIPT_ENTRY, map_receipt_entries
from ragstack.ingestion.manifest import WorkItem
from ragstack.ingestion.receipts import (
    COMPLETED,
    DocRow,
    DuplicateReceiptError,
    ShardReceipt,
    merge_summary,
)
from tests.ingestion.test_archive_version import _embed_file

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "scripts"))
import archive_version  # noqa: E402
import load_embeddings as load_cli  # noqa: E402
import merge_receipts  # noqa: E402

STAGED = "/tmp/gowe-ws-stage/sub_1"


def _batch(shard_id: str, *names: str) -> ShardReceipt:
    rows = [DocRow(doc_id=f"id-{n}", source=f"{STAGED}/{n}", chunk_ids=[f"{n}-c0"])
            for n in names]
    return ShardReceipt(shard_id=shard_id, tenant="public", status=COMPLETED,
                        n_docs=len(rows), n_chunks=len(rows),
                        chunk_ids=[c for r in rows for c in r.chunk_ids], docs=rows)


def _write(path: Path, r: ShardReceipt) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    r.write(str(path))
    return path


def _wi(name: str) -> WorkItem:
    return WorkItem(item_id=f"/u/home/sources/{name}", source=f"ws:///u/home/sources/{name}")


# --------------------------------------------------------------------------- #
# the cwltool shape still passes: a 2-task scatter, disjoint documents
# --------------------------------------------------------------------------- #


def test_two_task_scatter_with_disjoint_documents_reports_every_document_once(
        tmp_path: Path) -> None:
    """The issue's own test. Two tasks, each ``receipt.json``, in their own
    directories (cwltool's staging): the pack arrays both, the receipt holds
    each document once, and the API's mapping reports all completed."""
    _embed_file(tmp_path / "a.emb.jsonl", 3)
    r0 = _write(tmp_path / "task0" / "receipt.json", _batch("batch-00000", "a.pdf", "b.pdf"))
    r1 = _write(tmp_path / "task1" / "receipt.json", _batch("batch-00001", "c.pdf", "d.pdf"))
    m = write_version(tmp_path / "out", 1, [tmp_path / "a.emb.jsonl"], [r0, r1],
                      collection_id="c", tenant="public", workers=1)
    assert m["receipts"] == 2
    entries = json.loads((tmp_path / "out" / "1" / RECEIPT_NAME).read_text())
    sources = [d["source"] for e in entries for d in e["docs"]]
    assert sorted(sources) == sorted(set(sources)) and len(sources) == 4
    items = [_wi(n) for n in ("a.pdf", "b.pdf", "c.pdf", "d.pdf")]
    out = map_receipt_entries(items, entries)
    assert [(o.status, o.chunk_ids) for o in out] == [
        ("completed", [f"{n}-c0"]) for n in ("a.pdf", "b.pdf", "c.pdf", "d.pdf")]
    # and the gather counts each once
    assert merge_summary([ShardReceipt.load(str(r0)), ShardReceipt.load(str(r1))])["n_docs"] == 4


def test_what_the_guard_prevents_one_receipt_arrayed_twice_fails_the_other_task(
        tmp_path: Path) -> None:
    """The failure itself, so the refusal below is not guarding a phantom:
    one task's receipt arrayed twice maps the other task's documents to
    NO_RECEIPT_ENTRY."""
    one = dataclasses.asdict(_batch("batch-00000", "a.pdf", "b.pdf"))
    out = map_receipt_entries([_wi("a.pdf"), _wi("b.pdf"), _wi("c.pdf"), _wi("d.pdf")],
                              [one, one, one])
    assert [o.status for o in out] == ["completed", "completed", "failed", "failed"]
    assert out[2].error == NO_RECEIPT_ENTRY


# --------------------------------------------------------------------------- #
# the GoWe shape is refused: the pack
# --------------------------------------------------------------------------- #


def test_pack_refuses_receipts_staged_onto_one_file(tmp_path: Path) -> None:
    """N ``--receipt`` paths resolving to one file — literally the same path,
    or a link onto it — are refused before anything is packed, naming the
    remedy; nothing is left behind."""
    _embed_file(tmp_path / "a.emb.jsonl", 3)
    one = _write(tmp_path / "stage" / "receipt.json", _batch("batch-00000", "a.pdf"))
    link = tmp_path / "other" / "receipt.json"
    link.parent.mkdir()
    os.symlink(one, link)
    for receipts in ([one, one, one], [one, link]):
        with pytest.raises(ArchiveError, match=r"staged onto one file.*nameroot\)\.receipt\.json"):
            write_version(tmp_path / "out", 1, [tmp_path / "a.emb.jsonl"], receipts,
                          collection_id="c", tenant="public", workers=1)
        assert not (tmp_path / "out").exists()       # refused before the version dir
    # the CLI: exit 1 with the message on stderr, no version
    rc = archive_version.main(["--version", "2", "--collection-id", "c",
                               "--chunks", str(tmp_path / "a.emb.jsonl"),
                               "--receipt", str(one), str(one),
                               "--out", str(tmp_path / "out"), "--workers", "1"])
    assert rc == 1 and not (tmp_path / "out" / "2").exists()


def test_pack_refuses_duplicate_shard_ids(tmp_path: Path) -> None:
    """Two distinct files carrying the same ``shard_id`` (a copy of one
    receipt) are refused too."""
    _embed_file(tmp_path / "a.emb.jsonl", 3)
    a = _write(tmp_path / "t0" / "receipt.json", _batch("batch-00000", "a.pdf"))
    b = _write(tmp_path / "t1" / "receipt.json", _batch("batch-00000", "a.pdf"))
    with pytest.raises(ArchiveError, match=r"duplicate receipt shard_id.*batch-00000"):
        write_version(tmp_path / "out", 1, [tmp_path / "a.emb.jsonl"], [a, b],
                      collection_id="c", tenant="public", workers=1)
    assert not (tmp_path / "out" / "1").exists()


def test_pack_single_receipt_and_receipts_without_shard_id_are_not_compared(
        tmp_path: Path) -> None:
    """``pdf-ingest.cwl`` packs ONE load summary (no ``shard_id``) verbatim:
    untouched by the guard. Array elements without a ``shard_id`` are not
    compared."""
    _embed_file(tmp_path / "a.emb.jsonl", 3)
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"n_shards": 1, "n_chunks": 3}))
    write_version(tmp_path / "out", 1, [tmp_path / "a.emb.jsonl"], [summary],
                  collection_id="c", tenant="public", workers=1)
    other = tmp_path / "summary2.json"
    other.write_text(json.dumps({"n_shards": 1, "n_chunks": 3}))
    write_version(tmp_path / "out", 2, [tmp_path / "a.emb.jsonl"], [summary, other],
                  collection_id="c", tenant="public", workers=1)


# --------------------------------------------------------------------------- #
# the gather
# --------------------------------------------------------------------------- #


def test_merge_receipts_refuses_duplicate_shard_ids(tmp_path: Path, capsys) -> None:
    a = _write(tmp_path / "t0" / "receipt.json", _batch("s0", "a.pdf"))
    b = _write(tmp_path / "t1" / "receipt.json", _batch("s0", "a.pdf"))
    with pytest.raises(DuplicateReceiptError, match="s0"):
        merge_summary([ShardReceipt.load(str(a)), ShardReceipt.load(str(b))])
    out = tmp_path / "summary.json"
    assert merge_receipts.main([str(a), str(b), "--out", str(out)]) \
        == merge_receipts.DUPLICATE_RECEIPTS_EXIT_CODE == 3
    assert not out.exists()
    assert "duplicate receipt shard_id" in capsys.readouterr().err
    # one file named N times: refused by path, before it is even parsed twice
    assert merge_receipts.main([str(a), str(a), "--out", str(out)]) == 3
    assert "staged onto one file" in capsys.readouterr().err and not out.exists()
    # distinct receipts, same basename, different dirs: the cwltool shape passes
    c = _write(tmp_path / "t2" / "receipt.json", _batch("s1", "b.pdf"))
    assert merge_receipts.main([str(a), str(c), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["n_shards"] == 2


def test_load_refuses_one_embedding_file_named_twice_before_any_store(
        tmp_path: Path, capsys) -> None:
    """The load's summary is a ``merge_summary`` keyed by file path; the same
    file named twice is refused up front, never loaded twice and refused
    after the write."""
    _embed_file(tmp_path / "a.emb.jsonl", 3)
    f = str(tmp_path / "a.emb.jsonl")
    with pytest.raises(SystemExit) as e:
        load_cli.main([f, f, "--out", str(tmp_path / "s.json"), "--collection-id", "c",
                       "--vector-backend", "memory", "--text-backend", "memory",
                       "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1"])
    assert e.value.code == 2
    assert "staged onto one file" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


# --------------------------------------------------------------------------- #
# the CWL half: the guard ships only with per-task receipt names
# --------------------------------------------------------------------------- #

#: The first tools image whose pack/gather refuse receipts staged onto one file.
GUARD_IMAGE = (1, 6, 20)
_STAMPED = re.compile(r"ragstack-tools-v(\d+)\.(\d+)\.(\d+)-b\d+\.sif")
_CONSTANT_RECEIPT = re.compile(r"(glob|valueFrom):\s*receipt\.json\b")


def constant_receipt_names_under_scatter(text: str) -> bool:
    """Whether a workflow scatters a step AND names a receipt with the
    constant ``receipt.json`` — the shape GoWe stages onto one file."""
    return bool(re.search(r"^\s*scatter:", text, re.M)) and bool(_CONSTANT_RECEIPT.search(text))


def test_the_detector_sees_the_shape() -> None:
    bad = "steps:\n  ingest:\n    scatter: shard\n    run:\n      outputs:\n" \
          "        receipt: {type: File, outputBinding: {glob: receipt.json}}\n"
    good = bad.replace("glob: receipt.json", "glob: $(inputs.shard.nameroot).receipt.json")
    assert constant_receipt_names_under_scatter(bad)
    assert not constant_receipt_names_under_scatter(good)
    assert not constant_receipt_names_under_scatter(bad.replace("scatter: shard", ""))


def test_cwl_receipt_names_are_unique_once_the_image_guards() -> None:
    """A tree stamped with a tools image >= v1.6.20 (whose pack and gather
    refuse receipts staged onto one file) must not scatter a step that names
    its receipt ``receipt.json``: on GoWe every multi-task upload and bulk
    gather would then FAIL instead of mis-reporting. Below v1.6.20 (the tree
    as of this test) the old naming is still allowed."""
    receipt = json.loads((REPO / "cwl" / "tool-image.receipt.json").read_text())
    m = _STAMPED.fullmatch(receipt.get("name", ""))
    if m is None or tuple(int(x) for x in m.groups()) < GUARD_IMAGE:
        return
    offenders = sorted(p.name for p in (REPO / "cwl").glob("*.cwl")
                       if constant_receipt_names_under_scatter(p.read_text()))
    assert not offenders, (
        f"cwl/ is stamped with {receipt['name']}, whose tools refuse receipts staged "
        f"onto one file (#852), but {offenders} still name a scattered receipt "
        "`receipt.json`: rename to `$(inputs.shard.nameroot).receipt.json` in the same "
        "release that stamps the image")
