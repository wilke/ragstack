"""Unit tests for query rewriters."""
import pytest

from ragstack.rewriting.rewriters import (
    HyDERewriter,
    MultiQueryRewriter,
    PassthroughRewriter,
)


class _FakeLLM:
    """Exposes complete_text (what the rewriters call), returning a canned reply."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompt: str | None = None

    async def complete_text(self, prompt: str, **kw) -> str:
        self.prompt = prompt
        return self.reply


@pytest.mark.asyncio
async def test_passthrough_rewriter_returns_original():
    assert await PassthroughRewriter().rewrite("what is RAG?") == ["what is RAG?"]


@pytest.mark.asyncio
async def test_passthrough_rewriter_handles_empty_string():
    assert await PassthroughRewriter().rewrite("") == [""]


@pytest.mark.asyncio
async def test_multiquery_includes_original_and_alternatives():
    llm = _FakeLLM("alt one\nalt two\nalt three")
    out = await MultiQueryRewriter(llm, n=3).rewrite("original q")
    assert out[0] == "original q"  # original always first
    assert out[1:] == ["alt one", "alt two", "alt three"]
    assert "original q" in llm.prompt


@pytest.mark.asyncio
async def test_multiquery_caps_at_n():
    llm = _FakeLLM("a\nb\nc\nd\ne")
    out = await MultiQueryRewriter(llm, n=2).rewrite("q")
    assert out == ["q", "a", "b"]  # original + n alternatives


@pytest.mark.asyncio
async def test_hyde_returns_query_plus_hypothetical():
    llm = _FakeLLM("Paris is the capital of France.")
    out = await HyDERewriter(llm).rewrite("capital of France?")
    assert out == ["capital of France?", "Paris is the capital of France."]
