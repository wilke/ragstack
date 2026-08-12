# CWL workflows (ADR-0001 offline plane)

> Integration reference + performance evaluation: [`docs/gowe-integration.md`](../docs/gowe-integration.md).


Scaffold for the **offline / throughput plane** of [ADR-0001](../docs/adr/0001-execution-topology.md):
bulk ingestion and the eval/benchmark harnesses expressed as CWL DAGs, executed by
[GoWe](../../GoWe) (or any CWL v1.2 runner). Covers **step 1** (the eval harness —
the lowest-risk entry point) and **step 2** (bulk ingest — the atomic per-shard
tool + receipts).

## What's here

| File | Role |
|---|---|
| `eval-scifact-chunking.cwl` | **Step 1.** Scatter/gather: ingest+score each chunking config independently, then aggregate the stats. |
| `eval-scifact-chunking.inputs.yml` | Example inputs (the configs to compare + the embedding key). |
| `ingest-bulk.cwl` | **Step 2 (coupled).** Scatter/gather: ingest each JSONL shard independently → receipt, then merge receipts into a run summary. Chunk→embed→upsert inline. |
| `ingest-bulk.inputs.yml` | Example inputs (shard files + collection + embedding endpoints). |
| `embed-bulk.cwl` | **Step 2 (decoupled embed half, #141).** Scatter `embed_shard` → one JSONL **embedding file** per shard (no store contact), gather receipts. |
| `load-embeddings.cwl` | **Step 2 (decoupled load half, #141).** Single task: upsert the embedding files into Qdrant/ES (`index_chunks`), where backpressure will live. |
| `embed-bulk.inputs.yml` / `load-embeddings.inputs.yml` | Example inputs for the two halves. |
| `pdf-extract.cwl` | **PDF plane (#202/#203).** Run the real `PdfLoader` (PyMuPDF) over PDFs → one `{text,path,metadata}` JSONL shard (+ a skipped-files report). |
| `pdf-ingest.cwl` | **PDF plane (#202/#203).** Workflow chaining `pdf-extract` → `embed_shard` → `load_embeddings` — a directory of PDFs straight into Qdrant/ES. |
| `pdf-ingest.inputs.yml` | Example inputs (PDFs + collection + embedding endpoints). |
| `pdf-ingest-scatter.cwl` | **PDF plane, scatter-per-PDF (#203 Option A).** Scatter/gather: one PDF → one extract task → one `ingest_shard` task → **one receipt**, then merge. The only PDF workflow `GoWeBackend` can actually drive (below). |
| `pdf-ingest-scatter.inputs.yml` | Example inputs (3 PDFs + a `demo_*` throwaway collection). |
| `jats-ingest.cwl` | **JATS/OA plane (#301).** Hash-fanned PubMed-Central harvest → stores: scatter(`jats_extract` → `embed_shard`) chained per shard (no extract/embed barrier), then one gathered load. Shards come from `plan_shards.py` outside the workflow; the load step takes a **registry `collection_id` + the sqlite registry itself** (#263) — nothing in the DAG names a physical store. One chunk config (fixed_token 512/64) covers prose *and* the pre-capped table/figure units, so there is no second whole-doc pass and no ADR-0002 spec conflict. |
| `jats-ingest.inputs.yml` | Example inputs (dev tenant: `oa-dev` collection, `:24041`/`:24043` stores). |
| [`reports/oa-ingest-run.md`](../reports/oa-ingest-run.md) | **Run record** for the production open-access build: provenance, the exact reproduce commands, measured rates, and the six incidents (unindexed `doc_id`, single-endpoint embedding, GoWe scatter serialization, CWD-dependent doc ids, transient-LDAP batch failure, timeout-retry). Read before the next large ingest. |
| `../python/scripts/gowe_batch_ingest.py` | **JATS/OA batch driver.** Runs a whole shard plan through `jats-ingest.cwl` in bounded batches: submit → poll → verify against the stores (legs must agree; failed shards fatal; zero delta accepted as idempotent re-run) → delete that batch's staged `*.emb.jsonl` → append a resumable ledger. Batching is what pipelines batch N's load behind batch N+1's embed and caps intermediate disk (~50 GB/64-shard batch vs ~1.6 TB all-at-once). |

### Containerized runtime (#135)

Every step now runs inside the **`ragstack-worker` image** (`apptainer/ragstack-worker.def`
→ `apptainer/images/ragstack-worker.sif`; parallel `apptainer/Dockerfile` for
Docker hosts) via `DockerRequirement` (both `dockerPull:` **and**
`dockerImageId: ragstack-worker.sif` — see the gotcha below). The
`ragstack` package + its CPU-only deps (qdrant-client / httpx / elasticsearch<9 /
the HF tokenizer — **no torch**) come from the pinned image, and the scripts live
at `/opt/ragstack/scripts`. This **replaces the old
`InitialWorkDirRequirement: {pkgdir: ../python}` + PYTHONPATH staging** (delivering
path B below), which only worked next to a checkout and needed a ragstack-provisioned
host env.

Build + run:

```bash
apptainer build --sandbox /rag/tmp/ragstack-worker.sbx apptainer/ragstack-worker.def
apptainer build apptainer/images/ragstack-worker.sif /rag/tmp/ragstack-worker.sbx
# cwltool: --singularity resolves the SIF by filename from CWL_SINGULARITY_CACHE.
# APPTAINER_BIND/HF_HOME bind the tokenizer cache; steps reach the fleet via NetworkAccess.
CWL_SINGULARITY_CACHE=apptainer/images APPTAINER_BIND=/rag/cache HF_HOME=/rag/cache \
  cwltool --singularity cwl/pdf-ingest.cwl cwl/pdf-ingest.inputs.yml
```

The SIF is a build artifact (gitignored); the `.def`/`Dockerfile` are the source
of truth. cwltool does **not** expand `$(inputs...)` expressions inside
`DockerRequirement`, so the image is referenced by a bare filename (portable, not a
host path) rather than a CWL input; GoWe overrides it with a
`gowe:Execution.docker_image` hint.

Note the build uses the **sandbox route** (two `apptainer build` calls above)
because `--fakeroot` is not available to unprivileged users on the dev/deploy
hosts — building the SIF directly from the `.def` fails without it.

#### Declare **both** `dockerPull` and `dockerImageId`

Every `DockerRequirement` here carries the same bare filename under **two** keys:

```yaml
DockerRequirement:
  dockerPull: ragstack-worker.sif      # GoWe reads only this
  dockerImageId: ragstack-worker.sif   # cwltool --singularity needs this
```

Neither runner falls back to the other key, and each ignores the one the other
needs:

- **GoWe** (`internal/parser/parser.go`) reads *only* `dockerPull`. With just
  `dockerImageId` the workflow registers fine but every container step dies at
  execution with `execute: Apptainer execution requested but no docker image
  specified`. Found by a real submission of `pdf-ingest.cwl`: identical workflow,
  FAILED before, COMPLETED after adding `dockerPull`. GoWe keeps the bare name as
  a local image only because it ends in `.sif` (`resolveApptainerImage`); anything
  else is turned into `docker://<name>` — so don't drop the extension.
- **cwltool `--singularity`** treats a lone `dockerPull` as a *registry* reference.
  It looks for `<value>.sif` / `<value>.img` — i.e. `ragstack-worker.sif.sif` —
  and, not finding it, runs `singularity pull docker://ragstack-worker.sif`, which
  fails with `requested access to the resource is denied`. Only `dockerImageId`
  matches the bare filename found under `$CWL_SINGULARITY_CACHE`.

With both keys present cwltool takes the `dockerImageId` branch (verified: the
image is used from the cache, no pull attempted) and GoWe takes `dockerPull`.
Keep them in sync when the image name changes.

### Running these on a real GoWe engine — operational gotchas

Findings from an end-to-end `pdf-ingest.cwl` submission against the live engine.
These describe **the current deployment**, not permanent properties of GoWe —
re-check before relying on them.

**1. Deploy the SIF where the workers resolve it.** A GoWe worker is started with
`--image-dir <dir>` and joins the bare image name from `dockerPull` onto it. So
`dockerPull: ragstack-worker.sif` resolves to `<image-dir>/ragstack-worker.sif`
and the SIF must exist there on **every** worker host. The validated submission
used `/scout/containers/ragstack-worker.sif`. `CWL_SINGULARITY_CACHE` is a
**cwltool-only** variable — it has no effect on GoWe workers.

```bash
# after rebuilding, refresh the worker-visible copy
cp apptainer/images/ragstack-worker.sif /scout/containers/ragstack-worker.sif
```

**2. Worker group matters — a `--runtime none` worker cannot run container
steps.** GoWe only dispatches tasks carrying a `docker_image` to
container-capable runtimes. On the current deployment the **`ragstack-cpu` group
worker runs `--runtime none`** (path A above, the pre-image conda-env worker)
while the **default-group workers run `--runtime apptainer`**. Consequently a
submission of these now-containerized workflows must **not** set
`worker_group="ragstack-cpu"` (nor a `gowe:Execution.worker_group` hint naming
it) — leave the group unset so the task lands on an apptainer-capable default
worker, or the task will never be picked up. Correspondingly, leave
`GOWE_WORKER_GROUP` unset for `INGEST_BACKEND=gowe`. If a `--runtime apptainer`
worker is later added to the `ragstack-cpu` group, this restriction goes away.

**3. Workflows must be self-contained.** `GoWeClient.register_workflow` POSTs the
CWL **text**; there is no bundle upload, so an external `run: other-tool.cwl`
reference cannot be resolved engine-side. `pdf-ingest.cwl` therefore inlines all
three of its steps (`cwl/pdf-extract.cwl` remains as a standalone tool for direct
`cwltool` use — keep them in sync). Any new multi-step workflow intended for GoWe
must inline its tools too — and for the same reason it must not depend on any
**path relative to the CWL file**. `eval-scifact-chunking.cwl` still does (its
`evaldir` default is `{class: Directory, location: ../python/scripts/eval}`,
staged via `InitialWorkDirRequirement`, and it declares no `DockerRequirement`):
it is `cwltool`-only until it is containerized like the others. Not a regression —
it predates the image — but don't submit it to GoWe as-is.

**4. Input/output files must live under the server's `--upload-download-dirs`**
(and be readable by the worker host).

### Step 2 tools (bulk ingest)

- **`python/scripts/ingest_shard.py`** — scatter step. Ingests **one** JSONL shard
  → `receipt.json` (chunk ids + per-doc catalog), reusing `IngestionPipeline`
  (chunk→embed→quarantine→delete-prior→upsert→neighbor-link). **Stateless +
  idempotent**: no checkpoint/resume — the engine owns scatter/retry/resume, and a
  re-run overwrites in place (deterministic uuid5 ids + upsert), so a GoWe retry is
  safe. This is what retires `ingest_jsonl.py`'s bespoke machinery (#71) without
  forking the pipeline (#25). Needs live infra — not a CI step. The reusable core
  is `ragstack.ingestion.shard.run_shard` (offline-tested with in-memory stores).
- **`python/scripts/merge_receipts.py`** — gather step. Folds the per-shard
  receipts into a run summary (totals + **failed-shard ids**, so partial failure is
  surfaced, not silently under-ingested). Pure computation. `--fail-on-shard-error`
  makes it a gate. Verified end-to-end under `cwltool`.
- Receipt contract: `ragstack.ingestion.receipts` (`ShardReceipt`/`DocRow`) — the
  step's file output; the Qdrant/ES upsert is the side effect (#62). `DocRow.metadata`
  is the light `index_metadata` catalog subset (title/doc_type/doi/authors/year/…);
  it does **not** carry `ingest_jsonl.py --catalog-out`'s full enriched dump
  (citations/abstract are dropped).

### Runtime requirement (real GoWe workers)

Steps run inside the `ragstack-worker` image (see **Containerized runtime**
above), which carries the `ragstack` package + its deps — so neither source
staging nor a ragstack-provisioned host env is needed. `merge_receipts` is pure
stdlib+ragstack but runs in the same image for uniformity.

> **Historical:** earlier revisions staged `python/` via
> `InitialWorkDirRequirement` + `PYTHONPATH` and required the worker to already
> have `ragstack`'s deps (qdrant-client / httpx / elasticsearch / tokenizer) —
> the image now supplies both, retiring that hack.

### Decoupled embed/load (#141) — the capped-Qdrant path

`ingest-bulk.cwl` couples embed and upsert in one per-shard task. Under a
**capped Qdrant** (VMA-exhaustion workaround; see the incident doc), sustained
inline upserts drop connections and a Qdrant stall back-pressures onto the GPU
embedding. #141 splits the two so the GPU fleet is never blocked by the DB:

- **`embed-bulk.cwl` → `python/scripts/embed_shard.py`** — scatter step. Embeds
  **one** shard via `IngestionPipeline.embed_source` (load→chunk→embed→link) and
  writes a JSONL **embedding file** (`<shard>.emb.jsonl`). **No Qdrant/ES** — only
  the embedding fleet + tokenizer; the pipeline's stores are unused placeholders.
  Stateless + idempotent (deterministic ids, re-run overwrites the file).
- **`load-embeddings.cwl` → `python/scripts/load_embeddings.py`** — a **single**
  task (not a scatter) that reads the embedding files and upserts them via
  `IngestionPipeline.index_chunks` — the *same* delete-prior→upsert→index logic as
  the coupled pipeline, **no fork**. The collection dim is read from the embedding
  file **header** (a wrong-dim file — e.g. 768-d BGE into a 4096-d SFR collection —
  is rejected before any write). Load is a single task because **Qdrant
  backpressure belongs here** (throttle on live collection health), which is a
  stateful control loop, not a dataflow fan-out.
- **File contract:** `ragstack.ingestion.embedding_file` (`ragstack.embedding_file/v1`)
  — a versioned JSONL header + one embedded `Chunk` per line. This is the seam a
  future non-Python (Go) loader would depend on, not on the Python code.

**Backpressure is a follow-up, not in this scaffold.** #141's must-have — halting
upserts while Qdrant optimizes — lands as a `BackpressuredVectorStore` decorator
wrapping the pipeline's `vector_store`; `index_chunks` and `load_embeddings.py`
are unchanged when it arrives. Until then the load runs at full rate (safe on an
uncapped Qdrant). Like the other step tools, a real run needs live infra — the
`run_embed_shard`/`run_load_file` cores are unit-tested offline with a fake
embedder + in-memory stores (an end-to-end embed→file→load round-trip that
reconstructs the same store state as the coupled `ingest()`).

### PDF scatter-per-PDF (#203 Option A) — the driveable PDF workflow

`pdf-ingest.cwl` is **one shard per run**: every PDF lands in a single JSONL
shard, embedded once and loaded once, and the workflow emits no receipts. That is
fine for an operator running a batch by hand, but it cannot be driven by
`GoWeBackend`, which maps a `receipts` `File[]` output back to work items
**positionally** — a workflow with no `receipts` output makes a fully successful
run report *every item failed*.

`pdf-ingest-scatter.cwl` is the driveable shape: **one PDF = one work item = one
task chain = one receipt.**

```
pdfs: File[]  --scatter-->  extract (1 PDF -> 1 JSONL shard + report)
              --scatter-->  ingest  (1 shard -> Qdrant/ES + 1 ShardReceipt)
              --gather--->  merge   (receipts -> run summary)
```

Two deliberate choices:

- **The receipt comes from `ingest_shard.py`, not from `embed_shard.py`.** The
  decoupled halves exist so a stalling Qdrant can't back-pressure the GPU fleet,
  and `load_embeddings` is intentionally a *single* un-scattered task (that's
  where backpressure will live) — scattering it per PDF would contradict #141,
  and an embed-stage receipt would report `completed` for chunks that were never
  upserted. The coupled tool keeps `status`/`chunk_ids` honest and halves the
  per-PDF container starts.
- **`--shard-id` is the PDF's stem**, so each receipt names the document it came
  from rather than a staged temp basename.

Driving it from `GoWeBackend` needs **one non-default argument** — the scattered
input key, because this workflow's input is `pdfs`, not `shards`:

```python
GoWeBackend(client, cwl, workflow_name="ragstack-pdf-ingest-scatter",
            shards_input_key="pdfs",   # default is "shards" (ingest-bulk.cwl)
            worker_group=None,         # must stay unset — see "Worker group matters"
            static_inputs={"collection": ..., "embedding_url": [...], ...})
```

> **Not reachable from config yet.** `shards_input_key` is a constructor argument
> with no `Settings` field, and `make_ingest_backend` never passes it — so
> `INGEST_BACKEND=gowe` + `GOWE_WORKFLOW_CWL=cwl/pdf-ingest-scatter.cwl` alone
> would submit a `shards` input the workflow doesn't declare. Both `/v1/ingest`
> and `/v1/ingest/upload` also still return **501** for any non-local backend, and
> each `WorkItem.source` must be a path under the GoWe server's
> `--upload-download-dirs`. Those are the remaining gaps between this workflow and
> a browser upload (#203).

Failure semantics: `ingest_shard.py` exits non-zero on a failed shard, so a PDF
with no extractable text (scanned/image-only → empty shard → `EmptyIngestError`)
fails its task and therefore the submission. The extract step's `report` output
lists unextractable files — pre-filter on it rather than letting one bad file sink
a batch.

Validated end to end: `cwltool --singularity` over 3 corpus PDFs (3 receipts, 187
chunks in Qdrant **and** ES), and a live GoWe submission of the same three PDFs
staged under `/scout/wf/data`, submitted through `GoWeBackend` with no worker
group → `COMPLETED`, three `completed` `ItemResult`s with 31/82/74 chunk ids.

### Step 2b — submitting to a live GoWe engine

- **`ragstack.ingestion.gowe_client.GoWeClient`** — async REST wrapper over the
  GoWe API: `register_workflow` → `submit` → `wait` → `download`. **Auth is a
  BV-BRC token** (anonymous submission is disabled on the deployed server); it's
  loaded from `$GOWE_TOKEN`/`$BVBRC_TOKEN` or the standard token files
  (`~/.gowe/credentials.json`, `~/.patric_token`, …) and sent verbatim in the
  `Authorization` header. Validated end-to-end against the live server (register a
  workflow, submit with the token, poll to COMPLETED on a worker, download the
  output) — see the `GOWE_LIVE=1` round-trip test.
- **`ragstack.ingestion.gowe_backend.GoWeBackend`** — satisfies the in-process
  `IngestBackend` protocol: `run_shards` submits the shards' source files as a
  scatter workflow (this `ingest-bulk.cwl`, whose `receipts` output it collects),
  waits, downloads the per-shard receipts, and maps them to `ItemResult`s
  (positionally — CWL scatter preserves order). Pass `worker_group=` to route the
  run to a dedicated worker (below); each `WorkItem.source` is a shard file GoWe's
  workers can read.

### Running `ingest_shard` on GoWe — the dedicated-worker path (#135)

`ingest_shard` needs ragstack's *dependencies* (qdrant-client/httpx/elasticsearch/
tokenizers), which a stock apptainer worker container lacks. Two ways to provide
them:

- **(A, validated) A `--runtime none` worker in the ragstack conda env.** Start a
  dedicated worker whose `python` is the ragstack env's, in its own group, and
  route ingest tasks to it. No image to build; deps come from the env, ragstack
  code from the CWL's staged `python/` (`PYTHONPATH`). **Proven end-to-end**: an
  `ingest_shard` task ran on such a worker (deps + ragstack resolved, embedded via
  the BGE sidecar, wrote a receipt).

  ```bash
  # `python` on the worker's PATH must be the ragstack env's (deps live there);
  # --runtime none runs the tool directly on the host (no container). HF_HOME points
  # the tokenizer cache at a SHARED persistent dir — see the gotcha below.
  PATH="/rag/envs/ragstack/bin:$PATH" HF_HOME=/rag/cache gowe-worker \
      --server http://localhost:8091 --runtime none \
      --name ragstack-cpu-1 --group ragstack-cpu \
      --workdir /scout/wf/data/ragstack-workdir --stage-out file:///scout/wf/data
  ```
  > **Superseded by (B).** Now that every step declares a `DockerRequirement`,
  > routing to this `--runtime none` group makes the task *undispatchable* — see
  > "Worker group matters" above. Don't set `worker_group="ragstack-cpu"`.

  Route to it with `GoWeBackend(..., worker_group="ragstack-cpu")` (sends a
  submission `worker_group` label — it propagates to every scatter + merge task) or
  a `gowe:Execution.worker_group` CWL hint (which wins over the label). **The label
  selects the *group*, not the *executor*** — it assumes the server runs
  `--default-executor worker`; on a `--default-executor local` server the group is
  ignored and the tool runs on the *server* host instead.

  Gotchas with `--runtime none`: the tool runs with **`HOME`=the task workdir** (an
  ephemeral per-task dir) and a per-task `TMPDIR`. That matters for the tokenizer:
  the default chunk method `fixed_token` **requires** the HF offset tokenizer
  (`--chunk-token-counter estimate` is force-reverted to `hf` for token-window
  chunking, so it does *not* skip the download), and the default HF cache is
  `$HOME/.cache/huggingface` — so with `HOME`=workdir the model tokenizer would
  re-download **every task**. Set a shared **`HF_HOME`** (as above) so it's fetched
  once and reused. (`estimate` only helps the char/word/sentence methods, and isn't
  even wired through `ingest-bulk.cwl`.) Input/output files must live under the
  server's `--upload-download-dirs`.

- **(B, DELIVERED #135) A ragstack-provisioned worker SIF.** `apptainer/ragstack-worker.def`
  → `apptainer/images/ragstack-worker.sif`, referenced via `DockerRequirement`
  (`dockerPull:` + `dockerImageId:` — see the gotcha above)
  (`gowe:Execution.docker_image` on GoWe) — the reproducible/portable production
  path (multi-host). CPU-only (**no torch**: the steps call the embedding fleet
  over HTTP), so it's lean (~170 MB). This is now the default the CWL steps use;
  see **Containerized runtime** above.

**Wired in:** `ragstack.ingestion.backends.make_ingest_backend` selects the
`ShardedIngestor`'s backend from config — `INGEST_BACKEND=local` (default,
in-process) or `INGEST_BACKEND=gowe`, which builds a `GoWeBackend` from
`GOWE_URL` / `GOWE_TOKEN` / `GOWE_WORKFLOW_CWL` / `GOWE_WORKFLOW_INPUTS_JSON` /
`GOWE_WORKER_GROUP`. In `gowe` mode each manifest item's `source` is a **shard
file** the workers read (a JSONL shard for `ingest_shard.py`), not an arbitrary
document — build the manifest from pre-sharded files. (Transparently sharding
arbitrary `/v1/ingest` documents to GoWe is a separate follow-up.)

The two step tools live in the ragstack package's script tree:

- **`python/scripts/eval/chunk_one.py`** — scatter step. Ingests + scores **one**
  chunking config against SciFact (BEIR) and emits `metrics.json` (per-query metric
  arrays + means). A thin CLI over the existing `scifact_chunk_eval` harness — no
  reimplementation of the chunk/embed/ingest/score logic.
- **`python/scripts/eval/aggregate_stats.py`** — gather step. Reads the per-config
  `metrics.json` files and writes `report.md` (metrics table + paired-bootstrap
  difference CIs + Holm-corrected Wilcoxon), reusing the harness's assemblers and
  the `_stats` layer. Pure computation — no GPU/store/network.

## Running it

Each step stages `python/scripts/eval/` into its job sandbox via
`InitialWorkDirRequirement`, so the workflow is **CWD-independent and portable**
across CWL runners — no PATH or working-directory assumptions:

```bash
. /rag/bin/activate            # ragstack env + endpoints on PATH
cwltool cwl/eval-scifact-chunking.cwl cwl/eval-scifact-chunking.inputs.yml
```

On GoWe, submit the same document (local or Apptainer backend). GoWe owns scatter,
retry, and — for the future bulk-ingest workflow — checkpoint/resume, subsuming
the bespoke machinery in `ingest_jsonl.py` (#71).

The `aggregate` step is verified end-to-end under `cwltool` (it's pure
computation); the full workflow is `cwltool --validate`-clean.

### Requirements & caveats

- **`ragstack` must be importable** in the runtime env (installed in the conda env,
  or present in the run SIF) — the staged tools `import ragstack.*`.
- **`chunk_one` needs live infra**: the SFR embedding fleet + Qdrant + ES. It
  ingests into isolated `scifact_m7_<config>` stores and tears them down (the
  prefix-guarded teardown never touches a production collection). It is **not** a
  CI step — same as the harness it wraps. `aggregate_stats` *is* CI-friendly.
- **Optional follow-up**: packaging the tools as `ragstack.eval` console-scripts
  would let the `baseCommand` be a bare command (no dir-staging) — a nicety, not a
  blocker now that the steps stage the dir. Tracked issue linked from the PR.

## Roadmap (ADR-0001 rollout)

1. **Eval CWL (this)** — scatter/gather over chunking configs.
2. **`GoWeBackend` for bulk ingest** — `ingest_shard` per-shard tool + receipt
   files + a `GoWeBackend` implementing the existing `IngestBackend` seam. Retires
   the #71 resume machinery and dissolves the #25 pipeline duplication.
3. Go embedding-router sidecar; 4. Go API gateway (see the ADR).
