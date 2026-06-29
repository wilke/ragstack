#!/usr/bin/env python
"""Verify recovered DOIs and titles against PubMed (NCBI E-utilities).

A data-quality check for the metadata recovered by
:mod:`ragstack.ingestion.enrich`: it samples ingested documents and, for each,
asks PubMed two questions via the public E-utilities API:

1. **Does the DOI resolve?** ``esearch`` for ``<doi>[AID]`` — a hit means the
   DOI we derived (usually from the filename) is a real, indexed identifier.
2. **Does the title agree?** When both we and PubMed have a title, compare them
   (normalized, ``difflib`` ratio). Catches a DOI that resolves to the *wrong*
   article — i.e. a mis-derived identifier that happens to be valid.

Input is either the catalog emitted by ``ingest_jsonl.py --catalog-out`` or the
raw corpus JSONL (records are enriched on the fly); the format is auto-detected
per line. Only records with a non-empty DOI are eligible.

NCBI asks unauthenticated clients to stay under 3 requests/second and to send a
tool/email identifier; an API key (``--api-key`` or ``$NCBI_API_KEY``) raises
the limit to 10/s. This tool self-throttles accordingly. It is **read-only** —
it issues GET requests to a public API and writes nothing back.

Usage::

    . /rag/bin/activate
    cd python
    python scripts/verify_doi_pubmed.py /rag/inputs/<file>.catalog.jsonl \\
        --sample 100 --email you@example.org

    # straight from the corpus, deterministic sample, stricter title threshold:
    python scripts/verify_doi_pubmed.py /rag/inputs/<file>.jsonl \\
        --sample 50 --seed 7 --min-ratio 0.8 --report /tmp/doi_report.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from ragstack.ingestion.enrich import enrich

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _normalize_title(t: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for fuzzy comparison."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t.lower())).strip()


def _title_ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _candidates(path: Path) -> list[dict[str, str]]:
    """Read the input (catalog or corpus) into ``[{doi, title}]`` with a DOI."""
    out: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "doc_type" in obj and "doi" in obj:  # already-enriched catalog row
                doi, title = obj.get("doi", ""), obj.get("title", "")
            else:  # raw corpus record
                e = enrich(obj)
                doi, title = e.doi, e.title
            if doi:
                out.append({"doi": doi, "title": title or ""})
    return out


class _PubMedClient:
    """Minimal, self-throttling NCBI E-utilities client."""

    def __init__(self, http: httpx.AsyncClient, *, email: str | None,
                 api_key: str | None) -> None:
        self._http = http
        self._common = {"db": "pubmed", "retmode": "json", "tool": "ragstack-doi-verify"}
        if email:
            self._common["email"] = email
        if api_key:
            self._common["api_key"] = api_key
        # Stay safely under NCBI's documented rate limits.
        self._min_interval = 0.11 if api_key else 0.34
        self._last = 0.0

    async def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def _get(self, endpoint: str, params: dict[str, str], *, retries: int = 3) -> dict:
        """Throttled GET with backoff on NCBI's frequent 429/5xx throttling."""
        for attempt in range(retries + 1):
            await self._throttle()
            try:
                r = await self._http.get(f"{_EUTILS}/{endpoint}", params=params, timeout=30.0)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError:
                if attempt == retries:
                    raise
                await asyncio.sleep(0.5 * (2 ** attempt))  # 0.5s, 1s, 2s
        return {}  # unreachable

    async def pmid_for_doi(self, doi: str) -> str | None:
        data = await self._get("esearch.fcgi", {**self._common, "term": f"{doi}[AID]"})
        ids = data.get("esearchresult", {}).get("idlist", [])
        return ids[0] if ids else None

    async def title_for_pmid(self, pmid: str) -> str:
        data = await self._get("esummary.fcgi", {**self._common, "id": pmid})
        return data.get("result", {}).get(pmid, {}).get("title", "") or ""


async def run(args: argparse.Namespace) -> int:
    cands = _candidates(args.input)
    if not cands:
        print("no records with a DOI to verify", file=sys.stderr)
        return 1
    rng = random.Random(args.seed)
    sample = cands if args.sample <= 0 or args.sample >= len(cands) else rng.sample(cands, args.sample)
    print(f"verifying {len(sample)} of {len(cands)} DOIs against PubMed", file=sys.stderr)

    api_key = args.api_key or os.getenv("NCBI_API_KEY")
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as http:
        client = _PubMedClient(http, email=args.email, api_key=api_key)
        for i, c in enumerate(sample, 1):
            row: dict[str, Any] = {"doi": c["doi"], "our_title": c["title"]}
            try:
                pmid = await client.pmid_for_doi(c["doi"])
                row["resolved"] = pmid is not None
                row["pmid"] = pmid
                if pmid and c["title"]:
                    pm_title = await client.title_for_pmid(pmid)
                    row["pubmed_title"] = pm_title
                    row["title_ratio"] = round(_title_ratio(c["title"], pm_title), 3)
            except httpx.HTTPError as e:
                row["error"] = type(e).__name__
            results.append(row)
            if i % 25 == 0:
                print(f"  {i}/{len(sample)}", file=sys.stderr)

    # --- summarize -----------------------------------------------------------
    resolved = [r for r in results if r.get("resolved")]
    errored = [r for r in results if "error" in r]
    title_checked = [r for r in results if "title_ratio" in r]
    title_ok = [r for r in title_checked if r["title_ratio"] >= args.min_ratio]
    mismatches = [r for r in title_checked if r["title_ratio"] < args.min_ratio]

    n = len(results)
    print("\n=== PubMed DOI/title verification ===")
    print(f"sampled:        {n}")
    print(f"DOI resolved:   {len(resolved)}/{n}  ({100 * len(resolved) / n:.1f}%)")
    if errored:
        print(f"request errors: {len(errored)}")
    if title_checked:
        print(f"title checked:  {len(title_checked)} (had a title on both sides)")
        print(f"title match:    {len(title_ok)}/{len(title_checked)}  "
              f"({100 * len(title_ok) / len(title_checked):.1f}%, ratio>={args.min_ratio})")
    else:
        print("title checked:  0 (our records mostly lack titles — DOI resolution is the signal)")
    if mismatches:
        print(f"\ntitle mismatches (showing up to 10 of {len(mismatches)}):")
        for r in mismatches[:10]:
            print(f"  [{r['title_ratio']}] {r['doi']}")
            print(f"        ours:   {r['our_title'][:80]}")
            print(f"        pubmed: {r.get('pubmed_title', '')[:80]}")
    unresolved = [r for r in results if not r.get("resolved") and "error" not in r]
    if unresolved:
        print(f"\nunresolved DOIs (showing up to 10 of {len(unresolved)}):")
        for r in unresolved[:10]:
            print(f"  {r['doi']}")

    if args.report:
        args.report.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results))
        print(f"\nper-record report written to {args.report}", file=sys.stderr)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("input", type=Path, help="catalog JSONL (from ingest_jsonl --catalog-out) or raw corpus JSONL")
    p.add_argument("--sample", type=int, default=100, help="number of DOIs to check (0 = all)")
    p.add_argument("--seed", type=int, default=0, help="RNG seed for a reproducible sample")
    p.add_argument("--min-ratio", type=float, default=0.7,
                   help="title similarity threshold counted as a match (0..1)")
    p.add_argument("--email", default=None, help="contact email sent to NCBI (etiquette)")
    p.add_argument("--api-key", default=None, help="NCBI API key (or set $NCBI_API_KEY) to raise the rate limit")
    p.add_argument("--report", type=Path, default=None, help="write per-record results as JSONL here")
    args = p.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
