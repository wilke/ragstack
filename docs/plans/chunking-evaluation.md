# Redoing the chunking evaluation

**Status:** `PROPOSED`. The existing comparison cannot answer the questions we are about to
spend a corpus on, and the reason is the ground truth rather than the configurations.

**Update 2026-09-04 — the ground truth moved.** A Phase-0 pilot measured the BeIR datasets
below against a real long-document alternative, and both halves of that measurement change
this plan: **no planned BeIR dataset can exercise the top of the size ladder** (below), and
**TREC CDS 2014–16 — human-judged, PMC OA JATS, 90 topics — demonstrably can separate
chunking configs**, which an intermediate reading had denied. The set, the evidence and the
reversal live in [long-doc-judged-set.md](long-doc-judged-set.md) § 13; this document keeps
the grid, the cost model and the decision table, corrected where Phase 0 contradicted them.

**Update 2026-09-04 — stage 1 has run.** All 24 configs, on the CDS pilot rather than on
scifact, against a pre-registration written before any embedding call: **968 M tokens
embedded in 94 minutes, 0 retries in 186,647 requests.** [§ Stage 1, run](#stage-1-run-what-24-configs-on-a-long-document-corpus-actually-said)
records what it found. Three things change this plan materially:

| what | reading |
|---|---|
| **Overlap buys nothing at any size** | negative on nDCG at all four rungs, \|Δ recall@100\| ≤ 0.0033, at up to **1.32×** the vectors. Adequately powered. The largest actionable result Phase 0 produced |
| **The staged plan's premise did not survive its own stage 1** | "if overlap's effect doesn't depend on size the grid collapses from 12 configs to 4" — the interaction contrast turned out to be **structurally unanswerable at its own bar**, and a second leg then contradicted Leg A on the size axis. The plan of record is now to *keep* the size axis, at ~6× the stage-2 budget |
| **Semantic costs ~7× a `token_window` config, not 2×** | it embeds a rolling sentence buffer per sentence. It is also the worst-scoring kind on this leg and builds a 3.4× larger index |

**Update 2026-09-05 — three more runs, and the first one moves the ground under all of the
above.** The Leg B grid, a small-corpus chunk-granularity re-score and a breadth × k run:

| what | reading |
|---|---|
| **Every metric in this plan is a *document* metric** | and at a one-document corpus, where the document metric is 1.0000 by arithmetic, the top chunk hits the gold section only **55–65%** of the time. `Gap@1` **+0.28 to +0.45**, resolved 15/15, ~0 by k=10. This study has been measuring *"found the right paper"* — see § Metrics |
| **Size must be read budget-matched, and the answer flips** | fixed-`k` favours `tok2048`; at a matched 4,096-token budget **`tok256` wins by 2.2–3.8×**, on two corpora, two query styles and four breadth rungs. The first reading on which the two legs agree — see the end of § Pre-registration |
| **`words`/`sentence` rows are frozen at a legacy fill** | `55a0fc2` (#488) changed the default, so every such row here is a `budget_mode="summed"` measurement and is not comparable to a future run — see § Nominal size is not realised size. `semantic`'s size knob was never the `size` parameter either |

The run reports are in [`results/`](results/), copied verbatim and indexed in
[`results/README.md`](results/README.md) — which also lists, in one place, the conclusions
this record later revised.

---

## Why the current numbers cannot carry the decision

**1. The ground truth is known-item-by-title.** Query = the document's own title; the
relevant document is that document. `chunking_compare.py` flags its own bias:

> *"Title-query proxy. Known-item-by-title flatters lexical/BM25 matching (the title's words
> often appear verbatim in the lead chunk)."*
> *"Single relevant doc. nDCG/recall here assume exactly one relevant document per query."*

This systematically rewards whatever keeps the **lead chunk** intact, and under-measures
exactly what boundary-aware chunking is supposed to buy. It is a lexical task in retrieval's
clothing.

**2. The baseline may not be the shipping chunker.** In `chunking_compare_full_report.md`,
`fixed` has median **512 chars**; production is `fixed_token` 512 **tokens** (~1,790 chars,
measured). Those are different chunkers with the same label.

**3. "Semantic" was a method plus an undeclared truncation policy.** Unbounded chunks,
**9,365 capped**, and a prior benchmark recording **12% would overflow the 4096-token
window**. A config whose oversize handling is not pinned measures configuration, not method.

**4. The token counter could silently resize every config.** `make_token_counter` defaults to
`chars_per_token = 2.5`, and `chunker_config` used to **fall back to `estimate` when a model
was unavailable** — it logged, it did not refuse. Production measures **3.50 chars/token**, so
a "512-token" config could really be 366 tokens: **29% under-filled, ~1.4× the chunks**.
*Fixed* — see the prerequisite below.

---

## Prerequisite, before any run

**Pin the token counter — DONE.** For an evaluation or a corpus build, the estimator must be
an explicit opt-in and a missing tokenizer must **fail**, not fall back. Same rule as #454:
make the value required rather than defaulted. Re-running experiments on top of a silent
40% resize reproduces the confusion at higher resolution.

As of this commit both silent paths refuse. `make_token_counter("hf", …)` raises when the
tokenizer will not load instead of demoting to the endpoint counter and then to the
estimator, and `resolve_token_backend` raises for an hf/endpoint backend with no model
instead of warning and returning `"estimate"`. Nothing new was added to the surface: the
opt-in is the flag that already existed — `--chunk-token-counter estimate|endpoint` on the
ingest CLIs, `chunk_token_counter` (`CHUNK_TOKEN_COUNTER`) in settings — and the refusal
messages name it. `EstimatingTokenCounter` keeps `chars_per_token = 2.5`: it over-counts and
under-fills, which is recoverable, where the measured 3.50 would risk over-window chunks;
the measurement is recorded in its docstring so the trade is legible.

Consequence for a run: an eval or ingest that *cannot* count tokens now stops instead of
producing a differently-chunked index. On the API the refusal is a **boot** refusal (the
chunker is built in `lifespan`), and only for deployments that turn token sizing on.

---

## Design

### Vocabulary — see [GLOSSARY.md](../GLOSSARY.md#chunking-config-and-index-build)

A **chunking config** is `(kind, size, overlap, token counter)` plus its cap policy — the
tuple that decides where cuts fall. An **index build** is one config materialised over one
corpus. They are not 1:1: `index builds = configs × corpora`, so four configs across three
ladder rungs is **twelve builds**.

Retrieval mode, reranking on/off and `top_k` are **query-time** — they re-query an existing
index and never force a rebuild. That is why they can be varied freely and configs cannot.

The word *config* is not used below; it was used for both meanings once, and a twelve-build
stage got costed as four.

Minimum set of configs:

| config | why it is in |
|---|---|
| `fixed_tok512/64` | the shipping default — the thing to beat |
| `fixed_tok1024/0` | the memory lever, isolated: bigger chunks, no overlap |
| `sentence_tok512` | boundaries respected, same budget |
| `semantic_tok512_capped` | semantic with its truncation policy **declared** |
| **`structure_tok512`** | the proposed structure-aware packer (below) |

### Ground truth: real qrels, several domains

Use **BeIR** datasets rather than building corpora: real relevance judgments, multiple
relevant documents per query, no rating effort.

| dataset | domain | gives us |
|---|---|---|
| `scifact` | biomedical claims | the existing baseline, comparable to G1 |
| `nfcorpus` | nutrition/medical | different query style, many relevant per query |
| `trec-covid` | biomedical, **abstracts as cached** | ~~closest to the PMC target~~ — **corrected 2026-09-04**: the BeIR copy in `HF_HOME` is abstracts-only, median **378** SFR tokens and **max 925**. Full text needs the CORD-19 release itself, which is not JATS |
| `scidocs` | broader science | the "different scientific area" config |

That covers *"different scientific area"* and *"mixed documents"* with judgments already in
hand.

### Collection size: a distractor ladder, **not** subsampling

> Terms — **judged set**, **distractor**, **rung**, **ladder** — are defined in
> [GLOSSARY.md](../GLOSSARY.md#judged-set-distractor-rung-ladder). A **rung is a corpus**,
> which is why `index builds = configs × rungs`.

**The repo has already hit this trap.** The G1 pilot ran 50/100/200-document rungs and the
verdict was:

> *"not answerable from this run… the pilot's queries are the 24/46/90 whose judged documents
> landed in the sample… 0.95 here vs 0.74 there is 'small corpora are easy', not a
> comparison."*

Subsampling a corpus destroys the judgments: most queries lose their relevant documents, and
scores rise because the haystack shrank. The design that works — and which that pilot names —
is a **distractor ladder**:

- hold the **judged set fixed** — every document any query is judged against,
- vary the number of **unjudged distractor** documents around it,
- same queries and same needles at every step.

**A *rung* is one step on that ladder — one corpus size.** `×10` means ten distractor
documents for every judged one. The judged set never changes, so a score moving across rungs
is the chunking degrading under competition, not the task getting easier or harder.

### Measured judged coverage (all four datasets now cached in `HF_HOME`)

| dataset | docs | queries | qrels | judged docs | tokens |
|---|---|---|---|---|---|
| `scifact` | 5,183 | 1,109 | 339 | 283 (**5%**) | 2.2M |
| `nfcorpus` | 3,633 | 3,237 | 12,334 | 3,128 (86%) | 1.7M |
| `scidocs` | 25,657 | 1,000 | 29,928 | 25,657 (**100%**) | 8.8M |
| `trec-covid` | 171,332 | **50** | 66,336 | 35,480 (21%) | 76.5M |

Three consequences for the design:

- **`scidocs` cannot supply its own distractors** — every document is somebody's answer.
  Padding must come from outside, and our PMC corpus is the natural source: an in-domain
  distractor is a harder and more honest one than out-of-domain filler.
- **`trec-covid` has 50 queries.** Deeply judged (1,327 qrels per query) but thin for
  confidence intervals. Use it for realism — not as the decisive comparison. **And not for
  document length**: measured 2026-09-04, its longest document is 925 tokens (below).
- **`scifact` is 5% judged and `nfcorpus` 86%.** scifact is already 95% distractor at ×1, so
  the rung labels are not comparable across datasets. Normalise on distractors-per-judged-doc,
  not on the multiplier.

That measures the thing actually in question: *does this chunking degrade as the corpus
grows?* — which matters because the target is ~500k articles and every number we have comes
from a 3,817-document run.

### What "same journal" would cost

No qrels exist for a single-journal or mixed in-house corpus. `docs/g1-sop-rating.md` is a
complete human-rating SOP — pooling, adjudication, κ bands — and it is **weeks of rater
time**. Reserve it for a decision that a BeIR ladder genuinely cannot answer; do not spend
it on chunk-size tuning.

---

## Overlap belongs in the grid — as a fraction, not a token count

`chunk_overlap` is an **absolute token count** today, so holding it at 64 across a size
ladder does not hold overlap constant:

| size | 64 tokens is | chunk inflation |
|---|---|---|
| 256 | **25.0%** | 1.33× |
| 512 | 12.5% | 1.14× |
| 1024 | 6.2% | 1.07× |
| 2048 | **3.1%** | 1.03× |

A size sweep at fixed `overlap=64` would confound the size effect with a **fading overlap
effect** and could not separate them. Parameterise overlap as a **fraction** — 0% / 12.5% /
25% — so it means the same thing at every rung.

Inflation is `1/(1−f)` and multiplies vectors one for one. For the ~498k-article OA target
at fp32 4096-dim:

| size | 0% | 12.5% | 25% |
|---|---|---|---|
| 512 | 16.4M · 0.24 TB | 18.8M · 0.28 TB | 21.9M · 0.33 TB |
| 1024 | 8.2M · 0.12 TB | 9.4M · 0.14 TB | 11.0M · 0.16 TB |
| 2048 | 4.1M · 0.06 TB | 4.7M · 0.07 TB | 5.5M · 0.08 TB |

**Include 0% at every size.** It is the cheapest configuration, so the burden of proof sits
on overlap.

**Measured 2026-09-04 — overlap does not discharge that burden.** On a corpus long enough for
overlap to actually engage (the CDS pilot; measured inflation at size 256 is 1.000 / 1.135 /
1.317 against a theoretical 1.000 / 1.143 / 1.333, so it engages essentially fully), what the
extra vectors buy is:

| size | nDCG@10, 25% − 0% | recall@100, 25% − 0% |
|---:|---:|---:|
| 256 | **−0.0446** | +0.0033 |
| 512 | **−0.0197** | −0.0030 |
| 1024 | **−0.0412** | −0.0013 |
| 2048 | **−0.0037** | +0.0016 |

**Negative at every rung on nDCG@10, and |Δ| ≤ 0.0033 on recall@100 at every rung.** The
12.5% main effect is **−0.0210, CI [−0.047, +0.007]**, and it is one of only two contrasts in
the pre-registered family whose 80%-power floor (0.046) sits below the 0.05 bar written for
it — so this is a measurement, not an absence of one. The interval is consistent with overlap
*hurting* by up to ~0.047; it is not consistent with it helping much.

**0% overlap is not worse, and it is a `1/(1−f)` saving on every index for the product's
lifetime.** That is the largest actionable number Phase 0 produced. Two conditions on acting
on it: it is **one leg**, and overlap has **not been tested on Leg B** — the Leg B σ_d run
included a `tok512/25%` cell but published no overlap contrast from it. Drop the overlap axis
when Leg B confirms the null, not before.

### What the model limits allow

Queried live, not recalled:

| component | model | limit |
|---|---|---|
| embedding | `Salesforce/SFR-Embedding-Mistral` | **`max_model_len: 4096`** |
| LLM | `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic` | **`max_model_len: 60000`** |
| reranker | `BAAI/bge-reranker-v2-m3` | **not reported by the sidecar** |

256 / 512 / 1024 / 2048 all fit the embedding window with room; even 2048 × top_k 20 uses
two-thirds of the LLM window. **Chunk size here is a retrieval-quality and storage decision,
not a context-window one.**

**Measure the reranker's truncation point before the run.** If `bge-reranker-v2-m3`
truncates at 2048-token chunks, the 2048 config's rerank score measures truncation rather than
quality — and rerank score is the metric this study leans on.

---

## Cost: factorial vs staged, on the existing 4-endpoint fleet

**Rate.** ~**164k tokens/s** aggregate.

> **Corrected 2026-09-04.** This line read ~**80k tokens/s**, from the live-validation
> record: 5,915 `fixed_token 512` chunks embedded in 38.4 s of compute (~3.0M tokens), on
> 3 of 4 workers, flagged **±2×** as one measurement rather than a benchmark. The Phase-0
> step-3 run is a second measurement on the current six-endpoint fleet (:9001–:9006):
> **~108M tokens in ~11 minutes with 0 retries ≈ 164k tokens/s**. Every derived figure
> below is recomputed at the new rate.
>
> **Keep the ±2× band.** Two measurements are not a benchmark either, they are on
> different fleets, and they are themselves ~2× apart — which is the band doing exactly
> what it was written for. Read the hours as an order of magnitude.

**What actually costs.** Embedding tracks **total tokens**, which is roughly invariant to
chunk *size* over the same corpus — but overlap adds tokens directly (`1/(1−f)`), and the
**distractor ladder multiplies the whole corpus and is re-embedded per config**. Chunking
itself is ~2,200 chunks/s on CPU and is noise.

Corpora: `scifact` ~1.8M tokens (cached locally); `nfcorpus` ~1.5M, `scidocs` ~9M,
`trec-covid` ~60M (estimates — only scifact is in `HF_HOME` today). ~72M tokens for all four.

> **Corrected 2026-08-31.** An earlier version of this table multiplied the *whole corpus*
> by the rung. That is not the design: the ladder pads around the **judged set**, which is
> small. The real figures are ~2.5× lower, from the datasets now cached locally.

| rung | distractor docs | tokens | **per index build** (164k tok/s) | was (80k tok/s) |
|---|---|---|---|---|
| ×1 | 64,548 | 54M | 0.09 h | 0.19 h |
| ×10 | 645,480 | 306M | 0.52 h | 1.06 h |
| ×100 | 6,454,800 | 2.8B | **4.7 h** | 9.8 h |
| ×1000 | 64,548,000 | 28B | **47 h** | 97 h |

### Full factorial — 48 configs × 4 datasets × 4 rungs

**≈ 650 GPU-hours ≈ 27 days** of fleet wall-clock (was ≈1,300 h ≈ 54 days at 80k tok/s).

### Staged

| stage | what | cost | was |
|---|---|---|---|
| 1 | 24 configs — 12 `fixed`×sizes×overlaps, then 12 other kinds×sizes — ~~`scifact`, ×1~~ **run on the CDS pilot instead** | **1.57 h measured** (est. ~1.4 h) | ~2.8 h |
| 2 | ~~~4 surviving configs × 3 rungs = **12 builds**~~ — **see the plan of record below**: the size axis is not being pruned, so this is **24 configs × 3 rungs = 72 builds**, ~6× | ~~**~25 h**~~ **~150 h** at the same per-build rate | ~51 h |
| | **total** | ~~**~27 GPU-hours**~~ **~150 GPU-hours** unless a leg breaks the size tie | ~54 |

The 30× argument for staging is unchanged by the rate correction — it halves both sides.
What *does* change the staged plan is the corpus finding above: stage 1 on `scifact` prunes
on a corpus that cannot exercise half its own grid, whatever it costs. **Stage 1 was
therefore run on the CDS pilot** and came in at 1.57 h against its 3 h pre-registered ceiling
(§ Stage 1, run).

**And stage 2's line has moved for a reason that is not a cost correction.** The 12-build
figure assumed stage 1 would prune the grid to ~4 configs. It did not, and the reason is in
[long-doc-judged-set.md § 14.5](long-doc-judged-set.md): Legs A and B resolve the chunk-size
contrast with **opposite signs and non-overlapping intervals**, and each leg's direction is
predicted by how its own queries were constructed. **Pruning the size axis on either leg would
be recording a construction bias as a finding.** The plan of record is to keep it, produce
recommendations conditioned on query target, and cut only where the legs do not disagree —
overlap (pending Leg B) and semantic (on cost). The 6× is the price of that, and Holm across
more contrasts costs power on top of it: the same n resolves a larger δ. If the budget is
refused, the alternative is **not** to prune anyway — it is to run Leg C, whose bias profile
differs from both, and let a third leg break the tie.

### Stage 1's grid, as implemented

`chunking_compare_7way.STAGE1_CONFIGS` — generated, not hand-listed, by
`stage1_configs()`. Run it with `scifact_chunk_eval.py --configs stage1`.

Overlap is a **fraction** on the config and is resolved to an absolute token
count per size (`resolve_overlap_tokens(size, frac)`), which is what makes the
size ladder readable. Both numbers travel together — in the key, the label, the
report tables and the CSV (`overlap_frac`, `overlap_tokens`) — because either
one alone misleads: 64 tokens is 25% at 256 and 3.1% at 2048.

| | |
|---|---|
| 12 fixed | `fixed_tok{256,512,1024,2048}_ov{0,12_5,25}pct`, `kind="token_window"` |
| 12 other | `{sentence,words,semantic}_tok{256,512,1024,2048}_ov12_5pct` |

Three things this document left for the implementation to decide, recorded here
so the run is reproducible:

- **The other-kinds overlap.** The table above says "12 other kinds×sizes" without
  naming an overlap. It is **12.5%** (`STAGE1_OTHER_FRAC`) — the shipping
  default's own fraction, so the other-kind row is directly comparable to
  `fixed_tok512`. Every such label spells it out.
- **`fixed_tok512` is a cell of this grid, not a neighbour of one.** 64/512 is
  exactly 12.5%, so the shipping control falls out of the scheme identically and
  **keeps its key** — the key is a Qdrant collection and an ES index name, and
  renaming the one config that must stay comparable across stages would orphan
  every result already recorded under it. It is literally the same object in
  `CONFIGS` and in `STAGE1_CONFIGS`. `fixed_tok256`, `sentence_tok512` and
  `words_tok512` also turn out to be 12.5% cells; they keep uniform grid keys and
  the equality is asserted in tests rather than relied on.
- **Semantic has no overlap of its own.** It is adaptive, and `size` is its token
  *cap*. The fraction reaches only its oversized-doc fixed-token fallback window
  (threaded via `extra`, so the shipping `semantic_tokcap` / `semantic_pooled`
  are untouched). The labels say so. `structure_tok512` from the config table
  above does not exist yet — `words` stands in until the structure-aware packer
  below is built.

The grid needs a real tokenizer (#477 made the counter refuse rather than
silently resize). `Salesforce/SFR-Embedding-Mistral` loads offline from
`HF_HOME=/rag/cache` in the `/rag/envs/ragstack` environment — verified, and the
run must use that environment.

**What `OVERLAP_CHARS_PER_TOKEN = 2.5` costs.** The sentence/words packer takes
its overlap in *chars*, so the token intent is converted at 2.5 chars/token —
the constant the shipping `sentence_tok512` / `words_tok512` were written with
(64 tok → the committed 160 chars). It is kept for reproducibility, not because
it is right: production measures **3.50** chars/token, at which 160 chars is
≈45.7 tokens. **So the sentence/words rows labelled 12.5% carry ≈8.9% effective
overlap.** The `token_window` rows are exact — they are sized in tokens
throughout. Read a sentence-vs-fixed overlap comparison with that gap in mind.

### Nominal size is not realised size

`size` is a budget the packer fills *up to*, and the kinds do not fill it
equally. Measured on the real scifact corpus (5,183 docs, SFR tokenizer, median
chunk tokens as a percentage of the nominal budget):

| kind | 256 | 512 | 1024 | 2048 |
|---|---|---|---|---|
| `token_window` | 100% | 65% | 35% | 17% |
| `sentence` | 82% | 64% | 35% | 17% |
| `words` | **62%** | **56%** | 34% | 17% |

A kind-vs-kind row compared on nominal size alone is therefore comparing
different effective sizes. This is the same failure the overlap fraction was
introduced to fix — one number that reads like the quantity of interest but is
not it — so `scifact_chunk_eval` now records the realised median/p95/max tokens
and a `fill` column beside the nominal size, in both the report and the CSV.
Deliberately *measured*, not corrected: making `words` fill its budget would
change what `words` is.

> **The table above does not transfer to a long-document corpus. Measured
> 2026-09-04.** Re-run on the CDS pilot (median document **4,532** tokens, against
> scifact's 354):
>
> | kind | 256 | 512 | 1024 | 2048 |
> |---|---|---|---|---|
> | scifact `sentence` | 82% | 64% | 35% | 17% |
> | **CDS `sentence`** | **89%** | **94%** | **96%** | **97%** |
> | scifact `words` | 62% | 56% | 34% | 17% |
> | **CDS `words`** | **64%** | **64%** | **64%** | **64%** |
>
> The scifact decay to 17% is **entirely a document-length artefact** — a
> 354-token document cannot fill a 2048-token budget — and it vanishes here.
> `sentence` fill *rises* with size, because its waste is one partial sentence per
> chunk and that is a shrinking fraction of a growing budget; `words` is flat at
> 0.64 because its shortfall is proportional. **Fill is a property of corpus ×
> kind, not of kind.** Do not reuse the scifact numbers to interpret results on a
> long-document corpus — the direction of the trend reverses.
>
> Two further labels, needed before reading any `fill` column: **`semantic`'s fill
> is not a fill** — its `size` is a *cap* that mostly does not bind, so 0.17 at
> 2048 means the ceiling was irrelevant, not that chunks fell short of a target.
> And the `sentence`/`words` rows labelled 12.5% carry **≈8.9% effective overlap**,
> for the `OVERLAP_CHARS_PER_TOKEN` reason recorded above.

> **Two mechanisms behind those two labels, and one of them retires every `words`/`sentence`
> row above. Written 2026-09-05.**
>
> **`words`' flat 0.64 is a bug, and it is now fixed — so those rows are frozen.** Every
> Phase-0 run pins `d225cea`, where `_pack_spans_tokens` packed while the *sum of per-unit
> token counts* stayed within budget, each unit tokenized **in isolation**. A BPE tokenizer
> merges the space before a word into that word's token and a lone word cannot show that
> merge, so per-word sums over-count the joined chunk by a measured **1.497×** (range
> 1.433–1.629) — multiplicative, hence scale-free, hence 0.64 at every nominal size.
> `words_tok512` is not "words at 512 tokens", it is words at **328**. `55a0fc2` (#488) made
> filling to the *joined* budget the default and kept the old path as
> `budget_mode="summed"`, so **every `words` and `sentence` row in this document and in the
> stage-1 grids is a `summed` measurement and is not comparable to a run at `55a0fc2` or
> later.** The legacy mode was kept rather than deleted precisely so those grids stay
> reproducible.
>
> **`sentence` went through the same code path and is not distorted by it.** Its per-sentence
> over-count ratio is **1.000 on all 12 documents measured** — a sentence's isolated token
> count already equals its joined count. Its 0.89 → 0.97 rise is whole-unit granularity
> behaving correctly: a **property**, not the bug. Do not attribute the two kinds' fills to
> one cause.
>
> **`semantic`'s cap is not its size knob.** Its four size cells land at realised medians
> **255 / 359 / 357 / 343** tokens on Leg A and **255 / 324 / 350 / 351** on Leg B — it emits
> ~350-token blocks whatever the ceiling says, and the cap binds only at 256. The reason is
> that `breakpoint_percentile_threshold` is a percentile of *each document's own* distance
> distribution, so the fraction of gaps cut is fixed at `1 − p/100` by construction and the
> mean block is ~5 sentences regardless of `size` or document length. **That knob is the size
> axis for `semantic`, and this grid never touched it** — so "semantic has one natural size"
> is only true as *"semantic has one natural size at p = 80"*. Report `cap_bind_rate` rather
> than `fill` for this kind.
>
> **Counter-instruction, and it is deliberate: do not silently fix `words` before stage 2.**
> `words` at 0.64 and `semantic` pinned at ~350 are the only configurations where realised and
> nominal size come apart, and the study's central exploratory claim is that *realised* tokens
> explain quality. Repair them and that claim becomes an unfalsifiable restatement of the size
> axis. Turn each into a declared manipulation — `budget_mode` as a factor, `p` as a factor —
> instead.

### No planned BeIR corpus can power the top of the ladder

Measured, same run — scifact document lengths in SFR tokens:

| median | p95 | p99 | max | >256 | >512 | >1024 | >2048 |
|---|---|---|---|---|---|---|---|
| 354 | 662 | 914 | 2,164 | **79.8%** | **16.9%** | **0.62%** | **0.02%** |

The median document is 354 tokens — *shorter than a 512-token budget*. So:

- At **2048**, 5,182 of 5,183 documents are a single chunk. All three overlap
  fractions produce 5,184 chunks; they differ in one document out of 5,183.
- At **1024**, 99.4% are single chunks (5,216 / 5,217 / 5,217 chunks).
- Overlap therefore never engages at the top of the ladder. Measured inflation
  vs the 0% cell at the same size:

| size | 12.5% | 25% | plan predicted |
|---|---|---|---|
| 256 | 1.036× | 1.085× | 1.143× / 1.333× |
| 512 | 1.002× | 1.006× | 1.143× / 1.333× |
| 1024 | 1.000× | 1.000× | 1.143× / 1.333× |
| 2048 | 1.000× | 1.000× | 1.143× / 1.333× |

**The twelve cells at sizes 1024 and 2048 carry essentially no signal on this
corpus** — 6 `token_window` + 2 each of `sentence`/`words`/`semantic`. At those
sizes nearly every document is one chunk, so the cells collapse onto each other
*and* onto the smaller-size 0% cells: neither the size contrast nor the overlap
contrast has anything to bite on. Even at 512 the overlap effect is ~0.2–0.6% of
the index rather than the predicted 14–33%. The cost model in *"Overlap belongs in the grid"* assumes
documents long enough to be cut repeatedly; scifact abstracts are not, so its
`1/(1−f)` inflation is an upper bound that this corpus never approaches.

This is a property of **the grid on this corpus**, not a defect in the grid — the
generation is correct. It is a **study-design question**: stage 1 as specified
prunes on a corpus that cannot exercise its top half.

> **Corrected 2026-09-04.** The sentence that stood here said the same 24 configs
> *"would separate cleanly on `trec-covid` (full text) or the PMC target"*. The
> PMC half is right; the `trec-covid` half is **false for the dataset we have**.
> Re-measured with the SFR tokenizer: BeIR `trec-covid` is 171,332 documents,
> median **378** tokens, **max 925** — so **not one document in it could ever
> split at size 1024**, and neither the size nor the overlap contrast has anything
> to bite on there either. The same re-measurement puts scifact at median **348**
> and p95 **649** (the 354 / 662 above is the earlier pass on the same corpus; the
> few-token drift is unexplained and immaterial to every conclusion here).
> nfcorpus (~468 mean) and scidocs (~343) were abstract-length to begin with.
>
> **So the twelve dead cells are dead on every planned BeIR dataset, not just on
> scifact**, and "run stage 1 on trec-covid instead" is not an available option.

That leaves three real options: run stage 1 on a genuinely long-document corpus —
which is what [long-doc-judged-set.md](long-doc-judged-set.md) exists to build, and
whose Leg A judged documents measure a median **4,097** tokens with **80.2%** over
2,048 — or drop the 1024/2048 arms as known-null and spend the budget elsewhere, or
accept the grid as a null at the top of the ladder and say so in the report. That
decision is the user's; nothing here papers over it in code.

> Measured read-only with the cached corpus and the SFR tokenizer — no stores,
> no embedding endpoints. `semantic` is absent from the fill table because it
> needs an `embed_fn`, i.e. a live endpoint.

### Why staged, beyond the 30×

1. ~~**The interaction question is answerable for five minutes of GPU.** Stage 1 exists only
   to find out whether overlap's effect depends on size. If it does not, the grid collapses
   from 12 configs to 4 and the expensive stage shrinks by 3× before it starts.~~
   **Falsified as written, 2026-09-04 — and this is the plan's own biggest methodological
   error.** The interaction *as posed* — a difference-of-differences between the 256 and 2048
   rungs — has an **80%-power floor of 0.213 nDCG against the 0.05 bar this plan wrote for
   it**. It could not have returned a positive answer at n = 10 whatever the truth, and that
   was knowable in advance from its variance structure: a DiD compounds four cells' variance.
   It is recorded as **structurally unanswerable, not as a null**. The **slope form** of the
   same question — the overlap effect per size doubling — resolves four times better (floor
   0.056) and returns a genuinely tight null, **+0.0101, CI [−0.022, +0.044]**; make that the
   primary interaction contrast in stage 2. The collapse-to-4 conclusion does not follow
   anyway, for the separate reason in the staging table above.
   **The transferable rule: compute a contrast's power floor before committing to its
   threshold.** Every later Phase-0 reading was gated that way, and six of fifteen in the
   Leg B round failed the check and were written as unresolved rather than as nulls.
2. **The ladder is the expensive axis, so it must carry the fewest configs.** Every config on
   the ×100 rung costs **~4.7 h** at the measured rate. (This line read 25 h, which never
   matched the ×100 row's own 9.8 h even before the rate correction; both are now computed
   from the same table.) Arms are ~50× cheaper at ×1 than at ×100 — which is the argument for
   deciding as much as possible at ×1.
3. **The ×1000 rung is outside the operating range anyway.** It is 64.5M distractor
   documents against a ~500k-article target, which ×100 already brackets. Dropping it removes
   **~47 h per build** for no loss of relevance — now mostly a relevance argument rather than a
   cost one, since the figure has come down twice: 2.5× from the judged-set correction and 2×
   again from the measured rate.
4. **Failing fast is worth more than completeness.** If the structure-aware config does not beat
   the fixed window on `scifact`, that is known in minutes rather than after a week of
   padding embeddings.

**What staging costs us:** a genuine interaction that only appears at scale or in another
domain would be missed, because stage 1 prunes on one small dataset. Mitigation — carry any
config within noise of the winner rather than only the winner, and keep `fixed_tok512/64` in
stage 2 as the shipping control regardless of how it places.

**Caveat on all of it:** embedding cost per token is **super-linear in sequence length**
(attention), so the 2048 configs cost somewhat more than a token-proportional model predicts.
The ranking of the options does not change; the absolute hours are a floor.

---

## Stage 1, run: what 24 configs on a long-document corpus actually said

*Run 2026-09-04 against a pre-registration written before any embedding call, on the option
this plan named above: a genuinely long-document corpus rather than scifact. Corpus = the
10-topic / 4,053-document TREC CDS pilot. Full report:
[`results/stage1/RESULTS-stage1-legA.md`](results/stage1/RESULTS-stage1-legA.md);
pre-registration: [`results/stage1/PREREG-stage1.md`](results/stage1/PREREG-stage1.md);
generated tables: [`results/stage1/tables.md`](results/stage1/tables.md).*

> **This run prunes nothing, and the reason is not caution.** Leg A has a *measured* bias —
> CDS relevance is document-level and topical, so the leg rewards coarse, aboutness-carrying
> configs — and a second leg has since contradicted it on the size axis with a non-overlapping
> interval ([long-doc-judged-set.md § 14.5](long-doc-judged-set.md)). **n = 10 topics**, so
> the noise floor is ~0.12 nDCG@10 and neighbouring rows of any ranking are not ordered
> claims. Inference is confined to 9 pre-registered contrasts under Holm; the 276 pairwise
> contrasts the grid admits were never tested.
>
> Three grid cells *are* step-3 configs. Re-chunked and re-embedded from scratch, they produce
> **byte-identical chunk files** and **max |diff| = 0.0000** across all twelve metric × config
> values — so the harness reproduces, and the six-endpoint fleet is reproducible enough not to
> be a confound.

### What resolved

**Overlap has no measurable benefit at any size** — recorded in full under *"Overlap belongs
in the grid"* above, because that is where the burden of proof was set. Main effect −0.0210
(power floor 0.046, bar 0.05); recall@100 |Δ| ≤ 0.0033 at every rung; cost up to 1.32× the
vectors. **This is the run's largest actionable result.**

**The interaction's slope form is a genuinely tight null: +0.0101, CI [−0.022, +0.044]**
(power floor 0.056) — that is the *overlap effect per size doubling*, not a size main effect.
So the honest statement is *"we cannot resolve the interaction as posed, but the overlap
effect is small everywhere and its dependence on size is small"* — and the first half of that
sentence is the methodological finding recorded under *"Why staged"* above.

**Nothing else in the pre-registered family resolved.** One contrast came close and is flagged
rather than buried: **`sentence_tok512` beats `fixed_tok512` by +0.0606**, CI [+0.0138,
+0.1072], 7/10 topics — it clears every criterion except Holm across the family of nine
(adjusted p = 0.097). Treat it as the grid's most promising signal and the obvious thing for
stage 2 to power properly. It is not an established result.

**The pre-registered predictions, scored.** P3 — that step 3's non-monotone 256-vs-512
reversal would dissolve into noise once averaged over the overlap axis — **holds exactly**
(+0.0238, CI [−0.041, +0.090]) and is the one clean pre-registered success. P2, the size
effect, is **falsified on nDCG@10** (+0.0904, CI spans zero) but **holds on recall@100**
(+0.0432, CI [+0.0151, +0.0764]). P5 holds: overlap engages on this corpus, which is what
makes the contrast valid here and would have made it void on scifact.

### What predicts the grid, and it is not what the grid is parameterised by

> **Exploratory — NOT pre-registered**, no Holm protection. These correlations are across 24
> *config means* that all share the same 10 topics, so they are not independent observations
> and no inferential claim rests on them. Read it as a description of the grid's shape and a
> hypothesis for stage 2 to pre-register, held to a lower standard than the section above.

| predictor | corr with nDCG@10 | corr with recall@100 |
|---|---:|---:|
| log2 **nominal** size | +0.654 | — |
| log2 **realised median chunk tokens** | **+0.811** | **+0.891** |

**Once you know how many tokens land in a chunk, the chunking *method* adds almost nothing.**
Residual by kind against the realised-size fit spans 0.054 — about the noise floor — from
`sentence` at +0.0251 to `semantic` at −0.0287. `words_tok2048` places well not because word
packing is clever but because its 0.64 fill makes it a ~1,307-token config; `semantic` places
badly because it emits ~350-token blocks whatever its cap says.

This is the *"nominal size is not realised size"* section above arriving as a result rather
than a caveat. Two recommendations follow for stage 2: **parameterise the grid by realised
tokens, or at minimum report against them**, and read no kind-vs-kind row that has not
controlled for realised size.

### Reranking reorders the grid

`bge-reranker-v2-m3` over the top-100 of each config, 48,000 pairs. The spread barely shrinks
(dense 0.224 → reranked 0.205), but **the rank correlation across the 24 configs is only
r = +0.553.** The configs that gain most are the ones that did worst dense (`fixed_tok512`
+0.088, `fixed_tok256_ov25pct` +0.085); the best lose (`fixed_tok1024_ov0pct` −0.096,
`words_tok2048` −0.078).

**A config chosen on dense nDCG is not necessarily the config that wins after reranking, and
the production pipeline reranks.** This is the strongest form yet of the argument in *"The
reranker: measure with it and without it"* below: it is not a robustness check, it is a
different ranking. **Anything that will ship behind a reranker must be evaluated behind one.**
Step 3's label still applies — reranked numbers rank arms, they do not grade the product; the
cross-encoder often *lowers* absolute nDCG against the SFR dense ordering on this clinical set.

### The cost model held, and one part of it was wrong by ~3.5×

| leg | tokens | wall | achieved | requests | retries |
|---|---:|---:|---:|---:|---:|
| chunk embedding, 24 configs | **744 M** | 76.8 min | **161k tok/s** | 130,091 | **0** |
| semantic breakpoint pass | 225 M actual (**567 M notional**) | 17.7 min | 212k tok/s | 56,556 | **0** |
| **total fleet** | **968 M actual / 1,311 M notional** | **94.4 min = 1.57 h** | 171k tok/s | 186,647 | **0** |

**The ~164k tok/s model holds**: the chunk-embed leg came in at **161k, 98% of model**, with
zero retries across 130,091 requests. The other two rates are not comparable to it and should
not be quoted as confirmations — the breakpoint pass runs on ~272-token buffers and is
request-bound rather than token-bound, so it is faster per token precisely because its
sequences are short. Per-config rate also falls with chunk size, as a request becomes
item-bound rather than token-bound.

**The semantic cost model in this plan was wrong by ~3.5×.** It assumed semantic "embeds the
text twice — roughly double cost". `semantic` runs `pool_sentences=False`, so breakpoint
detection embeds **one overlapping seven-sentence buffer per sentence**: ~6× the corpus, *on
top of* the config's own chunk embedding. **Budget semantic at ~7× a `token_window` config of
the same nominal size, and project from notional, not actual** — an identical-text cache saved
60.4% across the four semantic cells only because they ran consecutively and their buffers are
identical except where the token cap bites (99.6% hit at 1024, 45.6% at 256). A semantic cell
run alone pays the full 6×.

Combined with the quality reading, semantic holds the worst cost/benefit position in the grid:
**worst-scoring kind** on this leg (mean 0.4630, ranking 16/18/20/24 of 24), **~7× the
embedding cost**, and a **3.4× larger index** (14.3 chunks/doc vs `token_window`'s 4.2 at
2048). It is the first place to cut if GPU budget forces one — and per the read-this-first
block, still not on Leg A alone.

*(One structural note for anyone re-running it: 12 of 4,053 documents exceeded
`max_breakpoint_sentences = 3000` and were chunked by the `fixed_token` fallback **inside**
the semantic arm, so those documents are not semantically chunked in any semantic row.)*

### The uncomfortable observation, and why it is not yet actionable

**The shipping default `fixed_tok512` ranks 21 of 24** on this leg (nDCG@10 0.4631, against
`sentence_tok2048`'s 0.6289 at the top). That is a real observation and it should not be
hidden. It is also, on its own, not a reason to change anything:

- **n = 10**, and the noise floor is ~0.12 nDCG@10 — wider than the gap to most of the rows
  above it.
- **One leg**, and that leg's measured bias points toward the coarse configs that beat it.
- **The ranking is descriptive.** Nothing in it survived Holm; the top five span 0.629–0.600.
- **It gains the most from reranking of any config in the grid** (+0.088), and the production
  pipeline reranks.

The right response is the one this plan already specifies: carry it into stage 2 as the
shipping control regardless of where it places, and give `sentence_tok512` — the one contrast
that nearly resolved — enough power to settle.

### Recommendations this run makes to stage 2

1. **Make the slope form the primary interaction contrast**, not the extremes DiD. Same data,
   4× the resolution.
2. **Report against realised tokens**, not nominal size alone.
3. **Evaluate behind the reranker.** Dense and reranked rankings correlate only +0.55.
4. **Budget semantic at ~7×.**
5. **Drop the overlap axis only after Legs B/C.** If they agree, 0% overlap is a free
   `1/(1−f)` saving on every index for the product's lifetime.
6. **Power.** At n = 10 the floor is ~0.12 nDCG@10; Leg A at its full 90 topics gives ~0.04,
   which is where most of these contrasts live. **The full leg is worth running — this pilot
   was never going to resolve them.**

---

## The proposed new config: a structure-aware token packer

The pieces exist and have never been combined:

| have | gives |
|---|---|
| `SentenceChunker` | *"no chunk ever splits a sentence"*; packs to a **token** budget |
| `RecursiveCharacterChunker` | a separator hierarchy — paragraphs first |
| `FixedTokenWindowChunker` | exact token accounting and offset mapping |

**For JATS, do not infer paragraphs at all** — `<sec>`, `<title>`, `<p>` are markup, and
`jats.py` already reads section titles. Chunking *within* structural units beats detecting
boundaries in flattened text.

1. Split on the source's own structure (JATS elements; blank lines for text/Markdown).
2. Sentences within a unit (Punkt).
3. Pack whole sentences to a real token budget.
4. Never cross a section; avoid crossing a paragraph unless a chunk would fall under a floor.
5. Preserve exact `start_char`/`end_char` — the offset model in
   [metadata-and-kg.md](metadata-and-kg.md) depends on it.

---

## The reranker: measure with it and without it

**Correction to an earlier draft of this plan.** It named *"rerank score"* as the deciding
signal. That conflated two different columns, and the full report already separates them:

| mode | recall@5 | nDCG@10 | **rerank recall@5** | **rerank MRR@10** | mean score |
|---|---|---|---|---|---|
| fixed | 0.896 | 0.890 | **0.900** | 0.881 | 8.330 |
| sentence | 0.898 | 0.884 | **0.900** | 0.887 | 8.279 |
| semantic | 0.899 | 0.889 | **0.896** | 0.878 | **7.107** |

**After reranking, semantic and fixed are 0.004 apart.** The 8.33 → 7.11 movement is in the
*mean cross-encoder score* — how confident the reranker is — not in ranking quality. Saying
larger chunks "cost precision, and the rerank drop is that cost" overstated it: the score
fell, the ranking did not.

Mean score is a **diagnostic**. Reranked recall/MRR are the **quality measures**. Keep both,
and do not let the first stand in for the second.

### Why the reranker is central, and why both **evaluations** are needed

1. **It is the last gate before the LLM.** Retrieval decides what is *reachable*; the
   reranker decides what actually reaches the answer. A chunking that improves recall but
   whose chunks the reranker then mis-scores has not improved the product.
2. **It is the stage most sensitive to chunk size.** It scores query–chunk pairs directly, so
   a larger chunk dilutes the match with surrounding text in a way a dense retriever's pooled
   embedding partly hides.
3. **Its truncation limit is unknown.** `bge-reranker-v2-m3` truncates, and the sidecar does
   not report the limit. At 2048-token chunks the reranker may be scoring a prefix.

Point 3 is exactly why **with/without is a factor, not an afterthought** — and note this is
a third thing the word *arm* used to cover: a measurement condition, not a chunking config — and it turns a
confound into a diagnostic:

| retrieval-only | reranked | reading |
|---|---|---|
| improves with size | improves | the chunking genuinely helps |
| improves with size | **degrades** | **the reranker is truncating** — an artefact, not a quality loss |
| flat | improves | the reranker is rescuing a weak retrieval |
| degrades | degrades | the chunking is worse, full stop |

Without the retrieval-only **evaluation**, rows 1 and 2 are indistinguishable, and we would have
concluded "big chunks hurt quality" when the truth was "our reranker cannot see them".

**Every config therefore reports both**, and the with-minus-without delta is a reported quantity
in its own right. The existing harness already emits both column families — this is making
the contrast explicit, not new instrumentation.

**Measured 2026-09-02 — DONE, and the 2048 config is clear.** Truncation is at **4096 tokens
per (query, chunk) pair**, not 2048, so the 2048 config's rerank score measures quality rather
than truncation. Method: score one padded chunk repeatedly, holding the answer sentence fixed
and moving it between the start and the end of the padding.

| approx chunk tokens | answer at start | answer at end |
|---:|---:|---:|
| 0 | 0.9868 | 0.9868 |
| 256 | 0.9302 | 0.8325 |
| 1024 | 0.8804 | 0.8047 |
| 2048 | 0.8589 | 0.8032 |
| 4096 | 0.7808 | **0.0025** |
| 6144 | **0.7808** | 0.0025 |

Two independent signatures of a hard cut at 4096: the start column **plateaus** — 0.7808 at both
4096 and 6144, because text past the limit is never read, so adding more cannot move the score —
and the end column **collapses** to 0.0025, the answer having been cut away entirely. A soft
degradation would show neither.

Two consequences for reading this study's results:

1. **Below 4096 the position effect is real, not an artefact.** At 2048 the same answer scores
   0.8589 at the front and 0.8032 at the back. Larger chunks genuinely dilute — that is a
   quality finding to report, not a measurement error to correct for.
2. **At or above 4096 the rerank score is meaningless for late content**, so no config in this
   study may exceed it. 2048 is the largest size tested and sits safely under, query included.

Re-measure if the reranker model or its `MAX_LENGTH` changes; the cut is a property of both.

### The reranker can reverse a first-stage verdict — measured, 2026-09-04

Point 1 above ("it is the last gate before the LLM") stopped being an argument and became a
measurement during Phase 0, and it is the reason this study reports both families rather
than one. On the TREC CDS pilot, first-stage dense retrieval says **a lead-only index is
better than the full one**: tok512-full − lead512 recall@100 = **−0.062, CI
[−0.084, −0.040]**. Put the reranker behind the same two indexes and it flips —
tok512_rr − lead512_rr nDCG@10 = **+0.137**, though that estimate's CI is
**[−0.005, +0.294]** and spans zero at n=10; the CI-clean version of the reversal is grade≥2
**MRR@10 +0.299, CI [+0.074, +0.542]**.

Two things follow for this plan:

1. **A retrieval-only reading of "does the body text matter" can be exactly backwards.** The
   reranker is the component that reads passages; an evaluation that omits it is not a
   cheaper version of the same question, it is a different question. A whole Phase-0 step
   drew the wrong conclusion from a BM25-only ablation for precisely this reason
   ([long-doc-judged-set.md](long-doc-judged-set.md) § 13.2).
2. **Reranked numbers rank arms; they do not grade the product.** On that clinical set
   `bge-reranker-v2-m3` sometimes lowers absolute nDCG against the SFR dense ordering
   (0.578 vs 0.600 for the same config). The contrast between arms is the signal; the level
   is not.

## Metrics, and reporting cost beside quality

`recall@{1,5,10}`, `nDCG@10`, `MRR@10`, **rerank score**, `chunks/doc`, chunking seconds,
total ingest seconds, and the resulting vector footprint.

Cost belongs in the same table as quality. Semantic's headline was 4.4× fewer chunks; its
unreported figure was **748× the chunking cost** and an **8.33 → 7.11 rerank-score drop**.

> **Added 2026-09-05 — every metric above is a *document* metric, and that is the wrong
> half.** The small-corpus re-score
> ([`results/rescore/`](results/rescore/RESULTS-rescore-small-corpora.md); n = 260 queries,
> one per document, every one written from a deep section, none answerable from an abstract)
> measured the document metric and the passage metric on the *same query, same document, same
> embedding*. At a **one-document** corpus, where the document metric is **1.0000 by
> arithmetic**, the top-ranked chunk lands in the gold section only **55–65%** of the time.
> `Gap@1 = DH@1 − PH@1` is **+0.28 to +0.45**, **RESOLVED 15 of 15** — `|mean| ≥ 0.05`, CI
> excludes 0, `δ80 ≤ |mean|` — against a power floor of ≈0.087 that every reading clears by
> 3–5×.
>
> It is a **top-1 phenomenon, not a recall one.** By `k = 10` the gap is gone (+0.019 /
> +0.023 / −0.012 / −0.015 at N = 100), because with ten chunks in hand you touch the gold
> section somewhere: `PH@10 ≈ 0.97–0.99`. Two pre-registered predictions aimed at `k = 10`
> **both failed** and are recorded as failures.
>
> So: if the consumer reads ten passages, the document metric is not badly misleading. If the
> consumer is a person, a citation, a snippet, or an agent acting on the first hit, it
> **overstates quality by roughly 30–45 points**. Every number in this study before
> 2026-09-05 — the +0.137 that reversed Leg A's demotion, and the whole stage-1 grid above —
> measured *"found the right paper"*, not *"found the answering passage"*. **Stage 2 must
> carry a passage-level metric beside each document metric**, or it is measuring the corpus
> size. See [long-doc-judged-set.md § 15.1](long-doc-judged-set.md).

---

## Pre-registration: what result changes what decision

Without this the output is a table nobody acts on.

| decision | the config that settles it | what would change it | status 2026-09-04 |
|---|---|---|---|
| chunk size for the OA load | `fixed_tok512` vs `fixed_tok1024/0` | if 1024/0 loses ≤0.01 nDCG for ~2× fewer chunks, take it — that is 0.25 TB | **BLOCKED — the two legs disagree** on the size axis with non-overlapping intervals, and each direction is predicted by its own leg's query construction. Also, **the ≤0.01 rule is dead as a hypothesis test**: at the measured σ_d it needs ~4,700–5,700 queries. Re-register as TOST at 0.02 and answer the size question *conditioned on query target* |
| build the structure-aware chunker? | `structure_tok512` vs `fixed_tok512` | build it only if **reranked recall/MRR** improves at comparable chunks/doc — mean score alone is a diagnostic, not a result | **unchanged — the packer does not exist yet.** `words` still stands in. The nearest evidence is `sentence_tok512` − `fixed_tok512` = **+0.0606**, CI excluding zero, 7/10 topics, but **not surviving Holm** (adj. p = 0.097): promising, unproven, and the obvious thing to power properly |
| keep the 64-token overlap? | `fixed_tok512/64` vs `/0` | drop it unless it earns >0.01 recall@5; it costs 12.5% of the index | **it does not earn it on Leg A** — negative on nDCG at all four rungs, recall@100 \|Δ\| ≤ 0.0033, adequately powered. **Pending Leg B**, which has not tested overlap. This is the closest thing the study has to a decidable row |
| revisit semantic? | `semantic_tok512_capped` | only if it wins on **reranked** metrics with its cap policy declared. Its current 0.004 reranked gap is inside noise on a proxy that flatters lead chunks — neither a win nor a loss | **it does not win** — worst-scoring kind on Leg A (ranks 16/18/20/24), **~7×** the embedding cost, **3.4×** the index. Cut it on cost-effectiveness if budget forces a cut; per the circularity discipline, not on Leg A's quality reading alone |

**Prediction on record:** structure-aware chunking improves **reranked** recall/MRR at
similar chunk counts, because less irrelevant text rides along in each chunk. If only the
mean score moves and the ranking does not, that is *not* the prediction confirmed — it is the
same non-result semantic produced.

**Added 2026-09-04 — which corpus may settle which row.** TREC CDS is now measured to
separate chunk sizes (tok2048 − tok512 nDCG@10 **+0.137**, CI [+0.051, +0.225], 8/10
topics), so it can speak to the size row. But its measured bias is to reward **coarse,
aboutness-carrying** configs — the same direction as the cheaper option in that row — so
**no row above may be settled on a document-level judged leg alone**. The size decision in
particular needs a leg whose evidence sits deep by construction (Legs B and C of
[long-doc-judged-set.md](long-doc-judged-set.md)) to contradict it, or it is the bias being
reported as a finding. Concordance across legs is the standard; a disagreement is a finding
to investigate, not to average away.

**Amended the same day — the leg that was supposed to contradict it did, and that is now the
study's gating problem, not its resolution.** Leg B ran at n = 260, and on recall@100 — the
one contrast where *both* legs resolve — the signs are opposite and the intervals do not
overlap: **Leg A +0.043 [+0.015, +0.076] for coarse, Leg B −0.035 (t = −3.05) for fine.**

Read the two carefully before quoting them. On nDCG@10 only Leg B resolves (−0.041,
t = −3.63); Leg A's point estimate there is the *larger* one (+0.090) and simply cannot
exclude zero at 10 topics — **that row is a Leg B result and a Leg A non-result, not a
head-to-head.** And Leg A's recall@100 figure is the *overlap-averaged* size main effect from
the stage-1 family rather than the literal `/0` pair Leg B measured; the literal `/0` cells
read **+0.0442** as a point estimate in the same direction, with no published CI.

**Neither direction is clean.** Leg A's judgments are document-level and topical, so aboutness
is declared in title+abstract and coarse configs are favoured. Leg B anchors a rare entity in
one deep section and scores by max-rollup over chunks, so a small chunk carrying that entity is
exactly what the scoring rewards. **Each leg is constructed to favour the direction it
reports**, which means:

> **No config may be pruned on either leg's direction** until the study declares which query
> population it optimises for. The circularity rule in
> [long-doc-judged-set.md § 7](long-doc-judged-set.md) stops a *chunker* grading its own
> homework; it had no clause for a *query construction* doing the same, and that clause has
> now been written into it on the strength of this disagreement.

**The plan of record, in consequence** (§ *Staged* above carries the cost): do **not** prune to
~4 configs. Keep the **size** axis intact and report recommendations *conditioned on query
target*; cut only on **uncontested axes** — overlap, if Leg B confirms the null — and on
**cost-effectiveness** — semantic. That is ~6× the stage-2 budget and it costs power under
Holm, and both are preferable to recording a construction bias as a finding. **Leg C is the
tiebreak**: human-authored queries, no LLM, a third bias profile, and it has not been run
against the grid. That is the highest-value unrun measurement in the study.

**Added 2026-09-05 — the size row must be read budget-matched, and the answer flips when it
is.** At fixed `k`, ten 2048-token chunks is 8× the context of ten 256-token chunks, so a
fixed-`k` comparison across sizes is not a comparison of chunking. Admitting chunks in rank
order until a **4,096-token budget** is spent — realised budgets matched to within 9–13% —
reverses the ordering everywhere it has been measured:

| reading | best | source |
|---|---|---|
| raw `PR@1`, Leg A m=1 | `tok2048` 0.087 vs `tok256` 0.058 | [`results/breadth-k/`](results/breadth-k/RESULTS-breadth-k.md) |
| budget-matched `PR_B@4096`, Leg A m=1 | **`tok256`** 0.291 vs `tok2048` 0.132 — **2.2×** | same |
| budget-matched `PR_B@4096`, Leg A m=16 | **`tok256`** 0.181 vs `tok2048` 0.047 — **3.8×** | same |
| budget-matched `H_B@4096`, Leg B N=1…400 | **`tok256`** 0.996 vs `tok2048` 0.815–0.842 | [`results/rescore/`](results/rescore/RESULTS-rescore-small-corpora.md) |

Two corpora, two query styles, four breadth rungs, same direction — and it is the **first
reading on which the two legs agree**, where the document-level grid had them contradicting
each other. That is not yet enough to settle the size row: both legs' query constructions
favour small chunks for stateable reasons (§ above), it is one metric family, and the raw
`@1` reading still points the other way. The dissent inside the family is recorded too —
budget-matched *recall* (`R_B@4096`) has `tok1024` in front and **nothing resolves** (extremes
+0.0430 against δ80 0.0714, Holm p 0.37), reported as unresolved rather than as a null.

**What does change now:** any future size comparison in this study must publish the
budget-matched reading beside the fixed-`k` one, and say which of the two a recommendation
rests on.

**And `k` is not a free parameter to leave at 10.** On the harder leg — Leg A, oracle-derived
gold inside topically-judged documents — `k = 20` retrieves only **21–32%** of the gold
passages and the curve is still climbing (`k*`, the smallest `k` reaching 90% of the `k = 20`
value, is **20 at every rung**). On the easier leg, `PH@10 ≈ 0.97–0.99` and `k = 20` is free.
Any `k` quoted in a decision table must name which difficulty regime it was chosen under.
