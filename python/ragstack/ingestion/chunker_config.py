"""Shared chunker construction for the bulk ingesters.

Both ``scripts/ingest_jsonl.py`` and ``scripts/ingest_shard.py`` need to turn a
chunk-method + size/overlap + embedding-model into a ready chunker with its token
counter and per-chunk token budget resolved. That wiring is subtle — the
``fixed_token`` sliding window *requires* the HF offset tokenizer, and an
hf/endpoint backend without a model cannot be built at all — so it lives here
once rather than being copied into each tool (the #25 no-fork rule the ADR rests
on).

Every one of those conditions is a refusal, not a demotion: the estimator is
reached by asking for it (``--chunk-token-counter estimate``), never by a
degrade, because a silently substituted counter re-sizes the whole corpus.

``build_chunker`` returns the resolved ``token_counter`` and ``max_tokens``
alongside the chunker because callers reuse them (e.g. the doc-metrics writer and
the segmentation-cache fingerprint).
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from ragstack.ingestion.chunkers import make_chunker
from ragstack.ingestion.tokenization import (
    TokenCounter,
    make_token_counter,
    resolve_max_tokens,
)


def resolve_token_backend(
    method: str, token_backend: str, model: str | None,
    warn: Callable[[str], None],
) -> str:
    """The effective token-counter backend for ``method``.

    ``fixed_token`` forces ``hf`` (only :class:`HFTokenCounter` exposes the offset
    mapping its sliding window needs; an estimate/endpoint counter would collapse a
    doc to one whole-doc chunk) and requires a model.

    An hf/endpoint backend **without** a model raises. It used to degrade to
    ``estimate`` with a warning, which is the defect: sizing did keep "working",
    but with a chars-per-token heuristic instead of the model's tokenizer, so the
    same command produced a differently-chunked corpus and only a stderr line
    said so. The estimator is still available — by asking for it.
    """
    backend = token_backend
    if method == "fixed_token":
        if not model:
            raise ValueError(
                "chunk method 'fixed_token' requires an embedding model — its "
                "sliding token window is built from that model's HF tokenizer"
            )
        if backend != "hf":
            warn(f"[chunker] fixed_token needs the HF tokenizer; overriding token "
                 f"backend {backend!r} -> 'hf'.")
            backend = "hf"
    if backend in ("hf", "endpoint") and not model:
        raise ValueError(
            f"token counter backend {backend!r} needs an embedding model, and none "
            f"was given. Refusing to fall back to 'estimate': it would size chunks "
            f"by a chars-per-token heuristic instead of the model's tokenizer, "
            f"silently changing the chunking of the whole run. Pass the embedding "
            f"model (--embedding-model), or choose the estimator explicitly with "
            f"--chunk-token-counter estimate."
        )
    return backend


def build_chunker(
    method: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    model: str | None = None,
    token_backend: str = "hf",
    max_tokens: int | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    embed_fn=None,
    buffer_size: int = 3,
    breakpoint_percentile: float = 80.0,
    min_chunk_length: int = 500,
    breakpoint_max_tokens: int | None = None,
    breakpoint_token_counter: TokenCounter | None = None,
    max_breakpoint_sentences: int | None = 3000,
    on_warn: Callable[[str], None] | None = None,
) -> tuple[object, TokenCounter, int]:
    """Construct a chunker with its token counter + budget resolved.

    ``max_tokens`` is the ``--chunk-max-tokens`` override (the *model window*);
    ``None`` auto-detects from ``base_url``. Returns
    ``(chunker, token_counter, resolved_max_tokens)`` — the counter/budget are
    returned so callers can reuse them without re-deriving. The semantic-only
    breakpoint args are passed straight through to :func:`make_chunker`.
    """
    warn = on_warn or (lambda m: print(m, file=sys.stderr))
    backend = resolve_token_backend(method, token_backend, model, warn)
    token_counter = make_token_counter(
        backend, model=model, base_url=base_url, api_key=api_key
    )
    resolved_max = resolve_max_tokens(max_tokens, base_url=base_url, api_key=api_key)
    chunker = make_chunker(
        method,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        embed_fn=embed_fn,
        buffer_size=buffer_size,
        breakpoint_percentile_threshold=breakpoint_percentile,
        min_chunk_length=min_chunk_length,
        max_tokens=resolved_max,
        token_counter=token_counter,
        breakpoint_max_tokens=breakpoint_max_tokens,
        breakpoint_token_counter=breakpoint_token_counter,
        max_breakpoint_sentences=max_breakpoint_sentences,
    )
    return chunker, token_counter, resolved_max
