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
window**. An arm whose oversize handling is not pinned measures configuration, not method.

**4. The token counter can silently resize every arm.** `make_token_counter` defaults to
`chars_per_token = 2.5` and `chunker_config` **falls back to `estimate` when a model is
unavailable** — it logs, it does not refuse. Production measures **3.50 chars/token**, so a
"512-token" arm can really be 366 tokens: **29% under-filled, ~1.4× the chunks**.

---

## Prerequisite, before any run

**Pin the token counter.** For an evaluation or a corpus build, the estimator must be an
explicit opt-in and a missing tokenizer must **fail**, not fall back. Same rule as #454:
make the value required rather than defaulted. Re-running experiments on top of a silent
40% resize reproduces the confusion at higher resolution.

---

## Design

### Arms are configurations, not names

Every arm declares: kind, target size, overlap, **token counter backend**, cap policy, and
boundary rule. Two arms differing only in size are two arms — that is the point, since size
is the lever the current data says dominates.

Minimum set:

| arm | why it is in |
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
| `scidocs` | broader science | the "different scientific area" arm |

That covers *"different scientific area"* and *"mixed documents"* with judgments already in
hand.

### Collection size: a distractor ladder, **not** subsampling

**The repo has already hit this trap.** The G1 pilot ran 50/100/200-document rungs and the
verdict was:

> *"not answerable from this run… the pilot's queries are the 24/46/90 whose judged documents
> landed in the sample… 0.95 here vs 0.74 there is 'small corpora are easy', not a
> comparison."*

Subsampling a corpus destroys the judgments: most queries lose their relevant documents, and
scores rise because the haystack shrank. The design that works — and which that pilot names —
is a **distractor ladder**:

- hold the **judged set fixed** (all documents any query is judged against),
- vary the number of **unjudged distractor** documents around it: ×1, ×10, ×100, ×1000,
- same queries and same needles at every rung.

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
truncates at 2048-token chunks, the 2048 arm's rerank score measures truncation rather than
quality — and rerank score is the metric this study leans on.

---

## Cost: factorial vs staged, on the existing 4-endpoint fleet

**Rate.** ~**80k tokens/s** aggregate, from the live-validation record: 5,915
`fixed_token 512` chunks embedded in 38.4 s of compute (~3.0M tokens), on 3 of 4 workers.
Treat as **±2×** — it is one measurement, not a benchmark.

**What actually costs.** Embedding tracks **total tokens**, which is roughly invariant to
chunk *size* over the same corpus — but overlap adds tokens directly (`1/(1−f)`), and the
**distractor ladder multiplies the whole corpus and is re-embedded per arm**. Chunking
itself is ~2,200 chunks/s on CPU and is noise.

Corpora: `scifact` ~1.8M tokens (cached locally); `nfcorpus` ~1.5M, `scidocs` ~9M,
`trec-covid` ~60M (estimates — only scifact is in `HF_HOME` today). ~72M tokens for all four.

| ladder rung | corpus | **per arm** |
|---|---|---|
| ×1 | 72M tokens | 0.3 h |
| ×10 | 723M | 2.5 h |
| ×100 | 7.2B | **25 h** |
| ×1000 | 72B | **251 h** |

### Full factorial — 12 arms × 4 datasets × 4 rungs

**≈ 3,350 GPU-hours ≈ 139 days** of fleet wall-clock. Not a long experiment; a project that
would occupy the embedding fleet for a third of a year.

### Staged

| stage | what | cost |
|---|---|---|
| 1 | 12 arms (4 sizes × 3 overlaps), `scifact` only, ×1 rung | **~5 minutes** |
| 2 | ~4 survivors, 4 datasets, ×1 / ×10 / ×100 | **~111 h (4.6 days)** |
| | **total** | **~112 h — 30× cheaper** |

### Why staged, beyond the 30×

1. **The interaction question is answerable for five minutes of GPU.** Stage 1 exists only
   to find out whether overlap's effect depends on size. If it does not, the grid collapses
   from 12 arms to 4 and the expensive stage shrinks by 3× before it starts.
2. **The ladder is the expensive axis, so it must carry the fewest arms.** Every arm on the
   ×100 rung costs 25 h. Arms are cheap at ×1 and ruinous at ×100 — which is an argument for
   deciding as much as possible at ×1.
3. **The ×1000 rung is outside the operating range anyway.** 72B tokens is ~2.2M documents;
   the OA target is ~500k, which ×100 (≈220k docs) already brackets. Dropping it removes
   **251 h per arm** for no loss of relevance — cost and validity agreeing for once.
4. **Failing fast is worth more than completeness.** If the structure-aware arm does not beat
   the fixed window on `scifact`, that is known in minutes rather than after a week of
   padding embeddings.

**What staging costs us:** a genuine interaction that only appears at scale or in another
domain would be missed, because stage 1 prunes on one small dataset. Mitigation — carry any
arm within noise of the winner rather than only the winner, and keep `fixed_tok512/64` in
stage 2 as the shipping control regardless of how it places.

**Caveat on all of it:** embedding cost per token is **super-linear in sequence length**
(attention), so the 2048 arms cost somewhat more than a token-proportional model predicts.
The ranking of the options does not change; the absolute hours are a floor.

---

## The proposed new arm: a structure-aware token packer

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

## Metrics, and reporting cost beside quality

`recall@{1,5,10}`, `nDCG@10`, `MRR@10`, **rerank score**, `chunks/doc`, chunking seconds,
total ingest seconds, and the resulting vector footprint.

Cost belongs in the same table as quality. Semantic's headline was 4.4× fewer chunks; its
unreported figure was **748× the chunking cost** and an **8.33 → 7.11 rerank-score drop**.

---

## Pre-registration: what result changes what decision

Without this the output is a table nobody acts on.

| decision | the arm that settles it | what would change it |
|---|---|---|
| chunk size for the OA load | `fixed_tok512` vs `fixed_tok1024/0` | if 1024/0 loses ≤0.01 nDCG for ~2× fewer chunks, take it — that is 0.25 TB |
| build the structure-aware chunker? | `structure_tok512` vs `fixed_tok512` | build it only if **rerank score** improves at comparable chunks/doc; a recall-only tie is not enough |
| keep the 64-token overlap? | `fixed_tok512/64` vs `/0` | drop it unless it earns >0.01 recall@5; it costs 12.5% of the index |
| revisit semantic? | `semantic_tok512_capped` | only if it wins on **rerank score** with its cap policy declared — the current claim rests on a proxy that flatters lead chunks |

**Prediction on record:** structure-aware chunking improves rerank score at similar chunk
counts, because less irrelevant text rides along in each chunk — which is precisely where
semantic chunking lost. If that does not reproduce, keep the fixed window and tune size.
