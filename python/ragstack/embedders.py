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

import httpx


class SidecarEmbedder:
    """RAGStack embedding sidecar client (``POST <base>/embed``)."""

    def __init__(self, base_url: str, http: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http

    async def embed(self, texts: list[str]) -> list[list[float]]:
        r = await self.http.post(
            f"{self.base_url}/embed",
            json={"texts": texts},
            timeout=120.0,
        )
        r.raise_for_status()
        return r.json()["embeddings"]


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = http
        self.api_key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = await self.http.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": texts},
            headers=headers,
            timeout=120.0,
        )
        r.raise_for_status()
        # OpenAI returns data sorted by request index, so order is preserved.
        return [item["embedding"] for item in r.json()["data"]]


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
