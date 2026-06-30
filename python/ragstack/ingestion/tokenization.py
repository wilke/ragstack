"""Token counting for chunk sizing — keep chunks under the embedder's context.

Chunkers size by characters by default, but embedders are *token*-bounded
(SFR-Embedding-Mistral has a 4096-token window). Char budgets can't guarantee a
chunk fits, so an oversized chunk gets rejected (HTTP 400) at embed time. This
module provides a small :class:`TokenCounter` abstraction plus three backends so
the chunkers can size by tokens and hard-cap every unit to the model window.

Backends, cheapest-import first:

- :class:`EstimatingTokenCounter` — ``ceil(len(text) / chars_per_token)``; zero
  third-party deps, a rough but safe-ish fallback.
- :class:`EndpointTokenCounter` — POST ``/tokenize`` to a vLLM endpoint for the
  exact server-side token count (the same tokenizer the embedder uses).
- :class:`HFTokenCounter` — load the embedding model's ``AutoTokenizer`` once and
  count locally; exact and offline once the tokenizer is cached. **The default.**

:func:`make_token_counter` is the factory; :func:`resolve_max_tokens` reads the
endpoint's ``max_model_len`` so the budget is auto-detected from the live model.
"""
from __future__ import annotations

import math
import sys
from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class TokenCounter(Protocol):
    """Counts tokens in text the way the embedding model would.

    Implementations should be cheap to call repeatedly (the packing chunkers call
    :meth:`count` many times). :meth:`count_batch` defaults to mapping
    :meth:`count`; a backend with a real batch API may override it.
    """

    def count(self, text: str) -> int:
        """Number of tokens ``text`` encodes to (no special/BOS/EOS tokens)."""
        ...

    def count_batch(self, texts: list[str]) -> list[int]:
        """Token counts for several texts; default maps :meth:`count`."""
        ...


class _BaseTokenCounter:
    """Mixin providing the default ``count_batch`` (map ``count``)."""

    def count(self, text: str) -> int:  # pragma: no cover - overridden
        raise NotImplementedError

    def count_batch(self, texts: list[str]) -> list[int]:
        return [self.count(t) for t in texts]


class EstimatingTokenCounter(_BaseTokenCounter):
    """Estimate tokens as ``ceil(len(text) / chars_per_token)``.

    Zero-dependency fallback used when neither a local tokenizer nor an endpoint
    is available. ``chars_per_token`` defaults to 3.7 — a middle-of-the-road
    English ratio; dense scientific text runs lower (~2.5), so this *under*-counts
    there and can let a chunk slip slightly over budget. Prefer the exact backends
    when you can; this only guarantees order-of-magnitude sizing.
    """

    def __init__(self, chars_per_token: float = 3.7) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be > 0")
        self.chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        return math.ceil(len(text) / self.chars_per_token)


class HFTokenCounter(_BaseTokenCounter):
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


class EndpointTokenCounter(_BaseTokenCounter):
    """Count via a vLLM ``/tokenize`` endpoint (exact, server-side tokenizer).

    POSTs ``{"model": ..., "prompt": text}`` and returns the response ``count``.
    A Bearer token is sent when ``api_key`` is set (keyless endpoints ignore it,
    so one key is safe for a mixed pool). Uses a sync :class:`httpx.Client`
    because chunkers run synchronously; a client is created lazily and reused.
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

    def _http(self) -> httpx.Client:
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
    chars_per_token: float = 3.7,
) -> TokenCounter:
    """Build a :class:`TokenCounter` for ``backend``.

    - ``"hf"`` (default): :class:`HFTokenCounter` for ``model``. If transformers
      or the tokenizer can't load, fall back to :class:`EndpointTokenCounter`
      (when ``base_url`` is given) and finally :class:`EstimatingTokenCounter`.
    - ``"endpoint"``: :class:`EndpointTokenCounter` (requires ``base_url`` +
      ``model``).
    - ``"estimate"``: :class:`EstimatingTokenCounter`.

    The chosen backend is logged to stderr so an operator can see when a fallback
    fired (e.g. transformers missing → endpoint, or no endpoint → estimator).
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
            # Force the lazy load now so we can fall back deterministically rather
            # than blowing up mid-ingest on the first chunk.
            counter._tokenizer()
        except Exception as exc:  # noqa: BLE001 - any load failure → fall back
            print(
                f"[tokenization] HF tokenizer for {model!r} unavailable "
                f"({type(exc).__name__}: {exc}); falling back.",
                file=sys.stderr,
            )
            if base_url:
                _log_backend("endpoint", base_url=base_url, model=model)
                return EndpointTokenCounter(base_url=base_url, model=model, api_key=api_key)
            _log_backend("estimate", chars_per_token=chars_per_token)
            return EstimatingTokenCounter(chars_per_token=chars_per_token)
        _log_backend("hf", model=model)
        return counter

    raise ValueError(f"unknown token-counter backend {backend!r}; valid: hf, endpoint, estimate")


def _log_backend(name: str, **kw: object) -> None:
    detail = " ".join(f"{k}={v!r}" for k, v in kw.items())
    print(f"[tokenization] token counter: {name} {detail}".rstrip(), file=sys.stderr)


def resolve_max_tokens(
    explicit: int | None,
    *,
    base_url: str | None,
    api_key: str | None = None,
    reserve: int = 16,
    default: int = 4096,
) -> int:
    """The per-chunk token budget: explicit override, else auto-detect.

    When ``explicit`` is given it's returned as-is. Otherwise GET
    ``{base_url}/v1/models`` and return ``max_model_len - reserve`` from the first
    model entry; ``reserve`` leaves headroom for BOS/EOS/pooling specials the
    chunker doesn't count. On any failure (no endpoint, network error, missing
    field) return ``default`` unchanged.
    """
    if explicit is not None:
        return explicit
    if not base_url:
        return default
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{base_url.rstrip('/')}/v1/models", headers=headers)
            resp.raise_for_status()
            max_len = int(resp.json()["data"][0]["max_model_len"])
        budget = max_len - reserve
        print(
            f"[tokenization] auto-detected max_model_len={max_len}, "
            f"using budget {budget} (reserve {reserve})",
            file=sys.stderr,
        )
        return budget
    except Exception as exc:  # noqa: BLE001 - any failure → safe default
        print(
            f"[tokenization] could not auto-detect max_model_len from "
            f"{base_url!r} ({type(exc).__name__}: {exc}); using default {default}.",
            file=sys.stderr,
        )
        return default
