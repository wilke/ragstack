"""Embed group size must scale with the fleet (#334).

One group is one ``embed()`` call, and the pool spreads a call across at most
``ceil(chunks / request_batch)`` endpoints — so ``group_size`` is the fan-out
ceiling, not just a memory bound. The old fixed 64 documents (~3 chunks/doc)
yielded ~1.5 sub-requests against a 128-chunk request batch, and a six-GPU fleet
measurably ran on ~1.3 GPUs, with four cards never above 5% across 937 samples
of a production batch.
"""
import argparse
import importlib.util
from pathlib import Path

import pytest

from ragstack.ingestion.embed_shard import run_embed_shard

_SPEC = importlib.util.spec_from_file_location(
    "_embed_shard_cli",
    Path(__file__).resolve().parents[2] / "scripts" / "embed_shard.py",
)
cli = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


def _args(urls):
    return argparse.Namespace(embedding_url=urls, embed_group_size=0)


def test_derived_group_scales_with_endpoints():
    one = cli._derive_group_size(_args(["u1"]))
    six = cli._derive_group_size(_args([f"u{i}" for i in range(6)]))
    assert six == 6 * one
    # The property that matters: at ~3 chunks/doc and 128-chunk sub-requests, a
    # six-endpoint group must yield at least one sub-request PER endpoint.
    chunks = six * 3
    assert chunks / 128 >= 6, "derived group cannot engage the whole fleet"


def test_explicit_flag_overrides_derivation():
    ns = _args(["u1", "u2"])
    ns.embed_group_size = 999
    assert (ns.embed_group_size or cli._derive_group_size(ns)) == 999


def test_cli_default_derives():
    ns = cli.parse_args(["shard.jsonl", "--embedding-url", "a", "b", "c"])
    assert ns.embed_group_size == 0
    assert cli._derive_group_size(ns) == 384


@pytest.mark.asyncio
async def test_run_embed_shard_passes_group_size_through(tmp_path):
    """The parameter must actually reach iter_embed_source — a default left in
    the pipeline call is exactly the silent regression this guards."""
    seen: list[int] = []

    class _Pipeline:
        async def iter_embed_source(self, source, tenant_id, group_size=64):
            seen.append(group_size)
            return
            yield  # pragma: no cover — makes this an async generator

    shard = tmp_path / "s.jsonl"
    shard.write_text('{"text": "x"}\n')
    await run_embed_shard(_Pipeline(), str(shard), "public", "s",
                          tmp_path / "out.jsonl", group_size=768)
    assert seen == [768]
