# Confirmation run, option (a) — the quarantined setup

*Run 2026-09-08. Implements the mechanical half of
[`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md) §10 item 5
option (a) — "run the confirmation on all 80 under the frozen procedure with the projected
power printed", P.7's closing clause. This document reports **what ran and how much of it**:
counts, throughput, projections, attestations. It reports **no outcome**, because none was
computed.*

> **Read this first — the quarantine.** Every artifact this task produced for a
> confirmation topic lives under `/rag/tmp/stage0-conf/work/conf/`, behind that directory's
> [`QUARANTINE.md`](artifacts/conf-a/QUARANTINE.md). **No aggregate metric over
> confirmation topics was computed, printed, logged or committed.** The rule is enforced in
> code and not only in prose: `s0c_common.QUARANTINE` is `True`, and `eret()`, `epack()`,
> `euc()`, `summarise()` and `forbid_metric()` all raise `QuarantineViolation` while it is.
> There is no code path in the `s0c_*` harness that returns an endpoint value, a ranking, a
> similarity, a fusion score or a rerank score for a confirmation topic. The labeler
> consumes only the pooled `(topic, docno)` id list.
>
> The analysis is blocked by pre-registration until (i) the two-reader human read
> (r3 §5 step 3) has produced κ and (ii) the labels are frozen (r2 §P.9 step 3, where the
> exclusion list is also fixed). **Neither is this task's, and neither has happened.**

---

## 1. What ran, in order

| step | what | module |
|---|---|---|
| 0 | quarantine directory + `QUARANTINE.md` + the guard's self-test | `s0c_common.py` |
| 1 | 160 confirmation queries (80 topics × {`summary`, `description`}) embedded on `:9001-:9006` | `s0c_retrieve.py` |
| 2 | 6 index arms × 3 modes at the served shape, reranked on `:50052`, pools frozen and sha256'd | `s0c_retrieve.py` |
| 3 | 11 scoring arms packed at B ∈ {4,096 / 16,384 / 32,768} SFR tokens; contexts persisted in Stage 0b′'s reference form | `s0c_pack.py`, `s0c_contexts.py` |
| 4 | the labeling set pooled (r2 §6.3) — **counts only** | `s0c_pool.py` |
| 5 | #513's locator fix as a post-filter, verified against #512's reliability on the **development** labels | `s0c_span_filter.py` |
| 6 | the 30-reading labeling pass launched detached and **handed off running** | `s0c_label.py`, `s0c_supervise.sh` |

**The pipeline is Stage 0b′'s, imported rather than copied.** `s0b_bm25`'s two legs,
`s0b_pack`'s walk / groups / parent slice / neighbour de-duplication, `s0b_contexts`' two
persistence forms and `s0b_common`'s constants are used as-is, so a rule that holds at
Stage 0b′ ([`RESULTS-stage0b-prime.md`](RESULTS-stage0b-prime.md), #520) holds here by
construction. The labeling recipe is `s0_label_r31`'s (#507) at the depth #512 measured
usable, and its identity is **asserted byte-for-byte** before the first call.

---

## 2. Counts

### 2.1 Retrieval — 160 queries, 6 arms, 3 modes

| item | value |
|---|---|
| queries | **160** = 80 confirmation topics × 2 variants |
| query embedding | 0.4 s, 10 requests, 0 retries, 21,210 tokens, `:9001-:9006` at ≤ 2 in flight each |
| corpus tokenization (BM25, once, shared by all arms) | 109,496,847 tokens, 621,850 types, 40.9 s |
| query vocabulary scored | 2,181 terms |
| rerank | **93,061 pairs**, 4,459 requests, **0 retries**, 1,128.7 s of `:50052` time at ≤ 4 in flight |
| retrieval wall, six arms | 1,242 s (20.7 min) |

Per arm (chunk count, wall seconds) — these are index properties and timings, not outcomes:
`fixed_tok256_ov0pct` 737,698 / 214 s; `fixed_tok512_ov0pct` 376,516 / 305 s;
`fixed_tok1024_ov0pct` 196,247 / 113 s; `fixed_tok2048_ov0pct` 106,353 / 244 s;
`fixed_tok512` 423,386 / 174 s; `header512` 376,516 / 194 s. Each pool file carries a
sha256 in [`artifacts/conf-a/retrieve_manifest.json`](artifacts/conf-a/retrieve_manifest.json).

The three pools of one `(arm, query)` are reranked **once over their union**, so a chunk
that appears in two modes costs one pair — which is why 93,061 rather than
6 × 3 × 160 × 50 = 144,000.

### 2.2 Packing and contexts

| item | value |
|---|---|
| records | **9,920** = 160 queries × (11 arms × 3 modes − the 2 `multi256+1024` non-vector cells) × 2 rerank states, each carrying all 3 budgets |
| wall | 101.8 s; 67,991 SFR-tokenizer calls |
| reference form | `contexts/packed-conf.jsonl.gz`, 6,608,093 bytes, sha256 `6c424578b4cb3dc6…` |
| materialised text | `contexts/text/<arm>.jsonl.gz` at B = 16,384, `hybrid`, rerank on — 10 arms (`multi256+1024` is vector-only, so it has no `hybrid` cell, exactly as at Stage 0b′) |
| total on disk | 37 MB |

[`artifacts/conf-a/pack_meta.json`](artifacts/conf-a/pack_meta.json) and
[`artifacts/conf-a/contexts-INDEX.json`](artifacts/conf-a/contexts-INDEX.json) carry a
digest per file and the reconstruction recipe.

### 2.3 The labeling set (r2 §6.3) — the headline counts

| | total | per topic: mean / median / min / max |
|---|---|---|
| **pooled** (top-20 documents of every ranking ∩ grade ≥ 1) | **2,962** | 37.0 / 29 / 0 / 143 |
| **bias-bound sample** (10 seeded, from outside the pool) | **776** | 9.7 / 10 / 1 / 10 |
| **TOTAL PAIRS TO LABEL** | **3,738** | 46.7 / 39 / 8 / 153 |
| *memo:* the confirmatory path alone (`hybrid` + rerank on) would have pooled | *1,696* | *21.2 / 17 / 0 / 69* |

Against r2 §6.3's expectation of **≤ 3,600 pooled + ≤ 800 bias-bound**: the pooled half
lands at 2,962 and the sample at 776 — inside the budget the spec sized the run for. The
distribution is in [`artifacts/conf-a/pool_counts.json`](artifacts/conf-a/pool_counts.json).

**What "every scoring arm × variant" was taken to mean, and why.** Stage 0's development
pooling took the top-20 documents of the six index arms' reranked lists × 2 variants — 12
rankings, because Stage 0b had one retrieval mode and one rerank state. Revision 3 §3.4
adds three modes and a rerank-off column and §3.5 four delivery arms plus
`multi256+1024`, and **reports all of them** (hybrid + rerank-on confirmatory, the rest as
pre-registered secondaries with identical tables and CIs). A document outside every pool
can never be packed into any arm's context, so under-pooling would bias the numerator of
the secondaries. `s0c_pool.py` therefore pools over the **76 distinct document rankings any
registered analysis reads**: 6 index arms × 3 modes × 2 rerank states × 2 variants, plus
`multi256+1024` (vector only) × 2 rerank states × 2 variants.

The four delivery arms add nothing to that: a parent slice and a neighbour chunk carry the
source chunk's `docno`, and the packing order is the source arm's. That is **asserted on
144 real `(arm, mode, rerank, query)` cells** by building the arms' actual groups — not
assumed, and not asserted against itself.

Two stated choices inside the pooling:

* **Documents absent from the assembled corpus are excluded** from both the pool and the
  bias-bound draw. A labeler cannot read a document that is not there, and such a document
  can never be packed. 98 of the 12,307 grade-≥ 1 documents across all 90 topics were never
  fetched. On the development set the filter is a **no-op** (0 of 308 pairs), so it changes
  nothing about the calibrated instrument.
* **The bias-bound draw is `s0_label.labeling_set`'s own**: `random.Random(f"{SEED_BIASBOUND}:{topic}")`
  over the sorted out-of-pool relevants, 10 or all remaining. Seven topics have fewer than
  10 pooled documents and one topic has none; three have fewer than 10 out-of-pool
  relevants. Whether such a topic survives r3 §3.8's amended exclusion rule (< 3
  evidence-bearing documents) is **fixed at label freeze**, not here.

### 2.4 The §6.5 window's token counts, precomputed

3,490 distinct documents carry the 3,738 pairs. 3,402 of them are shorter than
`WINDOW_TOKENS` **characters** and therefore cannot exceed `WINDOW_TOKENS` tokens, so they
are recorded as a single window without a call; the remaining 88 were tokenized unit by
unit — 9,142 calls to `mango:8003/tokenize` at ≤ 4 in flight, 37.8 s.

---

## 3. The locator fix (#513) and its verification on the development set

**The defect.** When a quoted span's first and last sentence locate in *different* units,
`s0_label_r3._snap` emits one span per unit the interval touches — filling every
intervening unit **whole**. On the 5 MB compendium document (`2016_1` / PMC4212306, 1,694
units) one model span became 986 emitted spans covering 22,366 sentences.

**The fix, applied as a post-filter** (`s0c_span_filter.py`), identically to the dev labels
and to the confirmation labels this run will produce, so that the instrument is the same on
both sets. It matches `_snap`'s cross-unit signature — consecutive spans in the set's list,
adjacent in the document's unit order, whole-unit interiors, first span running to its
unit's end and last starting at its unit's first sentence — **and** requires the record's
own `vstats["split_across_units"]` to be non-zero. A record with no recorded cross-unit
event is passed through untouched, so the filter can never rewrite a multi-span evidence
set the model itself emitted. A two-span group is already "the two anchored runs": it is
flagged and left alone. #513's blow-up criterion
(`spans_emitted ≥ 10 × max(spans_seen, 1)`) is recorded per record as a flag.

`s0c_span_filter.py --selftest` exercises all of that offline on synthetic unit shapes,
including the four cases where the filter must **not** fire.

**Applied to the committed r3.1-extension development labels** (a **new** directory;
`work/r31ext/` is never written to):

| judge | records | filtered | groups collapsed | fill spans dropped | sentences dropped | blow-ups flagged |
|---|---|---|---|---|---|---|
| scout | 6,160 | 82 | 83 | 1,088 | 25,208 | 1 |
| qwen | 3,080 | **0** | 0 | 0 | 0 | 0 |

Qwen's zero is the independent confirmation of #512's own report (0 `split_across_units`
events in 3,080 readings).

**The verification number.** #512's estimator — `s0_labelgates_r31ext.support_reliability`,
**imported, not re-implemented** — re-read on the same pooled interleaved order
(s0, q0, s1, q1, … then scout 10..19) at n = 30, same seed, same 20 half-splits:

| | split-half r | Spearman–Brown at 30 |
|---|---|---|
| committed (`artifacts/r31ext/gates-r31ext.json`) | 0.8527 | **0.9205** |
| recomputed here, unfiltered | 0.8527 | **0.9205** ✔ reproduces exactly |
| recomputed here, **filtered** | 0.8505 | **0.9192** |

**Δ = −0.0013 against the ±0.01 requirement → PASS.** The instrument is the same on the
development set and on the confirmation set. (For context, #512's own
*exclude-the-blow-up-pair-entirely* sensitivity read 0.9233; the filter keeps the pair and
cleans its spans, which is the smaller intervention and lands between.)

Full record: [`artifacts/conf-a/span-filter-dev-verification.json`](artifacts/conf-a/span-filter-dev-verification.json).

---

## 4. The labeling pass — launched, running, handed off

**The instrument is #507's, asserted rather than described.** Before the first call,
`s0c_label.py` checks the sha256 of the prompt, the system message, the re-prompt and the
rubric against the committed `artifacts/r31ext/label-manifest-r31.json` and stops if any
has moved:

```
prompt   ba09e122553833219a999f8a99c496fd2926b2b341f77026dfe7c6ab2f5131c7
system   f758cbf4d74bf3d419eb263678a5bd436b24454c61cc50a758f421771dcae166
reprompt f5932e0e8f1f488aa48d5593905c67166e82edd68d32a6d25e7313db3a3f4c0a
rubric   2e11f3688de916da8bfc8b5b0a788050bf9d077960d616d33490c6ecf747363b
```

Temperature 0; presentation seed `SEED_LABELDUP + 100*k + pair_index`; ≤ 4 in flight per
endpoint; **Scout × 20 + Qwen × 10 = 30 pooled readings**, the mixture whose reliability
0.92 was measured. Checkpointed per `(topic, docno, presentation)`.

### 4.1 Smoke (20 pairs × 2 presentations × 2 judges), and the projection

| judge | records | wall | s/record | requests | prompt tok | completion tok | retries | failures | truncated |
|---|---|---|---|---|---|---|---|---|---|
| scout (`mango:8003`) | 40 | 49.5 s | **1.238** | 42 | 215,328 | 12,704 | 0 | 0 | 0 |
| qwen (`mango:8004`) | 40 | 206.3 s | **5.158** | 40 | 221,922 | 124,619 | 0 | 0 | 0 |

| judge | records to run | at the smoke rate | at #512's measured dev rate |
|---|---|---|---|
| scout | 3,738 × 20 = **74,760** | 25.7 h | 1.728 s → 35.9 h |
| qwen | 3,738 × 10 = **37,380** | 53.6 h (**2.2 days**) | 7.727 s → 80.2 h (**3.3 days**) |

Both are far inside the task's 10-day stop condition on Qwen, so the run was launched. The
two judges run **concurrently** on separate ports of the same host; the sustained combined
rate observed after launch is in §4.3.

### 4.2 How it was launched, and how to stop it

`s0c_supervise.sh start scout` / `start qwen` launches each judge detached
(`nohup setsid`), restarts it on a non-zero exit — resume is free, because the run skips
every `(topic, docno, presentation)` already on disk — and rewrites a heartbeat every 15
minutes while polling liveness every 15 seconds, so a crash is noticed in seconds and a
hang is visible rather than silent.

**`stop` signals only the pid it recorded, and only after verifying `/proc/<pid>/cwd` is
inside this worktree.** Never by process-name pattern (#402).

**Resume was verified live, not asserted.** Both judges were stopped by recorded pid after
their first minutes and restarted; the new attempts reported `already_done=98` (scout) and
`already_done=21` (qwen) and continued from there, and no `s0c_label.py` process survived
the stop.

### 4.3 Status, as handed off

Measured **after** both judges had been running concurrently for ~12 minutes each — this
is the sustained rate with both on the same host, and it is *faster* than the smoke, so the
two endpoints are not contending:

| judge | records done | target | sustained s/record | projected remaining | projected completion (UTC) |
|---|---|---|---|---|---|
| scout | 748 | 74,760 | **1.157** | **23.8 h** | ≈ 2026-09-09 07:20 |
| qwen | 171 | 37,380 | **4.663** | **48.2 h** | ≈ 2026-09-10 07:45 |

Both are inside the 10-day stop condition by a wide margin. Zero retries, zero failures and
zero truncated responses across the smoke and the first 900 records.

**The command to check status** (safe, read-only, prints counts and liveness only):

```bash
cd /home/wilke/Development/worktrees/confirmation-run/docs/plans/results/stage0
./s0c_supervise.sh status
```

Progress files (rewritten every 25 records by the labeler itself):

```
/rag/tmp/stage0-conf/work/conf/run/progress-scout.json
/rag/tmp/stage0-conf/work/conf/run/progress-qwen.json
/rag/tmp/stage0-conf/work/conf/run/heartbeat-{scout,qwen}.json   (supervisor, every 15 min)
```

Logs: `<worktree>/run/label-{scout,qwen}.log` and `…supervisor.log`.

### 4.4 How to resume, or to finish

The run is idempotent. If a process is gone and the supervisor is not running:

```bash
cd /home/wilke/Development/worktrees/confirmation-run/docs/plans/results/stage0
./s0c_supervise.sh start scout      # picks up exactly where the file stops
./s0c_supervise.sh start qwen
```

When both report `state: finished`:

```bash
python3 s0c_label.py --merge-manifest
python3 s0c_span_filter.py \
  --in  /rag/tmp/stage0-conf/work/conf/labels/labels-conf-scout.jsonl \
  --out /rag/tmp/stage0-conf/work/conf/labels/labels-conf-scout-filtered.jsonl
# and the same for qwen
```

The #513 filter must be applied to the confirmation labels **before** they are used, with
the same code that produced §3's dev verification. It is deliberately not run inside the
labeling loop, so that the raw instrument output stays on disk unmodified.

---

## 5. Deviations, stated

1. **No re-embed in the generator's tokenizer.** r3 §3.3 specifies that the *confirmation
   run* re-chunks and re-embeds so that chunk sizes and budgets are both counted in
   `mango:8003`'s tokenizer (~1.93 fleet-hours). This run reuses the existing SFR-token
   index arms under `/rag/tmp/stage0-conf/emb/` and reads the budgets as SFR tokens — the
   Stage 0b′ path of §3.3. Consequence: the arms are 256/512/1024/2048 **SFR** tokens, not
   generator tokens, and generator-token columns remain the descriptive estimate
   `gen = sfr / 1.2564`. Nothing about this task's outputs depends on it (no metric was
   computed); it is a property of the frozen pools and contexts the analysis will read, and
   it must be stated in the analysis.
2. **The pool is wider than Stage 0's**, for the reason in §2.3 — 76 rankings rather than
   12, because r3 §3.4/§3.5 report three modes, two rerank states and four delivery arms.
   It cost 1,266 extra pairs against the confirmatory-path-only pool and stays inside r2's
   budget.
3. **The §6.5 window's token counts are precomputed** rather than fetched inside the
   labeling loop (§2.4). `s0_label_r31` calls `mango:8003/tokenize` from a process that
   already has four chat requests in flight, which is five in flight to one endpoint; this
   task's budget is four. Precomputing is exactly equivalent — same tokenizer, same
   `add_special_tokens=False`, same per-unit slices — and additionally means the Qwen
   process never contacts `:8003`.
4. **Documents absent from the corpus are excluded from the pool and the bias-bound draw**
   (§2.3). A no-op on the development set.
5. **`multi256+1024` has no materialised-text file**, because it is vector-only (r3 §3.4)
   and the materialised configuration is `hybrid`. Its reference-form records exist for all
   three budgets and both rerank states. This matches Stage 0b′ exactly.

---

## 6. Attestations

**What was read.** The r3 and r2 specs; Stage 0b′'s write-up and harness; the r3.1 and
r3.1-extension write-ups, their harnesses and their **development** label files; #513; the
Stage 0 corpus, units, qrels and chunk/embedding artifacts (non-outcome data, r2 §2.3's
left column); the confirmation topics' **query text** and **qrels counts and grades**
(explicitly permitted, r2 §2.3).

**What was not read.** No ranking, similarity, fusion score, rerank score, packed-context
composition, budget-realised total, or any value of `ERET`, `EPACK` or `EUC` for any
confirmation topic — by any person or agent working on this task. The sequestered §7a /
breadth-k per-topic artifacts (`oracle_results.jsonl`, `lega_gold.json`) were not opened.
No confirmation-topic label was read next to a retrieval outcome; the labeler pipeline
consumed the pooled id list and nothing else.

**What is committed.** Code, and JSON files containing counts, timings, digests and the
development-set verification. **No confirmation-topic artifact is committed.** The pools,
the packed contexts and the labels stay under `/rag/tmp/stage0-conf/work/conf/`.

**Endpoints and hardware.** `:9001-:9006` (≤ 2 in flight each, 160 query embeddings only),
`:50052` (≤ 4 in flight), `mango:8003` and `mango:8004` (≤ 4 in flight each). No store
client — no Qdrant, Elasticsearch, Neo4j or tenant API — is constructed anywhere in the
`s0c_*` harness. GPUs 6 and 7 were not selected and no endpoint was started on them.

---

## 7. What remains blocked

* **The two-reader human read** (r3 §5 step 3 / r2 §6.6, item 8). It is the only instrument
  that can say *which* "where" is right, it gates step 6, and it is 32–48 person-hours of
  human work. Nothing in this task substitutes for it, and no κ appears anywhere above.
* **Label freeze** (r2 §P.9 step 3): the labels hashed, the §8.5.6 / r3 §3.8 exclusion list
  fixed, the PREREG hash recorded. The #513 post-filter must be applied first (§4.4).
* **Unblinding** (r2 §P.9 step 4): only then may the quarantined pools and contexts be
  opened, the §7.6 manipulation checks run, and the confirmatory analysis computed — with
  the **projected power printed beside every contrast**, which is the whole point of option
  (a): Stage 0b′ put joint power at n = 80 at 0.37–0.56 on five of the six contrasts, and
  the standing prohibition applies — *failure to establish a difference must not be
  interpreted as equivalence or used to prune configurations.*
* **Stage 2** (r3 §5 step 7), the serving-path concordance gate on the dev tenant.
