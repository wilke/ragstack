"""Unit tests for the RRF scorer."""
import pytest

from ragstack.models import Chunk, ScoredChunk
from ragstack.scoring.scorers import RRFScorer


def _scored(cid: str, score: float = 1.0) -> ScoredChunk:
    return ScoredChunk(chunk=Chunk(id=cid, doc_id="doc1", content="test"), score=score)


def test_rrf_fuse_deduplicates_and_boosts_common_results():
    scorer = RRFScorer(k=60)
    list1 = [_scored("c1"), _scored("c2"), _scored("c3")]
    list2 = [_scored("c1"), _scored("c3"), _scored("c4")]
    fused = scorer.fuse([list1, list2])
    ids = [s.chunk.id for s in fused]
    # c1 and c3 appear in both lists → should rank higher
    assert ids[0] in ("c1", "c3")
    assert ids[1] in ("c1", "c3")


def test_rrf_fuse_all_unique_returns_all():
    scorer = RRFScorer()
    list1 = [_scored("a"), _scored("b")]
    list2 = [_scored("c"), _scored("d")]
    fused = scorer.fuse([list1, list2])
    assert len(fused) == 4


def test_rrf_fuse_empty_lists():
    scorer = RRFScorer()
    fused = scorer.fuse([[], []])
    assert fused == []


@pytest.mark.asyncio
async def test_rrf_scorer_score_returns_descending_scores():
    scorer = RRFScorer()
    chunks = [Chunk(id=f"c{i}", doc_id="d", content="x") for i in range(5)]
    results = await scorer.score("query", chunks)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
