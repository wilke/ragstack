"""Shared chunker construction for the bulk ingesters.

``scripts/ingest_jsonl.py``, ``scripts/ingest_shard.py`` and
``scripts/embed_shard.py`` all need to turn a chunk-method + size/overlap +
embedding-model into a ready chunker with its token counter and per-chunk token
budget resolved. That wiring is subtle — the
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
from collections.abc import Callable, Collection

from ragstack.ingestion.chunkers import CHUNK_METHODS, make_chunker
from ragstack.ingestion.tokenization import (
    TokenCounter,
    make_token_counter,
    resolve_max_tokens,
)

#: Chunk methods the **out-of-process** shard ingest cannot perform.
#:
#: ``scripts/ingest_shard.py`` is the step a GoWe scatter runs per shard. The
#: semantic methods embed sentence buffers *while* chunking, so they need a
#: synchronous embedding bridge; the shard step builds none, so there is nothing
#: for them to call. Issue #609 step 3 wires the bridge and empties this set.
#:
#: It lives here, beside :func:`build_chunker`, because THREE places have to
#: agree about it and two of them run in a different process from the third: the
#: tool's own refusal, the API's create-time guard, and the API's submit-time
#: guard. A divergence between them is precisely the defect #609 reports — the
#: API mints a semantic collection, then hands its uploads to a step that refuses
#: them 20 documents at a time, with retries that cannot help. One constant means
#: wiring the bridge re-opens all three call sites at once instead of leaving a
#: guard behind to refuse work the tool can now do.
#: The chunk methods that detect boundaries by EMBEDDING, and therefore need an
#: ``embed_fn``.
#:
#: The single source of this membership test. It was written out by hand in at
#: least five places (``api/deps.py`` ``_embed_fn_for``, ``routers/collections.py``,
#: ``ingest_jsonl.py`` twice, and the constant below), which is how a method gets
#: silently dropped to a different chunker when the set changes: a missed site does
#: not fail, it chunks differently. That matters most for a rename — the owner has
#: deferred ``semantic`` -> ``semantic_window`` (docs/plans/chunking-one-factory.md
#: §7d), and this constant is what keeps that a one-line change instead of a
#: five-site hunt.
SEMANTIC_METHODS: tuple[str, ...] = ("semantic", "semantic_pooled")


def needs_embed_fn(method: str | None) -> bool:
    """Does ``method`` detect boundaries by embedding, and so need an ``embed_fn``?"""
    return method in SEMANTIC_METHODS


#: Default for ``Settings.ingest_worker_unsupported_methods`` — see
#: :func:`shard_refusal`. A DEFAULT, not a fact about the library: which methods a
#: worker can run is a property of the deployed image, not of this source tree.
SHARD_UNSUPPORTED_METHODS: frozenset[str] = frozenset(SEMANTIC_METHODS)


def shard_supported_methods(
    unsupported: Collection[str] = SHARD_UNSUPPORTED_METHODS,
) -> tuple[str, ...]:
    """The chunk methods the shard ingest *can* perform, in ``CHUNK_METHODS`` order.

    Derived by subtraction rather than listed, so a method added to
    :data:`~ragstack.ingestion.chunkers.CHUNK_METHODS` is offered here without a
    second edit — the failure mode of a hand-kept list is that it silently stops
    naming a method the tool actually supports.
    """
    return tuple(m for m in CHUNK_METHODS if m not in unsupported)


def parse_unsupported_methods(raw: str | None) -> frozenset[str]:
    """Parse ``INGEST_WORKER_UNSUPPORTED_METHODS`` into a validated set.

    Empty/unset means nothing is refused — the state after an image carrying the
    wiring is rolled and the tenant is flipped.

    A name that is not a real chunk method raises. A typo would otherwise guard
    nothing while looking exactly like a guard that works, which is the failure
    mode this whole issue is about: the refusal is a *safety* mechanism, and a
    safety mechanism that silently does nothing is worse than none.
    """
    if not raw or not raw.strip():
        return frozenset()
    names = frozenset(n.strip() for n in raw.split(",") if n.strip())
    unknown = sorted(names - set(CHUNK_METHODS))
    if unknown:
        raise ValueError(
            f"INGEST_WORKER_UNSUPPORTED_METHODS names {', '.join(unknown)}, which "
            f"{'is not a chunk method' if len(unknown) == 1 else 'are not chunk methods'}; "
            f"valid: {', '.join(CHUNK_METHODS)}"
        )
    return names


def shard_refusal(
    method: str | None, *, unsupported: Collection[str] = SHARD_UNSUPPORTED_METHODS
) -> str | None:
    """Why the out-of-process ingest cannot chunk ``method``, or ``None`` when it can.

    One message for the API's 422 body and for any tool that refuses locally, so a
    user who hits this from the browser and an operator who hits it from the CLI
    read the same sentence and can find the same issue.

    ``unsupported`` is passed in rather than read from the module constant because
    **which methods a worker can run is a property of the deployed image, not of
    this source tree**. The API and the worker fleet are released and rolled
    separately; a constant can only be right for one fleet at a time, and this
    host runs two worker image directories. Callers pass
    ``settings.ingest_worker_unsupported_methods``, which ragstack-ctl can flip
    per tenant once an image carrying the wiring is rolled — and flip back, without
    a release, if the load check says no.
    """
    if method is None or method not in unsupported:
        return None
    return (
        f"chunk_method={method!r} is not yet wired for out-of-process bulk ingest: "
        f"it embeds sentence buffers while chunking and the shard step builds no "
        f"embedding bridge (issue #609). Supported on this path: "
        f"{', '.join(shard_supported_methods(unsupported))}."
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
