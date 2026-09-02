"""Tests for the shared chunker factory (build_chunker) used by both bulk
ingesters. Offline — no network/tokenizer download except where patched.

Regression-guards the #133 blocker: `fixed_token` must receive a token_counter,
not crash in make_chunker.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ragstack.ingestion import chunker_config
from ragstack.ingestion.chunker_config import build_chunker, resolve_token_backend
from ragstack.ingestion.tokenization import EstimatingTokenCounter
from tests.pinned_env_support import DEAD_URL, pinned_env

#: ``python/`` — the scripts dir and the child's PYTHONPATH both hang off it.
CHECKOUT_ROOT = Path(__file__).resolve().parents[2]


def test_build_chunker_refuses_hf_without_a_model() -> None:
    """No model + the default ``hf`` backend → refuse, don't quietly estimate.

    This test used to assert the opposite (``test_fixed_uses_estimate_without_
    model_offline``): it pinned the degrade as intended behaviour. The degrade is
    the defect — sizing kept "working" while the corpus got chunked by a
    chars-per-token heuristic instead of the model's tokenizer, and only a stderr
    warning marked the difference. The message must name the two ways out, since
    a caller who really has no model needs one of them.
    """
    with pytest.raises(ValueError) as excinfo:
        build_chunker("fixed", chunk_size=200, chunk_overlap=20)
    msg = str(excinfo.value)
    assert "--embedding-model" in msg
    assert "--chunk-token-counter estimate" in msg


def test_fixed_with_explicit_estimate_still_works_offline() -> None:
    """The opt-in the refusal above must leave intact.

    Same call, one flag: ``token_backend='estimate'``. Zero-dep, no network, no
    tokenizer. Guards the over-reaching mutation where the new refusal also
    swallows the explicitly-chosen estimator — which would leave a
    no-tokenizer operator with no working path at all.
    """
    chunker, counter, max_tokens = build_chunker(
        "fixed", chunk_size=200, chunk_overlap=20, token_backend="estimate"
    )
    assert chunker is not None
    assert isinstance(counter, EstimatingTokenCounter)
    assert max_tokens > 0


def test_fixed_token_requires_model() -> None:
    with pytest.raises(ValueError, match="requires an embedding model"):
        build_chunker("fixed_token", chunk_size=256, chunk_overlap=32)


def test_fixed_token_gets_token_counter(monkeypatch) -> None:
    # THE blocker regression: with a model, build_chunker must hand make_chunker a
    # (non-None) token_counter — previously ingest_shard called make_chunker with
    # none, crashing with "fixed_token requires a token_counter". Patch the
    # resolvers + make_chunker so the test needs no HF download / real endpoint and
    # doesn't hit FixedTokenWindowChunker's HF-tokenizer requirement.
    class _DummyCounter:
        pass

    captured: dict = {}

    def fake_make_chunker(method, **kw):
        captured["method"] = method
        captured["token_counter"] = kw.get("token_counter")
        captured["max_tokens"] = kw.get("max_tokens")
        return object()

    monkeypatch.setattr(chunker_config, "make_token_counter", lambda *a, **k: _DummyCounter())
    monkeypatch.setattr(chunker_config, "resolve_max_tokens", lambda *a, **k: 512)
    monkeypatch.setattr(chunker_config, "make_chunker", fake_make_chunker)
    chunker, counter, max_tokens = build_chunker(
        "fixed_token", chunk_size=256, chunk_overlap=32, model="some-model"
    )
    assert chunker is not None
    assert captured["method"] == "fixed_token"
    assert captured["token_counter"] is not None  # a counter IS passed now
    assert isinstance(counter, _DummyCounter) and max_tokens == 512


def test_resolve_token_backend_rules() -> None:
    warns: list[str] = []
    # fixed_token forces hf even if asked for estimate...
    assert resolve_token_backend("fixed_token", "estimate", "m", warns.append) == "hf"
    assert warns  # ...and warns about the override
    # a hf/endpoint request with no model is a hard error — it used to degrade to
    # 'estimate' with a warning, which silently re-sized every chunk in the run.
    # Both backends refuse; neither is allowed to pick a different one.
    for backend in ("hf", "endpoint"):
        with pytest.raises(ValueError, match="--chunk-token-counter estimate"):
            resolve_token_backend("fixed", backend, None, lambda m: None)
    # ...but the explicitly-chosen estimator is untouched: it needs no model.
    assert resolve_token_backend("fixed", "estimate", None, lambda m: None) == "estimate"
    # fixed_token with no model is a hard error
    with pytest.raises(ValueError):
        resolve_token_backend("fixed_token", "hf", None, lambda m: None)


def test_ingest_shard_refuses_a_token_budget_it_cannot_count(tmp_path) -> None:
    """End-to-end backstop: the CLI exits before any I/O when 'hf' can't load.

    The unit tests above pin the factory and the backend resolver. This pins the
    thing an operator actually runs, and closes the mutation that survives both:
    strictness added to the factory, then swallowed by a ``try/except`` in
    ``build_chunker`` or in the CLI's own wrapper "for robustness". A subprocess
    is the only way to see that — an in-process test would monkeypatch past it.

    ``sentence`` is the method that matters. ``fixed_token`` (the CLI default)
    already fails loudly downstream, because ``FixedTokenWindowChunker`` rejects
    any counter without an offset map; the token-budgeted *packing* methods
    (sentence/words/semantic) accept whatever counter they are handed, so they
    are where a demoted counter used to silently produce a ~1.4x-mis-sized
    corpus. That is precisely the chunker the large corpus build plans to use.

    **Why the exit code alone would be vacuous:** the shard file below does not
    exist, so this invocation exits non-zero on unfixed code too. The
    discriminating assertions are the stderr content and the absent receipt.
    Every store/sidecar URL is dead-ported and the HF cache is redirected at an
    empty directory with the hub offline, so nothing here can reach a live
    service or the network.
    """
    out = tmp_path / "receipt.json"
    proc = subprocess.run(
        [
            sys.executable, str(CHECKOUT_ROOT / "scripts" / "ingest_shard.py"),
            str(tmp_path / "shard.jsonl"),
            "--out", str(out),
            "--chunk-method", "sentence",
            "--chunk-max-tokens", "512",
            "--embedding-model", "ragstack-tests/no-such-tokenizer",
            # Both in-memory: satisfies the CLI's both-durable-or-both-memory
            # guard and skips registry resolution, so the chunker build is the
            # first thing that can fail.
            "--vector-backend", "memory",
            "--text-backend", "memory",
            # argparse still requires these two (#454); dead-port them.
            "--qdrant-url", DEAD_URL,
            "--es-url", DEAD_URL,
            "--embedding-url", DEAD_URL,
        ],
        capture_output=True, text=True, timeout=120,
        env=pinned_env(
            {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(CHECKOUT_ROOT)},
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            HF_HOME=str(tmp_path / "empty-hf-home"),
        ),
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined[-800:]
    # The refusal itself, not just "something failed".
    assert "token counter backend 'hf'" in combined, (
        "ingest_shard did not refuse the unloadable tokenizer — it proceeded on a "
        f"substituted counter, which mis-sizes every chunk: {combined[-800:]}"
    )
    for hatch in ("--chunk-token-counter endpoint", "--chunk-token-counter estimate"):
        assert hatch in combined, f"the refusal does not name {hatch}: {combined[-800:]}"
    assert not out.exists(), (
        "a receipt was written: the run got past the chunker build and did work "
        "before failing, so the refusal is not the fail-fast guard it claims to be"
    )
