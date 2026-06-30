"""Unit tests for LLMKGExtractor (M4 Phase 2).

No live LLM — a stub ``complete_text`` returns canned text. Covers JSON parsing,
doc_id stamping, dedup, the cost bounds, and graceful degradation (garbage /
raising LLM → empty, never an exception)."""
import pytest

from ragstack.graph.extractor import LLMKGExtractor
from ragstack.models import Chunk


class _StubLLM:
    """Returns the same canned response for every prompt."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def complete_text(self, prompt, max_tokens=512, temperature=0.0) -> str:
        self.calls += 1
        return self._response


class _RaisingLLM:
    async def complete_text(self, prompt, max_tokens=512, temperature=0.0) -> str:
        raise RuntimeError("LLM exploded")


def _chunk(content: str, *, cid: str = "c1", doc_id: str = "doc-1") -> Chunk:
    return Chunk(id=cid, doc_id=doc_id, content=content)


@pytest.mark.asyncio
async def test_parses_strict_json_and_sets_doc_id():
    llm = _StubLLM(
        '{"triples": ['
        '{"subject": "Alice", "predicate": "knows", "object": "Bob"},'
        '{"subject": "Alice", "predicate": "likes", "object": "Coffee"}]}'
    )
    triples = await LLMKGExtractor(llm).extract([_chunk("Alice knows Bob.", doc_id="doc-9")])
    assert {(t.subject, t.predicate, t.object) for t in triples} == {
        ("Alice", "knows", "Bob"),
        ("Alice", "likes", "Coffee"),
    }
    # doc_id is stamped from the chunk; tenant_id is left for the pipeline.
    assert all(t.doc_id == "doc-9" for t in triples)
    assert all(t.tenant_id == "" for t in triples)


@pytest.mark.asyncio
async def test_tolerates_code_fences_and_prose():
    llm = _StubLLM(
        "Sure, here are the triples:\n"
        "```json\n"
        '{"triples": [{"subject": "X", "predicate": "is", "object": "Y"}]}\n'
        "```\nHope that helps!"
    )
    triples = await LLMKGExtractor(llm).extract([_chunk("X is Y.")])
    assert [(t.subject, t.predicate, t.object) for t in triples] == [("X", "is", "Y")]


@pytest.mark.asyncio
async def test_dedups_on_spo_and_doc_id():
    llm = _StubLLM(
        '{"triples": ['
        '{"subject": "A", "predicate": "p", "object": "B"},'
        '{"subject": "A", "predicate": "p", "object": "B"}]}'
    )
    # Two chunks of the SAME doc both yield the duplicate → one survives.
    triples = await LLMKGExtractor(llm).extract(
        [_chunk("a", cid="c1"), _chunk("b", cid="c2")]
    )
    assert len(triples) == 1


@pytest.mark.asyncio
async def test_skips_incomplete_triples():
    llm = _StubLLM(
        '{"triples": ['
        '{"subject": "A", "predicate": "", "object": "B"},'
        '{"subject": "A", "predicate": "p", "object": "B"}]}'
    )
    triples = await LLMKGExtractor(llm).extract([_chunk("a")])
    assert [(t.subject, t.predicate, t.object) for t in triples] == [("A", "p", "B")]


@pytest.mark.asyncio
async def test_garbage_response_returns_empty_without_raising():
    triples = await LLMKGExtractor(_StubLLM("not json at all")).extract([_chunk("a")])
    assert triples == []


@pytest.mark.asyncio
async def test_malformed_triples_field_returns_empty():
    triples = await LLMKGExtractor(_StubLLM('{"triples": "nope"}')).extract([_chunk("a")])
    assert triples == []


@pytest.mark.asyncio
async def test_llm_error_is_swallowed_per_chunk():
    # An LLM that raises must NOT fail ingest — the chunk is skipped.
    triples = await LLMKGExtractor(_RaisingLLM()).extract([_chunk("a"), _chunk("b", cid="c2")])
    assert triples == []


@pytest.mark.asyncio
async def test_max_chunks_bounds_llm_calls():
    llm = _StubLLM('{"triples": [{"subject": "A", "predicate": "p", "object": "B"}]}')
    chunks = [_chunk(f"chunk {i}", cid=f"c{i}", doc_id=f"d{i}") for i in range(5)]
    await LLMKGExtractor(llm, max_chunks=2).extract(chunks)
    assert llm.calls == 2  # only the first two chunks hit the LLM


@pytest.mark.asyncio
async def test_max_triples_per_chunk_caps_output():
    llm = _StubLLM(
        '{"triples": ['
        '{"subject": "A", "predicate": "p", "object": "1"},'
        '{"subject": "A", "predicate": "p", "object": "2"},'
        '{"subject": "A", "predicate": "p", "object": "3"}]}'
    )
    triples = await LLMKGExtractor(llm, max_triples_per_chunk=2).extract([_chunk("a")])
    assert len(triples) == 2


@pytest.mark.asyncio
async def test_empty_inputs_make_no_llm_calls():
    llm = _StubLLM('{"triples": []}')
    assert await LLMKGExtractor(llm).extract([]) == []
    assert llm.calls == 0
    # Blank-content chunks are skipped without an LLM round-trip.
    assert await LLMKGExtractor(llm).extract([_chunk("   ")]) == []
    assert llm.calls == 0
