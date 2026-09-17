"""The GoWe/CWL worker tools must actually attach the DOI enricher (#596).

This is the regression that made #596 a bug rather than a feature request. The
enrichment machinery, the settings and the pipeline hook all already existed —
but they were wired only into the API process. The user-upload path on a
``INGEST_BACKEND=gowe`` tenant is ``cwl/pdf-ingest-scatter.cwl``, i.e.
``pdf_extract.py -> ingest_shard.py`` in a container, and *that* pipeline was
built with no enricher at all. So the measured outcome was a collection with
``doi`` on 382/382 chunks and title/authors/journal/pmid/pmcid on none, no matter
what the tenant had configured.

Nothing here touches the network: the pipelines are built, inspected and thrown
away. What is asserted is only that the object is attached, and that it is
absent by default.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import embed_shard as embed_shard_tool  # noqa: E402
import ingest_shard as ingest_shard_tool  # noqa: E402

from ragstack.ingestion.doi_metadata import DoiEnricher  # noqa: E402

BASE_ARGV = [
    "shard.jsonl",
    "--embedding-api", "openai",
    "--embedding-url", "http://localhost:9001",
    "--chunk-method", "fixed",
    "--chunk-token-counter", "estimate",
]
INGEST_ARGV = BASE_ARGV + [
    "--collection", "c", "--tenant", "t",
    "--vector-backend", "memory", "--text-backend", "memory",
    "--qdrant-url", "http://localhost:6333", "--es-url", "http://localhost:9200",
]


@pytest.mark.asyncio
async def test_ingest_shard_attaches_the_enricher_when_asked():
    """ingest_shard is the tool the USER-UPLOAD path runs (pdf-ingest-scatter)."""
    args = ingest_shard_tool.parse_args(INGEST_ARGV + [
        "--doi-enrichment", "--doi-mailto", "ops@example.org",
    ])
    async with httpx.AsyncClient() as http:
        pipeline = await ingest_shard_tool._build_pipeline(args, http)
    assert isinstance(pipeline.doi_enricher, DoiEnricher)


@pytest.mark.asyncio
async def test_ingest_shard_has_no_enricher_by_default():
    """Off unless asked: a hand-run bulk ingest makes no outbound request, and
    the argv is exactly what it was before #596."""
    args = ingest_shard_tool.parse_args(INGEST_ARGV)
    assert args.doi_enrichment is False
    async with httpx.AsyncClient() as http:
        pipeline = await ingest_shard_tool._build_pipeline(args, http)
    assert pipeline.doi_enricher is None


@pytest.mark.asyncio
async def test_embed_shard_attaches_the_enricher_when_asked():
    """embed_shard is the decoupled plane's chunking half (cwl/pdf-ingest.cwl),
    where enrichment has to happen because it runs between load and chunk — the
    load stage downstream only ever sees finished chunks."""
    args = embed_shard_tool.parse_args(BASE_ARGV + ["--doi-enrichment"])
    async with httpx.AsyncClient() as http:
        pipeline = embed_shard_tool._build_pipeline(args, http)
    assert isinstance(pipeline.doi_enricher, DoiEnricher)


@pytest.mark.asyncio
async def test_embed_shard_has_no_enricher_by_default():
    args = embed_shard_tool.parse_args(BASE_ARGV)
    async with httpx.AsyncClient() as http:
        pipeline = embed_shard_tool._build_pipeline(args, http)
    assert pipeline.doi_enricher is None
