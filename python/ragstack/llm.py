"""LLM client + RAG answer generation.

``OpenAILLM`` speaks the OpenAI chat-completions API, so it works against vLLM
(``vllm serve <model>``), OpenAI itself, or any compatible server — mirroring the
embedder's transport. ``RagGenerator`` turns retrieved sources + a question into a
grounded answer. When no LLM endpoint is configured, ``/v1/query`` keeps its
placeholder, so this is opt-in.
"""
from __future__ import annotations

import logging
from typing import Any

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
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = http
        self.api_key = api_key
        # Extra top-level fields merged into every chat-completions request, e.g.
        # a reasoning model's ``{"chat_template_kwargs": {"enable_thinking": false}}``
        # so it answers into ``content`` instead of a separate reasoning field.
        # Sourced from a registered model's ``params`` (per-model, via the registry).
        self.extra_body = extra_body or {}

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
                **self.extra_body,
            },
            headers=headers,
            timeout=120.0,
        )
        r.raise_for_status()
        # Don't trust the response shape: some OpenAI-compatible servers return no
        # choices (content filter) or a null content (finish_reason length /
        # tool_calls). Surface a clear error the caller can degrade on rather than
        # an IndexError / a None answer that fails response validation downstream.
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("LLM response contained no choices")
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise ValueError("LLM returned an empty answer")
        return content

    async def complete_text(
        self, prompt: str, max_tokens: int = 512, temperature: float = 0.0
    ) -> str:
        """Single-prompt completion — wraps the prompt as one user message. Used by
        the query rewriters, which think in plain prompts rather than chat turns."""
        return await self.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


class RagGenerator:
    """Synthesize an answer from a question and its retrieved sources."""

    def __init__(self, llm: OpenAILLM, max_context_chars: int = 8000) -> None:
        self._llm = llm
        self._max_context_chars = max_context_chars

    @property
    def llm(self) -> OpenAILLM:
        """The underlying chat client — used by the models benchmark probe."""
        return self._llm

    @staticmethod
    def _passage_text(s: Source) -> str:
        """The text a source contributes to the prompt: its content alone, or —
        when the request asked for ``context_window > 0`` and neighbours were
        attached — its neighbours and itself concatenated in document order
        (``position`` ascending), each part under a clear delimiter so the
        model can still tell the matched passage from its surroundings. The
        citation number stays on the whole block: it is one source."""
        if not s.context:
            return s.content
        before = [c.content for c in s.context if c.position < 0]
        after = [c.content for c in s.context if c.position > 0]
        parts: list[str] = []
        if before:
            parts.append("(context before)\n" + "\n".join(before))
        parts.append("(passage)\n" + s.content)
        if after:
            parts.append("(context after)\n" + "\n".join(after))
        return "\n".join(parts)

    def _format_context(self, sources: list[Source]) -> str:
        parts: list[str] = []
        used = 0
        for i, s in enumerate(sources, start=1):
            block = f"[{i}] {self._passage_text(s)}"
            sep = 2 if parts else 0  # the "\n\n" join adds 2 chars between blocks
            if parts and used + sep + len(block) > self._max_context_chars:
                break  # at the budget; stop adding passages
            if not parts and len(block) > self._max_context_chars:
                block = block[: self._max_context_chars]  # cap a lone oversized passage
            parts.append(block)
            used += sep + len(block)
        return "\n\n".join(parts)

    async def generate(self, query: str, sources: list[Source]) -> str:
        context = self._format_context(sources) if sources else "(no relevant passages found)"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
        ]
        return await self._llm.complete(messages)
