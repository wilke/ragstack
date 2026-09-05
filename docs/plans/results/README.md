# Phase-0 evidence record

The run reports behind [chunking-evaluation.md](../chunking-evaluation.md) and
[long-doc-judged-set.md](../long-doc-judged-set.md). Every one of them existed only in a
session scratch directory until it was committed here; they are copied **verbatim**,
unedited, and verified byte-identical by `sha256`, so a number quoted in a plan can be traced
to the run that produced it.

The plans are where a finding is *acted on*. These files are where it is *recorded*. If the
two ever disagree, the run report is the measurement and the plan is the reading of it — fix
the plan.

**Read § *Conclusions that were later revised* before quoting anything from here.** Several
of these runs overturned an earlier one, and two of them overturned their own headline. The
reports are left exactly as written, so a superseded claim is still sitting in the file that
made it.

---

## Layout

One directory per run, in the order the runs happened. Each holds the write-up, the
pre-registration it was written against (where one exists), and the machine-readable report.

| dir | run | date |
|---|---|---|
| [`step1/`](step1/) | TREC CDS coverage gate | 2026-09-04 |
| [`step2/`](step2/) | BM25 lead-only vs full-index ablation | 2026-09-04 |
| [`step3/`](step3/) | the real dense chunking contrast | 2026-09-04 |
| [`stage1/`](stage1/) | the 24-config chunking grid, Leg A | 2026-09-04 |
| [`stage1-legB/`](stage1-legB/) | the same grid, Leg B, two corpus rungs | 2026-09-05 |
| [`pilots/`](pilots/) | the §7a oracle, the Leg B and Leg C pilots, the Leg B re-run | 2026-09-04/05 |
| [`rescore/`](rescore/) | small-corpus chunk-granularity re-score | 2026-09-05 |
| [`breadth-k/`](breadth-k/) | breadth × k | 2026-09-05 |
| [`design/`](design/) | four analyses written across the whole record, plus five figures | 2026-09-05 |

Read them in that order — most have a "read this first" block that assumes its predecessors.

**Three things about the copies, none of which is a defect in the reports:**

- **Links to `.py`, `.jsonl` and to intermediate `.json` artefacts do not resolve.** Each
  report ends with a file manifest naming the harness that produced it; the harness and its
  intermediates stayed in the run directory. The manifests are kept because they say what was
  run, not because they are navigable.
- **`RESULTS-legBC-pilots.md` links to its predecessor as `../stage1/RESULTS-stage1-legA.md`.**
  That link resolves here, because the layout matches the run directory's.
- **Links back to the plans are written as `../../../docs/plans/…`** from the run directory.
  They resolve unchanged from here, because this directory sits at the same depth. Nothing to
  fix, but do not assume it of a future copy.

---

## The runs

### [`step1/`](step1/) — does TREC CDS clear the coverage gate?

[`RESULTS-step1-cds-gate.md`](step1/RESULTS-step1-cds-gate.md). No machine-readable report:
this was a measurement pass, not a scored experiment.

**PASS on the step-1 gate, by wide margins.** 90 topics, 12,307 distinct grade≥1 PMCIDs,
**98.5%** fetchable (Wilson 95% CI 95.7–99.5), median body **4,097** tokens, JATS parse
237/237 with zero empty bodies. It also corrected its own first pass: `shuf --random-source`
had put 28 of 60 draws in one decile, and every figure was re-drawn properly seeded.

**Explicitly not callable:** §8 check 1 (≥80% of judged docs over 2,048 tokens) measured
**80.2%** with CI 74.1–85.2 — it *straddles* its own line and is left uncalled rather than
rounded into a pass.

### [`step2/`](step2/) — can a lead-chunk baseline already solve the set?

[`RESULTS-step2-lead-ablation.md`](step2/RESULTS-step2-lead-ablation.md),
[`report2.json`](step2/report2.json), [`position_diag.json`](step2/position_diag.json).

**FAIL.** On the pre-registered instrument the full index is *not* better than lead-only:
recall@100 gap **−0.006** against a ≥0.15 bar; nDCG@10 gap **−0.017** against ≥0.10. The most
generous alternative arm (whole-document) tops out at +0.059 recall@100, CI upper bound
+0.098 — still under the bar.

It then inferred that Leg A could not rank chunking configs and recommended demoting it.
**That inference was falsified by step 3** — see the revisions section.

*On the two reports:* `report2.json` is the one the write-up names. A first-pass
`report.json` covering only the `full`/`lead` arms was superseded by it (`report2.json` adds
the `whole` and `full_sum3` arms and the bootstraps) and is not committed.

### [`step3/`](step3/) — run the experiment the proxy vetoed

[`PREREG-step3.md`](step3/PREREG-step3.md) (predictions and falsification bar, written before
any embedding call), [`RESULTS-step3-real-experiment.md`](step3/RESULTS-step3-real-experiment.md),
[`report3.json`](step3/report3.json).

**Configs do separate on Leg A; the demotion is reversed.** tok2048 − tok512 nDCG@10
**+0.137**, CI [+0.051, +0.225], 8/10 topics; tok2048 − tok256 recall@100 **+0.043**, CI
[+0.015, +0.077]. The plan's check 4 — never run in step 2 — passes **10/10**.

The mechanism is recorded because it transfers: dense retrieval *confirms* step 2's negative
at first stage (lead-only beats the full 512 index, −0.062, CI [−0.084, −0.040]) and the
**reranker reverses it** (grade≥2 MRR@10 +0.299, CI [+0.074, +0.542]). A BM25-only test was
structurally blind to the component that reads passages.

*Two labels the report insists on:* `whole4096` is really a head-4000-token arm, not a
whole-document control; and reranked numbers rank arms, they do not grade the product.

### [`stage1/`](stage1/) — the 24-config grid on Leg A

[`PREREG-stage1.md`](stage1/PREREG-stage1.md),
[`RESULTS-stage1-legA.md`](stage1/RESULTS-stage1-legA.md),
[`tables.md`](stage1/tables.md) (generated Tables 1–8),
[`NOTES-banked.md`](stage1/NOTES-banked.md) (written *during* the run, before results were
known — it is where the power-floor arithmetic was first stated),
[`report1.json`](stage1/report1.json).

968M tokens, 94 minutes, 0 retries, 0 store writes.

**Overlap buys nothing at any size.** 12.5% − 0% = **−0.0210**, CI [−0.047, +0.007], with
δ80 = 0.046 below the 0.05 bar — a powered null. On recall@100 the effect is ≤0.0033 in
absolute value at every size.

**What predicts the grid is realised size, not method.** `corr(log₂ realised tokens,
nDCG@10) = +0.811` against +0.654 for nominal size; the residual by kind spans 0.054, about
the noise floor. Labelled **exploratory — not pre-registered**.

**The pre-registered primary contrast could not have answered its own question** — see the
revisions section. Nothing in the family resolves under the pre-registered rule.
`sentence_tok512 − fixed_tok512` = **+0.0606**, CI [+0.0138, +0.1072], 7/2, clears every
criterion *except* Holm (adjusted p = 0.097) and is flagged as the grid's most promising
signal rather than buried or promoted.

### [`stage1-legB/`](stage1-legB/) — the same grid on Leg B

[`PREREG-stage1-legB.md`](stage1-legB/PREREG-stage1-legB.md),
[`RESULTS-stage1-legB.md`](stage1-legB/RESULTS-stage1-legB.md),
[`tables-legB.md`](stage1-legB/tables-legB.md) and
[`tables-legB-all.md`](stage1-legB/tables-legB-all.md),
[`report-legB.json`](stage1-legB/report-legB.json) and
[`report-legB-all.json`](stage1-legB/report-legB-all.json).

811M tokens, 0 retries in 132,689 requests, 0 store writes. Bar **X_B = 0.010** nDCG@10,
derived in advance as 22.3% of the pilot's realised spread, because Leg A's 0.05 would be
nearly the whole of Leg B's headroom.

**The overlap null replicates, on uncontested evidence.** 12.5% − 0% = **−0.0040**, CI
[−0.0098, +0.0015], δ80 = 0.0081 *below* the bar. At the ×11.5 rung overlap moves recall@100
by **exactly 0.0000** at every size.

**The Leg A / Leg B size disagreement is one step wide.** Not a uniform reversal: they
disagree at **512→1024** (Leg A +0.1204, CI [+0.052, +0.186]; Leg B −0.0182, CI [−0.031,
−0.006]) and nowhere else that both legs resolve. Q2 is recorded as **not establishable**,
under a rule tightened *after* seeing the data — in the conservative direction, and declared.

**Behind the reranker, no size contrast resolves on either leg.** Leg B's dense 2048 − 256 is
−0.0754; reranked it is −0.0136 with a CI spanning zero; the shrinkage itself resolves at
−0.0618, CI [−0.090, −0.034].

### [`pilots/`](pilots/) — the oracle, Leg B, Leg C, and σ_d

[`BRIEF-legBC-pilots.md`](pilots/BRIEF-legBC-pilots.md) (the task brief, not results),
[`RESULTS-legBC-pilots.md`](pilots/RESULTS-legBC-pilots.md),
[`RESULTS-legB-rerun.md`](pilots/RESULTS-legB-rerun.md).

**Check 2 finally has a number, on all 90 CDS topics, and it passes with room.** **55.4%** of
judged pairs have their best-supporting section starting past token 1,024 (bar ≥40%,
topic-clustered CI 53.0–58.4); **11.9%** in abstract+intro (bar ≤35%). 2,161 pairs, 2,095
documents.

**Leg C's resolvability expectation is falsified.** Resolving via pmid + pmcid + doi gives
**11.86%** — the union over all three keys is 6,767 pairs against pmid alone at 6,759. Eight
more. The plan's note that the recorded 11.8% was a "pmid-only floor" is wrong.

**The Leg B re-run** ([`RESULTS-legB-rerun.md`](pilots/RESULTS-legB-rerun.md)) re-ran the
pilot against a real served LLM with three fixes implemented as code. Yield **65.0%**
(260/400) automated; queries median 12 words, compound queries 64.3% → **0/260**; check 4
passes at its literal pre-registered pair, **100.0%** (260/260) against a ≥25% bar. σ_d
measured on Leg B's own queries is **0.152** — which *falsified* round 1's expectation that
Leg B would be noisier than Leg A — and reversed round 1's sizing conclusion: ~1,500 queries
is comfortable and 1,000 would do.

Six of fifteen gated readings in that round **failed their own power check and are written as
unresolved, not as nulls**, including the claim that the re-run's read-level quality improved
(20.0% vs 23.8% bad accepts, against a δ80 of 27.5 pp).

### [`rescore/`](rescore/) — the small-corpus chunk-granularity re-score

[`PREREG-rescore.md`](rescore/PREREG-rescore.md) (frozen before the first embedding call;
Addendum A1 added before any scoring code ran),
[`RESULTS-rescore-small-corpora.md`](rescore/RESULTS-rescore-small-corpora.md),
[`report-rescore.json`](rescore/report-rescore.json). Provenance and post-hoc controls:
[`provenance-rescore.json`](rescore/provenance-rescore.json),
[`estats-rescore.json`](rescore/estats-rescore.json),
[`corpus_tokens.json`](rescore/corpus_tokens.json),
[`posthoc-random-chunk-baseline.json`](rescore/posthoc-random-chunk-baseline.json).

5.22 GPU-minutes against a 45-minute ceiling. A reproduction gate recovers Leg B's published
dense nDCG@10 to **four decimals** (max |diff| 0.00005) through a separate chunking pass, a
separate embedding pass and a separate scoring path.

**The study has been measuring the wrong half.** At a **one-document** corpus, where every
document metric is **1.0000 by arithmetic**, the top-ranked chunk lands in the gold section
only **55–65%** of the time. `Gap@1 = DH@1 − PH@1` is **+0.28 to +0.45**, **RESOLVED 15 of
15** against a power floor of ≈0.087 every reading clears by 3–5×. It is a **top-1**
phenomenon: by k=10 the gap is ~0 and two pre-registered predictions aimed at k=10 **failed**.

**Chunk size stops mattering at the document level as the corpus shrinks and goes on
mattering, undiminished, at the passage level.** Document nDCG@10 four-cell spread: 0.0732 at
N=5,000 → 0.0403 at 100 → 0.0255 at 10 → **exactly 0.0000** at 1. Budget-matched passage
hit-rate spread over the same sizes: 0.181 / 0.181 / 0.173 / **0.154**. At N=100 the passage
effect is **4.5×** the document one.

**The "just stuff the corpus in the window" alternative is live at N≈10 and dead at N=100.**
94.6% of 10-document corpora fit 131k tokens; **0%** of 100-document ones do (median 1.03M).

*Two disclosures the report makes against itself:* the primary pre-registered metric
(`R_B@4096`) **does not resolve** and is not converted into a null; and the harness emits a
`spread_reading` field computed under the wrong rule for a large spread, left in
`report-rescore.json` unedited with an instruction to ignore it.

### [`breadth-k/`](breadth-k/) — does the value of `k` grow with breadth?

[`PREREG-breadth-k.md`](breadth-k/PREREG-breadth-k.md) (its sha256 begins `3264cae8354f`,
recorded in the provenance file before the first embedding call — and it matches the copy
committed here), [`RESULTS-breadth-k.md`](breadth-k/RESULTS-breadth-k.md),
[`report-breadth-k.json`](breadth-k/report-breadth-k.json). Provenance, gates and post-hoc
control: [`provenance-breadth-k.json`](breadth-k/provenance-breadth-k.json),
[`estats-breadth-k.json`](breadth-k/estats-breadth-k.json),
[`gate-legb.json`](breadth-k/gate-legb.json),
[`posthoc-crowding.json`](breadth-k/posthoc-crowding.json).

6.75 GPU-minutes. The Leg B half cost **zero GPU** — 11 s of CPU re-using the `rescore`
embeddings. Reproduction gate: 28 gated rows recover the earlier Leg B table, max |diff|
**0.000465** against a 0.0005 tolerance.

**The hypothesis is not supported, and the reason is more useful than the hypothesis.** The
pre-registered primary interaction is **−0.014 to −0.032** across the four chunk sizes — two
powered nulls, two unresolved, negative on every arm.

**And the residual is *structurally* zero, not empirically null.** With query and embedding
fixed, the ranking does not depend on `m` at all; `m` reaches the k-curve through exactly two
channels (the `min(1,k/m)` ceiling, absorbed by the random-ranking null, and inter-gold
competition), and there is no third. The one contrast that resolved is that competition seen
on the gap.

**What differs between the legs is difficulty, not breadth, and it is enormous** — the lumped
leg term is **−0.43 to −0.48 on PH@1**, roughly **15×** any candidate breadth effect.

**Budget-matched, the fixed-`k` chunk-size ordering reverses at every rung**: best raw `PR@1`
is `tok2048`, best `PR_B@4096` is `tok256`, by **2.2× at m=1 and 3.8× at m=16**.

---

## The design analyses

Four written across the whole record rather than against one run. They contain some of the
sharpest corrections in the set, and several of their findings are not in any run report.

| file | question | headline |
|---|---|---|
| [`ANSWER-completeness-and-subsets.md`](design/ANSWER-completeness-and-subsets.md) | Is the size sweep complete? Should corpus subset be a random effect? Do the results say anything about 10M documents, or about 1–100? | The gap is not sizes — the realised-token axis already spans 164–2,048 at 14 distinct points. The missing quantity is **τ, the between-corpus SD of a contrast**, the last unmeasured variance component. Fix is cost-neutral: build Leg B as 6–12 disjoint blocks on disjoint rungs. **The 10M target is unreachable** — the corpus holds 1,439,753 articles. |
| [`ANSWER-sufficiency-and-judges.md`](design/ANSWER-sufficiency-and-judges.md) | What would "sufficiency" mean operationally, and who judges it? | The missing measurement is **nugget support at a fixed context budget**, not answer quality. Equal delivered *tokens*, not equal `top_k` — Sufficiency@10-chunks is monotone in chunk size by construction. ~300 judged queries per leg. **HyDE is rejected in its original form** and adopted inverted. |
| [`ANSWER-provenance-and-repro.md`](design/ANSWER-provenance-and-repro.md) | How should this be packaged so someone else can re-run it? | **Scripts + a typed run manifest + a committed report tree, not CWL.** 16 capture rows, 12 of which already exist. Also the most alarming audit in the set — see the revisions section. |
| [`ANSWER-visualisation-audit.md`](design/ANSWER-visualisation-audit.md) | What can this study honestly be graphed as? | Four or five figures carry real information, and the most important results are the ones that plot well. **The two most tempting plots are rejected** — a ranked bar chart of the 24 configs and a chunk-size trend line would both draw *unresolved* results as effects. Every quantity in figures 2 and 3 was recomputed from the per-query arrays before drawing: **zero mismatches**. |

---

## The figures

Five SVGs in [`design/figures/`](design/figures/). Hand-written through a ~150-line
dependency-free plotting layer — `matplotlib` is not installed in any interpreter on the host
and nothing was installed to make them.

| file | the claim it carries |
|---|---|
| [`fig1-realised-vs-nominal.svg`](design/figures/fig1-realised-vs-nominal.svg) | Realised vs nominal chunk tokens by kind — "size" is not a comparable axis across kinds |
| [`fig2-quality-vs-realised-tokens.svg`](design/figures/fig2-quality-vs-realised-tokens.svg) | Quality vs realised tokens, both legs, 95% bootstrap CIs — realised beats nominal (r +0.811 / −0.974 vs +0.654 / −0.870) |
| [`fig3-dense-vs-reranked.svg`](design/figures/fig3-dense-vs-reranked.svg) | The decision figure: a contrast forest, dense vs reranked, with δ80 floors and bar-X bands |
| [`fig4-position-of-evidence.svg`](design/figures/fig4-position-of-evidence.svg) | ECDF of evidence start token with a topic-clustered band — 55.4% past token 1,024 |
| [`fig5-document-lengths.svg`](design/figures/fig5-document-lengths.svg) | Document-length ECDFs vs the reported BEIR summary stats — trec-covid's longest document is 925 tokens |

> ### ⚠ These figures have never been looked at
>
> They passed a **programmatic geometry audit** — `check.py` checks every SVG for
> out-of-canvas elements and text collisions, all five pass clean, and `fig2.py`/`fig3.py`
> carry build-failing assertions that no confidence interval is clipped. But **there is no
> SVG rasteriser on the host that made them** (no `rsvg-convert`, `inkscape`, ImageMagick,
> headless browser or `cairosvg`), so they were **never actually rendered**. The audit
> estimates text width as 0.55 × font-size × characters, a heuristic rather than a font
> metric.
>
> **Open all five in a browser before circulating them.**

Four caveats that must travel with the figures if they are reused:

- **fig1** — semantic's `fill` column is not a fill (its `size` is a cap that mostly does not
  bind), and the scifact overlay is *quoted from the plan, not measured here*.
- **fig2** — two panels, shared log₂ x, **separate y**. A shared y-axis is the one thing that
  would have made this figure dishonest. Leg A's range was widened to 0.23–0.81 because a
  range fitting the point estimates clipped 32 of 48 CI endpoints. The r values are labelled
  exploratory: the 24 points are means over the same queries and are not independent.
- **fig4** — the oracle *is* `bge-reranker-v2-m3`, the production model, so this measures what
  that reranker can find, not ground truth. Leg B's position distribution must never be added
  to it: 100% past token 1,024 is the sampling rule, not a finding.
- **fig5** — scifact and trec-covid have no per-document lengths, only reported summary
  statistics. They are drawn as labelled reference marks and must **never** become curves.

---

## Conclusions that were later revised

The reports are unedited, so every superseded claim is still sitting in the file that made
it. This is the list.

**1. Step 2 concluded Leg A was unusable. Step 3 falsified it by running the experiment.**
The BM25 lead-only ablation measured full − lead recall@100 = −0.006 against a ≥0.15 bar and
inferred that a chunking grid on Leg A "would be measuring differences smaller than the
lead/full difference, indistinguishable from zero". The real dense pipeline separates configs
at +0.137 nDCG@10 (CI [+0.051, +0.225], 8/10 topics), and check 4 — the pre-registered
instrument for *"can this leg rank configs"*, which step 2 never ran — passes 10/10.
**Step 2's measurements all stand and reproduce exactly; its inference does not.** Check 3
genuinely does fail on CDS in BM25 and dense alike, and has been re-registered as what it
measures: a first-stage lead-chunk-sufficiency diagnostic, i.e. reranker load.

**2. The pre-registered size × overlap interaction was structurally unanswerable, not null.**
`I` is a difference of two differences — four cells — so its per-topic variance compounds:
measured `sd_d` **0.214**, giving an 80%-power detectable effect of **0.213** nDCG@10 against
the **0.05** bar written for it. **4.3× too coarse.** At n = 10 it could not have returned a
positive answer whatever the truth, and that was knowable in advance from the contrast's
variance structure. Leg B declared the same contrast unreachable *before* its run (δ80 ≈0.043
against X_B = 0.010, again 4.3×) and reported it as unresolved regardless of what it returned.

*The null that is real is the slope form*, which is adequately powered on both legs:
Leg A **+0.0101**, CI [−0.022, +0.044]; Leg B **+0.0011**, δ80 0.0061 — the tightest null in
the study. So: *the interaction as posed cannot be resolved, but the overlap effect is small
everywhere and its dependence on size is small.* Do not compress those into "no interaction".

> Two cross-document number discrepancies here, left unharmonised because each doc computed
> under its own convention: Leg A's slope δ80 is **0.056** in the Leg A write-up and
> **0.0501** when recomputed in the Leg B write-up; Leg A's overlap 12.5%−0% δ80 is **0.046**
> in the Leg A docs and **0.041** when requoted in the Leg B one. Quote whichever doc you are
> citing, with attribution.

**3. `semantic`'s four size cells are not four sizes.** Realised medians on Leg A are
**255 / 359 / 357 / 343** tokens at nominal 256 / 512 / 1024 / 2048 — it emits ~350-token
blocks whatever the ceiling says, and the cap only binds at 256. Leg B replicates at
**255 / 324 / 350 / 351**; semantic is the only kind whose realised size moves with the
corpus at all. Its `size` is a **cap**, and the real size knob is
**`breakpoint_percentile_threshold`** — the threshold is a percentile of each document's own
distance distribution, so the *fraction* of gaps cut is fixed at `1 − p/100` by construction,
giving ~5 sentences per block whatever `size` is. **The grid never touched that knob.** So
"semantic has one natural size" is only true as *"semantic has one natural size at p = 80"*.
Its `fill` column must not be read as the other kinds' fill; report `cap_bind_rate` instead.

**4. `words`/`sentence` in Leg A and Leg B ran what the repo now calls the legacy `summed`
fill — and the two kinds are affected differently.** Every Phase-0 run pins `d225cea`, where
`budget_mode` did not exist and the summed behaviour was the only behaviour; `55a0fc2`
(#488) made `joined` the default and kept the old path as `budget_mode="summed"`, so the
label post-dates the runs. Measured over-count of a joined chunk by a sum of per-unit counts:
**1.497× per word** (range 1.433–1.629) but **1.000× per sentence, on all 12 documents
tested** — a BPE tokenizer merges the space before a word into that word's token, and a lone
word cannot show that merge, but a sentence's isolated count already equals its joined count.

So:

- **`words` is distorted by it**, flat at **0.64** fill at every nominal size on both legs
  (164 / 328 / 656 / 1307 realised tokens) — multiplicative, hence scale-free. `words_tok512`
  is not "words at 512 tokens", it is words at 328. A **labelling** bug, not a correctness
  one: no text is dropped and no chunk exceeds budget.
- **`sentence` is not.** Its fill **rises 0.89 → 0.94 → 0.96 → 0.97** with size, because its
  waste is one partial sentence per chunk — a shrinking fraction of a growing budget. That is
  a **property**, and it is a range, not the constant ~0.95 it is sometimes quoted as.

**Neither row is comparable to a future run at `55a0fc2` or later**, and the report's own
counter-instruction should travel with that: *do not silently fix `words` before stage 2* —
`words` at 0.64 and `semantic` pinned at ~350 are the only places realised and nominal size
come apart, and repairing them turns the realised-size model into an unfalsifiable
restatement of the size axis. Turn each into a manipulation instead.

> A distinct, adjacent defect on the same two kinds, easy to conflate with the above: their
> packer takes overlap in **chars** at `OVERLAP_CHARS_PER_TOKEN = 2.5` while production
> measures 3.50, so the rows labelled 12.5% carry **≈8.9% effective overlap**. Only the
> `token_window` rows are exact.

**5. "Narrow queries saturate" is wrong. It is easy versus hard.** The breadth × k hypothesis
assumed a narrow query saturates — one gold passage, once found extra `k` adds nothing.
Leg B's narrow queries do saturate (`PH@k` 0.515 → 1.000 by k=20). **Leg A's equally narrow
`m=1` queries do not saturate at all**: `k*` = 20 at every rung of the ladder and `PR@20` is
still only 0.320. Both rows have exactly one gold passage per query. The difference is that
**Leg B's queries were *written from* their gold section**, so the retriever is being asked to
invert a generator, while Leg A's gold is a cross-encoder's best unit inside a document TREC
merely judged topically relevant. A naive Leg-A-vs-Leg-B comparison would have charged the
whole −0.43 to −0.48 leg term to breadth.

**Consequence for reading Leg B at all:** its absolute numbers (nDCG@10 0.92–0.99, 94–98% of
queries putting the gold document at rank 1) are an upper bound on a real user's experience,
not an estimate of it.

**6. The Leg B re-run's own headline was downgraded by the Leg B grid.** The re-run called the
recall@100 extremes contrast "the head-to-head that does stand — both legs resolve". Recomputed
in [`stage1-legB/`](stage1-legB/RESULTS-stage1-legB.md) §7.2 under the identical δ80 convention
on both legs, **Leg A's side does not clear its own power floor**: +0.0432 against δ80 =
0.0466. It sits *at* the design's resolution, not above it. The study's one genuine
both-legs-resolvable disagreement is the **nDCG@10 512→1024 step**, not the recall@100 extremes.

**7. Round 1 of the Leg B pilot got σ_d and the sizing backwards.** It concluded Leg B would
be noisier than Leg A and that ~1,200–1,430 queries were needed. Measured on Leg B's own
queries in round 2: σ_d **0.152** — the same or lower than Leg A's 0.156 — and 1,500 queries
resolve δ = 0.017 under Holm, so 1,000 would already meet the plan's stated δ = 0.02.

**8. Leg C's resolvability expectation is falsified.** The plan recorded 11.8% as a
"pmid-only floor" to be improved on by also matching pmcid and doi. Measured: **11.86%**. The
union over all three keys is 6,767 pairs; pmid alone gets 6,759.

**9. Two cost models in the briefs were wrong, in opposite directions.** Semantic was expected
to "embed the text twice — roughly double cost"; it is **~7×** a `token_window` config of the
same nominal size, because breakpoint detection embeds one overlapping window per sentence
(567M notional tokens on a 4,053-document corpus, 60.4% saved by the per-document cache).
And cross-encoder throughput is **not flat**: 1,037 / 786 / 391 pairs/s at 256- / 512- /
2048-token chunks, against a flat "~658" — under-costing 2048-chunk work by 1.7×.

**10. The provenance audit's finding, which is about this record itself.** Across all seven
runs it audited, **no run captured the served embedding model id and no run captured a single
package version**; the only evidence the fleet served what was asked is `retries: 0` across
186,647 requests, *"an inference, not a record"*. Two further items worth knowing before
trusting a re-run: the study's plan **names the wrong generator for Leg B** (the plan and
`unified.env` name Scout; the run used `Qwen/Qwen3.6-35B-A3B` on `:8004`), and the
determinism gate's `0.0e+00` result proves the harness has no hidden nondeterminism of its
own — it does **not** promise byte-reproducibility on a fleet restarted next month. The later
`rescore` and `breadth-k` runs fixed most of this: both assert their repo commit, probe the
served model live on all six endpoints and refuse to run if they differ, and hash their
corpus before embedding.

---

## What is not here, and why

- **The raw per-query arrays** (`runs-*.json.gz`, `ranked-*.json.gz` — 16.2 MB compressed,
  111 MB expanded). Measured both ways against the ~15 MB budget this record was given: the
  tree packs to **470,871 bytes** without them, **16.7 MB** with them as `.gz`, and **17.7 MB**
  with them as plain JSON. Both inclusions fail the budget, and plain JSON packed *larger*
  than pre-compressed — five distinct files give git's delta compression nothing to work with.
  Every reading in the reports is reproducible from the committed `report-*.json`; only a
  fresh re-analysis at per-query granularity needs the arrays.
- **The `.npy` similarity matrices and embeddings** — 1.8 GB, and regenerable.
- **`chunks_*.jsonl` and the fetched JATS** — hundreds of megabytes, regenerable from the
  corpus and the recorded config.
- **The harnesses themselves.** ~7,178 lines of run code across the nine runs. Each report's
  file manifest names what ran; the code is not in the repo. The provenance analysis names
  this as the record's largest remaining gap.

---

## The discipline these reports keep

Worth stating once, because the plans lean on it: every threshold in the later rounds is read
against a **power floor computed before the reading**. Fifteen readings are gated that way in
the Leg B round and **six fail their own power check and are written as unresolved, not as
nulls** — including two where a naive Wald floor would have said "resolvable" because a
proportion sat at exactly 1.0 and its standard error degenerated to zero.

That habit came out of stage 1, where the pre-registered primary contrast turned out to have
an 80%-power floor of 0.213 nDCG against the 0.05 bar written for it — 4× too coarse, and
knowable in advance from its variance structure. A contrast's power floor is now computed
before its threshold is committed to.

Two consequences a reader should hold onto:

- **"Powered null" and "unresolved" are different claims and never share a symbol.** "We
  looked and found nothing" and "we could not have seen anything" are not the same sentence.
- **An unresolved reading is never written up as a null**, even when it would tidy the story.
  The `rescore` run's own pre-registered primary metric does not resolve, and says so.
