"""RagGenerator's prompt builder with ``context_window`` context attached
(issue #322 review): the budget scales with the window so context never
shrinks the number of sources reaching the prompt, and fitting is
passage-first — the matched passage stays whole while ``(context before)`` is
trimmed from the left and ``(context after)`` from the right.
"""
from __future__ import annotations

import pytest

from ragstack.llm import RagGenerator
from ragstack.models import ContextChunk, Source

CHUNK = 2000


def _text(tag: str, n: int = CHUNK) -> str:
    """``n`` chars, tagged at both ends so a cut edge is recognisable."""
    body = f"<{tag}-start>"
    return body + tag[0] * (n - len(body) - len(f"<{tag}-end>")) + f"<{tag}-end>"


def _source(i: int, window: int, chunk: int = CHUNK) -> Source:
    ctx = [
        ContextChunk(chunk_id=f"s{i}p{h}", position=-h, content=_text(f"s{i}b{h}", chunk))
        for h in range(window, 0, -1)
    ] + [
        ContextChunk(chunk_id=f"s{i}n{h}", position=h, content=_text(f"s{i}a{h}", chunk))
        for h in range(1, window + 1)
    ]
    return Source(
        doc_id="d", chunk_id=f"s{i}", content=_text(f"s{i}passage", chunk), score=1.0 / i,
        context=ctx or None,
    )


class _LLM:
    def __init__(self) -> None:
        self.prompt = ""

    async def complete(self, messages, max_tokens=512, temperature=0.0):
        self.prompt = messages[-1]["content"]
        return "ok"


def _blocks(prompt: str) -> list[str]:
    body = prompt[len("Context:\n"):prompt.rindex("\n\nQuestion:")]
    return body.split("\n\n")


@pytest.mark.asyncio
async def test_window_3_default_budget_keeps_every_passage_whole():
    # 3 sources x (3 + 1 + 3) chunks of 2,000 chars: 42k of text against the
    # old flat 8,000 budget would have kept a truncated [1] only. Scaled by
    # (2*3+1) the budget is 56,000: everything fits, nothing is cut.
    llm = _LLM()
    gen = RagGenerator(llm)  # type: ignore[arg-type]
    sources = [_source(i, 3) for i in (1, 2, 3)]
    await gen.generate("q", sources)
    blocks = _blocks(llm.prompt)
    assert [b[:4] for b in blocks] == ["[1] ", "[2] ", "[3] "]
    for i, b in zip((1, 2, 3), blocks, strict=True):
        assert f"(passage)\n{_text(f's{i}passage')}" in b
        assert b.count("(context before)") == 1 and b.count("(context after)") == 1
        assert _text(f"s{i}b3") in b and _text(f"s{i}a3") in b  # farthest hops intact
        assert "…" not in b


@pytest.mark.asyncio
async def test_tight_budget_trims_context_passage_first_and_keeps_all_sources():
    llm = _LLM()
    gen = RagGenerator(llm, max_context_chars=2500)  # type: ignore[arg-type]
    sources = [_source(i, 3) for i in (1, 2, 3)]
    await gen.generate("q", sources)
    blocks = _blocks(llm.prompt)
    budget = 2500 * 7
    assert len(blocks) == 3  # every source reaches the prompt
    assert len("\n\n".join(blocks)) <= budget
    for i, b in enumerate(blocks, start=1):
        passage = _text(f"s{i}passage")
        assert f"(passage)\n{passage}\n" in b  # whole, never trimmed
        before, rest = b.split("\n(passage)\n")
        _, after = rest.split("\n(context after)\n")
        # before: trimmed from the LEFT — the text nearest the passage survives,
        # the farthest hop's head is gone, and the cut edge is marked.
        assert before.startswith(f"[{i}] (context before)\n…")
        assert before.endswith(f"<s{i}b1-end>")
        assert f"<s{i}b3-start>" not in before
        # after: trimmed from the RIGHT — the head nearest the passage survives.
        assert after.startswith(f"<s{i}a1-start>") and after.endswith("…")
        assert f"<s{i}a3-end>" not in after
        assert len(b) <= budget // 3 + len(f"[{i}] ")


@pytest.mark.asyncio
async def test_passage_survives_oversized_before_context():
    llm = _LLM()
    gen = RagGenerator(llm, max_context_chars=1000)  # type: ignore[arg-type]
    src = Source(
        doc_id="d", chunk_id="s", content=_text("passage", 500), score=1.0,
        context=[ContextChunk(chunk_id="p", position=-1, content=_text("before", 10_000))],
    )
    await gen.generate("q", [src])
    [block] = _blocks(llm.prompt)
    assert block.endswith(f"(passage)\n{_text('passage', 500)}")
    assert block.startswith("[1] (context before)\n…") and block.endswith("<passage-end>")
    assert len(block) <= 1000 * 3


@pytest.mark.asyncio
async def test_side_with_nothing_left_is_dropped_with_its_delimiter():
    llm = _LLM()
    gen = RagGenerator(llm, max_context_chars=200)  # type: ignore[arg-type]
    src = Source(
        doc_id="d", chunk_id="s", content="p" * 580, score=1.0,
        context=[
            ContextChunk(chunk_id="a", position=-1, content="b" * 300),
            ContextChunk(chunk_id="c", position=1, content="a" * 300),
        ],
    )
    await gen.generate("q", [src])
    [block] = _blocks(llm.prompt)
    # 4 + 10 + 580 = 594 of 600: 6 spare, below either side's delimiter cost.
    assert block == "[1] (passage)\n" + "p" * 580
    # ...and a passage that alone overflows the budget keeps the lone-passage rule.
    src.content = "p" * 700
    await gen.generate("q", [src])
    [block] = _blocks(llm.prompt)
    assert block == ("[1] " + "p" * 700)[:600]


@pytest.mark.asyncio
async def test_long_passage_may_use_the_remainder_beyond_its_share():
    # Source 1's passage alone exceeds its per-source share (budget/3) but fits
    # the remainder: it is kept whole (context dropped to what fits), and the
    # later sources still get in with what is left.
    llm = _LLM()
    gen = RagGenerator(llm, max_context_chars=1000)  # type: ignore[arg-type]
    big = Source(
        doc_id="d", chunk_id="big", content="x" * 1500, score=1.0,
        context=[ContextChunk(chunk_id="n", position=1, content="y" * 100)],
    )
    small = [_source(i, 1, chunk=100) for i in (2, 3)]
    await gen.generate("q", [big, *small])
    blocks = _blocks(llm.prompt)
    assert blocks[0] == "[1] (passage)\n" + "x" * 1500 + "\n(context after)\n" + "y" * 100
    assert [b[:4] for b in blocks] == ["[1] ", "[2] ", "[3] "]


@pytest.mark.asyncio
async def test_without_context_the_old_rules_are_unchanged():
    llm = _LLM()
    gen = RagGenerator(llm, max_context_chars=1000)  # type: ignore[arg-type]
    srcs = [Source(doc_id="d", chunk_id=f"s{i}", content="c" * 600, score=1.0) for i in (1, 2)]
    await gen.generate("q", srcs)
    assert _blocks(llm.prompt) == ["[1] " + "c" * 600]  # [2] would overflow: stop
    srcs[0].content = "c" * 1200
    await gen.generate("q", srcs[:1])
    assert _blocks(llm.prompt) == [("[1] " + "c" * 1200)[:1000]]
