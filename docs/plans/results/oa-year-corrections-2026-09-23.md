# `open-access` future-year corrections — the remaining 8,208, applied and verified

**Date:** 2026-09-23
**Target:** `ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc`
on Qdrant `http://localhost:6333` (storage `/rag/data/qdrant`) + Elasticsearch `http://localhost:9200`
**Status:** **COMPLETE. 8,208 corrections applied to both stores, 17/17 verification checks pass, ES `year > 2026` is 0.**
**Scope:** corrections only. No fills were written. The fills run from
[the 09-16 record](oa-year-backfill-2026-09-16.md) stays stopped at its disk gate; this is not a resume of it.

---

## 1. Verdict first

The 8,208 points that still carried a future `year` (2027–2049, the pre-#573 accession/UUID scan) now carry the
`discovery.jsonl` year for their `pmcid`, as `year` (int) plus the derived `date = year * 10000` (int), in both
Qdrant and ES — the same two fields, same types, same derivation the canary wrote for its 200.

| gate | expected | measured |
|---|---:|---:|
| ES `metadata.year > 2026` **before** | 8,208 | **8,208** |
| ES `metadata.year > 2026` **after** | 0 | **0** |
| ES has-year (corrections already had a year) | 7,294,845 unchanged | **7,294,845** |
| ES has-date | 253,008 + 8,208 = 261,216 | **261,216** |
| Qdrant `points_count` | 47,625,155 unchanged | **47,625,155** |
| ES doc count | 47,625,155 | **47,625,155** |
| points corrected in Qdrant (`year`,`date` == expected, int) | 8,208 | **8,208 / 8,208** |
| docs corrected in ES (`metadata.year`,`metadata.date` == expected, int) | 8,208 | **8,208 / 8,208** |
| `new_year == discovery.jsonl[pmcid]` | all | **8,208 / 8,208** (and the required random 50) |
| ES sibling metadata intact after partial-doc merge | all | **8,208 / 8,208** |
| int-filter `year=2014` restricted to the corrected ids, Qdrant / ES | 1,825 | **1,825 / 1,825** |
| Qdrant `update_queue.length` after | 0 | **0** |
| `du` on the collection, three samples 20 s apart | identical | **913,602,568,579 × 3** |

The ledger of prior values was written **before** any write, and the rollback path was **executed** on 10 live
points (both stores back to the prior year, `date` removed, siblings intact) before the full run.

---

## 2. Target confirmation

Confirmed before any write, by explicit URL — every call in this job named `http://localhost:6333` or
`http://localhost:9200`; the decoy `:6343` (`/rag/data/qdrant2`) does not appear in any script.

| | points / docs | status |
|---|---:|---|
| Qdrant `:6333` — **the target** | 47,625,155 | green, optimizer ok, 193 segments |
| ES `:9200`, same index name | 47,625,155 | yellow (permanent single-node baseline, see 09-16 §2) |

Baseline invariants at start, all as the 09-16 record left them: ES `year > 2026` = 8,208,
has-year = 7,294,845, has-date = 253,008. No leftover pids from the 09-16 run were alive.
`/rag` free: 1,152 GB (gate: refuse below 400 GB).

---

## 3. The set, and the ledger

`plan/correct_*.txt` (20 files) holds 8,408 point ids with their prior year. Minus the 200 in
`canary_done.txt` (all 200 present in `canary_ledger.jsonl` as `kind: correct`) leaves **exactly 8,208**.

Ledger built read-only from Qdrant by point id, then cross-checked against ES by `_mget` on
`"public:" + chunk_id`, before any write:

```
plan ids                       8,408
canary corrections done          200
remaining                      8,208
fetched from Qdrant            8,208   missing 0
prior_year int and > 2026      8,208   plan-file prior == stored prior: 8,208   prior date present: 0
ES agrees (year, no date, pmcid)   8,208   missing 0   disagree 0
distinct documents (pmcid)       611
prior years   2027–2049 (2030: 519, 2035: 360, 2027: 244, …)
new years     2002–2026 (2014: 1,825, 2016: 1,301, 2013: 1,211, 2015: 953, 2018: 799, …)
```

**`corrections_ledger.jsonl`** — 8,208 rows, same row shape as `canary_ledger.jsonl`
(`qid, chunk_id, pmcid, prior_year, prior_date, new_year, kind`), md5 `fb5ea836595d578b1e55f6346439e764`.
Committed in [`oa-year-corrections-2026-09-23/`](oa-year-corrections-2026-09-23/) next to this record, together with
the three scripts, so the rollback below resolves from git and not from a session-scoped `/tmp`. The original is at
`/tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad/oa-year-corrections/`
alongside the done-files and every gate log (`ledger.log`, `dryrun_*.log`, `run_apply.log`, `verify_full.log`).

---

## 4. Dry run — 10 points, applied, rolled back, verified at each step

Ten ids sampled with a fixed seed from the ledger (prior 2028–2049 → new 2011–2019). Sequence, each step verified
in **both** stores per point (year, date, pmcid, chunk_id, ES sibling fields):

| step | ES `year > 2026` | ES has-date | per-point |
|---|---:|---:|---|
| before | 8,208 | 253,008 | 10/10 at prior year, no date |
| apply 10 | **8,198** | **253,018** | 10/10 at new year, date = year·10000 |
| `rollback_corrections.py --execute` on the 10 | **8,208** | **253,008** | 10/10 back at prior year, no date, siblings intact |

That is the rollback path proven with the real ledger and the real scripts, not read and agreed with. The 10 were
then re-applied as part of the full run (its done-files were fresh; `set_payload` is idempotent).

---

## 5. Full run

```
Qdrant  8,208 points in 12.0 s  (684 pts/s), 20 year groups, BATCH 1000, ?wait=true, 0 errors
ES      8,208 docs   in  4.7 s  _bulk partial-doc update, chunk_id fetched from Qdrant by point id
                                 and asserted equal to the ledger, 0 errors
```

Launched under `nohup` with the pid recorded in `apply.pid`; the process exited on its own. Floor checks every
2,000 points: `/rag` never below 1,149 GB; `points_count` never moved.

**Disk.** Transient peak **+3.76 GiB** (status yellow, segments 193 → 194) during the ~40 s the optimizer took
to merge the appendable segment; settled at **+0.13 GiB** with 193 segments and status green. That is
0.16 GiB / 10k points net — consistent with the 0.140 GiB / 10k the 09-16 record measured. Note the transient is
a near-fixed cost of one segment merge, not proportional to points: 8,208 points cost the same ~3.8 GiB peak the
canary's 50,200 points cost 5.2 GiB.

**Live-tenant impact:** not re-measured for a 17-second write; the 09-16 probes covered 30× this volume.

---

## 6. Verification — 17/17

```
PASS  qdrant points_count == 47,625,155
PASS  qdrant status green                                  optimizer=ok segments=193
PASS  ES doc count == 47,625,155
PASS  8208/8208 qdrant year,date == expected (pmcid,chunk_id intact)
PASS  qdrant year/date are int
PASS  8208/8208 ES metadata.year,date == expected and int (pmcid intact)
PASS  ES sibling metadata intact
PASS  random 50: new_year == discovery.jsonl year for pmcid
PASS  all 8208: new_year == discovery year
PASS  ES year>2026 == 0
PASS  ES has-year == 7,294,845 (unchanged)
PASS  ES has-date == 261,216
PASS  qdrant int-match year=2014 on corrected subset == 1825
PASS  ES int-term year=2014 on corrected subset == 1825
PASS  qdrant update_queue length == 0                      op_num 22,172,867
PASS  three du samples byte-identical (writes stopped)     913,602,568,579 × 3
PASS  qdrant optimizer_status ok after settle
```

The global `year > 2026` count is an **ES** measurement. The Qdrant-side exact `count` with a `range` filter on
`year` times out at 60 s on this collection (no payload index on `year`, 47.6M-point scan), which is also why the
09-16 record measured the gap in ES. The Qdrant side is instead proven per point: all 8,208 ids read back with
the expected values, and the int-filter check restricted by `has_id` matches.

---

## 7. State left behind, and how to undo it

Both stores consistent: the collection now has **261,216** points with `date` (253,008 from the 09-16 run +
8,208 here) and **zero** with `year > 2026`. `points_count` 47,625,155, green, 193 segments.

### Rollback command

```bash
cd docs/plans/results/oa-year-corrections-2026-09-23        # or the /tmp working dir named in §3
/rag/envs/ragstack/bin/python rollback_corrections.py --dry-run   # prints: to revert: 8,208 corrections
/rag/envs/ragstack/bin/python rollback_corrections.py --execute
```

Restores each point's prior (future) `year` from `corrections_ledger.jsonl` and deletes `date`, in Qdrant and
ES, resumable via `rollback_corrections_done.txt`. Same logic as the 09-16 `rollback.py` corrections branch.
Confirm with ES `year > 2026` returning to 8,208 and has-date to 253,008 — the exact transition the 10-point
roundtrip in §4 exhibited. `EXPECT=reverted python verify.py` checks every point.

The 09-16 `rollback.py` is **unaware of this ledger** (it reads `canary_ledger.jsonl` + `run_progress.json`);
a full revert of both jobs is the two commands run separately.

---

## 8. Open follow-ups

1. **The producer fix (#573) is not deployed on asm-next (v1.6.1).** New ingests there can still write future
   years; this pass corrected the stock, not the source. Re-run the ES `range year > 2026` count after any
   asm-next ingest into a collection that predates #573. Nothing was deployed by this job.
2. **The fills** (40,582,256 points without `year`) remain undone, stopped at the 09-16 disk gate. The disk
   decision in 09-16 §10 is still the decision to take.
3. **The 556,971 stored-vs-source disagreements** (09-16 §5), of which an implausible subset is very likely the
   same defect as these 8,408 landing in the past instead of the future, are still untouched.
4. `run_qdrant.py` in the 09-16 working dir was edited on 2026-09-17 (a "corrections first" ordering comment and
   an `ONLY=` switch); `run_progress.json` shows it was never run. It is now moot for corrections and should not be
   used for fills without the disk decision above.

---

## 9. Small surprises, for the next operator

* ES on this host rejects a body-level `_source: [..]` array in `_mget` (HTTP 400); use the
  `?_source_includes=` query parameter.
* `update_queue` is not on `/collections/<c>/cluster`; it is at
  `/telemetry?details_level=3 → collections.collections[].shards[].local.update_queue.length`.
* `:6333` holds **9** collections, not the 10 the 09-16 record left: `oa_smoke_tok512` is gone. The collections
  directory mtime is `2026-09-16 12:20:51 -0500` — seven hours after the 09-16 run ended and a week before this
  job, which issued no `DELETE` to any instance. Recorded here because the 09-16 record (§9) already flagged
  unattributed deletions on this instance; this is another one.
