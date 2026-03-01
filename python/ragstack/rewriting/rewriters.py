"""Query rewriting strategies."""
from __future__ import annotations


class PassthroughRewriter:
    """Return the original query unchanged (no-op, useful for testing)."""

    async def rewrite(self, query: str) -> list[str]:
        return [query]


class MultiQueryRewriter:
    """
    Expand a query into multiple paraphrases using an LLM.

    Improves recall by retrieving results for each paraphrase and
    deduplicating via Reciprocal Rank Fusion in the scorer.
    """

    def __init__(self, llm_client: object, n: int = 3) -> None:
        self._llm = llm_client
        self.n = n

    async def rewrite(self, query: str) -> list[str]:
        prompt = (
            f"Generate {self.n} alternative phrasings of the following question. "
            f"Return one per line, no numbering.\n\nQuestion: {query}"
        )
        # llm_client is expected to expose an async `complete(prompt) -> str` method.
        response: str = await self._llm.complete(prompt)  # type: ignore[attr-defined]
        alternatives = [line.strip() for line in response.splitlines() if line.strip()]
        return [query] + alternatives[: self.n]


class HyDERewriter:
    """
    Hypothetical Document Embeddings (HyDE).

    Ask the LLM to write a hypothetical answer to the query, then
    use that answer as the retrieval query instead of the original.
    """

    def __init__(self, llm_client: object) -> None:
        self._llm = llm_client

    async def rewrite(self, query: str) -> list[str]:
        prompt = (
            "Write a concise, factual answer to the following question "
            "as if you found it in a document:\n\n"
            f"Question: {query}\n\nHypothetical answer:"
        )
        hypothetical: str = await self._llm.complete(prompt)  # type: ignore[attr-defined]
        return [query, hypothetical.strip()]
