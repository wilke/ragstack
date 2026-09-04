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
| 1 | 24 configs — 12 `fixed`×sizes×overlaps, then 12 other kinds×sizes — `scifact`, ×1 | **~1.4 h** | ~2.8 h |
| 2 | ~4 surviving configs × 3 rungs = **12 builds**, 4 datasets | **~25 h** | ~51 h |
| | **total** | **~27 GPU-hours** | ~54 |

The 30× argument for staging is unchanged by the rate correction — it halves both sides.
What *does* change the staged plan is the corpus finding above: stage 1 on `scifact` prunes
on a corpus that cannot exercise half its own grid, whatever it costs.

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

1. **The interaction question is answerable for five minutes of GPU.** Stage 1 exists only
   to find out whether overlap's effect depends on size. If it does not, the grid collapses
   from 12 configs to 4 and the expensive stage shrinks by 3× before it starts.
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

---

## Pre-registration: what result changes what decision

Without this the output is a table nobody acts on.

| decision | the config that settles it | what would change it |
|---|---|---|
| chunk size for the OA load | `fixed_tok512` vs `fixed_tok1024/0` | if 1024/0 loses ≤0.01 nDCG for ~2× fewer chunks, take it — that is 0.25 TB |
| build the structure-aware chunker? | `structure_tok512` vs `fixed_tok512` | build it only if **reranked recall/MRR** improves at comparable chunks/doc — mean score alone is a diagnostic, not a result |
| keep the 64-token overlap? | `fixed_tok512/64` vs `/0` | drop it unless it earns >0.01 recall@5; it costs 12.5% of the index |
| revisit semantic? | `semantic_tok512_capped` | only if it wins on **reranked** metrics with its cap policy declared. Its current 0.004 reranked gap is inside noise on a proxy that flatters lead chunks — neither a win nor a loss |

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
