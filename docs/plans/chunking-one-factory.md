# Semantic chunking on the GoWe path, and one module that builds chunkers

**Status:** plan, agreed with the owner 2026-09-18; **partly landed** — see below.
**Citations:** paths are package-relative (`api/deps.py` = `python/ragstack/api/deps.py`,
`scripts/…` = `python/scripts/…`); the **symbol** is authoritative and the line
number is a convenience re-verified against `main` `49ffb07` on 2026-09-22 — main
moved 60 lines under one of these in four days, so re-derive from the symbol.

**Landed** (2026-09-18): step 1's guard, #610 → v1.6.3. Steps 2–3, #615
(`a2be96f`) → v1.6.4: `ingest_shard.py` builds the breakpoint bridge and reads
`chunk_params` from the registry entry; the guard is the per-tenant setting
`INGEST_WORKER_UNSUPPORTED_METHODS` (§5, done); `SEMANTIC_METHODS` /
`needs_embed_fn` replace the hand-written membership tests (§7d, done). hackathon
runs v1.6.4 with the setting **empty** (both semantic methods admitted) since
14:03 UTC; dev runs on its own `ragstack-dev` worker group. A first comparison
ran (§7c) — on a synthetic 3-document shard, not yet on the owner's corpus.
**Still open:** the corpus-scale comparison (§7c), the consolidation (§8), the
CWL enum (#613), image versioning (#614).
**Goal:** *enable semantic chunking via the API/GoWe route.* The guard shipped in
[#609](https://github.com/wilke/ragstack/issues/609) step 1 only refuses it.

Two pieces of work, deliberately separated because only the first is on the
critical path:

* **§1–§5 — the deliverable.** `ingest_shard.py` learns semantic. Small, and it
  is the whole of what hackathon and dev need.
* **§8 — the consolidation.** Five chunker builders become one factory. Worth
  doing, no deadline, and it touches code that *does not execute* on the tenants
  asking for semantic.

---

## 1. Why the scope is one file

On `INGEST_BACKEND=gowe` the API **never chunks**. Both ingest routes return from
the GoWe branch before the in-process path is reached:

| route | GoWe branch | in-process path |
|---|---|---|
| `POST /v1/ingest` | `documents.py:1037` — the gowe branch's `return IngestResponse(` | `_resolve_ingest_target` at `:1060` — unreachable |
| `POST /v1/ingest/upload` | `documents.py:1477` — the gowe branch's `return IngestResponse(` | `_resolve_ingest_target` at `:1490` — unreachable |

`_chunker_for` is reached only via `build_ingestor_for`, only via
`_resolve_ingest_target`. So on hackathon and dev every ingest is
`pdf-ingest-scatter.cwl` → `ingest_shard.py`, and nothing else.

**Enabling semantic for those tenants means `ingest_shard.py` and nothing else.**

## 2. Update the tool, not a new workflow

`_gowe_inputs` already sends `embedding_url` (`documents.py:681`, the
`inputs["embedding_url"] = …` line) — the tool holds
the collection's embedding endpoints because it embeds chunks with them. The
breakpoint bridge reuses exactly those.

So semantic needs **no new workflow input and no DAG change**. A separate
"semantic workflow" would be a byte-identical copy of the scatter, differing only
in which branch the tool takes internally — duplication with nothing to show for
it. The difference is inside the tool, so the change belongs inside the tool.

## 3. What changes in `ingest_shard.py`

1. **Build the bridge** from the same `--embedding-*` arguments the shard embeds
   with — the entry's endpoints, so boundaries are detected with the same model
   that stores the vectors.
2. **Construct it in `amain`**, not in `_build_pipeline`: the method must be known
   before the pipeline, and closing has to outlive `run_shard`.
3. **Close it in a `finally`**, and wrap the close in its own `try` —
   `SyncEmbedBridge.close()` can raise `RuntimeError` when `thread.join(5.0)`
   times out against a wedged endpoint, which would turn a correctly written
   `FAILED` receipt into a traceback. The thread is `daemon=True`, so an unclosed
   bridge cannot hang exit.
4. **Build it lazily, after the split-brain check**, so a refused config never
   spins a thread.
5. **Read `chunk_params` from the registry entry** — see §4.
6. Drop the semantic refusal (§5).

## 4. `chunk_params` is the gap nobody has noticed

`_gowe_inputs` sends `chunk_method`, `chunk_size`, `chunk_overlap` — and **not**
`chunk_params`. So `buffer_size`, `breakpoint_percentile_threshold` and
`min_chunk_length` fall back to the tool's own defaults.

Measured 2026-09-18: both semantic collections carry `chunk_params={}`, and the
tool's defaults match the API's exactly (3 / 80.0 / 500), so nothing diverges
today. But a user creating a semantic collection with custom params would
silently not get them, and semantic is the first method where those params matter.

**The tool reads `chunk_params` from `target.spec`.** Never a CWL input: a
submission's `submitted_inputs` is an immutable plaintext snapshot, and a second
source for identity is how #609 happened in the first place.

`chunk_method`/`size`/`overlap` keep coming from the CWL inputs, checked against
the entry by `IngestTarget.check_build` as today. That already implements the rule
we want ("unset either side is fine; both set and different is fatal"), and it is
the only end-to-end assertion that the API and the registry agree.

> If the tool ever stops receiving those flags, `--chunk-method` must become
> `default=None` first. Its argparse default is `fixed_token`, which always looks
> "set", so a semantic entry would exit 2 on
> `registry='semantic' ingest='fixed_token'`.

## 5. The guard becomes a per-tenant setting

`SHARD_UNSUPPORTED_METHODS` (#610) is the wrong lever to empty: it also gates the
**tool's own** refusal, so a rolled image would still refuse and the roll could
never be verified before flipping — and it assumes one fleet when there are two
image dirs.

* the tool refuses nothing it can do;
* `embed_shard.py` keeps a **local** refusal — it builds no bridge, and without
  one, emptying the shared constant turns a clear message into a `make_chunker`
  crash;
* `Settings.ingest_worker_unsupported_methods` (default `semantic,semantic_pooled`)
  backs the two API guards, flippable per tenant through ragstack-ctl and
  reversible without a release.

## 6. `chunk_method` becomes a CWL enum — per workflow

Today `chunk_method: {type: string}` accepts anything and the error surfaces
inside a worker mid-scatter, which is exactly how #609 presented. An enum rejects
it at submission.

The symbol list must differ **per workflow**, because the tool differs:

| workflow | tool | may list `semantic*` |
|---|---|---|
| `pdf-ingest-scatter.cwl` | `ingest_shard.py` | yes, after this work |
| `ingest-bulk.cwl` | `ingest_shard.py` / `ingest_jsonl.py` | yes |
| `embed-bulk.cwl`, `pdf-ingest.cwl`, `jats-ingest.cwl` | `embed_shard.py` | **no** |

**The enum does not replace §5.** The CWL is registered by the API from its own
checkout per submission, so the enum ships with the *API release* while the worker
image rolls separately. An enum admitting `semantic` against an old image still
fails inside the worker.

## 7. Deploying it

```
merge → tag → GoWe session builds the tool image from the tag → stage into each
--image-dir at a batch boundary → verify inside the image → flip dev via ctl →
semantic end-to-end on dev → load check → flip hackathon (API restart AND static
UI rebuild) → conformance
```

**dev runs on its own `ragstack-dev` worker group** (`GOWE_WORKER_GROUP=ragstack-dev`,
moved 2026-09-18 for exactly this work, with its own `--image-dir` and the tool
image behind a versioned symlink — #614). So a dev roll touches nothing else. The
original concern — that dev shared the `ragstack` group with the four OA-plane
workers, so a dev roll was an OA roll — is resolved for dev but **still holds for
any future roll of the shared `ragstack` group itself**: agree the tag with the
GoWe session explicitly. Image rolls and `--image-dir` staging are theirs; the
ctl restarts are the management session's.

**On hackathon an API restart alone is not enough**: the 422-detail rendering
landed in the same PR as the guard and the deployed bundle predates it.

**Load.** Semantic embeds sentence buffers *during* chunking. `max_inflight`
bounds one document; the scatter width is the fleet-wide bound. Both waiting
collections are legacy `semantic`, which embeds every buffer of
`2*buffer_size+1` sentences — roughly 7× the document's tokens — where
`semantic_pooled` embeds each sentence once. Chunk method is collection identity,
so `Salmonella_AMR2` cannot be switched without recreating it: whatever the dev
check measures for `semantic` is what that user gets.

**Acceptance (#609):** on dev, create a semantic collection → upload → job
`completed` → chunk count > 0 in **both** stores, metadata as the API path
produces.

## 7b. The two semantic methods, and why naming is POSTPONED

They are **not** synonyms. One boolean in `make_chunker` separates them:

| | `semantic` | `semantic_pooled` |
|---|---|---|
| embedded input | each overlapping **buffer text** (`2*buffer_size+1` sentences, re-embedded per position) | each **sentence once**, mean-pooled over the same window |
| embed tokens | ~7x the document at `buffer_size=3` | ~1x |
| `distance_round` | `None` (raw cosine) | `6` decimals before the percentile |

Everything downstream is the same code. Whether they produce the same
*boundaries* is unknown: the vectors necessarily differ (a transformer is not
linear in its input, and cross-sentence attention is what pooling discards), but
the breakpoint rule consumes only the **percentile rank** of consecutive
distances, so equal boundaries need rank-order preservation, not equal vectors.
Plausible; unproven. **No test compares them** — `test_chunkers_semantic.py`
asserts pooled's own invariants only.

### Decision (owner, 2026-09-18): keep both, change no names yet

* Both methods stay supported. The names stay `semantic` and `semantic_pooled`.
* `semantic_pooled` becomes the **recommended default for new collections** —
  a settings/CWL-default/docs change, which costs nothing in code.
* The rename to `semantic_window` / `semantic_pooled` is **deferred** to keep
  this change small. It is not abandoned; §7d records the path.

### Why the names are wrong, for whoever picks this up

`semantic` reads as canonical when it is the expensive legacy path, and
`semantic_pooled` reads as a variant when both are equally semantic. What
actually differs is *how the boundary signal is embedded* — which is what the
names should say.

## 7d. The path to renaming later

Recorded now so the option stays cheap. The rename is an **alias**, never a
repoint:

* `semantic_window` becomes the preferred spelling; `semantic` stays accepted
  **permanently** as an alias for it; `semantic` is never repointed at pooled.
* Why permanent: `asm-semantic` on hackathon holds **6,718,269 points in both
  stores**, recorded as `semantic`, in hackathon's own (unrouted) Qdrant.
  Repointing the keyword would leave a populated collection whose spec names an
  algorithm that did not produce its chunks (the ADR-0002 failure); dropping the
  keyword would make that collection permanently impossible to ingest into.
  Reads and restore are unaffected either way — neither builds a chunker.

**Three exact-string comparisons would have to learn the alias:**

1. `make_chunker` — the dispatch itself.
2. `IngestTarget.check_build` (`ops/ingest_target.py:128`) does
   `cmp("chunk_method", spec.chunk_method, chunk_method)` on raw strings, so a
   `semantic` registry row against a `semantic_window` CLI value would **exit 2**.
3. Every `method in ("semantic", "semantic_pooled")` membership test.

**And one place it must NOT go:** `chunk_descriptor` feeds `collection_name`
(the content address) and `spec_hash`, and `spec_hash` is stamped on Workspace
folders and archive manifests. Canonicalising it would move `asm-semantic`'s hash
under 6.7M existing points. Leave it; the cost is that a new *unnamed* corpus
created under each spelling content-addresses to two stores despite being one
algorithm — marginal, since named libraries fold their id in anyway.

**What makes this cheap later — DONE in #615:** a single `SEMANTIC_METHODS`
constant (`ingestion/chunker_config.py:58`) with `needs_embed_fn()` as the one
membership test. The five hand-written copies are gone: `api/deps.py`
`_embed_fn_for` (`:485`) and `_chunker_for` call `needs_embed_fn`,
`routers/collections.py:320` re-exports the constant, `ingest_jsonl.py:509` and
`:1124` call `needs_embed_fn`, and `SHARD_UNSUPPORTED_METHODS` is derived from it.
The one deliberate holdout is `chunkers.py`'s own dispatch tuple, left literal
because that file is frozen for the study (§5).
With one constant, adding a third spelling is a one-line change; without it, the
rename is a five-site hunt in which a missed site silently drops a method to a
different chunker. This is already part of the consolidation (§8) — it is pulled
forward not as extra scope but because it is the thing that keeps the deferred
decision reversible.

## 7c. The comparison run

**A first comparison has run — on a synthetic 3-document shard, not the corpus.**
Result and committed artifacts: `docs/plans/results/semantic-vs-pooled-2026-09-18.md`.
Headline: the two methods share **no** boundaries on the documents that split
(rank correlation 0.4254, span Jaccard 0.111) while each is reproducible against
itself (0.9984 / 0.9993), so the difference is algorithmic. **The corpus-scale
run is still pending:** the owner will run Clark's workflow (the `Salmonella_AMR2`
corpus, 20 PDFs) under **both** methods; `Salmonella_AMR2` still holds 0 points.

Chunk method is collection identity, so this needs **two collections** over the
same documents — which is the correct A/B anyway. `Salmonella_AMR2` is declared
`semantic` (→ `semantic_window`) and is currently empty (0 points in both stores,
`archive_version=1` from the failed job); the pooled arm needs a sibling
collection.

What to compare, cheapest-first:

1. **Spearman correlation of the two distance series** — this tests the actual
   mechanism, since the percentile rule consumes only rank order.
2. **Jaccard over chunk span sets** — did the boundaries move.
3. **Chunk-count distribution** and realised embed tokens per arm — the 7× claim,
   measured rather than derived.

The `semantic_window` arm is also the load check for #609 step 4: it is the
expensive path, and it is what `Salmonella_AMR2` will actually run.

## 8. The consolidation (follow-up, no deadline)

Not "two chunker builders" as #609 says. **Five**, with three answers to one
question:

| builder | token budget |
|---|---|
| `api/deps.py:402` `_chunker_for` (per collection) | none, ever |
| `api/deps.py:1208` `_build_chunker` (app default) | opt-in via `settings.chunk_max_tokens` |
| `ingestion/chunker_config.py:176` `build_chunker` | always — live `GET /v1/models` |
| `scripts/ingest_shard.py` `_build_chunker` | inherits the above |
| `scripts/embed_shard.py` `_build_chunker` | inherits the above |

plus `ingest_jsonl.py` inline, and three sites bypassing the dispatch
(`eval/chunking_compare.py`, `chunking_compare_7way.py`, the study).

**`scripts/load_embeddings.py` is not one of them.** It reads embedding files
`embed_shard.py` produced and calls only `IngestionPipeline.index_chunks`; its
`RecursiveCharacterChunker()` is a constructor argument the pipeline requires and
never invokes, like the `_NoEmbed()` beside it (the file says so at line 76). It
loads chunks; it does not make them.

### The shape

```
ragstack/ingestion/chunkers.py        ALGORITHMS — frozen, see below
ragstack/ingestion/chunker_config.py  the only place a spec becomes a chunker
```

```python
def chunker_for(
    method: str | None, *,
    size: int | None, overlap: int | None,
    params: Mapping[str, Any] | None,
    model: str | None,                  # fixed_token's tokenizer; ignored otherwise
    defaults: ChunkDefaults,
    embed_fn: EmbedFn | None = None,    # required iff method is semantic*
    budget: TokenBudget | None = None,  # None = char budget (today's API semantics)
) -> Chunker
```

`budget=None` by default is what makes adoption a no-op for the API. The bulk
plane opts in.

**No adapter layer.** `CollectionSpec` (durable registry row) and
`CollectionEntry` (runtime object = spec + bound stores + flags) agree on all four
chunk field names and differ in exactly one field this factory cares about:
`entry.model` vs `spec.embedding_model`. Two named adapters to bridge one renamed
attribute is two things that can drift — the disease being cured. Callers pass raw
values; defaulting happens once, inside the factory.

> `text_index` means different things on the two types — a *string* on the spec,
> the *store object* on the entry (name in `text_index_name`). Nothing in the
> chunking path touches it; anything treating the two as interchangeable will.

### Deliberate exceptions

**The study stays pinned.** `docs/plans/results/stage0/` constructs
`FixedTokenWindowChunker` directly, pins the repo (`s0_common.py:58`
`EXPECT_COMMIT`, `pin_repo()` raises on drift) and `git diff`s `chunkers.py`
against that commit. That is what an experiment is for. **So this work must not
change `chunkers.py` semantics** — and it does not. Study arms are `fixed_token`
at tok256/512/1024/2048 plus neighbour variants; semantic was deferred by owner
decision 2026-09-06.

**`eval/chunking_compare*.py` stay independent** — building several chunkers side
by side is their job.

### `sentence` and `words` are boundary guards

`sentence_spans()` is the segmentation primitive for `SemanticChunker.chunk`
(`chunkers.py:1225`) and the study's labeler; `word_spans()` has exactly one
caller, `WordChunker`. Both remain reachable from `POST /v1/collections`, but no
collection uses them and they are not study arms.

That matters because the builders disagree for exactly these two and agree
everywhere else:

| method | API builder | `build_chunker` |
|---|---|---|
| `fixed` | 25 | 25 |
| `fixed_token` | identical (`max_tokens` not threaded) | identical |
| `semantic` | identical (edge cases differ) | identical |
| `sentence` | **30** | **1** |
| `words` | **25** | **1** |

*(4,489-char document, size 200 / overlap 20, estimating counter, budget 4080.)*
`_pack_spans` drops `chunk_size` once a budget is present and packs to the model
window. Real — and the reason the shared core defaults to the API's semantics
rather than the bulk plane's.

### Measured blast radius

Registries read read-only, 2026-09-18:

| tenant | collections | `sentence`/`words` | method-less | `semantic` |
|---|---|---|---|---|
| hackathon | 10 — 7 `fixed`, 1 `fixed_token`, 2 `semantic` | **0** | **0** | 2 |
| dev | 1 `fixed_token` | **0** | **0** | 0 |

Zero live blast radius, and the method-less ambiguity does not arise. No
operational migration. The identity test is a regression net, not a gate.

### How the consolidation proves itself

**Identity.** Freeze today's `_chunker_for` body; assert per method that
`[(start_char, end_char, id)]` over a fixture document is identical to the new
one.

**Equality.** Same document, same spec, tool vs API → identical chunk ids, over
all six methods. Ids are `uuid5(doc_id:start:end)`, so id equality *is* span
equality.

The semantic fake embedder must be **topic-sensitive** (vector derived from
content, as in `test_chunkers_semantic.py`'s `_topic_embed_fn`). A constant-vector
fake makes every cosine distance 0, no breakpoint fires, and the test passes
without exercising what it names.

*Caveat:* on a live fleet, legacy `semantic` has no distance rounding, so
API-vs-tool equality on real vectors is not byte-guaranteed (GPU batch
nondeterminism); `semantic_pooled` rounds to 6 decimals for this reason. The
contract is over chunker *construction*, proven with a deterministic embedder.
