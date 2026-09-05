# Pre-registration — stage 1: the 24-config chunking grid on the Leg A (TREC CDS) pilot set

Written 2026-09-04, **before any embedding call for this run**. (The only compute already
spent is `probe_cost.py`, a CPU-only tokenizer probe that makes no network request — its
numbers appear under *Budget* below and could not be influenced by, nor influence, any
retrieval outcome.)

Repo: `/home/wilke/Development/ragstack`, main at `d225cea`. Grid: the 24 cells of
`python/scripts/eval/chunking_compare_7way.STAGE1_CONFIGS`, imported from that module so
the run cannot drift from the committed definition.

---

## 1. The question stage 1 exists to answer

**Does overlap's effect depend on chunk size?**

This is the reason overlap was made a *fraction* rather than an absolute (`chunking-evaluation.md`
§ "Overlap belongs in the grid"): at a fixed absolute overlap the size effect and a fading
overlap effect move together and cannot be told apart. Making it a fraction removes that
confound but does not answer the question it exposes — whether 12.5% at size 256 buys the
same thing 12.5% at size 2048 buys. If it does, overlap is a single scalar decision that can
be made once and reused across the size ladder. If it does not, size and overlap have to be
chosen jointly, and every future single-size overlap experiment is unreliable.

### 1.1 The quantitative form of "yes" vs "no" — PRIMARY contrast

Let `M(s, f)` be the metric at token-window size `s ∈ {256, 512, 1024, 2048}` and overlap
fraction `f ∈ {0, 0.125, 0.25}`, and `m_t(s, f)` its per-topic value.

Define the **overlap effect at size s**:

```
E(s) = M(s, 0.25) − M(s, 0)
```

and the **primary interaction contrast**, a difference-in-differences over the size extremes:

```
I = E(256) − E(2048)
```

computed per topic (`I_t`) and averaged over the 10 topics, with a paired per-topic bootstrap
(10,000 resamples, seed 7 — the step-3 seed) for the 95% CI.

**Primary condition, fixed now:** `nDCG@10`, grade ≥ 1, `summary` queries, dense retrieval
(no reranking), max-rollup to document. One primary contrast, one metric, one condition.

**Threshold at which we call the interaction real** — the step-3 bar `X`, unchanged so the two
runs are commensurable:

> **|I| ≥ 0.05** *and* the paired-bootstrap 95% CI excludes zero *and* ≥ 7/10 topics agree in
> sign *and* it survives Holm correction across the pre-registered family in §3.

- **"Yes, overlap's effect depends on size"** = all four conditions met. Then overlap must be
  chosen jointly with size, and no single-size overlap result generalises.
- **"No"** = |I| < 0.05 with a CI containing zero. Then overlap is (to this instrument's
  resolution) a size-independent scalar, and stage 2 may carry one overlap decision across
  the ladder. Stated with its power caveat, not as proof of absence (§5).
- **Ambiguous** = |I| ≥ 0.05 but the CI spans zero, or vice versa. Reported as ambiguous. At
  n = 10 this is the *likely* outcome and saying so now is the point of pre-registering.

### 1.2 Secondary form (more powerful, uses all 12 cells)

`I` above throws away sizes 512 and 1024. The secondary form is the **slope of E(s) against
log2(s)**, by ordinary least squares over the four sizes, computed per topic and bootstrapped
the same way. Units: change in the overlap effect per doubling of chunk size. Reported
alongside `I`; it does **not** replace it as primary, because a slope assumes monotonicity in
log-size that nothing guarantees.

### 1.3 Directional prediction, recorded before the run

**We predict `I > 0` but sub-threshold** — the overlap effect is larger at 256 than at 2048,
because a fixed *fraction* still leaves small chunks with many more boundaries per document
(≈ 24 chunks/doc at 256 vs ≈ 3 at 2048 on this corpus), so more evidence spans a cut and
overlap has more to repair. But Leg A's measured bias rewards aboutness-carrying coarse units
(§13.3 of `long-doc-judged-set.md`), and at 2048 a 25% overlap mostly re-embeds text that a
neighbouring chunk already carries whole, so we expect the magnitude to be small — most likely
**0 < I < 0.05**, i.e. the "ambiguous/no" branch at n = 10.

### 1.4 A design-validity note this corpus earns and scifact does not

`chunking-evaluation.md` § "No planned BeIR corpus can power the top of the ladder" measured
that on scifact (median document 354 tokens) **overlap never engages above size 512**: at
2048, 5,182 of 5,183 documents are a single chunk and all three overlap fractions produce the
same 5,184 chunks. `E(2048)` would there be structurally zero, and `I` an artefact of that.

The Leg A pilot's median document is **4,532 SFR tokens** (measured, `probe_cost.py`; mean
6,071), so a 2048-token window yields ~3 chunks for the median document and overlap engages at
every rung of the ladder. **The interaction is measurable on this corpus.** This is recorded
in advance because it is the precondition that makes the primary contrast meaningful; if the
realised chunks/doc at 2048 come out ≈ 1.0, the primary contrast is void and we will say so
instead of interpreting it.

---

## 2. The prior: what step 3 already measured

Step 3 (`../step3/RESULTS-step3-real-experiment.md`), same corpus, same 10 topics, same
metrics, **at 12.5% overlap only**, summary queries, grade ≥ 1, dense:

| metric | tok256/32 | tok512/64 (shipping) | tok2048/256 |
|---|---|---|---|
| nDCG@10 | 0.4952 | 0.4631 | **0.6000** |
| recall@100 | 0.3403 | 0.3349 | **0.3833** |
| MRR@10 | 0.6111 | 0.6750 | **0.8033** |

Load-bearing there: tok2048 − tok512 nDCG@10 **+0.137**, CI [+0.051, +0.225], 8/10 topics;
tok2048 − tok256 recall@100 **+0.043**, CI [+0.015, +0.077], 9/10.

**The ordering is non-monotone**: 2048 > 256 > 512. Step 3 flagged the 256-vs-512 step as
*unresolved at n = 10* — a local reversal inside a coarse-wins trend.

### 2.1 Predictions carried from that prior

- **P1 (replication).** The three grid cells that are literally step-3 configs —
  `fixed_tok256_ov12_5pct`, `fixed_tok512`, `fixed_tok2048_ov12_5pct` — are re-chunked and
  **re-embedded from scratch** (not read from step 3's `.npy`) so this is a real reproduction
  of the extended pipeline. Bar: chunk files byte-identical to step 3's, and every headline
  metric within **±0.01**. A miss is a harness defect and blocks interpretation of everything
  else.
- **P2 (size effect replicates).** Averaged over the three overlap fractions,
  `M̄(2048) − M̄(256)` on nDCG@10 clears X (≥ 0.05, CI excluding zero).
- **P3 — does the fuller grid resolve the 256-vs-512 step?** **We predict it resolves it as
  noise, not as a real reversal.** Concretely: averaged over the three overlap fractions,
  `|M̄(256) − M̄(512)| < 0.05` with a bootstrap CI spanning zero. Tripling the observations per
  size (3 overlap cells instead of 1) does not add topics, so it cannot buy much power against
  between-topic variance — but it does average away the cell-specific noise that a single
  12.5% cell carries, and step 3's own gap (0.032) is already inside X. The falsifying
  outcome, recorded so it counts if it happens: `M̄(256) − M̄(512) ≥ +0.05` with a CI excluding
  zero would mean the non-monotonicity is real and the size ladder is not a ladder.

---

## 3. Multiple comparisons — the named family, fixed before the run

24 configs admit 276 pairwise contrasts. Inference is confined to this family of **9**:

| # | contrast | role |
|---|---|---|
| 1 | `I = E(256) − E(2048)` | **primary** |
| 2 | slope of `E(s)` vs log2 s | secondary form of #1 |
| 3 | overlap main effect `M̄(·,0.25) − M̄(·,0)` (mean over sizes) | overlap, main |
| 4 | overlap main effect `M̄(·,0.125) − M̄(·,0)` | overlap, main |
| 5 | size `M̄(2048) − M̄(256)` (mean over fracs) | P2 |
| 6 | size `M̄(256) − M̄(512)` (mean over fracs) | P3 |
| 7 | `sentence_tok512_ov12_5pct` − `fixed_tok512` | kind |
| 8 | `words_tok512_ov12_5pct` − `fixed_tok512` | kind |
| 9 | `semantic_tok512_ov12_5pct` − `fixed_tok512` | kind |

**Holm–Bonferroni at α = 0.05 across these 9**, on the primary metric/condition
(nDCG@10, grade ≥ 1, summary, dense). Bootstrap p-values are two-sided from the paired
per-topic resample. A contrast is called **resolved** only if it clears X *and* survives Holm.

Everything else — the full 24-row table, all other metrics (`recall@10`, `recall@100`,
`MRR@10`), grade ≥ 2, `description` queries, and every reranked number — is **descriptive**.
It is reported with CIs so a reader can see the spread, and no claim rests on it. Reranked
arms in particular rank arms; they do not grade the product (step 3's label, kept).

---

## 4. What is measured, per config

- `recall@10`, `recall@100`, `nDCG@10`, `MRR@10`, at **grade ≥ 1** and **grade ≥ 2**.
- **chunks/doc** = total chunks ÷ 4,053.
- **realised tokens**: median and p95, SFR tokenizer, counted at chunk time.
- **fill fraction** = median realised tokens ÷ nominal size. Load-bearing: `words` fills
  ~55–62% of its budget and `sentence` ~82% (`chunking-evaluation.md` § "Nominal size is not
  realised size"), so a nominal-size comparison across kinds compares different effective
  sizes. Any kind-vs-kind read is stated against fill, not against nominal size.
- **Embed cost**: tokens and wall seconds per config, cumulative, and achieved tokens/s.

### 4.1 A caveat that travels with every sentence/words row

The sentence/words packer takes its overlap in **chars**, converted at
`OVERLAP_CHARS_PER_TOKEN = 2.5`, while production measures 3.50 chars/token. So the rows
labelled 12.5% carry **≈ 8.9% effective overlap**. The `token_window` rows are exact. This is
repeated next to every kind-vs-kind comparison rather than left in a footnote.

---

## 5. Where n = 10 cannot support a conclusion — stated in advance

Most step-3 CIs spanned zero, and this run has 24 configs on the same 10 topics. Recorded now
so it is not a post-hoc excuse:

- 10 topics is the resolution limit, not the number of configs. Adding cells adds contrasts,
  not power; the paired bootstrap's width is set by between-topic variance in the per-topic
  delta, which no amount of grid refinement reduces.
- A **null on the primary contrast is not evidence of no interaction.** We will report the
  observed per-topic σ_d and the effect size this design could have detected at 80% power, and
  say what a null does and does not license.
- The full 24-row ranking is a **ranking**, not a set of significance claims. Neighbouring rows
  will differ by less than the noise floor and must not be read as ordered.

---

## 6. This run is PROVISIONAL and may not prune the grid

Leg A has a **measured** bias (`long-doc-judged-set.md` §13.3): CDS relevance is
document-level and topical, so the leg rewards coarse, aboutness-carrying configs — exactly
the direction step 3 found. **The coarse-wins result may itself be that bias.** Legs B and C
are deep-evidence by construction, exist to confirm or contradict this direction, and **have
not run**.

Therefore, fixed in advance: this run reports a ranking and an interaction result and
**no config may be dropped from the grid on this evidence alone**. That applies most sharply
to the chunk-size decision, where Leg A's bias points the same way as the cheaper option — the
direction in which a biased instrument does the most damage.

---

## 7. Method — the step-3 harness, extended

The step-3 harness is reused, not rewritten: its zero-store-write design was reviewed and is
deliberate.

- **Corpus**: the same 4,053 fetched CDS judged documents / 10 topics, re-parsed from
  `../step2/xml/` with `ragstack.ingestion.jats.article_prose`. Not re-fetched.
- **Chunking**: the committed `STAGE1_CONFIGS`, imported from
  `python/scripts/eval/chunking_compare_7way.py`, driven through the same repo chunkers the
  7-way harness uses (`chunk_docs_for_config`'s per-kind recipe, including
  `max_tokens=cfg.size` for sentence/words and `**cfg.extra` for semantic so the grid's
  budget reaches semantic's oversized-doc fallback window).
- **Code identity**: `ragstack` is force-resolved to the working copy at `d225cea`. The env
  `/rag/envs/ragstack` carries an editable-install *meta-path finder* pointing at
  `/rag/repos/ragstack` (a **different commit**, 6d6fcf6) which overrides `PYTHONPATH`; the
  finder is removed from `sys.meta_path` in the parent **and re-applied in every multiprocessing
  worker initialiser**, and each script asserts `ragstack.__file__` resolves under the working
  copy before doing anything.
- **Runtime**: `/rag/envs/ragstack/bin/python` with `HF_HOME=/rag/cache` — the only env with a
  loadable `Salesforce/SFR-Embedding-Mistral` tokenizer. #477 made the counter refuse rather
  than silently fall back, so a token-budgeted config would hard-fail elsewhere.
- **Retrieval**: exact brute-force cosine in numpy (fp16 storage, fp32 math) over in-memory
  embeddings. Doc score = max over its chunks. Top-200 docs per query. **No store client is
  constructed anywhere in this harness.**
- **Queries**: the 10 topics' `summary` (primary) and `description` (sensitivity), embedded
  raw with no instruction prefix (the `scripts/search.py` production convention).
- **Rerank arm** (descriptive): top-100 docs' winning chunk texts through `POST :50052/rerank`.
  24 × 20 × 100 ≈ 48k pairs at ~658 pairs/s ≈ 75 s. Not a bottleneck.
- **Safety assertion**: every chunk's realised token count must be ≤ `HARD_CAP_TOKENS` (4080).
  A violation fails the run loudly; nothing is silently capped.

### 7.1 Hard constraints

- **Zero store writes.** Qdrant `:24041` and ES `:24043` collection/index listings **and per-
  collection document counts** are captured before and after and must be byte-identical;
  the comparison is published in the results. `:6333` and `:9200` are never contacted.
- **6 endpoints only** (`:9001`–`:9006`, verified live and idle at launch on GPUs 0–5).
  **GPUs 6 and 7 are reserved: not used, and no new endpoint is started.**
- **≤ 2 in-flight requests per endpoint** (12 global), enforced by a token queue holding each
  endpoint twice — step 3's politeness budget, unchanged.
- **Batching identical to step 3**: ≤ 16 items and ≤ 8,192 estimated tokens per request. Kept
  fixed *because* this run doubles as the check on the 164k tok/s cost model; changing the
  request policy would make the comparison meaningless.
- Nothing is written under `/rag/`. All outputs under `scratchpad/phase0/stage1/`.

### 7.2 Ordering, checkpointing, and interruption

Configs run **cheapest-first at group level**: the 12 `token_window` cells (cheapest, and they
carry the primary question) → the 8 `sentence`/`words` cells → the 4 `semantic` cells (most
expensive by a wide margin, §8). Within the first two groups, ascending **measured** embed
tokens — chunking is CPU-only and runs first, so the exact per-config token count is known
before any embedding call and the ordering is a measurement, not an estimate.

The **semantic group is the one exception**, ordered `2048 → 1024 → 512 → 256` (descending
size) rather than by cost. The buffer cache of §8.1 makes the *first* semantic config pay the
full breakpoint bill whichever it is, and running the uncapped end first means the later three
are almost pure cache hits; the reverse order pays twice. Per-config **notional** cost is
reported unchanged by the ordering, so nothing about the cost model depends on this choice.

Each config writes its results atomically (temp file + rename) on completion; a restart skips
any config whose results file parses. Embeddings are scored immediately and **not** persisted
(24 configs of fp16 vectors would exceed the 31 GB free on this filesystem); the persisted
checkpoint is the per-config run (top-200 docs × 20 queries, with the winning chunk row) plus
its structure/cost statistics, which is everything the metrics and the reranker need.

An interruption therefore loses at most the config in flight.

---

## 8. Expected cost — and a correction to the brief's model

Measured by `probe_cost.py` (CPU only, 60-doc sample, SFR tokenizer, extrapolated):
corpus = **24.6M tokens**, 628k sentences, median document 4,532 tokens.

| group | configs | projected embed tokens | note |
|---|---|---|---|
| `token_window` | 12 | ~376M | ~27M at 0%, ~31M at 12.5%, ~36M at 25%, × 4 sizes |
| `sentence` + `words` | 8 | ~216M | ≈ corpus × ~1.09 (the 8.9% effective overlap) |
| `semantic` | 4 | ~340M **actual** / ~760M **notional** | see below |
| **total** | **24** | **~930M actual** | ~1.35B notional |

At the measured 164k tok/s that is **~1.6 h**; the request-rate ceiling observed in step 3
(~30 batches/s) puts the realistic figure at **~2.2 h** of fleet wall-clock.

### 8.1 The brief's "semantic embeds the text twice, expect roughly double" is wrong — it is ~7×

`semantic` (unlike `semantic_pooled`) runs `pool_sentences=False`, so breakpoint detection
embeds **one overlapping 7-sentence buffer TEXT per sentence** (`_buffer_embeddings`), not one
sentence each. Measured cost of the breakpoint pass alone, per config:

| size | breakpoint items | breakpoint tokens | × corpus |
|---|---|---|---|
| 256 | 628k | 145.1M | **5.9×** |
| 512 | 628k | 169.6M | **6.9×** |
| 1024 | 628k | 170.5M | **6.9×** |
| 2048 | 628k | 170.5M | **6.9×** |

So one semantic config costs ~7× corpus for boundaries **plus** ~1× for its chunks ≈ 8×, and
the four together are ~760M tokens notional — more than the other twenty configs combined.

**Mitigation, and how it is accounted.** Breakpoint buffers are *identical text* across the
four semantic sizes except where `_cap_tokens` truncates them to the config's budget — which
is why the table above is flat from 512 up and only 256 differs. A shared **text → vector
cache** (keyed on the exact post-`_cap_tokens` string, values fp16) is therefore exactly
equivalent: identical input, identical model, identical vector. It cuts the four configs from
~760M to ~340M actually-embedded tokens.

Because this run doubles as the stage-2 cost-model check, **both numbers are reported per
semantic config**: *notional* tokens (what a production ingest of that one config would pay —
the number stage 2 needs) and *actual* tokens embedded after cache hits (what the fleet did).
Throughput is reported against actual; cost projections for stage 2 must use notional. Cache
size ~1M × 4096 × fp16 ≈ 8 GB, affordable on this host (1.5 TB).

Also reported for the semantic arm: the count of documents that exceeded
`max_breakpoint_sentences = 3000` and fell back to `token_window` chunking. Those documents
are not semantically chunked, and a "semantic" row that is quietly part fixed-token would
otherwise misreport what was measured.

### 8.2 The GPU-hour ceiling — both readings, and the one used

The brief says to stop if projected cost exceeds **~3 GPU-hours**. That is ambiguous here:

- **Fleet wall-clock** (the reading used): ~2.2 h projected — under the ceiling. The brief's
  own yardstick, 164k tok/s, is an aggregate wall-clock rate, so this is the reading the
  budget was written against.
- **GPU × hours** (6 endpoints × wall-clock): ~13 GPU×hours. Under this reading even the 20
  non-semantic configs (~10 GPU×hours) would be infeasible, and step 3 itself (~11 min × 6 ≈
  1.1) would have consumed a third of the budget — so it cannot be the intended meaning.

**Proceeding under the wall-clock reading, with the ambiguity declared rather than resolved
silently.** Pre-registered fallback: at each group boundary, if elapsed + projected exceeds
**3 h of fleet wall-clock**, finish the group in flight, stop, and write partial results with
the shortfall named. The group ordering makes that degrade gracefully — the 12 cells carrying
the primary interaction question complete first.

---

## 9. Falsification summary

| # | prediction | falsified if |
|---|---|---|
| P1 | the 3 step-3 cells reproduce | chunk files differ, or any headline metric off by > 0.01 |
| P2 | size effect replicates: `M̄(2048) − M̄(256)` ≥ 0.05, CI excludes 0 | it does not clear X |
| P3 | the 256-vs-512 step resolves as **noise** (\|Δ\| < 0.05, CI spans 0) | \|Δ\| ≥ 0.05 with a CI excluding zero — the reversal is real |
| P4 | interaction `I > 0` but < 0.05 (ambiguous/no branch) | \|I\| ≥ 0.05 clearing CI + sign + Holm (either direction) |
| P5 | overlap engages at 2048 on this corpus (chunks/doc at 2048 ≫ 1) | realised chunks/doc at size 2048 ≈ 1.0 — primary contrast void |
