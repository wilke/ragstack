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
| `semantic` | tokens of the collection's embedding model: *size* is a **cap** on every chunk for a collection that records `size_cap` (every semantic collection created from this release on). In older collections *size* is not a cap. Overlap is not used ([size cap](#the-semantic-size-cap)) | at topic shifts that embeddings detect; a block over the cap is split by `cap_split` | yes, about 7× the tokens of `semantic_pooled` | `pdf-ingest-scatter.cwl`, `ingest-bulk.cwl` only |
| `semantic_pooled` | as `semantic` | as `semantic`, but in different places | yes | `pdf-ingest-scatter.cwl`, `ingest-bulk.cwl` only |

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
  In a collection that records the size-cap marker, *size* caps every chunk
  in tokens, and by default it also bounds the breakpoint-embedding inputs. See
  [the semantic size cap](#the-semantic-size-cap). In a collection without the
  marker (every semantic collection created before it existed), *size* and
  *overlap* matter only for a document that splits into more than 3,000
  sentence spans (`max_breakpoint_sentences`). On GoWe that document is
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

Three more keys, `size_cap`, `cap_split` and `breakpoint_cap`, control the
semantic size cap. They are described in the next section. Two opt-in keys,
`size_tolerance` and `min_piece`, turn the cap into a target with a tolerance
([below](#opt-in-size-tolerance-and-small-piece-merge)).

## The semantic size cap

Before this change, `semantic` and `semantic_pooled` ignored *size*. The only
cap was the model window minus 16 tokens (4,080 for SFR-Embedding-Mistral), so a
collection created as "semantic, size 512" held chunks of up to 3,398 tokens.
The confirmatory study
([`chunk-size-deployed-docs-part4-2026-10-25/RESULTS.md`](plans/results/chunk-size-deployed-docs-part4-2026-10-25/RESULTS.md))
found semantic chunking capped at 512 tokens no worse than uncapped, and better
at small context budgets. That result is provisional until its grader
validation is in.

The cap is set by three keys in the collection's `chunk.params`:

| key | values | default when the API records it | what it does |
|---|---|---|---|
| `size_cap` | `"tokens"` | `"tokens"` on every new semantic collection | Marks *size* as an upper bound on every chunk, in tokens. Absent means an older, uncapped collection. |
| `cap_split` | `"greedy"`, `"balanced"`, `"topic"` | `"greedy"` | How a block over the cap is split ([below](#splitting-a-block-over-the-cap)). |
| `breakpoint_cap` | `"size"`, `"window"` | `"size"` | How the breakpoint-embedding inputs are bounded ([below](#bounding-the-breakpoint-inputs)). |

### The cap

For a collection that records `"size_cap": "tokens"`, *size* is an upper bound
on every semantic chunk. It is counted in tokens of the collection embedding
model's tokenizer, the same counter the window cap uses.

*size* may not exceed the model's per-chunk budget (window − 16). A larger
*size* is **refused**, never clamped. The `ValueError` names both numbers. It
is a 400 at ingest, and also at create when `CHUNK_MAX_TOKENS` names the window
without a network call.

**The default size.** A semantic collection created **without a size** gets
the deployment's `CHUNK_SIZE` (`Settings.chunk_size`, 512 unless `tenant.env`
sets it), recorded as a number together with the marker. This includes every
semantic collection created from the UI, which sends no size unless the user sets one, and a create that
takes a semantic server default.

When `CHUNK_MAX_TOKENS` names the window, a default above the per-chunk budget
is **clamped** to that budget. The clamped number is recorded and an INFO line
is logged. For example, `CHUNK_MAX_TOKENS=512` with the default `CHUNK_SIZE=512`
records 496. Only the deployment default is clamped: a size the caller sends is
refused above the budget, never clamped. When the window comes from the
embedding endpoint instead, it is not known at create. An over-budget default
is then refused at the first ingest, like any other size.

`CHUNK_SIZE` is the size of the deployment's default method. That method is
`fixed_token` (tokens) unless `CHUNK_METHOD` says otherwise, and tokens too when
`CHUNK_METHOD` is a semantic method. When `CHUNK_METHOD` is `fixed`,
`sentence` or `words`, `CHUNK_SIZE` counts characters. A semantic create
without a size is then refused with a 400 asking for an explicit size in
tokens, rather than reading characters as tokens.

*overlap* is not applied by semantic chunking. Only the oversized-document
fallback uses it. A create with a non-zero overlap below the recorded size logs
a WARNING and is not refused, because existing clients send 64. An overlap at or
above the recorded size (the explicit size, or the clamped default when the size
is omitted) is refused with a 400, as for every other method, because the
recorded spec would otherwise state an overlap the chunker never applies.

### Splitting a block over the cap

`cap_split` decides how a semantic block (the sentences between two
breakpoints) that is over the cap is split.

| `cap_split` | what it does | when to choose it |
|---|---|---|
| `greedy` (default) | Packs the block's sentences greedily into pieces of at most *size* tokens. This is what the study's capped arm ran. | When you want to match the tested configuration. It can leave a small tail: a 520-token block at cap 512 becomes 512 + 8. |
| `balanced` | Cuts the block into k = ⌈tokens / *size*⌉ pieces of roughly equal size at sentence boundaries. If k pieces cannot all fit, it tries k + 1, and so on. | When small tail pieces are a problem. |
| `topic` | Cuts the block at its strongest internal breakpoint, the largest cosine distance between neighbouring sentence buffers inside it. It repeats on each piece until every piece fits. It reuses the distances breakpoint detection already computed, so it embeds nothing extra. | When cuts should follow the weaker topic shifts inside a block. It can still leave a small piece next to a strong shift. |

Under every strategy:
- a cut falls inside a sentence only when that one sentence alone is longer
  than *size*;
- the pieces tile the block exactly;
- pieces are not merged afterwards (`min_chunk_length` is applied before the
  cap), unless the collection opts in to the
  [size tolerance](#opt-in-size-tolerance-and-small-piece-merge).

`greedy` stays the default until an evaluation chooses between the three.
Only a block over the cap is split, so a block that fits is one chunk under
all three.

### Bounding the breakpoint inputs

Breakpoint detection embeds a buffer of up to 2 × `buffer_size` + 1 sentences
around each sentence. Those buffers are bounded so they fit an embedding model.

- **`breakpoint_cap: "size"`** (the default) bounds each buffer at *size*
  tokens; only its first *size* tokens are embedded. This is exactly what the
  study's capped arm ran: `make_chunker("semantic", max_tokens=512)` bounded
  both the breakpoint inputs and the chunks with one number. So a rebuild with
  *size* 512 reproduces that arm, which a test asserts span for span. The cost
  is that a buffer longer than *size* is compared on its beginning only.
- **`breakpoint_cap: "window"`** bounds buffers only at the model window (or
  at a smaller breakpoint model's window). The breakpoints are then those of an
  uncapped collection, and only the emitted chunks are capped.

### Who sets it

- **The API** (`POST /v1/collections`) writes all three keys into the
  `chunk_params` of every new `semantic`/`semantic_pooled` collection, with or
  without a size and including a create that takes the server default. A key
  the request names is kept. Every value is stored explicitly, so a later
  change of default cannot re-chunk the collection.
  - A request that sends any of these keys on another method, or an unknown
    value, gets a `400`.
- **`ingest_shard.py`** (the GoWe worker) reads the keys from the registry
  entry, as it reads `buffer_size`. It has no flag for them, and no CWL input
  changed.
- **`ingest_jsonl.py`** reads them from the registry entry when it writes into
  one. It refuses a contradicting `--semantic-size-cap`, `--semantic-cap-split`,
  `--semantic-breakpoint-cap`, `--semantic-size-tolerance` or
  `--semantic-min-piece` with exit 2. Without an entry, the flags
  decide; an unset split or breakpoint flag takes the API's defaults.

Every path reads the keys through one function,
`chunker_config.semantic_cap_from_params`, and builds through `build_chunker`.
So one recorded spec gives the same chunks in-process and on GoWe. Within a
recorded cap, an absent `cap_split` reads as `greedy` and an absent
`breakpoint_cap` as `size`. Without `size_cap`, either key is refused.

### Existing collections are not changed

Every semantic collection created before the marker records a *size* (often
512, a default that meant nothing), and its stored chunks were made with only
the window as a cap. Reading that *size* as a cap now would make new uploads
chunk differently from the stored chunks. That is the #647 defect class: one
collection, two chunkings.

So **only a collection whose `chunk_params` record `size_cap` is capped**.
Without it, chunking is exactly what it was, including a recorded *size* larger
than the window, which is neither refused nor applied. To cap an existing
collection, create a new one and ingest again
([identity](#a-collection-keeps-its-method-for-life)).

### Release order: capped collections need new workers

**A capped collection must be ingested by workers built from this release or
later.** On `INGEST_BACKEND=gowe` the chunking runs in the worker image
(`ingest_shard.py`), not in the API. An older image does not know the keys and
ignores them, so it would chunk a capped collection **uncapped**, with no error.
That leaves one collection holding chunks made two ways.

Nothing refuses this at runtime. Roll the tools image and the workflows before
creating capped semantic collections on a GoWe tenant. The new image prints a
`[chunker] … size_cap=…` line on stderr and writes a `chunking` object into
every semantic shard receipt (below). A semantic receipt without that object
came from an older image.

### Where it is recorded

| where | what |
|---|---|
| the collection spec (`chunk_size`, `chunk_params`) | the cap, `size_cap`, `cap_split` and `breakpoint_cap`, as recorded at create. `chunk_params` is free-form in the contract, so no schema changed. It is part of the chunk descriptor, the `spec_hash` and the physical name, and it is shown by `GET /v1/collections`. |
| the collection manifest | the same `chunk_size` and `chunk_params`, written by every ingest path. Because a cap above the window is refused rather than clamped, the cap that is applied always equals the recorded *size*. |
| the shard receipt (GoWe) | `chunking`: what the worker actually applied. Its fields are `v` (2), `method`, `size_cap`, `chunk_size`, `cap_split`, `breakpoint_cap`, `window_budget` (the resolved `max_tokens`), `breakpoint_input_budget`, and since v2 `size_tolerance`, `min_piece` and `ceiling` (the applied ceiling in tokens, already clamped to the window; all three `null` on a strict or uncapped collection). On an uncapped collection, `size_cap` is `null` there. A v1 object has no tolerance fields and was written by a strict worker. The object is written only for the semantic methods. A receipt without it predates it, or came from another method. |

## Opt-in: size tolerance and small-piece merge

Issue #831. The strict cap above is what the study tested, and it stays the
default. A collection can instead opt in to treating *size* as a **target**
with a tolerance, and to merging small pieces. This becomes the default only if
the retrieval evaluation planned in #831 supports it.

Why: a small piece (a heading line, or a one-sentence lead-in cut off at a
strong topic edge) is a poor retrieval unit and takes one of a fixed top-k's
slots. The study's losses came from much larger chunks (1,024 tokens, and
uncapped chunks of up to 3,398), not from a tolerance just above the target.

| key | values | default when on | what it does |
|---|---|---|---|
| `size_tolerance` | a number from 0 to 1 | 0.25 | Turns the setting on. The **ceiling** is ⌊*size* × (1 + `size_tolerance`)⌋ tokens, clamped to the window budget: never above it. |
| `min_piece` | whole tokens, 0 to *size* | ⌊*size* / 4⌋ | A piece under this many tokens is merged into a neighbour. 0 never merges. |

Both need the size-cap marker, so they are refused on the other methods (400 at
create) and on an uncapped entry.

**The rule.** At *size* 512 with tolerance 0.25 the ceiling is 640.

1. **Split only over the ceiling.** A block (the sentences between two
   breakpoints) at or under the ceiling is one chunk: a 600-token block stays
   whole. A block over it is split **aiming at the target**, by the block's
   `cap_split` with *size* as the cap, so a 700-token block becomes about
   2 × 350 under `balanced` (512 + 188 under `greedy`). Inside such a block,
   a sentence over the target is kept whole when it fits the ceiling and is
   cut only when it is over the ceiling; a run of sentences between such
   sentences is kept whole when it fits the ceiling.
2. **Merge small pieces.** After `min_chunk_length` (characters, applied before
   any of this) and after the split, every piece under `min_piece` joins the
   neighbour across the **weaker** boundary: the smaller breakpoint distance,
   from the distances breakpoint detection already computed, so no extra
   embedding call is made. A boundary inside a sentence (a hard split) counts
   as the weakest. The merge happens only when the joined piece is at most the
   ceiling; otherwise the piece joins the other neighbour if that fits;
   otherwise it stays as it is. A merged piece that is still small is examined
   again.
3. **Sections (seam, not yet active).** A piece must never merge across a
   document section boundary. That needs the document structure planned in
   #828, which does not exist yet; the merge takes a set of barrier offsets
   (`_merge_small_pieces(barriers=…)`) and is passed none today.

Every chunk is at most the ceiling, and so at most the window. The chunks tile
the document. A cut falls inside a sentence only when that sentence alone is
over the ceiling. A single-sentence document follows the same rule; the
oversized-document fallback (`fixed_token`) is unchanged.

**Tolerance 0 is its own setting, not a synonym for strict.** With
`size_tolerance` 0 (and `min_piece` 0) the ceiling equals *size*, and for
`greedy` and `balanced`, or any block with no sentence over *size*, the chunks
are the strict ones. Under `topic` with a sentence over *size*, they can
differ: the tolerant path isolates that sentence before the topic split, where
the strict path reaches it by recursion. For example, at *size* 20, sentences
of 6 | 6 | 60 tokens with a strong boundary after the first give
5 + 5 + 20 + 20 + 19 strict and 10 + 20 + 20 + 19 at tolerance 0. Treat it as
its own arm in an evaluation.

**Recording.** The API records both keys as concrete numbers when a request
sends either one (the other takes its default; a `null` counts as not sent).
`size_tolerance` is stored as a float, so `0` and `0.0` give the same
`spec_hash`. A create that sends neither records neither: **a collection
without `size_tolerance` is chunked exactly as under the strict cap**, which
includes every collection created before #831. The ceiling depends on the
window, which may be known only at ingest, so it is not part of the spec; the
shard receipt (`chunking.ceiling`) and the `[chunker] … ceiling=…` stderr line
of `ingest_shard.py` record the applied value. The in-process (non-GoWe)
ingest path writes no receipt, so there the applied ceiling is derivable
(*size*, `size_tolerance` and the model window) but not recorded. When
`CHUNK_MAX_TOKENS` names the window at create and the asked-for ceiling is
above the per-chunk budget, the create logs one INFO line saying the ceiling
will be clamped to the budget; the recorded tolerance is then larger than the
effective one. The keys are read by the same
`semantic_cap_from_params` as the cap, on the API, in `ingest_shard.py` and in
`ingest_jsonl.py` (`--semantic-size-tolerance` and `--semantic-min-piece`
without a registry entry).

**Release order.** As for the cap: a worker image built before #831 ignores
the two keys and chunks a tolerant collection strictly, with no error. Its
receipts carry `chunking.v` 1. Roll the image before creating tolerant
collections on a GoWe tenant.

**A semantic-default deployment** (`CHUNK_METHOD=semantic` plus `CHUNK_SIZE`)
is affected too. A create that omits `chunk` now records the marker, so its
content address differs from the uncapped, settings-derived default
collection. It mints a separate store instead of re-mapping to that surface.

## Sentence segmentation

`sentence`, `semantic` and `semantic_pooled` all start from
`chunkers.sentence_spans`, so a sentence boundary is a chunk boundary.

**What Punkt sees.** NLTK's `PunktSentenceTokenizer` is built in code from
`PunktParameters`, not loaded from NLTK's `punkt` data, so nothing is
downloaded. It has no trained statistics. The only thing it knows is
`abbrev_types`, which is set to `chunkers.SENTENCE_ABBREVIATIONS`. After a
period, Punkt then decides like this:

- after a listed abbreviation, it never breaks ("et al.", "Fig. 2", "i.e.",
  "Proc. Natl. Acad. Sci. U.S.A."), unless a paragraph break follows the
  period;
- after a single letter followed by a letter, it does not break. This is
  Punkt's own initial heuristic, and it is what handles genus abbreviations
  ("E. coli", "S. enterica") and personal initials;
- after a number followed by a lower-case word, it does not break ("arm 2.
  received");
- after any other period, it breaks.

Without `nltk`, the regex fallback applies the same rules. On the 20-passage
gold set (`python/tests/unit/fixtures/sentence_gold_biomedical.json`) both
backends score 20/20.

**The list.** There are about 130 lower-case entries without their final
period. Punkt matches one token at a time, so "et al." is matched by its
entry `al`. The groups are honorifics and degrees, Latin connectives,
pointers into the paper (`fig`, `tab`, `eq`, `ref`, `suppl`, `vol`, `pp`,
`no`), measures, dosing routes (`i.v`, `p.o`, `b.i.d`), taxonomy (`sp`,
`spp`, `subsp`), journal-title words, institutions and places, and months.
Matching ignores case, so an entry also matches the same letters in capitals.
For that reason `min`, `max`, `sec`, `ed`, `ms` and `am` are left out on
purpose: "5 min.", "VO2 max.", "the ED." and "MS." end sentences far more
often than they abbreviate. The reasoning for each group is in the comment
above the set.

**`SEGMENTER_VERSION`.** `chunkers.SEGMENTER_VERSION` names the segmenter.
It is `"punkt-abbrev-v1"` now. The previous segmenter, a bare
`PunktSentenceTokenizer()` with every period a candidate break, is
`"punkt-bare"`; that name was never recorded anywhere. Bump the version
whenever `sentence_spans` output can change for any input, together with
`REGEX_SEGMENTER_VERSION` (`"regex-abbrev-v1"`), the name of the regex
fallback that runs when `nltk` is not installed. What is *recorded* is
`chunkers.segmenter_version()`: the name of the backend this process actually
runs (#815 — every image before v1.6.15 lacked `nltk` and ran the fallback).
It is recorded under `segmenter_version` in the `provenance` object of every
shard receipt and archive manifest (`provenance.tool_provenance`), in every
`sentence_spans_fingerprint`, and in every segmentation-cache key. A record
without the key predates it and was built with `punkt-bare`. A process on the
fallback logs one WARNING the first time it segments.

**Migration rule.** A chunk id is `uuid5(doc_id:start:end)`, so a moved
sentence boundary changes the chunk id and the point id. When the segmenter
changes, every `sentence`, `semantic` and `semantic_pooled` collection needs
a **full re-ingest**, never a hot patch:

- Re-ingesting an unchanged document writes new points and leaves the old
  ones orphaned.
- A partial re-ingest leaves a collection that mixes chunks from two
  segmenters.
- The collection's `spec_hash` does not include the segmenter, so nothing
  refuses the mixed ingest.

Labels keyed by sentence span (stage0/stage1) come from a different
coordinate system ([section-taxonomy.md](plans/section-taxonomy.md)), and
they do not translate to the new spans. `fixed`, `fixed_token` and `words`
collections are unaffected.

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
- **A semantic collection with a recorded size cap** is capped at its *size*,
  which must be within the window budget below. By default its breakpoint
  inputs are bounded at *size* too ([size cap](#the-semantic-size-cap)).
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
  the embedding tokens of `fixed`, and `semantic_pooled` about 1.8×. Measured before the size cap existed, chunks were larger and varied more (p50 611 tokens,
  up to the 4,080 window cap, against 179 for `fixed` 512 characters on the same
  papers). On a GoWe tenant you also need the method off
  `INGEST_WORKER_UNSUPPORTED_METHODS` and a workflow that admits it. Choose
  between `semantic` and `semantic_pooled` knowingly, because they produce
  different chunks. A new semantic collection is capped at its *size* in
  tokens (512 by default); on GoWe, roll workers from this release first
  ([release order](#release-order-capped-collections-need-new-workers)).
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
