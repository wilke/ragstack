# Completeness of the size sweep, corpus-subset replication, and scale bracketing

*Analysis and design only. No experiment was run, no store was written, no embedding endpoint
was called. Three read-only measurements were taken and are marked **[measured here]**: a full
scan of `/rag/oa/corpus/manifest.jsonl` (1,439,753 records), an article-type sample of 2,755
XML files, and a CPU tokenizer check with the cached `Salesforce/SFR-Embedding-Mistral`
tokenizer. Everything else is read off the Phase-0 RESULTS files, the committed chunker
source, or arithmetic on those.*

*Repo `/home/wilke/Development/ragstack`, main `d225cea`. Inputs:
`docs/plans/chunking-evaluation.md`, `docs/plans/long-doc-judged-set.md`,
`phase0/{stage1,stage1-legB,pilots,step3}/RESULTS-*.md`,
`python/ragstack/ingestion/chunkers.py`.*

---

## 0. Answers in one place

| question | answer |
|---|---|
| **Is a `semantic` size sweep meaningful?** | Yes, but `size` is the wrong knob and always was. `size` is a **cap**; the size *knob* is `breakpoint_percentile_threshold` (with `min_chunk_length` as the floor). "Semantic has one natural size" is the finding **at p = 80**; it is not a finding about semantic chunking. A real sweep costs almost nothing — the cosine distances are independent of *p*, so four sizes share one breakpoint-embed pass. |
| **Does the missing semantic sweep matter?** | Yes, but not for config selection — for **model validation**. §9's realised-token model is the study's central claim, and `semantic` currently contributes **one point** on the realised-token axis. It is the kind with the least leverage on the model that explains it. |
| **Is `words`' 0.64 fill a bug or a property?** | **A bug**, and the mechanism is confirmed. `_pack_spans_tokens` budgets by the **sum of per-span token counts, each word tokenized in isolation**, which over-counts joined text by a median **1.497×** on real PMC text **[measured here]** → implied fill 0.668 against the measured 0.641 (residual = greedy granularity). It is multiplicative, hence scale-free, hence a flat 0.64 at every size. **Do not fix it before stage 2** (§1.4). |
| **What is *actually* missing from the size sweep?** | Not sizes. (a) No within-kind size variation for `semantic`; (b) nothing above 2,048 realised tokens although the models allow 4,096; (c) **Leg B is at ceiling** (0.93–0.98) — its fine end cannot be resolved by adding finer sizes, only by adding difficulty. (c) is the real gap, and its fix is the ladder, which makes Q1 and Q3 the same problem. |
| **Corpus-subset design** | Build the **already-planned 1,500-query Leg B set as 6–12 disjoint blocks on disjoint 5,000-document rungs**, not as one set on one corpus. Cost-neutral. It converts τ (the between-corpus SD of a contrast) from un-estimable to estimated. |
| **How much does that detect?** | τ_det ≈ σ_d·√(f(C)/T), f minimised ≈ 21 at C = 8–12. At the planned **T = 1,500 queries: τ_det = 0.018** (1.8 bars). To detect **τ = 0.010 (one bar) needs T ≈ 4,900 queries**. |
| **Random effect?** | **Yes** — one REML model on per-query contrasts with a corpus random intercept gives both the generalising Δ̄ (t on ≈ C−1 df) and τ̂ with its CI. Rung and distractor-composition stay **fixed** effects. The honest price at C = 6 is a **~30% wider CI**. |
| **Ladder or judged-corpus variation?** | Different questions. **Ladder → Q3 (scale).** **Judged corpus → generalisability, which is what Q2 asks.** Q2 needs the judged corpus to vary. A third, cheap arm nobody named — vary *distractor composition* at fixed rung size — is where the measured 6× between-journal length spread bites. |
| **Do current results say anything about 10M?** | Only by extrapolation, and **10M is unreachable on this host**: the corpus is 1,439,753 files. Largest honest bracket is ×288 over the 5,000-doc rung (×3,600 over the judged set), at **≈26 GPU-h for one config**. |
| **Do they say anything about 1–100 documents?** | Nothing, and they cannot: document-level retrieval is degenerate there. What matters instead is **passage selection**, **whole-document-in-window as a live baseline**, and **answer-span straddling** — the one thing overlap plausibly buys and the one thing the overlap null explicitly did not test. |
| **Cheapest experiment that brackets both ends** | Small end: re-score the existing ×0 rung at **chunk** granularity against Leg B's recorded gold sections — **≈10 GPU-min**. Top end: a **×100 rung (40,000 docs, 4 configs) for ≈2.9 GPU-h** as a pre-registered falsification of the miss-rate power law, then the full-corpus one-config bracket only if the law survives. |

---

## 1. Q1 — what is actually missing from the size sweep

### 1.1 `semantic`: `size` is a cap, and the size knob is elsewhere — **property, not omission**

Read from `python/ragstack/ingestion/chunkers.py`:

* `SemanticChunker.chunk` (l. 1078) splits into sentence spans, embeds buffers, cosine-distances
  consecutive buffers, and cuts wherever the distance exceeds the
  **`breakpoint_percentile_threshold`** (default **80.0**) — `_breakpoint_groups`, l. 1121.
* Because the threshold is a **percentile of that document's own distances**, the *fraction* of
  gaps cut is fixed by construction: `q = 1 − p/100 = 0.20`. Mean block ≈ `1/q` = 5 sentences,
  **whatever `size` is and whatever the document's length is**.
* `_merge_short` (l. 1136) then merges anything under **`min_chunk_length` = 500 chars**, roughly
  halving the block count and raising the median.
* `chunk_size` / `max_tokens` enter **only** via `_emit`'s token-cap split and via
  `_oversize_fallback`'s window. They are a ceiling, never a target.

That is the whole explanation of the observed medians, and the numbers check out arithmetically.
Leg B: median 212 sentences/doc, doc median 8,778 tokens → ≈41 tokens/sentence. `semantic_tok2048`
emits 22.3 chunks/doc → 9.5 sentences/chunk → ≈390 mean tokens, median 351. Observed: **351**.

**So "semantic has one natural size" is true only as "semantic has one natural size at p = 80."**
The knob exists; the grid never touched it.

**Correction to the brief's numbers.** The medians quoted in the task (255 / 359 / 357 / 343) are
**Leg A's** `cstats_semantic_*` — Leg B's are **255 / 324 / 350 / 351** (fills 1.00 / 0.63 / 0.34 /
0.17 vs Leg A's 1.00 / 0.70 / 0.35 / 0.17). That per-corpus difference is small but it is not
zero, and it is a preview of §2: `semantic` is the **only** kind whose realised size moves with
the corpus, because it is the only kind whose realised size is set by where a cap happens to bind
on that corpus's sentence-length distribution.

**What a real semantic size sweep would move, concretely.** Sweep `breakpoint_percentile_threshold`,
hold `max_tokens` at a non-binding cap (4,096), and scale `min_chunk_length` with the target
(≈0.4 × target × 3.5 chars/token). Starting points to be calibrated on a held-out sample, from
q ≈ (mean sentence tokens)/(target tokens) with the `_merge_short` inflation folded in:

| target realised median | `breakpoint_percentile_threshold` (start) | `min_chunk_length` (chars) |
|---:|---:|---:|
| ~256 | 84 | 350 |
| ~512 | 92 | 700 |
| ~1024 | 96 | 1,400 |
| ~2048 | 98 | 2,800 |

**This sweep is nearly free, and that is the point.** The per-buffer cosine distances do **not**
depend on *p*. Cache the distance array per document once and all four *p* levels — plus the
calibration passes — come out of a **single** breakpoint-embed pass. Against the measured semantic
cost (Leg B ×0: 101 M notional / 42 M actual for four *sizes*, §10.2), a four-*p* sweep costs
**one** config's breakpoint pass, not four. The study's "semantic is 6.5× a token_window config"
objection does not apply to this particular sweep.

**Does it matter?** Not for picking a config — §9 predicts the answer: the semantic rows will
trace the same realised-token curve everything else does (Leg B per-kind residual about the
realised-size fit: `semantic` −0.0016, against a bar of 0.010). It matters because **that is the
prediction and it is currently untestable**: with `semantic` pinned at ~350 realised tokens it
contributes one point, so it cannot falsify the model it is being explained by. Run it as an
add-on to stage 2, not as a stage.

**Reporting fix.** `fill` (median ÷ nominal) is meaningless for `semantic` — the denominator is
not a target. Report **`cap_bind_rate`** (fraction of chunks at exactly the cap) instead. Leg B's
own cstats already imply it: `semantic_tok256` p95 = 256, max = 258 (cap binds on nearly every
chunk); `semantic_tok2048` p95 = 1,003, max = 2,048 (cap essentially never binds). Those are two
different chunkers wearing the same kind label, and `fill` hides it while `cap_bind_rate` shows it.

### 1.2 `words`' 0.64 fill: a **bug**, mechanism confirmed

The measured fills are not approximately constant, they are **exactly** constant, on two disjoint
corpora:

| nominal | Leg A (4,053 CDS docs) | Leg B (400 PMC docs) | fill |
|---:|---:|---:|---:|
| 256 | 164 | 164 | 0.640625 |
| 512 | 328 | 328 | 0.640625 |
| 1024 | 656 | 657 | 0.640625 |
| 2048 | 1307 | 1312 | 0.6382 / 0.640625 |

A granularity artefact shrinks with size (`sentence` does exactly that: 0.895 → 0.936 → 0.956 →
0.966). A **multiplicative** bias is scale-free. This is multiplicative.

**The mechanism, located in source.** `_pack_spans_tokens`
(`python/ragstack/ingestion/chunkers.py`, l. 620) packs whole units while the **memoised
per-span token sum** stays within budget. Each span is tokenized **in isolation**. Its own
docstring says so: *"the per-span sum over-counts the joined chunk (tokens that merge across a
unit seam are double-counted) … it just forgoes the seam-merge reclaim."* For `words`, **every
unit boundary is a seam**, so the over-count is maximal and proportional to chunk length.

**[measured here]** — SFR tokenizer, 12 PMC OA bodies, ~2,000–3,000 tokens each:

| unit | Σ(isolated token counts) ÷ joined token count | implied fill |
|---|---:|---:|
| per **word** | **1.497** (range 1.433–1.629) | **0.668** |
| per **sentence** | **1.000** (all 12 documents) | **1.000** |

That closes the case in both directions at once:

* `words` measured fill 0.641 vs predicted 0.668 — the residual 4% is the greedy packer stopping
  before it would exceed budget. **Bug.**
* `sentence` over-counts by **nothing** (ratio exactly 1.000), so its 0.89 → 0.97 fill is pure
  whole-unit granularity, shrinking with size exactly as it should. **Property.**

**Severity: not a correctness bug, a labelling bug.** No text is dropped and no chunk exceeds
budget. But `words_tok512` is not "words at 512 tokens", it is "words at 328 tokens", and any
kind contrast read at *nominal* size is a realised-size contrast in disguise — which is exactly
what §9 concluded and correctly conditioned on.

**Documentation defect to fix regardless.** `_pack_spans`' docstring (l. 569) claims *"the
candidate combined chunk is then verified against the exact joined-token count"*. The token path
it delegates to, `_pack_spans_tokens`, deliberately does **not** do that verification — it says so
in its own docstring. One of the two is wrong; the code is right and `_pack_spans`' docstring is
stale. One-line fix, no behaviour change.

### 1.3 What is actually missing — and it is not sizes

The pooled realised-token axis across all four kinds already spans **164 … 2,048** with 14
distinct points: 164, 229, 255/256, 324/328, 350, 479, 512, 656, 979, 1024, 1307/1312, 1979,
2048. That is a denser curve than a nominal-size reading suggests. What is genuinely absent:

1. **No within-kind size variation for `semantic`** — §1.1.
2. **Nothing above 2,048 realised**, although the embedder window is 4,096 and the reranker's
   measured hard cut is 4,096 for the (query, chunk) pair. Leg A's whole coarse-wins direction
   points *up* the axis and the grid stops one doubling short of the models' own limit. Phase-0
   step 3 had a `whole4096` arm; the 24-cell grid does not. **If Leg A is right, the grid does
   not contain the winner.**
3. **Leg B is at ceiling, and that is the real gap.** nDCG@10 spans 0.931–0.984 at ×0 and
   0.868–0.960 at ×11.5, on a near-binary one-relevant-document task. The three finest realised
   points (164 / 229 / 255) are within 0.0019 of each other. **Adding finer sizes to Leg B buys
   nothing** — the instrument is saturated, not under-sampled. Leg A has headroom (0.40–0.63) and
   there its optimum is bracketed *descriptively* — interior maximum at 1024 with 2048 below it
   — but **not resolvably**: the 2048−1024 step is −0.0062 against δ80 = 0.0586 at n = 10.

**So the fix for the size sweep is not more sizes, it is a harder Leg B — which is the ladder.**
That collapses Q1 into Q3 and is why §3 is where the money should go.

### 1.4 Do **not** silently fix `words` before stage 2

This looks backwards and is not. The realised-token model — the study's strongest cross-leg
agreement (§9: Leg B *r* = −0.974 realised vs −0.870 nominal; kind spread 0.0143 → 0.0023) —
gets essentially all its leverage from the two arms this document just called defective. `words`
at fill 0.64 and `semantic` pinned at ~350 are the **only** places where realised and nominal
size come apart. Repair them and the model becomes an unfalsifiable restatement of the size axis.

The productive move is the opposite: **turn each defect into a manipulation.**

* Keep the current `words` **and** add a seam-reclaimed `words` (verify the joined count before
  accepting a unit). The pair is the study's only **within-kind, fixed-nominal-size** manipulation
  of realised size — a causal test of the realised-token model rather than a fit to it.
  Pre-register: if the model holds, `words_tok512` (328 realised) and
  `words_tok512_reclaimed` (≈480 realised) differ by the fitted slope
  (`−0.0140 × log₂(realised)` on Leg B → predicted −0.0077) and by nothing else.
* The semantic *p*-sweep of §1.1 is the same manipulation for `semantic`.

Two cheap arms, and the study's central claim goes from *fitted* to *tested*.

---

## 2. Q2 — corpus-subset replication

### 2.1 First, what the determinism result actually establishes

Chunking is deterministic and PB6 reproduced five cells at max |diff| = **0.0e+00** over 396
queries at both rungs. That is a **software-determinism gate, not a statistical replication.** Its
real consequence is the opposite of reassuring: it says the **measurement error is exactly zero**,
so *every* remaining source of uncertainty is sampling. There are exactly two components:

| component | status |
|---|---|
| **query sampling** — σ_d | **measured**: 0.119 (×0), 0.152 (×11.5) on Leg B; 0.156 / 0.173 on Leg A |
| **corpus sampling** — τ | **never measured, on either leg** |

τ is the last unmeasured variance component in the whole study, and every CI published so far
conditions on one corpus, i.e. assumes τ = 0.

**And C = 2 (the two legs) cannot estimate it**, because Legs A and B differ in *four* things at
once — corpus, query construction, relevance definition, and metric. The 512→1024 contradiction
(A +0.1204 vs B −0.0182) is currently attributable to any of the four. **The corpus-subset design
is the way to unconfound exactly one of them: hold Leg B's query-construction pipeline fixed and
move only the documents.**

### 2.2 What varies across subsets — measured on the real corpus

**[measured here]** Full scan of `/rag/oa/corpus/manifest.jsonl`, 1,439,753 records, 0 parse
failures.

**(a) Document length.** Corpus `body_chars`: p05 11,788 · p25 26,209 · **median 35,295** · p75
47,319 · p95 74,241 · p99 108,097. A 6.3× p05→p95 spread. Per-journal *medians* span **10,471
(Emerging Infectious Diseases) to 63,870 (eLife)** — also ~6×.

This is the top-priority axis because of a conditional that must be stated precisely:

> **Realised chunk size is a corpus-invariant function of the chunker *conditional on
> documents ≫ budget*. The corpus enters through the fraction of documents shorter than the
> budget.**

The unconditional form is false and the study already has the counter-example: `token_window`
fill on scifact is 1.00 / 0.65 / 0.35 / 0.17 (documents shorter than the budget) against 1.00 at
every size on Leg B. Where the conditional holds, invariance is near-total — Leg A (4,053 CDS
docs) vs Leg B (400 PMC docs), same chunker, same commit:

| kind / size | Leg A median tok | Leg B median tok | Δ |
|---|---:|---:|---:|
| sentence 256 / 512 / 1024 / 2048 | 229 / 479 / 979 / 1977 | 229 / 479 / 979 / 1979 | ≤ 0.1% |
| words 256 / 512 / 1024 / 2048 | 164 / 328 / 656 / 1307 | 164 / 328 / 657 / 1312 | ≤ 0.4% |
| semantic 256 / 512 / 1024 / 2048 | 255 / 359 / 357 / 343 | 255 / 324 / 350 / 351 | up to **9.7%** |

But `chunks_per_doc` moves a lot (sentence512: 15.76 on Leg A, 22.08 on Leg B) because document
length moves. **So a corpus effect cannot enter through realised chunk size for three of the four
kinds; it enters through chunks-per-document, through where semantic's cap binds, and through the
query/relevance and distractor sides.** That is a genuinely sharper statement of what a subset
replication is testing than "the corpus might differ".

**(b) Structural markup — the biggest hazard, and it is journal-concentrated.** Corpus-wide only
**1.41%** of records have `body_chars == 0` and 2.40% have `< 5,000`. But:

| journal | docs | % body_chars == 0 | % < 5,000 | % n_sections == 0 |
|---|---:|---:|---:|---:|
| **Open Forum Infectious Diseases** | 24,659 | **72.9** | 77.6 | 78.0 |
| Retrovirology | 4,206 | 0.8 | **60.7** | 6.6 |
| Emerging Infectious Diseases | 13,627 | 0.1 | 11.2 | **38.0** |
| PLoS Biology | 5,079 | 0.0 | 7.3 | 19.3 |
| Journal of the American Chemical Society | 5,879 | 0.0 | 0.1 | 18.9 |
| Scientific Reports / PLoS ONE | 545,227 | 0.0 | 0.0 | 0.0 |

**An OFID-drawn subset is 73% abstract-only — the scifact degeneracy reproduced inside PMC.** At
size 2048 those documents are a single chunk and neither the size nor the overlap contrast has
anything to bite on. A random draw hides this at 1.4%; a journal-blocked draw exposes it at 73%.
The manifest's `body_chars` measures the hazard per candidate subset for free, before any GPU is
spent — that check should be a gate on every subset.

**(c) Article type.** **[measured here]**, 2,755 XML files sampled from 120 leaf shards:
`research-article` **86.9%**, `review-article` 6.1%, `abstract` 1.6%, `brief-report` 1.5%,
`case-report` 1.4%, remainder ≤0.7% each. Reviews lack Methods/Results and carry aboutness
differently; `abstract` is a degenerate one-paragraph document. Note this **exonerates Leg B's
sampler**: its 35/260 = 13.5% non-`research-article` accepted sources match the corpus's own
13.1% almost exactly. That is the corpus, not a sampling defect.

**(d) Journal / domain mix — and why random subsets vary far less than the framing implies.**
344 distinct journal strings, but **top-10 = 65.2%**, top-50 = 92.2%, top-200 = 99.8% of the
corpus. Consequences for two 400-document random blocks:

* SD of "fraction with `body_chars` < 5,000" = √(0.024·0.976/400) = **0.0076** → blocks differ by
  ~±1.5 pp.
* SE of the block median `body_chars` ≈ 1.2533·σ/√n with σ ≈ IQR/1.349 = 15,652 → **±981 chars
  (±2.8%)** at n = 400, ±277 at n = 5,000.

Against a **6× between-journal** spread in the same statistic. **So random disjoint subsets test
sampling stability, not compositional generalisability, and those are different τ's.** Say which
you are estimating:

| design | estimates | expected size | is it a random effect? |
|---|---|---|---|
| C random disjoint blocks | **τ_sample** — "would another random draw give the same answer?" | small | **Yes** — levels are exchangeable draws |
| journal/length-blocked arms | **τ_compose** — "does the answer depend on what kind of documents?" | large | **No** — levels are chosen, so they are fixed effects / a moderator analysis |

Reporting τ_compose as a variance component would be a category error: those levels are not a
sample from anything.

**(e) Unmeasured and worth naming.** Publication era — the manifest carries `fetched`, not
publication date, so JATS-convention drift over time is invisible to this scan and would need the
XML. And near-duplicate / topical clustering, which matters for §3's exchangeability assumption
rather than for §2.

### 2.3 The design: block the query set you are already going to build

**This is the headline.** The plan already intends a ~1,500-query Leg B set
(`long-doc-judged-set.md` §6, `chunking-evaluation.md`). Building it as **one** set on **one**
corpus is a pseudo-replication: it buys within-corpus precision (δ80 = 0.011 at n = 1,500) and
leaves τ entirely unestimated.

> **Build the same 1,500 queries as C ≈ 6–12 disjoint blocks, each on its own disjoint
> 5,000-document ×11.5 rung.** Same number of source documents, same number of queries, same
> number of embedded tokens. τ goes from un-estimable to estimated. The design is cost-neutral.

Concretely, for C = 6:

* 6 × 400 source documents = 2,400, drawn **disjointly** from the 1,439,753-file corpus with the
  same stratification as Leg B's sampler.
* 6 × 4,600 seeded distractors = 27,600. Total 30,000 documents = **2.1% of the corpus** — the
  disjointness constraint is nowhere near binding.
* ~260 accepted queries per block (Leg B's measured 65% accept rate over 400 sources) → **1,560
  accepted**, which is the plan's own target.
* Gate every block on the manifest before running: `% body_chars == 0`, `% < 5,000`, median
  `body_chars`, and journal Herfindahl. A block that fails is redrawn for free.

### 2.4 Power — what this detects, computed not assumed

Model the per-block contrast as `Δ̂_c = Δ + b_c + e_c`, `b_c ~ N(0, τ²)`, `Var(e_c) = σ_d²/n`.
Using the **measured** σ_d = 0.152 (Leg B, ×11.5 — the rung a replication would run at; 0.119 at
×0 is the optimistic variant) and the project's bar **X_B = 0.010**.

**(i) Detecting a corpus effect** (χ²_{C−1} test on the between-block variance, α = 0.05,
power 0.80):

| C | n/block | within-block SE | **τ detectable @80%** | ÷ bar |
|---:|---:|---:|---:|---:|
| 3 | 260 | 0.00943 | 0.0332 | 3.3 |
| 4 | 260 | 0.00943 | 0.0245 | 2.5 |
| **6** | **260** | 0.00943 | **0.0182** | **1.8** |
| 8 | 260 | 0.00943 | 0.0154 | 1.5 |
| 12 | 260 | 0.00943 | 0.0127 | 1.3 |
| 20 | 260 | 0.00943 | 0.0103 | 1.0 |
| 6 | 1000 | 0.00481 | 0.0093 | 0.9 |
| 12 | 1000 | 0.00481 | 0.0065 | 0.7 |

**The block count barely matters; the total query count does.** Substituting `σ_w = σ_d√C/√T`
with `T = nC` gives `τ_det = σ_d·√(f(C)/T)` where `f(C) = C·(R(C) − 1)`. Evaluated: f(4) = 27,
f(6) = 19.9, **f(8) = 21.3, f(12) = 21.8**, f(20) = 23.9 — **f is minimised around C = 8–12 at
≈ 21** and is flat there. So:

> **τ_det ≈ 0.152 · √(21 / T).**
>
> * **T = 1,560** (the plan's own set, blocked): **τ_det = 0.018** — 1.8 bars.
> * **T = 4,900**: **τ_det = 0.010** — one bar.
> * T = 3,000: τ_det = 0.0127.

**Read that as the honest cost of a generalising claim: the already-planned query budget resolves
a corpus effect of about twice the decision bar, and resolving one *of* the decision bar costs
~4,900 queries.** Also note a null is weak here: with C = 10 blocks and τ̂ = 0, the upper 95%
bound on τ is still ≈ 0.016. Pre-register that bound as the claim, not "no corpus effect".

**(ii) Precision of the pooled, generalising contrast**, `SE = √(τ²/C + σ_d²/(nC))`, CI on
t_{C−1} df:

The last column is the half-width you would report if you pooled all `T = n·C` queries and
ignored the corpus entirely: `1.96·σ_d/√T`.

| τ | C | n | T | SE | 95% half-width (t_{C−1}) | half-width **if τ is ignored** | ratio |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.005 | 6 | 260 | 1,560 | 0.00436 | 0.0112 | 0.0075 | 1.49× |
| **0.010** | **6** | **260** | **1,560** | **0.00561** | **0.0144** | **0.0075** | **1.92×** |
| 0.010 | 12 | 260 | 3,120 | 0.00397 | 0.0087 | 0.0053 | 1.64× |
| 0.020 | 6 | 260 | 1,560 | 0.00903 | 0.0232 | 0.0075 | 3.09× |
| 0.020 | 12 | 260 | 3,120 | 0.00638 | 0.0141 | 0.0053 | 2.66× |
| 0.010 | **2** | 260 | 520 | 0.00972 | **0.1235** | 0.0131 | **9.4×** |

Two things to take from this table:

1. **At C = 6 the CI is 1.5× wider at τ = 0.005 and 1.9× wider at τ = 0.010** than the
   τ-ignoring width — part from the extra variance, part from t₅ = 2.571 instead of 1.96.
   **That widening is the honest price of a claim about "PMC OA" rather than about "those 400
   documents", and it should be paid in the report rather than discovered by a reader.**
2. **C = 2 is useless** — t₁ = 12.7 makes the interval an order of magnitude wider than the
   effect. This is a quantitative argument against "we have two legs, that is our replication".

**(iii) Do not forget σ_d itself moves with the rung.** 0.119 at ×0 → 0.152 at ×11.5. It will
rise further at deeper rungs as the miss rate rises (§3). Treat 0.152 as a **floor** for any
design at or above ×11.5, and re-measure per rung rather than carrying it forward.

### 2.5 Ladder vs judged-corpus variation — which we need

They are not substitutes. Stated as estimands:

| design | what moves | what is held | estimand | answers |
|---|---|---|---|---|
| **Distractor ladder** (present design) | number of distractors | judged set, queries, needles | degradation of a contrast under competition | **Q3 — scale** |
| **Judged-corpus variation** (proposed) | the judged documents *and* their queries | pipeline, rung size, distractor policy | **τ** — is the contrast a property of the corpus or of those 400 documents | **Q2 — generalisability** |
| **Distractor composition** (proposed, cheap) | *what kind* of distractor at fixed rung size | judged set, queries, rung size | is the contrast sensitive to competitor hardness | a confound in both |

**Q2 needs the judged corpus to vary.** The ladder holds the needles fixed by design, so it can
never detect that the answer depends on which needles you picked — which is precisely the open
question, because Leg A and Leg B picked different needles *and different queries* and got
opposite signs at 512→1024.

The third arm is worth having because it is nearly free: same judged set, same rung size, only
the distractor pool changes, so it costs one extra index build per config per arm. Given the
measured 6× between-journal length spread and the OFID hazard, propose **four** composition arms
on **one** judged block:

| arm | distractor pool | measured character | tests |
|---|---|---|---|
| `D-random` | seeded random PMC OA (Leg B's own) | corpus-typical, median 35.3k chars | the baseline |
| `D-modal` | Scientific Reports + PLoS ONE only (545k available) | median 34.2–48.6k, 0% degenerate | in-domain, homogeneous |
| `D-short` | Emerging Infectious Diseases (13,627) | median 10.5k, 38% zero-sections | competitor documents shorter than the budget |
| `D-degenerate` | Open Forum Infectious Diseases (24,659) | **72.9% empty body** | the scifact failure, inside PMC |

`D-degenerate` is the interesting one: if the size contrast survives against distractors that
cannot be chunked at all, then the effect is about the *needle's* chunking; if it collapses, it
was partly about the *haystack's*. Nobody has separated those.

**Do not** run the judged-corpus blocks crossed with the composition arms — 6 × 4 = 24 index
builds per config is not worth it. Additive is enough: 6 blocks at `D-random` for τ, plus 3 extra
composition arms on block 1.

### 2.6 Should corpus subset be a random effect? — Yes, with three qualifications

**Yes.** Fit one model on the per-query contrast values, `Δ_qc = Δ + b_c + ε_qc`, `b_c ~ N(0, τ²)`,
by REML, with Satterthwaite df. One fit yields both deliverables: the generalising Δ̄ with a CI
that covers "PMC OA" rather than "this corpus", and τ̂ with its own CI. The alternative — a
separate "corpus experiment" — computes the same two quantities from the same data with less
efficiency and an artificial ordering between them.

Qualifications, all of which should be pre-registered:

1. **C ≥ 6.** Below that, τ̂ has < 5 df, the REML estimate is unstable and frequently pins at
   zero, and the t multiplier explodes (t₁ = 12.7). C = 8–12 is the efficient region (§2.4).
2. **Rung is a fixed effect, and so are the composition arms.** Their levels are chosen design
   points, not draws from a population. Only the random disjoint blocks are exchangeable.
3. **Report both, always.** A random-effects model with τ̂ ≈ 0 silently collapses to the
   fixed-effect answer, and a reader cannot tell whether τ was estimated as zero or never
   estimated. Publish τ̂ **and** its upper bound next to every generalising Δ̄.

### 2.7 Cost

Unit derived from the measured Leg B ×11.5 leg (666 M tokens, 5,000 docs, 12 configs, 64.8 min at
171k tok/s): **11.1 M tokens and 1.08 min of fleet per (config × 1,000 documents)**.

| design | index builds | tokens | fleet |
|---|---|---:|---:|
| 6 blocks × 5,000 docs × 6 configs | 36 | 2.00 B | **3.2 h** |
| + rerank leg (36 config-rungs at Leg B's measured 1.37 min each) | — | — | 0.8 h |
| + 3 composition arms on block 1 × 6 configs | 18 | 1.00 B | 1.6 h |
| **total** | | **3.0 B** | **≈ 5.6 GPU-h** |

Against Leg B's own 1.28 GPU-h for a 24-config × 2-rung run. The LLM query-generation chain for
2,400 source documents is the same chain the plan already budgets for 1,500 queries — it is
being **re-partitioned, not enlarged**.

---

## 3. Q3 — scale bracketing

### 3.1 First, a framing correction: 10M is not reachable on this host

`/rag/oa/corpus/xml/` holds **1,439,753** files (manifest scan, exact). The chunking plan's own
OA target is ~498k articles. So:

* the largest honest bracket available is **×288 over the 5,000-doc rung**, or **×3,600 over the
  400-document judged set**;
* **10M articles is ~7× more than the corpus contains** and would require sources outside this
  PMC OA snapshot — which is itself a change of corpus composition, i.e. Q2 again, at a magnitude
  the subset design cannot bound.

State the target as "the ~500k OA load, bracketed to 1.44M" or acquire the corpus first. A power
calculation for 10M on a 1.44M corpus is not a design, it is an extrapolation.

### 3.2 What the current results say about the top end: a power law, offered as a **prediction**

Leg B gives two rungs — 400 and 5,000 documents, a 12.5× step — on the same 260 queries and the
same 12 `token_window` cells. Miss rate `m = 1 − nDCG@10`:

| cell | m (×0) | m (×11.5) | ratio | implied exponent p |
|---|---:|---:|---:|---:|
| 256/0% | 0.0179 | 0.0405 | 2.263 | 0.323 |
| 512/12.5% | 0.0415 | 0.0786 | 1.894 | 0.253 |
| 1024/0% | 0.0518 | 0.0906 | 1.749 | 0.221 |
| 2048/12.5% | 0.0693 | 0.1325 | 1.912 | 0.257 |
| *(all 12 cells)* | | | 1.749–2.580 | **median 0.255**, mean 0.271, range 0.221–0.375 |

Model `m(N) = m(5000)·(N/5000)^p`, p = 0.255:

| N | ×over 5k | factor | 256/0% | 512/12.5% | 2048/12.5% |
|---:|---:|---:|---:|---:|---:|
| 5,000 | 1 | 1.00 | 0.960 | 0.921 | 0.868 |
| 40,000 | 8 | 1.70 | 0.931 | 0.867 | 0.775 |
| 500,000 | 100 | 3.23 | 0.869 | 0.746 | 0.572 |
| 1,439,753 | 288 | 4.23 | 0.829 | 0.667 | 0.439 |
| 10,000,000 | 2,000 | 6.93 | 0.719 | 0.455 | 0.081 |

**This is a two-point fit extrapolated three orders of magnitude. It is a hypothesis.** Its
sensitivity is severe: at p = 0.20 the 10M projection for 512/12.5% is 0.64; at p = 0.35 it is 0.
Three specific caveats:

1. **The base rung is not a pure dilution step.** ×0's 400 documents include the 140 sampled
   sources whose query was rejected — topically matched deep science, i.e. *harder* distractors
   than the random seeds added at ×11.5. So the 400→5,000 step mixes "more competitors" with
   "easier competitors" and p is biased in an undetermined direction.
2. **Exchangeability fails upward.** At 1.44M the corpus contains near-duplicates, same-topic
   clusters, and multiple versions. Real competitors are more correlated with the needle than iid
   draws, so the true miss rate should rise **faster** than the iid power law. p = 0.255 is
   plausibly a **lower bound**.
3. σ_d rises with the miss rate, so the deep-rung floors widen too — δ80 at n = 260 goes from
   0.0264 at σ_d = 0.152 to 0.0347 at σ_d = 0.20.

### 3.3 The finding this makes urgent: the two headline nulls were measured where the contrasts are smallest

Both nulls the study leans on were established at N ≤ 5,000, and both quantities **grow with N**.
Matched-base measurements, `token_window` cells only, both rungs:

| quantity | ×0 (400) | ×11.5 (5,000) | exponent |
|---|---:|---:|---:|
| dense spread (max − min nDCG@10) | 0.0531 | 0.0920 | 0.218 |
| **reranked** spread | **0.0186** | **0.0276** | **0.156** |

*(Both rows are the 12 `token_window` cells — same cells at both rungs, so the exponents are
matched-base. They therefore include the overlap variants; the 0%-only sub-spread is smaller and
is used where the design has only 0% cells — see §3.5.)*

Projected:

| N | dense spread | **reranked spread** | vs bar 0.010 |
|---:|---:|---:|---:|
| 5,000 | 0.092 | 0.028 | 2.8× |
| 40,000 | 0.145 | **0.038** | 3.8× |
| 500,000 | 0.251 | **0.057** | 5.7× |
| 1,439,753 | 0.316 | **0.067** | 6.7× |

> **"Behind the reranker, no size contrast resolves on either leg" is a statement about a
> 5,000-document corpus.** The reranked spread is already 2.8× the bar there and is projected at
> 5.7× the bar at the ~500k OA target. The reranker collapses the grid *at the scale it was
> measured*; there is no evidence it still does at the production scale, and the extrapolation
> says it does not. Leg B's §12 correctly refuses to prune on the size axis; this is the
> quantitative reason that refusal should hold through stage 2 as well.

The same argument applies to overlap: its null is powered at N ≤ 5,000 and the overlap effect,
too, may not be scale-invariant. But overlap's deep-rung recall@100 delta is **exactly 0.0000 at
all four sizes**, which is a much harder null than the size result, so the size axis is the one
to worry about.

### 3.4 The small end: 1–100 documents — the questions are different, not smaller

**Current results say nothing about 1–100 documents, and cannot.** With 100 documents and top_k =
10 the retriever returns 10% of the corpus; with one relevant document, recall@10 is near 1 for
every config. The repo has already hit this and named it: the G1 pilot's *"0.95 here vs 0.74 there
is 'small corpora are easy', not a comparison."* **Document-level retrieval metrics are degenerate
at this scale and no chunking config can be distinguished by them.** Reporting a size decision
from them would be reporting the corpus size.

What actually matters at 1–100 documents:

1. **Passage selection, not document selection.** The question is *which passage of this document
   answers the query*. The unit of evaluation must be the chunk. This is the position-of-evidence
   oracle that `long-doc-judged-set.md` §7 specified and §13.5 records as **never run**.
2. **Whole-document-in-window is a live baseline.** At 1–10 documents the right answer may be "do
   not retrieve, stuff everything" — the LLM window is 60,000 tokens and the corpus median
   document is ~10,000. Phase-0 step 3's `whole4096` arm is the right control and it is not in the
   grid.
3. **Answer-span straddling — where overlap should finally be tested.** The overlap null is a null
   about *document retrieval quality*; the Leg B report says so itself in §12 (*"Anything overlap
   does for answer-generation context continuity is untested here"*). The one thing overlap
   plausibly buys — an answer not cut in half at a chunk boundary — is invisible when the metric
   is "did the right document come back" and matters most when the document is already in hand.
   **This is the scale at which the overlap decision should be revisited, with a
   span-straddle-rate metric, not a retrieval metric.**
4. **Cost, not quality.** With quality saturated, the decision variable is chunks/document and
   build time — which the grid already reports.

### 3.5 The cheapest experiment that brackets both ends

**Small end — ≈10 GPU-minutes, and the ground truth already exists.** Leg B's construction
records, per query, *the deep section the query was written from*. That is a free passage-level
qrel that has never been used. Re-run the ×0 rung (400 documents, 24 configs = 9.6 config-kdocs =
**0.11 B tokens, ≈10 min of fleet**) and score at **chunk** granularity:

* primary: is a chunk overlapping the gold section in the top-k **chunks** (k = 1, 5, 10)?
* mini-corpora: for each query, gold document + 0 / 9 / 99 topically matched neighbours, drawn
  from the existing 400 — no new embedding at all, just re-scoring subsets of the same matrix.
* arms: the grid, plus `whole4096`, plus the overlap pair with a **span-straddle rate** (fraction
  of gold sections cut by a chunk boundary) reported beside it.

Chunks and embeddings were deliberately not persisted, so this is a re-embed rather than a
re-score — but at the ×0 rung that is 10 minutes. **This is the highest value-per-GPU-minute
experiment available anywhere in this study.**

**Top end — ≈2.9 GPU-h, as a falsification.** Do **not** buy the full-corpus bracket first. Run
one **×100 rung: 40,000 documents, 4 configs** (post-overlap-drop: 256/0, 512/0, 1024/0, 2048/0),
on the existing judged set and queries. 160 config-kdocs = **1.78 B tokens ≈ 2.9 h**.

Pre-register the prediction from §3.2 before running it:

| config | ×11.5 measured | predicted nDCG@10 at N = 40,000 (factor 8^0.255 = 1.699 on the miss rate) |
|---|---:|---:|
| `fixed_tok256_ov0pct` | 0.9595 | **0.931** |
| `fixed_tok512_ov0pct` | 0.9291 | **0.880** |
| `fixed_tok1024_ov0pct` | 0.9094 | **0.846** |
| `fixed_tok2048_ov0pct` | 0.8863 | **0.807** |
| dense spread (these 4 cells) | 0.0732 | **0.124** |

and the decision rule: **if the observed values fall inside ±0.02 of these, the law has survived
one more decade and the 500k / 1.44M projections are worth acting on; if not, the law is wrong and
we learned it for 2.9 GPU-h instead of 26.** The predicted dense spread (0.124) is ~4.8× the
δ80 of 0.026 at n = 260, so this is a well-powered test of the dense law.

**What this rung does *not* settle.** Among the four **0%** cells the ×11.5 **reranked** spread is
only **0.0080** (0.9390 / 0.9364 / 0.9310 / 0.9341) — the 0.0276 figure in §3.3 is the 12-cell
spread and is driven mostly by the overlap variants. Scaled by 8^0.156 the ×100 prediction is
**0.011**, sitting exactly on the 0.010 bar, so **the ×100 rung cannot resolve whether the
reranker's collapse survives scale.** That question resolves only at the full-corpus bracket:
0.0080 × 288^0.156 = **0.019**, ≈2 bars. Say so in the pre-registration rather than reading a null
there as confirmation.

**Top end, if the law survives — ≈26 GPU-h.** Index the **full 1,439,753-document corpus with one
config** and measure the judged set's rank against it: 1,440 config-kdocs = 16.0 B tokens ≈ **25.9
h**. Two configs (the extremes, 256/0 and 2048/0) = **51.8 h**, and that pair is the decisive
scale experiment — it is ~2 days of fleet time, not the 30 days a full grid at that rung would
cost. It is also, not coincidentally, the same work as the production OA load, so it can be
scheduled as one.

**A cheaper estimator for the very top, offered with its assumption named.** If even 26 h is too
much: the gold document's rank in a corpus of N distractors is `1 + Binomial(N, p(s))` where
`p(s) = P(a distractor's max-chunk score > the gold score)`. Estimate `p(s)` from a distractor
sample of size m and extrapolate the upper tail (a generalised-Pareto fit on the top 0.1%). A
200,000-document sample per config costs 200 config-kdocs ≈ 3.6 h and resolves `p` to ~5×10⁻⁶,
enough to project to 10⁷. **Its assumption is exactly the exchangeability that §3.2 caveat 2 says
fails**, so it yields a *lower bound* on rank inflation — and the size of the violation is what
§2.5's `D-modal` / `D-degenerate` composition arms measure. That is the one place where the Q2
design directly buys down a Q3 uncertainty.

---

## 4. Corrections to the framing

1. **"semantic 255/359/357/343"** — those are **Leg A's** `cstats`. Leg B is **255/324/350/351**
   (fills 1.00/0.63/0.34/0.17). Minor in itself, but the per-corpus difference is real, is
   confined to `semantic`, and is a preview of τ > 0.
2. **"`semantic` — no size variation"** is right about the outcome and misleading about the cause.
   Semantic **has** a size knob (`breakpoint_percentile_threshold`); the grid moved a cap instead.
   Call it "the grid never varied semantic's size", not "semantic has one size".
3. **"fill 1.00→0.17"** for `semantic` is not a fill. The denominator is a cap, not a target.
   Report `cap_bind_rate`.
4. **"`words`' constant 0.64 fill — bug or property?"** — **bug**, with the mechanism confirmed at
   1.497× **[measured here]** and located at `_pack_spans_tokens`
   (`python/ragstack/ingestion/chunkers.py`, l. 620). And a real documentation defect beside it:
   `_pack_spans`' docstring (l. 569) claims a joined-count verification that `_pack_spans_tokens`
   deliberately does not perform.
5. **"Overlap is a powered null on both legs"** — true, and true only of **document-retrieval
   quality at N ≤ 5,000**. Two live exceptions: answer-span continuity, untested (§3.4.3, and the
   Leg B report's own §12), and scale, since the effect being called null grows with N (§3.3).
6. **"Legs agree finer-is-better at 256→512 and 1024→2048"** — the point estimates agree; **Leg A
   resolves neither step** (δ80 = 0.059–0.099 against effects of 0.006–0.024). Leg A's size
   optimum at 1024 is bracketed **descriptively, not resolvably**. The concordance is weaker than
   the phrasing implies, which the Leg B report states and which should travel with the summary.
7. **"Behind the reranker no size contrast resolves on either leg"** — measured at N ≤ 5,000,
   where the reranked spread is 0.0276. Projected 0.057 at 500k. This is a small-corpus finding
   being carried as a general one (§3.3).
8. **"the end goal is meaningful retrieval at 10 million articles"** — not reachable from
   `/rag/oa/corpus/xml/` (1,439,753 files) and ~20× the plan's own ~498k OA target. Either the
   target or the corpus needs to change before a design can be costed for it.
9. **"Chunking is deterministic … we verified 0.0e+00"** — correct, and its consequence is worth
   making explicit: measurement error is *zero*, so **all** remaining uncertainty is sampling, and
   τ is the only sampling component the study has never measured (§2.1).
10. **σ_d** — 0.152 is Leg B at **×11.5**; ×0 is 0.119. Leg A's 0.156/0.173 is estimated from
    **n = 10 topics** (≈24% relative SE) on a different metric definition and is not
    interchangeable with Leg B's. Use 0.152 as a **floor** and re-measure per rung.

---

## 5. Recommended order of work

| # | what | cost | buys |
|---|---|---:|---|
| 1 | Chunk-granularity re-score of the ×0 rung against Leg B's recorded gold sections; add `whole4096` and a span-straddle metric | **0.2 h** | the entire 1–100-document regime, and the first real test of overlap's actual purpose |
| 2 | Fix the `_pack_spans` docstring; add `cap_bind_rate` to cstats; add a seam-reclaimed `words` arm and a semantic *p*-sweep (one shared breakpoint pass) | ~0 GPU | turns the realised-token model from fitted into tested |
| 3 | ×100 rung, 40,000 docs, 4 configs, against the pre-registered predictions in §3.5 | **2.9 h** | falsifies or confirms the scale law for 1/9th the cost of assuming it |
| 4 | Blocked Leg B: 6–12 disjoint blocks × 5,000-doc rungs, + 3 distractor-composition arms | **5.6 h** | τ, a generalising CI, and the OFID/EID hazard map — cost-neutral against the already-planned 1,500-query set |
| 5 | Full-corpus bracket, 2 configs, **only if step 3's law survives** | **51.8 h** | the top of the ladder that actually exists (1.44M), doubling as the production OA load |

Steps 1–4 total **≈ 8.7 GPU-h** — under seven times Leg B's own 1.28 h — and between them close
the small end, test the scale law, and measure the study's last unmeasured variance component.

---

## 6. Provenance of the measurements taken here

| what | how | file |
|---|---|---|
| journal mix, `body_chars` distribution, per-journal degenerate-body rates | full scan of `/rag/oa/corpus/manifest.jsonl`, 1,439,753 records, 0 parse failures, read-only | `design/scan_manifest.py`, `design/manifest_scan.json` |
| article-type distribution | first 4 KB of 2,755 XML files across 120 leaf shards, seeded sample, read-only | inline |
| per-word / per-sentence isolated-tokenization over-count | cached `Salesforce/SFR-Embedding-Mistral` tokenizer, offline, CPU only, 12 PMC bodies | `design/tokcheck.py` |
| power tables, exponents, cost lines | arithmetic on the above and on the Phase-0 RESULTS files | `design/power2.py` |

No GPU was used, no embedding or reranker endpoint was contacted, no Qdrant or Elasticsearch
client was constructed, and nothing under `/rag/` was written. `/rag/oa/` was read only.
