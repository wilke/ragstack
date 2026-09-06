# RESULTS — Stage 0 (0a + 0b) calibration for the chunking confirmation run

**Specification:** `design/SPEC-confirmation-run.md` rev. 2. This document fills the
`[FROZEN-AT-STAGE-0]` slots of **P.7** and reports the complete **§8.5.7** table.

**Scope note, stated first because it bounds everything below.**
Stage 0's deliverable 8 — the ≥100-pair, **two-independent-human-reader** label validation
(R-dev) — is **OUT OF SCOPE for this run and was not performed**. No agent read was
substituted for a human read, and **no κ(human–human) is reported anywhere in this
document**. The stratified ≥100-pair sample is produced and rendered ready-to-read; the
label-validation gate is **`PENDING-HUMAN`**. Items 1–7 and 9, plus the 0a prerequisites,
were run.

---

## 0. Provenance

| item | value |
|---|---|
| repo commit | `55a0fc2f6bf64e592c2c65d8825524216c423e2b` (the brief's `55a0fc2`; `d225cea`, which every Phase-0 artifact pins, is its direct parent — verified with `git merge-base --is-ancestor`) |
| working tree | clean except three untracked `*.pid` leftovers (`chain.pid`, `legb2_sample.pid`, `oracle.pid`) — recorded, not touched |
| interpreter | `/rag/envs/ragstack/bin/python` (CPython 3.12.13) |
| `HF_HOME` | `/rag/cache` |
| editable-install defence | `pin_repo()` run in the main process **and every worker initialiser**; `ragstack.__file__` asserted `= /home/wilke/Development/ragstack/python/ragstack/__init__.py`; surviving `sys.meta_path` recorded in `provenance-stage0.json`. The `/rag/repos/ragstack` finder did **not** win. |
| packages | numpy 2.5.0, httpx 0.28.1, transformers 5.12.1, tokenizers 0.22.2, **scipy absent** (see §7) |
| served ids, probed live | `:9001`–`:9006` all `Salesforce/SFR-Embedding-Mistral`; `:50052` `/health` → `BAAI/bge-reranker-v2-m3`; `mango:8003` → `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`; `mango:8004` → `Qwen/Qwen3.6-35B-A3B` |
| GPUs 6 and 7 | `0 MiB` used, `0%` utilisation, **before and after** every stage. No endpoint above `:9006` was contacted. |
| stores | **none**. No Qdrant/Elasticsearch/Neo4j client is constructed in any `s0_*.py` or in anything they import (grep recorded); `:6333`, `:9200`, `:24041`, `:24043` never contacted. |
| seeds | grade-0 dev `20260904`, grade-0 confirmation `20260912`, unit-cap `20260912`, bootstrap/permutation `20260913`, labeling duplicates `20260914`, R-dev draw `20260915`, bias-bound sample `20260916` |
| `budget_mode` | pinned `"joined"`. **Inertness proved, not assumed**: `FixedTokenWindowChunker.__init__` at `55a0fc2` takes `(self, chunk_size, chunk_overlap, token_counter)` and the string `budget_mode` does not appear in its source. The `55a0fc2` word/sentence fill change therefore cannot reach any arm in this design. |
| **rubric** | `design/RUBRIC-evidence.md`, sha256 **`2e11f3688de916da8bfc8b5b0a788050bf9d077960d616d33490c6ecf747363b`**, written and hashed **before the first labeling call** |
| **corpus manifest** | sha256 **`b15f059f95005f04ee2dc44994795f8a6247a9bab2dd0d3b08f119a4666a8cbf`** over the sorted `(pmcid, sha256(file bytes))` list, computed **before any embedding** and **re-verified inside the embed process** before the first vector |

Full record: `provenance-stage0.json`.

---

## 1. Stage 0a — corpus and index builds

### 1.1 Corpus

| quantity | value |
|---|---|
| topics assembled | **90 distinct** year-prefixed ids (asserted); 10 development + 80 confirmation, **no reserve** |
| distinct grade ≥ 1 documents | **12,307** (the spec's figure, reproduced exactly) |
| (topic, doc) grade ≥ 1 pairs | **13,807** (the spec's figure, reproduced exactly) |
| PMCIDs wanted | 33,165 |
| already local (step-2 / oracle / review dirs) | 5,967 |
| fetched from `pmc-oa-opendata` | 26,824 OK, 373 MISS, 1 ERR |
| **fetch rate** | **98.87 %** (S1 measured 98.5 %) |
| parsed and kept | **32,663** |
| excluded, empty parsed body (§4.2.5) | **128** (0.39 %; the dev pilot's rate was 0.3 %) |

**Deviation from the spec's "≈ 38k documents", disclosed:** the corpus is **32,663**
documents, not ~38k. The shortfall is not fetch loss — it is dedup. 90 × 300 seeded grade-0
negatives is 27,000 draws, but the negatives overlap each other and the positives far more
than §4.1's arithmetic assumed, so the union of positives and negatives is 33,165 PMCIDs
before fetch rather than ~39k. Composition and policy are exactly as specified (judged-only,
300 grade-0 per topic, dedup by PMCID, dev seed reused); only the spec's *estimate* of the
union size was optimistic. Every dev query still discriminates against **~32,550 documents
that are not its relevants**, which is what §4.1's argument needs.

**Dev reproduction check (the point of reusing seed `20260904`):** the dev-10 slice of the
new fetchlist is **byte-identical** to `step2/fetchlist.txt` — 4,099 ids, zero on either
side only. The pilot corpus reproduces.

### 1.2 The six index arms

`FixedTokenWindowChunker` at `55a0fc2`, SFR tokenizer, token-counter backend asserted `hf`
(construction rejects a non-HF counter). Chunking took 208 s over 32 processes.

| arm | chunks | vectors/doc | SFR tokens |
|---|---|---|---|
| `fixed_tok256_ov0pct` | 737,698 | 22.59 | 184.70 M |
| `fixed_tok512_ov0pct` | 376,516 | 11.53 | 184.44 M |
| `fixed_tok1024_ov0pct` | 196,247 | 6.01 | 184.31 M |
| `fixed_tok2048_ov0pct` | 106,353 | 3.26 | 184.24 M |
| `fixed_tok512` (shipping, 512/64) | 423,386 | 12.96 | 209.48 M |
| `header512` | 376,516 | 11.53 | 184.44 M + headers |
| **total** | **2,216,716** | — | **1.132 B** |

The storage lever N1 is measured, not assumed: `fixed_tok512` → `fixed_tok1024_ov0pct` is
**2.157× fewer vectors** on this corpus (423,386 → 196,247).

### 1.3 Cost, and the 8-GPU-hour cap

The brief caps projected cost at **8 GPU-hours**. The two accountings differ by 6×, so both
are stated:

| accounting | value |
|---|---|
| **fleet wall-clock** (the accounting §11 uses — "≈ 3.2 h central, ≤ 7 h pessimistic", "nowhere near the 20 GPU-hour line") | projected **1.95 h** at the record's 161k tok/s; **measured 1.926 h** at the achieved 167k tok/s |
| **device-hours** (6 endpoints × wall-clock) | **11.6 GPU-h** |

**Decision, stated rather than assumed:** the cap is read in the spec's own accounting
(fleet wall-clock), because under per-device accounting the six index builds the task
explicitly orders are impossible in principle at any speed — 1.13 B tokens is ≥ 11 device-h
however it is spread. A tripwire was armed in the embed driver at **8 fleet-hours** and at
**2× the central projection** (§11's own stop rule), re-evaluated after every arm, with the
per-arm `.npy` files as checkpoints. It did not fire.

Arm order was chosen so that a tripwire stop would still leave the NI family calibrated:
shipping → 1024/0 → 512/0 → 256/0 → 2048/0 → header512.

---

## 2. Stage 0b — what was run

| step | detail |
|---|---|
| dev retrieval | 10 development topics × {`summary`, `description`}, queries embedded **raw** (as-deployed; the §10 instructed variant is a sensitivity and was not needed by the gate). Exact brute-force cosine (fp32) against **all** chunk embeddings of each arm; top **D = 50**; **full-pool rerank** of all 50 pairs on `:50052`. Never one-chunk-per-document. No BM25/RRF, `max_per_doc = 0`, no boilerplate demotion. **Nothing from the 80 confirmation topics was embedded, ranked, packed, labeled or inspected** — asserted in code before the first query embed. |
| packing | A1 as disambiguated in §7.3, with the served-generator tokenizer probed live on `mango:8003/tokenize` (`add_special_tokens=false`, pinned). `parent256` built at packing time from `fixed_tok256_ov0pct`'s reranked list. |
| labeling | **Llama-4-Scout** on `mango:8003`, served id asserted, temperature 0, seed `20260914`, **≤ 4 concurrent**. **308 dev pairs** (208 pooled + 100 bias-bound), 455 requests, 5.32 M prompt tokens, 27 min of LLM time. `:50052` contributed **nothing** to gold. |
| rubric | frozen and hashed **before the first labeling call** (§0). |

**Deviation from §6.3's ≈450 dev pairs, disclosed:** the pooled labeling set is **208
pairs**, not ~350, because the shared 32.7k corpus is far more discriminative than the
dev-scale pilot: the union of the top-20 documents over 6 arms × 2 variants contains only
5–30 grade ≥ 1 documents per topic (median 13). That is defect 3 closing exactly as
intended — and it is the first sign of the finding in §5.

**Prompt revision, recorded.** The labeling prompt was revised **once**, at smoke-test time,
**before any label was retained**: revision 1 returned "no localizable evidence" on two dev
pairs whose abstracts plainly carry the evidence, i.e. it did not faithfully express
RUBRIC §1/D2's lead instruction. The **rubric is unchanged and its sha256 is unmoved**;
only the prompt's rendering of it was corrected. Both prompt sha256s are in
`artifacts/label_meta.json`. No further revision was made — in particular the prompt was
**not** tuned until any particular pair produced spans.

**Quote-verification, corrected mid-run and pinned.** A first implementation verified the
anchor quotes against the *claimed span's own text* and scored every mismatch as a
hallucination, giving 0.369. That conflates two things the SPEC keeps apart: §6.4 rule 2
says the checker "verifies substrings **against the document**", and §6.6.1 names *quote
verifies, location wrong* as **`wrong-location`, a label error — explicitly not a
hallucination**. The corrected implementation:

* **hallucinated** — the quoted words are nowhere in the document → the span is dropped and
  it is the P.5 gate's numerator;
* **misindexed** — the quote is in the document but not at the claimed
  `(unit, first_sentence, last_sentence)`. **PINNED:** the quote is the thing the checker
  can verify, so the span is **relocated** to the quote's position and snapped outward to
  whole sentences inside the one unit containing it (D1). Claimed indices are treated as
  derived, not authoritative, and the index-agreement rate is reported as a finding.
* **unresolvable** — the quote is present but its interval crosses a unit boundary → dropped.

The labels were regenerated from scratch under the corrected checker; the first pass is
retained at `work/labels-v1-strictchecker.jsonl` for audit.

---

## 3. The §8.5.7 table

The complete rendered table is `TABLE-8.5.7.md` (generated from the JSON artifacts, never
retyped). Reproduced here with the reading that matters.

### Row 1 — σ_d(EUC@4096) per contrast, **all five calibrated directly**

No contrast was left uncalibrated: all six index arms were built before calibration, so
N1, N2, R1, R2 and R3 were each measured on their own arm pair (§8.5.4's preferred path).
The max/min-proxy fallback was not needed.

| contrast | control − candidate | n | mean d | **σ̂_d** | χ² 80% | χ² 90% | χ² 95% | boot 80% | governing bound |
|---|---|---|---|---|---|---|---|---|---|
| **N1** | `fixed_tok512` − `fixed_tok1024_ov0pct` | 10 | −0.0524 | **0.0600** | 0.0777 | 0.0882 | 0.0988 | 0.0660 | **0.0777** (χ²) |
| **N2** | `fixed_tok512` − `fixed_tok512_ov0pct` | 10 | 0.0000 | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 | **0.0000** |
| **R1** | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | 10 | −0.0024 | **0.0597** | 0.0772 | 0.0877 | 0.0982 | 0.0659 | **0.0772** (χ²) |
| **R2** | `header512` − `fixed_tok512_ov0pct` | 10 | 0.0083 | **0.0263** | 0.0341 | 0.0387 | 0.0434 | 0.0351 | **0.0351** (boot) |
| **R3** | `parent256` − `fixed_tok512` | 10 | 0.0083 | **0.0615** | 0.0795 | 0.0904 | 0.1012 | 0.0766 | **0.0795** (χ²) |

Raw 10-difference arrays per contrast: `artifacts/stage0_table.json` →
`contrasts.<id>.row1_sigma_d.differences`. No topic was excluded by §7.4's < 3-unit rule
(minimum m was 5).

**σ_d by `n_rel` stratum:** every dev topic sits in `n_rel ∈ [85, 152]` — the single
central stratum the S2 selection rule produced. **There is no sparse-tail stratum and no
dense-tail stratum in the dev sample at all**, so §2.1's "affected contrasts inherit the
wider of the neighbouring strata's σ_d" has no neighbour to inherit from. 25 of the 80
confirmation topics (13 with `n_rel < 40`, 12 with `n_rel > 250`, range 8–854) lie outside
that window entirely, and nothing measured here speaks for them.

### Row 2 — unit-level `p_flip`, **ρ**, and the model-vs-direct σ_d

**ρ is the single number the task named as most likely to decide this. It is essentially zero.**

| contrast | units | `p_flip` | **ρ (one-way ICC)** | ρ 95% topic cluster-boot | model σ_d | direct σ_d | governing point σ_d |
|---|---|---|---|---|---|---|---|
| **N1** | 110 | 0.0546 | **−0.0223** | [−0.0707, 0.0322] | 0.0704 | 0.0600 | **0.0704** (model) |
| **N2** | 110 | 0.0000 | — (undefined: no flips) | — | 0.0000 | 0.0000 | **0.0000** |
| **R1** | 110 | 0.0727 | **−0.0458** | [−0.0905, 0.0118] | 0.0813 | 0.0597 | **0.0813** (model) |
| **R2** | 110 | 0.0091 | **−0.0092** | [−0.0381, −0.0008] | 0.0288 | 0.0263 | **0.0288** (model) |
| **R3** | 110 | 0.0273 | **0.0643** | [−0.0319, 0.0875] | 0.0638 | 0.0615 | **0.0638** (model) |

**Measured ρ ≈ 0 (−0.046 to +0.064), and every 95% interval contains 0.** On its face this
is the *good* branch of §8.5.2: at ρ ≤ 0.03 the requirement σ_d ≤ 0.160 survives, and the
unit-cap 12 → 16 adaptation would even have been permitted. **Do not read it that way.**
ρ near zero here is not evidence that units share little; it is what an intra-class
correlation *must* return when `p_flip` is 0.5–7.3 % — almost no unit flips at all, so
there is almost no signal to be correlated. The ρ measurement is real, and it is
uninformative for the reason §5 gives.

### Row 3 — units per topic after D3, and the cap

* **m̄ = 11.0**, median 12, min 5, max 12, cap 12.
* **Cap-hit rate 7 / 10 topics (0.70)** — the cap binds for most topics.
* Per topic: `2014_5` 11, `2014_11` 12, `2014_29` 12, `2015_8` 12, `2015_18` 10, `2015_23` 12, `2016_1` 12, `2016_9` 12, `2016_13` 12, `2016_26` 5.

D3 pipeline, counted at every step: **391 raw evidence sets → 388** after the
Jaccard ≥ 0.5 within-document merge (3 merged) **→ 388** after containment pruning
(0 dropped) **→ 110** after the 12-per-topic document-stratified cap. 38 of 308 pairs
returned the legal "no localizable evidence" verdict (19 at grade 1, 19 at grade 2);
11 pairs were dropped by the quote path; 2 pairs were §6.5-windowed.

That the merge and containment rules removed only 3 of 391 sets is itself informative:
Scout emits few near-duplicate sets, so D3's dedup machinery is nearly inert on this data.

### Row 4 — measured per-topic binary discordance (`ES-Hit@4096`)

**Reported as a fact rather than argued, which is what §14 amendment 3 asked for.**

| contrast | discordant / n | **d** | Wilson 95% | d ≤ 0.025 at the point estimate? | at the Wilson upper bound? |
|---|---|---|---|---|---|
| **N1** | 4 / 10 | **0.400** | [0.168, 0.687] | **NO** | NO |
| **N2** | 0 / 10 | **0.000** | [0.000, 0.278] | yes | NO |
| **R1** | 4 / 10 | **0.400** | [0.168, 0.687] | **NO** | NO |
| **R2** | 1 / 10 | **0.100** | [0.018, 0.404] | **NO** | NO |
| **R3** | 2 / 10 | **0.200** | [0.057, 0.510] | **NO** | NO |

The binary secondary `ES-Hit@4096` requires **d ≤ 0.025** (≈ 2 of 80 topics) to be
resolvable at ε = 0.05, n = 80. Measured discordance is **0.10–0.40** on four of five
contrasts, and even N2's zero has a Wilson upper bound of 0.278. **`ES-Hit` is not
resolvable at n = 80 on any contrast** — the first row of this table that is a clean,
decisive answer to the question it was asked. Note this holds despite the continuous
endpoint being on a floor: `ES-Hit` is *high*-variance precisely because a topic covering
one unit out of twelve still scores 1.

### Row 5 — measured ρ_variant and the real variant-averaging divisor

| contrast | topics paired | **ρ_variant** | **measured divisor √(2/(1+ρ))** | assumed in rev. 1 | adaptation permitted (≥ 1.15)? |
|---|---|---|---|---|---|
| **N1** | 10 | −0.094 | **1.486** | 1.3 | yes |
| **N2** | 10 | — (both variants identically zero) | — | 1.3 | no |
| **R1** | 10 | −0.757 | **2.868** | 1.3 | yes |
| **R2** | 10 | +1.000 | **1.000** | 1.3 | no |
| **R3** | 10 | −0.048 | **1.449** | 1.3 | yes |

§8.5.2 predicted the divisor would be far *below* the assumed 1.3 because two formulations
of the same case must be strongly correlated. The measurement says the opposite —
ρ_variant is **negative** on three contrasts, implying a divisor *above* 1.3. **That is not
a windfall; it is another symptom of §5.** With per-topic differences that are almost all
exactly 0 and a handful of ±1/12 spikes, the `summary` and `description` differences are
near-independent noise, and a correlation estimated from 10 such points carries no
information (R2's ρ_variant = +1.000 exactly is the giveaway: both variants produced the
same two-valued vector). **No variant-averaging adaptation should be taken on these
numbers.**

### Row 6 — projected `n_retained`

| criterion | when | measured |
|---|---|---|
| **< 5 fetchable grade ≥ 1 documents** | corpus assembly — **non-outcome data, so computed EXACTLY on all 80, not projected** | **0 topics excluded** (the minimum over the 80 is 8) |
| < 3 evidence units | label freeze | dev rate **0.00** (min m = 5) |
| > 1/3 of pairs failed quote verification | label freeze | dev rate **0.00** |
| majority windowed **and** windowed union failed self-consistency | label freeze | dev rate **0.00** (no dev topic was even majority-windowed) |

**Projected n_retained = 80.** The `n_retained < 60` gate is **not tripped**. Requirement
for 80 % power at ε = 0.05, exact non-central t: σ_d ≤ **0.1577** at n = 80 (0.1360 at
n = 60) — the normal-approximation headline 0.05·√80/2.802 = 0.1596 reproduces, and so does
the SPEC's exact df = 79 figure of 0.158.

**Caveat that the number itself cannot carry:** the three label-freeze rates are projected
from ten topics whose `n_rel` all lie in [85, 152]. The 13 confirmation topics with
`n_rel < 40` are precisely the ones at risk of the < 3-unit exclusion, and the dev sample
contains no topic like them. n_retained = 80 is the optimistic reading, not a measurement.

### Row 7 — power against Δ ∈ {0, 0.01, 0.02}, per contrast

| contrast | σ_d | Δ = 0 | Δ = 0.01 | Δ = 0.02 |
|---|---|---|---|---|
| **N1** point / bound | 0.0704 / 0.0911 | 100.0 % / 99.8 % | 99.9 % / 97.3 % | 96.4 % / 82.9 % |
| **N2** point / bound | 0.0000 / 0.0000 | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % |
| **R1** point / bound | 0.0813 / 0.1052 | 100.0 % / 98.7 % | 99.1 % / 91.9 % | 90.3 % / 71.2 % |
| **R2** point / bound | 0.0288 / 0.0372 | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % |
| **R3** point / bound | 0.0638 / 0.0825 | 100.0 % / 100.0 % | 100.0 % / 99.0 % | 98.6 % / 89.4 % |

The power engine was validated against §8.5.1's own published table before use: it
reproduces 95.7 / 88.4 / 78.8 / 70.4 / 62.0 % at Δ = 0 and **47.3 %** at σ_d = 0.140,
Δ = 0.02 (and 59.8 % at Δ = 0.015, confirming §14.A's transcription-slip finding), plus the
χ² multipliers ×1.293 / ×1.469 / ×1.645 / ×1.826 at df = 9 and t₀.₉₇₅,₇₉ = 1.99045.

### Row 8 — label validation

**Machine gates (P.5 / §6.4) — two of three FAIL:**

| statistic | measured | gate | verdict |
|---|---|---|---|
| **hallucinated-span rate** (quote nowhere in the document) | **0.0504** (24 / 476 spans; Wilson 95 % upper **0.0739**) | ≤ 0.05 | **FAIL** (marginally, and the Wilson upper is half again over) |
| **self-consistency** (10 % duplicates at a different unit order) | **0.323** (10 / 31) | ≥ 0.90 | **FAIL, decisively** |
| minimality shrinkage (10 % audit) | **instrument failure** — the audit re-prompt returned an *empty* set on **29 / 29** audited pairs (mean spans 1.83 → 0.00). The statistic is uninformative, not a measured shrinkage of 1.0. | descriptive | not measurable |
| **index agreement** (new; a finding, not a gate) | **0.645** — 140 of 476 verified spans were **misindexed and relocated** | — | reported |
| unresolvable spans | 5 / 476 (0.011) | — | dropped |
| "no localizable evidence" | 38 / 308 pairs (0.123); 19 at grade 1, 19 at grade 2 | legal verdict | reported |

Self-consistency was computed under all three defensible readings of §6.4 rule 4's
"primary-set char-span Jaccard", and none rescues it: **union of all sets 0.323**,
**primary (first) set 0.387**, **best-matching set pair 0.387**. What Scout *does* reproduce
is the *verdict*: it agreed with itself on whether the document contains any localizable
evidence on **27 / 31** duplicated pairs (0.871). **It agrees on whether, and disagrees on
where.** For an endpoint whose entire content is *where*, that is the disqualifying result.

**§6.6.4 makes self-consistency < 0.90 a stop.** It is recorded as such here.

**Two disclosures that pre-empt the obvious audit questions.** *(i)* **The verifier
correction did not flip any gate.** The first labeling pass, under the over-strict checker,
gave hallucinated-span 0.369 and self-consistency 0.452; the corrected pass gives 0.0504 and
0.323. **Both passes fail both gates**, so the stop verdict is invariant to the checker
choice. Both label sets are retained (`work/labels-v1-strictchecker.jsonl` and
`artifacts/labels-dev.jsonl`). *(ii)* **κ(Scout–Qwen) (§6.6.3) was not computed** and
`mango:8004` was never called: the self-consistency stop is terminal on its own, and the
statistic's purpose in §6.6.3 is to locate disagreement clusters *against the adjudicated
human verdicts*, which are `PENDING-HUMAN`.

**An interpretation worth stating, because it is what the human read would settle.** The
rubric (E3) demands that *every* alternative location be enumerated. Scout agreeing on the
verdict (0.871) while disagreeing on the location (0.32) is exactly what **under-enumeration**
looks like: sampling one of several valid evidence sets rather than listing them all. That
is the `missed-evidence` failure mode §6.6.2 audits — which is why the R-dev read remains
necessary even though the machine gates already stop the study.

**Human half — `PENDING-HUMAN`.** κ(human–human), κ(Scout–human), positive-class agreement
and the `wrong-location` / `non-minimal` / `missed-evidence` rates require the two-reader
R-dev read of §6.6.2. **They were not computed. No agent read was substituted, and no κ is
reported anywhere in this document.** A second deviation is recorded with it: §6.6.1
requires both readers to sign off on the rubric before any labeling call, and **no human
signed off** — the rubric was frozen and hashed, and labeling proceeded, so a reader-forced
rubric revision would require a dev relabel under §6.6.4.

The draw is complete and rendered:

* **100 pairs**, seed `20260915`, in `artifacts/rdev_sample.json`;
* drawn by stratum: model-positive **48**, model-negative **27**, deep-section **3**,
  long-document **22**;
* **stratum shortfall, recorded:** the deep-section stratum wanted 20 and only **3 pairs
  existed** — across all 308 dev pairs, only 3 had *every* supplied span outside the
  Abstract and the first body unit. That is not a sampling accident; it is a measurement of
  the labeler, and it is the **abstract bias §6.6.6 warns about, observed directly**. The
  stratum the read most needs is the one the labels barely populate;
* ready-to-read artifacts, independently shuffled per reader:
  `artifacts/RDEV-readsheet-A.html`, `artifacts/RDEV-readsheet-B.html` (verified to differ),
  with blank verdict sheets `artifacts/rdev_verdicts_{A,B}.csv`.

### Row 9 — manipulation checks (§7.6) and the dev EUC level

| check | requirement | measured | verdict |
|---|---|---|---|
| 1. **GOLD packing control** | EUC ≥ 0.95 | **1.000 on every one of the 10 topics** | **PASS** |
| 2. **NEGATIVE control** | EUC ≤ 0.05 from grade-0 documents only | **0.000 on every arm** | **PASS** |
| 3. **discrimination (defect 3)** | doc Hit@1 < 1.0; top-10 sets differ across the size extremes for ≥ 25 % of topics | Hit@1 **0.30–0.60**; top-10 differ on **10 / 10** topics | **PASS** |
| 4. **budget bind** | realised ∈ [0.85 B, B] except rank-1 overshoot | `tok256`, `tok512/0`, `tok512`, `header512` **10/10**; `parent256` 8/10; **`tok1024` 3/10, `tok2048` 0/10** (mean 3,269) | **FAIL** |
| 5. **dev EUC level** | inside **[0.15, 0.90]** | **0.017 – 0.069** | **FAIL** |

**Check 4 is not a bug and it is not plumbing.** Budgets are counted in the *generator's*
tokenizer and chunk sizes in *SFR's*; on this corpus a 2,048-SFR-token chunk is ≈ 1,630
Scout tokens, so §7.3's premise that "4,096 is an exact multiple of every fixed arm size"
does not hold in the tokenizer that actually governs the budget. `tok2048` therefore stops
after two chunks at ~80 % of B while `tok256` fills to 97 %. **This systematically
under-supplies the coarse arms at fixed budget and inflates their apparent disadvantage** —
a confound in the primary contrast R1, disclosed here rather than discovered later.
GOLD and NEGATIVE — the two checks whose failure would mean a plumbing bug — both pass
exactly.

---

## 4. The gate, read literally — and why the literal reading is wrong

Applying §8.5.5's three-outcome rule mechanically to the measured numbers gives:

| contrast | σ_d point | σ_d 80 % bound | requirement at n_retained = 80 | holds at point? | holds at bound? | literal verdict |
|---|---|---|---|---|---|---|
| **N1** | 0.0704 | 0.0911 | 0.1577 | yes | yes | *power gate passes* |
| **N2** | 0.0000 | 0.0000 | 0.1577 | yes | yes | *power gate passes* |
| **R1** | 0.0813 | 0.1052 | 0.1577 | yes | yes | *power gate passes* |
| **R2** | 0.0288 | 0.0372 | 0.1577 | yes | yes | *power gate passes* |
| **R3** | 0.0638 | 0.0825 | 0.1577 | yes | yes | *power gate passes* |

**This table must not be reported as a pass, and this document does not report it as one.**
§8.5.7 row 9 exists precisely to stop it: *"the dev `EUC` level … must sit in [0.15, 0.90]
… a floor or ceiling effect destroys variance estimates as surely as any of the above."*
The level is **0.017–0.069**. Every σ_d above is the standard deviation of a quantity
pinned near zero, and N2's σ_d = 0.0000 with `p_flip` = 0.0000 is the proof: the shipping
arm and the 0 %-overlap arm covered **identical** unit sets on all ten topics — at a
coverage level of 1.7 %. That is not two configurations shown to be equivalent. That is two
configurations both covering almost nothing.

**Recorded gate verdict, per contrast: `GATE-NOT-EVALUABLE — row 9 precondition failed`.**
Not "passes", not `POWER-UNCERTAIN`, not "underpowered". The variance instrument was
applied to an endpoint that failed its own manipulation check, so its output carries no
information about power.

**ε did not move, and no adaptation was applied.** The §8.5.8 ladder was not entered: step 1
(variant averaging) and step 2 (unit cap 12 → 16) both key off measurements — ρ_variant and
ρ — that §3's argument shows are uninformative here, and applying an adaptation on an
uninformative measurement is the failure mode the ladder's "only where the measurement says
it will help" clause forbids.

---

## 5. Why the endpoint is on a floor — the mechanism, measured

`EUC` factors exactly into two terms, and both were measured at B = 4,096 on `summary`:

> **EUC = P(a unit's document is in the packed context) × P(the unit is fully contained | its document is packed)**

| arm | P(doc packed) | P(covered \| packed) | product |
|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.209 | 0.174 | 0.036 |
| `fixed_tok512_ov0pct` | 0.091 | 0.200 | 0.018 |
| `fixed_tok512` (shipping) | 0.100 | 0.182 | 0.018 |
| `header512` | 0.145 | 0.188 | 0.027 |
| `parent256` | 0.064 | 0.429 | 0.027 |
| `fixed_tok1024_ov0pct` | 0.082 | 0.889 | 0.073 |
| `fixed_tok2048_ov0pct` | 0.045 | 0.800 | 0.036 |

The two factors move in **opposite** directions with chunk size — small chunks reach more
documents, large chunks contain more of a span once reached — which is exactly the trade
the study wants to measure. But the first factor is catastrophically small for every arm.
At B = 4,096 the packed context shares on average **1.1 of a topic's 10.1 unit-bearing
documents**.

The cause is a **calibration mismatch inside the specification itself**, between three of
its own frozen rules:

1. **§6.3** pools the top-**20 documents** of every arm × variant, so the labeling set spans
   ~30–40 documents per topic;
2. **§6.2.1 D3 rule 4** caps a topic at 12 units and subsamples them **stratified by source
   document**, deliberately spreading the denominator across up to 12 *different* documents;
3. **§7.3** at B = 4,096 generator tokens admits **5–10 chunks**, hence at most 5–10
   *documents*, chosen by the reranker.

The denominator is built to be document-diverse and the numerator can only ever be
document-narrow. Nothing in the design ties them together.

**The B/D recalibration §8.5.7 row 9 calls for was run** (`artifacts/floor_diagnostic.json`),
re-packing the same frozen pools at B ∈ {4 096, 8 192, 16 384, 32 768} and at the ceiling
where the *entire* D = 50 pool is packed:

| arm | B=4 096 | B=8 192 | B=16 384 | B=32 768 | **ceiling (whole D=50 pool)** |
|---|---|---|---|---|---|
| `fixed_tok256_ov0pct` | 0.033 | 0.033 | 0.033 | 0.033 | **0.033** |
| `fixed_tok512_ov0pct` | 0.017 | 0.025 | 0.034 | 0.034 | **0.034** |
| `fixed_tok512` (shipping) | 0.017 | 0.017 | 0.025 | 0.025 | **0.025** |
| `header512` | 0.025 | 0.053 | 0.062 | 0.070 | **0.070** |
| `parent256` | 0.025 | 0.033 | 0.051 | 0.051 | **0.051** |
| `fixed_tok1024_ov0pct` | 0.069 | 0.094 | 0.104 | 0.150 | **0.150** |
| `fixed_tok2048_ov0pct` | 0.036 | 0.097 | 0.164 | 0.222 | **0.259** |

**No budget rescues the endpoint.** For the shipping arm and both fine arms, packing the
*entire* 50-chunk reranked pool — an unlimited budget — still covers **2.5–3.4 %** of the
located evidence. Only `tok2048` reaches the window, at B = 16,384, and it does so for a
reason that discredits rather than saves the measurement: at the ceiling, `EUC` is very
nearly proportional to chunk size, because 50 chunks of size *S* simply cover more
characters of more documents. **At D = 50, `EUC@B` is dominated by total supplied text
volume (D × chunk size), not by chunking quality** — which is the confound the endpoint
exists to avoid.

The ceiling above the ceiling is retrieval itself: the fraction of unit-bearing documents
that appear **anywhere** in the top-50 reranked pool is only **0.28–0.48** (shipping 0.282,
`tok2048` 0.482). Even a perfect packer could not exceed that.

**The bias-bound sample corroborates this independently.** §6.3's 10-per-topic seeded
draw from *outside* every pool was labeled alongside the pooled pairs. Out-of-pool
grade ≥ 1 documents yielded ≥ 1 evidence set **75.0 %** of the time against **88.5 %** for
pooled ones (and "no localizable evidence" 22.0 % against 7.7 %). Labelable evidence is
therefore **abundant outside the pools** — the pools are not missing evidence because it
isn't there, but because retrieval does not reach it. This is the evidence-recall secondary
of §7.5 speaking early, and it says the ceiling above is a retrieval ceiling, not a
labeling one.

**D is frozen at 50 by P.4** (= production's `rerank_candidates`), and raising it would stop
the harness being production-shaped, which is the whole answer to defect 4. So the
recalibration row 9 authorises cannot be performed within the frozen design.

---

## 6. What Stage 0 concludes

**Items 1–7 and 9 were run in full; item 8 is `PENDING-HUMAN` by scope.** Three findings, in
descending order of consequence.

**A. The gold is not reproducible, and that is a study stop under §6.6.4.**
Scout's self-consistency across a re-presentation of the same document is **0.32–0.39**
against a **≥ 0.90** gate, on all three readings of the rule. It agrees with itself about
*whether* a document contains localizable evidence (0.871) and disagrees about *where*
(0.32). Only **64.5 %** of its verified spans were where it said they were. The
hallucinated-span rate is **0.0504** against a ≤ 0.05 gate (Wilson upper 0.0739). Two of
three machine gates fail, and the third could not be measured because the minimality-audit
prompt empties the label set. **`Llama-4-Scout` under this rubric does not produce labels
stable enough to define this endpoint**, and no amount of n rescues an unstable denominator.

**B. The endpoint is on a floor, so the power gate cannot be read at all.**
Dev `EUC@4096` is **0.017–0.069** against a required **[0.15, 0.90]**. The σ_d values look
excellent (0.00–0.08 against a 0.158 requirement) only because the endpoint is pinned near
zero. The recalibration row 9 authorises does not rescue it: even packing the entire D = 50
pool leaves the shipping arm at **0.025**. The measured **ρ ≈ 0** — the number the brief
correctly identified as most likely to decide the gate — is real but uninformative, because
`p_flip` is 0.5–7.3 % and an ICC of almost-never-flipping indicators has nothing to
correlate.

**C. The binary secondary is cleanly resolved, negatively.**
Measured per-topic discordance for `ES-Hit@4096` is **0.10–0.40** where **≤ 0.025** is
needed. **`ES-Hit` is not resolvable at ε = 0.05, n = 80 on any contrast.** This is the one
row of the table that answers its own question decisively, and §14 amendment 3 was right to
demand it be measured rather than argued.

### Can the confirmation run as specified answer its own question?

**No — and the reason is not n = 80.** At n = 80 the design would have ample power *if* the
endpoint behaved. It does not. Two independent preconditions fail before power is reachable:
the labels are not reproducible (A), and the endpoint cannot register a difference because
almost nothing is ever covered (B). Running the 80 confirmation topics as frozen would
produce five contrasts whose differences are near-zero for a reason that has nothing to do
with chunking, and §8.5.8's closing clause would then apply with full force: *failure to
establish a difference must not be interpreted as equivalence or used to prune
configurations.*

**What would have to change, stated so the next revision is not a guess.** These are the
places the measurement points at; none is a decision this document takes.

1. **Tie the denominator to the budget.** D3 rule 4's document-stratified cap and §7.3's
   5–10-document budget capacity are pulling in opposite directions. Either the cap follows
   the documents a realistic context can hold, or `EUC` is redefined per *document* rather
   than per topic.
2. **Fix the labeler or the protocol, not the sample size.** An index-primary,
   quote-verified protocol asks a non-reasoning model to track sentence numbers over units
   with dozens of sentences, and it fails 35 % of the time. A quote-primary protocol (the
   relocation this run had to implement anyway) or a reasoning judge would both be
   defensible; a second judge (`mango:8004`) exists and was never needed here because the
   first judge failed against *itself*.
3. **Count budgets and chunk sizes in the same tokenizer,** or accept that the coarse arms
   are systematically under-supplied at fixed budget (check 4).
4. **ε does not move**, under any of the above. Nothing measured here licenses widening it.

---

## 7. Reproduction

| artifact | path |
|---|---|
| code | `phase0/stage0/s0_*.py`, `run_0b.sh` |
| rendered §8.5.7 table | `phase0/stage0/TABLE-8.5.7.md` |
| provenance (commit, served ids, seeds, tokenizer hashes, GPU snapshots) | `artifacts/provenance-stage0.json` |
| corpus manifest (sha256 + all 32,663 `(pmcid, sha256)` pairs) | `artifacts/manifest.json` |
| frozen rubric | `design/RUBRIC-evidence.md` (sha256 `2e11f368…c747363b`) |
| raw per-topic difference arrays, all rows | `artifacts/stage0_table.json` |
| per-unit coverage matrix | `artifacts/unit_matrix.json` |
| evidence units after D3 | `artifacts/units.json` |
| dev labels (308 pairs, with per-span verification stats and raw model output) | `artifacts/labels-dev.jsonl` |
| machine label gates | `artifacts/label_gates.json` |
| manipulation checks | `artifacts/checks.json` |
| floor mechanism + B/D recalibration | `artifacts/floor_diagnostic.json` |
| **R-dev human-read package (`PENDING-HUMAN`)** | `artifacts/RDEV-readsheet-{A,B}.html`, `artifacts/rdev_verdicts_{A,B}.csv`, `artifacts/rdev_sample.json` |
| embeddings, chunk spans, pools, packed contexts (49 GB) | `/rag/tmp/stage0-conf/` |

`scipy` is absent from `/rag/envs/ragstack`, so the χ², Student-t, non-central-t, Wilson
and bootstrap machinery is implemented in `s0_math.py` from `math`/`numpy` only and is
**validated against the SPEC's own published tables** on import (`selftest()`); the gate
refuses to compute if any published cell fails to reproduce.

**Resource ledger.** 1.13 B SFR tokens over 6 index arms in **1.93 fleet-hours** at
167 k tok/s (≈ 11.6 device-GPU-hours across `:9001`–`:9006`; the 8-hour cap was read in
§11's fleet-wall-clock accounting, tripwire armed and never fired, 0 retries in 168,134
requests). `:50052`: 9,000 rerank pairs. `mango:8003`: **972 requests, 11.36 M prompt tokens, ~56 min of LLM time in
total** — 517 requests / 6.03 M tokens for the first labeling pass under the over-strict
checker, 455 / 5.32 M for the retained pass, plus 10 smoke-test calls. `mango:8004`
(Qwen second judge) was **never called**. **GPUs 6 and 7: 0 MiB before and after. No store was contacted at any point.**
