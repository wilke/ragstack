"""LLM client + RAG answer generation.

``OpenAILLM`` speaks the OpenAI chat-completions API, so it works against vLLM
(``vllm serve <model>``), OpenAI itself, or any compatible server — mirroring the
embedder's transport. ``RagGenerator`` turns retrieved sources + a question into a
grounded answer. When no LLM endpoint is configured, ``/v1/query`` keeps its
placeholder, so this is opt-in.
"""
from __future__ import annotations

import logging

import httpx

from ragstack.models import Source

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the question using ONLY the provided "
    "context passages. If the context does not contain the answer, say you don't "
    "know rather than inventing one. Cite the passages you used as [n]."
)


class OpenAILLM:
    """OpenAI-compatible chat-completions client (``POST <base>/v1/chat/completions``)."""

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

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = await self.http.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            headers=headers,
            timeout=120.0,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class RagGenerator:
    """Synthesize an answer from a question and its retrieved sources."""

    def __init__(self, llm: OpenAILLM, max_context_chars: int = 8000) -> None:
        self._llm = llm
        self._max_context_chars = max_context_chars

    def _format_context(self, sources: list[Source]) -> str:
        parts: list[str] = []
        used = 0
        for i, s in enumerate(sources, start=1):
            block = f"[{i}] {s.content}"
            if parts and used + len(block) > self._max_context_chars:
                break  # keep at least one passage; otherwise stop at the budget
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    async def generate(self, query: str, sources: list[Source]) -> str:
        context = self._format_context(sources) if sources else "(no relevant passages found)"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
        ]
        return await self._llm.complete(messages)
