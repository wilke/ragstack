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

    _BEFORE = "(context before)\n"
    _PASSAGE = "(passage)\n"
    _AFTER = "(context after)\n"
    _ELLIPSIS = "\u2026"

    def _passage_text(self, s: Source, room: int) -> str | None:
        """The text a source contributes to the prompt, within ``room`` chars:
        its content alone, or — when the request asked for ``context_window > 0``
        and neighbours were attached — its neighbours and itself concatenated in
        document order (``position`` ascending), each part under a clear
        delimiter so the model can still tell the matched passage from its
        surroundings. The citation number stays on the whole block: one source.

        Fitting is **passage-first**: the matched passage is never trimmed to
        make room for its context. When the block would overflow ``room``, the
        context gives way — ``(context before)`` is trimmed from its LEFT (the
        text farthest from the passage goes first), ``(context after)`` from
        its RIGHT, the spare split evenly between the two sides with either
        side's unused share passed to the other; a side trimmed to nothing is
        dropped, delimiter included. A trimmed side is marked with an ellipsis
        on the cut edge. Returns ``None`` when even the passage alone would not
        fit — the caller decides between its lone-oversized-passage rule and
        stopping. A context-free source is returned as-is (its own ``room``
        handling is the caller's, unchanged from before context existed)."""
        if not s.context:
            return s.content
        passage = self._PASSAGE + s.content
        spare = room - len(passage)
        if spare < 0:
            return None
        before = "\n".join(c.content for c in s.context if c.position < 0)
        after = "\n".join(c.content for c in s.context if c.position > 0)
        # Full cost of each side, delimiters and the "\n" joining it to the
        # passage included; and the fixed overhead a trimmed side still pays
        # (delimiter + join + ellipsis) — below that the side is dropped.
        want_b = len(self._BEFORE) + len(before) + 1 if before else 0
        want_a = 1 + len(self._AFTER) + len(after) if after else 0
        if want_b + want_a <= spare:
            give_b, give_a = want_b, want_a
        else:
            give_b = min(want_b, spare // 2)
            give_a = min(want_a, spare - give_b)
            give_b = min(want_b, spare - give_a)  # hand `after`'s unused share back
        parts: list[str] = []
        head_b = len(self._BEFORE) + 1 + len(self._ELLIPSIS)
        if give_b == want_b and before:
            parts.append(self._BEFORE + before)
        elif give_b > head_b:
            parts.append(self._BEFORE + self._ELLIPSIS + before[-(give_b - head_b):])
        parts.append(passage)
        head_a = 1 + len(self._AFTER) + len(self._ELLIPSIS)
        if give_a == want_a and after:
            parts.append(self._AFTER + after)
        elif give_a > head_a:
            parts.append(self._AFTER + after[: give_a - head_a] + self._ELLIPSIS)
        return "\n".join(parts)

    def _format_context(self, sources: list[Source]) -> str:
        """The numbered passage blocks, within the prompt's character budget.

        Without context (``context_window`` 0, the default) this is unchanged:
        ``max_context_chars`` total, sources added in rank order until one no
        longer fits, a lone oversized first passage cut to the budget.

        With context the budget scales by the block size a window implies —
        ``(2 * window + 1)`` chunks per source — so turning context on does not
        shrink the NUMBER of sources that reach the prompt. And an early hit's
        context cannot crowd later hits out: each source is fitted (see
        :meth:`_passage_text`, passage-first) into what is left of the budget
        minus a per-source share reserved for every source after it; a source
        whose passage alone would not even fit its share may still use the
        whole remainder, so a long passage is not starved by the reservation.
        """
        n = len(sources)
        window = max(
            (abs(c.position) for s in sources for c in (s.context or ())), default=0
        )
        budget = self._max_context_chars * (2 * window + 1)
        share = budget // n if n else budget
        parts: list[str] = []
        used = 0
        for i, s in enumerate(sources, start=1):
            prefix = f"[{i}] "
            sep = 2 if parts else 0  # the "\n\n" join adds 2 chars between blocks
            left = budget - used - sep - len(prefix)
            # What this source may take: the remainder minus a share for each
            # later source — but never less than its own share of the remainder.
            room = max(left - (n - i) * share, min(left, share))
            text = self._passage_text(s, room)
            if text is None:
                text = self._passage_text(s, left)  # passage-first, whole remainder
            if text is None:
                text = s.content  # context dropped: the lone/overflow rules below
            block = prefix + text
            if parts and used + sep + len(block) > budget:
                break  # at the budget; stop adding passages
            if not parts and len(block) > budget:
                block = block[:budget]  # cap a lone oversized passage
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
