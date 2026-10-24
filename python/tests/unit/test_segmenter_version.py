"""``chunkers.segmenter_version()`` in production and experiment provenance
(#318 P2; honest about the backend since #815).

A sentence boundary is a chunk boundary and chunk ids are
``uuid5(doc_id:start:end)``, so which segmenter built a collection must be
readable from its records: the receipt / archive-manifest ``provenance`` object
(:func:`ragstack.provenance.tool_provenance`) and every sentence-span fingerprint.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from ragstack import provenance as p
from ragstack.ingestion import chunkers
from ragstack.ingestion.archive import read_manifest, write_version
from ragstack.ingestion.chunkers import (
    REGEX_SEGMENTER_VERSION,
    SEGMENTER_VERSION,
    segmenter_version,
)
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt


def test_segmenter_version_is_a_named_string():
    assert isinstance(SEGMENTER_VERSION, str) and SEGMENTER_VERSION.strip() == SEGMENTER_VERSION
    assert SEGMENTER_VERSION and SEGMENTER_VERSION != "punkt-bare"


def test_key_is_listed_and_set_by_tool_provenance(tmp_path: Path):
    assert "segmenter_version" in p.PROVENANCE_KEYS
    got = p.tool_provenance("wf_1", "img", "sha", release_path=tmp_path / "absent")
    assert tuple(got) == p.PROVENANCE_KEYS
    assert got["segmenter_version"] == segmenter_version()
    # Through the CLI flags every worker tool uses, too.
    parser = argparse.ArgumentParser()
    p.add_provenance_arguments(parser)
    assert p.provenance_from_args(parser.parse_args([]))["segmenter_version"] == segmenter_version()


def test_shard_receipt_round_trips_segmenter_version(tmp_path: Path):
    prov = p.tool_provenance("wf_1", "img", "sha", release_path=tmp_path / "absent")
    r = ShardReceipt("s", "public", COMPLETED, n_docs=1, provenance=prov)
    path = tmp_path / "r.json"
    r.write(path)
    assert json.loads(path.read_text())["provenance"]["segmenter_version"] == segmenter_version()
    back = ShardReceipt.load(path)
    assert back == r
    assert p.read_provenance({"provenance": back.provenance})["segmenter_version"] == segmenter_version()
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
    assert got["segmenter_version"] == segmenter_version()
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
    assert fp["segmenter_version"] == segmenter_version()
    # A label, not identity: the span sha256 already is the segmentation.
    rec = {"segmentation": [fp]}
    assert "segmenter_version" not in p.comparison_key(rec)["segmentation"][0]


# --------------------------------------------------------------------------- #
# #815: the recorded version names the backend actually in use
# --------------------------------------------------------------------------- #
_HAVE_NLTK = chunkers._punkt_sentence_spans("A b. C d.") is not None


@pytest.fixture
def fresh_probe(monkeypatch):
    """Forget the cached probe for the duration of a test (restored after)."""
    monkeypatch.setattr(chunkers, "_SEGMENTER_VERSION_CACHE", None)


@pytest.fixture
def no_nltk(monkeypatch, fresh_probe):
    """``import nltk`` raises ImportError: the regex fallback, as in every image
    before v1.6.15."""
    import sys

    monkeypatch.setitem(sys.modules, "nltk", None)


def test_the_two_backends_have_distinct_names():
    assert SEGMENTER_VERSION == "punkt-abbrev-v1"
    assert REGEX_SEGMENTER_VERSION == "regex-abbrev-v1"


@pytest.mark.skipif(not _HAVE_NLTK, reason="nltk not installed")
def test_with_nltk_every_writer_records_punkt(tmp_path: Path, fresh_probe):
    from ragstack.ingestion.segmentation_cache import SEGMENTER_KEY, config_fingerprint

    assert segmenter_version() == "punkt-abbrev-v1"
    prov = p.tool_provenance("wf_1", "img", "sha", release_path=tmp_path / "absent")
    assert prov["segmenter_version"] == "punkt-abbrev-v1"
    fp = p.sentence_spans_fingerprint(["Fig. 2 shows it. Done."])
    assert (fp["backend"], fp["segmenter_version"]) == ("punkt", "punkt-abbrev-v1")
    assert json.loads(config_fingerprint(method="sentence"))[SEGMENTER_KEY] == "punkt-abbrev-v1"


def test_without_nltk_every_writer_records_regex(tmp_path: Path, no_nltk):
    from ragstack.ingestion.segmentation_cache import SEGMENTER_KEY, config_fingerprint

    assert segmenter_version() == "regex-abbrev-v1"
    prov = p.tool_provenance("wf_1", "img", "sha", release_path=tmp_path / "absent")
    assert prov["segmenter_version"] == "regex-abbrev-v1"
    fp = p.sentence_spans_fingerprint(["Fig. 2 shows it. Done."])
    assert (fp["backend"], fp["segmenter_version"]) == ("regex", "regex-abbrev-v1")
    assert json.loads(config_fingerprint(method="sentence"))[SEGMENTER_KEY] == "regex-abbrev-v1"
    r = ShardReceipt("s", "public", COMPLETED, n_docs=1, provenance=prov)
    r.write(tmp_path / "r.json")
    assert json.loads((tmp_path / "r.json").read_text())["provenance"]["segmenter_version"] \
        == "regex-abbrev-v1"


@pytest.mark.skipif(not _HAVE_NLTK, reason="nltk not installed")
def test_a_regex_cache_key_never_serves_a_punkt_process(monkeypatch, fresh_probe):
    import sys

    from ragstack.ingestion.segmentation_cache import config_fingerprint

    cfg = {"method": "semantic", "buffer_size": 3, "pct": 80.0, "chunk_size": 512}
    punkt_key = config_fingerprint(**cfg)
    monkeypatch.setattr(chunkers, "_SEGMENTER_VERSION_CACHE", None)
    monkeypatch.setitem(sys.modules, "nltk", None)
    regex_key = config_fingerprint(**cfg)
    assert punkt_key != regex_key


def test_the_probe_is_cached(monkeypatch, fresh_probe):
    calls = []
    real = chunkers._punkt_sentence_spans

    def counting(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(chunkers, "_punkt_sentence_spans", counting)
    first = segmenter_version()
    assert segmenter_version() == first and len(calls) == 1


def test_the_regex_fallback_warns_once_per_process(no_nltk, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(chunkers, "_FALLBACK_WARNED", False)
    with caplog.at_level(logging.WARNING, logger=chunkers.__name__):
        for _ in range(3):
            chunkers.sentence_spans("One. Two. Three.")
    warned = [r for r in caplog.records
              if r.name == chunkers.__name__ and r.levelno == logging.WARNING]
    assert len(warned) == 1
    msg = warned[0].getMessage()
    assert "regex-abbrev-v1" in msg and 'pip install "ragstack[chunking]"' in msg


@pytest.mark.skipif(not _HAVE_NLTK, reason="nltk not installed")
def test_punkt_does_not_warn(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(chunkers, "_FALLBACK_WARNED", False)
    with caplog.at_level(logging.WARNING, logger=chunkers.__name__):
        chunkers.sentence_spans("One. Two. Three.")
    assert not [r for r in caplog.records if r.name == chunkers.__name__]
    assert chunkers._FALLBACK_WARNED is False
