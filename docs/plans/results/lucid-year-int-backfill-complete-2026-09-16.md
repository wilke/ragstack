# `lucid` year str→int backfill + `date` derivation — COMPLETE

**Date:** 2026-09-16
**Target:** `lucid_sfr_tok256` on Qdrant `http://localhost:6343` (storage `/rag/data/qdrant2`).
**Elasticsearch was deliberately NOT written** — `metadata.year` is mapped `keyword` there and
already matches both the numeric and the string form of a term query (measured before the run).
**Status:** plan exhausted, 1,484/1,484 batches. Collection green at rest.
**Not committed.**

---

## 1. Why this run existed

`{"year": 2021}` returned **0** results on the vector leg and **129,248** on BM25. Hybrid fusion
hid the disagreement behind the BM25 leg, so the filter looked like it worked. Qdrant compares
payload types exactly; the stored value was the string `"2021"`.

## 2. Result

| | before | after |
|---|---:|---:|
| `{"year": 2021}` on the vector leg | **0** | **129,248** |

Five years sampled identically before and after:

```
          before              after
2019   int  1,073  str 72,242     int  73,315  str 0
2020   int  1,434  str 96,632     int  98,066  str 0
2021   int  1,890  str127,358     int 129,248  str 0
2022   int  1,968  str132,655     int 134,623  str 0
2023   int  4,810  str324,340     int 329,150  str 0
----   11,175 int + 753,227 str = 764,402        764,402 int, 0 str
```

Exact. Nothing was lost in conversion and nothing was double-counted.

## 3. Collection-wide reconciliation

```
numeric `year` (int)                 1,363,425
no `year` at all                       188,979
`year` an unparseable string             2,386
                                     ---------
                                     1,554,790   == points_count, exactly
```

`points_count` was **1,554,790 before and after**, measured at rest with the optimizer green
both times. (`points_count` is only an estimate *during* merges — both readings here were taken
at rest, which is the condition that makes it an invariant.)

### The 2,386 left alone

`""` × 2,382 and `"????"` × 4. Genuinely unparseable, so per
[metadata-consistency.md](../metadata-consistency.md) §4.4 they were left as-is rather than
guessed at or deleted. They are a *known* gap, which is the point — an invented value would
look identical to a real one.

### Two implausible populations, found but not touched

* **80 points with `year > 2026`** — the accession/ISSN-scraping defect documented in
  [date-filtering.md](../date-filtering.md) Part B step 1.
* **26 points with `year == 1400`** — the same defect landing in the past, where the `> 2026`
  guard cannot see it.

Both are out of scope for a type conversion and are recorded here so they are not rediscovered
as new.

## 4. `date` derivation

`date` is present on **exactly 1,363,425** points — the same set that has a numeric `year`, so
the two representations cannot drift.

Verified on a 3,000-point sample:

* `date // 10000 == year` — **0 violations**
* both fields `int` — 3,000/3,000
* precision **100% `yyyy`** (`date % 10000 == 0`), which is correct: lucid's source carries a
  year and nothing finer. The zero-padding is self-describing, so no `date_precision` field is
  needed and nothing was invented.

A range query works as designed: `date >= 20200315` → **628,558** points.

## 5. Disk

Green at rest, segments 8→9→8. Peak churn **0.59 GiB per 10,000 points** on the final
(uncontended) leg; earlier contended legs measured 0.12 GiB/10k. Free space on `/rag` moved
1050.2 → 1045.4 GiB across the last run and fully reclaimed on settle. The collection did not
grow net.

## 6. Operational note — the harness killed this job twice, falsely

Two **background** runs were killed by the harness for "low memory" on a host with
1,385 GB available and memory pressure 0.00. The Qdrant RSS figures that triggered it
(331/142/48 GB) are mmap'd segment page cache, not anonymous memory. The second kill made
**zero** progress — the checkpoint never moved off 689 — so restarting was not converging.

**The job completed under `timeout N python3 apply.py …` in the foreground**, which is not
subject to that guard, run as five chained ~540 s segments (689 → 789 → 966 → 1126 → 1266 →
1411 → 1484). Throughput roughly doubled once the volume was uncontended: 171 pt/s contended,
~275 pt/s clean.

Worth remembering: because the job is checkpointed and idempotent, a kill costs only the
in-flight batch — but a kill that lands *before the first batch* costs everything, and looks
identical from the outside. Check that the checkpoint moved, not that the process ran.
