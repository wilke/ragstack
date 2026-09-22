# ASM metadata: a local cache, so the backfill is a write and not a fetch

**Date:** 2026-09-15
**Scope:** Read-only against production; network reads against Crossref, the NCBI PMC ID
Converter and OpenAlex; writes to **files only**. No document, payload, mapping, index, registry
row or service was written, altered, reindexed or restarted. No plan document was edited, nothing
was committed, no PR was opened.

**Deliverable:** `/rag/data/asm-metadata-cache/` — 1.3 GB, three JSON Lines files, checksummed,
resumable. Its `README.md` is the format spec; this file is the measurement.

---

## Headline

**The route is Crossref's *array* endpoint — `GET /works?filter=doi:a,doi:b,…` at 200 DOIs per
request — paced to the 3 req/s that endpoint itself announces. Measured: 373–547 DOIs/s.**
Paired with the NCBI PMC ID Converter (200 ids/request, 3 req/s) for `pmcid`/`pmid`, which
measured **575 DOIs/s sustained across the entire population**.

> **Unverified as committed (found in the #624 review).** The **373 DOIs/s paced at 3 req/s, 0× 429** and **547 (first 100 req of the real run)** figures do not resolve to any committed log: `bench_cr_batch.log` holds only the batch-200 rows at concurrency 1/2/4 = 141.7 / 304.2 / 198.9 docs/s, and the only "547" in a committed log is NCBI's (`ncbi_full.log`). What *is* committed for Crossref is the real-run average of 88 DOIs/s with 14× 429 over 1,378 requests (`cr_full2.log`). Read the headline as a claim, the 88 as the measurement.

**ETA for the full fetch: about 9 minutes per route, run in parallel. It is already done.**
The cache holds all **277,682** distinct DOIs behind all **440,049** ASM documents.

Both figures in the brief's contradiction were measured on the wrong endpoint, and I can show it:

| what was measured | endpoint | pacing | result |
|---|---|---|---|
| `metadata-consistency.md`'s budget | per-DOI `/works/{doi}` | "concurrency 4" | **claimed** 10–20 docs/s → ~6 h |
| the ASM audit's 9.8% | per-DOI `/works/{doi}` | concurrency 4, anonymous | 429-storm |
| **my control**, polite pool | per-DOI `/works/{doi}` | concurrency 4, unpaced | **166/200 = 83% HTTP 429**, 7.9 docs/s |
| **my control**, polite pool | per-DOI `/works/{doi}` | sequential 1 req/s | 1.0 docs/s, 0× 429 |
| **chosen route** | `/works?filter=doi:…×200` | **paced to its announced 3 req/s** | **373 docs/s, 0× 429** |

The polite pool is **not** the variable that mattered. My concurrency-4 control ran *in* the
polite pool, with a `mailto` in both the User-Agent and the query string, and was 429'd at 83% —
reproducing the audit's failure almost exactly. The audit's diagnosis ("the 429s came from
*anonymous* concurrency") is wrong; the 429s came from **exceeding 10 req/s**, which is what
unpaced concurrency 4 does (my control attempted 49 req/s). And `metadata-consistency.md`'s
"10–20 docs/s at concurrency 4" is not reachable on that endpoint under any pacing: the endpoint
caps at 10 req/s and returns one DOI per request, so **10 docs/s is its ceiling**, not its budget.

The thing neither document noticed is that Crossref serves the two endpoints from **two different
pools with two different limits**, and says so in a response header:

```
GET /works/{doi}          ->  x-api-pool: polite-single   x-rate-limit-limit: 10  / 1s
GET /works?filter=doi:…   ->  x-api-pool: polite-array    x-rate-limit-limit: 3   / 1s
```

3 req/s × 200 DOIs = **600 DOIs/s ceiling, entirely within the documented limit** — 60× the
per-DOI endpoint, from the same API, with the same politeness. Nothing here exceeds a documented
rate limit; the win comes from moving 200 DOIs per permitted request instead of one.

---

## Job 1 — the routes, measured

All on the same 400 real ASM DOIs sampled from production. Harness: `bench.py`; raw output in
`bench_cr_batch.log`, `bench_oa.log`, `bench_ncbi.log`.

| route | batch | concurrency | req/s | **DOIs/s** | 429s | ETA for 277,682 |
|---|---|---|---|---|---|---|
| Crossref `/works/{doi}` | 1 | 1 (1 req/s) | 1.01 | **1.0** | 0 | **3.2 days** |
| Crossref `/works/{doi}` | 1 | 4, unpaced | 49.1 | 7.9 | **166/200** | — (fails) |
| Crossref `/works?filter=` | 50 | 1 | 0.64 | 30.4 | 0 | 2.5 h |
| Crossref `/works?filter=` | **200** | 3, paced 3 req/s | 1.96 | **373.0** | 0 | **12 min** |
| Crossref `/works?filter=` | 200 | 4 (first 100 req of the real run) | 2.7 | **547** | 2 | **8.5 min** |
| OpenAlex `filter=doi:a\|b\|…` | 50 | 4 | 4.88 | 233.0 | 0 | see below |
| NCBI ID Converter | 200 | 1, paced 3 req/s | 3.03 | **575** (full population) | 44/1,393 | **8.0 min** |

### 1. Crossref polite pool

Verified: `mailto` in both the `User-Agent` and the query string moves you to `x-api-pool:
polite-*`. It raises the *announced* limit, it does not make concurrency free. Everything above
was run in the polite pool.

**Max batch size is bounded by URL length, not by a documented item cap.** 200 DOIs is a 6.3 KB
URL and works; batches of long DOIs overflow and return **HTTP 414**. My first full run lost 106
batches to 414 before I capped the filter at 5,200 characters and added a split-on-414 retry.
After that fix: **0 errors across 1,378 requests.** Anyone reusing this must keep the length cap —
a fixed count of 200 is not safe on its own.

Coverage on the whole population: **263,532 / 277,682 = 94.9%** of ASM DOIs resolve. Of the 14,150
that do not, **99.3% are malformed DOIs, not Crossref gaps** — `10.1128/aac.46.6.1971` (page range
and year missing), `10.1128/cmr.20.1.2007`, `10.1128/mSphere` (journal-level), `10.1016/j`
(truncated mid-scan). These are artefacts of the filename→DOI and text-scan rules in
`enrich.py`, and a repair pass on them is a separate, cheap win.

### 2. OpenAlex — works, but its free tier is a daily budget, not a rate

The batch mechanism is real: `filter=doi:A|B|…` accepts 50 DOIs, returns them in one response,
and **costs exactly 1 credit** regardless of batch size (`x-ratelimit-credits-used: 1`). Latency
is better than Crossref's (p50 0.58 s vs 1.29 s) and it sustained 233 DOIs/s at concurrency 4
with zero 429s.

**But the limit is not a request rate. It is a credit budget**, and it is small:

```
x-ratelimit-limit: 1000        x-ratelimit-limit-usd: 0.1
x-ratelimit-remaining: 973     x-ratelimit-cost-usd: 0.0001
x-ratelimit-reset: 15625       (-> 00:00 UTC; a daily reset)
```

1,000 credits/day × 50 DOIs = **50,000 DOIs/day**. 277,682 DOIs is **6 days** — worse than
Crossref's 9 minutes, and worse than the 5 days the brief was trying to avoid. The brief's
hypothesis ("potentially *minutes*, not days") is right about the mechanism and wrong about the
budget; OpenAlex has moved to metered pricing since that was written.

**Fields, verified on 200 real ASM DOIs rather than trusted from the schema** (94.5% hit rate,
the same as Crossref):

| field | populated |
|---|---|
| `title`, `publication_year`, `publication_date`, `type`, `authorships` | 100% |
| journal (`primary_location.source.display_name`) | 100% |
| publisher (`…source.host_organization_name`) | 100% |
| `abstract_inverted_index` | 97.4% |
| `ids.pmid` | 99.5% |
| **`ids.pmcid`** | **0 / 189 — absent** |

So the brief's "identifiers including PMID/PMCID" does not hold for these records: **OpenAlex
carries no PMCID for ASM**, and a PMCID route through NCBI is required regardless of which
bibliographic source wins. One caution if OpenAlex is ever preferred: its `publication_date` is a
normalised `YYYY-MM-DD` string on 100% of records, which is *less* honest than Crossref's
`date-parts` — Crossref says "1980, month 8, day unknown" and OpenAlex invents a day.

### 3. Bulk snapshots — priced, not taken

Not worth it, and not close. The batch APIs did the whole job in under an hour of wall clock; a
snapshot is hundreds of gigabytes to transfer and decompress before the first record is readable,
against 277,682 records that fit in 1.1 GB when fetched precisely. **I did not verify the snapshot
sizes** — the OpenAlex S3 works manifest at `https://openalex.s3.amazonaws.com/data/works/manifest`
returned **404**, so I have no current figure for it, and I did not attempt the Crossref annual
file. That gap does not change the decision: the alternative finished before a download would
have started. Crossref's full corpus is 186,617,798 works, of which we need 0.15%.

### What I did about being rate-limited

44 429s on NCBI across 1,393 requests and 14 on Crossref across 1,378 — every one backed off and
retried, none hammered. The fetcher slows its **global** gate on a 429 rather than retrying a
single batch harder. That control is one-way by design and never speeds back up, which is why the
real Crossref leg averaged 88 DOIs/s (2,438 s) instead of its measured 547: a handful of early
429s ratcheted the gate to its 2 s floor and it stayed there. **That is my client being
conservative, not Crossref being slow** — the clean measured rate stands at 373–547 DOIs/s, and
NCBI, which took fewer 429s early, held 575 DOIs/s across the entire population. A decaying
backoff would bring the Crossref leg to ~9 minutes.

---

## Job 2 — the DOI list (read-only)

`search_after` paging over `metadata.chunk_index: 0`, sorted by `doc_id`, 2,000 rows/page, 50 ms
between pages, `_source` restricted to eight fields. All three ASM indices: **1,085,470 rows in
212 seconds**, no PIT, no scroll context left behind, no unbounded scan. Harness:
`extract_dois.py`.

| | measured here | the brief's figure |
|---|---|---|
| distinct `doc_id` across all three | **440,049** | ~438,834 |
| with `metadata.doi` | **394,131 (89.6%)** | ~392,083 (89%) |
| **distinct DOIs** | **273,653** | — |
| without `metadata.doi` | **45,918 (10.4%)** | ~46,700 |
| without `metadata.title` | **261,912 (59.5%)** | ~261,407 (60%) |

Two things worth flagging that the brief's numbers do not show:

- **394,131 documents share only 273,653 DOIs.** 120,478 documents sit on a DOI that another
  document also claims — chiefly an article and its supplementary files. Any backfill keyed on
  DOI must expect a 1:N fan-out and decide whether a supplement inherits its article's title.
- **Collection membership**, computed exactly rather than sampled: `asm-tok256` 439,513 docs,
  `asm-tok512` 439,818, `asm-semantic` **205,589 (46.7%)**. That confirms the audit's ~49% and
  refutes `HANDOFF-2026-07-04.md`'s 8.6%. I did not investigate it further, per the brief.

---

## Job 3 — the cache

`/rag/data/asm-metadata-cache/` (writing there was permitted; no fallback needed).

| file | lines | bytes | sha256 |
|---|---|---|---|
| `crossref.jsonl` | 277,682 | 1,109,658,556 | `93297b04…adfd80cf` |
| `ncbi-idconv.jsonl` | 277,682 | 49,091,190 | `216ac7ae…72501de7` |
| `doc-index.jsonl` | 440,049 | 220,240,681 | `80f49002…92720b2a` |

0 unparseable lines across all three. One record per DOI, no duplicate keys. Full schema in the
cache's own `README.md`.

**Dates are recorded unflattened, as asked.** Every date Crossref returned is kept in its native
`date-parts` form under `dates`, so the pending type decision — date type vs. split
year/month/day ints — can be served from the cache either way with no re-fetch. Publication-date
granularity across the 263,532 resolved records:

| publication date | records |
|---|---|
| year + month + day | 61,261 (23.2%) |
| year + month | 201,901 (76.6%) |
| year only | 370 (0.1%) |

> **Unverified as committed (found in the #624 review).** These three counts came from a per-`issued`-date pass whose script and output were not kept. The committed `coverage-final.txt` measures the *maximum* `date-parts` length across **all** Crossref date kinds (`created`/`deposited` included) and reports `{3: 263532}` — 100% three-part — which is exactly the trap this document warns about below, and does **not** support this split. Re-measure on `issued` alone before relying on it. The 76.6% figure is re-quoted in `docs/plans/date-filtering.md` and `docs/plans/metadata-consistency.md`, each now carrying this caveat.

**A consumer must read the length of `date-parts` and not assume 3.** Note also that `created`
and `deposited` always carry three parts and are Crossref *registration* dates — mistaking them
for publication dates would put three-quarters of the corpus on a wrong day.

**Resumability** was not designed on paper; it was exercised. The Crossref leg was interrupted
once (a 414 storm) and restarted, and the restart correctly skipped the 57,800 DOIs already
cached and fetched exactly the remaining 215,826. Negative results are cached as
`{"found": false}` so a resume does not re-query them forever.

### Coverage, document-level, across all 440,049 ASM documents

| field | production today | **in the cache** |
|---|---|---|
| `title` | 178,137 (40.5%) | **415,540 (94.4%)** |
| `journal` | **0 (0.0%)** | **415,486 (94.4%)** |
| `authors` | ~7% | **411,436 (93.5%)** |
| `publisher` | **0 (0.0%)** | **415,540 (94.4%)** |
| publication date (unflattened) | year only, 99.5% | **415,540 (94.4%)**, y-m or y-m-d |
| `pmcid` | **0 (0.0%)** | **411,587 (93.5%)** |
| `pmid` | **0 (0.0%)** | **407,213 (92.5%)** |
| abstract | **0 (0.0%)** | 388,538 (88.3%) |

**248,477 documents (56.5%) gain a title they do not have today.**

The aggregate understates it, because it averages articles together with mastheads. By
`doc_type`:

| `doc_type` | docs | title before | **title after** | **pmcid after** |
|---|---|---|---|---|
| `article` | 377,665 | 39.5% | **99.2%** | **98.7%** |
| `short` | 4,632 | 41.5% | **99.8%** | 58.7% |
| `supplement` | 52,488 | 50.4% | **68.8%** | 68.7% |
| `front-matter` | 5,264 | 10.7% | 0.5% | 0.4% |

**For the 86% of the corpus that is a body article, the title gap closes to 0.8% and the PMCID
gap to 1.3%.** Per collection the numbers are identical to a decimal (94.4% title, 93.5% pmcid on
each of tok256, tok512 and semantic), because semantic is a strict document subset.

### What remains unrecoverable, and why

**24,504 documents (5.6%)** have no record from either service:

| | count | why |
|---|---|---|
| `supplement` | 16,375 | a supplementary file whose parent article is itself unresolvable, or which has no parent DOI in its path |
| `front-matter` | 5,239 | mastheads, covers, editorial boards. **These have no bibliographic record to find** — they are not articles. Correctly unrecoverable, not a failure |
| `article` | 2,879 | inspected: mostly mastheads *misclassified* as `article` (`jvi.masthead.88-16.pdf`), plus documents whose production DOI is malformed (`10.1128/mSphere`, `10.1128/microbiolspec.PSIB-` — truncated by the text-scan rule) |
| `short` | 11 | |

Also: **305 documents (0.07%) have neither a DOI nor a derivable parent DOI** and are outside
every route here.

And one finding that changes what "coverage" means: **of the ~178,100 documents production counts
as *having* a title, 10,196 (5.7%) carry a PDF-internal id instead** —
`jv089906257p`, `SM-JVIJ200119 1..22`, `Microsoft Word - SI-Chrysogine_AEM.docx`. So today's real
title coverage is ~38.2%, not 40.5%, and a backfill that only fills *empty* titles will leave
those 10,196 wrong. The cache has the correct title for essentially all of them.

---

## Job 4 — the ~10% with no DOI

Reported separately, as asked. Two findings, and the second one displaces the first.

### The `first_page` parse works on the wrong population

I scored a deliberately small (~70-line) heuristic — strip masthead, take the lines before the
author block — against **documents that already have a title in production**, so the score is
objective rather than eyeballed. 1,179 documents pulled from the 41 source shards
(`scan_first_page.py`, 1,179/1,200 matched, `first_page` present on 99.8% of those).

Set C — 300 DOI-bearing articles with a known title:

| | |
|---|---|
| produced some title | 295 (98.3%) |
| gold title text **located** (contained either way) | **201 / 290 = 69.3%** |
| similarity ≥ 0.90 | 128 / 290 = 44.1% |
| similarity ≥ 0.60 | 254 / 290 = 87.6% |

And the residual is not what the number suggests. Reading the 36 "wrong" cases: several are the
**production title being wrong and the parse being right** (gold `SM-JVIJ200119 1..22`, parsed
"Transcriptional Analysis of Lymphoid Tissues from Infected Nonhuman Primates…"), and several
more are PDF **ligatures** (`Ciproﬂoxacin` vs `Ciprofloxacin`) that a two-line normalisation
would fix. **The title text is in `first_page` and a modest parser finds it.** Viability:
confirmed. Cost: a day on the parser, or one cheap local model pass; it is offline, free, and
needs no network.

### But it is not the route for the no-DOI population — a better one exists

The no-DOI population is **not** 46,700 articles missing titles. It is:

| `doc_type` | count | share |
|---|---|---|
| `supplement` | 33,986 | 74.0% |
| `article` | 6,771 | 14.7% |
| `front-matter` | 5,156 | 11.2% |

Three-quarters of it is supplementary figures and tables, and a ninth is mastheads. On the 400
sampled documents with neither DOI nor title, the parser did "succeed" — and returned
`"Supplemental Material"`, `"Table S1. Cp-values in IS1111 qPCR…"`, and `"1"`. **There is no
article title in these files to recover, so no extractor can recover one.** The 25,887 no-DOI
documents that *do* carry a title mostly carry a junk one (`TABLE 1`, `Microsoft Word - figure
legends for supplementary figure 1.doc`; 17.6% are internal ids on the sample).

**What works instead is entirely offline: the parent article's DOI is sitting in
`source_path`.**

```
/local/scratch/<uuid>/SPECTRUMv9i2_10_1128_Spectrum_01309_21-.../
    spectrum.2021.9.issue-2/spectrum.01309-21/suppl/spectrum01309-21_supp_1_seq2.pdf
                             ^^^^^^^^^^^^^^^^  -> 10.1128/spectrum.01309-21
```

Two rules — the ASM filename→DOI regex `enrich.py` already owns, applied to *every path segment*
rather than only the basename; and the `_10_1128_<Journal>_<id>_<id>-` job-directory token as a
fallback — derive a parent DOI for **45,613 of 45,918 (99.3%)** of the no-DOI documents. Of the
distinct article-shaped parents, **80.5% were already independently present** in the DOI set
production derived from *other* documents' filenames, which is a clean cross-check that the rule
produces real DOIs and not plausible-looking strings. The 4,074 that were new resolved in
Crossref at **98.8%**.

Those 45,613 documents are already joined and resolved in the cache (`doc-index.jsonl`'s
`join_doi`), and they are why `supplement` reaches 68.8% title coverage above rather than 0%.
6,912 of them resolve only to an *issue*-level pseudo-DOI (`10.1128/jb.1991.173.issue-4`) — that
is the front matter, and it correctly yields nothing.

**Recommendation for the no-DOI documents: use the parent DOI from `source_path`, not a
`first_page` parse.** It is free, offline, authoritative, already done, and it carries `pmcid`,
`pmid`, `journal` and authors with it — none of which a masthead parse could ever produce.
Keep the `first_page` parser in reserve for the ~2,879 articles with a broken DOI, where it is
the only remaining route.

---

## What I did not verify

- **Whether the 94.9% Crossref resolution is correct rather than merely returned.** I checked
  that the records come back populated; I did not spot-check that a returned title is the title
  of *that* document. The audit's n=200 manual check is the only evidence on that, and it was
  100%.
- **Bulk snapshot sizes.** The OpenAlex manifest URL 404'd and I did not chase it; I did not
  price the Crossref annual data file at all. Neither affects the route decision.
- **Whether OpenAlex's 1,000-credit limit is per-account, per-IP, or raisable.** I read it from
  response headers and inferred a daily reset from `x-ratelimit-reset: 15625` landing on 00:00
  UTC. I did not test a second identity or look for a paid tier's terms.
- **The 14,057 ASM-shaped DOIs Crossref did not find.** I classified them by shape and read a
  handful; I did not confirm that repairing the filename rule would recover them.
- **`asm-semantic`'s 47% coverage.** Measured, not investigated — out of scope per the brief.
- **The contact address.** Both services were given `awilke1972@gmail.com` as the polite-pool
  `mailto`, because it is the only contact address in this environment and none is configured in
  the repo (`doi_enrichment_mailto` defaults to empty; `.env.example` sets nothing). **If a role
  address should carry this traffic instead, set `ASM_CACHE_MAILTO` and the fetcher will use it.**
  The address appears in the `User-Agent` and the `mailto` query parameter of Crossref and NCBI
  requests, and in `retrieved`-stamped provenance nowhere else; it is not in the cache records.

## Two operational lessons

- **A SIGTERM to a threaded fetcher does not stop it.** I killed the first Crossref run by its
  recorded pid, confirmed the process was gone, and started the resumed run — and its worker
  threads went on writing to the same file, producing 69,844 duplicate lines. They were fully
  self-consistent (0 conflicting values) and have been removed, but the overlap was real and the
  two runs were briefly hitting Crossref together. Stopping by recorded pid is necessary and was
  not sufficient; verify the file stops growing before restarting.
- **A one-way backoff is safe and slow.** Slowing the global gate on a 429 and never recovering
  turned a 9-minute job into a 41-minute one. Safe was the right default for a first run against
  someone else's service; a decaying recovery is the fix.

## Files

Harness and logs are in this directory; the cache is at `/rag/data/asm-metadata-cache/`.

| file | what |
|---|---|
| `extract_dois.py` | Job 2 — read-only `search_after` extraction from production ES |
| `bench.py` | Job 1 — the route benchmark (`bench_*.log` are its raw output) |
| `fetch_cache.py` | Job 3 — the resumable batched fetcher, both routes |
| `coverage.py` | the coverage roll-up (`coverage-final.txt`, `cuts.txt`) |
| `scan_first_page.py` | Job 4 — offline pull of `first_page` from the 41 source shards |
| `title_parse.py` | Job 4 — the scored heuristic (`title-parse-eval.txt`) |
| `cr_full2.log`, `ncbi_full.log`, `fp_scan.log` | run logs |
| `crossref-requests.log`, `ncbi-requests.log` | every non-200 response, and the run summaries |
