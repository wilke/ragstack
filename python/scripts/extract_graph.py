#!/usr/bin/env python
"""``extract-graph`` step (#350, phase 6 of #201): extract knowledge-graph
triples from ONE archived chunk version and write the graph leg beside it.

Input: a ``ragstack-archive/1`` version directory (``versions/<n>/`` — the
engine pre-stages the ``ws://`` Directory with the submitter's token, so this
task reads a plain directory and never sees a token). Reads ``chunks.jsonl.gz``
only (text; the vectors are never touched), runs the LLM extractor
(:class:`ragstack.graph.extractor.LLMKGExtractor`, the #347 stamping:
``chunk_id``, ``evidence``, ``derived_by=llm``, ``confidence=1``) over every
chunk with ``--concurrency`` calls in flight, and writes
``<out>/<version>/{manifest.json, triples.jsonl.gz}`` — the *delta* the
workflow emits as a ``Directory`` output whose basename is the version number,
so GoWe post-stages it onto ``versions/<version>/``: the manifest overwrite
(``graph: true``, the ``triples`` role) is the one intended overwrite of an
archived file; the chunk/vector/receipt files there are untouched.

Exit codes (the API classifies a FAILED submission by them):
  0  the leg was written
  3  the archive was REFUSED before anything was written (``ArchiveCorrupt:`` /
     ``SpecMismatch:`` line on stderr — a hash, format, tombstone or identity
     problem); permanent
  4  the version's own triples exceed ``--max-triples`` (the graph budget):
     ``graph_cap_exceeded: live=? incoming=I cap=C would_fit=W`` on stderr,
     nothing written
  1  RETRYABLE: the LLM endpoint failed for every attempted chunk, or for more
     than ``--max-failed-fraction`` of them (``llm_unavailable: …`` on stderr)
     — an outage is not an empty graph, and a delivered empty leg would be
     permanent (the endpoint is idempotent per version) — or a write failure.
     Nothing is written, no delta is emitted.

The LLM API key, when the endpoint needs one, comes from ``$OPENAI_API_KEY``
in the task's environment — never from the command line, which the engine
records with the submission. ``RAGSTACK_FAKE_LLM=1`` swaps in a deterministic
in-process fake (one triple per chunk from its first sentence) for tests.

Usage::

    python scripts/extract_graph.py --version-dir versions/3 --version 3 \
        --collection-id lib --spec-hash cafe0001 \
        --llm-endpoint http://llm:8000 --llm-model Qwen/Qwen3-8B --out .
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from ragstack.graph.budget import GRAPH_CAP_REFUSED_EXIT_CODE, GraphCapExceeded
from ragstack.graph.extract_version import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_FAILED_FRACTION,
    ExtractionUnavailable,
    ExtractRefused,
    extract_version,
)
from ragstack.graph.extractor import LLMKGExtractor
from ragstack.ingestion.archive import ArchiveError

#: Exit code for a refused archive — the replay loader's, so an operator
#: learns one code (restore.REFUSED_EXIT_CODE).
REFUSED_EXIT_CODE = 3

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class FakeLLM:
    """Deterministic stand-in for tests (``RAGSTACK_FAKE_LLM=1``): reads the
    chunk text back out of the extractor's prompt and answers with ONE triple
    from its first sentence — ``(X, is, Y)`` for an "X is Y." sentence, else
    ``(first word, mentions, next words)`` — quoting that sentence as the
    evidence, so the stamping path is exercised end to end with no endpoint."""

    async def complete_text(self, prompt: str, **_kw: object) -> str:
        text = prompt.rsplit("Text:\n", 1)[-1].strip()
        first = _SENTENCE_END.split(text, 1)[0].strip() if text else ""
        if not first:
            return json.dumps({"triples": []})
        body = first.rstrip(".!?")
        if " is " in body:
            subject, obj = body.split(" is ", 1)
            predicate = "is"
        else:
            words = body.split()
            subject, predicate, obj = words[0], "mentions", " ".join(words[1:4]) or "nothing"
        return json.dumps({"triples": [{"subject": subject.strip(), "predicate": predicate,
                                        "object": obj.strip(), "evidence": first}]})


def _version(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"--version must be a non-negative integer, got {text!r}")
    return int(text)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version-dir", required=True, metavar="DIR",
                   help="the archived chunk version (ragstack-archive/1) to extract from")
    p.add_argument("--version", type=_version, default=None,
                   help="version number N the directory must be (the output is <out>/N); "
                        "default: the manifest's own")
    p.add_argument("--collection-id", default="",
                   help="registry collection id the manifest must name (refused otherwise)")
    p.add_argument("--tenant", default="",
                   help="the tenant the caller expects; only a note is printed when it "
                        "disagrees with the manifest. Triples keep their chunk's tenant_id "
                        "(the manifest's tenant is the fallback), never this value")
    p.add_argument("--spec-hash", default="",
                   help="the registry row's build-spec hash the manifest must carry")
    p.add_argument("--llm-endpoint", default=os.getenv("LLM_ENDPOINT", ""),
                   help="OpenAI-compatible chat endpoint (env LLM_ENDPOINT)")
    p.add_argument("--llm-model", default=os.getenv("LLM_MODEL", ""),
                   help="model name the endpoint serves (env LLM_MODEL)")
    p.add_argument("--llm-timeout", type=float, default=120.0,
                   help="per-call LLM timeout in seconds (default 120)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help=f"LLM calls in flight at once (default {DEFAULT_CONCURRENCY})")
    p.add_argument("--max-triples", type=int, default=0,
                   help="refuse (exit 4) when the version yields more triples than this; "
                        "0 = unbounded. The load step applies the same cap against the "
                        "live graph")
    p.add_argument("--max-triples-per-chunk", type=int, default=0,
                   help="keep at most N triples per chunk (0 = unbounded)")
    p.add_argument("--max-failed-fraction", type=float, default=DEFAULT_MAX_FAILED_FRACTION,
                   help="refuse the run (exit 1, retryable, nothing written) when more than "
                        "this share of the attempted chunks failed their LLM call — and "
                        "always when every one did (default "
                        f"{DEFAULT_MAX_FAILED_FRACTION})")
    p.add_argument("--out", default=".",
                   help="parent directory for the <N>/ delta (default: cwd)")
    p.add_argument("--summary", default="extract-graph-summary.json",
                   help="where to write the run summary (outside the delta directory)")
    return p.parse_args(argv)


def _build_llm(args):
    if os.getenv("RAGSTACK_FAKE_LLM", "") == "1":
        return FakeLLM(), "fake"
    if not args.llm_endpoint or not args.llm_model:
        raise SystemExit("--llm-endpoint and --llm-model are required (or RAGSTACK_FAKE_LLM=1)")
    import httpx

    from ragstack.llm import OpenAILLM

    http = httpx.AsyncClient(timeout=args.llm_timeout)
    llm = OpenAILLM(base_url=args.llm_endpoint, model=args.llm_model, http=http,
                    api_key=os.getenv("OPENAI_API_KEY") or None)
    return llm, args.llm_model


async def amain(args) -> int:
    from ragstack.ingestion.archive import read_manifest

    vdir = Path(args.version_dir)
    try:
        manifest = read_manifest(vdir)
    except ArchiveError as e:
        print(f"ArchiveCorrupt: {e}", file=sys.stderr, flush=True)
        return REFUSED_EXIT_CODE
    version = int(manifest.get("version", 0) or 0)
    if args.version is not None and args.version != version:
        print(f"SpecMismatch: {vdir}: manifest version {version} != --version {args.version}",
              file=sys.stderr, flush=True)
        return REFUSED_EXIT_CODE
    if args.tenant and str(manifest.get("tenant") or "") != args.tenant:
        print(f"note: manifest tenant {manifest.get('tenant')!r} != --tenant {args.tenant!r}; "
              "triples keep their chunk's tenant", flush=True)
    llm, name = _build_llm(args)
    extractor = LLMKGExtractor(llm, max_triples_per_chunk=args.max_triples_per_chunk)
    out_dir = Path(args.out) / str(version)
    try:
        summary = await extract_version(
            vdir, extractor, out_dir=out_dir, concurrency=args.concurrency,
            collection_id=args.collection_id, spec_hash=args.spec_hash,
            max_triples=args.max_triples, max_failed_fraction=args.max_failed_fraction,
            extractor_name=name, log=lambda msg: print(msg, flush=True),
        )
    except ExtractRefused as e:
        print(str(e), file=sys.stderr, flush=True)
        return REFUSED_EXIT_CODE
    except ExtractionUnavailable as e:
        print(str(e), file=sys.stderr, flush=True)
        print("refused: LLM unavailable; nothing written (retryable)", flush=True)
        return 1
    except GraphCapExceeded as e:
        print(str(e), file=sys.stderr, flush=True)
        print("refused: nothing written", flush=True)
        return GRAPH_CAP_REFUSED_EXIT_CODE
    except ArchiveError as e:
        print(f"extract-graph: {e}", file=sys.stderr, flush=True)
        return 1
    finally:
        http = getattr(llm, "http", None)
        if http is not None and hasattr(http, "aclose"):
            await http.aclose()
    with open(args.summary, "w", encoding="utf-8") as fh:
        json.dump(summary.as_dict(), fh, indent=2, sort_keys=True)
    print(f"[{args.collection_id or manifest.get('collection_id')}] version={version}: "
          f"triples={summary.n_triples} chunks={summary.n_chunks} "
          f"→ {out_dir} ({summary.as_dict()['chunks_per_second']} chunks/s)", flush=True)
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")
    if not 0.0 <= args.max_failed_fraction <= 1.0:
        raise SystemExit("--max-failed-fraction must be between 0 and 1")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
