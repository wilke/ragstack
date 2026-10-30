"""Glossary benchmark round 2, tooling blocker B1: ``glossary_file.py subset``
and ``shard-subset`` (keep / drop by document id). Offline: synthetic
documents, no store, no endpoint.

A post-freeze tooling addition: the subsets copy rows and shard lines
unchanged, so no glossary, version stamp or document text differs from the
source's.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.ingestion.glossary_file import (
    SHARD_SUBSET_SCHEMA,
    GlossaryFileError,
    file_sha256,
    merge_files,
    read_glossary_file,
    read_id_list,
    row_to_obj,
    subset_file,
    subset_shard,
)
from ragstack.ingestion.loaders import JsonlLoader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import glossary_file as gf_cli  # noqa: E402

PAD = " ".join(["filler words keep the definitions apart"] * 4)
DOCS = {
    "p1": "The rate Qb is the breathing flow rate. " + PAD + " Table: Qb = 0.5 m3/h.",
    "p2": "We introduce the deactivation rate Kv, a key constant. " + PAD + " Kv = 0.3 per hour.",
    "p3": "No symbols here, only words. " + PAD,
    "p4": "Here Zq is the fade rate of the signal. " + PAD + " Zq = 3.",
    "p5": "Let Wm denote the window width. " + PAD + " Wm = 7.",
}


def _shard(path: Path, docs: dict[str, str], extra_lines: tuple[str, ...] = ()) -> Path:
    lines = [json.dumps({"text": t, "path": f"{n}.pdf", "metadata": {}}) for n, t in docs.items()]
    path.write_text("\n".join([*lines[:2], *extra_lines, *lines[2:]]) + "\n", encoding="utf-8")
    return path


def _ids(path: Path, shard: Path, names: tuple[str, ...]) -> Path:
    by_name = {Path(d.source).stem: d.id for d in JsonlLoader().load(str(shard))}
    path.write_text("".join(by_name[n] + "\n" for n in names), encoding="utf-8")
    return path


@pytest.fixture
def built(tmp_path: Path) -> dict[str, Path]:
    shard = _shard(tmp_path / "shard.jsonl", DOCS)
    gfile = tmp_path / "glossary.jsonl"
    assert gf_cli.main(["build", "--jsonl", str(shard), "--out", str(gfile)]) == 0
    return {"shard": shard, "gfile": gfile, "ids": _ids(tmp_path / "eval.txt", shard, ("p2", "p4")),
            "dir": tmp_path}


def _rows(path: Path) -> dict[str, dict]:
    return {d: row_to_obj(r) for d, r in read_glossary_file(path).rows.items()}


# --- glossary files -----------------------------------------------------------


def test_keep_and_drop_partition_and_merge_back_to_the_original_rows(built: dict[str, Path]) -> None:
    d = built["dir"]
    keep = subset_file(built["gfile"], d / "keep.jsonl", built["ids"], "keep")
    drop = subset_file(built["gfile"], d / "drop.jsonl", built["ids"], "drop")
    ids = set(read_id_list(built["ids"]))
    assert set(_rows(d / "keep.jsonl")) == ids
    assert set(_rows(d / "drop.jsonl")).isdisjoint(ids)
    assert keep["rows"] == 2 and drop["rows"] == 3 and keep["source_rows"] == 5
    merge_files([d / "keep.jsonl", d / "drop.jsonl"], d / "merged.jsonl")
    # every row, byte-equal as an object: glossaries, versions, provenance, identity
    assert _rows(d / "merged.jsonl") == _rows(built["gfile"])
    orig = read_glossary_file(built["gfile"])
    merged = read_glossary_file(d / "merged.jsonl")
    assert merged.header["counts"] == orig.header["counts"]
    assert merged.glossary_versions == orig.glossary_versions
    assert merged.header["config"] == orig.header["config"]


def test_kept_rows_keep_their_versions_and_stamps(built: dict[str, Path]) -> None:
    subset_file(built["gfile"], built["dir"] / "keep.jsonl", built["ids"], "keep")
    src = read_glossary_file(built["gfile"]).rows
    for doc_id, row in read_glossary_file(built["dir"] / "keep.jsonl").rows.items():
        assert row.glossary_version == src[doc_id].glossary_version
        assert row.stamped().version == src[doc_id].stamped().version
        assert row.provenance == src[doc_id].provenance


def test_subset_records_source_ids_and_mode_and_is_deterministic(built: dict[str, Path]) -> None:
    d = built["dir"]
    a = subset_file(built["gfile"], d / "a.jsonl", built["ids"], "drop")
    subset_file(built["gfile"], d / "b.jsonl", built["ids"], "drop")
    assert (d / "a.jsonl").read_bytes() == (d / "b.jsonl").read_bytes()
    h = read_glossary_file(d / "a.jsonl").header
    src_h = read_glossary_file(built["gfile"]).header
    assert h["created"] == src_h["created"]
    assert "subset --drop" in h["producer"]
    assert file_sha256(built["gfile"]) in h["producer"] and file_sha256(built["ids"]) in h["producer"]
    assert src_h["producer"] in h["producer"]
    assert a["source_sha256"] == file_sha256(built["gfile"]) and a["ids_sha256"] == file_sha256(built["ids"])
    assert a["out_sha256"] == file_sha256(d / "a.jsonl") and a["mode"] == "drop"


def test_unknown_ids_are_refused_unless_allowed(built: dict[str, Path]) -> None:
    d = built["dir"]
    ids = d / "ids.txt"
    ids.write_text(built["ids"].read_text() + "not-a-doc\n", encoding="utf-8")
    with pytest.raises(GlossaryFileError, match="not in"):
        subset_file(built["gfile"], d / "o.jsonl", ids, "keep")
    assert not (d / "o.jsonl").exists()
    r = subset_file(built["gfile"], d / "o.jsonl", ids, "keep", allow_missing=True)
    assert r["ids_missing"] == 1 and r["rows"] == 2
    assert "1 not in source" in read_glossary_file(d / "o.jsonl").header["producer"]


def test_subset_refusals(built: dict[str, Path]) -> None:
    d = built["dir"]
    with pytest.raises(GlossaryFileError, match="unknown subset mode"):
        subset_file(built["gfile"], d / "o.jsonl", built["ids"], "only")
    with pytest.raises(GlossaryFileError, match="is the input"):
        subset_file(built["gfile"], built["gfile"], built["ids"], "keep")
    with pytest.raises(GlossaryFileError, match="is the id list"):
        subset_file(built["gfile"], built["ids"], built["ids"], "keep")
    dup = d / "dup.txt"
    dup.write_text("x\nx\n", encoding="utf-8")
    with pytest.raises(GlossaryFileError, match="duplicate id"):
        subset_file(built["gfile"], d / "o.jsonl", dup, "keep")
    empty = d / "empty.txt"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(GlossaryFileError, match="empty"):
        subset_file(built["gfile"], d / "o.jsonl", empty, "drop")
    assert not (d / "o.jsonl").exists()


def test_subset_cli(built: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    d = built["dir"]
    out = d / "keep.jsonl"
    assert gf_cli.main(["subset", "--keep", str(built["ids"]), str(built["gfile"]), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "keep" and printed["rows"] == 2
    assert gf_cli.main(["verify", str(out)]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):  # --keep and --drop are exclusive, and one is required
        gf_cli.main(["subset", str(built["gfile"]), "--out", str(out)])
    assert gf_cli.main(["subset", "--drop", str(built["ids"]), str(built["gfile"]),
                        "--out", str(built["gfile"])]) == 2


# --- JsonlLoader shards ---------------------------------------------------------


def test_shard_keep_and_drop_load_exactly_the_source_documents(built: dict[str, Path]) -> None:
    d = built["dir"]
    shard = _shard(d / "noisy.jsonl", DOCS, extra_lines=("{not json", json.dumps({"text": "", "path": "e.pdf"})))
    k = subset_shard(shard, d / "s-keep.jsonl", built["ids"], "keep")
    r = subset_shard(shard, d / "s-drop.jsonl", built["ids"], "drop")
    assert k["documents"] == 2 and r["documents"] == 3 and k["lines_without_document"] == 2
    loader = JsonlLoader()
    src = {x.id: x.content for x in loader.load(str(shard))}
    kept = {x.id: x.content for x in loader.load(str(d / "s-keep.jsonl"))}
    dropped = {x.id: x.content for x in loader.load(str(d / "s-drop.jsonl"))}
    assert set(kept) == set(read_id_list(built["ids"])) and set(kept).isdisjoint(dropped)
    assert {**kept, **dropped} == src
    # kept lines are the source's lines, byte for byte and in order
    src_lines = shard.read_bytes().splitlines()
    for part in ("s-keep.jsonl", "s-drop.jsonl"):
        lines = (d / part).read_bytes().splitlines()
        assert all(x in src_lines for x in lines)
        assert [src_lines.index(x) for x in lines] == sorted(src_lines.index(x) for x in lines)


def test_shard_subset_glossaries_merge_back_to_the_full_shards(built: dict[str, Path]) -> None:
    d = built["dir"]
    subset_shard(built["shard"], d / "s-keep.jsonl", built["ids"], "keep")
    subset_shard(built["shard"], d / "s-drop.jsonl", built["ids"], "drop")
    for part in ("keep", "drop"):
        assert gf_cli.main(["build", "--jsonl", str(d / f"s-{part}.jsonl"), "--out", str(d / f"g-{part}.jsonl")]) == 0
    merge_files([d / "g-keep.jsonl", d / "g-drop.jsonl"], d / "g-merged.jsonl")
    assert _rows(d / "g-merged.jsonl") == _rows(built["gfile"])
    # and the shard subset agrees with the glossary-file subset of the same list
    subset_file(built["gfile"], d / "keep.jsonl", built["ids"], "keep")
    assert _rows(d / "g-keep.jsonl") == _rows(d / "keep.jsonl")


def test_shard_subset_record_and_determinism(built: dict[str, Path]) -> None:
    d = built["dir"]
    a = subset_shard(built["shard"], d / "a.jsonl", built["ids"], "keep")
    subset_shard(built["shard"], d / "b.jsonl", built["ids"], "keep")
    assert (d / "a.jsonl").read_bytes() == (d / "b.jsonl").read_bytes()
    rec = json.loads((d / "a.jsonl.subset.json").read_text())
    assert rec["schema"] == SHARD_SUBSET_SCHEMA and rec["mode"] == "keep"
    assert rec["source_sha256"] == file_sha256(built["shard"]) and rec["ids_sha256"] == file_sha256(built["ids"])
    assert rec["out_sha256"] == file_sha256(d / "a.jsonl") == a["out_sha256"]
    assert (d / "a.jsonl.subset.json").read_bytes() == (d / "b.jsonl.subset.json").read_bytes()


def test_shard_subset_refusals(built: dict[str, Path]) -> None:
    d = built["dir"]
    ids = d / "ids.txt"
    ids.write_text("not-a-doc\n", encoding="utf-8")
    with pytest.raises(GlossaryFileError, match="not in"):
        subset_shard(built["shard"], d / "o.jsonl", ids, "drop")
    assert not (d / "o.jsonl").exists()
    assert subset_shard(built["shard"], d / "o.jsonl", ids, "drop", allow_missing=True)["documents"] == 5
    dup = _shard(d / "dup.jsonl", {"p1": DOCS["p1"]}, extra_lines=(json.dumps(
        {"text": "other text", "path": "p1.pdf", "metadata": {}}),))
    with pytest.raises(GlossaryFileError, match="duplicate document id"):
        subset_shard(dup, d / "o2.jsonl", built["ids"], "keep", allow_missing=True)
    with pytest.raises(GlossaryFileError, match="is the input shard"):
        subset_shard(built["shard"], built["shard"], built["ids"], "keep")


def test_shard_subset_cli(built: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    out = built["dir"] / "eval-shard.jsonl"
    assert gf_cli.main(["shard-subset", "--keep", str(built["ids"]), str(built["shard"]),
                        "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["documents"] == 2 and printed["record"].endswith(".subset.json")
    assert gf_cli.main(["verify", str(built["gfile"]), "--jsonl", str(out)]) == 0
