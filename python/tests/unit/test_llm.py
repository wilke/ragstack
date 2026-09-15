"""Unit tests for the RAG answer generator."""
import json

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


@pytest.mark.asyncio
async def test_extra_body_merged_into_request():
    # A registered model's params (e.g. a reasoning model's enable_thinking=false)
    # must reach the chat request as top-level fields, so the model answers into
    # `content` instead of a separate reasoning field.
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    extra = {"chat_template_kwargs": {"enable_thinking": False}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenAILLM(base_url="http://llm", model="m", http=http, extra_body=extra)
        await llm.complete([{"role": "user", "content": "hi"}])
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["model"] == "m"  # base fields still present


# --- the two hops that actually carry the token-cap fix ----------------------
#
# Both of these test the HTTP BOUNDARY, not a collaborator's signature. The
# first version of these guarantees was covered only by a fake whose
# `complete_detailed` recorded its own argument — which stops one hop short of
# "sent", and a review proved it: replacing `"max_tokens": max_tokens` with a
# literal 512, and `finish_reason = choices[0].get(...)` with `""`, each left
# the entire 3880-test suite green while fully restoring the reported defect.


@pytest.mark.asyncio
async def test_max_tokens_reaches_the_request_body():
    """The ceiling must appear in the JSON posted to the model."""
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenAILLM(base_url="http://llm", model="m", http=http)
        await llm.complete([{"role": "user", "content": "hi"}], max_tokens=2500)

    assert seen["max_tokens"] == 2500, "the caller's ceiling never reached the model"


@pytest.mark.asyncio
async def test_the_default_ceiling_is_still_512_for_callers_that_do_not_ask():
    """Opt-in only: an unchanged caller must post exactly what it always did."""
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenAILLM(base_url="http://llm", model="m", http=http)
        await llm.complete([{"role": "user", "content": "hi"}])

    assert seen["max_tokens"] == 512


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, expected",
    [("length", "length"), ("stop", "stop"), (None, ""), ("content_filter", "content_filter")],
)
async def test_finish_reason_is_read_from_the_real_response_body(reason, expected):
    """Extracted from the RESPONSE, not supplied by a fake.

    `truncated` on /v1/query is derived from this value, so a server that
    stopped reporting it would silently make every truncated table look complete.
    """
    choice: dict[str, object] = {"message": {"content": "A\tB"}}
    if reason is not None:
        choice["finish_reason"] = reason

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [choice]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenAILLM(base_url="http://llm", model="m", http=http)
        text, got = await llm.complete_detailed([{"role": "user", "content": "hi"}])

    assert text == "A\tB"
    assert got == expected


@pytest.mark.asyncio
async def test_an_empty_answer_names_the_finish_reason():
    """`length` with an empty body is the WORST truncation case — the ceiling was
    so low the model produced nothing usable — and it surfaces as a generic
    failure. Naming the reason in the error is the only place that fact survives,
    so it needs an assertion or it silently regresses."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenAILLM(base_url="http://llm", model="m", http=http)
        with pytest.raises(ValueError, match="finish_reason=length"):
            await llm.complete([{"role": "user", "content": "hi"}])
