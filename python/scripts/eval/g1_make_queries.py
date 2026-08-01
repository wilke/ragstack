#!/usr/bin/env python
"""G1 pilot query generation — LLM (paraphrase-first) and human-written paths.

Protocol §4.2 wants 600 questions per rung, generated from chunks of the P50
judged core, filtered, and pinned as a fixture *before* any retrieval run. §9
registers the reason that is dangerous:

    **T1b — query-generation contamination.** A query written from the chunk it
    is meant to retrieve shares vocabulary with that chunk by construction. This
    is the same lexical bias that disqualifies the known-item title→doc proxy as
    primary evidence (§5.7), and it points at the *sparse* leg — it inflates
    BM25-only and hybrid relative to dense-only, on exactly the axis RQ1/H1
    measures.

The protocol's mitigations are not optional and both are implemented here:

**(a) Generate from a paraphrase, not the chunk.** :func:`generate_one` is a
two-pass pipeline. Pass 1 asks the model for an abstractive summary of the source
chunk. Pass 2 writes the question **from the summary alone** — the verbatim chunk
never enters the query-writing context. Both prompt templates are hashed into the
manifest (§8.1, "query generation" row), so a later reader can verify which
generation mode produced a fixture instead of taking the fixture's word for it.
It does not *remove* the bias — a summary still carries the chunk's entity names —
which is why (b) is also mandatory.

**(b) Measure the overlap and record it.** Every accepted query carries the
IDF-weighted term overlap against its source chunk plus the unweighted Jaccard
(:func:`_g1_rating.idf_overlap`), IDF computed over the *largest* rung's chunk
text so the covariate is comparable across rungs. The manifest carries the
distribution and the tertile edges, which is what makes §9's sensitivity analysis
— recompute the hybrid-vs-dense contrast on the lowest-overlap tertile — runnable
at analysis time rather than a promise.

Filters, per §4.2, with a machine-checkable form of each:

===================  =======================================================
``too_short`` /      fewer than 4 content terms, or over 60 words — not a
``too_long``         plausible researcher question either way
``not_a_question``   neither ends in "?" nor opens with an interrogative
``names_document``   "this paper", "the authors", "et al.", "Table 2" — §4.2's
                     "names a document explicitly", which also leaks the answer
``title_answerable`` ≥ 80% of the query's IDF mass is present in the source
                     document's *title* — §4.2's first discard rule, and the
                     known-item proxy in miniature
``duplicate``        same normalized text as an earlier accept
``critic_rejected``  the optional ``--critic`` pass judged it implausible
===================  =======================================================

The human path (``--source human``) exists because the operator asked for domain
experts to be able to supply queries directly, and because T1b's *only* clean
escape is a query nobody generated from the target text. It takes a spreadsheet
export (``.csv``/``.tsv``) or a JSONL dump with a ``text`` column, runs the same
filters, emits the same schema with ``source: "human"``, and records the author
ids in the manifest. A human query with no declared source chunk simply carries a
null overlap covariate — that is the honest value, and it is also the sub-population
T1b's sensitivity analysis most wants.

Outputs (all under ``--out``'s stem)::

    <out>.jsonl            accepted queries, the fixture
    <out>.rejected.jsonl   every discard with its reason — the accept rate is
                           itself a quality signal about the generator
    <out>.audit.csv        a seeded sample for §4.2's two-reviewer plausibility
                           audit, with blank verdict columns
    <out>.manifest.json    §8.1's query-generation provenance block

Usage::

    cd python && export PYTHONPATH="$PWD"

    # LLM path (paraphrase-first, the protocol's default mode)
    /rag/envs/ragstack/bin/python scripts/eval/g1_make_queries.py \\
        --source llm --chunks /rag/data/g1-corpus/chunks.p50.jsonl \\
        --idf-chunks /rag/data/g1-corpus/chunks.p200.jsonl \\
        --n 600 --llm-base-url http://localhost:9101 --llm-model <model> \\
        --out reports/g1-library-retrieval/fixtures/g1_pilot_p50_queries

    # Human path (same schema, same filters, different provenance)
    /rag/envs/ragstack/bin/python scripts/eval/g1_make_queries.py \\
        --source human --human-input expert_queries.csv \\
        --idf-chunks /rag/data/g1-corpus/chunks.p200.jsonl \\
        --out reports/g1-library-retrieval/fixtures/g1_pilot_expert_queries
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Protocol

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import _g1_rating as g1r  # noqa: E402

# --------------------------------------------------------------------------- #
# Prompts — hashed into the manifest (protocol §8.1, §9 T1b(a))
# --------------------------------------------------------------------------- #
PARAPHRASE_PROMPT = """\
You are helping build an evaluation set for a scientific search system.

Read the passage below and write a 2-3 sentence ABSTRACTIVE summary of what it
establishes. Rules:
- Use your own words. Do not copy phrases of more than three consecutive words.
- Keep the scientific content (organisms, mechanisms, measurements, conclusions).
- Do not mention "the passage", "the text", "the authors", or any document.
- Output only the summary.

PASSAGE:
{chunk}
"""

QUERY_PROMPT = """\
You are writing a realistic question that a working researcher might type into a
literature search tool.

Below is a short summary of a finding. Write ONE question that this finding would
answer. Rules:
- The question must stand on its own: a reader who has never seen the summary
  must be able to understand it.
- Do not refer to "this study", "the passage", "the authors", a figure, a table,
  or any document.
- Do not quote the summary. Ask about the underlying science, not about the text.
- One sentence, ending in a question mark. Output only the question.

SUMMARY:
{summary}
"""

CRITIC_PROMPT = """\
Is the following a plausible question for a working researcher to type into a
biomedical literature search tool?

Answer NO if it is self-referential ("what does this study show"), if it is
trivially general, if it is not a question, or if it could not be answered by a
scientific paper.

QUESTION: {query}

Answer with exactly one word: YES or NO.
"""

PARAPHRASE_PROMPT_SHA256 = g1r.sha256_text(PARAPHRASE_PROMPT)
QUERY_PROMPT_SHA256 = g1r.sha256_text(QUERY_PROMPT)
CRITIC_PROMPT_SHA256 = g1r.sha256_text(CRITIC_PROMPT)

# --------------------------------------------------------------------------- #
# Filters (protocol §4.2)
# --------------------------------------------------------------------------- #
MIN_CONTENT_TERMS = 4
MAX_WORDS = 60
TITLE_ANSWERABLE_THRESHOLD = 0.80

_DOC_REFERENCE_RE = re.compile(
    r"\b("
    r"this (?:paper|study|article|manuscript|work|passage|text|chapter|review|report)"
    r"|the (?:authors?|present study|current study|passage|excerpt)"
    r"|et al\.?"
    r"|(?:figure|fig\.|table|supplementary|appendix)\s*\d"
    r"|(?:described|shown|reported|discussed|mentioned)\s+(?:above|below|here)"
    r"|according to the (?:paper|text|passage|abstract|study)"
    r")\b",
    re.IGNORECASE,
)

_INTERROGATIVE_RE = re.compile(
    r"^\s*(what|which|how|why|when|where|who|whom|whose|does|do|did|is|are|was|were|can|"
    r"could|should|would|will|has|have|had|in what|to what|under what)\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Whitespace- and case-normalized form, for duplicate detection."""
    return " ".join(text.lower().split())


def screen_query(
    text: str,
    *,
    title: str | None = None,
    idf: dict[str, float] | None = None,
    seen: set[str] | None = None,
) -> str | None:
    """Return the discard reason for ``text``, or ``None`` if it is accepted.

    Order matters only for reporting: the *first* applicable reason is recorded,
    so the ``discarded`` histogram in the manifest reads as a funnel rather than
    as overlapping counts.
    """
    stripped = text.strip()
    if not stripped:
        return "empty"
    # Checked before the length screens on purpose: "What did this paper conclude?"
    # is *both* short and self-referential, and the second is the diagnostic one —
    # it says the generator (or the expert) misunderstood the task, which a
    # ``too_short`` count would hide.
    if _DOC_REFERENCE_RE.search(stripped):
        return "names_document"
    words = stripped.split()
    if len(words) > MAX_WORDS:
        return "too_long"
    if len(g1r.tokenize(stripped)) < MIN_CONTENT_TERMS:
        return "too_short"
    if not stripped.endswith("?") and not _INTERROGATIVE_RE.match(stripped):
        return "not_a_question"
    if title:
        # §4.2: "answerable from its source chunk's document title alone". The
        # machine-checkable form is IDF coverage of the query by the title —
        # exactly the known-item proxy §5.7 rejects, measured rather than assumed.
        cover = g1r.idf_overlap(stripped, title, idf or {})["idf_overlap"]
        if cover >= TITLE_ANSWERABLE_THRESHOLD:
            return "title_answerable"
    if seen is not None and normalize(stripped) in seen:
        return "duplicate"
    return None


# --------------------------------------------------------------------------- #
# Chunk loading
# --------------------------------------------------------------------------- #
def load_chunks(path: str | Path) -> list[dict[str, Any]]:
    """Normalize a chunk file into ``[{chunk_id, doc_id, text, title?}, …]``.

    Accepts the flat JSONL the sweep and the ingest scripts emit
    (``{chunk_id, doc_id, content|text}``) and the document-grouped JSON shape of
    ``scripts/example_chunks.json`` (``[{doc_id, metadata, chunks: [...]}]``),
    because both already exist in this repository and requiring a third would
    only add a conversion step nobody would keep in sync.
    """
    p = Path(path)
    raw = g1r.read_jsonl(p) if p.suffix.lower() == ".jsonl" else json.loads(
        p.read_text(encoding="utf-8")
    )
    if isinstance(raw, dict):
        raw = raw.get("documents") or raw.get("chunks") or []
    out: list[dict[str, Any]] = []
    for rec in raw:
        if "chunks" in rec and isinstance(rec["chunks"], list):  # grouped shape
            doc_id = str(rec.get("doc_id") or rec.get("id") or "")
            title = str((rec.get("metadata") or {}).get("title") or rec.get("title") or "")
            for i, ch in enumerate(rec["chunks"]):
                text = str(ch.get("text") or ch.get("content") or "")
                idx = ch.get("chunk_index", i)
                out.append(
                    {
                        "chunk_id": str(ch.get("chunk_id") or f"{doc_id}#{idx}"),
                        "doc_id": doc_id,
                        "text": text,
                        "title": title,
                    }
                )
            continue
        doc_id = str(rec.get("doc_id") or rec.get("document_id") or "")
        text = str(rec.get("text") or rec.get("content") or "")
        out.append(
            {
                "chunk_id": str(rec.get("chunk_id") or rec.get("id") or f"{doc_id}#{len(out)}"),
                "doc_id": doc_id,
                "text": text,
                "title": str(rec.get("title") or (rec.get("metadata") or {}).get("title") or ""),
            }
        )
    return [c for c in out if c["text"].strip()]


def load_titles(corpus_manifest: str | Path | None) -> dict[str, str]:
    """``doc_id → title`` from the corpus manifest (``/rag/data/g1-corpus/manifest.json``).

    The manifest keys documents by ``pmcid``; the chunk files key them by whatever
    the ingest used, so both the raw pmcid and the pdf stem are registered.
    """
    if not corpus_manifest:
        return {}
    data = json.loads(Path(corpus_manifest).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for doc in data.get("documents", []):
        title = str(doc.get("title") or "")
        for key in (doc.get("pmcid"), doc.get("doi"), doc.get("pmid")):
            if key:
                out[str(key)] = title
        f = doc.get("file")
        if f:
            out[Path(str(f)).stem] = title
    return out


# --------------------------------------------------------------------------- #
# The LLM path
# --------------------------------------------------------------------------- #
class ChatClient(Protocol):
    """The one method this script needs — ``ragstack.llm.OpenAILLM`` satisfies it,
    and so does a two-line stub in the unit tests."""

    async def complete_text(self, prompt: str, max_tokens: int = 512, temperature: float = 0.0) -> str: ...


async def generate_one(
    llm: ChatClient,
    chunk_text: str,
    *,
    temperature: float = 0.0,
    critic: bool = False,
) -> dict[str, Any]:
    """Two-pass paraphrase→question generation for one chunk (§9 T1b(a)).

    The invariant that makes this the *mitigated* mode rather than the naive one:
    ``chunk_text`` appears in the pass-1 prompt and nowhere else. Pass 2 sees only
    the model's own summary.
    """
    summary = (
        await llm.complete_text(
            PARAPHRASE_PROMPT.format(chunk=chunk_text), max_tokens=220, temperature=temperature
        )
    ).strip()
    query = (
        await llm.complete_text(
            QUERY_PROMPT.format(summary=summary), max_tokens=80, temperature=temperature
        )
    ).strip()
    query = query.strip().strip('"').strip()
    out: dict[str, Any] = {"summary": summary, "query": query, "critic": None}
    if critic and query:
        verdict = (
            await llm.complete_text(
                CRITIC_PROMPT.format(query=query), max_tokens=4, temperature=temperature
            )
        ).strip()
        out["critic"] = verdict.upper().startswith("Y")
    return out


async def generate_queries(
    llm: ChatClient,
    chunks: list[dict[str, Any]],
    *,
    n: int,
    seed: int = 0,
    temperature: float = 0.0,
    concurrency: int = 8,
    critic: bool = False,
    titles: dict[str, str] | None = None,
    idf: dict[str, float] | None = None,
    progress: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sample ``n`` chunks and generate one query each. Returns ``(accepted, rejected)``.

    Sampling is seeded and over-samples by 40%: filters discard, and a fixture
    short of its target is worse than a few unused generations (§7.4 makes the
    query count the cheapest lever on the pilot's power, so under-delivering here
    costs statistical resolution downstream).
    """
    rng = random.Random(seed)
    pool = sorted(chunks, key=lambda c: c["chunk_id"])
    rng.shuffle(pool)
    target_draw = min(len(pool), int(n * 1.4) + 8)
    sampled = pool[:target_draw]

    sem = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def _one(chunk: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        nonlocal done
        async with sem:
            try:
                res = await generate_one(llm, chunk["text"], temperature=temperature, critic=critic)
            except Exception as exc:  # noqa: BLE001 - one bad generation must not kill the run
                res = {"summary": "", "query": "", "critic": None, "error": f"{type(exc).__name__}: {exc}"}
            done += 1
            if progress and done % 25 == 0:
                print(f"[gen] {done}/{len(sampled)} chunks", flush=True)
            return chunk, res

    results = await asyncio.gather(*(_one(c) for c in sampled))

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    titles = titles or {}
    idf = idf or {}
    for chunk, res in results:
        if len(accepted) >= n:
            break
        query = res.get("query", "")
        title = titles.get(chunk["doc_id"]) or chunk.get("title") or None
        reason = screen_query(query, title=title, idf=idf, seen=seen)
        if reason is None and res.get("critic") is False:
            reason = "critic_rejected"
        base = {
            "source_chunk_id": chunk["chunk_id"],
            "source_doc_id": chunk["doc_id"],
            "text": query,
        }
        if reason is not None:
            rejected.append({**base, "reason": reason, "error": res.get("error")})
            continue
        seen.add(normalize(query))
        cov = g1r.idf_overlap(query, chunk["text"], idf)
        accepted.append(
            {
                **base,
                "source": "llm",
                "covariates": cov,
                # The summary is retained because it is the *only* evidence that
                # the query was written from a paraphrase rather than the chunk —
                # T1b(a) is an unverifiable claim without it.
                "paraphrase": res.get("summary", ""),
            }
        )
    return accepted, rejected


# --------------------------------------------------------------------------- #
# The human path
# --------------------------------------------------------------------------- #
HUMAN_INPUT_COLUMNS = ("text", "author_id", "source_doc_id", "source_chunk_id", "notes")


def ingest_human_queries(
    rows: list[dict[str, Any]],
    *,
    chunks_by_id: dict[str, dict[str, Any]] | None = None,
    titles: dict[str, str] | None = None,
    idf: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize and screen expert-written queries. Returns ``(accepted, rejected)``.

    Input columns (SOP §3.2): ``text`` (required), ``author_id``,
    ``source_doc_id``, ``source_chunk_id``, ``notes``. The last three are optional
    — a genuinely independent question has no source chunk, and that is the point
    of this path — but when a source *is* declared the query goes through the same
    ``title_answerable`` screen and the same overlap covariate as an LLM query, so
    the two sources are comparable in the analysis rather than merely coexisting.
    """
    chunks_by_id = chunks_by_id or {}
    titles = titles or {}
    idf = idf or {}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        text = str(row.get("text") or row.get("query") or "").strip()
        doc_id = str(row.get("source_doc_id") or "").strip()
        chunk_id = str(row.get("source_chunk_id") or "").strip()
        title = titles.get(doc_id) if doc_id else None
        reason = screen_query(text, title=title, idf=idf, seen=seen)
        base = {
            "text": text,
            "source_doc_id": doc_id or None,
            "source_chunk_id": chunk_id or None,
            "author_id": str(row.get("author_id") or "").strip() or None,
            "notes": str(row.get("notes") or "").strip() or None,
        }
        if reason is not None:
            rejected.append({**base, "reason": reason})
            continue
        seen.add(normalize(text))
        src = chunks_by_id.get(chunk_id)
        cov = g1r.idf_overlap(text, src["text"], idf) if src else None
        accepted.append({**base, "source": "human", "covariates": cov})
    return accepted, rejected


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def finalize(records: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Stamp query ids and pin the common schema — LLM and human alike.

    One schema for both sources is a hard requirement: the pool, the assignment
    files and the agreement statistics all key on ``query_id``, and a second
    shape would fork every one of them.
    """
    out: list[dict[str, Any]] = []
    for i, rec in enumerate(records, start=1):
        text = rec["text"]
        out.append(
            {
                "query_id": g1r.query_id_for(text, source, i),
                "text": text,
                "source": rec.get("source", source),
                "source_doc_id": rec.get("source_doc_id"),
                "source_chunk_id": rec.get("source_chunk_id"),
                "author_id": rec.get("author_id"),
                "covariates": rec.get("covariates"),
                "paraphrase": rec.get("paraphrase"),
                "notes": rec.get("notes"),
            }
        )
    return out


def audit_sample(queries: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """§4.2's plausibility audit: a seeded sample for two independent reviewers."""
    rng = random.Random(seed + 977)
    pool = sorted(queries, key=lambda q: q["query_id"])
    rng.shuffle(pool)
    return pool[: min(n, len(pool))]


def write_audit_csv(path: Path, sample: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["query_id", "text", "reviewer_a_plausible", "reviewer_b_plausible", "notes"])
        for q in sample:
            w.writerow([q["query_id"], q["text"], "", "", ""])


def build_manifest(
    *,
    args: argparse.Namespace,
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    n_source_chunks: int,
    idf_terms: int,
) -> dict[str, Any]:
    discarded: dict[str, int] = {}
    for rec in rejected:
        discarded[rec["reason"]] = discarded.get(rec["reason"], 0) + 1
    overlaps = [
        q["covariates"]["idf_overlap"] for q in accepted if isinstance(q.get("covariates"), dict)
    ]
    jaccard = [
        q["covariates"]["jaccard"] for q in accepted if isinstance(q.get("covariates"), dict)
    ]
    man = g1r.manifest_header("g1_make_queries")
    man["query_generation"] = {
        "applies": True,
        "source": args.source,
        "generator_model": args.llm_model if args.source == "llm" else None,
        "generator_base_url": args.llm_base_url if args.source == "llm" else None,
        "generation_mode": "paraphrase_first" if args.source == "llm" else "human_written",
        "paraphrase_prompt_sha256": PARAPHRASE_PROMPT_SHA256 if args.source == "llm" else None,
        "query_prompt_sha256": QUERY_PROMPT_SHA256 if args.source == "llm" else None,
        "critic_prompt_sha256": CRITIC_PROMPT_SHA256 if (args.source == "llm" and args.critic) else None,
        "temperature": args.temperature if args.source == "llm" else None,
        "seed": args.seed,
        "authors": sorted({q["author_id"] for q in accepted if q.get("author_id")}),
        "n_source_chunks": n_source_chunks,
        "n_requested": args.n,
        "n_generated": len(accepted) + len(rejected),
        "n_accepted": len(accepted),
        "discarded": discarded,
        # T1b(b): the covariate distribution *is* the deliverable here — a fixture
        # whose overlap distribution is not recorded cannot support §9's
        # lowest-tertile sensitivity analysis.
        "idf_overlap": g1r.distribution(overlaps),
        "jaccard": g1r.distribution(jaccard),
        "idf_source": str(args.idf_chunks or args.chunks or ""),
        "idf_vocabulary_terms": idf_terms,
    }
    man["inputs"] = {
        "chunks": str(args.chunks or ""),
        "chunks_sha256": g1r.sha256_file(args.chunks) if args.chunks else None,
        "human_input": str(args.human_input or ""),
        "human_input_sha256": g1r.sha256_file(args.human_input) if args.human_input else None,
        "corpus_manifest": str(args.corpus_manifest or ""),
        "corpus_manifest_sha256": (
            g1r.sha256_file(args.corpus_manifest) if args.corpus_manifest else None
        ),
    }
    return man


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate or ingest G1 pilot queries (protocol §4.2, threat T1b).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source", choices=("llm", "human"), required=True)
    p.add_argument("--chunks", help="chunk file to generate from (JSONL or grouped JSON)")
    p.add_argument(
        "--idf-chunks",
        help="chunk file the IDF table is built from; §9 wants the LARGEST rung "
        "(P200) so the covariate is comparable across rungs. Defaults to --chunks.",
    )
    p.add_argument("--corpus-manifest", help="/rag/data/g1-corpus/manifest.json, for doc titles")
    p.add_argument("--human-input", help="expert-written queries (.csv/.tsv/.jsonl/.json)")
    p.add_argument("--n", type=int, default=600, help="accepted queries to produce")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--llm-base-url", default="http://localhost:8080")
    p.add_argument("--llm-model", default="")
    p.add_argument("--llm-api-key", default=None)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--critic", action="store_true", help="run the third-pass plausibility critic")
    p.add_argument("--audit-n", type=int, default=100, help="§4.2 two-reviewer audit sample size")
    p.add_argument("--out", required=True, help="output stem (no extension)")
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    titles = load_titles(args.corpus_manifest)
    chunks = load_chunks(args.chunks) if args.chunks else []
    idf_path = args.idf_chunks or args.chunks
    idf_chunks = load_chunks(idf_path) if idf_path else []
    idf = g1r.idf_table(c["text"] for c in idf_chunks)
    print(f"[idf] {len(idf)} terms over {len(idf_chunks)} chunks from {idf_path}", flush=True)

    if args.source == "llm":
        if not chunks:
            raise SystemExit("--source llm needs --chunks")
        if not args.llm_model:
            raise SystemExit("--source llm needs --llm-model")
        import httpx

        from ragstack.llm import OpenAILLM

        async with httpx.AsyncClient(timeout=180.0) as http:
            llm = OpenAILLM(args.llm_base_url, args.llm_model, http, api_key=args.llm_api_key)
            accepted, rejected = await generate_queries(
                llm,
                chunks,
                n=args.n,
                seed=args.seed,
                temperature=args.temperature,
                concurrency=args.concurrency,
                critic=args.critic,
                titles=titles,
                idf=idf,
            )
    else:
        if not args.human_input:
            raise SystemExit("--source human needs --human-input")
        rows = g1r.read_table(args.human_input)
        accepted, rejected = ingest_human_queries(
            rows,
            chunks_by_id={c["chunk_id"]: c for c in chunks},
            titles=titles,
            idf=idf,
        )

    queries = finalize(accepted, args.source)
    stem = Path(args.out)
    g1r.write_jsonl(stem.with_suffix(".jsonl"), queries)
    g1r.write_jsonl(stem.with_suffix(".rejected.jsonl"), rejected)
    write_audit_csv(stem.with_suffix(".audit.csv"), audit_sample(queries, args.audit_n, args.seed))
    man = build_manifest(
        args=args,
        accepted=queries,
        rejected=rejected,
        n_source_chunks=len(chunks),
        idf_terms=len(idf),
    )
    man["outputs"] = {
        "queries": str(stem.with_suffix(".jsonl")),
        "queries_sha256": g1r.sha256_file(stem.with_suffix(".jsonl")),
    }
    g1r.write_json(stem.with_suffix(".manifest.json"), man)

    qg = man["query_generation"]
    print(
        f"\n[done] {len(queries)} accepted / {qg['n_generated']} generated "
        f"({args.source}); discarded={qg['discarded'] or '{}'}"
    )
    print(f"[t1b]  idf_overlap {qg['idf_overlap']}")
    print(f"[out]  {stem.with_suffix('.jsonl')}")
    if len(queries) < args.n:
        print(
            f"[warn] {len(queries)} < requested {args.n}: the confirm split will be "
            f"smaller than §7.4's power budget assumes. Re-run with more source chunks.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
