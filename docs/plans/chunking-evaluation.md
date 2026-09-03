# Redoing the chunking evaluation

**Status:** `PROPOSED`. The existing comparison cannot answer the questions we are about to
spend a corpus on, and the reason is the ground truth rather than the configurations.

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
| `trec-covid` | biomedical, full text | closest to the PMC target |
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
  confidence intervals. Use it for realism — it is the closest thing to the PMC target — not
  as the decisive comparison.
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

**Rate.** ~**80k tokens/s** aggregate, from the live-validation record: 5,915
`fixed_token 512` chunks embedded in 38.4 s of compute (~3.0M tokens), on 3 of 4 workers.
Treat as **±2×** — it is one measurement, not a benchmark.

**What actually costs.** Embedding tracks **total tokens**, which is roughly invariant to
chunk *size* over the same corpus — but overlap adds tokens directly (`1/(1−f)`), and the
**distractor ladder multiplies the whole corpus and is re-embedded per config**. Chunking
itself is ~2,200 chunks/s on CPU and is noise.

Corpora: `scifact` ~1.8M tokens (cached locally); `nfcorpus` ~1.5M, `scidocs` ~9M,
`trec-covid` ~60M (estimates — only scifact is in `HF_HOME` today). ~72M tokens for all four.

> **Corrected 2026-08-31.** An earlier version of this table multiplied the *whole corpus*
> by the rung. That is not the design: the ladder pads around the **judged set**, which is
> small. The real figures are ~2.5× lower, from the datasets now cached locally.

| rung | distractor docs | tokens | **per index build** |
|---|---|---|---|
| ×1 | 64,548 | 54M | 0.19 h |
| ×10 | 645,480 | 306M | 1.06 h |
| ×100 | 6,454,800 | 2.8B | **9.8 h** |
| ×1000 | 64,548,000 | 28B | **97 h** |

### Full factorial — 48 configs × 4 datasets × 4 rungs

**≈ 1,300 GPU-hours ≈ 54 days** of fleet wall-clock.

### Staged

| stage | what | cost |
|---|---|---|
| 1 | 24 configs — 12 `fixed`×sizes×overlaps, then 12 other kinds×sizes — `scifact`, ×1 | **~2.8 h** |
| 2 | ~4 surviving configs × 3 rungs = **12 builds**, 4 datasets | **~51 h** |
| | **total** | **~54 GPU-hours** |

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

### Why staged, beyond the 30×

1. **The interaction question is answerable for five minutes of GPU.** Stage 1 exists only
   to find out whether overlap's effect depends on size. If it does not, the grid collapses
   from 12 configs to 4 and the expensive stage shrinks by 3× before it starts.
2. **The ladder is the expensive axis, so it must carry the fewest configs.** Every config on the
   ×100 rung costs 25 h. Arms are cheap at ×1 and ruinous at ×100 — which is an argument for
   deciding as much as possible at ×1.
3. **The ×1000 rung is outside the operating range anyway.** It is 64.5M distractor
   documents against a ~500k-article target, which ×100 already brackets. Dropping it removes
   **97 h per build** for no loss of relevance — now mostly a relevance argument rather than a
   cost one, since the corrected figure is 2.5× lower than first stated.
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

**Still a prerequisite:** measure the sidecar's effective truncation point before the run, so
the 2048 config's numbers can be interpreted rather than argued about afterwards.

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
