# RESULTS — Stage 0b′ step 2, second attempt (“r3.1”): whole-sentence anchors, five presentations, union saturation

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§3.7 items 1 and 6, under the owner's decisions in §10 items 2 (a) and 3, executed as its
§5 step 2 for the **second** time. The first attempt is
[`RESULTS-stage0b-relabel.md`](RESULTS-stage0b-relabel.md) (PR #501); this document reports
what changed against it, number by number.

**Scope note, stated first because it bounds everything below.** The two-independent-human-reader
validation (item 8, §6.6.2) is **not** in this run and was not performed. **No κ(human–human)
or κ(judge–human) appears anywhere in this document**, no agent read was substituted for a
human read, and the R-dev pairs were not read by the agent that produced these labels. The
human half stays **`PENDING-HUMAN`**. r3 §3.7 item 5's *actual* gate — enumeration recall
against human-marked evidence sets — is therefore **absent, not zero**; the cross-judge
coverage proxy stands in its place and is labelled as a proxy throughout.

---

**Verdict: `NEITHER JUDGE PASSES — the stop stands per r3 §5 step 2.`** But the two halves of
the result point in opposite directions and both matter:

* **The anchor decision (r3 §10 item 3) worked, decisively.** Scout's hallucinated-span rate
  fell from **0.0820 to 0.02517** (22/874, Wilson 95 % upper 0.0378) and Qwen's from 0.0253 to
  **0.00279** (1/358). **Both judges now pass the copy gate**, which only Qwen did in #501. The
  failure that motivated the change is gone: #501's split was **4 first-anchor / 54 closing-anchor**
  for Scout; r3.1's is **16 / 6**.
* **Self-consistency did not follow, and for Scout it went backwards.** Scout **0.645 → 0.3831**
  (0.2903 on the same 31 pairs #501 measured); Qwen **0.419 → 0.5617** (0.5484 on those 31).
  Neither is 0.90. §3 shows the mechanism, and it is not a matcher artefact: whole-sentence
  anchors made Scout's spans **three times smaller** (median 812 → 273 characters), and a
  Jaccard gate punishes small targets for the same amount of disagreement.
* **The union does not saturate.** At five presentations the marginal gain is still **10.2 %**
  (Scout), **11.3 %** (Qwen) and **10.7 %** (pooled) of the union size, against the 5 % bar this
  run pre-registered. r3 §3.7 item 6 asked whether the union of plausible locations is the
  stable statistic the study needs. On this evidence, at k = 5, **it is not yet one.**

---

## 0. Provenance

| item | value |
|---|---|
| specification | `../design/SPEC-confirmation-run-r3.md` §3.7 items 1 + 6, owner decisions §10 items 2 (a) and 3; run as its §5 step 2, second attempt |
| supersedes | `RESULTS-stage0b-relabel.md` (#501) — same 308 pairs, same two judges, same rubric, same labeling set |
| **rubric** | `../design/RUBRIC-evidence.md`, sha256 `2e11f3688de916da…c747363b` — **unchanged** from Stage 0 and #501, hashed before the first call. Its §6 output block is superseded by this run's answer format; the amendment is **proposed in §8 and not applied**, because the rubric is frozen and hashed |
| **prompt** | revision **3.1** (whole-sentence anchors), sha256 `ba09e122553833219a999f8a99c496fd2926b2b341f77026dfe7c6ab2f5131c7`. #501's revision 3 was `49f567d77cbe1ac4…9f1e979e`. System prompt `f758cbf4d74bf3d419eb263678a5bd436b24454c61cc50a758f421771dcae166` — **byte-identical** to Stage 0's and #501's. Re-prompt `f5932e0e8f1f488a…3a3f4c0a`. **One prompt, both judges** |
| judge 1 | `mango:8003` → `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, probed live and asserted against `s0_common.SCOUT_EXPECT` |
| judge 2 | `mango:8004` → `Qwen/Qwen3.6-35B-A3B`, probed live and asserted against `s0_common.QWEN_EXPECT`. Reasoning model; thinking arrives in a sibling `reasoning` field, is counted (26,897,775 characters) and is stripped before parsing by #501's stripper, imported unchanged |
| sampling | **temperature 0**, seed `20260914`, ≤ **4** concurrent per endpoint (§6.4 rule 5), `max_tokens` 3,000 (scout) / 12,000 (qwen) |
| pairs | **308** — 208 pooled + 100 bias-bound sample, over the **10 development topics only**; asserted against `C.DEV_TOPICS` in both the labeler and the gate reader. No confirmation topic was read, retrieved, packed or labeled |
| **presentations** | **5 per pair, per judge** = 1,540 records each, all complete (`0: 308, 1: 308, 2: 308, 3: 308, 4: 308`; zero incomplete pairs). Unit order seeded `SEED_LABELDUP + 100·k + pair_index`; **k = 0 is the natural order** |
| labeling set | `s0_label.labeling_set` re-run on the pools already in `work/`; asserted **equal** to Stage 0's `labeling_set.json`. Zero retrieval, zero embedding |
| the 31 pairs | Stage 0's own 10 % duplicate draw, reproduced and asserted against `label_meta.json`, carried on each record as `in_501_duplicate_31` so the #501-comparable reading is computable |
| segmentation | `ragstack.ingestion.chunkers.sentence_spans`; `git diff 55a0fc2..HEAD -- python/ragstack/ingestion/chunkers.py` asserted **empty** before the first call, so the segmenter is Stage 0's |
| windowing | §6.5, 48,000 tokens in the served generator's tokenizer, applied **along each presentation's own unit order**; 2 of 308 pairs windowed for both judges |
| interpreter | `/rag/envs/ragstack/bin/python3`, `HF_HOME=/rag/cache`, `PYTHONPATH=…/ragstack/python`, `STAGE0_HELPERS=…/phase0-rescue/phase0` |
| repo | worktree of `1a67662`; `git diff 55a0fc2..HEAD -- python/ragstack/` is empty in full |
| ran | scout 2026-09-06T16:53:40Z → 17:39:59Z; qwen 16:54:27Z → 20:15:12Z (issued in parallel, one per endpoint) |
| **endpoints contacted** | `mango:8003` and `mango:8004` **only** — plus `mango:8003/tokenize`, the same host and the same served model, for the §6.5 window budget. No `:9001`–`:9006`, no `:50052`, no Qdrant/Elasticsearch/Neo4j, no tenant API. **No store client is constructed** anywhere in `s0_label_r31.py`, `s0_label_r3.py` or `s0_labelgates_r31.py`. GPUs 6 and 7 untouched — nothing here selects a device and mango is a remote host |
| artifacts | `artifacts/r31/labels-r31-scout.jsonl` (sha256 `a6d0c51f958b444b…`), `artifacts/r31/labels-r31-qwen.jsonl` (`5d494959cbb8c55c…`), `artifacts/r31/label-manifest-r31.json` (`0b811f9b53091783…`), `artifacts/r31/gates-r31.json` (`2de8e8ff40eee34d…`), `artifacts/r31/gates-r31.md` |
| not committed | `work/r31/raw-r31-{scout,qwen}.jsonl` — the full raw responses **including 26.9 MB of Qwen thinking**. The committed label records keep the post-strip answer text plus a sha256 over the true raw response, exactly as #501 did |

Each label record carries `judge`, `served_model`, `prompt_sha256`, `raw_response_sha256`,
`presentation`, `unit_order_seed` and `unit_order` alongside #501's own fields, so the two
runs' records are directly comparable and every presentation is separately checkable.

---

## 1. What changed in the protocol, and what deliberately did not

Two changes, both pre-decided by the owner in r3 §10, both implemented in a **new** module —
`s0_label_r3.py`, `s0_label.py` and every committed artifact of #501 are untouched, so #501's
labels stay reproducible.

**Whole-sentence anchors (r3 §10 item 3, “decision C”).** The judge quotes the **whole first
sentence** and the **whole last sentence** of each span, in full, instead of their first and
last ten words. For a one-sentence span the two quotes are the same sentence (an omitted or
equal `last_sentence` is accepted). Each quote is located by a three-rung ladder:

1. **exact** — the string appears in the document byte for byte;
2. **normalised** — it appears in the whitespace-collapsed, case-folded map
   (`s0_label._norm_map` / `_find`, the path #501 already used);
3. **eight-word** — the full sentence does not locate, but its **first eight** and **last eight**
   words both do, and both land inside **one** sentence of the segmented document. That sentence
   is the located sentence, whole.

A quote that survives none of the three is a hallucinated span, dropped, and counted against the
P.5 gate. The span is `[first located sentence … last located sentence]`, snapped outward to
whole-sentence boundaries within one unit (D1); a quote pair crossing a unit boundary becomes a
multi-span set, as in #501.

**Five presentations per pair (r3 §3.7 item 6 / §10 item 2 (a)).** Every pair is presented five
times **at temperature 0**, each with the document's units in a different seeded order. This is
Stage 0's own 10 %-duplicate mechanism (§6.4 rule 4) applied to all 308 pairs rather than 31, so
that the union of a judge's readings can be built and its saturation measured. The variation is
the *presentation*, never the sampler: temperature stays 0 and the seed stays `20260914`.

Carried over **unchanged**, so a difference between these labels and #501's is attributable to
the anchor change and not to a rewritten task: the rubric; the prompt's clinical framing,
definitions, worked example, enumeration rule and “no localizable evidence” clause (only the
span-identifier block and the output block differ); the labeling set; the two judges and their
politeness contract; the `<think>` stripper; D1's outward snap and the cross-unit multi-span
reading; the 48,000-token windowing; and the sentence segmenter, asserted unchanged from
Stage 0's pinned `55a0fc2`.

**The eight-word ladder barely fired, and that is itself a result.** Of Scout's 874 attempted
k = 0 spans, **829 first-sentence quotes matched exactly** and 27 after normalisation; only
**2** needed the eight-word rescue. Qwen: 349 exact, 9 normalised, **0** rescued. Whole-sentence
quoting is not a matcher trick — the models really do copy whole sentences verbatim.

---

## 2. The gate table

| gate | requirement | **scout** | **qwen** |
|---|---|---|---|
| **self-consistency** — reading (i), k = 0 vs k = 1, all 308 pairs | ≥ 0.90 | **0.3831** (118/308) **FAIL** | **0.5617** (173/308) **FAIL** |
|   — reading (i) on the same 31 pairs #501 used | reported (#501: 0.645 / 0.419) | **0.2903** (9/31) | **0.5484** (17/31) |
|   — reading (i) over all 308, first set only / best-matching set pair | reported | 0.5422 / 0.6656 | 0.5584 / 0.5714 |
|   — the same two readings on the 31 pairs | reported (#501: 0.7742 / 0.9032 and 0.4194 / 0.4194) | 0.5484 / 0.6129 | 0.5484 / 0.5484 |
|   — reading (ii), mean over all 10 presentation pairs (union) | reported | 0.4201 (range 0.3766–0.4675) | 0.5399 (range 0.4870–0.5779) |
|   — reading (ii), first set only / best-matching set pair | reported | 0.5630 / 0.6880 | 0.5396 / 0.5510 |
|   — mean pairwise span-union Jaccard (raw value, not the ≥ 0.5 indicator) | reported | 0.4350 (median 0.3534, n = 2,898) | 0.5430 (median 0.7518, n = 3,056) |
| **hallucinated-span rate**, k = 0 | ≤ 0.05 | **0.02517** (22/874 spans; Wilson 95 % CI 0.0167–0.0378) **PASS** | **0.00279** (1/358 spans; Wilson 95 % CI 0.0005–0.0157) **PASS** |
|   — **split by anchor** (first-sentence / last-sentence quote) | reported (#501: **4 / 54** scout, 2 / 7 qwen) | **16 / 6** | **0 / 1** |
|   — all five presentations (sensitivity) | reported | 0.01957 (85/4,344); anchors 58 / 27 | 0.00888 (16/1,802); anchors 12 / 4 |
|   — locate ladder, first anchor (exact / normalised / eight-word) | descriptive | 829 / 27 / 2 | 349 / 9 / 0 |
| **document-level whether-agreement**, all five presentations agree | ≥ 0.90 | **0.9188** (283/308) **PASS** | **0.9805** (302/308) **PASS** |
|   — mean pairwise whether-agreement | reported (#501, 2 presentations: 0.968 / 0.968) | 0.9610 | 0.9909 |
| “no localizable evidence” rate, k = 0 | descriptive (#501: 0.081 / 0.013) | 0.0844 (26/308 pairs) | 0.0195 (6/308 pairs) |
| spans emitted / attempted, k = 0 | descriptive | 876 / 874 | 357 / 358 |
| spans split across a unit boundary, k = 0 | descriptive (#501: 14 / 0) | 10 | 0 |
| quotes ambiguous (> 1 occurrence), k = 0 | descriptive (#501: 104 / 30) | 24 | 7 |
| quote landed inside the unit whose title it named, k = 0 | descriptive | 798/830 | **339/339** |
| one-sentence spans (last quote equal to first), k = 0 | descriptive | 569/874 | 300/358 |
| pairs dropped (no verified span survived), k = 0 | descriptive (#501: 1 / 3) | **0/308** | **0/308** |
| mean evidence sets / spans per positive pair, k = 0 | descriptive (#501: 1.809/2.096 and 1.153/1.153) | 2.436 / 3.007 (n = 282) | 1.182 / 1.182 (n = 302) |
| pairs whose every span is in the abstract, k = 0 | descriptive (#501: 168/282, 183/301) | 104/282 | 200/302 |
| deep-section pairs, k = 0 | descriptive (Stage 0: 3/308; #501: 6 / 80) | 5/282 | 67/302 |
| **ALL THREE GATES** | conjunctive | **FAIL** | **FAIL** |
| union of five presentations, split-half stability (0–2 vs 3–4) | reported, **not** a gate | 0.4708 (145/308) | 0.5779 (178/308) |

### 2.1 Union saturation — r3 §3.7 item 6 / §10 item 2's question, answered

Distinct = **not merged by D3 rule 1** (span-union Jaccard ≥ `JACCARD_MERGE` = 0.5), accumulated
in presentation order with k = 0 first. Denominator: the pairs that carry at least one set
somewhere in the five presentations (295 for scout, 307 for qwen and pooled; the all-308 curve is
in `gates-r31.json` and differs only by the empty pairs' dilution).

| union | n pairs | k = 1 | k = 2 | k = 3 | k = 4 | k = 5 | marginal gain at k = 5 |
|---|---|---|---|---|---|---|---|
| **scout** | 295 | 2.2746 | 3.6203 | 4.5017 | 5.4780 | **6.1017** | **0.1022** |
| **qwen** | 307 | 1.1629 | 1.7231 | 2.1661 | 2.4886 | **2.8046** | **0.1127** |
| **pooled (scout ∪ qwen)** | 307 | 3.0293 | 4.7003 | 5.8404 | 6.9870 | **7.8241** | **0.1070** |

Marginal gain at each k, for the record: scout 0.3717 / 0.1958 / 0.1782 / **0.1022** at
k = 2…5; qwen 0.3251 / 0.2045 / 0.1296 / **0.1127**; pooled 0.3555 / 0.1952 / 0.1641 / **0.1070**.

**Read plainly: it is not saturating.** Every curve is still climbing at the fifth presentation
by roughly a tenth of its own size — twice the 5 % bar and more. Five presentations of one judge
recover a **2.7×** larger set of distinct locations than one presentation does (scout 2.27 → 6.10),
and there is no knee. A sixth presentation would add locations; so, on this evidence, would a
tenth.

### 2.2 Enumeration proxy — r3 §3.7 item 5, standing in for a statistic that does not exist yet

| statistic | value |
|---|---|
| asymmetric coverage on the **k = 5 unions** (scout's chars inside qwen's / qwen's inside scout's) | **0.1852** / **0.7802** (n = 295) |
| the same at k = 0 (#501: 0.1622 / 0.6592) | 0.2252 / 0.5908 (n = 281) |
| scout: fraction of its own k = 5 union that presentation 0 alone recovered | 0.4248 (median 0.4000, n = 295) |
| qwen: fraction of its own k = 5 union that presentation 0 alone recovered | 0.5612 (median 0.5000, n = 307) |
| fraction of a judge's k = 0 sets still present in its own k = 5 union | **1.0 by construction, not by measurement** — the union is accumulated from k = 0 outwards, so every k = 0 set is one of its seeds. Reported because the brief asks for it; the informative direction is the row above |

**The asymmetry #501 found is still there and is larger at the union level.** 78.0 % of Qwen's
selected characters lie inside Scout's, against 18.5 % the other way. Qwen is picking a *subset*.
Sampling each judge five times does not close the gap — it widens it, because Scout's extra
presentations add locations Qwen's do not.

**And one presentation is not most of a judge.** Scout's single primary reading recovers **42 %**
of what its own five readings find; Qwen's recovers **56 %**. Whatever a one-shot label of this
development set is, it is not an enumeration of the plausible locations.

### 2.3 Cross-judge agreement (scout vs qwen)

| statistic | value |
|---|---|
| co-labeled pairs (complete for both judges) | 308 |
| κ(scout–qwen), pair-level binary evidence/none, k = 0 | **0.29** (#501: 0.34) |
| observed / expected agreement | 0.9286 / 0.8994 |
| confusion (both + / both − / scout-only + / qwen-only +) | 281 / 5 / 1 / 21 |
| span-union Jaccard where both positive, k = 0 | mean **0.2123**, median 0.0805, ≥ 0.5 on 0.1459 of 281 |
| span-union Jaccard between the k = 5 unions | mean **0.1613**, median 0.1012, ≥ 0.5 on 0.0407 of 295 |
| distinct sets in the cross-judge (scout ∪ qwen) union at k = 5 | **7.8241** per positive pair (n = 307) |

κ = 0.29 is low **because the negative class is nearly empty** — Scout calls 26 of 308 pairs
negative and Qwen 6 — not because the judges disagree about *whether*: they agree on **286 of
308** (281 both-positive, 5 both-negative). The confusion counts are the honest
reading, as they were in #501.

---

## 3. What the numbers say, read against #501

| statistic | Stage 0 (index-primary) | #501 r3 scout | **r3.1 scout** | #501 r3 qwen | **r3.1 qwen** |
|---|---|---|---|---|---|
| hallucinated-span rate | 0.0504 | 0.0820 **FAIL** | **0.0252 PASS** | 0.0253 PASS | **0.0028 PASS** |
| — closing-anchor share of failures | — | 54/58 | **6/22** | 7/9 | 1/1 |
| self-consistency, union, the same 31 pairs | 0.323 | 0.645 | **0.290** | 0.419 | **0.548** |
| self-consistency, union, all 308 pairs | absent | absent | **0.383** | absent | **0.562** |
| self-consistency, best-matching set pair | 0.387 | 0.903 | 0.666 | 0.419 | 0.571 |
| whether-agreement | 0.871 | 0.968 (2 presentations) | 0.919 (5), 0.961 pairwise | 0.968 (2) | 0.981 (5), 0.991 pairwise |
| sets / spans per positive pair | 1.83 spans | 1.81 / 2.10 | **2.44 / 3.01** | 1.15 / 1.15 | 1.18 / 1.18 |
| **median span length, characters** | — | **812** | **273** | 257 | 225 |
| mean span length, characters | — | 1,181.5 | 739.9 | 320.3 | 293.6 |
| mean sentences per span | — | 6.98 | **4.54** | 1.66 | 1.39 |
| pairs dropped | — | 1/308 | 0/308 | 3/308 | 0/308 |

### The anchor decision did exactly what it was decided to do

r3 §10 item 3's ground was that *“evidence that is cited must not appear hallucinated either — a
correct span discarded for a bad receipt is a correctness failure of the pipeline.”* That failure
is now largely gone. Scout's rate is a third of what it was, its Wilson 95 % upper bound
(**0.0378**) sits under the gate rather than over it, and the specific defect the decision named
— a reconstructed closing anchor, a plural for a singular, a trimmed parenthetical — has
inverted from 54 of 58 failures to **6 of 22**.

**The 22 remaining failures were re-checked by hand**, one at a time, against the
whitespace-collapsed case-folded document. Every one is genuinely absent, and the residual failure
mode is legible: the model copies the *opening* of the sentence faithfully and then stops early or
alters the tail. In **10 of the 16** first-anchor failures and **4 of the 6** last-anchor ones, the
quote's first eight words are present in the document and its last eight are not — for example `"The primary clinical presentation was hematochezia in
44 (75%) patients."`, whose opening is in the article and whose ending is not. That is why the
eight-word ladder rescued only 2 spans: it requires *both* ends. **This is not a matcher
artefact**; the checker is measuring what it is meant to measure.

### And it made self-consistency worse for Scout, for a reason worth stating

Scout's union self-consistency fell from 0.645 to 0.383 (0.290 on #501's own 31 pairs). Two things
happened at once and both are visible in the table above.

**Scout's spans got three times smaller.** Median span length **812 → 273 characters**; mean
sentences per span **6.98 → 4.54**. Under ten-word anchors the model's closing anchor often landed
several sentences downstream, so a span swept up a paragraph; under whole-sentence anchors a span
is what the model actually meant, and 569 of 874 are a single sentence. **A Jaccard threshold
punishes small targets.** Two readings that differ by one sentence score near 0 when the spans are
one sentence each and near 0.8 when they are seven. The gate is measuring stability *and* span
size, and it cannot tell them apart.

**Scout also enumerates more.** 1.81 → **2.44** sets per positive pair. More sets is what r3 §3.7
item 5 asked for — Qwen's under-enumeration is the failure mode the endpoint's denominator is most
sensitive to — but a union of 2.4 sets agreeing with another union of 2.4 sets is a harder test
than 1.8 against 1.8.

Neither observation licenses moving the gate, and this document does not propose moving it. What
it does establish is that **#501's 0.645 and r3.1's 0.383 are not measuring the same object**, and
that a self-consistency number is only interpretable beside the span size it was measured on. The
best-matching-set-pair reading, which is insensitive to how *many* sets there are, fell too
(0.903 → 0.666), so span size — not enumeration count — carries most of the change.

**The reasoning judge improved and still fails.** Qwen went 0.419 → **0.562** on the same 31 pairs
and 0.562 over all 308; its spans barely moved in size (257 → 225 median characters), which is
consistent with the reading above — it was already quoting single sentences. Its median pairwise
raw Jaccard is **0.752**, so more than half its presentation pairs agree substantially; the gate's
≥ 0.5 indicator says 0.54 because the distribution is bimodal, not because it is uniformly poor.

### The whether-question is the only thing anyone agrees on, again

Every judge agrees with itself about *whether* a document carries localizable evidence across all
five presentations at **0.919** (scout) and **0.981** (qwen), and pairwise at 0.961 / 0.991 — over
the gate under both readings. Across judges, observed agreement is **0.929**. On *where*, the
median cross-judge Jaccard is **0.081**. Two protocols, two anchor schemes, five presentations
each: the split Stage 0 found is now confirmed a third time and is not an artefact of any of them.

### What the union answer means for the endpoint

r3 §3.7 item 6 reasoned that because D4 counts a unit covered when *any one complete* evidence set
is contained, `EPACK` is robust to a gold that lists **more** valid locations than a single judge
finds and fragile to one that lists **fewer** — so the stable statistic is the union, *if it
saturates*. It does not, at k = 5, for either judge or for the pool. Three readings are available
and this document does not choose between them:

1. the union is the right object and five samples are too few — the curve suggests many more;
2. the union is accumulating **noise as well as locations** — each presentation contributes ~1.4
   new "distinct" sets for Scout, and D3 rule 1 at Jaccard ≥ 0.5 is a *lenient* merge for
   single-sentence spans, so two readings of the same location can fail to merge;
3. the endpoint needs *the* location, and no amount of sampling supplies it.

**Reading 2 is testable and is not tested here**, because the instrument that separates a genuine
alternative location from a near-miss of the same one is the human read. That is the sharpest
version of why item 8 is the blocking item: **the saturation curve cannot be interpreted without
it.** Note also that the split-half stability of the union (0.471 scout, 0.578 qwen) is barely
better than the single-presentation self-consistency it was meant to stabilise — a union that is
still growing is not yet a stable statistic, which is the same finding stated a second way.

### Nothing here licenses moving a gate

Neither judge passes the conjunction. r3 §5 step 2's instruction applies as written: **stop**. But
the reason has changed. #501 stopped with *one* judge failing the copy gate and both failing
stability. r3.1 stops with **both judges passing the copy gate** and both failing stability — and
with the measurement showing that the stability gate, as written, is entangled with span size.

---

## 4. Deviations from the brief and from #501, all of them

1. **Presentation 1 is not #501's duplicate presentation.** #501 shuffled the duplicate with seed
   `SEED_LABELDUP + pair_index`; the brief's formula for this run is
   `SEED_LABELDUP + 100·k + pair_index`, so k = 1 is a *different* seeded permutation of the same
   units. Both are arbitrary permutations and neither is privileged, but the "same 31 pairs"
   comparison in §2 is like-for-like in the pairs and in the protocol, **not** in the permutation.
   The k = 0 presentation *is* byte-identical in construction to #501's primary pass.

2. **The §6.4 rule 3 minimality audit was not run**, as in #501. It was an instrument failure in
   Stage 0 (29/29 empty), the brief does not gate on it, and re-running it unchanged would
   re-measure a broken instrument. Minimality shrinkage is **absent, not zero**. This matters more
   than it did in #501: §3 shows spans got three times shorter, which is *prima facie* a minimality
   improvement, and this run has **no instrument that can confirm it**.

3. **Windowing is applied along each presentation's unit order.** #501 computed window groups on
   the natural order for the primary pass and did not window the duplicate presentation at all.
   Here every presentation is windowed on its own order, so all five see comparably sized windows.
   It affects **2 pairs of 308** for both judges.

4. **#501's request accounting under-stated its two windowed pairs, and this run's does not.**
   #501 reconciled Scout's 430 requests as "308 + 31 + 2 window continuations + 89 re-prompts".
   The two windowed pairs (`2016_1/4212306`, `2016_1/4070603` — 1,694 and 1,666 units, ~5 MB of
   text each) actually split into **26–29 windows**, in #501's own label file as well as this one.
   Here `sum(len(raws))` over the label file equals the judge's own request counter **exactly**
   (1,857 for scout, 1,845 for qwen), so the accounting is checkable rather than reconstructed.

5. **The gate denominator is the primary presentation (k = 0) only**, as the brief specifies and
   as #501 computed it, so the two runs are comparable. The all-presentations sensitivity reading
   is scout **0.01957** (85/4,344) and qwen **0.00888** (16/1,802) — both on the same side of the
   gate as the primary reading, and both *lower* than it, so the verdict is denominator-independent.

6. **Qwen truncated 46 responses** at `max_tokens` = 12,000 (`finish_reason: length`), against 5 in
   #501 — the run is 3.6× larger and the windowed pairs dominate. **10 records of 1,540** contain a
   truncated response, 2 of them at k = 0, and **none was dropped**: every one took its single
   re-prompt and returned a parseable answer. Scout truncated 2 responses in 2 records. Both
   judges dropped **0 of 308** pairs at k = 0, against #501's 1 and 3.

7. **A tolerated field-name alias.** The parser accepts `first_words`/`last_words` as aliases for
   `first_sentence`/`last_sentence` and counts every use, so a format slip would be visible rather
   than fatal. **It fired 0 times** for either judge in either the smoke or the full run.

8. **`stage1_common.py` and `pilot_common.py` are not repo files**, as in #501 deviation 7. They
   live in the Phase-0 build tree; `s0_label_r31.py` appends `$STAGE0_HELPERS/{stage1,pilots}` to
   `sys.path` before importing `s0_common`, defaulting to `~/Development/worktrees/phase0-rescue/phase0`.
   `cds/topics_merged.json` is resolved from the same tree.

9. **Repo `HEAD` is `1a67662`, not Stage 0's pinned `55a0fc2`.** `s0_common.provenance()` asserts
   the commit and would refuse; it is not called here. What matters is asserted directly instead:
   `git diff 55a0fc2..HEAD -- python/ragstack/ingestion/chunkers.py` is **empty** before the first
   labeling call, and `git diff --stat 55a0fc2..HEAD -- python/ragstack/` is empty in full.

10. **`index_agreement` is not reported**, having no referent under a protocol with no claimed
    indices — as in #501.

11. **No minimality, no human read, no κ against a human, no enumeration recall.** Item 8 remains
    `PENDING-HUMAN`. The R-dev pairs were not read by this agent and no agent read was substituted
    for one.

---

## 5. Cost

| run | requests | prompt tokens | completion tokens | wall | s/record | retries | failures | truncated |
|---|---|---|---|---|---|---|---|---|
| scout (`mango:8003`), 308 × 5 | 1,857 | 22,847,647 | 472,978 | **2,778.6 s** (46.3 min) | **1.80** | 0 | 0 | 2 |
| qwen (`mango:8004`), 308 × 5 | 1,845 | 26,306,333 | 6,811,554 | **12,044.3 s** (3.35 h) | **7.82** | 0 | 0 | 46 |
| smoke, scout (20 pairs × 2) | 40 | 153,896 | 10,900 | 42.8 s | 1.07 | 0 | 0 | 0 |
| smoke, qwen (20 pairs × 2) | 40 | 167,076 | 132,250 | 210.4 s | 5.26 | 0 | 0 | 0 |
| **total** | **3,782** | **49.47 M** | **7.43 M** | **~3.4 h** | — | **0** | **0** | **48** |

The two full runs were issued in parallel, one per endpoint, ≤ 4 in flight each; wall-clock end to
end was **3 h 22 min**, set by the reasoning judge. LLM-side service time was 10,768 s (scout) and
47,864 s (qwen). Scout's 1,857 requests are 1,540 records + 61 re-prompts + the window
continuations of the two 5 MB documents; the label file's own `sum(len(raws))` reproduces the
counter exactly. Qwen's thinking came to **26,897,775 characters**, stripped before parsing and
not committed.

Against r3 §4's estimate for this run — "≈ 3 fleet-hours, no API cost" for five samples of each
local judge — the realised figure is **3.4 h** and **$0**.

**Throughput check against the brief's stop rules.** All three were evaluated on the smoke and
none fired: both served model ids matched `s0_common`'s expectations; the smoke hallucinated-span
rates were **0.000** (scout, 0/102) and **0.000** (qwen, 0/44), far under the 0.5 "the prompt is
broken" threshold; and the projected full runs were 0.46 h (scout) and 2.25 h (qwen), or 0.78 h
and 3.83 h with #501's own smoke-to-full slippage factor applied, against the 5-hour ceiling. The
realised figures were 0.77 h and 3.35 h — inside the projection with the margin applied, and 49 %
over the raw smoke projection for qwen, which is the same direction and magnitude #501 saw.

---

## 6. Reproduction

```bash
export HF_HOME=/rag/cache PYTHONPATH=/home/wilke/Development/ragstack/python
export STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0
PY=/rag/envs/ragstack/bin/python3
cd docs/plans/results/stage0

$PY s0_label_r31.py --selftest                                    # offline; no endpoint
$PY s0_label_r31.py --judge scout --limit 20 --presentations 2 --tag smoke
$PY s0_label_r31.py --judge qwen  --limit 20 --presentations 2 --tag smoke
$PY s0_label_r31.py --judge scout                                 # 308 pairs x 5
$PY s0_label_r31.py --judge qwen
$PY s0_label_r31.py --merge-manifest                              # label-manifest-r31.json
$PY s0_labelgates_r31.py                        # artifacts/r31/gates-r31.{json,md}
```

`s0_label_r31.py` is **resumable and idempotent**: it reads the `(topic, docno, presentation)`
triples already in its output file and runs only what is missing, so an interrupted run is
restarted by re-issuing the same command. Work goes to `$STAGE0_BIG/work/r31/` and nowhere else
under `/rag/tmp/stage0-conf/`.

`--selftest` is offline and checks the whole-sentence locator on a real indexed document: a
one-sentence span snaps to its own sentence; a multi-sentence span covers first through last; a
cross-unit quote pair becomes a two-span set; a sentence mangled in the middle but copied at both
ends is rescued by the eight-word ladder and lands on the **right** sentence; a quote whose first
eight and last eight words come from **different** sentences is **not** rescued; an omitted
`last_sentence` is accepted; a nowhere-quote is counted as hallucinated **with the failing anchor
named**; and the presentation seeding is deterministic and differs between presentations.

---

## 7. What the next revision would have to change, stated so it is not a guess

None of these is a decision this document takes.

1. **Read the self-consistency gate against span size, or change what it measures.** ≥ 0.90 on a
   Jaccard ≥ 0.5 indicator is a much harder test for single-sentence spans than for paragraph-sized
   ones, and this run changed the span size by 3× while holding everything else. A stability
   statistic that is scale-free — sentence-level set overlap, or agreement on the *sentence
   indices* covered rather than the character intervals — would separate "the judge moved" from
   "the judge is precise". The gate should not be relaxed; it should be made to measure one thing.
2. **The union does not saturate at five, so either sample deeper or stop treating the union as
   the gold.** The curve is still climbing at 10 % per presentation. Deciding between "too few
   samples" and "the merge rule is too strict for one-sentence spans" needs the human read, which
   is the next item.
3. **Item 8 is now the *only* instrument that can advance this.** It was necessary in #501; it is
   load-bearing now. It alone can say whether presentation 5's new "distinct" set is a genuine
   alternative location or the same location the merge rule failed to fold in — and that single
   question decides whether the saturation result means "sample more" or "the union is the wrong
   object". The package should be rebuilt on the **union of both judges' r3.1 labels at k = 5**
   (7.82 distinct sets per positive pair, each tagged with its judge and the presentations that
   produced it), so the read yields per-judge wrong-location rates, a missed-evidence rate against
   the union, **and** a merge-rule validation the machine cannot do.
4. **A third-family judge (r3 §10 item 2 (b)) would break the tie about *where*, and only that.**
   Two local judges now agree at 0.929 on whether and 0.081 (median) on where, across two anchor
   protocols and five presentations each. Nothing in this run suggests a third model of the same
   kind would land anywhere but a third place; the value of §10 item 2 (b) is as a *different
   family*, scored against the human read, not as a tiebreak between these two.

**The gates do not move.** Nothing measured here licenses relaxing ≥ 0.90, ≤ 0.05 or ≥ 0.90 —
the last two of which, for the record, **both judges passed**.

---

## 8. Proposed amendment to `RUBRIC-evidence.md` §6 — recorded, not applied

The rubric is **frozen and hashed** (`2e11f3688de916da…c747363b`) and this run did not touch it;
its sha256 is unchanged in the manifest and was verified before the first call. But §6's output
block describes the **index-primary** answer format of Stage 0 (`"unit": 3, "first_sentence": 7,
"last_sentence": 7` as *integers*, plus ten-word anchors), which neither #501 nor this run used.
Under r3.1 the field names `first_sentence` / `last_sentence` are reused for **whole verbatim
sentences**, which makes the frozen text actively misleading rather than merely stale. The
amendment below is the text this run's format would require; applying it is an owner decision,
and it would move the rubric's hash and — per §6.6.4 — require a development relabel.

> ### 6. Output format the labeler must emit
>
> Strict JSON, no prose outside it:
>
> ```json
> {"evidence_sets": [
>    {"spans": [{"unit_title": "Results",
>                "first_sentence": "Pulmonary embolism occurred in 14 of 412 patients (3.4%) within 30 days of mastectomy, and 11 of these had concurrent deep venous thrombosis of the calf.",
>                "last_sentence":  "Pulmonary embolism occurred in 14 of 412 patients (3.4%) within 30 days of mastectomy, and 11 of these had concurrent deep venous thrombosis of the calf."}]}
>  ]}
> ```
>
> * `evidence_sets: []` **is** the "no localizable evidence" verdict.
> * `unit_title` is the title of the unit the span sits under, copied verbatim. It is used only
>   to **disambiguate** a quote that occurs more than once; it never overrides a match.
> * `first_sentence` and `last_sentence` are the span's first and last sentences, quoted **in
>   full and verbatim** — the whole printed line, from its first character to its final
>   punctuation. For a one-sentence span the two are the same sentence, and `last_sentence` may
>   be omitted.
> * **There are no unit or sentence indices** in the prompt and none in the answer; the labeler
>   must not invent any.
> * A deterministic checker locates each quote by **exact** match, then by
>   whitespace-collapsed case-folded match, then — only if the full sentence still does not
>   locate — by its **first eight and last eight words**, which must both land inside **one**
>   sentence of the segmented document. A quote that survives none of the three earns **one**
>   re-prompt; if it fails again the span is dropped and the **hallucinated-span rate** is
>   incremented (gate ≤ 0.05).
> * The span is `[first located sentence … last located sentence]`, snapped **outward** to whole
>   sentence boundaries (D1). A quote pair whose interval crosses a unit boundary becomes a
>   multi-span set, one span per unit it touches.

---

*κ(human–human) and κ(judge–human) are `PENDING-HUMAN`. They require the two-reader R-dev read of
§6.6.2; no agent read was substituted and none was performed. Enumeration recall against
human-marked evidence sets — r3 §3.7 item 5's stated gate — is **absent, not zero**.*
