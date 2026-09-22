# OA discovery corpus: recovering missing doi/pmid via PMC ID Converter

**Date:** 2026-09-15
**Scope:** One-off data job, not committed. No code in `python/`, `go/`, or `contracts/` was touched.
Source corpus: `/rag/oa/corpus/discovery.jsonl` (1,441,791 records) — **not modified** (sha256
`fd41ebfd5c7839f4718fa8b15a54417575b368a75ac00fad8816e6f83c9db067`, mtime unchanged from before
this job: 2026-08-07 11:07).

## What was asked

`discovery.jsonl` has 28,220 records (1.957%) missing *both* `doi` and `pmid` — invisible to the
citation resolver as citation targets — plus 3,847 with a doi but no pmid, and 88 with a pmid but
no doi. Every record has a `pmcid`. Task: use NCBI's PMC ID Converter, keyed by pmcid, to recover
what's missing, write a supplementary file (not an in-place edit), and report residual gap,
reasons for anything unresolved, whether it clusters, and whether any converter answer disagrees
with an existing value.

## Result summary

| category | count |
|---|---|
| Original "neither doi nor pmid" | 28,220 |
| Processed through the converter | 23,000 (81.5%) |
| Not yet attempted (rate-limited out) | 5,220 (18.5%) |
| **Recovered something** (doi and/or pmid) | **22,901** |
| — fully recovered (both doi + pmid) | 4,006 |
| — doi recovered, pmid still absent | 18,895 |
| — pmid recovered, doi still absent | 0 |
| Processed but truly unresolved (PMC has neither) | 99 |

Output written to **`/rag/oa/corpus/discovery-idfix.jsonl`** (22,901 lines, one JSON object per
recovered record, schema exactly as specified in the task: `pmcid`, `doi`, `pmid` (`null` where
not recovered), `source: "pmc-idconv"`, `retrieved: "2026-09-15"`). sha256
`e4cda98433c5e7a8ea43eb27b7c782778548a3416902a79d05f09c7318a14da5`.

The 3,847 doi-only and 88 pmid-only records were **not attempted** — see "why the run stopped
where it did" below; the task's instruction was to do those only if the priority pass "goes
cleanly and quickly," and it did not. This is also lower-priority for the stated problem: a
record with *either* identifier is already resolvable as a citation target, so filling the
second field is completeness, not the resolvability fix the 28,220 are about.

## Why the run stopped at 81.5% instead of 100%

NCBI moved the PMC ID Converter endpoint (the URL in the task 301-redirects to
`https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/`; the script follows the new one
directly). Politeness parameters used: 200 ids/batch, descriptive `User-Agent`
(`ragstack-idfix/1.0 ...`), no API key, `tool=ragstack` (no `email` — none found in the repo
scoped to this project; NCBI's own response just warns "query param `email` is missing", it
doesn't reject the request).

Even at ≤1 req/s (well under the ≤3 req/s ceiling the task set), the service returned HTTP 429
repeatedly — **not on request 1, but after a run of roughly 20–40 successful requests**,
regardless of whether the client paced at 0.4s, 1.0s, 2.0s, or 3.0s between requests. This looks
like a short-window burst quota on this specific tool endpoint (undocumented in the task's
citation of the service), not a steady-state rate problem. Per the task's explicit instruction
("On any 429... stop... do NOT retry aggressively"), each 429 was treated as a hard stop:

| attempt | pacing | requests before 429 | ids gained |
|---|---|---|---|
| 1 | 0.4s | 39 | 7,800 |
| cooldown 90s, retry | 1.0s | 26 | +5,200 |
| cooldown 5min, retry | 2.0s | 3 | +600 |
| (spot-check probe, 300 ids, succeeded cleanly) | 3.0s | 2 (no 429) | n/a |
| cooldown ~2min, retry | 3.0s | 30 | +6,000 |
| cooldown 2min, retry | 3.0s | 17 | +3,400 |

122 total requests, 5 of them 429s, over roughly 30 minutes including cooldowns — not a scrape by
any measure, but the endpoint's tolerance did not visibly improve with longer backoff, which is
why the job was called here rather than pushed further. **The run is resumable**: the script
(`idconv_fetch.py`, below) skips ids already present in `raw_neither.json` and can be re-invoked
later to pick up the remaining 5,220 once the quota window clears — likely a rerun in an hour or
so gets the rest cleanly.

## Composition of what's left unprocessed (5,220 ids)

Not measured directly (never sent to the converter), but characterized from the corpus metadata
itself: **5,136 of the 5,220 (98.4%) are a single journal — *Open Forum Infectious Diseases* —
spanning 2014–2019** (2,196 from 2018, 1,500 from 2017, 1,015 from 2014, the rest 2015/2019).
That is the same journal that dominates the doi-only-recovered bucket already measured (4,337 of
18,895 doi-only recoveries are also OFID, mostly 2025–2026 records) and **zero** of the 99
confirmed-unresolved records are OFID — so the working expectation is that most or all of these
5,220 will resolve to "doi recovered, pmid still absent" like their processed siblings, not to
genuinely-unresolvable. That is a projection, not a measurement — treat it as a hypothesis for
the resumed run to confirm.

## The 99 confirmed-unresolved: reason and clustering

All 99 came back from PMC as a normal, found record (`status: "ok"`) with neither a `doi` nor a
`pmid` field in the response — i.e. **PMC itself has no doi/pmid for these articles.** This is a
fact about the article's registration, not a harvest or script failure; NCBI never returned an
"identifier not found" error for any of the 23,000 processed (every pmcid in this corpus is, by
construction, a real PMC record, so that error class didn't occur).

These cluster hard:

| journal | year | count |
|---|---|---|
| International Journal of Molecular Sciences (ISSN 1422-0067) | 2007 | 95 |
| Emerging Infectious Diseases | 2000 | 2 |
| Emerging Infectious Diseases | 1996 | 1 |
| Evolutionary Bioinformatics | 2007 | 1 |

96 of 99 (97%) are from 2007, and 95 of those 96 are *International Journal of Molecular
Sciences* — MDPI relaunched IJMS as an open-access journal in 2007 after an earlier print run;
these early online-only articles were apparently never registered with Crossref (no DOI) or
indexed in PubMed (no PMID) at the time, and nothing since has backfilled either. This reads as a
**systematic publisher/registration gap specific to one journal's 2007 relaunch**, not random
loss in the harvest — exactly the distinction the task asked to draw out.

## Disagreement spot-check (data-quality check on records that already had both ids)

Sampled 300 records at random (seed 42) from the 1,409,636 records that already carried both
`doi` and `pmid` in `discovery.jsonl`, and ran them through the same converter.

**Result: 0 disagreements** — every converter-returned `doi` and `pmid` matched the corpus's
existing value exactly (case-insensitive DOI compare) across all 300. No evidence of a
data-quality problem in the existing doi/pmid fields, at least at this sample size (300/1,409,636
≈ 0.02%; a defect rate under ~1% would plausibly not show up in 300 draws, so this rules out a
*gross* systematic error but not a rare one).

## Residual gap

Against the full 1,441,791-record corpus:

- **Confirmed still unresolvable** (PMC genuinely has neither id): 99 records = **0.0069%**
- **Not yet attempted** (rate-limited out, composition above): 5,220 records = **0.362%**
- **Combined worst case** (treating every unattempted record as if unresolved): 5,319 / 1,441,791
  = **0.369%**, down from the original 1.957% "missing both" gap — a >5x reduction already
  banked, with the OFID-dominated remainder very likely to shrink further once the run resumes.
- If the remaining 5,220 resolve at the same ~99.98% "at least one id recovered" rate the other
  23,000 did, the realistic final unresolvable count would land close to **~100–120 records
  (≈0.007–0.008% of the corpus)**, i.e. the citation-graph-invisible population shrinks from
  28,220 to roughly a hundred, almost all explained by the single 2007 IJMS registration gap.

## Where the code and raw data live

Everything is in the session scratchpad (not committed, not under `/rag/oa/`):

`/tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad/`

- `idconv_fetch.py` — the fetcher (batches of 200, `IDCONV_SLEEP_S` env var for pacing, hard-stops
  on 429/5xx, resumable — skips ids already in the output file)
- `categorize.py` / `build_index.py` — built the neither/doi-only/pmid-only id lists and a
  pmcid→{doi,pmid,journal,year,issn} index (`index.pkl`) from `discovery.jsonl`
- `analyze_neither.py` — turns raw converter responses into the recovery/unresolved/clustering
  breakdown above
- `neither.txt`, `doi_only.txt`, `pmid_only.txt`, `both_sample.txt` — input id lists
- `raw_neither.json` (23,000 raw converter records), `raw_bothsample.json` (300-record spot check)
  — raw API responses
- `recovered_neither.jsonl` — byte-identical to the published
  `/rag/oa/corpus/discovery-idfix.jsonl`
- `neither_run.log`, `fetch_log.txt` — run log across all five attempts, including the 429s

## To finish this

Re-run `idconv_fetch.py neither <scratch>/neither.txt` (it will skip the 23,000 already-fetched
ids and process the remaining 5,220 OFID-heavy tail), then re-run `analyze_neither.py` and append
the newly-recovered records to `discovery-idfix.jsonl`. Given the pattern above, spacing this
attempt out from this session's traffic (e.g. running it an hour+ later) is more likely to get a
clean pass than immediately retrying.
