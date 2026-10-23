"""``--chunk-size-unit`` reaches the chunker from all three bulk tools (#318 P1).

Offline: the estimator counter is chosen explicitly and nothing is embedded.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import embed_shard as embed_shard_tool  # noqa: E402
import ingest_shard as ingest_shard_tool  # noqa: E402

_OFFLINE = ["--chunk-token-counter", "estimate", "--embedding-model", ""]


def _ingest_shard_args(*extra: str):
    return ingest_shard_tool.parse_args(
        ["x.jsonl", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
         *_OFFLINE, *extra]
    )


def _embed_shard_args(*extra: str):
    return embed_shard_tool.parse_args(["x.jsonl", *_OFFLINE, *extra])


@pytest.mark.parametrize("make_args", [_ingest_shard_args, _embed_shard_args])
@pytest.mark.parametrize("method", ["sentence", "words"])
def test_default_is_chars(make_args, method):
    args = make_args("--chunk-method", method)
    assert args.chunk_size_unit == "chars"
    build = (ingest_shard_tool if make_args is _ingest_shard_args else embed_shard_tool)._build_chunker
    assert build(args).size_unit == "chars"


@pytest.mark.parametrize("make_args", [_ingest_shard_args, _embed_shard_args])
@pytest.mark.parametrize("method", ["sentence", "words"])
def test_tokens_is_threaded(make_args, method):
    args = make_args("--chunk-method", method, "--chunk-size", "128", "--chunk-size-unit", "tokens")
    build = (ingest_shard_tool if make_args is _ingest_shard_args else embed_shard_tool)._build_chunker
    chunker = build(args)
    assert chunker.size_unit == "tokens"
    assert chunker.chunk_size == 128


@pytest.mark.parametrize("make_args", [_ingest_shard_args, _embed_shard_args])
def test_bad_unit_is_rejected_by_argparse(make_args):
    with pytest.raises(SystemExit):
        make_args("--chunk-size-unit", "token")


def test_ingest_jsonl_exposes_the_flag():
    """ingest_jsonl builds its parser inside main(); --help is the offline probe."""
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "ingest_jsonl.py"), "--help"],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    assert "--chunk-size-unit" in out
    assert "{chars,tokens}" in out
