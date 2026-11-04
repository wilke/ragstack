"""#828 PR 7: ``structure_cuts`` (section-preferred cuts, §7.1) and the walls
it adds to the #831 small-piece merge.

Offline: the word COUNTER, a tiling sentence splitter (or the regex fallback),
synthetic structured documents from ``tests/structure_support``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from ragstack.ingestion import chunkers
from ragstack.ingestion.chunkers import make_chunker
from ragstack.models import Document
from tests.structure_support import paper
from tests.unit.test_chunker_factory_parity import COUNTER, _topic_doc

BASELINE_812 = json.loads(
    (Path(__file__).parent / "fixtures" / "chunk_spans_baseline_812fea0.json").read_text()
)["spans"]


def _tiling_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r".+?(?:\.\s+|$)", text, re.S)]


def _set_segmenter(monkeypatch, name: str) -> None:
    if name == "tiling":
        monkeypatch.setattr(chunkers, "sentence_spans", _tiling_spans)
    else:
        monkeypatch.setattr(chunkers, "_punkt_sentence_spans", lambda text: None)


def _baseline_docs() -> dict[str, Document]:
    content, structure = paper()
    return {"topics": _topic_doc(),
            "paper": Document(id="paper", content=content, metadata={}, structure=structure)}


def _baseline_kwargs(method: str, unit: str) -> dict:
    if method == "fixed_token":
        return {"max_tokens": 512, "token_counter": COUNTER}
    if unit == "chars":
        return {}
    if unit == "chars+cap":
        return {"max_tokens": 80, "token_counter": COUNTER}
    return {"max_tokens": 512, "token_counter": COUNTER, "size_unit": "tokens"}


def _fingerprint(chunks) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(f"{c.id}\x00{c.content}\x01".encode())
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Test 1: absent markers give the pre-PR-7 spans and ids
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", sorted(BASELINE_812))
def test_absent_marker_reproduces_the_812fea0_spans_and_ids(monkeypatch, key):
    seg, doc_name, method, unit, size, ov = key.split("|")
    _set_segmenter(monkeypatch, seg)
    chunker = make_chunker(method, chunk_size=int(size), chunk_overlap=int(ov),
                           **_baseline_kwargs(method, unit))
    chunks = chunker.chunk(_baseline_docs()[doc_name])
    want = BASELINE_812[key]
    assert [[c.start_char, c.end_char] for c in chunks] == want["spans"]
    assert _fingerprint(chunks) == want["ids_content_sha256"]
