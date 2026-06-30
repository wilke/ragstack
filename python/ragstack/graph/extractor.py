"""LLM-backed knowledge-graph triple extraction (M4 Phase 2).

``LLMKGExtractor`` satisfies the ``KGExtractor`` protocol: it prompts an
OpenAI-compatible LLM to pull ``(subject, predicate, object)`` triples out of
each chunk's text and returns deduplicated :class:`~ragstack.models.Triple`
objects (with ``doc_id`` set; ``tenant_id`` is left empty for the ingestion
pipeline to stamp).

Design goals, matching repo conventions:

* **Opt-in / cheap when off.** The extractor is only constructed when
  ``kg_extraction_enabled`` is set and an LLM is configured (see
  ``deps._build_kg_extractor``). ``max_chunks`` / ``max_triples_per_chunk``
  (0 = unbounded) bound LLM cost.
* **Graceful degradation.** An LLM call or a parse failure on a chunk skips
  that chunk (logged) — it never raises, because an extraction failure must
  not fail an otherwise-successful ingest.
* **Deterministic.** ``temperature=0.0`` and a stable dedup order.
"""
from __future__ import annotations

import json
import logging
import re

from ragstack.models import Chunk, Triple

log = logging.getLogger(__name__)

# Prompt the model for STRICT JSON. We still parse defensively (code fences /
# stray prose are tolerated) — instruction-following is best-effort.
_PROMPT = (
    "Extract knowledge-graph triples from the text below. A triple is a "
    "(subject, predicate, object) fact stated in the text. Use short noun "
    "phrases for subject/object and a concise verb phrase for predicate. Do "
    "not invent facts that are not stated.\n\n"
    "Respond with STRICT JSON and nothing else, in exactly this shape:\n"
    '{{"triples": [{{"subject": "...", "predicate": "...", "object": "..."}}]}}\n\n'
    "If there are no clear facts, respond with {{\"triples\": []}}.\n\n"
    "Text:\n{text}"
)

# Match the first {...} JSON object in the response, tolerating code fences and
# leading/trailing prose. Greedy to the last brace so nested objects survive.
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> str | None:
    """Pull the JSON object out of an LLM response, tolerating code fences and
    surrounding prose. Returns ``None`` when no ``{...}`` span is present."""
    match = _JSON_OBJ_RE.search(text)
    return match.group(0) if match else None


class LLMKGExtractor:
    """Extract triples from chunks via an OpenAI-compatible LLM.

    ``llm`` must expose ``async complete_text(prompt, ...) -> str`` (e.g.
    :class:`ragstack.llm.OpenAILLM`).
    """

    def __init__(
        self,
        llm: object,
        *,
        max_chunks: int = 0,
        max_triples_per_chunk: int = 0,
    ) -> None:
        self._llm = llm
        self._max_chunks = max_chunks
        self._max_triples_per_chunk = max_triples_per_chunk

    async def extract(self, chunks: list[Chunk]) -> list[Triple]:
        """Extract deduplicated triples from ``chunks``.

        Processes at most ``max_chunks`` chunks (0 = all). Per-chunk failures
        (LLM error or unparseable response) are skipped, never raised — the
        graph leg degrades, ingest still succeeds.
        """
        if not chunks:
            return []
        selected = chunks if self._max_chunks <= 0 else chunks[: self._max_chunks]

        triples: list[Triple] = []
        # Dedup on (subject, predicate, object, doc_id); tenant is stamped by the
        # pipeline, so it can't differ within a single extract() call.
        seen: set[tuple[str, str, str, str]] = set()
        for chunk in selected:
            for triple in await self._extract_chunk(chunk):
                key = (triple.subject, triple.predicate, triple.object, triple.doc_id)
                if key in seen:
                    continue
                seen.add(key)
                triples.append(triple)
        return triples

    async def _extract_chunk(self, chunk: Chunk) -> list[Triple]:
        """Extract triples from one chunk. Any LLM/parse error → ``[]``."""
        if not chunk.content.strip():
            return []
        try:
            raw = await self._llm.complete_text(  # type: ignore[attr-defined]
                _PROMPT.format(text=chunk.content)
            )
        except Exception:
            log.warning(
                "kg extraction: LLM call failed for chunk %r; skipping",
                chunk.id,
                exc_info=True,
            )
            return []
        return self._parse(raw, chunk.doc_id, chunk.id)

    def _parse(self, raw: str, doc_id: str, chunk_id: str) -> list[Triple]:
        """Parse an LLM response into triples for ``doc_id``. Tolerant of code
        fences / extra prose; returns ``[]`` on any malformed payload."""
        blob = _extract_json_object(raw or "")
        if blob is None:
            log.debug("kg extraction: no JSON object in response for chunk %r", chunk_id)
            return []
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            log.debug("kg extraction: unparseable JSON for chunk %r", chunk_id)
            return []

        items = data.get("triples") if isinstance(data, dict) else None
        if not isinstance(items, list):
            log.debug("kg extraction: no 'triples' list for chunk %r", chunk_id)
            return []

        out: list[Triple] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            subject = str(item.get("subject", "")).strip()
            predicate = str(item.get("predicate", "")).strip()
            obj = str(item.get("object", "")).strip()
            if not (subject and predicate and obj):
                continue  # skip incomplete triples rather than store empties
            out.append(
                Triple(subject=subject, predicate=predicate, object=obj, doc_id=doc_id)
            )
            if 0 < self._max_triples_per_chunk <= len(out):
                break
        return out
