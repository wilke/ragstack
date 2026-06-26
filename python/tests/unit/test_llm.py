"""Unit tests for the RAG answer generator."""
import httpx
import pytest

from ragstack.llm import OpenAILLM, RagGenerator
from ragstack.models import Source


class _FakeLLM:
    def __init__(self, reply: str = "the answer") -> None:
        self.reply = reply
        self.messages: list[dict] | None = None

    async def complete(self, messages, max_tokens=512, temperature=0.0):
        self.messages = messages
        return self.reply


def _source(content: str, score: float = 1.0) -> Source:
    return Source(doc_id="d", chunk_id="c", content=content, score=score, metadata={})


@pytest.mark.asyncio
async def test_generate_grounds_on_sources():
    llm = _FakeLLM("Paris is the capital.")
    gen = RagGenerator(llm)
    answer = await gen.generate(
        "What is the capital of France?",
        [_source("France's capital is Paris."), _source("Paris has 2M people.")],
    )
    assert answer == "Paris is the capital."
    # The prompt carries the question and the source passages as context.
    user_msg = llm.messages[-1]["content"]
    assert "What is the capital of France?" in user_msg
    assert "France's capital is Paris." in user_msg
    assert "[1]" in user_msg and "[2]" in user_msg


@pytest.mark.asyncio
async def test_generate_with_no_sources_still_calls_llm():
    llm = _FakeLLM("I don't know.")
    gen = RagGenerator(llm)
    answer = await gen.generate("anything?", [])
    assert answer == "I don't know."
    assert "no relevant passages" in llm.messages[-1]["content"]


@pytest.mark.asyncio
async def test_context_respects_char_budget():
    llm = _FakeLLM()
    gen = RagGenerator(llm, max_context_chars=20)
    await gen.generate("q", [_source("x" * 15), _source("y" * 15), _source("z" * 15)])
    ctx = llm.messages[-1]["content"]
    # First passage always included; the budget stops further ones.
    assert "x" * 15 in ctx
    assert "z" * 15 not in ctx


async def _complete_against(payload: dict) -> str:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenAILLM(base_url="http://llm", model="m", http=http)
        return await llm.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_complete_raises_on_empty_choices():
    # Content-filtered / gateway responses can omit choices → must not IndexError.
    with pytest.raises(ValueError):
        await _complete_against({"choices": []})


@pytest.mark.asyncio
async def test_complete_raises_on_null_content():
    # finish_reason length / tool_calls can yield null content → must not return None.
    with pytest.raises(ValueError):
        await _complete_against({"choices": [{"message": {"content": None}}]})


@pytest.mark.asyncio
async def test_complete_returns_content_on_well_formed_response():
    out = await _complete_against({"choices": [{"message": {"content": "hello"}}]})
    assert out == "hello"
