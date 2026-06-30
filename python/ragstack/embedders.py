"""HTTP-backed embedder clients.

Two flavors share the same async interface (``embed(texts) -> vectors``):

- ``SidecarEmbedder`` talks to the RAGStack embedding sidecar
  (``POST /embed`` with ``{"texts": [...]}``).
- ``OpenAIEmbedder`` talks to anything that implements the OpenAI
  embeddings API (``POST /v1/embeddings`` with ``{"model": ..., "input": [...]}``)
  — vLLM ``--runner pooling``, OpenAI itself, Together, etc.

``make_embedder()`` is the typical entry point.
"""
from __future__ import annotations

import logging

import httpx

from ragstack.sidecar_http import DEFAULT_TIMEOUT, SidecarClient

log = logging.getLogger(__name__)


class SidecarEmbedder:
    """RAGStack embedding sidecar client (``POST <base>/embed``)."""

    def __init__(
        self,
        base_url: str,
        http: httpx.AsyncClient,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._client = SidecarClient(base_url, http, timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client.http

    async def embed(self, texts: list[str]) -> list[list[float]]:
        body = await self._client.post_json("embed", {"texts": texts})
        return body["embeddings"]


class OpenAIEmbedder:
    """OpenAI-compatible embeddings client (``POST <base>/v1/embeddings``).

    Works against any server that speaks the OpenAI embeddings API, including
    vLLM's pooling runner (``vllm serve <model> --runner pooling``).
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        http: httpx.AsyncClient,
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._client = SidecarClient(base_url, http, timeout=timeout)
        self.model = model
        self.api_key = api_key

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client.http

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = await self._client.post_json(
            "v1/embeddings",
            {"model": self.model, "input": texts},
            headers=headers,
        )
        # Sort by the returned index: not every OpenAI-compatible server (e.g.
        # some vLLM builds) preserves input order, and a silent reordering would
        # bind embeddings to the wrong chunks.
        data = sorted(body["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]


class BatchingEmbedder:
    """Wrap an embedder with bounded batching and poison-input isolation.

    A bare ``embed`` sends every text in one request, which overflows the
    backend's max-batch / context window on large documents and fails the whole
    call on a single bad input. This splits work into batches bounded by item
    count and an estimated-token budget. ``embed_isolated`` additionally bisects
    a failing batch to quarantine the offending input rather than losing the
    document; infrastructure failures (5xx / network) are re-raised, never
    silently quarantined.

    Token estimation is deliberately crude (``len(text) // chars_per_token``) —
    it only needs to bound request size, not be exact.
    """

    def __init__(
        self,
        base,
        *,
        max_batch_items: int = 64,
        max_batch_tokens: int = 8192,
        chars_per_token: int = 4,
    ) -> None:
        self._base = base
        self._max_items = max(1, max_batch_items)
        self._max_tokens = max(1, max_batch_tokens)
        self._chars_per_token = max(1, chars_per_token)

    def _est_tokens(self, text: str) -> int:
        return len(text) // self._chars_per_token + 1

    def _batches(self, texts: list[str]) -> list[list[int]]:
        """Group input indices into item- and token-bounded batches."""
        groups: list[list[int]] = []
        current: list[int] = []
        tokens = 0
        for i, text in enumerate(texts):
            t = self._est_tokens(text)
            if current and (len(current) >= self._max_items or tokens + t > self._max_tokens):
                groups.append(current)
                current, tokens = [], 0
            current.append(i)
            tokens += t
        if current:
            groups.append(current)
        return groups

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Bounded batching, order-preserving, all-or-nothing (raises on failure)."""
        out: list[list[float]] = []
        for group in self._batches(texts):
            out.extend(await self._base.embed([texts[i] for i in group]))
        return out

    async def embed_isolated(
        self, texts: list[str]
    ) -> tuple[list[list[float] | None], int]:
        """Like ``embed`` but isolates poison inputs.

        Returns ``(vectors, quarantined)`` where ``vectors`` is aligned to
        ``texts`` with ``None`` for quarantined entries. Caller drops the
        ``None`` slots. Infrastructure errors propagate.
        """
        out: list[list[float] | None] = [None] * len(texts)
        quarantined = 0
        for group in self._batches(texts):
            quarantined += await self._embed_group(texts, group, out)
        return out, quarantined

    async def _embed_group(
        self, texts: list[str], indices: list[int], out: list[list[float] | None]
    ) -> int:
        try:
            vecs = await self._base.embed([texts[i] for i in indices])
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            # 4xx = the input is the problem (too long / malformed): bisect to
            # find and quarantine it. Anything else (5xx, network) is the backend
            # failing — re-raise so we don't silently drop the whole corpus.
            if status is None or not 400 <= status < 500:
                raise
            if len(indices) == 1:
                log.warning(
                    "quarantining unembeddable input #%d (HTTP %d)", indices[0], status
                )
                return 1
            mid = len(indices) // 2
            return await self._embed_group(texts, indices[:mid], out) + await self._embed_group(
                texts, indices[mid:], out
            )
        for i, v in zip(indices, vecs, strict=True):
            out[i] = v
        return 0


def make_embedder(
    api: str,
    http: httpx.AsyncClient,
    base_url: str,
    model: str | None = None,
    api_key: str | None = None,
):
    """Construct an embedder by name. Returns either a SidecarEmbedder
    or an OpenAIEmbedder. Raises ValueError on bad inputs."""
    if api == "sidecar":
        return SidecarEmbedder(base_url, http=http)
    if api == "openai":
        if not model:
            raise ValueError(
                "embedding api 'openai' requires --embedding-model "
                "(e.g. Salesforce/SFR-Embedding-Mistral for vLLM)"
            )
        return OpenAIEmbedder(base_url, model, http=http, api_key=api_key)
    raise ValueError(f"unknown embedding api: {api!r} (use 'sidecar' or 'openai')")
