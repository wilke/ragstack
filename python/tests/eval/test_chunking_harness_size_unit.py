"""The 7-way chunking harness builds sentence/words as a TOKEN budget (#318).

The harness used to spell a token-budget pack as ``chunk_size=10**9,
max_tokens=cfg.size``. That relied on a token budget silently replacing
``chunk_size``, which #318 removed: ``max_tokens`` now only caps a
``chunk_size``-char pack. The spelling is now ``size_unit="tokens",
chunk_size=cfg.size``.

This pins the new spelling in two ways:

- the shipping and stage-1 configs build a ``size_unit="tokens"`` chunker at
  the requested budget, with ``budget_mode`` passing through;
- on a fixture, the new spelling reproduces byte-for-byte what the OLD
  spelling produced on origin/main 0b43ebb, for both fill modes. That is what
  keeps the committed Leg A / Leg B grids (including the ``summed`` arm)
  reproducible.

There is no model, no data and no store. ``SpaceMergeCounter`` is the
deterministic fake from the fill tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import chunking_compare_7way as c7  # noqa: E402

from ragstack.ingestion.chunkers import SentenceChunker, WordChunker  # noqa: E402
from ragstack.models import Document  # noqa: E402
from tests.unit.test_chunkers_fill import SpaceMergeCounter, _prose  # noqa: E402

COUNTER = SpaceMergeCounter()


def _sw_configs() -> list:
    cfgs = [c for c in c7.ALL_CONFIGS if c.kind in ("sentence", "words")]
    assert cfgs, "no sentence/words configs found — the harness changed shape"
    return cfgs


@pytest.mark.parametrize("cfg", _sw_configs(), ids=lambda c: c.key)
def test_config_builds_a_token_budget_chunker(cfg):
    ch = c7.sentence_words_chunker(cfg, COUNTER)
    assert isinstance(ch, SentenceChunker if cfg.kind == "sentence" else WordChunker)
    assert ch.size_unit == "tokens"
    assert ch.chunk_size == cfg.size
    assert ch.chunk_overlap == cfg.char_overlap
    assert ch.max_tokens is None  # the budget IS chunk_size; nothing else caps it
    assert ch.budget_mode == cfg.extra.get("budget_mode", "joined")


@pytest.mark.parametrize("kind", ["sentence", "words"])
def test_summed_extra_reaches_the_packer(kind):
    cfg = c7.ChunkConfig(key="t", kind=kind, size=256, char_overlap=80,
                         extra={"budget_mode": "summed"})
    assert c7.sentence_words_chunker(cfg, COUNTER).budget_mode == "summed"


# Captured from origin/main 0b43ebb with the OLD spelling:
#   make_chunker(kind, chunk_size=10**9, chunk_overlap=40, max_tokens=64,
#                token_counter=SpaceMergeCounter(), budget_mode=mode)
FIXTURE = _prose(60) + " " + "z" * 900 + " " + _prose(10)
LEGACY: dict[tuple[str, str], list[tuple[int, int]]] = {
    ('sentence', 'joined'): [(0, 211), (175, 373), (373, 572), (572, 775), (744, 942), (942, 1141), (1141, 1338), (1307, 1520), (1520, 1709), (1709, 1908), (1908, 2119), (2085, 2283), (2283, 2436), (2399, 2436), (2436, 2688), (2688, 2940), (2940, 3192), (3192, 3422), (3422, 3621), (3621, 3755)],  # noqa: E501
    ('sentence', 'summed'): [(0, 211), (175, 373), (373, 572), (572, 775), (744, 942), (942, 1141), (1141, 1338), (1307, 1520), (1520, 1709), (1709, 1908), (1908, 2119), (2085, 2283), (2283, 2436), (2399, 2436), (2436, 2688), (2688, 2940), (2940, 3192), (3192, 3422), (3422, 3621), (3621, 3755)],  # noqa: E501
    ('words', 'joined'): [(0, 237), (203, 432), (397, 631), (600, 823), (785, 1023), (987, 1209), (1174, 1414), (1384, 1607), (1567, 1799), (1764, 1997), (1963, 2196), (2156, 2386), (2353, 2436), (2399, 2436), (2403, 2436), (2409, 2436), (2415, 2436), (2419, 2436), (2426, 2436), (2436, 2688), (2688, 2940), (2940, 3192), (3192, 3337), (3337, 3574), (3540, 3755)],  # noqa: E501
    ('words', 'summed'): [(0, 153), (116, 265), (225, 373), (339, 495), (461, 609), (572, 729), (690, 832), (795, 952), (918, 1070), (1030, 1174), (1141, 1296), (1261, 1412), (1373, 1524), (1489, 1635), (1597, 1749), (1709, 1861), (1827, 1972), (1933, 2085), (2052, 2205), (2167, 2322), (2283, 2426), (2386, 2436), (2399, 2436), (2403, 2436), (2409, 2436), (2415, 2436), (2419, 2436), (2426, 2436), (2436, 2688), (2688, 2940), (2940, 3192), (3192, 3337), (3337, 3490), (3453, 3602), (3562, 3710), (3676, 3755)],  # noqa: E501
}


@pytest.mark.parametrize("key", sorted(LEGACY))
def test_new_spelling_reproduces_the_old_harness_output(key):
    kind, mode = key
    extra = {} if mode == "joined" else {"budget_mode": mode}
    cfg = c7.ChunkConfig(key="t", kind=kind, size=64, char_overlap=40, extra=extra)
    chunks = c7.sentence_words_chunker(cfg, COUNTER).chunk(
        Document(id="h7", content=FIXTURE, source="test")
    )
    assert [(c.start_char, c.end_char) for c in chunks] == LEGACY[key]
