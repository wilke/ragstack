# PREREG — the breadth × k interaction

**Written and frozen before any new number was computed** — including the CPU-only Leg B
k = 2/3/20 extension, which needs no GPU but would still taint the predictions below if it were
read first. The only numbers quoted here are ones that already existed in
`RESULTS-rescore-small-corpora.md`, `RESULTS-legBC-pilots.md`, or the task brief.

Everything below §2 is a commitment, not a description.

---

## 0. The hypothesis, in one sentence

**Larger `k` helps more with broader questions.** A narrow query has one gold passage: once it is
found, extra `k` adds nothing and the curve saturates. A broad query has relevant material spread
over many passages and documents, so recall should keep climbing with `k`.

---

## 1. Why the obvious test is invalid, and what is run instead

The obvious test is *Leg A curve vs Leg B curve*. It is invalid. Leg A and Leg B differ in at
least six ways at once:

| axis | Leg A (§7a oracle) | Leg B (×0 rung) |
|---|---|---|
| corpus | TREC CDS / PMC OA, 2 095 fetched docs | 400 OA full-text articles |
| topics / queries | 90 CDS clinical case narratives | 396 LLM-written section queries |
| relevants per topic | ~109 judged (≤ 25 sampled here) | **1**, by construction |
| gold provenance | **oracle-derived** — `bge-reranker-v2-m3` argmax over structural units | **recorded by construction** — the section the query was written from |
| gold unit | top-level `<sec>` (structural) | top-level `<sec>` (structural) — same |
| query style | long case narrative (`summary` variant) | short synthetic question |

A naive A-vs-B difference attributes all six to breadth. **This pre-registration therefore does
not make breadth a between-leg variable.** It makes breadth a **manipulated, randomized,
within-Leg-A** variable, and it reports the between-leg difference separately as a single
**lumped, explicitly non-decomposable nuisance term**.

### 1.1 The three terms, named in advance

* **Term A — breadth (IDENTIFIED).** Within Leg A: for each topic, the number of gold
  (document, passage) pairs in play is set to `m ∈ {1, 2, 4, 8, 16}` by **seeded nested
  subsampling of that topic's own qrels**, with corpus size, corpus composition policy, query,
  embedding model, chunker, scorer and gold provenance **held exactly constant**. Nothing varies
  across the ladder except `m`. This is the primary analysis and it is the only place a causal
  reading of breadth is claimed.
* **Term B — leg difference (LUMPED, NOT DECOMPOSABLE).** Leg A at `m = 1`, `N = 100` versus Leg B
  at `m = 1`, `N = 100`. Corpus-size matched by construction. Everything else in the table above
  is inside this one number. It is reported as a *bound on how far a cross-leg reading could be
  wrong*, never as a breadth effect.
* **Term C — gold provenance (NOT IDENTIFIED here).** Leg A's gold is what a cross-encoder chose;
  Leg B's is what a generator recorded. This run **cannot** separate C from the rest of B. Two
  things are done about it and neither is a fix: (i) the direction of its likely bias is named in
  advance in §8, and (ii) a **pre-registered sensitivity** stratifies Leg A by the oracle's own
  `margin_over_head` (§6.4). A measured decomposition would require running the §7a cross-encoder
  over Leg B's documents; `:50052` is outside this run's permitted endpoint range
  (`:9001`–`:9006` only) and is **not contacted**.

---

## 2. What is measured

### 2.1 Leg B — extend `k`, store ranked lists (zero GPU)

The `rescore` harness's per-config chunk spans and embeddings already exist on disk
(`emb_<key>.npy`, `spans_<key>.json`, `stage1-legB/queries.npy`). The k-extension **re-uses them
unchanged** and is pure CPU. Nothing is re-embedded, so no fleet time and no possibility of
drifting from the numbers it must reproduce.

* `k ∈ {1, 2, 3, 5, 10, 20}` for the whole metric family.
* **Ranked lists are persisted**, not only derived metrics: per (config, policy, N, query), the
  top-50 chunk rows with their spans, token counts and scores, plus the fp16 query × chunk
  similarity matrices. Any future `k`, budget, or corpus subset is then a pure re-analysis with
  no GPU cost.

**Reproduction gate (hard, aborts on failure).** The extended harness must recover, to
**≤ 0.0005 absolute**, the Leg B N = 1 topical n = 396 table quoted in the task brief:

| config | Gap@1 | Gap@5 | Gap@10 | PH@1 | PH@5 | PH@10 |
|---|---|---|---|---|---|---|
| tok256 | +0.505 | +0.104 | +0.030 | 0.495 | 0.896 | 0.970 |
| tok512 | +0.467 | +0.093 | +0.005 | 0.533 | 0.907 | 0.995 |
| tok1024 | +0.505 | +0.033 | +0.005 | 0.495 | 0.967 | 0.995 |
| tok2048 | +0.424 | +0.005 | +0.000 | 0.576 | 0.995 | 1.000 |

and the n = 260 accepted `Gap@1` values from `RESULTS-rescore` §3.1 (+0.4308 / +0.3692 / +0.4500 /
+0.3538).

### 2.2 Leg A — score the §7a oracle at passage level (~6.6 GPU-min)

The §7a oracle produced, for **2 161 (topic, document) pairs over 90 CDS topics and 2 095 distinct
documents**, the **argmax structural unit** — the unit the cross-encoder scored highest against the
topic's `summary` query. That is Leg A's passage-level gold and it has never been used for
retrieval scoring.

`oracle_results.jsonl` stores the argmax as a **unit index** (`argmax_i`) and **token** offsets,
not character offsets. `pilot_common.units_for_article` is deterministic, so the character span is
recovered by re-deriving the units and taking `units[argmax_i]["start_char"], ["end_char"]`.

**Gold-recovery gate (hard, aborts on failure).** For **all 2 161 pairs**, the re-derived units
must satisfy `len(units) == n_units`, `[token_offsets(...)] == unit_start_tok`,
`units[argmax_i]["cls"] == argmax_cls`, and `len(doc_text) == doc_chars`. This proves the units
being scored are the same objects the oracle ranked. *(Verified 12/12 on a seeded spot-check
during design; the harness re-runs it on all 2 161.)*

**Chunk-offset gate**, unchanged from `PREREG-rescore` §9.8: every emitted chunk must satisfy
`doc_text[start:end] == chunk.content`.

**Exactly one gold passage per gold document**, so `m` gold documents ⇒ `m` gold passages and the
document- and passage-level denominators are identical. This is what makes the recall forms in
§3.2 directly comparable.

### 2.3 Arms

The **four `token_window` 0 %-overlap sizes** (256 / 512 / 1024 / 2048) plus the **`whole4096`**
one-vector-per-document control — the same five arms as `PREREG-rescore` §3.1, imported from
`chunking_compare_7way.STAGE1_CONFIGS`, never re-declared. **The eight overlap cells are out of
scope**: the question here is `k` × breadth, and the overlap decision is closed.

Dense, exact brute-force cosine. **No reranking.** **Zero store writes** — no Qdrant or
Elasticsearch client is constructed in this harness or anything it imports; `:6333`, `:9200`,
`:24041`, `:24043` are never contacted.

**Query embedding template.** `stage1-legB/legb_grid.py` embeds queries as `fleet.embed([r["query"]
...])` — **raw text, no SFR instruction prefix**. The 90 CDS `summary` queries are embedded the
identical way, so no template difference enters Term B. (Consistency note: the §7a oracle also
selected its argmax with the `summary` variant.)

---

## 3. The breadth ladder — Term A

### 3.1 Topic set

Topics with **≥ 16 sampled gold pairs**, so the full ladder is realisable on every topic and the
topic set is **identical at every rung**. From the observed per-topic counts (69 topics at the
cap of 25; 9 at 24; 5 at 23; one each at 21, 19, 18, 15, 13, 11, 8) this is **n = 86 topics**.
The 4 excluded topics are named in the results.

### 3.2 Corpus

Fixed at **`N = 100` documents** for every topic and every rung — matching Leg B's `N = 100` cell
exactly, which is what makes Term B corpus-size-matched.

* `m` **gold** documents, drawn from that topic's sampled relevants.
* `100 − m` **distractors**, from a **fixed per-topic ordered distractor list**, so the `m = 16`
  corpus's distractors are a subset of the `m = 1` corpus's. Distractors are pool documents that
  are **not relevant to this topic at any grade in the full TREC qrels** (`qrels-treceval-2014/
  2015/2016.txt`, year-aware topic mapping: qrels topic `1` of year 2014 ↔ `2014_1`).
* **`topical` distractor policy (primary):** nearest neighbours, by `whole4096` cosine, of the
  centroid of **all** of the topic's sampled gold vectors. The centroid is computed over the full
  sampled gold set and is therefore **independent of `m`**, so the distractor list does not move
  as the ladder is climbed. **`random` (secondary):** seeded uniform draw. Both are free.
* Seed `20260905`, inherited from `PREREG-rescore`.

### 3.3 Nested subsampling and replicates

Gold subsets are **nested**: `m=1 ⊂ m=2 ⊂ m=4 ⊂ m=8 ⊂ m=16` within a replicate chain, so the
ladder is monotone within a chain and the contrast is paired.

**`R = 8` seeded replicate chains per topic.** At `m = 1` a topic's reading is a single near-binary
draw; averaging `R` chains within the topic before any contrast is the largest available power
lever and it costs **zero GPU** (every cell is a mask over the same similarity matrix).
Replicates are averaged **within topic first**; the **topic is the unit of analysis** and the
clustering unit, exactly as §7a did. Replicates never inflate `n`.

---

## 4. Metrics

Let `G_{t,d}` be the gold passage span for topic `t`, gold document `d`; `m` the rung; `C` the
number of chunks in the mini-corpus; a **hit** = character overlap ≥ **50** (the house constant,
`MIN_OVERLAP_CHARS`).

**Hit forms — kept only for continuity with Leg B, DEGENERATE at `m > 1` (§5.2):**
`PH@k` = ≥ 1 of the top-k chunks hits ≥ 1 gold passage. `DH@k` = ≥ 1 of the top-k documents
(max-rollup) is gold. `Gap@k = DH@k − PH@k`.

**Recall forms — PRIMARY:**

* `PR@k` = (# gold passages `j` hit by ≥ 1 of the top-k chunks) / `m`
* `DR@k` = (# gold documents in the top-k documents by max-rollup) / `m`
* **`GapR@k = DR@k − PR@k`**

> **At `m = 1` the recall forms reduce *exactly* to the hit forms**: `PR@k ≡ PH@k`,
> `DR@k ≡ DH@k`, `GapR@k ≡ Gap@k`. This identity is what licenses the cross-leg anchor of Term B
> and the reproduction gate of §2.1; the harness **asserts** it numerically at `m = 1`.

**Lift over the random-ranking null — the INFERENTIAL form (§5.1 explains why):**

For gold passage `j`, let `h_j` = the number of chunks in the mini-corpus that hit `G_{t,d_j}`.
Under a uniformly random ranking of the `C` chunks,

> `E_rand[PR@k] = (1/m) · Σ_j [ 1 − C(C − h_j, k) / C(C, k) ]`
> `E_rand[DR@k] = k / N`  (uniform draw of `k` of `N = 100` documents)

both **closed-form**, computed per (topic, replicate, `m`, config, `k`). Then

> **`L_P@k = PR@k − E_rand[PR@k]`**,  `L_D@k = DR@k − E_rand[DR@k]`,
> `L_Gap@k = L_D@k − L_P@k`.

**Fixed-budget family (mandatory, §7).** `B ∈ {1024, 4096, 16384}`, admission rule taken verbatim
from `PREREG-rescore` **Addendum A1**: walk the chunk ranking admitting while cumulative tokens
≤ `B`, **with the top-ranked chunk always admitted** even if it alone exceeds `B`. Report the
realised token total `ntok_B` and the realised chunk count `k_B` beside every value. Metrics
`PR_B@B`, `DR_B@B`, `H_B@B`, `GapR_B@B`, and the lift `L_{P,B}@B` evaluated at the **realised**
`k_B` — i.e. the same closed-form null, at the number of chunks the budget actually bought.

---

## 5. The two arithmetic traps, declared before the run

**These would manufacture the hypothesized interaction if left untreated.** Both are arithmetic,
not measurement.

### 5.1 The `k/m` ceiling

`k` chunks can hit at most `k` distinct gold passages, so **`PR@k ≤ min(1, k/m)`**, and likewise
`DR@k ≤ min(1, k/m)`. Consequences, stated now:

* At `m = 16`, `PR@1 ≤ 0.0625` and `PR@5 ≤ 0.3125` **by arithmetic**. A raw
  `[PR@20 − PR@5]_{m=16} − [PR@20 − PR@5]_{m=1}` is therefore **near-forced positive**: the
  `m = 1` arm is capped by its already-measured saturation (`PH@5 ≈ 0.94`, headroom ≤ 0.06) while
  the `m = 16` arm has 0.69 of ceiling headroom it must climb.
* Symmetrically, `GapR@1` at `m = 16` is pinned near 0 (both terms ≤ 0.0625) while Leg B's
  `Gap@1 ≈ +0.43`. "The gap narrows with breadth at small `k`" would be **partly arithmetic**.

**Therefore: every inferential contrast in this pre-registration is stated on the lift `L`, never
on raw `PR`/`GapR`.** The lift subtracts the random-ranking expectation, which carries the same
`k/m` ceiling, the same `C`, and the same corpus composition. Raw `PR@k` and `GapR@k` curves are
reported as the **practitioner table** — a practitioner does not care that recovered material is
"arithmetic" — **explicitly labelled descriptive**, with the random-null curve overlaid so the
arithmetic component is visible in the same figure. House precedent: `RESULTS-rescore` §4.5's
random-chunk lift, which was post-hoc there and is **pre-registered** here.

### 5.2 Saturation and the proportion-at-1.0 hazard

**Pre-enumerated degeneracies. Any of these appearing in a table is labelled DEGENERATE and is
never read as a finding.**

1. **`PH@k`/`DH@k` at `m > 1` saturate by ceiling.** With 16 gold documents in a 100-document
   corpus, "≥ 1 gold in the top-k" is ≈ 1 for any `k ≥ 2`. The hit forms are reported at `m > 1`
   for continuity only; **all `m > 1` inference is on the recall forms.**
2. **Leg B, `N = 1`, `k = 20`.** Realised chunks/doc are 38.3 / 19.4 / 9.9 / 5.2 for
   256 / 512 / 1024 / 2048, so at `N = 1` the top-20 **is the whole document** for tok1024 and
   tok2048 (and for tok512 on any document below the mean). `PH@20 ≡ 1.0`, `Gap@20 ≡ 0` by
   arithmetic there. The harness counts chunks per document and marks each cell DEGENERATE
   exactly. **The curve-shape analysis lives at `N = 100`, not `N = 1`.** (`PH@10` at `N = 1` is
   already degenerate for tok2048 — flagged in `RESULTS-rescore` §9.)
3. **A proportion at exactly 0 or 1 has a Wald SE of zero and a falsely "resolvable" δ80.**
   Carried over verbatim from `PREREG-stage1-legB` §4.2 and `PREREG-rescore` §4.4. **Several Leg B
   `PH@10` values are at or near 1.000, so this is live here.** Any such proportion is read on its
   **Wilson** interval; **no δ80 is ever quoted from a Wald SE**; the row is written
   **unresolvable by construction**.
4. **`whole4096` cannot contain most gold passages.** One unit per document, truncated at 4 080
   tokens. Its passage numbers are largely arithmetic and are reported as a **bound**, not a
   comparison — as in `PREREG-rescore` §4.3.
5. **Unjudged-relevant contamination.** A distractor is "non-relevant" per the full TREC qrels,
   which are pooled and incomplete. This is a real nuisance; it is **`m`-invariant by
   construction** (the distractor list is fixed per topic across the whole ladder), so it cannot
   generate a breadth × k interaction. Declared, bounded, not corrected.

---

## 6. The contrasts, and the δ80 power floor of every threshold to be read

### 6.1 The convention, unchanged

`δ80 = (z_{0.025} + z_{0.20}) · σ_d / √n = 2.802 · σ_d / √n`, identical to `PREREG-stage1-legB` §4
and `PREREG-rescore` §5.

**Two rules, pre-committed:**
1. **δ80 is computed from this run's own paired differences, before the threshold is read.**
2. **A row whose δ80 exceeds the distance it must travel is written UNRESOLVED, never "null."**

### 6.2 The analytic resolvability bound, stated in advance

| leg | unit of analysis | `n` | δ80 | resolvable at bar `b` ⟺ | paired-binary discordance limit |
|---|---|---:|---|---|---|
| **Leg A** | **topic** (clustering unit; replicates averaged within topic first) | **86** | `0.3022·σ_d` | `σ_d ≤ 3.310·b` | `d ≤ 10.96·b²` |
| Leg B | query | 396 | `0.1408·σ_d` | `σ_d ≤ 7.102·b` | `d ≤ 50.44·b²` |
| Leg B (accepted) | query | 260 | `0.1738·σ_d` | `σ_d ≤ 5.755·b` | `d ≤ 33.12·b²` |

**Leg A's `n = 86` topics is the binding power constraint of this entire run, and it is stated
before any reading.** At the house bar `X_P = 0.05`, a paired **binary** Leg A contrast resolves
only if fewer than **2.7 %** of topics disagree (2.4 of 86). Binary Leg A contrasts are therefore
expected to be mostly UNRESOLVED and are pre-declared as such. The primary contrasts of §6.3 are
**continuous** (lifts averaged over 8 replicates), for which `σ_d` is far smaller — that is
precisely why the replicate averaging of §3.3 is in the design.

Leg A CIs are **topic-clustered bootstrap** (10 000 resamples over topics), as §7a did, with the
pooled Wilson interval shown beside it and labelled the over-confident one.

### 6.3 The contrasts, pre-specified

**Bars, chosen before the data:** `X_I = 0.05` on every lift-based contrast (the house passage-level
bar from `PREREG-rescore` §5, same justification: 5 points is the smallest difference a
practitioner would re-tune `top_k` for). A sensitivity column at `0.10` is reported and labelled.

| # | contrast | definition | reads |
|---|---|---|---|
| **I1** | **PRIMARY — breadth × k on passage-recall lift** | `I1 = [L_P@20 − L_P@5]_{m=16} − [L_P@20 − L_P@5]_{m=1}`, paired per topic | **`I1 > 0` ⇒ larger `k` helps more with broader questions.** This is *the* deliverable. |
| **I2** | breadth × k on the gap | `I2 = [GapR@5 − GapR@20]_{m=16} − [...]_{m=1}` (lift form `L_Gap` reported beside it) | whether the *shape* of the Gap@k curve differs by breadth |
| **I3** | the full ladder, not two ends | `[L_P@20 − L_P@5]` at each `m ∈ {1,2,4,8,16}`; monotonicity in `m` by Spearman ρ over the 5 rungs, topic-clustered | a saturation point, or its absence |
| **I4** | **the fixed-budget form of I1** | `I4 = [L_{P,B}@16384 − L_{P,B}@4096]_{m=16} − [...]_{m=1}` | the only cross-chunk-size-fair version of the interaction (§7) |
| **B1** | **Term B, the lumped leg difference** | Leg A `m=1, N=100` minus Leg B `m=1, N=100`, on `Gap@1`, `Gap@5`, `Gap@10`, `PH@k`, per config | a **bound** on how wrong a naive cross-leg breadth reading could be. **Never reported as a breadth effect.** |
| **S1** | saturation point | smallest `k ∈ {1,2,3,5,10,20}` with `PR@k ≥ 0.9 · PR@20`, per `m`; and the same on `L_P` | the practitioner-facing `top_k` answer |

Holm correction across the 5-rung I3 family and across the 4 chunk sizes; **not** applied to I1,
I2, I4, which are single pre-specified contrasts.

**A contrast is `RESOLVED` iff** `|mean| ≥ bar` **and** the 95 % CI excludes 0 **and**
`δ80 ≤ |mean|`. Anything else is `UNRESOLVED` (if `δ80 >` the distance) or a **powered NULL** (if
`δ80 ≤ bar` and the effect is below the bar). **Unreachable thresholds are reported as
unresolved, never as nulls.** In this study a pre-registered contrast has already turned out 4×
too coarse to answer its own question, and 6 of 15 gated readings in another round were
unresolvable; that is the expected failure mode here and it is declared, not feared.

### 6.4 Pre-registered sensitivities

* **S-margin (Term C handle).** Split Leg A gold pairs at the median `margin_over_head` (the
  oracle's own confidence, recorded per pair; median +0.163 from §7a). Re-run I1 on the
  high-margin half. If the interaction is a gold-quality artefact it should move; if it is
  breadth it should not. **This is a handle, not a decomposition.**
* **S-policy.** `random` vs `topical` distractors.
* **S-size.** All four chunk sizes; the interaction must not depend on the arm.
* **S-legB-N.** Leg B `N ∈ {1, 10, 100, 400}` — the k-curve at every corpus size already on disk.

---

## 7. Fixed-budget reading — mandatory

At fixed `k`, ten 2 048-token chunks is **8×** the context of ten 256-token chunks. Any `k`
comparison **across chunk sizes** is therefore unfair unless budget-matched. Pre-committed:

* **Every conclusion states which reading it rests on.** Conclusions **across chunk sizes** rest
  on the fixed-budget family (`B ∈ {1024, 4096, 16384}`). Conclusions **within one chunk size**
  (which is where I1–I4 live, since the breadth ladder is run inside each arm) may rest on
  fixed-`k`, and this is legitimate precisely because the arm is held constant.
* **Pre-registered expectation:** the fixed-`k` advantage of large chunks **shrinks or reverses**
  under budget matching. `RESULTS-rescore` §4.2 already found exactly this at `m = 1`
  (`H_B@4096`: tok256 0.9962 → tok2048 0.8154, the reverse of the raw `PH@1` ordering). This run
  **checks it holds across the breadth ladder** and reports it if it does not.
* `B = 1024` carries the Addendum A1 caveat: `tok2048` receives 2 048 tokens against everyone
  else's 1 024. `B = 4096` and `B = 16384` are exact multiples of all four sizes and are the
  clean readings.

---

## 8. Predictions, to be scored

Recorded so they can be falsified. A wrong prediction is **reported**, not quietly dropped — as
P1 and P2 of `PREREG-rescore` were.

| # | prediction |
|---|---|
| **P1** | **I1 > 0 and RESOLVED** — the passage-recall lift gains more between `k=5` and `k=20` at `m=16` than at `m=1`. |
| **P2** | `I3` is **monotone increasing in `m`** (Spearman ρ > 0 over the five rungs). |
| **P3** | At `m = 1`, `L_P@20 − L_P@5` is **≤ 0.05** — narrow queries saturate by `k = 5`, so there is little left for `k = 20` to win. |
| **P4** | **S1: the saturation point rises with breadth** — `k* = 1–3` at `m = 1`, `k* ≥ 10` at `m = 16`. |
| **P5** | **Term B is large** — `|B1|` on `Gap@1` exceeds `X_I = 0.05` for at least three of the four configs, i.e. the naive cross-leg comparison would have been materially confounded. This is a prediction that the *confound is real*, not that breadth is. |
| **P6** | `I2` (the gap-shape interaction) **does not resolve** at `n = 86` — `Gap@k` is a difference of two saturating quantities and is the noisier instrument. Pre-registering an expected non-resolution so that reporting it is not a retreat. |
| **P7** | **The fixed-budget ordering reverses the fixed-`k` ordering across chunk sizes at every `m`** — tok256 best on `H_B@4096`, tok2048 best or near-best on raw `PH@1`. |
| **P8** | Leg A `m=1` `PH@1` **exceeds** Leg B `m=1` `PH@1` — because Leg A's gold was chosen by a neural cross-encoder and is scored by a neural retriever, and two correlated neural rankers agreeing flatters the passage metric. **Named in advance as the likely direction of Term C's bias.** |

---

## 9. Cost, and the stop rule

| item | tokens | projected fleet time |
|---|---:|---:|
| Leg B k-extension | **0** (re-uses `emb_*.npy`) | **0** |
| Leg A, 4 `token_window` cells × 2 095 docs | 4 × 15.334 M = **61.34 M** | ≈ 5.9 min |
| Leg A `whole4096` doc vectors | ≈ **7.5 M** | ≈ 0.7 min |
| Leg A 90 `summary` queries | ≈ 0.02 M | ≈ 0 |
| **total** | **≈ 68.9 M** | **≈ 6.6 GPU-minutes** |

Scaled from the measured `RESULTS-rescore` rate (54.9 M tokens → 5.22 GPU-min). Ceiling
**45 GPU-minutes**; projection is **14.7 %** of it. Scoring is CPU-only.

**Stop rule:** if measured fleet time passes **20 minutes** with cells outstanding, stop and
report the partial grid. The per-cell embedding artifact is the checkpoint. **If the projection
had exceeded 45 GPU-minutes the run would not start.**

**Fleet politeness:** `stage1_common.Fleet`, **≤ 2 in flight per endpoint**, `:9001`–`:9006`
**only**. **GPUs 6 and 7 are RESERVED**: no endpoint is started, none outside `:9001`–`:9006` is
contacted, and `:50052` — used by the §7a oracle in an earlier round — is **not** contacted in
this run. `nvidia-smi` recorded before and after.

---

## 10. Provenance, as a hard gate

Written to `provenance-breadth-k.json` before the first embedding call; the harness **aborts** if
any check fails.

1. **The commit discrepancy, resolved before freezing.** The task brief names `main 55a0fc2`. The
   local checkout is at **`d225cea`**, and `55a0fc2` is on `origin/main`, **one commit ahead and
   not an ancestor** of the local HEAD. **This run executes at `d225cea`**, because every reused
   artifact pins it (`emb_*.npy`, `spans_*.json`, `queries.npy`, the `rescore` harness gate, the
   §7a oracle output). Both hashes are recorded.
   The brief notes `55a0fc2` changed the default word/sentence fill. **Verified, not assumed:**
   `FixedTokenWindowChunker` is **byte-identical** at the two commits
   (`sha256(class source) = 15309b8e0e3a53c0…` at both) and contains no reference to
   `budget_mode`; the `55a0fc2` diff touches only the sentence/word packers, their tests, and a
   doc. **This run is `token_window` only and is therefore unaffected** — now a proven statement.
2. `stage1_common.pin_repo()` executed, and `ragstack.__file__` asserted to start with
   `/home/wilke/Development/ragstack/python`. **The `/rag/envs/ragstack` environment carries an
   editable-install meta-path finder pointing at `/rag/repos/ragstack` (a different commit); a
   meta-path finder runs before `sys.path`, so `PYTHONPATH` does not win.** The resolved path and
   the surviving `sys.meta_path` are recorded, so the reader can see which code actually ran.
3. The **served** model id from a live `GET /v1/models` on each of the six endpoints, recorded
   verbatim; the harness refuses to run if they differ from each other.
4. `sha256` of the sorted Leg A document list, **computed before embedding**; likewise the Leg B
   400-file list, which must equal `d83c7fe1399b92ab394bd914f22caac689e85913bd73bbd2e9713973a6e56b34`.
5. Seeds: `20260905` (distractors, gold subsampling, replicate chains); the Leg B and §7a seeds
   inherited unchanged (`20260904` for the oracle sample).
6. Python and package versions (`numpy`, `transformers`, `httpx`, `tokenizers`).
7. **Gold-recovery gate** (§2.2) over all 2 161 pairs.
8. **Chunk-offset gate** over every emitted chunk.
9. `nvidia-smi` before and after, showing GPUs 6 and 7 untouched.
10. **Store gate:** `:6333`, `:9200`, `:24041`, `:24043` never contacted; no store client
    constructed. A grep of the harness's imports is recorded. `:24041`/`:24043` are not touched at
    all, so there is nothing to verify unchanged.

---

## 11. What this run cannot answer

Stated in advance so a null is not over-read.

* **It cannot decompose Term C** (oracle-derived vs recorded gold) from the rest of Term B. §6.4's
  margin split is a sensitivity, not an identification. Anything that survives only in a cross-leg
  comparison is reported as **unattributable**.
* **Leg A's realised breadth tops out at ~24, not ~109.** The §7a sample capped relevants at 25
  per topic, and only those documents were fetched. The ladder therefore spans `m = 1 … 16`, and
  **nothing is claimed about `m` in the hundreds.**
* **Nothing about reranking.** Dense only. This matters: `RESULTS-rescore` §10.6 identified
  reranking as the one intervention predicted to move the top-1 gap, and it remains untested.
* **Nothing about answer quality.** A chunk overlapping the gold passage is not a chunk that
  answers the question. For Leg A the gold passage is *what a cross-encoder ranked highest*, which
  is weaker still than Leg B's "the section the query was written from".
* **Nothing about non-biomedical corpora**, and nothing about corpus sizes other than
  `N = 100` for Leg A (Leg B carries `N ∈ {1, 10, 100, 400}` from the existing run).
* **`n = 86` topics is the power ceiling** for every Leg A reading, and no amount of replicate
  averaging changes it.
