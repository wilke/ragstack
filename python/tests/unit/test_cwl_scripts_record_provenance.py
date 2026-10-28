"""#821: every record a CWL-run script writes carries tool provenance.

ADR-0010 decision 8 asks every receipt, summary and report a workflow step
writes to say which workflow and which tools image produced it. Until v1.6.20
the bulk/JATS plane wrote none: ``embed_shard.py``'s receipts read all-null
(``segmenter_version`` included), the extract reports and ``merge_receipts``'s
summary had no key, and ``load_embeddings.py`` recorded one only on
``--replay``.

Two pins:

* **static** (AST, like ``test_cwl_scripts_read_no_policy.py``): every script
  a ``cwl/*.cwl`` runs calls both ``add_provenance_arguments`` and
  ``provenance_from_args``;
* **runtime**: each such script, run offline with NO provenance flag (what
  every shipped CWL does until G2b declares the inputs), writes a record whose
  ``provenance`` is not :func:`unknown_provenance` — so dropping the
  ``receipt.provenance = …`` line turns it red even where the flags are still
  declared. Inside an image (a ``RELEASE`` file) the image fields are set too.

Plus the record-specific tests: the embedding-file header round-trips its
``provenance`` (and an old header still reads), ``merge_receipts`` lists the
shards' distinct provenance, the load summary carries it on the load path and
on a cap refusal, not only on ``--replay``.

Every new flag is optional: the shipped CWLs pass none of them, and the CWLs
are not changed here (ADR-0010 d4: G2b declares the inputs).

Offline throughout: fake embedder, memory stores, store URLs on the dead port.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from ragstack.ingestion.chunkers import segmenter_version
from ragstack.ingestion.embedding_file import (
    EmbeddingFileWriter,
    read_embedding_file,
    read_header,
)
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.models import Chunk
from ragstack.ops import ingest_target as it
from ragstack.provenance import PROVENANCE_KEYS, read_provenance, unknown_provenance

REPO = Path(__file__).resolve().parents[3]
PY = REPO / "python"
CWL_DIR = REPO / "cwl"
sys.path.insert(0, str(PY / "scripts"))
import embed_shard as embed_cli  # noqa: E402
import jats_extract  # noqa: E402
import load_embeddings as load_cli  # noqa: E402
import load_graph as load_graph_cli  # noqa: E402
import merge_receipts  # noqa: E402
import pdf_extract  # noqa: E402

DEAD = "http://127.0.0.1:1"
RELEASE_TEXT = "version=v9.9.9\ncommit=" + "c" * 40 + "\nbuild=2\nbuild_date=2026-10-28T00:00:00Z\n"
SEEDED = ["--workflow-id", "wf_821", "--tool-image", "ragstack-tools-v9.9.9-b2.sif",
          "--tool-image-digest", "d" * 64]


def _cwl_scripts() -> list[str]:
    names: set[str] = set()
    for cwl in sorted(CWL_DIR.glob("*.cwl")):
        names.update(re.findall(r"scripts/([A-Za-z0-9_]+\.py)", cwl.read_text(encoding="utf-8")))
    return sorted(names)


def _calls(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            f = node.func
            out.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute)
                    else "")
    return out


# --------------------------------------------------------------------------- #
# static pin
# --------------------------------------------------------------------------- #


def test_the_cwl_scripts_are_found() -> None:
    """Not vacuous: the scan finds the scripts the workflows run."""
    assert {"embed_shard.py", "merge_receipts.py", "load_embeddings.py",
            "jats_extract.py", "pdf_extract.py", "ingest_shard.py"} <= set(_cwl_scripts())


@pytest.mark.parametrize("script", _cwl_scripts())
def test_every_cwl_script_records_provenance(script: str) -> None:
    calls = _calls(PY / "scripts" / script)
    missing = {"add_provenance_arguments", "provenance_from_args"} - calls
    assert not missing, (
        f"scripts/{script} is run by a cwl/*.cwl workflow but never calls {sorted(missing)}: "
        "its receipt/summary/report would carry no tool provenance (ADR-0010 d8, #821)")


#: Scripts whose runtime record is pinned elsewhere (``test_tool_provenance.py``).
RUNTIME_ELSEWHERE = {"ingest_shard.py", "archive_version.py", "extract_graph.py"}


def test_every_cwl_script_has_a_runtime_pin() -> None:
    assert set(_cwl_scripts()) <= set(RUNNERS) | RUNTIME_ELSEWHERE, (
        "a new CWL-run script needs a runtime provenance pin below")


# --------------------------------------------------------------------------- #
# runners: each writes its record offline and returns it
# --------------------------------------------------------------------------- #


def _shard(tmp: Path) -> Path:
    shard = tmp / "shard.jsonl"
    text = " ".join(f"Sentence number {i} says something plain." for i in range(40))
    shard.write_text(json.dumps({"text": text, "path": "a.txt", "metadata": {}}) + "\n"
                     + json.dumps({"text": text[::-1], "path": "b.txt", "metadata": {}}) + "\n",
                     encoding="utf-8")
    return shard


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 2.0, 3.0] for t in texts]


_EMBED_OFFLINE = ["--chunk-token-counter", "estimate", "--embedding-model", "",
                  "--chunk-max-tokens", "4096", "--embedding-url", DEAD,
                  "--symbol-glossary", "off", "--chunk-method", "fixed",
                  "--chunk-size", "200", "--chunk-overlap", "0"]


def _embed(tmp: Path, mp: pytest.MonkeyPatch, *extra: str, name: str = "s0") -> tuple[Path, Path]:
    mp.setattr(embed_cli, "make_embedder_auto", lambda **_kw: _FakeEmbedder())
    mp.setattr(it, "require_store_urls", lambda *a, **k: pytest.fail("no store"))
    mp.delenv("RAGSTACK_COLLECTION_ID", raising=False)
    out, receipt = tmp / f"{name}.emb.jsonl", tmp / f"{name}.receipt.json"
    assert embed_cli.main([str(_shard(tmp)), "--out", str(out), "--receipt", str(receipt),
                           "--shard-id", name, *_EMBED_OFFLINE, *extra]) == 0
    return out, receipt


def run_embed_shard(tmp: Path, mp: pytest.MonkeyPatch) -> dict:
    return json.loads(_embed(tmp, mp)[1].read_text())


def run_merge_receipts(tmp: Path, mp: pytest.MonkeyPatch) -> dict:
    a = tmp / "a.json"
    ShardReceipt("s0", "public", COMPLETED, n_docs=1).write(str(a))
    out = tmp / "summary.json"
    assert merge_receipts.main([str(a), "--out", str(out)]) == 0
    return json.loads(out.read_text())


def _load(tmp: Path, *argv: str) -> tuple[Any, dict]:
    out = tmp / "load-summary.json"
    try:
        rc: Any = load_cli.main([*argv, "--out", str(out), "--collection-id", "scratch-821",
                                 "--vector-backend", "memory", "--text-backend", "memory",
                                 "--qdrant-url", DEAD, "--es-url", DEAD])
    except SystemExit as e:
        rc = e.code
    return rc, json.loads(out.read_text())


def run_load_embeddings(tmp: Path, mp: pytest.MonkeyPatch) -> dict:
    emb, _ = _embed(tmp, mp)
    rc, summary = _load(tmp, str(emb))
    assert rc == 0
    return summary


def run_pdf_extract(tmp: Path, mp: pytest.MonkeyPatch) -> dict:
    src = tmp / "note.txt"
    src.write_text("A plain text document with enough words to extract.\n", encoding="utf-8")
    report = tmp / "r.json"
    assert pdf_extract.main([str(src), "--out", str(tmp / "o.jsonl"),
                             "--report", str(report)]) == 0
    return json.loads(report.read_text())


def run_jats_extract(tmp: Path, mp: pytest.MonkeyPatch) -> dict:
    from tests.ingestion.test_jats_extract import _corpus

    corpus = _corpus(tmp)
    report = tmp / "r.json"
    assert jats_extract.main(["--corpus", str(corpus), "--out", str(tmp / "o.jsonl"),
                              "--report", str(report), "--no-token-budget"]) == 0
    return json.loads(report.read_text())


def run_load_graph(tmp: Path, mp: pytest.MonkeyPatch) -> dict:
    from ragstack.ingestion.archive import write_triples
    from ragstack.models import Triple
    from tests.ingestion.test_extract_graph import _version

    vdir = _version(tmp / "archive", 1)
    write_triples(vdir, [Triple(subject=f"s{i}", predicate="p", object="o", doc_id="d",
                                derived_by="llm", confidence=1) for i in range(2)])
    out = tmp / "graph.json"
    assert load_graph_cli.main(["--version-dir", str(vdir), "--graph-backend", "memory",
                                "--stamp-collection", "lib_x", "--out", str(out)]) == 0
    return json.loads(out.read_text())


RUNNERS = {
    "embed_shard.py": run_embed_shard,
    "merge_receipts.py": run_merge_receipts,
    "load_embeddings.py": run_load_embeddings,
    "pdf_extract.py": run_pdf_extract,
    "jats_extract.py": run_jats_extract,
    "load_graph.py": run_load_graph,
}


@pytest.fixture
def no_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ragstack.provenance.RELEASE_PATH", str(tmp_path / "no-RELEASE"))


@pytest.fixture
def in_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rel = tmp_path / "RELEASE"
    rel.write_text(RELEASE_TEXT)
    monkeypatch.setattr("ragstack.provenance.RELEASE_PATH", str(rel))


@pytest.mark.parametrize("script", sorted(RUNNERS))
def test_unseeded_record_is_never_unknown(script: str, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch, in_image) -> None:
    """Today's CWL command lines (no provenance flag): the record still says
    what it is — the image's RELEASE identity and the segmenter."""
    record = RUNNERS[script](tmp_path, monkeypatch)
    assert "provenance" in record, f"{script}: no provenance key"
    prov = read_provenance(record)
    assert prov != unknown_provenance()
    assert set(record["provenance"]) == set(PROVENANCE_KEYS)
    assert prov["segmenter_version"] == segmenter_version()
    assert (prov["image_version"], prov["image_commit"], prov["image_build"]) == \
        ("v9.9.9", "c" * 40, "2")
    assert prov["workflow_id"] is None and prov["tool_image"] is None


# --------------------------------------------------------------------------- #
# the record-specific tests
# --------------------------------------------------------------------------- #


def test_embed_shard_receipt_carries_provenance_even_unseeded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_image) -> None:
    _, receipt = _embed(tmp_path, monkeypatch, name="u")
    got = json.loads(receipt.read_text())["provenance"]
    assert set(got) == set(PROVENANCE_KEYS)
    assert got == {**unknown_provenance(), "segmenter_version": segmenter_version()}
    # the three flags round-trip, onto the receipt and into the header
    emb, receipt = _embed(tmp_path, monkeypatch, *SEEDED, name="seeded")
    got = json.loads(receipt.read_text())["provenance"]
    assert (got["workflow_id"], got["tool_image"], got["tool_image_digest"]) == \
        ("wf_821", "ragstack-tools-v9.9.9-b2.sif", "d" * 64)
    assert read_header(emb)["provenance"] == got
    assert ShardReceipt.load(str(receipt)).provenance == got


def test_embedding_file_header_provenance_round_trips_and_old_header_reads(
        tmp_path: Path) -> None:
    prov = {**unknown_provenance(), "workflow_id": "wf_1",
            "segmenter_version": segmenter_version()}
    chunk = Chunk(id="c1", doc_id="d1", content="x", embedding=[1.0, 2.0])
    with EmbeddingFileWriter(tmp_path / "new.emb.jsonl", tenant="t", provenance=prov) as w:
        w.write(chunk)
    chunks, header = read_embedding_file(tmp_path / "new.emb.jsonl")
    assert header["provenance"] == prov and [c.id for c in chunks] == ["c1"]
    # no provenance given: exactly the old header, and an old file reads
    with EmbeddingFileWriter(tmp_path / "old.emb.jsonl", tenant="t") as w:
        w.write(chunk)
    chunks, header = read_embedding_file(tmp_path / "old.emb.jsonl")
    assert "provenance" not in header and [c.id for c in chunks] == ["c1"]
    assert read_provenance(header) == unknown_provenance()


def test_merge_receipts_summary_carries_tool_and_shard_provenance(
        tmp_path: Path, no_image) -> None:
    p1 = {**unknown_provenance(), "image_version": "v1.6.20", "segmenter_version": "s"}
    p2 = {**unknown_provenance(), "image_version": "v1.6.18", "segmenter_version": "s"}
    files = []
    for i, prov in enumerate([p1, p2, p1, None]):
        f = tmp_path / f"r{i}.json"
        ShardReceipt(f"s{i}", "public", COMPLETED, n_docs=1, provenance=prov).write(str(f))
        files.append(str(f))
    out = tmp_path / "summary.json"
    assert merge_receipts.main([*files, "--out", str(out), *SEEDED]) == 0
    s = json.loads(out.read_text())
    assert s["provenance"]["workflow_id"] == "wf_821"
    assert s["provenance"]["segmenter_version"] == segmenter_version()
    assert s["shard_provenance"] == sorted([p1, p2], key=lambda p: json.dumps(p, sort_keys=True))
    assert s["n_shards_without_provenance"] == 1
    assert s["n_shards"] == 4


def test_load_summary_carries_provenance_on_the_load_path_not_only_replay(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_image) -> None:
    seeded, _ = _embed(tmp_path, monkeypatch, *SEEDED, name="a")
    # an embedding file from an embed step older than v1.6.20: no header provenance
    old = tmp_path / "old.emb.jsonl"
    lines = seeded.read_text().splitlines()
    header = json.loads(lines[0])
    header.pop("provenance")
    for i in range(1, len(lines)):
        rec = json.loads(lines[i])
        rec["id"], rec["doc_id"] = f"old-{i}", f"old-doc-{i}"
        lines[i] = json.dumps(rec, sort_keys=True)
    old.write_text("\n".join([json.dumps(header, sort_keys=True), *lines[1:]]) + "\n")
    rc, s = _load(tmp_path, str(seeded), str(old), "--workflow-id", "wf_load")
    assert rc == 0
    assert s["provenance"]["workflow_id"] == "wf_load"
    assert s["provenance"]["segmenter_version"] == segmenter_version()
    assert [p["workflow_id"] for p in s["embed_provenance"]] == ["wf_821"]
    assert s["n_files_without_provenance"] == 1
    # the cap refusal's summary too
    rc, s = _load(tmp_path, str(seeded), "--max-chunks", "1", "--workflow-id", "wf_cap")
    assert rc == 1 and "chunk_cap" in s
    assert s["provenance"]["workflow_id"] == "wf_cap"
    assert [p["workflow_id"] for p in s["embed_provenance"]] == ["wf_821"]


def test_extract_reports_carry_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          no_image) -> None:
    for runner in (run_pdf_extract, run_jats_extract):
        sub = tmp_path / runner.__name__
        sub.mkdir()
        got = runner(sub, monkeypatch)["provenance"]
        assert got == {**unknown_provenance(), "segmenter_version": segmenter_version()}
    src = tmp_path / "n.txt"
    src.write_text("Some words to extract here.\n")
    report = tmp_path / "seeded.json"
    assert pdf_extract.main([str(src), "--out", str(tmp_path / "o.jsonl"),
                             "--report", str(report), *SEEDED]) == 0
    assert json.loads(report.read_text())["provenance"]["workflow_id"] == "wf_821"
