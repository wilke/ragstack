"""``chunkers.SEGMENTER_VERSION`` in production and experiment provenance (#318 P2).

A sentence boundary is a chunk boundary and chunk ids are
``uuid5(doc_id:start:end)``, so which segmenter built a collection must be
readable from its records: the receipt / archive-manifest ``provenance`` object
(:func:`ragstack.provenance.tool_provenance`) and every sentence-span fingerprint.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ragstack import provenance as p
from ragstack.ingestion.archive import read_manifest, write_version
from ragstack.ingestion.chunkers import SEGMENTER_VERSION
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt


def test_segmenter_version_is_a_named_string():
    assert isinstance(SEGMENTER_VERSION, str) and SEGMENTER_VERSION.strip() == SEGMENTER_VERSION
    assert SEGMENTER_VERSION and SEGMENTER_VERSION != "punkt-bare"


def test_key_is_listed_and_set_by_tool_provenance(tmp_path: Path):
    assert "segmenter_version" in p.PROVENANCE_KEYS
    got = p.tool_provenance("wf_1", "img", "sha", release_path=tmp_path / "absent")
    assert tuple(got) == p.PROVENANCE_KEYS
    assert got["segmenter_version"] == SEGMENTER_VERSION
    # Through the CLI flags every worker tool uses, too.
    parser = argparse.ArgumentParser()
    p.add_provenance_arguments(parser)
    assert p.provenance_from_args(parser.parse_args([]))["segmenter_version"] == SEGMENTER_VERSION


def test_shard_receipt_round_trips_segmenter_version(tmp_path: Path):
    prov = p.tool_provenance("wf_1", "img", "sha", release_path=tmp_path / "absent")
    r = ShardReceipt("s", "public", COMPLETED, n_docs=1, provenance=prov)
    path = tmp_path / "r.json"
    r.write(path)
    assert json.loads(path.read_text())["provenance"]["segmenter_version"] == SEGMENTER_VERSION
    back = ShardReceipt.load(path)
    assert back == r
    assert p.read_provenance({"provenance": back.provenance})["segmenter_version"] == SEGMENTER_VERSION
    assert p.read_provenance(json.loads(path.read_text())) == prov


def test_archive_manifest_round_trips_segmenter_version(tmp_path: Path):
    from ragstack.ingestion.embedding_file import SCHEMA

    emb = tmp_path / "s.emb.jsonl"
    emb.write_text(json.dumps({"schema": SCHEMA, "tenant": "public", "dim": 2}) + "\n"
                   + json.dumps({"id": "c0", "doc_id": "d0", "content": "p0",
                                 "embedding": [0.1, 0.2], "metadata": {},
                                 "start_char": 0, "end_char": 2}) + "\n", encoding="utf-8")
    (tmp_path / "r.json").write_text(json.dumps({"shard_id": "s", "status": "completed"}))
    prov = p.tool_provenance("wf_1", "img", "sha", release_path=tmp_path / "absent")
    write_version(tmp_path / "out", 1, [emb], [tmp_path / "r.json"],
                  collection_id="c", tenant="public", workers=1, provenance=prov)
    got = p.read_provenance(read_manifest(tmp_path / "out" / "1"))
    assert got["segmenter_version"] == SEGMENTER_VERSION
    assert got == prov


def test_record_from_before_the_key_reads_null():
    """A receipt / manifest written before #318 P2 has no segmenter_version:
    it reads as null (its segmenter was the unrecorded ``punkt-bare``)."""
    old = {"provenance": {"workflow_id": "wf_0", "tool_image": "img", "tool_image_digest": None,
                          "image_version": "v1.6.14", "image_commit": "c" * 40,
                          "image_build": "1"}}
    got = p.read_provenance(old)
    assert got["segmenter_version"] is None and got["workflow_id"] == "wf_0"
    assert ShardReceipt.from_dict({"shard_id": "s", "status": "completed", **old}).provenance \
        == old["provenance"]


def test_sentence_spans_fingerprint_records_segmenter_version():
    fp = p.sentence_spans_fingerprint(["Fig. 2 shows it. Done."], sample="run")
    assert fp["segmenter_version"] == SEGMENTER_VERSION
    # A label, not identity: the span sha256 already is the segmentation.
    rec = {"segmentation": [fp]}
    assert "segmenter_version" not in p.comparison_key(rec)["segmentation"][0]
