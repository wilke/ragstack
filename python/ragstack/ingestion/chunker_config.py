"""Shared chunker construction — the bulk ingesters AND the API.

``scripts/ingest_jsonl.py``, ``scripts/ingest_shard.py``,
``scripts/embed_shard.py`` and the API's in-process builders (``api/deps.py``
``_chunker_for`` / ``_build_chunker``, since #647) all need to turn a
chunk-method + size/overlap + embedding-model into a ready chunker with its token counter and per-chunk token
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
import threading
from collections.abc import Callable, Collection
from typing import Any

from ragstack.ingestion.chunkers import (
    BREAKPOINT_CAPS,
    CAP_SPLITS,
    CHUNK_METHODS,
    DEFAULT_BREAKPOINT_CAP,
    DEFAULT_CAP_SPLIT,
    DEFAULT_SIZE_UNIT,
    MAX_SIZE_TOLERANCE,
    SIZE_CAPS,
    SIZE_UNITS,
    default_min_piece,
    make_chunker,
)
from ragstack.ingestion.tokenization import (
    HFTokenCounter,
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


#: ``chunk_params`` keys of the semantic size cap (see ``chunkers.SIZE_CAPS`` /
#: ``chunkers.CAP_SPLITS``). They ride in ``chunk_params``, so they are already
#: part of ``chunk_descriptor()`` -> ``spec_hash`` -> the physical name, with no
#: contract change: ``chunk_params`` is free-form in the schema.
SIZE_CAP_PARAM = "size_cap"
CAP_SPLIT_PARAM = "cap_split"
BREAKPOINT_CAP_PARAM = "breakpoint_cap"
#: Every size-cap key, in the order the API records them.
SEMANTIC_CAP_PARAMS: tuple[str, ...] = (SIZE_CAP_PARAM, CAP_SPLIT_PARAM, BREAKPOINT_CAP_PARAM)
#: The OPT-IN size tolerance (#831): ``size_tolerance`` (a fraction) turns it on
#: and ``min_piece`` (tokens) sets the merge threshold. Absent = the strict cap.
SIZE_TOLERANCE_PARAM = "size_tolerance"
MIN_PIECE_PARAM = "min_piece"
SIZE_TOLERANCE_PARAMS: tuple[str, ...] = (SIZE_TOLERANCE_PARAM, MIN_PIECE_PARAM)


def _size_tolerance_from_params(params: dict, recorded_size: int) -> dict[str, Any]:
    """The tolerance kwargs (``size_tolerance``, ``min_piece``) of a CAPPED
    semantic entry, or ``{}`` when it records no ``size_tolerance`` (strict).

    ``size_tolerance``: a number in ``[0, MAX_SIZE_TOLERANCE]`` (not a bool).
    ``min_piece``: whole tokens in ``[0, recorded_size]``; absent under a recorded
    tolerance is :func:`~ragstack.ingestion.chunkers.default_min_piece` (the API
    records it explicitly). A ``min_piece`` without ``size_tolerance`` is refused:
    it would look applied while merging nothing.
    """
    tol = params.get(SIZE_TOLERANCE_PARAM)
    piece = params.get(MIN_PIECE_PARAM)
    if tol is None:
        if piece is not None:
            raise ValueError(
                f"chunk param {MIN_PIECE_PARAM!r} needs {SIZE_TOLERANCE_PARAM!r}: the "
                f"small-piece merge is part of the opt-in size tolerance"
            )
        return {}
    if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not (
        0.0 <= float(tol) <= MAX_SIZE_TOLERANCE
    ):
        raise ValueError(
            f"chunk param {SIZE_TOLERANCE_PARAM!r} must be a number between 0 and "
            f"{MAX_SIZE_TOLERANCE}; got {tol!r}"
        )
    if piece is None:
        piece = default_min_piece(recorded_size)
    if (isinstance(piece, bool) or not isinstance(piece, (int, float))
            or (isinstance(piece, float) and not piece.is_integer())
            or not 0 <= piece <= recorded_size):
        raise ValueError(
            f"chunk param {MIN_PIECE_PARAM!r} must be a whole number of tokens between "
            f"0 and chunk_size ({recorded_size}); got {piece!r}"
        )
    return {"size_tolerance": float(tol), "min_piece": int(piece)}


def semantic_cap_from_params(
    method: str | None, params: dict | None, *, recorded_size: int | None
) -> dict[str, Any]:
    """The ``build_chunker`` kwargs (``size_cap``, ``cap_split``, ``breakpoint_cap``)
    a collection's
    recorded ``chunk_params`` set — THE one reader, for the API's in-process
    builder and for the bulk tools alike.

    * Not a semantic method -> ``{}``: the keys are ignored (the create router
      refuses them there; an existing entry carrying junk must not fail a shard,
      the same leniency ``ingest_shard._semantic_params`` has).
    * No ``size_cap`` recorded -> ``{}`` unless ``cap_split`` or
      ``breakpoint_cap`` is (refused): the collection
      predates the marker (or never asked for it) and is chunked exactly as
      before, with only the model window as a cap.
    * ``size_cap`` must be one of :data:`~ragstack.ingestion.chunkers.SIZE_CAPS`,
      ``cap_split`` one of :data:`~ragstack.ingestion.chunkers.CAP_SPLITS`; a
      ``cap_split`` without a ``size_cap`` is refused (it would look applied
      while splitting nothing differently). Absent ``cap_split`` under a cap is
      ``greedy`` — but the API records it explicitly at create, so a later change
      of that default cannot re-chunk an existing collection. Likewise
      ``breakpoint_cap`` (one of ``BREAKPOINT_CAPS``; absent under a cap is
      ``"size"``, the study arm, and the API records it explicitly).

    * A recorded ``size_cap`` needs a recorded ``chunk_size`` (``recorded_size``,
      the entry's own value): the cap IS that number, and an unrecorded one would
      be filled in differently by each path (the API's ``CHUNK_SIZE`` setting,
      the shard tool's workflow input) — one spec, two caps.

    * The opt-in size tolerance (#831): ``size_tolerance`` / ``min_piece`` are
      read only under a recorded cap (either one without ``size_cap`` is
      refused, like ``cap_split``) and returned only when ``size_tolerance`` is
      recorded — a strict entry returns exactly the three keys above.

    Raises ``ValueError`` naming the key (the API's 400; the shard tool turns it
    into a SystemExit).
    """
    if not needs_embed_fn(method):
        return {}
    params = params or {}
    cap = params.get(SIZE_CAP_PARAM)
    split = params.get(CAP_SPLIT_PARAM)
    bp = params.get(BREAKPOINT_CAP_PARAM)
    if cap is None:
        for key, value in ((CAP_SPLIT_PARAM, split), (BREAKPOINT_CAP_PARAM, bp),
                           (SIZE_TOLERANCE_PARAM, params.get(SIZE_TOLERANCE_PARAM)),
                           (MIN_PIECE_PARAM, params.get(MIN_PIECE_PARAM))):
            if value is not None:
                raise ValueError(
                    f"chunk param {key!r} needs {SIZE_CAP_PARAM!r}="
                    f"{SIZE_CAPS[0]!r}: without a recorded size cap a semantic "
                    f"collection is chunked as before (window-only, greedy)"
                )
        return {}
    if not isinstance(cap, str) or cap not in SIZE_CAPS:
        raise ValueError(
            f"chunk param {SIZE_CAP_PARAM!r} must be one of {', '.join(SIZE_CAPS)}; got {cap!r}"
        )
    if recorded_size is None:
        raise ValueError(
            f"chunk param {SIZE_CAP_PARAM!r}={cap!r} makes chunk_size the token cap, "
            f"but the collection records no chunk_size"
        )
    if split is None:
        split = DEFAULT_CAP_SPLIT
    if not isinstance(split, str) or split not in CAP_SPLITS:
        raise ValueError(
            f"chunk param {CAP_SPLIT_PARAM!r} must be one of {', '.join(CAP_SPLITS)}; "
            f"got {split!r}"
        )
    if bp is None:
        bp = DEFAULT_BREAKPOINT_CAP
    if not isinstance(bp, str) or bp not in BREAKPOINT_CAPS:
        raise ValueError(
            f"chunk param {BREAKPOINT_CAP_PARAM!r} must be one of "
            f"{', '.join(BREAKPOINT_CAPS)}; got {bp!r}"
        )
    out: dict[str, Any] = {"size_cap": cap, "cap_split": split, "breakpoint_cap": bp}
    out.update(_size_tolerance_from_params(params, recorded_size))
    return out


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


#: ``--chunk-size-unit`` choices and help, shared by the three bulk tools so they
#: say the same thing (#318 P1). See :data:`ragstack.ingestion.chunkers.SIZE_UNITS`.
CHUNK_SIZE_UNIT_CHOICES: tuple[str, ...] = SIZE_UNITS
CHUNK_SIZE_UNIT_HELP = (
    "what --chunk-size counts for sentence/words: 'chars' (default) or 'tokens'. "
    "Either way the model window is a cap: a chunk over it is re-packed at "
    "sentence/word boundaries, and a single sentence/word longer than the budget "
    "is split. --chunk-overlap stays chars. Ignored by other methods."
)

#: The methods whose ``chunk_size`` unit is selectable (#318 P1); every other
#: method ignores ``size_unit``.
SIZE_UNIT_METHODS: tuple[str, ...] = ("sentence", "words")


def size_unit_from_params(method: str | None, params: Any) -> str:
    """What ``chunk_size`` counts for ``method``, read from a registry entry's
    ``chunk_params`` — the one reader for this identity value (#318, #828 PR 3).

    **Method-conditional.** Only ``sentence``/``words`` read the unit:
    ``create_collection`` persists ``chunk_params`` for any method but validates
    them only for the methods that use them, so a ``fixed``/``fixed_token``
    collection can legitimately carry junk here, and reading it unconditionally
    would refuse work on the path every current collection uses. For any other
    method this returns ``"chars"`` without looking. An entry that records no
    unit means ``"chars"``, the historical behaviour every existing collection
    was built with. A recorded value that is not a unit raises ``ValueError``
    naming the key.

    Called by ``ingest_shard._size_unit``, by
    :func:`ragstack.ops.ingest_target.refuse_size_unit_mismatch` (``ingest_jsonl``
    and ``embed_shard``) and by the load-side header check
    (:func:`ragstack.ops.ingest_target.check_header_chunking`), so the three
    readings cannot drift apart.
    """
    if method not in SIZE_UNIT_METHODS:
        return DEFAULT_SIZE_UNIT
    unit = dict(params or {}).get("size_unit")
    if unit is None:
        return DEFAULT_SIZE_UNIT
    if unit not in SIZE_UNITS:
        raise ValueError(
            f"chunk_params['size_unit'] must be one of {', '.join(SIZE_UNITS)}; "
            f"got {unit!r}"
        )
    return str(unit)


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


#: ``resolve_max_tokens``'s ``default`` when the caller wants to KNOW that no
#: window was found. Every budget it returns on success (and every explicit one) is
#: clamped to ``>= 1``, so ``0`` cannot be confused with a detected window.
_UNDETECTED_WINDOW = 0

#: The budget a non-strict build falls back to when nothing reports a window —
#: ``resolve_max_tokens``'s own default, kept for the bulk tools' behaviour.
_FALLBACK_BUDGET = 4096


class ModelWindowUnavailable(RuntimeError):
    """No per-chunk token budget could be determined, and the caller refused to guess.

    A dependency failure, not a client error: no change to the request would fix
    it. So it is a ``RuntimeError`` (like ``TokenCounterUnavailable``), never a
    ``ValueError`` — the ingest router maps ``ValueError`` to 400, and maps this
    to 503 explicitly. ``transient`` is True when an endpoint IS configured but did
    not report a window (down, slow, malformed: worth a ``Retry-After``), False
    when there is none to ask (an operator has to act).

    The message deliberately names no endpoint URL: it crosses the API boundary,
    and the URL is an internal address — the operator finds it in the WARNING
    ``resolve_max_tokens`` logs just before.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


#: Real HF token counters, one per model, for the life of the process.
#:
#: ``make_token_counter("hf", ...)`` builds a fresh :class:`HFTokenCounter` and
#: force-loads ``AutoTokenizer.from_pretrained`` every call, so the API's
#: per-collection path (one build per ingest request) would re-read the tokenizer
#: from disk every time. Only SUCCESSES are cached — a load failure may be
#: transient (Hub/NFS hiccup) and must be retried — and only real
#: ``HFTokenCounter`` instances, so a test's fake counter can never leak into a
#: later build. Sharing one tokenizer across threads is safe for the calls the
#: chunkers make (``encode``/``__call__`` without changing truncation or padding,
#: which is the only state the fast tokenizer mutates).
_HF_COUNTERS: dict[str, TokenCounter] = {}
_HF_COUNTERS_LOCK = threading.Lock()


def cached_token_counter(
    backend: str, *, model: str | None, base_url: str | None, api_key: str | None,
) -> TokenCounter:
    """:func:`make_token_counter`, with the ``hf`` backend cached per model."""
    if backend != "hf" or not model:
        return make_token_counter(backend, model=model, base_url=base_url, api_key=api_key)
    hit = _HF_COUNTERS.get(model)
    if hit is not None:
        return hit
    with _HF_COUNTERS_LOCK:
        hit = _HF_COUNTERS.get(model)
        if hit is not None:
            return hit
        counter = make_token_counter(backend, model=model, base_url=base_url, api_key=api_key)
        if isinstance(counter, HFTokenCounter):
            _HF_COUNTERS[model] = counter
        return counter


def clear_token_counter_cache() -> None:
    """Drop every cached HF counter (tests; a tokenizer swapped on disk)."""
    with _HF_COUNTERS_LOCK:
        _HF_COUNTERS.clear()


def resolve_chunk_sizing(
    method: str,
    *,
    model: str | None,
    token_backend: str,
    max_tokens: int | None,
    base_url: str | None,
    api_key: str | None,
    warn: Callable[[str], None],
    require_window: bool = False,
) -> tuple[TokenCounter, int]:
    """THE place a chunk spec's ``(token_counter, max_tokens)`` is decided.

    Every builder — the bulk tools via :func:`build_chunker`, the API's
    per-collection and server-default chunkers — resolves through here, so one
    collection spec cannot be sized two ways (#647).

    * counter: ``hf`` bound to ``model`` for ``fixed_token`` (forced) or when
      ``token_backend == "hf"``; otherwise the configured backend
      (:func:`resolve_token_backend` — which refuses rather than degrades).
    * budget: ``max_tokens`` (the *model window*, an explicit override) minus the
      specials reserve; else auto-detected from ``{base_url}/v1/models``.

    ``require_window``: when nothing yields a window (no override, endpoint absent,
    unreachable or reporting no ``max_model_len``) raise
    :class:`ModelWindowUnavailable` instead of falling back to a fixed 4096. The
    API passes it — a silently assumed window there sizes a whole collection —
    while the bulk tools keep their historical fallback.
    """
    backend = resolve_token_backend(method, token_backend, model, warn)
    token_counter = cached_token_counter(
        backend, model=model, base_url=base_url, api_key=api_key
    )
    if not require_window:
        return token_counter, resolve_max_tokens(
            max_tokens, base_url=base_url, api_key=api_key, default=_FALLBACK_BUDGET
        )
    budget = resolve_max_tokens(
        max_tokens, base_url=base_url, api_key=api_key, default=_UNDETECTED_WINDOW
    )
    if budget == _UNDETECTED_WINDOW:
        where = (
            "the embedding endpoint did not report the model window (max_model_len "
            "from GET /v1/models)" if base_url else "no embedding endpoint is configured"
        )
        raise ModelWindowUnavailable(
            f"cannot size chunks for chunk_method={method!r}: {where} and "
            f"CHUNK_MAX_TOKENS is not set. Refusing to assume a window — a guessed "
            f"budget would silently re-size every chunk. Fix: set CHUNK_MAX_TOKENS "
            f"to the embedding model's context window (e.g. its max_model_len), or "
            f"make the embedding endpoint answer GET /v1/models with max_model_len.",
            transient=bool(base_url),
        )
    return token_counter, budget


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
    size_unit: str = DEFAULT_SIZE_UNIT,
    size_cap: str | None = None,
    cap_split: str = DEFAULT_CAP_SPLIT,
    breakpoint_cap: str | None = None,
    size_tolerance: float | None = None,
    min_piece: int | None = None,
    on_warn: Callable[[str], None] | None = None,
    require_window: bool = False,
) -> tuple[object, TokenCounter, int]:
    """Construct a chunker with its token counter + budget resolved.

    ``max_tokens`` is the ``--chunk-max-tokens`` override (the *model window*);
    ``None`` auto-detects from ``base_url``. ``size_unit`` (``sentence``/``words``)
    is what ``chunk_size`` counts: ``"chars"`` (default) or ``"tokens"``; the
    resolved window is a cap under both. Returns
    ``(chunker, token_counter, resolved_max_tokens)`` — the counter/budget are
    returned so callers can reuse them without re-deriving. The semantic-only
    breakpoint args are passed straight through to :func:`make_chunker`.
    ``require_window`` is :func:`resolve_chunk_sizing`'s refusal switch.
    ``size_cap`` / ``cap_split`` (semantic only) come from the collection's
    recorded ``chunk_params`` via :func:`semantic_cap_from_params` — every caller
    reads them through that one function, so a spec resolves the same way on the
    API and the bulk path. With ``size_cap="tokens"`` the semantic cap is
    ``chunk_size`` tokens, refused (``ValueError``) when it exceeds the resolved
    budget returned here. ``size_tolerance`` / ``min_piece`` (#831) come from
    the same reader, and only when the entry records the tolerance.
    """
    warn = on_warn or (lambda m: print(m, file=sys.stderr))
    token_counter, resolved_max = resolve_chunk_sizing(
        method, model=model, token_backend=token_backend, max_tokens=max_tokens,
        base_url=base_url, api_key=api_key, warn=warn, require_window=require_window,
    )
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
        size_unit=size_unit,
        size_cap=size_cap,
        cap_split=cap_split,
        breakpoint_cap=breakpoint_cap,
        size_tolerance=size_tolerance,
        min_piece=min_piece,
    )
    return chunker, token_counter, resolved_max
