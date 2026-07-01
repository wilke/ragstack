"""Persistent segmentation cache — reproducible blocks, computed once.

The semantic chunkers place block boundaries from *embedding* distances, and an
embedding backend (vLLM) is not bit-deterministic run-to-run: a low-bit float
change can nudge a distance across the breakpoint threshold and shift a boundary,
so re-ingesting the same document can produce slightly different chunks (observed:
~2/1944 on SFR). That breaks idempotent re-ingest and "reproducible blocks".

This cache stores the resulting chunk **spans** — the ``(start_char, end_char)``
pairs — per document, keyed by a hash of the content plus a fingerprint of the
segmentation config. On a hit the blocks are rebuilt deterministically from the
cached spans (identical ``uuid5(doc_id:start:end)`` ids), so:

- **Blocks are reproducible by construction**, independent of embedding jitter.
- The expensive breakpoint embed is **skipped entirely** on any re-run of already
  segmented content.

Only the spans are cached (small); the chunk text/metadata are re-sliced from the
document, so the cache stays compact and never holds corpus text.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from ragstack.ingestion.chunkers import _make_chunk
from ragstack.models import Chunk, Document


def config_fingerprint(**parts: object) -> str:
    """A stable string identifying the segmentation config. Any change (chunk
    method, buffer/percentile/min-length, token budgets, breakpoint model) yields a
    different fingerprint → a different cache key → a clean recompute (never a stale
    span reused under new settings)."""
    return "|".join(f"{k}={parts[k]}" for k in sorted(parts))


class SegmentationCache:
    """Content-addressed store of per-document chunk spans (JSONL, append-only).

    Loaded fully into memory at construction (one dict entry per doc: a hex key and
    a small int-pair list). Appends are flushed on each miss so a crash keeps the
    segmentations computed so far. Single-writer: the ingest producer is the only
    caller, so no locking is needed.
    """

    def __init__(self, path: Path, fingerprint: str) -> None:
        self._path = Path(path)
        self._fp = fingerprint
        self._spans: dict[str, list[tuple[int, int]]] = {}
        self.hits = 0
        self.misses = 0
        self._load()
        # Append handle opened after load so a fresh file starts empty.
        self._fh = open(self._path, "a", encoding="utf-8")

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        self._spans[rec["k"]] = [(int(s), int(e)) for s, e in rec["s"]]
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue  # skip a corrupt line, like the checkpoint reader
        except FileNotFoundError:
            pass

    def _key(self, content: str) -> str:
        h = hashlib.sha1()
        h.update(self._fp.encode("utf-8"))
        h.update(b"\x00")
        h.update(content.encode("utf-8", "replace"))
        return h.hexdigest()

    def get_or_compute(
        self, doc: Document, chunk_fn: Callable[[Document], list[Chunk]]
    ) -> list[Chunk]:
        """Return the doc's chunks from cache, or compute + record them.

        On a hit the chunks are rebuilt from the cached spans (deterministic ids),
        so the blocks are identical to the first segmentation regardless of any
        embedding-backend jitter since. On a miss ``chunk_fn`` runs and its spans
        are persisted."""
        key = self._key(doc.content)
        spans = self._spans.get(key)
        if spans is not None:
            self.hits += 1
            return [_make_chunk(doc, s, e) for s, e in spans]
        self.misses += 1
        chunks = chunk_fn(doc)
        spans = [(c.start_char, c.end_char) for c in chunks]
        self._spans[key] = spans
        self._fh.write(json.dumps({"k": key, "s": spans}) + "\n")
        self._fh.flush()
        return chunks

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
