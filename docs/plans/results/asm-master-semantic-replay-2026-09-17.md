# asm-next master `ragstack_sfr_semantic_full` — metadata replay COMPLETE

**Date:** 2026-09-16 → 2026-09-17
**Target:** `ragstack_sfr_semantic_full` on Qdrant `http://localhost:6333` + Elasticsearch `http://localhost:9200`
(the **asm-next master**, the collection `asm` exposes as `asm-semantic`).
**Status:** plan exhausted — 431,959/431,959 documents, 6,541,797 points, both legs.
**Working dir:** `/rag/data/asm-master-replay/` (plan, ledger, checkpoint, disk log, supervisor log, rollback).
**Not committed.**

---

## 1. Result

| field | before | after |
|---|---:|---:|
| `title` | 44.4% | **99.18%** |
| `journal` | **0%** | **97.35%** |
| `publisher` | **0%** | **97.37%** |
| `date` | **0%** | **97.35%** |
| `pmcid` | **0%** | **94.87%** |
| `pmid` | **0%** | **94.66%** |
| `authors` | 10.0% | 98.01% |
| `year` | — | 99.96% |

Counts at rest, both legs, after the optimizer settled green:

```
qdrant points_count  6,718,269   (was 6,718,269 — unchanged)
es     docs.count    6,718,269   (was 6,718,269 — unchanged)
status green · optimizer ok · update queue 0
```

### Verification

2,000 points sampled at random, every written field compared in **both** stores:

```
qdrant exact 2000/2000    es exact 2000/2000    mismatches: none
(date, year) types        599 int/int, 1 None/int (a document with no Crossref date)
date // 10000 == year     0 violations
```

## 2. What this was, and why it was the cheap one

The enrichment was built and verified on the **hackathon** copy of this corpus on 2026-09-16.
The master had never received it: measured on 2026-09-17 it sat at 44.4% title and **0%**
on journal, publisher, date, pmcid and pmid, while the hackathon copy was at 99%/97%.

The two collections are the same corpus with **identical point ids and chunk ids** —
1,120 points sampled across 14 random offsets in the id space matched byte-for-byte on
payload *and* vector. So the existing plans applied unchanged; nothing was re-derived and
no network call was made.

`ragstack_sfr_tok256` (24.8M points, the collection the `asm` tenant actually *defaults*
to) is still at 0% on those five fields. It is the same corpus under different chunking, so
its point ids differ and its plan must be rebuilt at document level — but 300/300 sampled
`doc_id`s are already in `docmeta.jsonl`, so that too is a local join.

## 3. Throughput — and the bug that cost 4× before it was found

The first canary ran at **44 pt/s**, which projected to ~40 hours. Instrumenting instead of
assuming contention showed only **21 s of 322 s** was spent in the stores. The rest was my
own gate: its frequency test compared `(i - start)` against `(i - j - start)`, but `i == j`
after the group loop, so it fired on **every batch** — and each call ran `coll_bytes()`,
walking every file in a 119 GiB collection.

| | rate | projected |
|---|---:|---:|
| as written | 44 pt/s | ~40 h |
| gate frequency fixed | 176 pt/s | ~10 h |
| + ES doc body serialized once per document (not once per point) | **248 pt/s** | **7.2 h** |

Settled at **120–128 pt/s** for the long run: 4,894,962 points in 39,969 s. The drop from
248 is not a regression — store time was ~28% of wall clock and the rest is the gate
legitimately waiting out merges, which grow more frequent as segments churn.

## 4. Disk

**0.020 GiB per 10,000 points at peak** over the final leg; collection 125.58 → 129.58 GiB
(peak 135.37), `/rag` free 1154.9 → 1141.7 GiB against a 250 GiB floor. The early canary's
1.245 GiB/10k was optimizer catch-up, not a trend — the run ended with essentially no net
growth, matching the hackathon and lucid profile rather than open-access's.

## 5. The supervisor earned its place, once

The 300 s yellow-gate budget tripped during a routine merge overnight and the job sat
**idle for 6.5 hours at 20%** because nothing restarted it. The budget was raised to 1800 s
and a supervisor added that resumes across *transient* gate stops only — red, a moved
`points_count`, the disk floor, ES bulk errors and three non-advancing retries all still
stop for a person, and a retry that does not advance the checkpoint is not counted as
progress (the lucid lesson: a restart that achieved nothing looked identical to a healthy run).

It ran **one attempt, zero restarts, zero give-ups** across 11 hours, and exited
`kind=done reason='plan exhausted'`. So its value here was insurance, not intervention —
but the 6.5 idle hours are what it was insuring against.

## 6. Titles: markup, and what was left alone

23.5% of the plan's Crossref titles carried JATS markup and 20.1% embedded newlines, which
render as literal `<i>` to a user because the frontend correctly escapes rather than
`dangerouslySetInnerHTML`. They were normalized before writing — tags stripped, entities
unescaped, whitespace collapsed, with `<sub>`/`<sup>` attaching to the token *before* them
(`N <sub>2</sub>` → `N2`) while whitespace *after* the close tag is kept, so `aa<sub>3</sub> in`
does not fuse into `aa3in`.

Residue: **3 of 283,681 plan titles** (0.0011%) still contain a `<…>`, all three the string
`USP<71>` — a real pharmacopeia chapter designation, correctly entity-unescaped, not markup.

**Not this run's to fix:** titles the plan did *not* write keep their original PDF-extracted
form, and some of those carry footnote daggers (`…IcsA\n†`, `…Hosts\n†\n‡\n§`). Two of 600
sampled points showed this. A separate, smaller cleanup.

## 7. Rollback

`rollback.py` deletes exactly the keys this job wrote, bounded by the ledger rather than the
plan. Every field was additive on the master except `title`, and a 3,000-point pre-flight
found 136 title overwrites, **all of junk values** (`FigS6`,
`Microsoft Word - Supplements_revision.docx`) and **0 of real titles** — so a rollback
restores nothing worth keeping, and `--keep-title` exists for anyone who disagrees.
