# 0011. Provenance records: one record format, one ambient API, written into the artifact it describes

Status: Proposed (2026-10-06; issues #683, #682 (step 0), PR #680; GoWe#49; the provenance design study and prior-art survey of 2026-10-06)

## Context

"Provenance" names three different things in `ragstack/` today, and the word is
the only thing they share:

1. **What a build observed.** The per-collection manifest in
   `python/ragstack/provenance.py` (`CollectionManifest`, `write_manifest`,
   `source: ingest | config`) — the module's own "verified lineage, as opposed
   to the registry's operator-asserted labels" (ADR-0002's `source: ingest` vs
   `source: config`), surfaced as `Provenance` on `GET /v1/collections`
   (`api/routers/collections.py`).
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
| **Code / environment identity** | Agent | static per process; the git / `RELEASE` part computed once, the submission inputs passed in | derived version (`vX` / `vX+<sha>`, ADR-0010 d1), its `source` (`git` \| `image` \| `distribution`, ADR-0010 d1), full commit, raw `git describe`, dirty flag, untracked count; tools-image name, digest, `RELEASE` version / commit / build; GoWe `workflow_id` |
| **Data lineage** | Entity, Activity, `used`, `wasGeneratedBy` | per step | which inputs (by URI and digest) went into which outputs |
| **Parameters** | attributes of the Activity | per activity | what the step was asked to do (chunk method, size, model, seeds, arms) |

Identity never carries parameters; parameters never carry identity. A reader
who wants "same code, different settings" or "same settings, different code"
compares one field group and not the other. The Python version is context, not
identity (#682's comparison-key rule), and is recorded in `info`.

### 2. The record: `ragstack.provenance/1`

```json
{
  "schema": "ragstack.provenance/1",
  "fingerprint": "sha256:…",
  "core": {
    "activity": {"id": "act_…", "kind": "ingest_shard",
                 "params": {"chunk_method": "semantic", "chunk_size": 256, "…": "…"}},
    "used":      [{"role": "shard", "uri": "s0.jsonl", "sha256": "…",
                   "bytes": 1234, "digest_source": "computed"}],
    "generated": [{"role": "receipt", "uri": "receipt.json", "digest_source": "none"}],
    "agent":     {"version": "v1.6.5", "commit": "abc1234…40 hex…",
                  "describe": "v1.6.5-0-gabc1234", "source": "git",
                  "dirty": false, "untracked": 0,
                  "tool_image": "ragstack-tools-v1.6.5-b1.sif", "tool_image_digest": "…",
                  "image_version": "v1.6.5", "image_commit": "…", "image_build": "b1",
                  "workflow_id": "wf_…"}
  },
  "info": {
    "started_at": "…", "ended_at": "…", "host": "…", "pid": 0,
    "request_id": null, "gowe_job_id": "…", "python": "3.12.4",
    "argv_redacted": ["…"], "packages": {"ragstack": "1.6.5", "nltk": "3.9"},
    "notes": {"embedding_endpoints": ["…"]}
  }
}
```

The example shows every `info` key a record may carry, including
`gowe_job_id`. A shard receipt, an embedding-file header and an archive
manifest keep a restricted `info` of `{python, packages, notes}` (plus `parent_kind`
on a child's record) that excludes `gowe_job_id`, so a re-submission's new
GoWe job id never breaks the byte-identity promise (section 5).

* **`core` is deterministic and closed.** It is the only part that is
  fingerprinted. Its JSON Schema, `contracts/schemas/provenance_record.json`,
  sets `additionalProperties: false` on `core` and every object inside it, the
  same rule the API contract already applies (ADR-0006 decision 4). A new `core`
  field is a schema change and a new record version (`ragstack.provenance/2`),
  never an ad-hoc key. Fingerprints are comparable only within one version.
  Fields may be added to `info` within a version.
* **`info` is never fingerprinted.** Timestamps, host, pid, the correlation id
  (`request_id` on the API path, `gowe_job_id` on the engine path), the Python
  version, redacted argv, a small named-package list, `parent_kind` on a nested
  activity's record (section 3), and `notes`. `info.notes` is the **only
  open map** in the record, and it is capped at 64 keys and 64 distinct values
  per key — the same `MAX_SERIES` discipline, for the same reason, as
  `observability/stages.py`: an unbounded dict fed from a run is how a record
  becomes a memory leak.
* **`fingerprint = "sha256:" + sha256(canonical(core))`**, where `canonical`
  is: keys sorted at every level; compact separators (`,` `:`); strings NFC-
  normalised and encoded UTF-8 without ASCII escaping; floats as Python
  `repr` (shortest round-trip); booleans distinct from integers (`true` is
  never `1`); `null` kept, never dropped; non-finite floats are refused.
  `activity.id` is the one key removed before hashing, because it is
  **derived from the fingerprint**: `activity.id = "act_" + fingerprint[7:23]`. Two runs with the same code, the
  same inputs and the same parameters therefore have the same fingerprint and
  the same activity id, and a receipt re-written on re-run is byte-identical.
* **`EntityRef`** = `{role, uri, sha256?, bytes?, schema?, spec_hash?,
  record_fingerprint?, digest_source}`. For a file, `uri` is a path **relative
  to the artifact that holds the record** (for a sidecar, relative to the
  artifact it describes) when the file lies under that artifact's directory,
  and otherwise the file's basename. A CWL-staged input sits in a per-attempt
  staging directory whose relative path changes on every retry; its `sha256`
  is the identity and its `uri` is a label. Stores and the BV-BRC Workspace are
  `qdrant://<collection>`, `es://<index>`, `ws://<workspace path>` (`ws://` is
  the scheme the engine already stages from — `api/routers/documents.py`; the
  design study wrote `workspace:`, the code's spelling wins). `schema` names
  the entity's own format tag when it has one (`ragstack.embedding_file/v1`);
  `spec_hash` is ADR-0002's build-spec hash when the entity is a collection;
  `record_fingerprint` is the fingerprint of the record found *inside* a used
  entity, which is how lineage chains across steps, or, on a
  `role: "child_activity"` entry in `generated`, the fingerprint of a finished
  nested activity's record (section 3). `digest_source` says where `sha256`
  came from: `computed` (hashed by this writer), `upstream` (copied from the
  entity's own receipt, header or record), `declared` (supplied by the caller,
  unverified) or `none`.
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
| `environment(workflow_id=None, tool_image=None, tool_image_digest=None)` | The Agent. **Never raises**; one identity function replacing the five in #683. The git / `RELEASE` / Python part is computed once per process and cached: version and commit come from `version.py` (the only module that may run `git`, ADR-0010 d1); the raw describe comes through the one labelled accessor PR #680 permits as a provenance field (`version.raw_describe_for_provenance()`, of which `environment()` is the sole caller); image identity from `RELEASE` as `tool_provenance()` reads it today; the Python version goes to `info`. The three submission inputs are passed by the tool that parsed them — the flags from `add_provenance_arguments`, exactly as `tool_provenance()` takes them today. |
| `with activity(kind, params=…, used=…, strict=False) as act:` | Opens an Activity and owns its scope. The record is **finalised by the first `attach()` / `write_sidecar()` call**, because the writer needs it inside the block, in the same write as the artifact. After that, `used()` / `generated()` / `note()` on this activity raise in strict mode and are dropped with a warning otherwise. Exit runs the strict checks and resets the context var. |
| `current()`, `used(ref)`, `generated(ref)`, `note(key, value)` | Ambient: act on the current activity from any depth, like `logging.getLogger(__name__).info(…)`. **No-ops outside an activity** — a library function must never fail because nobody opened one. `note()` is **set-valued per key**: it adds `value` to the key's set of values (a repeat is a no-op) and never overwrites; the set is serialised as a list in canonical order, within the caps of section 2. |
| `entity(path, role=…)`, `store_entity(kind, name, …)` | Build an `EntityRef`; `entity()` reuses a digest from the file's own receipt/header/record when one exists (`digest_source: upstream`) rather than re-hashing. |
| `Activity.attach(obj)` / `write_sidecar(path)` / `child(kind, …)` | Put the finished record into a dict that is about to be written (receipt, manifest, header), or beside a non-JSON artifact as `<name>.prov.json`, or open a nested activity (below). |
| `read(obj_or_path)` | Tolerant reader: a v1 record, a v0 six-key object, a #682 experiment block (both upgraded, see 7), or nothing (`None`). Never raises on a malformed record; reports what it could not read. |
| `citable(record)` | True only when the commit is known, `dirty` is false, `untracked` is 0 (#682's rule: the harness may itself be an uncommitted file; a record whose `source` is `image` has no checkout and passes this check), and the record is a v1 record with a fingerprint. One function, so the g1 harnesses' two dirty gates and #682's `citable` key become the same gate. |
| `diff(a, b)` | Which `core` fields differ between two records, by group (agent / params / used). Subsumes #682's `comparison_key()`. |
| `lineage(path)` | Walk `used[*].record_fingerprint` through the artifacts it names and return the chain. |
| `redact_for_export(record)` | The view of a record that leaves the host (section 6): drops `info.host`, `info.pid` and `info.argv_redacted`, and reduces `ws://` URIs to their basename. |
| `ProvenanceIncomplete` | Raised **only in `strict=True`**: at activity exit when a `used` entity has no digest (`digest_source: none`), the agent has no commit, or no `attach()` / `write_sidecar()` was made; and on a `used()` / `generated()` / `note()` after the record was finalised. The `generated` entry for the artifact that carries the record itself has `digest_source: none` by necessity (a file cannot contain its own hash) and is exempt. Experiments and release builds run strict; a hand-run tool does not. |

**Nesting is recorded parent→children.** There is no `parent_id` in `core`,
because a child closes before its parent's fingerprint exists and so cannot
name it. Instead the parent's `generated` lists each finished child record as
an `EntityRef` with `role: "child_activity"` and `record_fingerprint` set to
the child's fingerprint, and the child's `info.parent_kind` names the parent's
`kind` for readers (in `info`, so a child's fingerprint does not depend on where
it was opened). A child is therefore finished before its parent is finalised; a
`child()` on a finalised activity is a late call like any other.

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
| shard receipt (`receipt.json`) | `scripts/ingest_shard.py`, `scripts/embed_shard.py` via `ingestion/receipts.py` | `ShardReceipt.provenance` (v0 today, written by `ingest_shard.py` only; `embed_shard.py` writes none) plus `provenance_record` (v1, section 7) |
| embedding file | `scripts/embed_shard.py` via `ingestion/embedding_file.py` | an additional `provenance_record` key in the existing line-1 header (`ragstack.embedding_file/v1` already carries `schema`, `tenant`, `dim`, `count`); see below |
| load / replay summary (`load-summary.json`) | `scripts/load_embeddings.py` | `provenance_record` beside its `provenance` key (replay mode writes v0 today; the load-from-files mode and the chunk-cap refusal summary write none) |
| `versions/<n>/manifest.json` | `scripts/archive_version.py` via `ingestion/archive.py` | top-level `provenance` plus `provenance_record`; `graph_extraction.provenance` plus `graph_extraction.provenance_record` for `scripts/extract_graph.py` (`graph/extract_version.py`) |
| collection manifest (`<collection>.json` under `collection_manifest_dir`) | `provenance.write_manifest`, called from `api/deps.py` on API ingest and from `ops/ingest_target.py` by `ingest_shard.py` / `load_embeddings.py` | a new `provenance_record` field on `CollectionManifest` (no v0 exists here) |
| experiment outputs (CSV, markdown, `*_results.json`) | the eval harnesses | JSON outputs embed the record; everything else gets a `<name>.prov.json` sidecar, **written before any index or report that lists the artifact**, so an index never names an output that has no record; #682's `*_results.provenance.json` sidecars become `<name>.prov.json` in PR-5 |

The record is written in the **same write as the artifact** — inside the JSON
the writer is already serialising (atomic where that writer already is:
`archive.py` and `write_manifest` use a temp file and `os.replace`), or as a
sidecar written before the artifact is announced. A writer that cannot attach
the record does not emit it to a log instead; in strict mode it fails.

**The embedding file is written before its digest exists.**
`EmbeddingFileWriter` writes the header on the first `write()`, so the header's record is finalised
before the first chunk, and its `generated` entry for the file has
`digest_source: none` (the strict-mode exemption of section 3). The step is
therefore a parent and a child: the child activity writes the file and its
header record; the parent's record, attached to the shard receipt written
after the file, carries the file's digest and lists the child as
`child_activity`. `embed_shard.py` takes no provenance flags today. PR-3 adds
`add_provenance_arguments` to it and the matching inputs to the embed
CommandLineTool in `cwl/` — a workflow-text change, and so a new GoWe id.

**Shard receipts stay byte-identical on re-run.** `ShardReceipt.to_json` is
documented as "sorted, no timestamp" for idempotence and diff-ability; the
record keeps that. A receipt's `info` is exactly `{python, packages, notes}`
(plus `parent_kind` on a child's record, a constant). The test for a key is
whether it is constant for the same code, environment and input: `python` and
`packages` are fixed per environment, so they pass. There is no `started_at`, `ended_at`,
`host`, `pid`, `argv_redacted` or `gowe_job_id`, because argv names
per-attempt staging paths and a re-submission gets a new GoWe job id, which
would otherwise make the receipt differ from its own prior write. The GoWe
job is recoverable from where the receipt was written and from the workflow
run, not from the receipt bytes. The same restriction holds for the
embedding-file header and the archive manifest, whose byte-identity
`archive.py` also promises. On these three artifact kinds a writer must not
put per-run values in `notes`; today it carries the tenant's embedding
endpoints, which are stable.

### 6. Determinism, redaction, performance

* Only `core` is fingerprinted (section 2). Nothing time- or host-dependent is
  allowed into `core`; a schema test holds the key set.
* **Secrets are redacted before fingerprinting**, so the fingerprint never
  depends on a secret and a redacted record re-fingerprints identically. The
  rule:
  * **Matching:** case-insensitive substring match after normalising `-` to `_`.
  * **Scope:** every key at any depth of `params` and `notes`. For argv, the
    flag name stays and the following value, or the `=value` tail, is replaced.
  * **List:** `token`, `api_key`, `apikey`, `password`, `passwd`, `secret`,
    `authorization`, `auth`, `bearer`, `cookie`, `credential`, `dsn`,
    `private_key`.
  * **URLs:** every string value anywhere in the record that parses as a URL
    loses its userinfo and query string. This includes DSNs
    (`postgresql+asyncpg://user:pass@host/db`, `redis://:pass@host`),
    `qdrant_url` / `elasticsearch_url`, and the `embedding_endpoints` in
    `notes`.
  * **Export:** records that leave the host (the PR-8 crate, a published study)
    pass through `redact_for_export(record)`. It additionally drops
    `info.host`, `info.pid` and `info.argv_redacted`, and reduces `ws://` URIs
    to their basename, because the Workspace path embeds the BV-BRC username,
    which this section otherwise forbids recording.

  `argv` is recorded only redacted and only in `info` (never on a receipt,
  header or archive manifest, section 5). **Never recorded**: `os.environ`,
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
record from a native one. During migration:

* **Writers that write a v0 object today dual-write:** `ingest_shard.py`, the
  replay summary of `load_embeddings.py`, `archive_version.py`,
  `extract_graph.py`. The v0 object stays under `provenance` for ADR-0010's
  readers (`read_provenance`, called from `ingestion/load_embeddings.py`; the
  runbooks), and v1 goes beside it under `provenance_record`. The graph leg
  uses `graph_extraction.provenance_record`.
* **Writers with no v0 today write only `provenance_record`:**
  `embed_shard.py`, the embedding-file header, the collection manifest, the
  load-from-files summary.
* **Existing readers are unaffected:** `ShardReceipt.from_dict` builds from
  named keys; `archive.read_manifest` shape-checks only `format` / `files` /
  `sha256` / `counts`; `read_header` returns the dict; `CollectionManifest` is
  a pydantic model with default `extra=ignore`.

`tool_provenance()` is kept as the six-key view over `environment()`. PR-9
retires the v0 write once every reader has moved to `read()`; a record without
either key reads as unknown, exactly as today (ADR-0010 migration step 2:
"never as an error").

#682's provisional `experiment_provenance()` block is
**`ragstack.provenance/0-experiment`**. It has `schema: 1` and the keys
`version` / `describe` / `commit` / `dirty` / `untracked` / `source` /
`in_image` / `image` / `installed_distribution` / `segmentation` / `python` /
`host` / `recorded_at` / `citable` / `warnings`, and the three chunking
harnesses write it as `*_results.provenance.json` once #682 lands. `read()`
upgrades it too: `version`, `describe`, `commit`, `dirty`, `untracked`,
`source` and `image` go onto `agent`; `segmentation` goes onto `params`;
`in_image`, `installed_distribution`, `python`, `host` and `recorded_at` go
into `info` (`recorded_at` as `info.started_at`), and `warnings` goes into
`info.notes`; `citable` is recomputed by `citable()`, never copied.
`comparison_key()` is subsumed by `diff()`. PR-5 renames those sidecars to
`<name>.prov.json`. `0-experiment` labels a pre-v1 shape that only `read()`
accepts; it is not a step in the record's version sequence.

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
  Nextflow (nf-prov), COMPSs and others. Its `ContainerImage` type, defined in
  Process Run Crate 0.6 and inherited by the provenance profile, carries
  `name`, `tag`, `registry` and `sha256` — the last the one field ADR-0010 made
  load-bearing. RAGStack exports **one crate per archive version**
  (`ro-crate-metadata.json` beside `manifest.json`, PR-8), built from the v1
  records in the version's manifest, receipts and summaries, each passed
  through `redact_for_export()` (section 6). GoWe emits **one crate per submission** (GoWe#49,
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
* **Readers must tolerate four states**: no record, a v0 object, a #682
  experiment block, a v1 record. `read()` is the only reader; nobody parses
  `provenance` by hand.
* **A lineage walk depends on the artifacts still existing.** `lineage()`
  follows URIs relative to the artifact; a moved or purged upstream, or a
  staged input recorded by basename only (section 2), is reported as a broken
  link, not a guess. That is the right failure: the record says what *was*
  used, not what is still there.
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
  leaves it through one labelled accessor as a provenance field (PR #680),
  `environment()` is that accessor's sole caller, and that is the only door.

## Migration

Each step is its own PR; #682 is step 0 and must land first (the
`sentence_spans_fingerprint` rename, the unfreeze, and the provisional
`experiment_provenance()` this record subsumes).

| PR | Lands |
|---|---|
| **PR-1** | `environment()` (cached, never raises) and `fingerprint()` / `canonical()` in `ragstack/provenance.py`; the guard test for `git` callers. `tool_provenance()` becomes the six-key view over `environment()`. `environment()` becomes the sole caller of `version.raw_describe_for_provenance()`; #682's caller-grep test is re-pointed; ADR-0010 d1's exception paragraph (added by PR #680) is amended to name `environment()` in place of `experiment_provenance()`. |
| **PR-2** | `contracts/schemas/provenance_record.json` (v1, `core` closed), `read()` with the v0 and #682-block upgrades, `citable()`, `diff()`, `redact_for_export()`. |
| **PR-3** | The recorder: `activity()`, `current()`, `used()`, `generated()`, `note()`, `entity()`, `store_entity()`, `Activity.attach/write_sidecar/child`, `ProvenanceIncomplete`. **Dual-write** in `ingest_shard.py`, `load_embeddings.py` (replay summary), `archive_version.py`, `extract_graph.py`; `provenance_record` only in `embed_shard.py` (receipt and embedding-file header, plus `add_provenance_arguments` and the embed CommandLineTool's inputs in `cwl/` — a new GoWe id) and `load_embeddings.py`'s load-from-files summary. |
| **PR-4** | Deep notes: `note()` and `used()` calls from inside the pipeline (embedder endpoints used, chunker and sentence-span fingerprints, retry counts), reaching the activity ambiently with no signature changes. |
| **PR-5** | Experiments: `_g1_rating.py`, `g1_library_sweep.py`, `chunking_compare.py`, `chunking_compare_7way.py`, `scifact_chunk_eval.py` open an activity, delete their own `git` calls, write sidecars before indexes, gate on `citable()`. #682's `*_results.provenance.json` sidecars are renamed to `<name>.prov.json`. `experiment_provenance()` and `comparison_key()` are removed. |
| **PR-6** | The collection manifest gains `provenance_record`, attached on both `write_manifest` paths (`api/deps.py` on API ingest, `ops/ingest_target.py`); `GET /v1/collections` exposes `fingerprint` and `citable`. |
| **PR-7** | `lineage()` and a CLI (`python -m ragstack.provenance show|lineage|diff <path>`). |
| **PR-8** | RO-Crate export per archive version (`ro-crate-metadata.json`, Provenance Run Crate 0.6), from records passed through `redact_for_export()`. |
| **PR-9** | Retire the v0 write; `read_provenance()` becomes `read()` projected to the six keys; ADR-0010 decision 8's field list is marked as the v0 profile of this record. |

## Open for owner confirmation

This ADR is Proposed. Each item below is a choice the text now makes, with the
reason for it; the owner confirms or reverses each before PR-1.

* **Nesting direction: parent→children** (section 3). A child closes before its
  parent's fingerprint exists, so only the parent can name the other.
* **Finalisation at the first `attach()` / `write_sidecar()`** (section 3). The
  writer needs the record inside the block, in the same write as the artifact.
* **Integer record versions** (`ragstack.provenance/2` for a new `core` field;
  section 2). Fingerprints compare only within one version, so a version must
  mark every change to what is hashed.
* **The receipt `info` set `{python, packages, notes}`, dropping `gowe_job_id`**
  (section 5), to keep receipts, headers and archive manifests
  byte-identical on re-run. The GoWe job is recoverable from where the
  receipt was written and from the workflow run, not from the receipt bytes.
* **Basename URIs for staged inputs** (section 2). A per-attempt staging path
  would change on every retry; the `sha256` carries the identity.
* **`citable` requires `untracked == 0`** (section 3). #682's rule: the harness
  may itself be an uncommitted file.
* **`python` in `info`** (sections 1, 2). #682's comparison-key rule treats it
  as context, not identity. It is constant per environment, so it is kept in
  a receipt's restricted `info` (owner, 2026-10-06).
* **The `vX` / `vX+<sha>` version spelling** (section 2). ADR-0010 d1's derived
  version, as #682 already writes it.
* **The #682 sidecar rename in PR-5** (`*_results.provenance.json` →
  `<name>.prov.json`). One sidecar convention for every experiment output.
* **`read()` upgrades #682 blocks** (section 7). Results written under #682
  stay readable through the one reader.
* **`environment()` takes its submission inputs as arguments** (section 3). The
  tool that parsed the flags passes them, as `tool_provenance()` does today;
  the cached part stays argument-free.
* **`note()` is set-valued per key** (section 3). Repeated notes from a loop
  cannot overwrite each other, and the cap of section 2 bounds the set.
* **Export-time stripping of `ws://` paths and host** (section 6). The
  Workspace path embeds the BV-BRC username; host and pid say nothing a reader
  off the host needs.
* **`environment()` inherits the raw-describe door** (PR-1). One caller of
  `version.raw_describe_for_provenance()` keeps PR #680's exception narrow.
* **`activity.id = "act_" + fingerprint[7:23]`** (section 2). Derived, so a
  re-run reproduces it; 16 hex digits are enough to tell activities apart.
* **`digest_source ∈ {computed, upstream, declared, none}`** (section 2). A
  reader can tell a verified digest from a copied or asserted one.
* **The `provenance_record` key name** (sections 5, 7). It sits beside v0's
  `provenance` without colliding during dual-write.
* **The `sha256:` prefix on fingerprints** (section 2). The algorithm is named
  in the value, so a future change of hash cannot be mistaken for a match.
* **`ws://` URIs** (section 2). The scheme the engine already stages from.

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
