"""Unit tests for query rewriters."""
import pytest

from ragstack.rewriting.rewriters import PassthroughRewriter


@pytest.mark.asyncio
async def test_passthrough_rewriter_returns_original():
    rewriter = PassthroughRewriter()
    result = await rewriter.rewrite("what is RAG?")
    assert result == ["what is RAG?"]


@pytest.mark.asyncio
async def test_passthrough_rewriter_handles_empty_string():
    rewriter = PassthroughRewriter()
    result = await rewriter.rewrite("")
    assert result == [""]
