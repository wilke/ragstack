"""Shared plumbing for the Leg B / Leg C pilots and the §7a section-level oracle.

Design rules carried over from the stage-1 harness (../stage1/stage1_common.py):

* **Zero store writes, and no store client at all.** Nothing in this module or its
  callers constructs a Qdrant or Elasticsearch client; ``:24041`` / ``:24043`` are
  never contacted and production ``:6333`` / ``:9200`` are not even reachable from
  this code path.
* ``pin_repo()`` — ``/rag/envs/ragstack`` carries an editable-install META-PATH
  FINDER pointing at ``/rag/repos/ragstack`` (a different commit). A meta-path
  finder runs BEFORE ``sys.path``, so ``PYTHONPATH`` does not win. Strip it.
* The only GPU service used here is the **crossencoder sidecar on :50052**
  (``BAAI/bge-reranker-v2-m3``, ``MAX_LENGTH=4096``, ``CUDA_VISIBLE_DEVICES=0``).
  No embedding endpoint is called, and no endpoint is started. GPUs 6 and 7 are
  never touched.

**Client-side windowing, not sidecar truncation.** The sidecar silently truncates
at 4,096 of its own tokens. For a position-of-evidence oracle that is a
*systematic* bias — it would push the argmax toward sections whose evidence sits
early — so long units are windowed here and max-pooled over windows.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

REPO_PY = "/home/wilke/Development/ragstack/python"
HERE = pathlib.Path(__file__).resolve().parent
PHASE0 = HERE.parent
STEP2_XML = PHASE0 / "step2" / "xml"

RERANKER_URL = "http://localhost:50052"
SFR_MODEL = "Salesforce/SFR-Embedding-Mistral"

OA_XML = pathlib.Path("/rag/oa/corpus/xml")
OA_MANIFEST = pathlib.Path("/rag/oa/corpus/manifest.jsonl")

# Window so the sidecar never truncates. bge-reranker-v2-m3 tokenises to roughly
# 3.5 chars/token on biomedical prose, so 4,096 tokens is ~14k chars; 10k-char
# windows with a 2k-char stride overlap leave a wide safety margin for a query
# prefix and for token-dense passages.
WINDOW_CHARS = 10_000
WINDOW_OVERLAP_CHARS = 2_000

CE_CONCURRENCY = 4      # politeness: :50052 is a shared prod sidecar on GPU 0
CE_BATCH = 24           # documents per /rerank call


def pin_repo() -> None:
    """Force ``ragstack`` to resolve to the working copy at d225cea, and prove it."""
    sys.meta_path[:] = [f for f in sys.meta_path if "editable" not in type(f).__module__]
    for p in (REPO_PY, os.path.join(REPO_PY, "scripts", "eval")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import ragstack

    if not ragstack.__file__.startswith(REPO_PY):
        raise SystemExit(
            f"ragstack resolved to {ragstack.__file__!r}, not the working copy under "
            f"{REPO_PY!r} — the editable-install finder for /rag/repos won."
        )


# --------------------------------------------------------------------------- #
# Structural units (§7a: "structural sections", never a chunker's cuts)
# --------------------------------------------------------------------------- #
_CLASS_RULES = [
    ("abstract", re.compile(r"^\s*(abstract|summary)\b", re.I)),
    ("intro", re.compile(r"^\s*(introduction|background|rationale)\b", re.I)),
    ("methods", re.compile(
        r"^\s*((materials?\s*(and|&)\s*)?methods?|patients?\s*(and|&)\s*methods?|"
        r"methodology|experimental(\s+(section|procedures?|design))?|study\s+design|"
        r"subjects?\s*(and|&)\s*methods?|data\s+(and|&)\s*methods?)\b", re.I)),
    ("results", re.compile(r"^\s*(results?|findings)\b", re.I)),
    ("discussion", re.compile(r"^\s*(discussion|conclusions?|concluding|"
                              r"limitations|implications|future\s+(work|directions))\b", re.I)),
]


def classify_section(title: str) -> str:
    """One of abstract / intro / methods / results / discussion / other."""
    t = (title or "").strip().lstrip("#").strip()
    # Strip a leading numeric label ("2. Materials and Methods", "IV Results").
    t = re.sub(r"^\s*(\d+(\.\d+)*|[ivxIVX]+)[.)\s]+", "", t)
    for name, rx in _CLASS_RULES:
        if rx.match(t):
            return name
    if re.search(r"\bresults?\s*(and|&)\s*discussion\b", t, re.I):
        return "results"
    return "other"


def units_for_article(root, section_text, article_prose, front_meta,
                      min_unit_chars: int = 400):
    """Split one JATS article into structural units over the *indexed* text.

    Returns ``(doc_text, units)`` where ``doc_text`` is exactly the text the
    stage-1 grid indexed — ``title \\n\\n abstract \\n\\n body`` — and each unit is

        {"i", "title", "cls", "start_char", "end_char", "text"}

    with ``start_char``/``end_char`` being offsets **into ``doc_text``**, so a
    unit's position can be converted to a token offset with the same tokenizer
    the grid used.

    Units are the abstract (with the title prepended, since the title is part of
    the indexed lead) and each **top-level** ``<body>`` child. Sub-sections are
    never split out, which is the §7a "~1k-token floor" taken from the safe side:
    we never descend below a top-level section, so no unit is a fragment. Runs of
    very short top-level children (``< min_unit_chars``) are merged forward into
    the next unit so a two-line "Competing interests" stub cannot win an argmax.
    """
    abstract, _body_text = article_prose(root)
    title = front_meta(root).get("title", "")

    lead = "\n\n".join(x for x in (title, abstract) if x)
    pieces: list[tuple[str, str]] = []          # (title, text)
    if lead:
        pieces.append(("Abstract", lead))

    body = root.find(".//body")
    raw: list[tuple[str, str]] = []
    if body is not None:
        from ragstack.ingestion.jats import LIFT, clean_itext

        for child in body:
            if child.tag in LIFT:
                continue
            if child.tag == "sec":
                txt = section_text(child)
                t = child.find("title")
                st = (t.text or "").strip() if t is not None and t.text else ""
                if not st:
                    from ragstack.ingestion.jats import itext

                    st = itext(t) if t is not None else ""
            else:
                txt = clean_itext(child)
                st = ""
            if txt and txt.strip():
                raw.append((st, txt))

    # Merge short units forward (keep the *later*, larger unit's title/class).
    merged: list[tuple[str, str]] = []
    carry = ""
    for st, txt in raw:
        cand = (carry + "\n\n" + txt) if carry else txt
        if len(cand) < min_unit_chars:
            carry = cand
            continue
        merged.append((st, cand))
        carry = ""
    if carry:
        if merged:
            merged[-1] = (merged[-1][0], merged[-1][1] + "\n\n" + carry)
        else:
            merged.append(("", carry))
    pieces.extend(merged)

    # Rebuild doc_text from the pieces so offsets are exact by construction.
    # This reproduces jats' own "\n\n".join over body children plus the lead,
    # which is byte-identical to stage 1's doc_text when no unit was dropped.
    units = []
    cursor = 0
    parts = []
    for i, (st, txt) in enumerate(pieces):
        if i:
            cursor += 2          # the "\n\n" separator
            parts.append("\n\n")
        units.append({
            "i": i,
            "title": st if i else "Abstract",
            "cls": "abstract" if i == 0 else classify_section(st),
            "start_char": cursor,
            "end_char": cursor + len(txt),
            "text": txt,
        })
        parts.append(txt)
        cursor += len(txt)
    return "".join(parts), units


def load_article(path):
    return ET.parse(path).getroot()


# --------------------------------------------------------------------------- #
# Crossencoder client (§7a oracle)
# --------------------------------------------------------------------------- #
def windows(text: str, size: int = WINDOW_CHARS, overlap: int = WINDOW_OVERLAP_CHARS):
    """Character windows over ``text`` so the sidecar never truncates silently."""
    if len(text) <= size:
        return [text]
    step = size - overlap
    return [text[s:s + size] for s in range(0, max(1, len(text) - overlap), step)]


class CE:
    """Crossencoder client with a bounded number of in-flight requests.

    ``:50052`` is a shared production sidecar pinned to GPU 0; the in-flight cap
    is the same politeness policy stage 1 used against the embedding fleet.
    """

    def __init__(self, concurrency: int = CE_CONCURRENCY):
        import httpx

        self.client = httpx.Client(timeout=600)
        self.pool = ThreadPoolExecutor(concurrency)
        self.slots: queue.Queue = queue.Queue()
        for _ in range(concurrency):
            self.slots.put(1)
        self.lock = threading.Lock()
        self.pairs = 0
        self.requests = 0
        self.retries = 0
        self.seconds = 0.0

    def _post(self, query: str, docs: list[str]) -> list[float]:
        self.slots.get()
        try:
            for attempt in range(4):
                try:
                    t0 = time.time()
                    # top_k must be len(docs): the sidecar truncates BOTH `scores`
                    # and `indices` to top_k, and the oracle needs every unit's score.
                    r = self.client.post(
                        RERANKER_URL + "/rerank",
                        json={"query": query, "documents": docs, "top_k": len(docs)})
                    r.raise_for_status()
                    body = r.json()
                    if len(body["scores"]) != len(docs):
                        raise RuntimeError(f"{len(body['scores'])} scores for {len(docs)} docs")
                    # scores come back sorted by rank; restore input order
                    scores = [0.0] * len(docs)
                    for idx, s in zip(body["indices"], body["scores"]):
                        scores[idx] = s
                    with self.lock:
                        self.requests += 1
                        self.pairs += len(docs)
                        self.seconds += time.time() - t0
                    return scores
                except Exception as e:  # noqa: BLE001
                    with self.lock:
                        self.retries += 1
                    if attempt == 3:
                        raise
                    print(f"  CE retry: {type(e).__name__} {e}", flush=True)
                    time.sleep(2 * (attempt + 1))
            raise AssertionError("unreachable")
        finally:
            self.slots.put(1)

    def score(self, query: str, docs: list[str]) -> list[float]:
        """Score every ``doc`` against ``query``, batching under ``CE_BATCH``."""
        out: list[float] = []
        for s in range(0, len(docs), CE_BATCH):
            out.extend(self._post(query, docs[s:s + CE_BATCH]))
        return out

    def score_units(self, query: str, unit_texts: list[str]) -> list[float]:
        """Max-pool over client-side windows so no unit is silently truncated."""
        flat, owner = [], []
        for i, t in enumerate(unit_texts):
            for w in windows(t):
                flat.append(w)
                owner.append(i)
        scores = self.score(query, flat)
        best = [float("-inf")] * len(unit_texts)
        for o, s in zip(owner, scores):
            if s > best[o]:
                best[o] = s
        return best

    def map(self, fn, items):
        return list(self.pool.map(fn, items))

    def stats(self) -> dict:
        return {"pairs": self.pairs, "requests": self.requests,
                "retries": self.retries, "ce_seconds": round(self.seconds, 1)}


def atomic_json(path: pathlib.Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)
