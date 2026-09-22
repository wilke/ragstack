# ASM-semantic metadata backfill on the hackathon tenant — COMPLETE

**Date:** 2026-09-16, 03:36–09:55 America/Chicago.
**Target:** Qdrant `:24081` / Elasticsearch `:24083`, collection
`ragstack_lib_asm_semantic_salesforce_sfr_embedding_4096_semantic_f59de32c`
(registry id `asm-semantic`), 6,718,269 chunks / 440,201 documents.
Nothing on `:6333`, `:6343`, `:9200`, `:24003`, `:24041`, `:24043` was read or written.

---

## Is the corpus in a good state to demo right now?

**Yes.**

Qdrant is `green`, `optimizer_status: ok`, 18 segments, update queue 0. `points_count` is
**exactly 6,718,269** and ES `docs.count` is **exactly 6,718,269** — both unchanged, both read
after full settling. Query latency is **at or better than the pre-write baseline** (median
0.55 s vs 0.61 s; p95 0.73 s vs 0.72 s; 0 of 30 samples over 1 s). The top-1 and top-3 results
of ten fixed queries are **identical** to before the backfill.

**It completed.** All 431,808 planned documents were written — 6,403,265 chunk payloads across
both stores.

### Coverage: what an attendee sees

Title quality over **all 440,201 documents** (a census, not a sample), stated two ways because
my junk test has a measured false-negative population and the flattering number would be
misleading:

| | before | after |
|---|---:|---:|
| real title (**by my junk test**) | 150,131 (34.1%) | 433,657 (**98.5%**) |
| real title (**adjusted for known false negatives**) | 141,504 (32.1%) | 425,014 (**96.5%**) |
| junk title (adjusted) | 36,702 (8.3%) | 10,276 (**2.3%**) |
| absent title | 261,995 (59.5%) | 4,911 (**1.1%**) |

Per field, document level:

| field | before | after |
|---|---:|---:|
| `title` | 178,206 (40.5%) | **435,290 (98.9%)** |
| `journal` | 0 (0.0%) | **431,749 (98.1%)** |
| `publisher` | 0 (0.0%) | **431,803 (98.1%)** |
| `authors` | 48,049 (10.9%) | **428,003 (97.2%)** |
| `pmcid` | 0 (0.0%) | **427,850 (97.2%)** |
| `pmid` | 0 (0.0%) | **423,470 (96.2%)** |
| `date` (yyyymmdd int) | 0 (0.0%) | **431,753 (98.1%)** |
| `year` | 436,527 (99.2%) | **439,786 (99.9%)** |

**Field paths were proven against a real sampled document in each store before any number above
was computed** — this assumption produced confidently wrong readings three times in this work:

- **Qdrant** point `0000030e-5446-5cc2-94e5-bb69458a630a` — payload keys are **flat**, no
  nesting. Path is `title`.
- **ES** doc `public:2f226d43-0fb6-5afb-b6ea-32a1e583d7ba` — `_source` is
  `[chunk_id, doc_id, end_char, metadata, start_char]`, fields nested under `metadata.*`.
  Path is `metadata.title`.

The two stores were then censused **independently** — Qdrant by scrolling all 6,718,269 points,
ES by `_count` with `exists` queries — and agree **exactly**: title 6,524,727 in both, journal
6,401,814 in both, date 6,401,829 in both. Agreement of two independently-measured stores is
the strongest evidence here that the numbers are real.

---

## The four checks, all read after settling

| # | check | result |
|---|---|---|
| 1 | `points_count` | **exactly 6,718,269, unchanged** |
| 2 | ES `docs.count` | **exactly 6,718,269, unchanged** |
| 3 | 200 random documents agree across both stores | **2,756 chunks, 0 mismatches** — Qdrant == ES == planned value on every written field |
| 4 | retrieval invariance | **see below — vectors provably unchanged, tail of top-20 moves** |

### `points_count`, verified properly

Confirmed at rest **twice**: after the 08:30 settle and after the final settle, both exactly
**6,718,269**. This matters because the figure is misleading if sampled mid-flight: during
segment merges Qdrant reports it as an *estimate* and it dips transiently (observed
6,718,269 → 6,718,255 → back within seconds). Any future check of this number must be taken
after `status: green`, never during a run.

### Retrieval invariance — a partial pass, and exactly what moved

| | |
|---|---|
| top-1 identical | **10/10** |
| top-3 identical | **10/10** |
| top-5 identical | 9/10 |
| top-10 identical | 8/10 |
| top-20 identical | 1/10 |

The strict check as written ("chunk ids and scores identical") **fails at top-20**. The cause is
not the payload write, and this is provable rather than asserted:

> Of the 188 chunks appearing in **both** the before and after result sets, **188 kept their
> exact score and 0 changed.** Twelve of 200 hits (6%) entered the top-20 and twelve left.

A vector that moved would change the score of a chunk that stayed. None did. What changed is
**HNSW approximate-search recall** after the index was re-segmented (15 → 48 → 18 segments over
the run): an ANN index returns slightly different neighbours once its graph is rebuilt. The
churn is concentrated at the **bottom of the top-20** — mostly ranks 16–19, in a tight score
band just above the cutoff — while the head of the ranking is stable.

Two supporting facts: the cross-encoder scores `(query, chunk.content)` only and never sees
`title` (`scoring/scorers.py:98`), so reranking cannot be the cause; and the same check after
the 2,000-document canary was 9/10 identical, scaling with how much of the index had been
rewritten rather than with anything about the metadata.

**Call it honestly: this is an index-rebuild effect inherent to re-segmenting an HNSW
collection, not a data defect — but it is a real behavioural change and "byte-identical
retrieval" is not a claim this run can make.**

---

## What could not be fixed — the failure population by name

Of 440,201 documents, **431,808 (98.1%) were enriched**. The residual:

| class | docs | why |
|---|---:|---|
| `join_doi` present but genuinely unregistered in **both** Crossref and NCBI | **7,936** | the DOI exists in production metadata but no registry has a record |
| no DOI at all and none derivable (incl. 305 `microbiolspec.*` book chapters, 152 `doc_id`s absent from `doc-index.jsonl`) | **457** | nothing local to join on |
| **total unfixable** | **8,393 (1.9%)** | |

Of those 8,393, **3,486 already carried some title**, so 4,907 remain title-less — which is
most of the 4,911 documents still showing no title at all.

**Residual junk titles: 10,276 documents (2.3%)**, of which **8,616 are `doc_type: supplement`**.
These break down as 1,633 my junk test flags but could not replace (no cache title), plus
**8,643 false negatives my test deliberately keeps**:

| false-negative class | docs | example |
|---|---:|---|
| supplement label | 4,447 | `Supplementary Figures w legends` |
| generic placeholder | 2,121 | `PowerPoint Presentation`, `R Graphics Output`, `Slide 1` |
| leading Table/Figure | 1,515 | `Table S1`, `Figure S2` |
| underscored id | 560 | `Figure_S6`, `Sakai_TableS2` |

This is the intended direction of error — a left-alone junk title is recoverable, a destroyed
real title is not. **A tightened second pass would catch all 8,643, and 99.8% of them are
`doc_type: supplement`** (8,616 of 8,643; only 24 articles and 3 short). Gating those four rules
on `doc_type == "supplement"` makes a false positive close to impossible and would take real
titles from 96.5% to ~98.5%. That is a clean follow-up, not something to do under time pressure.

---

## The junk test, as applied

A title is junk if **any** rule fires. Implemented in `junk.py`. Tuned to prefer false negatives.

| rule | test | docs matched (before) |
|---|---|---:|
| `J1-toolprefix` | starts with `Microsoft Word/PowerPoint/Excel`, `untitled`, `NO JOB NAME`, `PII:`, bare `Document`/`Print`/`Layout`/`Final`/`Draft` | 9,757 |
| `J2-pagerange` | ends with the typesetter artifact `\d+\.\.\d+` (`SM-AEMJ210365 1..8`) | 8,215 |
| `J3-fileext` | contains a file extension (`.doc .docx .pdf .qxd .indd .eps .ppt .xls …`) | 2,141 |
| `J4-idlike` | single token, no whitespace, ≤40 chars, ≥2 digits (`jm119903586p`) | 6,521 |
| `J5-numeric-shortstring` | ≤3 tokens and ≥40% of alphanumerics are digits | 133 |
| `J6-equals-filename` | equals the chunk's own `filename`, ± extension | 89 |
| `J8-tooshort` | ≤5 alphanumeric characters (`-`, `FigS1`, `ctx`) | 1,219 |
| | **total judged junk** | **28,075 (6.4%)** |

**Consequence worth stating:** a supplement's cache join is on its **parent article's DOI**, so a
supplement titled `Microsoft Word - Table S4.docx` now carries the parent article's title. That
is intended and is a large readability win, but title alone no longer distinguishes a supplement
from its article — `doc_type: supplement` remains the discriminator.

---

## The clobber policy, as actually applied

Decided **per chunk**, not per document.

| prior state | action | chunks |
|---|---|---:|
| `title` absent | fill from cache | 3,540,821 |
| `title` junk **and** cache supplies a title | replace, prior value to the ledger | 338,711 |
| `title` real | **never touched** | 2,523,685 |
| cache has no title | nothing written | 48 |

Two deliberate departures, stated rather than buried:

- **`year` is overwritten wherever `date` is written** — 19,503 chunks disagreed. Mandatory:
  §1.2 requires `year = date // 10000`, never captured independently, which is the drift bug
  #573 removed. Spot checks show it is a *correction* (`aac.33.2.136.pdf` carried `1971`,
  Crossref says `1989`; another carried a future `2039`). Every prior value is in the ledger.
- **`authors` is *not* overwritten** — 520,006 chunks kept their existing value. The existing
  values are PDF-metadata extractions and often one name for a ten-author paper, so Crossref is
  strictly better; but the brief authorises clobbering only *titles* and I have no stated junk
  test for an author list. A deliberate gap.

Two fields beyond the brief's list were written because §1.2 marks them required-iff-recovered
and they make the write auditable: `title_source` (`crossref` / `crossref-parent`, only where a
title was set) and `enriched_from` (`["crossref:2026-09-16"]` /
`["crossref:2026-09-16:parent-doi"]`, which is how the two recovery paths stay distinguishable
in the store itself).

**Types:** `date`/`year` ints; `pmid`/`pmcid` strings; `authors`/`enriched_from` lists. `date` is
zero-padded `yyyymmdd` from Crossref `date-parts`, reading the array length rather than assuming
3, priority `issued → published → published-print → published-online`, never
`created`/`deposited` (registration, not publication). Values outside 1800 ≤ year ≤ 2027 dropped
and counted.

---

## The synthetic-DOI recovery (16,263 documents)

Found mid-run: 16,263 of the 24,656 originally-unfixable documents carry a **synthetic
per-supplement DOI** (`10.1128/mbio.03591-22-s0004`) that Crossref never registers. Stripping
the suffix resolves the parent (`10.1128/mbio.03591-22`), which was already in the local cache.
No network. The cache's parent-DOI derivation had only run on documents whose `doi` was *null*,
so these fell out of the join.

This lifted the fixable population from 415,545 (94.4%) to **431,808 (98.1%)**.

**Verified intact after settling:** 300 sampled recovery documents → 2,361 chunks,
**2,361/2,361 correct in Qdrant, 2,361/2,361 correct in ES, 2,361/2,361 carrying the
`crossref:2026-09-16:parent-doi` stamp.**

---

## Throughput, disk, and the two things that actually mattered

**`set_payload` rewrites the whole point.** The canary's 29,174 payload writes created a new
1.4 GB segment and raised `indexed_vectors_count` by 28,860 — one new indexed vector per chunk
written. Qdrant copies the vector, the int8 quantized copy and the payload into a fresh segment
and soft-deletes the original. This is the finding that should carry to the open-access job.

**But the canary over-projected disk by ~50×.** An isolated burst has no concurrent reclaim;
under sustained load the optimizer keeps pace. Measured GB per 10,000 documents:

| phase | GB/10k docs |
|---|---:|
| canary (isolated burst) | ~7 |
| run 3 (under contention) | **−2.52** (the segments directory *shrank* 27.9 GB) |
| run 5 (no contention, paced) | **0.36** |

Final collection size **101.8 GB vs 94 GB originally** — +8 GB for a 6.4M-chunk backfill, against
a 300 GB projection. `/rag` never approached the 400 GB floor (lowest observed 1,031 GB free).

**`wait=true` was the throughput bug.** Chosen for backpressure, it blocks behind the optimizer:

| | per batch of 250 docs |
|---|---|
| Qdrant `set_payload` **`wait=true`** | **12.54 s** |
| Qdrant `set_payload` **`wait=false`** | **0.04 s** |
| ES `_bulk` | 1.4–4.0 s |

Switching to `wait=false` (durable via WAL) with explicit `update_queue` backpressure moved the
run from 6.2 doc/s to 47–90 doc/s. **The cost is visibility lag**: the applied frontier trails
the checkpoint by roughly the queue depth (~17,000 documents at `QUEUE_MAX=20000`), so a
mid-run read of a recently-written document shows the *old* value. This produced one alarming
false negative during verification — a 0/8 sample that was simply reading unapplied documents.
**Verification must follow a queue drain**; a shallower `QUEUE_MAX=5000` cut the lag and the
end-of-run drain from 715 s to 179 s.

**Settling is fast and does not scale with write volume:**

| stop | chunks written | segments at stop | drain | to green | final segments |
|---|---:|---:|---:|---:|---:|
| canary | 29,174 | 22 | — | ~60 s | 15 |
| run 1 | 529,620 | ~30 | — | ≤90 s | 16 |
| 08:30 cut-off | 2,589,133 | 54 | 715 s | 836 s | 18 |
| final | 708,018 | 54 | 179 s | 340 s | **18** |

**Contention was real.** Sustained rate was 19–23 doc/s while the lucid and open-access
backfills were running, and 47.6 doc/s paced once they stopped — the same code, the same
settings.

---

## Rollback

```bash
cd /rag/data/asm-semantic-backfill
python3 rollback.py --docs 431808 --dry-run   # inspect first
python3 rollback.py --docs 431808             # then execute
```

Restores every prior value and **deletes the keys that did not exist before**, in both stores
(Qdrant `set_payload` + `delete_payload`; ES `_bulk` with a painless script that both sets and
`remove()`s). Idempotent. Dry-run reports: *431,808 documents / 6,403,265 chunks — qdrant
set_payload 6,365,170, delete_payload 6,403,265; es bulk-script updates 6,403,265*.

**Verified:** `ledger-full.jsonl` is line-aligned to `plan-full.jsonl` across all 431,808
entries with **0 doc_id mismatches**, and `ledgercheck.py` confirmed every recorded prior value
matches the pre-write inventory for the canary population (29,174/29,174, 0 mismatches).

**Caveat: the rollback has been dry-run, not executed against the stores.** Its inputs are
verified; its execution path is not.

---

## Artefacts — `/rag/data/asm-semantic-backfill/`

| file | what |
|---|---|
| `chunks-before.jsonl` (2.2 GB) | complete pre-write state of every field on all 6,718,269 points |
| `chunks-after.jsonl` | the same, post-run — the after census |
| `ledger-full.jsonl` (377 MB+) | per document, per point, the prior value of exactly the keys written |
| `plan-full.jsonl` / `plan-remaining.jsonl` | the deterministic write plans |
| `not-fixed-final.jsonl` | the 8,393 unfixable documents, with reason |
| `rollback.py`, `junk.py`, `final_verify.py`, `apply8.py`, … | tooling |
| `settle.log`, `latency.log`, `run*.log` | the measurements behind every number here |

---

## Follow-ups

1. **The tightened junk pass gated on `doc_type == "supplement"`** — 8,643 documents, near-zero
   false-positive risk, takes real titles 96.5% → ~98.5%. The single highest-value remaining fix.
2. **`title = filename` with `title_source: "filename"`** for the 4,907 title-less unfixable
   documents (§1.2 calls for it; not done because stamping a filename as a title has UI
   consequences the brief did not ask me to decide).
3. **`authors` overwrite** — 520,006 chunks still carry partial PDF-metadata author lists.
4. **Feed the `wait=false` + queue-backpressure pattern and the `set_payload` write-amplification
   numbers into the open-access job**, which is the same operation at 7× the scale.

No archive re-cut is needed: `versions []`, `archive_version 0` on this collection, so there is
no archive to go stale (metadata-consistency §0.4).
