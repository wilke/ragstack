# Choosing a chunk method

Before a document is embedded it is cut into **chunks**, and each chunk becomes
one vector and one search hit. This page is for people creating collections and
for admins deciding what a deployment should offer. It covers what each method
does, who can pick one, what the measurements say, and what you cannot change
later. For how the chunkers work inside, see
[ARCHITECTURE-DEEP-DIVE.md §2](ARCHITECTURE-DEEP-DIVE.md#2-chunking). For which
ingest path runs your upload, see [ingest-paths.md](ingest-paths.md).

**Short version.** Keep the server default, `fixed_token` 512/64. Semantic
chunking is opt-in, admin-only, and more expensive, and no committed measurement
shows it retrieving better. Whatever method a collection gets, it keeps for
life.

## The default

**`fixed_token` (a 512-token window with 64 tokens of overlap) is the default
for new collections.** The owner decided this on 2026-10-06, "for now". Earlier
deployments defaulted to `fixed`, a character window of 512/64 (the
`chunk_method` / `chunk_size` / `chunk_overlap` defaults in
`python/ragstack/config.py` `Settings`). Collections created under that default
stay `fixed`; see [identity](#a-collection-keeps-its-method-for-life).

A deployment can override the default with `CHUNK_METHOD`, `CHUNK_SIZE` and
`CHUNK_OVERLAP` in its `tenant.env`. No tenant uses a semantic method as its default today.

## The six methods

`python/ragstack/ingestion/chunkers.py` `CHUNK_METHODS` lists them, and
`make_chunker` builds them.

| method | size and overlap are counted in | where chunks end | embeds while chunking | GoWe workflows that accept it |
|---|---|---|---|---|
| `fixed` | characters | every *size* characters, mid-word if necessary | no | all |
| `fixed_token` | tokens of the collection's embedding model | every *size* tokens | no | all |
| `sentence` | characters, or tokens with `size_unit="tokens"` (see [token budget](#token-budget)) | sentence boundaries | no | all |
| `words` | characters, or tokens with `size_unit="tokens"` (see [token budget](#token-budget)) | word boundaries | no | all |
| `semantic` | not used (see below) | at topic shifts that embeddings detect | yes, about 7× the tokens of `semantic_pooled` | `pdf-ingest-scatter.cwl`, `ingest-bulk.cwl` only |
| `semantic_pooled` | not used (see below) | at topic shifts that embeddings detect, in different places from `semantic` | yes | `pdf-ingest-scatter.cwl`, `ingest-bulk.cwl` only |

- **`fixed`**: `RecursiveCharacterChunker`. Takes a window of *size*
  characters, then steps forward by *size − overlap*.
- **`fixed_token`**: `FixedTokenWindowChunker`. Tokenizes the whole document
  once and takes a window of *size* tokens, stepping forward by *size − overlap*
  tokens. Each chunk is cut out of the source text by character offset. The
  tokenizer is the HF fast tokenizer of **the collection's embedding model**
  (the measurements below used `Salesforce/SFR-Embedding-Mistral`), so a chunk
  never exceeds *size* tokens of that model. It needs an embedding model with
  a loadable HF tokenizer and refuses to start without one
  (`chunker_config.py` `resolve_token_backend`; `api/deps.py` `_chunker_for`).
  A *size* larger than the token budget (`max_tokens`) is refused with a
  `ValueError` naming both numbers, so on the bulk path a window wider than the
  model's (window − 16) cannot be built.
- **`sentence`**: `SentenceChunker`. Packs whole sentences up to *size*, and
  the next chunk repeats about *overlap* characters of sentences. What *size*
  counts is `size_unit` (`--chunk-size-unit` on the bulk tools):
  - `chars` (the default): *size* characters. If there is a token budget, it
    is a **cap**. A packed chunk that comes out over the budget is re-packed
    into whole sentences that fit. Any chunk already within the budget is left
    exactly as it was (see [token budget](#token-budget)).
  - `tokens`: *size* tokens, capped at the token budget when there is one. A
    single sentence longer than *size* tokens is split by tokens at *size*, not
    at the model window, so no chunk exceeds *size*. Needs a token counter
    and refuses to build without one.

  *size* is always used. *overlap* is characters in both units. Under `chars`,
  the pieces of a re-packed chunk do not overlap each other. The overlap with
  the next chunk is the same as it would be without the cap. A sentence is
  split only when it alone is over the budget. Sentences are found with NLTK
  Punkt when the `[chunking]` extra is installed and with a regex otherwise. A
  *size* of `-1` puts the whole document in one chunk (still token-split to fit
  a budget).
- **`words`**: `WordChunker`. The same as `sentence`, with words as the unit,
  including `size_unit`. *size* `-1` also works here.
- **`semantic`**: `SemanticChunker`. Splits the text into sentences. For each
  sentence it embeds the text of a window reaching `buffer_size` sentences
  either side, then cuts wherever the cosine distance between neighbouring
  windows is above the `breakpoint_percentile_threshold` percentile. Chunks
  shorter than `min_chunk_length` *characters* are merged into a neighbour.
  With a token budget, a chunk over the budget is split **at sentence
  boundaries**, packing as many whole sentences as fit; a cut falls inside a
  sentence only when that one sentence is longer than the budget. (Before
  #318's follow-up it was cut wherever the token count ran out, usually
  mid-sentence.)
    *size* and *overlap* only matter for a document that splits into more than
  3,000 sentence spans (`max_breakpoint_sentences`). On GoWe that document is
  chunked by the `fixed_token` window instead, using the collection's
  size/overlap or, if it recorded none, the workflow defaults (256/32 in
  `pdf-ingest-scatter.cwl`). The in-process path does the same since
  [#647](#token-budget) (before it, no tokenizer was wired there and the
  fallback returned the whole document as one chunk). The `fixed_token`
  fallback window does cut mid-sentence; that is a deliberate cost guard and is
  unchanged. Apart from that fallback, semantic chunks do not overlap.
- **`semantic_pooled`**: the same class with `pool_sentences=True` and
  distances rounded to 6 decimals. It embeds each sentence once and averages
  the vectors over the window, instead of embedding every window's text. That
  is cheaper, and it puts the cuts somewhere else; see
  [below](#semantic-and-semantic_pooled-are-different-chunkers).

**Semantic tunables** go in the collection's `chunk.params`, which the API checks
in `routers/collections.py` `SEMANTIC_PARAM_BOUNDS`:

| param | default | allowed |
|---|---|---|
| `buffer_size` | 3 | 1–50 |
| `breakpoint_percentile_threshold` | 80.0 | 1–100 |
| `min_chunk_length` (characters) | 500 | 0–100,000 |

The defaults come from `make_chunker`'s signature and `Settings.chunk_buffer_size`
/ `chunk_breakpoint_percentile` / `chunk_min_length`.

## A collection keeps its method for life

The chunk method is part of a collection's identity
([ADR-0002](adr/0002-collection-identity.md)):

- **It is set when the collection is created and cannot be edited.** No
  endpoint changes a collection's chunking. An ingest whose build spec differs
  from the collection's is refused with `409` (ADR-0002, decision 3).
- **To change it, create a new collection and ingest everything again.** The
  old collection is not converted.
- **Physical storage depends on it.** The method, size, overlap and params form
  a descriptor such as `fixed_token/512/64` (`provenance.py`
  `chunk_descriptor`). That descriptor goes into the physical Qdrant/ES name
  (`stores/qdrant.py` `collection_name`) and into the manifest's `spec_hash`
  (`provenance.py` `spec_hash`, a hash of `model|dim|chunk`).
- **The default is fixed at creation too.** A collection created without a
  `chunk` gets the server default written into its spec as concrete values
  (`routers/collections.py` `create_collection`, step 2). If an operator later
  changes the default, existing collections keep their method; only new ones
  get the new default.

`GET /v1/collections` shows each collection's `chunk_method`, `chunk_size`,
`chunk_overlap` and `chunk_params`.

## Who can choose

- **Ordinary users get the server default.** In `POST /v1/collections`, a body
  with `chunk` (or `embedding`) from anyone but an admin is refused with `403`:
  "build-spec overrides ('embedding', 'chunk') are admin-only"
  (`routers/collections.py` `create_collection`, step 0a).
- **Admins can send an explicit `chunk`:**
  `{"method": "...", "size": N, "overlap": N, "params": {...}}`. It is checked
  by `_validate_chunk`: the method must be known, *size* must be ≥ 1 (or `-1`
  for `sentence`/`words`), and *overlap* must be ≥ 0 and smaller than *size*.
- **In the UI**, *Collections → New collection* offers "Server default
  (recommended)" and "Choose a strategy (admin only)"
  (`frontend/src/components/NewCollectionForm.tsx`).
- **Operators running the bulk CLI** (`python/scripts/ingest_jsonl.py`) pass
  `--chunk-method` directly.

## Which tenant can run which method

On `INGEST_BACKEND=local` (the default) the API process does the chunking and
can run all six methods.

On `INGEST_BACKEND=gowe` the API never chunks. Uploads go to a GoWe workflow,
and the worker tool `python/scripts/ingest_shard.py` chunks. Two things limit
what that tool will accept:

1. **`INGEST_WORKER_UNSUPPORTED_METHODS`**, a per-tenant list. The code default
   is `semantic,semantic_pooled` (`Settings.ingest_worker_unsupported_methods`).
   For a method on the list, the API refuses both creating the collection and
   submitting an ingest, with `422` (`routers/collections.py`
   `_refuse_chunk_this_deployment_cannot_ingest`; `routers/documents.py`
   `_refuse_unrunnable_chunk_method`). An empty value refuses nothing. A
   misspelled method name is an error, not a no-op
   (`chunker_config.py` `parse_unsupported_methods`).
2. **The workflow's `chunk_method` enum** (#643). Only `cwl/pdf-ingest-scatter.cwl`
   and `cwl/ingest-bulk.cwl` accept `semantic` and `semantic_pooled`.
   `cwl/pdf-ingest.cwl`, `cwl/jats-ingest.cwl` and `cwl/embed-bulk.cwl` accept
   only `fixed`, `fixed_token`, `sentence` and `words`.

**What each tenant allows today** (read from
`/rag/data/tenants/*/config/tenant.env` on 2026-10-06):

| tenant | `INGEST_BACKEND` | `INGEST_WORKER_UNSUPPORTED_METHODS` | methods it can run | `CHUNK_METHOD` set? |
|---|---|---|---|---|
| `dev` | `gowe` (`pdf-ingest-scatter.cwl`) | empty | all six | no, so the code default |
| `hackathon` | `gowe` (`pdf-ingest-scatter.cwl`) | empty, with a comment calling this "owner's decision" | all six | no, so the code default |
| `demo` | unset → `local` | not used | all six | `fixed_token` 512/64 |
| `asm` | unset → `local` | not used | all six | no, so the code default |
| `lucid` | unset → `local` | not used | all six | no, so the code default |

"Can run" is not "can choose": on every tenant, only an admin can pick a method
other than the default.

## `semantic` and `semantic_pooled` are different chunkers

`semantic_pooled` is not a cheaper way to get the same chunks. The two put
their cuts in different places.

- **Small test** ([`semantic-vs-pooled-2026-09-18.md`](plans/results/semantic-vs-pooled-2026-09-18.md)):
  3 synthetic documents, so small n, at `buffer_size=2`. On the documents that
  split, the two methods shared no boundaries. The rank correlation of their
  distance series was 0.4254 and span Jaccard was 0.111. Run against itself,
  each method was reproducible (Spearman 0.9984 / 0.9993), so the gap comes from
  the method and not from noise. `semantic` embedded 4.47× the tokens of
  `semantic_pooled` at `buffer_size=2`; the same file estimates ~7× at the
  default of 3.
- **Corpus scale** ([`salmonella-amr-semantic-vs-pooled-2026-09-24.md`](plans/results/salmonella-amr-semantic-vs-pooled-2026-09-24.md)):
  20 real papers ingested on hackathon through API → GoWe, with 13,274
  distance pairs. The two methods shared 2 spans out of 791 (Jaccard 0.0025),
  and one of those two was a document neither method split. Rank correlation
  was 0.2694. The cost ratio measured **6.78×** in tokens at `buffer_size=3`.
  **The token saving barely changed the time taken:** the worker's ingest step
  took 65.8 s for `semantic` and 62.4 s for `semantic_pooled`, because the
  averaging step (`_mean_pool`) is pure Python.

Neither study says which method retrieves better; neither had a query set.
Because the method is part of a collection's identity, picking
`semantic_pooled` to save tokens also fixes where the chunks fall, permanently.

## What the measurements say about size and overlap

Every number below comes from a committed file.

- **7-way comparison**
  ([`python/scripts/eval/chunking_compare_7way_report.md`](../python/scripts/eval/chunking_compare_7way_report.md);
  STATUS.md v0.14.0): 300 article records and 300 known-item (title) queries.
  It compared `fixed` at 512 and 2048 characters, `fixed_token` at 256 and 512,
  `sentence` and `words` packed to ≤512 tokens, and `semantic` with a token cap.
  No config separated from `fixed_tok512` (reranked MRR@10 0.904; every
  paired-bootstrap difference interval includes 0; no Wilcoxon test survives
  Holm correction). STATUS.md records the recommendation as `fixed_tok512`:
  deterministic, with no chunk overflowing the embedder. The report itself
  calls `fixed_tok256` the narrow "quality winner" (reranked recall@5 0.917
  against 0.910, at twice the chunks per document) and suggests weighing it
  against `fixed_tok512` for a production rebuild.
- **SciFact**
  ([`python/scripts/eval/scifact_chunk_eval_report.md`](../python/scripts/eval/scifact_chunk_eval_report.md);
  STATUS.md v0.15.0): 5,183 abstracts and 300 claim queries with real relevance
  judgements. Under Holm-corrected Wilcoxon, no config differs from
  `fixed_tok512` (nDCG@10 0.698). The largest difference was `fixed_tok256`, +0.023 nDCG@10,
  with a paired-bootstrap interval of [0.006, 0.040] but Holm p = 0.077.
  Abstracts are short: at 512 tokens there are 1.18 chunks per document, so on this benchmark `fixed_tok512` is close to one chunk per abstract. Chunking time was 169.2 s for
  `semantic` against 4.9 s for `fixed_tok512`.
- **Overlap**
  ([`plans/results/stage1/RESULTS-stage1-legA.md`](plans/results/stage1/RESULTS-stage1-legA.md) §1):
  on the chunking study's Leg A, "Overlap 12.5% − 0% = **−0.0210**, CI
  [−0.047, +0.007] … the interval is consistent with overlap *hurting* by up to
  ~0.047, not with it helping." That run is marked **provisional** (n = 10
  topics; the leg has a known bias toward coarse configs). A later re-score at
  passage level
  ([`plans/results/rescore/RESULTS-rescore-small-corpora.md`](plans/results/rescore/RESULTS-rescore-small-corpora.md) §8)
  found that overlap does lower the chance of an answer straddling two chunks
  at 1024/2048 tokens, but it "does not translate into a resolved
  passage-*retrieval* gain."

In short, for retrieval in these tests, the method and the overlap mattered
less than the measurements could detect. What `fixed_token` reliably buys is
determinism and token safety.

## Token budget

An embedder silently truncates anything longer than its context window, so
chunks have to fit inside it.

- **`fixed_token`** is capped by its own window: no chunk exceeds *size*
  tokens.
- **Every other method, on every path**, is capped at the model window minus
  a 16-token reserve (`ingestion/tokenization.py` `resolve_max_tokens`,
  `DEFAULT_TOKEN_RESERVE`); for SFR-Embedding-Mistral that is 4,080 tokens.
  Since [#647](https://github.com/wilke/ragstack/issues/647) there is one
  place that decides the counter and the budget, `chunker_config.py`
  `resolve_chunk_sizing`, and the GoWe/bulk tools, the in-process
  per-collection path (`api/deps.py` `_chunker_for`) and the in-process default
  collection (`_build_chunker`) all build through it. The window is
  `CHUNK_MAX_TOKENS` (`--chunk-max-tokens` on the CLIs) when set, otherwise the
  `max_model_len` the collection's own embedding endpoint reports at
  `GET /v1/models`; tokens are counted with the collection model's HF tokenizer
  unless `CHUNK_TOKEN_COUNTER` says otherwise. The API **refuses** an ingest
  (503, naming the collection) when neither gives a window, rather than assuming
  one; the bulk tools still fall back to 4,096. The default collection resolves
  its budget on the first ingest, not at boot, so a deployment that sets no
  chunk options still starts without loading a tokenizer or calling the
  endpoint. Before #647 the in-process path passed no budget at all, so a long
  semantic chunk there was truncated by the embedder instead of being split (in
  the corpus run, semantic chunks reached 4,080 tokens on GoWe: p50 611, p90
  2,125).
- **`sentence` and `words` with a token budget**
  ([#318](https://github.com/wilke/ragstack/issues/318) P1): *size* is always
  the size, and the budget is always a cap.
  - Under the default `size_unit="chars"`, chunks are *size* characters. A
    chunk over the budget is re-packed at sentence or word boundaries.
  - Under `size_unit="tokens"`, chunks are *size* tokens. The budget is
    min(*size*, model window − 16), and that number is used both for packing and
    for splitting a single over-long sentence or word.
  - Before #318, a token budget **replaced** *size*. On GoWe and the bulk CLIs,
    which always resolve a budget, `sentence`/`words` therefore produced chunks
    close to the model window (about 4,080 tokens for SFR), not *size*
    characters. That behaviour is removed, not kept. A registry check on
    2026-10-23 found no production `sentence`/`words` collection, and the
    chunking study is pinned to its commit.
  - See [deep-dive §2.1](ARCHITECTURE-DEEP-DIVE.md#21-chunker-construction-the-five-builders-and-what-615-consolidated)
    for how the builders used to diverge.
  - The 7-way eval harness, `scripts/eval/chunking_compare_7way.py`, now
    builds its `sentence`/`words` arms with `size_unit="tokens"` (its old
    spelling relied on the removed behaviour). It reproduces the committed
    grids, including the `budget_mode="summed"` arm.

  Where `size_unit` comes from depends on the tool:
  - **`ingest_shard.py`** (the GoWe worker, which writes into a registry
    collection) reads it from the collection's `chunk.params` as
    `"size_unit": "tokens"`, and has no flag, so it cannot contradict the
    collection. That makes the unit part of the collection's
    [identity](#a-collection-keeps-its-method-for-life): `chunk_params` is in
    the chunk descriptor and the `spec_hash`. It is read only for
    `sentence`/`words`. A bad value fails the shard with a message naming the
    key. If no unit is recorded, the unit is `chars`.
  - **`ingest_jsonl.py`** takes `--chunk-size-unit tokens`. When it writes into
    a registry collection, a flag that contradicts the unit the entry records
    is refused with exit 2. An entry that records none counts as `chars`.
  - **`embed_shard.py`** takes `--chunk-size-unit tokens`. It writes an
    embedding file, not a collection.

  - **The API** (#647) accepts `"size_unit"` in `chunk.params` at create, only
    for `sentence`/`words` and only `chars` or `tokens`; anything else is a
    `400`. `"chars"` is dropped before the spec is stored, so it
    content-addresses to the same collection as no unit at all. In-process
    ingest (`_chunker_for`) reads it from the entry, the same way the shard
    tool does.

  The GoWe workflows do not pass a unit yet.

## Recommendations

- **Use the default, `fixed_token` 512/64.** It is deterministic and cannot
  overflow the embedder. No committed comparison beats it with statistical
  support after Holm correction, though `fixed_tok256` is nominally ahead on
  both the 7-way and SciFact runs, and on the provisional Leg A grid
  `fixed_tok512` ranked 21 of 24, inside the noise floor.
- **Semantic chunking might be worth it** when your documents change topic
  clearly and you want chunks that follow those changes, and you can afford the
    ingest cost. Measured on 20 papers (one corpus, default params): boundary
  detection embedded 2.41 M tokens for `semantic` and 0.36 M for
  `semantic_pooled` (6.78×), on top of about 0.35 M tokens of chunk embedding.
  `fixed` 512/64 embedded 0.40 M tokens in total, so `semantic` cost about 7×
  the embedding tokens of `fixed`, and `semantic_pooled` about 1.8×. Chunks are larger and vary more (p50 611 tokens,
  up to the 4,080 cap, against 179 for `fixed` 512 characters on the same
  papers). On a GoWe tenant you also need the method off
  `INGEST_WORKER_UNSUPPORTED_METHODS` and a workflow that admits it. Choose
  between `semantic` and `semantic_pooled` knowingly, because they produce
  different chunks.
- **Decide before you ingest.** You cannot change the method in place. To try
  another method, create a second collection, ingest the same documents, and
  compare them.

## Not built yet: section-aware chunking

Every method above cuts through flat text and ignores the document's own
sections, so a chunk can start in the Methods section and end in the Results.
A structure-preserving ingest, with one contract and per-format extractors
(PDF, HTML, JATS), is planned in
[`plans/structured-ingest.md`](plans/structured-ingest.md) (status: plan, not
started). The section classes such a chunker would use, along with the study
data that shapes them, are proposed in
[`plans/section-taxonomy.md`](plans/section-taxonomy.md). Neither is a method
you can choose today, and `CHUNK_SECTION_AWARE` from
[`plan-c5.md`](plan-c5.md) was never built.

The first piece exists for JATS: `ingestion/jats.py::article_spans` returns
the article record's text together with each section's and paragraph's
character span in it. Untitled sections and paragraphs before the first
section get their own spans. Nothing consumes these spans yet, and the stored
records are unchanged.
