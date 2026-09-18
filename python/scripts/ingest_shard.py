#!/usr/bin/env python
"""Atomic per-shard ingest tool for the CWL/GoWe bulk-ingest workflow (ADR-0001
step 2). Ingests ONE shard (a JSONL file of document records — under #203 2b a
*batch* of N PDFs' extracted text) → emits a ``receipt.json`` (chunk ids + a
per-document catalog with each document's status). The workflow scatters this
over the shards; ``merge_receipts.py`` gathers the receipts into a run summary.

**Per-document failure does not fail the task.** A document that produced no
embeddable chunk, or that the extract stage skipped (a scanned PDF — pass its
``--extract-report`` so the receipt lists it with the constant ``NO_TEXT_ERROR``),
is recorded on its own ``docs[i].error`` row and the batch continues; the
embedding file holds only the successful documents' chunks (header-only when
none succeeded). The task exits non-zero ONLY for a batch-level error (the batch
could not be loaded/embedded/indexed — then every row carries that error): a
batch in which every document failed still exits 0, because the engine would
otherwise retry and then fail the whole run after the sibling batches had
already upserted (see ``ragstack.ingestion.shard``).

**Stateless + idempotent by design.** It reuses ``IngestionPipeline.ingest``
(which owns chunk → embed → quarantine → delete-prior → upsert → neighbor-link)
and carries **no** checkpoint / resume / in-process concurrency — the workflow
engine owns scatter/retry/resume, so this tool just does one shard and reports.
Re-running a shard overwrites in place (deterministic uuid5 ids + upsert), so a
GoWe retry is safe. This deliberately sheds the bespoke machinery in
``ingest_jsonl.py`` (#71) rather than re-implementing the pipeline (#25).

Like the eval scatter step, a real run needs the live embedding fleet + Qdrant/ES;
it is not a CI step. The ``run_shard`` core is unit-tested offline with in-memory
stores + a fake embedder.

Usage::

    python scripts/ingest_shard.py shard.s0.jsonl --tenant public \
        --collection ragstack_sfr_tok256 --es-index ragstack_sfr_tok256 \
        --embedding-api openai --embedding-model Salesforce/SFR-Embedding-Mistral \
        --embedding-url http://localhost:9001 --chunk-method fixed_token \
        --chunk-size 256 --chunk-overlap 32 --out receipt.json
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

from ragstack.embed_pool import make_embedder_auto
from ragstack.ingestion.boilerplate import filter_from_mode
from ragstack.ingestion.chunk_cap import CAP_REFUSED_EXIT_CODE, is_cap_refusal
from ragstack.ingestion.chunker_config import build_chunker, needs_embed_fn
from ragstack.ingestion.doi_metadata import add_doi_enrichment_args, enricher_from_args
from ragstack.ingestion.embed_bridge import SyncEmbedBridge
from ragstack.ingestion.loaders import JsonlLoader
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.ingestion.receipts import COMPLETED
from ragstack.ingestion.shard import ExtractReport, run_shard
from ragstack.ops import ingest_target
from ragstack.stores.elasticsearch import ElasticsearchTextIndex
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.qdrant import QdrantVectorStore


def _build_embedder(args, http: httpx.AsyncClient):
    return make_embedder_auto(
        api=args.embedding_api, http=http, base_urls=args.embedding_url,
        model=args.embedding_model,
        api_key=args.embedding_api_key or os.getenv("OPENAI_API_KEY"),
        max_concurrency=args.embedding_max_concurrency,
    )


def _semantic_params(spec) -> dict:
    """The semantic tunables, **from the registry entry** — never from a CLI flag.

    ``_gowe_inputs`` sends ``chunk_method``/``chunk_size``/``chunk_overlap`` as
    workflow inputs but has never sent ``chunk_params``, so ``buffer_size``,
    ``breakpoint_percentile_threshold`` and ``min_chunk_length`` silently fell back
    to this tool's defaults. Harmless so far — every semantic collection carries
    ``{}`` and the defaults coincide (3 / 80.0 / 500) — but semantic is the first
    method where those values change the output, so a collection created with
    ``buffer_size=5`` must be ingested with 5.

    Deliberately NOT a CWL input: a submission's ``submitted_inputs`` is an
    immutable, UI-rendered, plaintext snapshot, and a second source for a value
    that is part of collection identity is exactly how #609 happened. The entry is
    the one source.

    A bad value raises here rather than reaching ``make_chunker`` as a TypeError
    mid-shard, so the task fails at configuration with a message naming the key.
    """
    params = dict(getattr(spec, "chunk_params", None) or {}) if spec is not None else {}
    out: dict = {}
    for key, cast in (("buffer_size", int), ("breakpoint_percentile", float),
                      ("min_chunk_length", int)):
        # The API's contract spells the percentile `breakpoint_percentile_threshold`
        # (routers/collections.py SEMANTIC_PARAM_BOUNDS); build_chunker's kwarg is
        # `breakpoint_percentile`. Accept the contract's spelling, which is what a
        # collection actually records.
        src = "breakpoint_percentile_threshold" if key == "breakpoint_percentile" else key
        if params.get(src) is None:
            continue
        try:
            out[key] = cast(params[src])
        except (TypeError, ValueError):
            raise SystemExit(
                f"collection chunk_params[{src!r}] must be a number; got {params[src]!r}"
            ) from None
    return out


def _build_chunker(args, spec=None, embed_fn=None):
    """Chunker via the shared factory (fixed_token token-window included).

    ``embed_fn`` is the breakpoint bridge, required by the semantic methods and
    built by :func:`amain` (see there for why it is not built in here). Passing
    ``None`` for a semantic method reaches ``make_chunker``'s own
    ``requires an embed_fn`` refusal rather than being silently demoted.
    """
    chunker, _counter, _max_tokens = build_chunker(
        args.chunk_method,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        model=args.embedding_model,
        token_backend=args.chunk_token_counter,
        max_tokens=args.chunk_max_tokens,
        base_url=args.embedding_url[0] if args.embedding_url else None,
        api_key=args.embedding_api_key or os.getenv("OPENAI_API_KEY"),
        embed_fn=embed_fn,
        **_semantic_params(spec),
    )
    return chunker


async def _build_pipeline(args, http: httpx.AsyncClient, target=None,
                          embed_fn=None) -> IngestionPipeline:
    # Guard the half-ingest footgun: a qdrant vector store with an in-memory text
    # index (or vice versa) would silently write only one leg. Require both real
    # or both in-memory.
    if (args.vector_backend == "qdrant") != (args.text_backend == "elasticsearch"):
        raise SystemExit(
            "vector-backend and text-backend must be consistent (both durable or "
            f"both in-memory); got vector={args.vector_backend} text={args.text_backend}"
        )
    # fail fast on a bad chunk config, before any I/O
    chunker = _build_chunker(args, getattr(target, "spec", None), embed_fn=embed_fn)
    embedder = _build_embedder(args, http)
    if args.vector_backend == "memory":
        vstore = InMemoryVectorStore()
        tindex = InMemoryTextIndex()
    else:
        if target is None:  # pragma: no cover — main() resolves before calling
            raise SystemExit("internal: no ingest target resolved")
        # Both names come from the registry entry, never from the command line
        # (#263). A contradicting --es-index was refused during resolution.
        es_index = target.es_index
        # …and so does the ES cluster: a routed index lives on another cluster and
        # the route wins over --es-url (ES_COLLECTION_ROUTES). Unrouted, this is
        # exactly the --es-url the operator passed.
        es_url = target.es_url or args.es_url
        dim = len((await embedder.embed(["dimension probe"]))[0])
        # The probed dim must match the entry, or this shard's vectors are not the
        # ones the entry promises (ADR-0002).
        target.check_build(dim=dim)
        vstore = QdrantVectorStore(url=target.qdrant_url, collection=target.collection,
                                   vector_size=dim, timeout=args.qdrant_timeout)
        await vstore.ensure_collection()
        tindex = ElasticsearchTextIndex(url=es_url, index=es_index)
        await tindex.ensure_index()
    return IngestionPipeline(loader=JsonlLoader(), chunker=chunker, embedder=embedder,
                             vector_store=vstore, text_index=tindex,
                             # #596: the USER-UPLOAD path runs here, not in the
                             # API process — cwl/pdf-ingest-scatter.cwl is
                             # pdf_extract -> ingest_shard, so the enricher the
                             # API builds from its own settings never reaches
                             # this document. Without this argument every
                             # gowe-backend upload was born with a DOI and
                             # nothing else. None when --doi-enrichment is off.
                             doi_enricher=enricher_from_args(args, http),
                             boilerplate_filter=filter_from_mode(
                                 args.boilerplate, args.boilerplate_config))


def _build_bridge(args) -> SyncEmbedBridge:
    """The breakpoint embed bridge for a semantic shard.

    It embeds with the SAME ``--embedding-*`` backend the shard stores vectors
    with — which, on the GoWe path, is the collection's own endpoints (the API
    sends them as ``embedding_url``). That is the rule ``deps._embed_bridge_for``
    already applies in-process: detecting boundaries with a different model than
    the one storing the chunks would make the boundaries unrelated to the
    embedding space they are supposed to describe.

    No ``--breakpoint-embedding-*`` flags here, deliberately (``ingest_jsonl.py``
    has them and keeps them): nothing on a GoWe submission can populate them, and
    they would be a second source of a value that is part of collection identity.
    """
    return SyncEmbedBridge(
        lambda http: _build_embedder(args, http), batch_size=args.batch_size
    )


async def amain(args, target=None) -> int:
    timeout = httpx.Timeout(300.0, connect=30.0)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    report = ExtractReport.load(args.extract_report) if args.extract_report else None
    # Built HERE and not in _build_pipeline: the bridge owns a background event
    # loop + thread, and closing it has to outlive run_shard. Built lazily and only
    # for the methods that embed while chunking, so a non-semantic shard — and a
    # shard refused by _build_pipeline's own config guards — never spins a thread.
    bridge: SyncEmbedBridge | None = (
        _build_bridge(args) if needs_embed_fn(args.chunk_method) else None
    )
    try:
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as http:
            pipeline = await _build_pipeline(args, http, target, embed_fn=bridge)
            shard_id = args.shard_id or os.path.basename(args.shard)
            receipt = await run_shard(pipeline, args.shard, args.tenant, shard_id,
                                      embedding_file=args.embedding_file or None,
                                      report=report, max_chunks=max(0, args.max_chunks))
    finally:
        if bridge is not None:
            # close() can raise RuntimeError of its own: after thread.join(5.0)
            # times out against a wedged endpoint it closes a still-running loop.
            # Letting that escape would turn a correctly written FAILED receipt
            # into a traceback and lose the exit code the engine classifies on.
            # The thread is daemon=True, so an unclosed bridge cannot hang exit.
            try:
                bridge.close()
            except Exception as e:  # pragma: no cover - defensive
                print(f"[ingest_shard] embed bridge close failed: {e!r}", flush=True)
    receipt.write(args.out)
    print(f"[{shard_id}] status={receipt.status} docs={receipt.n_docs} "
          f"failed={receipt.n_docs_failed} chunks={receipt.n_chunks} → {args.out}"
          + (f"  ERROR: {receipt.error}" if receipt.error else ""), flush=True)
    for row in receipt.docs:
        if row.error:
            print(f"[{shard_id}]   failed {os.path.basename(row.source)}: {row.error}",
                  flush=True)
    # This script builds a BARE ``JsonlLoader()`` — no passthrough allow-list at
    # all — so a JATS shard ingested through it loses pmcid/pmid/journal/… on
    # every record. That drop used to be invisible; say it out loud.
    dropped = getattr(pipeline.loader, "dropped", None)
    if dropped:
        print(f"[{shard_id}] dropped record metadata — {dropped.summary()}", flush=True)
    # Arm ADR-0002's build-spec guard for the store this shard wrote into. The
    # chunk count is deliberately omitted: many shards write one store, so a
    # per-shard count would describe the corpus wrongly. It is the SPEC that arms
    # the guard; the count is informational (#263).
    manifest_dir = args.manifest_dir or os.getenv("COLLECTION_MANIFEST_DIR", "")
    if target is not None and manifest_dir and receipt.status == COMPLETED:
        target.write_manifest(
            manifest_dir,
            embedding_api=args.embedding_api,
            embedding_endpoints=list(args.embedding_url),
            corpus=f"shard {shard_id}",
        )
    if is_cap_refusal(receipt.error):
        # The chunk cap (#291), checked BEFORE the generic rule: a cap refusal
        # is a whole-job refusal by spec, so it IS a task failure — with its own
        # exit code (the deterministic signal the API classifies the FAILED
        # submission by) and the refusal line on stderr (the scheduler keeps
        # the first part of stderr on the submission's error record).
        print(receipt.error, file=sys.stderr, flush=True)
        return CAP_REFUSED_EXIT_CODE
    return 0 if receipt.status == COMPLETED else 1


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("shard", help="one JSONL shard file to ingest")
    p.add_argument("--out", default="receipt.json", help="output receipt path")
    p.add_argument("--shard-id", default=None, help="receipt shard id (default: basename)")
    p.add_argument("--embedding-file", default="",
                   help="also write the embedded chunks here (ragstack.embedding_file/v1) "
                        "on the way to the stores — the archive step's input (#357). "
                        "Removed again if the shard fails")
    p.add_argument("--extract-report", default="",
                   help="the extract stage's sidecar report (pdf_extract.py --report): "
                        "its skipped files become failed rows of the receipt, carrying "
                        "their constant error (e.g. a scanned PDF's), and its inputs "
                        "list lets an all-skipped batch be reported per document (#203)")
    p.add_argument("--tenant", default="public")
    p.add_argument("--max-chunks", type=int, default=0,
                   help="per-collection chunk cap (#291): refuse this batch, whole, "
                        "when the collection's live chunk count plus the batch's "
                        "chunks would exceed N (one count, before any write; the "
                        "receipt reports live/incoming/cap/would_fit under the "
                        f"chunk_cap_exceeded label and the tool exits {CAP_REFUSED_EXIT_CODE}). "
                        "0 (default) = unlimited. The API derives it per job: the "
                        "collection's registry override, else MAX_CHUNKS_PER_COLLECTION "
                        "for a user-created collection")
    # Same three modes as ingest_jsonl.py; "flag" only stamps metadata, so the
    # offline plane matches the online API's default instead of silently
    # producing chunks the API path would have tagged.
    p.add_argument("--boilerplate", choices=["off", "flag", "drop"], default="flag",
                   help="chunk-level boilerplate handling (see ingest_jsonl.py)")
    p.add_argument("--boilerplate-config", default="",
                   help="JSON object overriding BoilerplateConfig thresholds")
    p.add_argument("--chunk-method", default="fixed_token")
    p.add_argument("--chunk-size", type=int, default=256)
    p.add_argument("--chunk-overlap", type=int, default=32)
    p.add_argument("--chunk-token-counter", choices=["hf", "endpoint", "estimate"],
                   default="hf", help="token counter backend (fixed_token forces hf)")
    p.add_argument("--chunk-max-tokens", type=int, default=None,
                   help="per-chunk token budget (model window); default auto-detect")
    p.add_argument("--vector-backend", choices=["qdrant", "memory"], default="qdrant")
    p.add_argument("--collection", default=None,
                   help="DEPRECATED — the PHYSICAL store name. Accepted only when "
                        "a registry entry already claims it; prefer --collection-id")
    p.add_argument("--qdrant-url", required=True,
                   help="Qdrant instance to WRITE to. REQUIRED, no default: the "
                        "conventional localhost:6333 is the PRODUCTION instance on "
                        "the deployment host, and this is a write path (#454)")
    p.add_argument("--qdrant-timeout", type=int, default=120)
    p.add_argument("--text-backend", choices=["elasticsearch", "memory"], default="elasticsearch")
    p.add_argument("--es-url", required=True,
                   help="Elasticsearch instance to WRITE to. REQUIRED, no default — "
                        "see --qdrant-url (#454)")
    p.add_argument("--es-index", default=None)
    p.add_argument("--embedding-api", choices=["sidecar", "openai"], default="openai")
    p.add_argument("--embedding-url", nargs="+", default=["http://localhost:9001"])
    p.add_argument("--embedding-model", default="Salesforce/SFR-Embedding-Mistral")
    p.add_argument("--embedding-api-key", default=None)
    p.add_argument("--embedding-max-concurrency", type=int, default=8)
    p.add_argument("--manifest-dir", default="",
                   help="write a provenance manifest here (defaults to "
                        "$COLLECTION_MANIFEST_DIR). Arms ADR-0002's build-spec "
                        "guard for this store; skipped if neither is set")
    add_doi_enrichment_args(p)
    ingest_target.add_arguments(p)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # Resolve the registry entry before embedding or writing anything (#263). The
    # in-memory backend owns no physical store, so it has nothing to register.
    target = (
        ingest_target.resolve_or_exit(
            args,
            model=args.embedding_model,
            chunk_method=args.chunk_method,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        if args.vector_backend == "qdrant"
        else None
    )
    return asyncio.run(amain(args, target))


if __name__ == "__main__":
    sys.exit(main())
