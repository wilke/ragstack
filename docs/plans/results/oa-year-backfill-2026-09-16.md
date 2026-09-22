# `open-access` year/date backfill — canary report and STOP

**Date:** 2026-09-16
**Target:** `ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc`
on Qdrant `http://localhost:6333` (storage `/rag/data/qdrant`) + Elasticsearch `http://localhost:9200`
**Status:** **STOPPED at the disk gate after 253,008 of 40,590,664 points.** Canary itself: clean, 15/15 checks pass.
**Record committed 2026-09-22 (#624); the run's working directory under `/rag/data/…` is not in git.**

---

## 1. Verdict first

The canary passed every correctness check. **The run was stopped by the disk gate, not by a correctness failure.**

My collection does **not** show the flat-footprint behaviour measured on the hackathon and lucid
collections. After writes stop and the optimizer fully settles, it retains a real net growth of
**0.140 GiB per 10,000 points**, measured across 253,008 points in five sustained groups. Extrapolated
over the 40,337,656 points remaining that is **≈565 GiB net**, which exceeds the 400 GB peak gate.

There is a plausible mechanism by which that growth would flatten later in the run (§6), but it is
**unverified**, and confirming it costs roughly 8M points / ~112 GiB / 6–9 hours of writes on the shared
production instance. That is a decision to be taken deliberately, not slid into. Stopping here with
numbers is the outcome the brief asks for.

**Coverage before → now → projected**

| | points | % of 47,625,155 |
|---|---:|---:|
| `year` present at start | 7,042,037 | 14.786% |
| `year` present now | 7,294,845 | 15.317% |
| projected at completion | 47,624,293 | 99.998% |

**Future-year corrections:** 8,408 identified, **200 applied and verified**, 8,208 remaining.
ES `year > 2026` went 8,408 → 8,208, exactly.

---

## 2. Target confirmation (the trap)

Confirmed before any write:

| | points / docs | status |
|---|---:|---|
| Qdrant `:6333` (`/rag/data/qdrant`) — **the target** | 47,625,155 | green |
| ES `:9200`, same index name | 47,625,155 | yellow (see note) |
| Qdrant `:6343` (`/rag/data/qdrant2`) — **the decoy** | 0 | never addressed |

Every write in this job used an explicit `http://localhost:6333` / `http://localhost:9200` URL. The decoy
was never written to.

*ES "yellow" is its permanent baseline here* — single node with 12 unassignable replica shards. It is not
a signal, and must not be read as one.

---

## 3. The gap, confirmed independently

Measured directly against ES rather than taken from the brief:

```
total                47,625,155
year present          7,042,037   (14.786%)
year > 2026               8,408
date present                  0
pmcid present        47,625,155   (100.000%)
```

---

## 4. Join key — established by inspection, not assumption

Payloads were sampled from **both** stores and the field paths proven separately:

* **Qdrant** — payload is flat: `year`, `date`, `pmcid` at top level.
* **ES** — nested: `metadata.year` (`long`), `metadata.pmcid` (`keyword`). `metadata.date` did not exist;
  it was added explicitly as `long` before writing rather than left to dynamic mapping.
* **Cross-store address** — `_id == "public:" + chunk_id`, verified by round-tripping five Qdrant-sourced
  chunks through ES `_mget` with `pmcid` and `year` agreeing. `tenant_id` is uniformly `public`
  (47,625,155/47,625,155). The Qdrant point id is **not** the chunk id — they are different UUIDs, so the
  two stores need different selectors.

**Join key: `pmcid`** — present on 100% of points in both stores.

### Join rate

Full read-only pass over all 47,625,155 points (64.6 min at 12,290 pts/s):

```
resolved via pmcid   47,611,843   99.972%
resolved via doi              0
resolved via pmid             0
resolved via idfix            0
unresolvable             13,312    0.028%
```

`discovery.jsonl`: 1,441,791 records → 1,441,425 usable `pmcid → year`, 62 distinct years (1951–2026),
366 records with no year, **zero** malformed years, **zero** conflicting duplicates.

> **`year` in `discovery.jsonl` is a JSON string** (`"2026"`), not an int. Coerced with `int()` at load.
> This is precisely the bug class PR #573 exists for.

**`discovery-idfix.jsonl` contributed nothing.** Of its 22,901 pmcids, 22,543 were already in the primary
map and 358 were not — and of those 358, **zero** carry a doi or pmid that resolves to a year in
`discovery.jsonl`. The supplementary file widened the join by 0 points. The chunk payloads already carry
`doi` (99.99%) and `pmid` (99.94%) directly, so those fallbacks were also tried in-line and also added 0.

---

## 5. Dry run — the full decision table

Read-only over all 47,625,155 points:

| outcome | points | action |
|---|---:|---|
| **FILL** — no year, source has one | **40,582,256** | write |
| **CORRECT** — year > 2026 | **8,408** | overwrite, prior value ledgered |
| LEAVE_AGREE — stored year == source | 6,464,208 | untouched |
| **LEAVE_DISAGREE** — stored year ≠ source, both plausible | **556,971** | **untouched, reported** |
| LEAVE_NO_SOURCE — plausible year, no source row | 12,450 | untouched |
| UNJOINABLE — no year, no source | 862 | cannot fill |

**Cross-check:** `6,464,208 + 556,971 + 12,450 + 8,408 = 7,042,037` — exactly the independently measured
"has year" count. The partition is complete and consistent.

### The 556,971 disagreements (finding, not an action)

Per the clobber policy these were left alone. The sample suggests two distinct populations, and they are
not equally innocent:

```
stored 2024 vs source 2025     off-by-one, likely epub/print date skew
stored 2023 vs source 2024
stored 1967 vs source 2025     implausible — same accession-matching bug as the future years
stored 1989 vs source 2013
stored 1990 vs source 2020
stored 2000 vs source 2023
```

The off-by-one cases are a legitimate ambiguity. The 1967/1989/1990 cases are almost certainly the *same*
defect that produced the future years — a number scraped from a DOI, ISSN or accession — merely landing in
the past, where the `> 2026` guard cannot see it. **A follow-up pass keyed on "stored year disagrees with
`discovery.jsonl` by more than ~2 years" would likely find several hundred thousand more wrong values.**
That was out of scope here and is left as a recommendation.

### The unjoinable population, with reasons

1,404,452 distinct pmcids in the collection; **385 are absent from `discovery.jsonl`, covering 13,312 chunks**:

* **0 of the 385 are newer than `discovery.jsonl`'s highest id** (`PMC13364717`). This is a **harvest gap,
  not snapshot recency** — the obvious "corpus is older than the collection" explanation is wrong.
* They cluster at the recent end: 251 in `PMC13M–14M`, 99 in `PMC12M–13M`, 35 below `PMC12M`.
* Of the 13,312 chunks, **862 have no stored year** (the true residual gap, 0.0018% of the collection) and
  12,450 already carry a plausible year and need nothing.

After a completed run, 862 points would remain without a year. Closing them needs a second ID-converter
pass for those 385 pmcids.

---

## 6. Disk — the gate that stopped this run

Baseline: collection **909,628,040,668 bytes (847.2 GiB)**, 192 segments, `/rag` 1027 GB free.
`/rag` is **one shared filesystem** — Qdrant `:6333`, `:6343` and Elasticsearch all store on it.

### Measured on this collection

| | transient peak | settled net | reclaim |
|---|---:|---:|---|
| canary, 50,200 pts | **+5.19 GiB** | **+0.85 GiB** | ~2 min, segments 193→192, green |
| cumulative, 253,008 pts | +7.97 GiB | **+3.54 GiB** | ~5 min, segments →193, green |

* **Transient peak: 1.03 GiB per 10,000 points.**
* **Settled net: 0.140 GiB per 10,000 points.**
* Reclaim demonstrably works and is fast, but it does **not** return to zero.

### Free-space series (per the request to report the series, not endpoints)

```
05:17  960G   05:19 1030G   05:22  985G   05:24 1009G   05:33  985G   05:47  994G
```

**`df /rag` oscillates and is useless as my signal** — it swings ±70 GB for reasons unrelated to this job
(see §9: nine collections were deleted by another actor mid-session, releasing space). The trustworthy
measure is `du` on my collection alone, and **that series rises monotonically after settling**:
`+0.85 GiB @ 50k → +3.54 GiB @ 253k`.

### Why this differs from the other two runs

| collection | points | segments | behaviour |
|---|---:|---:|---|
| lucid `lucid_sfr_tok256` | 1.55M | 8 | zero net growth |
| hackathon `asm-semantic` | 6.7M | ~15 | flat, 97.8 → 97 GB |
| **this one** | **47.6M** | **192** | **+0.140 GiB / 10k, accumulating** |

The likely mechanism: with 192 segments averaging 4.4 GB, writes scattered across 48,330 documents
tombstone a few points in *every* segment. At 0.6% of the fill set written, no segment is anywhere near
the `deleted_threshold: 0.2` that triggers vacuum, so nothing is reclaimed — only the new appendable
segments are merged. The smaller collections concentrate their deletions enough to cross that threshold
almost immediately, which is why they stay flat.

**The counter-hypothesis, which I could not test:** once ~20% of the fill set is written (≈8.1M points),
segments begin crossing the threshold, vacuum engages, and growth should flatten or reverse — capping peak
near **110–150 GiB** instead of 565 GiB. The settled rate did drift down (0.17 → 0.140 GiB/10k), weakly
consistent with this. But 253,008 points is 0.6% of the run; that is not enough to extrapolate into a
regime change.

### Projection against the gate

```
remaining points                40,337,656
net at measured 0.140 GiB/10k      ~565 GiB
plus transient                       ~8 GiB
projected peak                     ~573 GiB      >  400 GB gate   -> STOP
free now                            994 GB       -> would land ~421 GB, inside the ±70 GB noise band
```

**Gate 2 fails on the only measurement that describes this collection.** The optimistic mechanism might
well be right, but it is a model, and the downside of being wrong is a full disk on a shared production
instance serving two live tenants.

---

## 7. Canary verification — 15/15 pass

50,200 points = 50,000 fills + 200 corrections, spanning **48,330 distinct documents and 33 year values**
(deliberately scattered, the representative case for segment churn). Ledger written **before** any write.

Throughput: **Qdrant 747 pts/s** (67.2 s), **ES 835 docs/s** (60.2 s), **0 errors**.

```
PASS  qdrant points_count == 47,625,155          (invariant held)
PASS  qdrant status green
PASS  ES doc count == 47,625,155
PASS  300/300 qdrant year+date correct
PASS  year and date are int, not str
PASS  date == year*10000  (derived, never captured separately)
PASS  200/200 documents agree across Qdrant AND ES
PASS  ES year is int
PASS  sibling metadata intact after partial-doc merge (25 fields)
PASS  qdrant int-match year=2019 on canary subset == 2,734
PASS  ES int-term  year=2019 on canary subset == 2,734
PASS  ES int-term  date=20190000 on canary subset == 2,734
PASS  200 corrections: none still > 2026
PASS  200 corrections match discovery year
PASS  ES year>2026 == 8,208  (dropped by exactly 200)
PASS  ES has-year == 7,092,037  (moved by exactly 50,000)
```

`indexed_vectors_count` was **excluded as an invariant** (it moved 48,032,411 → 48,032,224 → 48,082,419 and
already exceeded `points_count` at baseline). `points_count` held at 47,625,155 throughout.

### Retrieval invariance — reported precisely

The harness was **null-tested first**: re-captured with zero writes, 15/15 identical, bit-exact. It is
trustworthy.

**Qdrant — passes.** 9/10 queries bit-identical ids and scores. Query 6 shows three ranks transposed at
positions 23–25, all at score `0.9221523`: a genuine three-way tie, identical id set, identical score
multiset, and **none of the three ids was touched by this job**. Tie ordering only, matching the hackathon
observation.

**ES — result sets identical, scores drift, and the drift is growing.** This does not meet the literal
"identical scores" criterion and I am not going to call it a pass:

| after | max relative drift | rank order preserved | result docs written by me |
|---|---:|---|---:|
| 50,200 writes | 3.35e-05 | 5/5 | **0 / 250** |
| 253,008 writes | **6.32e-04** | **4/5** | **0 / 250** |

The cause is established rather than assumed: **zero of the 250 documents in the result sets were written
by this job**, yet their scores moved. That is BM25 corpus statistics (`df`, `avgdl`) shifting as updated
documents are deleted-and-reinserted and segments merge — inherent to *any* ES document update, not a
change to those documents. Id sets were identical on 5/5 queries at both checkpoints.

The one ordering change was a **genuine near-tie**, not a tie: two documents 1.41e-04 apart
(`29.099155` vs `29.099014`) flipped when drift of comparable magnitude arrived.

**Consequence for a full run, and it is a real one:** drift grew ~19× while writes grew ~5×. At 85% of the
index rewritten it will be substantially larger, and adjacent near-ties will reorder. Result *membership*
should stay stable; *ranking among near-identical scores* will not. Anyone holding cached or golden ES
result orderings for this index should expect them to move.

### Live-tenant impact — no degradation

| probe | pre | post | final (settled) |
|---|---:|---:|---:|
| `:24020` `/health` | 0.9 ms | 0.9 ms | **0.9 ms** |
| ES search (target) | 76.1 | 59.7 | 58.4 |
| ES search (neighbour `sfr_tok256`) | 46.5 | 35.1 | 33.9 |
| Qdrant search (neighbour `sfr_tok256`) | 13.5 | 16.2 | **19.5** |

The live tenant API was flat at 0.9 ms throughout. ES improved (cache warming). **One honest caveat:**
neighbour vector search sits at 19–20 ms against a 13.5–16.5 ms pre-run band, and stayed there 15+ minutes
after writes stopped — a ~3–6 ms elevation, plausibly cache displacement. Below any stop threshold, but it
did not fully return.

---

## 8. State left behind, and how to undo it

**253,008 points written, consistent in both stores.** Verified: ES `has-date` = 253,008 = exactly the
rollback ledger's count; ES `has-year` = 7,294,845 = 7,042,037 + 252,808 fills (the 200 corrections already
had a year). Qdrant green, 193 segments, `points_count` 47,625,155. Writes confirmed *stopped* by three
byte-identical `du` samples 25 s apart with `update_queue` 0 — not merely by a pid being gone.

**Ledgers** (the only rollback — there is no ES snapshot repo and a 47.6M-point Qdrant snapshot is
impractical), all under
`/tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad/oa-year-backfill/`:

| file | contents |
|---|---|
| `canary_ledger.jsonl` | 50,200 entries with prior values, written before any write |
| `run_progress.json` | resumable checkpoint: consumed line offset per plan file |
| `plan/fill_<year>.txt` | 40,582,256 point ids by year |
| `plan/correct_<year>.txt` | 8,408 point ids **with prior year** |
| `monitor.csv`, `run_trend.csv` | 235-sample disk/status series |

### Rollback command

```bash
cd /tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad/oa-year-backfill
python3 rollback.py --dry-run     # prints: 252,808 fills + 200 corrections = 253,008
python3 rollback.py --execute
```

Fills lose `year` and `date` (they had neither); the 200 corrections have their prior year restored from
the ledger and lose `date`. Both stores, resumable via `rollback_done.txt`.

**This was proven, not assumed** — executed against 10 live points: both stores returned to
`(None, None)`, sibling metadata (`pmcid`, `title`, …) intact, then re-applied and re-verified. Confirm a
completed rollback with ES `has-date` returning to 0.

### To resume instead

`run_qdrant.py` skips completed plan offsets and the canary set; `reconcile_es.py` mirrors whatever Qdrant
has into ES via point-id → `chunk_id`, keeping the stores in lockstep. `set_payload` is idempotent, so a
replayed batch is harmless.

---

## 9. Unrelated finding — 9 collections deleted from `:6333` during this session, not by me

At session start `:6333` held 18 collections; it now holds 10. Gone:

```
persist_test                  rag_layout_test               ragstack
ragstack_demo                 ragstack_sfr_semantic         ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe
ragstack_lib_catlle_50genes_...915eb6bf                     ragstack_test_sfr_8_fixed_token_256_32_d877910b
ragstack_test_sfr_8_fixed_token_512_64_4037f431
```

The `:6343` decoy copy of the open-access collection is also gone (404).

**`ragstack_sfr_semantic` is named in my brief as do-not-touch.** It now exists only on `:6343`.

**This was not this job.** Evidence:

* `/rag/data/qdrant/storage/collections` mtime is **04:07:24**; my first write was **05:04:50**, 57 minutes
  later. At 04:07 this job was mid dry-run and had issued nothing but reads.
* The only mutating calls this job ever made were `points/payload` (set) and `points/payload/delete`
  (keys `year`,`date` on explicit point ids) against the target collection, plus ES `_bulk` doc updates and
  one additive `_mapping` PUT. No `DELETE /collections/*` was issued to any instance.

Worth someone's attention on its own, and it is also why `df /rag` was unusable as a trend signal here.

---

## 10. Recommendation

1. **Decide the disk question before resuming.** Either accept a bounded probe — write to ~8.1M points
   (20% of the fill set, ~112 GiB, 6–9 h) and watch for the vacuum regime to engage — or reject the run
   until the collection is reshaped. The probe is safe under the 400 GB floor with ~590 GB of margin and
   would settle the 565 GiB-vs-150 GiB question with data.
2. **Budget realistically.** At the measured 343–747 pts/s, the remaining 40.3M points is **15–33 hours**
   of Qdrant writes plus ~20 h of ES mirroring — 1–2 days paced, not an event-window task.
3. **Investigate the 556,971 disagreements** (§5). The implausible subset looks like the same defect as the
   future years and is 66× larger than it.
4. **A second ID-converter pass** for the 385 missing pmcids would close the residual 862.
5. **Expect ES ranking drift** among near-tied results across a full run (§7). Refresh any golden orderings
   afterwards.
6. **Follow up the `:6333` deletions** (§9) independently.
