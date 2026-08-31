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
