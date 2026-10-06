# 0011. Provenance records: one record format, one ambient API, written into the artifact it describes

Status: Proposed (2026-10-06; issues #683, #682 (step 0), PR #680; GoWe#49; the provenance design study and prior-art survey of 2026-10-06)

## Context

"Provenance" names three different things in `ragstack/` today, and the word is
the only thing they share:

1. **What a build observed.** The per-collection manifest in
   `python/ragstack/provenance.py` (`CollectionManifest`, `write_manifest`,
   `source: ingest | config`) — ADR-0002's "verified lineage, as opposed to the
   registry's operator-asserted labels", surfaced as `Provenance` on
   `GET /v1/collections` (`api/routers/collections.py`).
2. **Which workflow and image ran.** The six-key object ADR-0010 decision 8
   added (`PROVENANCE_KEYS`, `tool_provenance`, `read_provenance` in the same
   module): `workflow_id`, `tool_image`, `tool_image_digest`, `image_version`,
   `image_commit`, `image_build`, written into `ShardReceipt.provenance`, the
   archive `manifest.json`, `graph_extraction.provenance` and the load summary.
3. **What produced an answer or a triple.** The query response's "four
   provenance fields" (`api/routers/query.py`, ADR-0008) and the epistemic
   `derived_by` stamp on graph triples (`graph/extractor.py`, #347).

A fourth lives in `python/scripts/backfill_collection_metadata.py`, whose own
`PROVENANCE_KEYS` (`metadata_source`, `enriched_from`) is a different tuple
under the same name as the one in `ragstack.provenance`. None of the four says
*what was used to make what*.

Meanwhile "which code ran" — the one question every experiment and every
artifact must answer — is answered by **five independent implementations that
each run `git` themselves** (#683, measured on `main` 2026-10-06):
`docs/plans/results/stage0/s0_common.py`, `s0b_common.py`, `s0_pointed_gen.py`,
`python/scripts/eval/_g1_rating.py` and `g1_library_sweep.py`, plus
`tool_provenance()` for the image side. That is 18 `git` call sites, four
spellings of the commit (`commit`, `repo_head`/`worktree_head`,
`code_commit`/`worktree_commit`, `image_commit`), three of "is it dirty", and
none of them goes through ADR-0010's single derivation in `ragstack/version.py`.
Run *parameters* (seeds, arms, budgets) are mixed into the same dicts as code
identity, so two runs cannot be compared mechanically and nothing tells a reader
whether a result is citable. The chunking study's answer was to pin itself to a
commit (`55a0fc2`) and refuse to run anywhere else; #682 replaces that pin with a
record and unfreezes `ingestion/chunkers.py`.

**Why a dropped record makes an artifact uncitable.** A result is a number plus
the method that produced it. If the method is only in a log line, a stream or a
job's stdout, it is separable from the result: the log rotates, the engine
re-stages the outputs, the CSV is copied into a paper directory, and the number
survives while the method does not. From then on the artifact can be *trusted*
but not *verified* — exactly the `source: config` ("declared, not verified")
state ADR-0002 accepted as a one-time migration cost and that we should never
manufacture again. The record has to travel with the artifact or it is not a
record.

Two external facts shape the interchange side. GoWe has **no provenance export**
today — its output JSON carries path, checksum and size and nothing about the
step that produced them (GoWe#49, open). And the community format that engines
actually emit has settled: Workflow Run RO-Crate (Galaxy, Nextflow via nf-prov,
COMPSs, others), not CWLProv.

## Decision

Owner's framing (2026-10-06): **logging-like ergonomics, explicit contract.**
Provenance is callable from any function, the way `logging` is — but a record
is *part of the artifact it describes*: written by the code that writes the
artifact, in the same write, stored in or beside it, never only emitted to a
stream or a log.

### 1. Three kinds of fact, kept separate

The record separates what the five implementations mixed, using W3C PROV's
vocabulary without its serialisation:

| Kind | PROV | Scope | Content |
|---|---|---|---|
| **Code / environment identity** | Agent | static per process; computed once | derived version, full commit, raw `git describe`, dirty flag, untracked count; tools-image name, digest, `RELEASE` version / commit / build; GoWe `workflow_id`; Python version |
| **Data lineage** | Entity, Activity, `used`, `wasGeneratedBy` | per step | which inputs (by URI and digest) went into which outputs |
| **Parameters** | attributes of the Activity | per activity | what the step was asked to do (chunk method, size, model, seeds, arms) |

Identity never carries parameters; parameters never carry identity. A reader
who wants "same code, different settings" or "same settings, different code"
compares one field group and not the other.

### 2. The record: `ragstack.provenance/1`

```json
{
  "schema": "ragstack.provenance/1",
  "fingerprint": "sha256:…",
  "core": {
    "activity": {"id": "act_…", "kind": "ingest_shard", "parent_id": null,
                 "params": {"chunk_method": "semantic", "chunk_size": 256, "…": "…"}},
    "used":      [{"role": "shard", "uri": "shards/s0.jsonl", "sha256": "…",
                   "bytes": 1234, "digest_source": "computed"}],
    "generated": [{"role": "receipt", "uri": "receipt.json", "digest_source": "none"}],
    "agent":     {"version": "1.6.5", "commit": "…40 hex…", "describe": "v1.6.5",
                  "dirty": false, "untracked": 0,
                  "tool_image": "ragstack-tools-v1.6.5-b1.sif", "tool_image_digest": "…",
                  "image_version": "v1.6.5", "image_commit": "…", "image_build": "b1",
                  "workflow_id": "wf_…", "python": "3.12.4"}
  },
  "info": {
    "started_at": "…", "ended_at": "…", "host": "…", "pid": 0,
    "request_id": null, "gowe_job_id": "…",
    "argv_redacted": ["…"], "packages": {"ragstack": "1.6.5", "nltk": "3.9"},
    "notes": {"embedding_endpoints": ["…"]}
  }
}
```

* **`core` is deterministic and closed.** It is the only part that is
  fingerprinted. Its JSON Schema, `contracts/schemas/provenance_record.json`,
  sets `additionalProperties: false` on `core` and every object inside it, the
  same rule the API contract already applies (ADR-0006 decision 4). A new field
  is a schema change and a new minor version of the record, never an ad-hoc key.
* **`info` is never fingerprinted.** Timestamps, host, pid, the correlation id
  (`request_id` on the API path, `gowe_job_id` on the engine path), redacted
  argv, a small named-package list, and `notes`. `info.notes` is the **only
  open map** in the record, and it is capped at 64 keys and 64 distinct values
  per key — the same `MAX_SERIES` discipline, for the same reason, as
  `observability/stages.py`: an unbounded dict fed from a run is how a record
  becomes a memory leak.
* **`fingerprint = "sha256:" + sha256(canonical(core))`**, where `canonical`
  is: keys sorted at every level; compact separators (`,` `:`); strings NFC-
  normalised and encoded UTF-8 without ASCII escaping; floats as Python
  `repr` (shortest round-trip); booleans distinct from integers (`true` is
  never `1`); `null` kept, never dropped. `activity.id` is the one key removed
  before hashing, because it is **derived from the fingerprint**:
  `activity.id = "act_" + fingerprint[7:23]`. Two runs with the same code, the
  same inputs and the same parameters therefore have the same fingerprint and
  the same activity id, and a receipt re-written on re-run is byte-identical.
* **`EntityRef`** = `{role, uri, sha256?, bytes?, schema?, spec_hash?,
  record_fingerprint?, digest_source}`. `uri` is a path **relative to the
  artifact that holds the record** for files, or `qdrant://<collection>`,
  `es://<index>`, `ws://<workspace path>` for stores and the BV-BRC Workspace
  (`ws://` is the scheme the engine already stages from —
  `api/routers/documents.py`; the design study wrote `workspace:`, the code's
  spelling wins). `schema` names the entity's own format tag when it has one
  (`ragstack.embedding_file/v1`); `spec_hash` is ADR-0002's build-spec hash
  when the entity is a collection; `record_fingerprint` is the fingerprint of
  the record found *inside* a used entity, which is how lineage chains across
  steps. `digest_source` says where `sha256` came from: `computed` (hashed by
  this writer), `upstream` (copied from the entity's own receipt, header or
  record), `declared` (supplied by the caller, unverified) or `none`.
* **Naming.** `sentence_spans_fingerprint(texts)` is the fingerprint of the
  sentence-offset coordinate system (#682, where it was renamed from
  `segmentation_fingerprint`); `span_fingerprint(spans, kind=…)` is its generic
  form (`kind="units"` for structural units); `chunker_spec_fingerprint(…)` is
  the fingerprint of a chunker specification (future). All three are
  `params`-group facts, never `agent` facts: they describe what the code *did*,
  not which code it was. ADR-0002's `spec_hash` and `chunk_descriptor` are
  untouched and keep their meaning.

### 3. The API — `python/ragstack/provenance.py`

The module that already holds both the collection manifest and the v0 object
grows the recorder; nothing moves.

| Call | What it does |
|---|---|
| `environment()` | The Agent. Cached for the process, **never raises**; one identity function replacing the five in #683. Version and commit come from `version.py` (the only module that may run `git`, ADR-0010 d1); the raw describe comes through the one labelled accessor PR #680 permits as a provenance field; image identity from `RELEASE` as `tool_provenance()` reads it today. |
| `with activity(kind, params=…, used=…, strict=False) as act:` | Opens an Activity, owns its scope, closes it on exit. The record is built at exit. |
| `current()`, `used(ref)`, `generated(ref)`, `note(key, value)` | Ambient: act on the current activity from any depth, like `logging.getLogger(__name__).info(…)`. **No-ops outside an activity** — a library function must never fail because nobody opened one. |
| `entity(path, role=…)`, `store_entity(kind, name, …)` | Build an `EntityRef`; `entity()` reuses a digest from the file's own receipt/header/record when one exists (`digest_source: upstream`) rather than re-hashing. |
| `Activity.attach(obj)` / `write_sidecar(path)` / `child(kind, …)` | Put the finished record into a dict that is about to be written (receipt, manifest, header), or beside a non-JSON artifact as `<name>.prov.json`, or open a nested activity with `parent_id` set. |
| `read(obj_or_path)` | Tolerant reader: a v1 record, a v0 six-key object (upgraded, see 7), or nothing (`None`). Never raises on a malformed record; reports what it could not read. |
| `citable(record)` | True only when the commit is known, the tree was clean, and the record is a v1 record with a fingerprint. One function, so the g1 harnesses' two dirty gates become the same gate. |
| `diff(a, b)` | Which `core` fields differ between two records, by group (agent / params / used). |
| `lineage(path)` | Walk `used[*].record_fingerprint` through the artifacts it names and return the chain. |
| `ProvenanceIncomplete` | Raised at activity exit **only in `strict=True`** when an entity has no digest, the agent has no commit, or a generated artifact was never attached. Experiments and release builds run strict; a hand-run tool does not. |

### 4. Context propagation

One `Activity` object in **one** `ContextVar`, mutated in place — exactly the
discipline `python/ragstack/observability/context.py` documents and that the
whole observability package depends on: a child context sees the same object,
so `used()` from inside `asyncio.gather` legs reaches the activity that opened
them; `ContextVar.set()` below the owner is invisible to the owner and is never
done; `asyncio.to_thread` / `anyio.to_thread.run_sync` propagate the context, a
raw `loop.run_in_executor(ThreadPoolExecutor())` does not and silently no-ops.
One deliberate difference from the request context: `activity()` **owns its
scope and resets the var on exit** with the token it set, because an activity
is a `with` block with a known end, whereas a request's context must outlive
the middleware's `finally` for the exception handler.

Process pools never record: `contextvars` do not cross `fork`/`spawn`, so a
pool worker's `used()` is a no-op by construction and the **parent** records
the pool's inputs and outputs. Across CWL steps there is no shared process at
all, so the record is serialised **into the artifact** (receipt,
embedding-file header, load summary, archive `manifest.json`) and re-attached
downstream by `used(entity(path))`, which copies the upstream record's
fingerprint into `record_fingerprint`. That is the whole lineage mechanism;
there is no provenance service.

### 5. Persistence: inside or beside each artifact

| Artifact | Written by | Where the record goes |
|---|---|---|
| shard receipt (`receipt.json`) | `scripts/ingest_shard.py`, `scripts/embed_shard.py` via `ingestion/receipts.py` | `ShardReceipt.provenance` (v0 today; v1 record per section 7) |
| embedding file | `scripts/embed_shard.py` via `ingestion/embedding_file.py` | an additional key in the existing line-1 header (`ragstack.embedding_file/v1` already carries `schema`, `tenant`, `dim`, `count`) |
| load / replay summary (`load-summary.json`) | `scripts/load_embeddings.py` | its `provenance` key |
| `versions/<n>/manifest.json` | `scripts/archive_version.py` via `ingestion/archive.py` | top-level `provenance`; `graph_extraction.provenance` for `scripts/extract_graph.py` (`graph/extract_version.py`) |
| collection manifest (`<collection>.json` under `collection_manifest_dir`) | `provenance.write_manifest`, called from `api/deps.py` on API ingest | its `provenance` key |
| experiment outputs (CSV, markdown, `*_results.json`) | the eval harnesses | JSON outputs embed the record; everything else gets a `<name>.prov.json` sidecar, **written before any index or report that lists the artifact**, so an index never names an output that has no record |

The record is written in the **same write as the artifact** — inside the JSON
the writer is already serialising (atomic where that writer already is:
`archive.py` and `write_manifest` use a temp file and `os.replace`), or as a
sidecar written before the artifact is announced. A writer that cannot attach
the record does not emit it to a log instead; in strict mode it fails.

**Shard receipts stay byte-identical on re-run.** `ShardReceipt.to_json` is
documented as "sorted, no timestamp" for idempotence and diff-ability; the
record keeps that: a receipt's `info` carries no `started_at`, `ended_at`,
`host` or `pid`.

### 6. Determinism, redaction, performance

* Only `core` is fingerprinted (section 2). Nothing time- or host-dependent is
  allowed into `core`; a schema test holds the key set.
* **Secrets are redacted before fingerprinting**, so the fingerprint never
  depends on a secret and a redacted record re-fingerprints identically. Key
  denylist (case-insensitive substring, applied to `params`, `notes` and
  `argv_redacted`): `token`, `api_key`, `password`, `secret`, `authorization`,
  `cookie`, `credential`. URLs lose userinfo and query string. `argv` is
  recorded only redacted and only in `info`. **Never recorded**: `os.environ`,
  user subjects, query text (#114, the same rule `stages.query_sha` enforces),
  document content.
* **One record per job, shard, version or experiment — never per chunk.** A
  shard of 10⁴ chunks has one record; chunk-level lineage is a *join* through
  the receipt's `chunk_ids`, which is what receipts already carry.
* **Reuse upstream digests.** The embed stage already hashed what the load
  stage reads; `entity()` copies the digest (`digest_source: upstream`) rather
  than streaming a multi-gigabyte embedding file a second time.

### 7. Compatibility: v0 is a version, not a mistake

Today's six-key object (`PROVENANCE_KEYS`, `tool_provenance()`,
`read_provenance()`, ADR-0010 decision 8) is hereby **`ragstack.provenance/0`**.
It maps onto v1 as the `agent`'s image and workflow fields with an empty
`used`/`generated` and no fingerprint; `read()` performs that upgrade and marks
the result `schema: "ragstack.provenance/0"` so a reader can tell an upgraded
record from a native one. During migration every writer in section 5
**dual-writes**: the v0 object stays under `provenance` for ADR-0010's readers
(`read_provenance`, `verify_named_image`'s consumers, the runbook), and the v1
record is written beside it under `provenance_record`. `tool_provenance()` is
kept as the six-key view over `environment()`. PR-9 retires the v0 write once
every reader has moved to `read()`; a record without either key reads as
unknown, exactly as today (ADR-0010 migration step 2: "never as an error").

### 8. The online query path records nothing

A query is not an artifact. The response already says what produced it
(ADR-0008's fields); no provenance record is written per request. If a later
need arises to join a served answer to the collection version that served it,
the join is OpenTelemetry attributes on the existing request span —
collection, `spec_hash`, archive version, model — not a record. Attribute
names are not decided here.

### 9. Interchange: Workflow Run RO-Crate; CWLProv is not adopted

* **Export format: Workflow Run RO-Crate, Provenance Run Crate profile 0.6**
  (verified in the prior-art survey): plain JSON-LD, emitted today by Galaxy,
  Nextflow (nf-prov), COMPSs and others, with a `ContainerImage` slot that
  carries a sha256 — the one field ADR-0010 made load-bearing. RAGStack exports
  **one crate per archive version** (`ro-crate-metadata.json` beside
  `manifest.json`, PR-8), built from the v1 records in the version's manifest,
  receipts and summaries. GoWe emits **one crate per submission** (GoWe#49,
  open). The two join on **GoWe `workflow_id` + GoWe submission id**: ours says
  what the tools did, GoWe's says how the engine ran them.
* **CWLProv is not adopted** as either internal format or export: the spec has
  been frozen at 0.6.0 since 2018, only cwltool writes it, it is PROV-N-
  centric, it copies data into the BagIt bag, and its image identity is a name
  only. `cwltool --provenance --singularity` is **kept as an independent
  reproduction and audit path** — it is cwltool running our CWL and our tools
  image with its own bookkeeping, and it worked on `coconut` in testing.
* **Not adopted as the internal API**, one line each: *OpenTelemetry* — lossy
  by design and models execution, not derivation. *OpenLineage* — event
  emission to a backend, no ambient context for library code. *MLflow* —
  thread-local run state and a tracking server; heavy for a CWL step.
  *yProv4ML* — GPL-3.0. *Flowcept* — a capture service and message bus; heavy.
  *noWorkflow* — forensic tracing of scripts after the fact, not a record the
  writer controls. The `prov` Python library is a possible **export
  serialiser** (PROV-JSON / PROV-O from v1 records) and nothing more.
* **What GoWe needs before its crate is useful**, recorded so GoWe#49 can take
  it up (GoWe's design is GoWe's): engine version per submission; the resolved
  image path **and digest** reported by the worker (today a name, ADR-0010
  context); the exact command line; per-attempt task records (retries
  currently overwrite the previous attempt); input checksums; a packed CWL.

### 10. Experiments

`ingestion/chunkers.py` is unfrozen (#682). An experiment records the derived
version, the raw `git describe` and the full commit (ADR-0010 d1's one
exception, PR #680), and should run from a versioned `ragstack-tools` image so
that `agent.tool_image_digest` is set; `citable()` is the gate a write-up uses.
`experiment_provenance()` from #682 becomes `environment()` plus the
fingerprints of section 2, under `params`.

## Not decided

* The exact OpenTelemetry attribute names for the query-path join (section 8).
* Whether the server image replaces checkouts for every tenant (ADR-0010's open
  question; it decides whether `agent.commit` is always known on the API path).
* The GoWe crate's API shape — the flag, the directory layout, how the
  submission id is exposed. GoWe's own decision (GoWe#49).

## Consequences

* **Cost.** One schema file, one module's growth, a guard test, one additive
  key in six writers, a sidecar convention for experiments, and an export. No
  service, no database, no new process.
* **One `git` caller.** The guard test (`git grep` for a `"git"` subprocess
  outside `ragstack/version.py`, with an allow-list for the frozen
  `docs/plans/results/stage0/` code, `docs/build_docs.py`'s own stamp per
  ADR-0010 d1, and tests) makes a sixth implementation a test failure.
* **The frozen study is left alone** (#683 step 4). `stage0/` re-runs at its
  pinned commit where the old code is what runs anyway; new stages use
  `environment()`.
* **Receipts and manifests grow** by one object per record — hundreds of bytes
  against receipts that list every chunk id. Negligible; and it is why there is
  no per-chunk record.
* **Readers must tolerate three states**: no record, a v0 object, a v1 record.
  `read()` is the only reader; nobody parses `provenance` by hand.
* **A lineage walk depends on the artifacts still existing.** `lineage()`
  follows URIs relative to the artifact; a moved or purged upstream is
  reported as a broken link, not a guess. That is the right failure: the
  record says what *was* used, not what is still there.
* **Two export formats coexist** for one run (our crate, GoWe's crate) until
  GoWe#49 lands; the join key exists from PR-3 onward because `workflow_id` is
  already in v0.

## What NOT to do

* **A logging handler as the store.** A record that exists only because a
  handler caught it is separable from its artifact (Context). Logging may
  *mirror* a record's fingerprint for correlation; it is never where the
  record lives.
* **Per-chunk records.** Lineage at chunk granularity is a join through
  `chunk_ids`; a record per chunk is 10⁴× the bytes for no new information.
* **Timestamps, hosts or pids in `core`.** They belong in `info`; in `core`
  they would make every fingerprint unique and every receipt non-idempotent.
* **Dumping `os.environ`, raw `argv` or the settings object.** Environments
  carry secrets; settings carry DSNs. Record the parameters that mattered,
  under `params`, by name.
* **A second contextvar discipline.** One object, one var, mutate in place,
  owner resets. Anyone who needs a field adds it to `Activity`, not a new var.
* **`git` subprocesses outside `ragstack/version.py`.** The raw describe
  leaves it through one labelled accessor as a provenance field (PR #680), and
  that is the only door.

## Migration

Each step is its own PR; #682 is step 0 and must land first (the
`sentence_spans_fingerprint` rename, the unfreeze, and the provisional
`experiment_provenance()` this record subsumes).

| PR | Lands |
|---|---|
| **PR-1** | `environment()` (cached, never raises) and `fingerprint()` / `canonical()` in `ragstack/provenance.py`; the guard test for `git` callers. `tool_provenance()` becomes the six-key view over `environment()`. |
| **PR-2** | `contracts/schemas/provenance_record.json` (v1, `core` closed), `read()` with the v0 upgrade, `citable()`, `diff()`. |
| **PR-3** | The recorder: `activity()`, `current()`, `used()`, `generated()`, `note()`, `entity()`, `store_entity()`, `Activity.attach/write_sidecar/child`, `ProvenanceIncomplete`. **Dual-write** in `ingest_shard.py`, `embed_shard.py` (receipt and embedding-file header), `load_embeddings.py`, `archive_version.py`, `extract_graph.py`. |
| **PR-4** | Deep notes: `note()` and `used()` calls from inside the pipeline (embedder endpoints used, chunker and sentence-span fingerprints, retry counts), reaching the activity ambiently with no signature changes. |
| **PR-5** | Experiments: `_g1_rating.py`, `g1_library_sweep.py`, `chunking_compare.py`, `chunking_compare_7way.py`, `scifact_chunk_eval.py` open an activity, delete their own `git` calls, write sidecars before indexes, gate on `citable()`. `experiment_provenance()` is removed. |
| **PR-6** | The collection manifest and the API ingest path (`api/deps.py`) attach a record; `GET /v1/collections` exposes `fingerprint` and `citable`. |
| **PR-7** | `lineage()` and a CLI (`python -m ragstack.provenance show|lineage|diff <path>`). |
| **PR-8** | RO-Crate export per archive version (`ro-crate-metadata.json`, Provenance Run Crate 0.6). |
| **PR-9** | Retire the v0 write; `read_provenance()` becomes `read()` projected to the six keys; ADR-0010 decision 8's field list is marked as the v0 profile of this record. |

## Alternatives considered

* **Emit provenance as structured log events and reconstruct records later.**
  Rejected by the owner's framing: a record separable from its artifact is a
  log, and the Context section is the list of ways logs get separated.
* **Adopt CWLProv now, since cwltool already writes it.** Rejected (section 9):
  frozen spec, single writer, name-only image identity. Kept as an audit path.
* **A provenance service / database that steps post to.** Rejected: a step
  that runs on a GoWe worker with no network to such a service would have to
  choose between failing and silently dropping the record; writing into the
  artifact needs neither network nor service and survives the engine's
  post-staging because it *is* the output.
* **Extend ADR-0010's six keys in place.** Rejected: that object is identity
  only and has no slot for lineage or parameters; growing it would reproduce
  the five-implementations problem inside one dict. It is kept as v0 instead.
