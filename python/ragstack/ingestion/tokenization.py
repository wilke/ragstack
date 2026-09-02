"""Token counting for chunk sizing — keep chunks under the embedder's context.

Chunkers size by characters by default, but embedders are *token*-bounded
(SFR-Embedding-Mistral has a 4096-token window). Char budgets can't guarantee a
chunk fits, so an oversized chunk gets rejected (HTTP 400) at embed time. This
module provides a small :class:`TokenCounter` abstraction plus three backends so
the chunkers can size by tokens and hard-cap every unit to the model window.

Backends, cheapest-import first:

- :class:`EstimatingTokenCounter` — ``ceil(len(text) / chars_per_token)``; zero
  third-party deps, and *inexact*: it mis-sizes chunks by tens of percent.
- :class:`EndpointTokenCounter` — POST ``/tokenize`` to a vLLM endpoint for the
  exact server-side token count (the same tokenizer the embedder uses).
- :class:`HFTokenCounter` — load the embedding model's ``AutoTokenizer`` once and
  count locally; exact and offline once the tokenizer is cached. **The default.**

**The backend is never substituted silently.** :func:`make_token_counter`
builds the backend it was asked for or raises: an ``hf`` request whose tokenizer
will not load is an error, not a quiet demotion to the estimator, because the
same command then builds a differently-sized index without saying so. Choosing
an inexact counter is an explicit act — ``--chunk-token-counter estimate`` on
the ingest CLIs, ``chunk_token_counter`` in settings for the API.

:func:`make_token_counter` is the factory; :func:`resolve_max_tokens` reads the
endpoint's ``max_model_len`` so the budget is auto-detected from the live model.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

# Tokens of headroom left below the model window for BOS/EOS/pooling specials the
# chunker doesn't count (it counts with ``add_special_tokens=False``).
DEFAULT_TOKEN_RESERVE = 16


class TokenCounter(Protocol):
    """Counts tokens in text the way the embedding model would.

    Implementations should be cheap to call repeatedly (the packing chunkers call
    :meth:`count` many times).
    """

    def count(self, text: str) -> int:
        """Number of tokens ``text`` encodes to (no special/BOS/EOS tokens)."""
        ...


class EstimatingTokenCounter:
    """Estimate tokens as ``ceil(len(text) / chars_per_token)``.

    The zero-dependency backend, chosen **explicitly** (``--chunk-token-counter
    estimate`` / ``chunk_token_counter="estimate"``) — nothing falls back to it,
    because a silent demotion from an exact counter re-sizes a whole corpus
    without saying so.

    ``chars_per_token`` stays at **2.5**, deliberately *below* the ratio actually
    observed: measurement on this corpus put dense scientific text at **3.50**
    chars/token (plain English runs ~3.7). Keeping the divisor low makes the
    estimate *over*-count and pack *smaller* chunks; raising it to the measured
    3.50 would trade that under-fill for a real risk of a chunk slipping over the
    embedder window. Under-filling is recoverable, an over-window chunk is a
    rejected embed — so the conservative value stays, and the measured one is
    recorded here so the trade is visible rather than folklore. Prefer
    ``hf``/``endpoint`` for an exact count, and pair this one with the embed-side
    backstop so a residual over-budget chunk can't abort an ingest.
    """

    def __init__(self, chars_per_token: float = 2.5) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be > 0")
        self.chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        return math.ceil(len(text) / self.chars_per_token)


class HFTokenCounter:
    """Count with the embedding model's ``transformers.AutoTokenizer``.

    The tokenizer is loaded lazily on first :meth:`count` and cached on the
    instance (one ``from_pretrained`` per model). Counting uses
    ``encode(text, add_special_tokens=False)`` so the count is the raw content
    length the chunker should pack against — specials are covered by the
    ``reserve`` in :func:`resolve_max_tokens`. Exact and offline once the
    tokenizer is in the HF cache.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self._tok = None  # lazy: AutoTokenizer is a heavy import

    def _tokenizer(self):
        if self._tok is None:
            from transformers import AutoTokenizer  # lazy heavy import

            self._tok = AutoTokenizer.from_pretrained(self.model)
        return self._tok

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._tokenizer().encode(text, add_special_tokens=False))


class EndpointTokenCounter:
    """Count via a vLLM ``/tokenize`` endpoint (exact, server-side tokenizer).

    POSTs ``{"model": ..., "prompt": text}`` and returns the response ``count``.
    A Bearer token is sent when ``api_key`` is set (keyless endpoints ignore it,
    so one key is safe for a mixed pool). Uses a sync :class:`httpx.Client`
    because chunkers run synchronously; a client is created lazily and reused.
    The lazy init is guarded by a lock so concurrent :meth:`count` calls (e.g.
    ``chunking_compare`` chunks in a ``ThreadPoolExecutor``) share one client
    instead of racing to construct several.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._client = client
        self._client_lock = threading.Lock()

    def _http(self) -> httpx.Client:
        # Double-checked locking: the fast path (client already built) stays
        # lock-free, and the no-key/no-call case never pays for the lock at all.
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def count(self, text: str) -> int:
        if not text:
            return 0
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        resp = self._http().post(
            f"{self.base_url}/tokenize",
            json={"model": self.model, "prompt": text},
            headers=headers,
        )
        resp.raise_for_status()
        return int(resp.json()["count"])


def make_token_counter(
    backend: str = "hf",
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    chars_per_token: float = 2.5,
) -> TokenCounter:
    """Build a :class:`TokenCounter` for ``backend`` — or raise.

    - ``"hf"`` (default): :class:`HFTokenCounter` for ``model``, with the
      tokenizer loaded eagerly. If transformers or the tokenizer can't load this
      **raises**; it does not substitute another backend.
    - ``"endpoint"``: :class:`EndpointTokenCounter` (requires ``base_url`` +
      ``model``).
    - ``"estimate"``: :class:`EstimatingTokenCounter`.

    There is no fallback chain. An unavailable HF tokenizer used to demote to the
    endpoint counter and then to the estimator with only a ``log.warning``, which
    meant the *same command* could build an index whose chunks were sized by a
    ~1.4x-off heuristic instead of the real tokenizer — a corpus-wide difference
    that nothing in the output announced. Picking an inexact counter is now
    something a caller has to say out loud: ``--chunk-token-counter estimate``
    (or ``endpoint``) on the ingest CLIs, ``chunk_token_counter`` in settings.

    The chosen backend is logged so an operator can see which one is in use.
    """
    if backend == "estimate":
        _log_backend("estimate", chars_per_token=chars_per_token)
        return EstimatingTokenCounter(chars_per_token=chars_per_token)

    if backend == "endpoint":
        if not base_url or not model:
            raise ValueError("backend='endpoint' requires base_url and model")
        _log_backend("endpoint", base_url=base_url, model=model)
        return EndpointTokenCounter(base_url=base_url, model=model, api_key=api_key)

    if backend == "hf":
        if not model:
            raise ValueError("backend='hf' requires model")
        counter = HFTokenCounter(model=model)
        try:
            # Force the lazy load now so the failure is deterministic and lands
            # here, rather than blowing up mid-ingest on the first chunk.
            counter._tokenizer()
        except Exception as exc:  # noqa: BLE001 - any load failure is fatal
            raise RuntimeError(
                f"token counter backend 'hf' could not load the tokenizer for "
                f"model {model!r} ({type(exc).__name__}: {exc}). Refusing to "
                f"substitute another counter: a different backend sizes chunks "
                f"differently, so the same command would silently build a "
                f"differently-chunked index. Fix the tokenizer (install the "
                f"'chunking' extra, or make the model available to the HF cache), "
                f"or choose another backend explicitly: "
                f"--chunk-token-counter endpoint / --chunk-token-counter estimate "
                f"on the ingest CLIs, or chunk_token_counter=endpoint|estimate "
                f"(CHUNK_TOKEN_COUNTER) in settings."
            ) from exc
        _log_backend("hf", model=model)
        return counter

    raise ValueError(f"unknown token-counter backend {backend!r}; valid: hf, endpoint, estimate")


def _log_backend(name: str, **kw: object) -> None:
    detail = " ".join(f"{k}={v!r}" for k, v in kw.items())
    log.info("token counter: %s", f"{name} {detail}".rstrip())


def resolve_max_tokens(
    explicit: int | None,
    *,
    base_url: str | None,
    api_key: str | None = None,
    reserve: int = DEFAULT_TOKEN_RESERVE,
    default: int = 4096,
) -> int:
    """The per-chunk token budget: explicit override, else auto-detect.

    ``explicit`` (the ``--chunk-max-tokens`` flag) is interpreted as the *model
    window*, not the final budget: the chunker counts content tokens with
    ``add_special_tokens=False``, so the same ``reserve`` headroom for
    BOS/EOS/pooling specials is subtracted here too — ``max(1, explicit -
    reserve)``. Setting the flag to the model's true window therefore no longer
    overflows at embed time. Otherwise GET ``{base_url}/v1/models`` and return
    ``max(1, max_model_len - reserve)`` from the first model entry (clamped so a
    tiny/odd ``max_model_len`` can't yield a zero/negative budget that would
    silently disable capping). On any failure (no endpoint, network error,
    missing/empty data, missing field) return ``default`` unchanged.
    """
    if explicit is not None:
        return max(1, explicit - reserve)
    if not base_url:
        return default
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{base_url.rstrip('/')}/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data") or []
            if not data or "max_model_len" not in data[0]:
                raise ValueError("no max_model_len in /v1/models response")
            max_len = int(data[0]["max_model_len"])
        budget = max(1, max_len - reserve)
        log.info(
            "auto-detected max_model_len=%d, using budget %d (reserve %d)",
            max_len, budget, reserve,
        )
        return budget
    except Exception as exc:  # noqa: BLE001 - any failure → safe default
        log.warning(
            "could not auto-detect max_model_len from %r (%s: %s); using default %d.",
            base_url, type(exc).__name__, exc, default,
        )
        return default
