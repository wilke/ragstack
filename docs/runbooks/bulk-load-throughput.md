# Enabling the bulk-load throughput changes (#323)

Written to be executed at a **batch boundary**, never under a running load.

The load stage is serialized, not resource-bound. Measured during a live
64-shard load on an otherwise idle host: 384 cores at load average 3.85, disks
under 8% utilization, the vector store using 12.6 cores, the text index at 0%
CPU with an empty write queue — and the loader pinned under a single core.

## The gating step: rebuild the worker image

The load runs **inside** the worker container (`baseCommand: [python,
/opt/ragstack/scripts/load_embeddings.py]`). The new flags do not exist until
the image carries the new code. Rebuilding is therefore step one, not an
afterthought — passing `--no-delete-prior` to an old image fails the task with
an unrecognized-argument error, which in a 64-shard batch means 64 failed tasks.

1. Merge #324.
2. Rebuild the worker image from the merged tree.
3. **Install it where the workers actually resolve it — not where the repo keeps
   it.** The CWL names the image bare (`dockerPull: ragstack-worker.sif`), and it
   is resolved by the worker's `--image-dir`, not by the checkout. The repo copy
   under `apptainer/images/` is *not* what a submitted batch runs. Check the
   running worker's own command line for its `--image-dir` and install there.
   Getting this wrong is silent: the batch runs happily on the old image and the
   new flags simply never take effect.

   **There is now more than one image dir.** Because the CWL names the image
   bare, `--image-dir` is the only way to give one worker group a different
   image, and the `ragstack-hackathon` group uses that: it resolves
   `/scout/containers/ragstack-hackathon/ragstack-worker.sif` (built with the
   `postgres` extra for that tenant's registry — see #563), while the `ragstack`
   group still resolves `/scout/containers/ragstack-worker.sif`. Installing a
   rebuild into one does nothing for the other. `ps -eo args | grep gowe-worker`
   lists every group's `--image-dir`; update each one you mean to move.

   The per-tenant group exists because of the *second* half of #563: a worker
   group also carries exactly one collection registry, so pinning the shared
   `ragstack` group at the dev tenant's sqlite registry broke every `hackathon`
   ingest. That half is now fixed in the workflows (`registry`, below) and the
   per-tenant group is no longer required for registry reasons — but it is still
   required for **image** reasons while only one image dir carries the `postgres`
   extra. Collapse the groups only once every image dir a tenant might land on
   has that extra.

   **`GOWE_TOOL_IMAGE` pins the image per tenant instead ([#614](https://github.com/wilke/ragstack/issues/614)).**
   Set it in a tenant's env (e.g. `GOWE_TOOL_IMAGE=ragstack-worker-v1.6.3.sif`)
   and the API substitutes that name for `ragstack-worker.sif` in every
   `dockerPull` (and its `dockerImageId` twin) of every workflow it registers —
   ingest, graph-extract and restore — before POSTing the text. Versioned images
   then coexist in one `--image-dir`: stage the new file next to the old one,
   point one tenant at it, restart that tenant's API. Nothing else moves, and a
   submission already in flight keeps the image its registered text names. Once
   a tenant sets `GOWE_TOOL_IMAGE`, **the worker group is no longer that tenant's
   version knob** — installing a rebuild as `ragstack-worker.sif` in its group's
   dir changes nothing for it; the file it names must exist in *every* image dir
   its group's workers resolve. A missing `.sif` is not detected at registration
   or at dispatch: apptainer fails fast, the task retries 3× and the job FAILS
   in about 16 s with the resolved image path in its stderr — so a wrong pin
   surfaces as the tenant's first failed job, not at boot. Empty keeps today's
   behaviour (the bare `ragstack-worker.sif`, resolved by the group). The value
   must be a bare filename ending in `.sif`, at most 255 bytes; anything else
   fails the boot. An API that finds no `dockerPull: ragstack-worker.sif` to
   replace logs a warning naming the setting; one that could rewrite only some
   of a workflow's image sites (a flow mapping, a list item, a value on the next
   line) refuses, naming the residual lines, rather than run those steps
   unpinned. **The pin covers only what the API registers:**
   `python/scripts/gowe_batch_ingest.py` and a hand-run `gowe submit <cwl>`
   bundle the CWL from disk as written, so the bulk/JATS plane is not pinned and
   still runs whatever `ragstack-worker.sif` its group resolves. Open
   question, not yet verified against the engine: whether GoWe resolves the name
   at dispatch or caches it at registration — confirm before relying on two
   tenants running different images side by side.
4. **Never overwrite the image in place while a load is running** — a container
   is mapped to that file. Stage it under a versioned name and swap at a batch
   boundary. (An atomic `mv` on the same filesystem preserves the running
   process's inode and is safe in principle; staging and swapping at a boundary
   removes the need to rely on that.)
5. Verify the image actually has the flags before submitting a real batch:

   ```
   <run the worker image> python /opt/ragstack/scripts/load_embeddings.py --help \
     | grep -E "no-delete-prior|file-concurrency|bulk-refresh"
   ```

   Three matches or stop. This one check prevents the whole failure mode above.

## Which collection registry the load resolves against (#563)

`load_embeddings.py` and `ingest_shard.py` resolve the collection through the
**collection registry** to get the physical store names and the build spec. They
used to learn WHICH registry from the worker's own environment
(`COLLECTION_STORE_BACKEND` / `_PATH` / `_DSN`), which `gowe-worker` is given
once per **worker group** — so one group served one tenant, and a shared group
pointed at the wrong tenant's registry failed every task with

```
physical store '…' is claimed by no registry entry (sqlite:/rag/data/tenants/dev/…)
```

*after* extract had already run. Each affected job died half-done.

The write workflows now take an optional **`registry`** input carrying a NAME,
which the tool resolves against per-registry variables in its own container:

| variable | who sets it | where |
|---|---|---|
| `COLLECTION_STORE_BACKEND_<NAME>` | operator | `gowe-worker --env-file` (not a secret) |
| `COLLECTION_STORE_PATH_<NAME>` | operator | `--env-file` (a path, not a secret) |
| `COLLECTION_STORE_DSN_<NAME>` | operator | **`--secret-file` only** |
| `registry` (the name) | the tenant API, per job, from `COLLECTION_REGISTRY_NAME` | the submission |

There is deliberately **no `RAGSTACK_COLLECTION_REGISTRY` env fallback** for
`--registry`. On a shared worker group an ambient default would silently pick
one tenant's registry for every job that omitted the input, and would override
`jats-ingest.cwl`, whose tool binds no `--registry` because it pins
`COLLECTION_STORE_*` itself per submission. Do not add one.

`<NAME>` is simply the registry name uppercased. Registry names are
**lowercase letters, digits and `_` only** (`^[a-z0-9][a-z0-9_]{0,63}$`);
`Hackathon`, `prod-eu` and `prod.eu` are refused rather than folded, because
folding them would let two names that read as different tenants resolve to the
same `COLLECTION_STORE_*_<NAME>` variables.

**The suffixed variables are read from the process environment only.** The
unsuffixed `COLLECTION_STORE_*` also come from a `.env` file (pydantic's
`env_file`); the per-registry ones do not — they are looked up with `os.getenv`
in the tool. That is right for a container, where the worker injects them as
real environment variables, and surprising on the host: testing a `--registry`
invocation from a checkout with the values only in `.env` will report the
registry as unconfigured. `export` them, or use `env VAR=… python …`.

Two rules that are not stylistic:

* **The DSN may only ever come from `--secret-file`.** `gowe-worker` logs
  `--env-file` values in clear at INFO and `--secret-file` names only; and a
  workflow input is worse than either, because `submitted_inputs` on a GoWe
  submission is an immutable snapshot rendered by the UI and stored in the
  engine's SQLite in plaintext. A DSN placed there could never be withdrawn.
* **An unconfigured name fails loudly.** It does not fall back to the unsuffixed
  `COLLECTION_STORE_*` — a silent fallback to another tenant's registry is the
  outage. Omit `registry` entirely and you get the old behaviour, unchanged.

Verify before submitting a real batch, the same way you verify the flags above:

```
<run the worker image> python /opt/ragstack/scripts/load_embeddings.py --help | grep -- --registry
```

One match or the image predates #563 and will reject the input.

## Settings to enable, and when each is safe

| flag | when to use | when NOT to |
|---|---|---|
| `bulk_refresh: true` | always, for a bulk load | when something must search the index live during the load |
| `file_concurrency: 1` | leave it at 1 | **do not raise it** — see below (#328) |
| `no_delete_prior: true` | **only** a replay from unchanged embedding files | any batch whose inputs were re-extracted or re-chunked |

### `file_concurrency` — leave it at 1 (#328)

**Do not raise this.** An earlier revision of this runbook recommended `2` and
estimated ~6 GB of loader memory per concurrent file. That estimate was wrong by
more than an order of magnitude, and the recommendation broke a real batch.

Measured on a 64-file batch, same image and same other flags:

| setting | loader RSS | outcome |
|---|---|---|
| `file_concurrency: 2` | **142.5 GB**, one core pinned | 2 files loaded, **22 failed** |
| `file_concurrency: 1` | **4.8 GB** | steady, no failures |

That is ~30x the memory for 2x the concurrency — so it is not "two files in
flight", something retains nearly every file in the batch.

**The failure mode is actively misleading.** It surfaces as the vector client's
`ResponseHandlingException` with an empty message, which reads as a store
outage. It is not: throughout the failures the vector store was `green`,
`optimizer_status: ok`, answering in 0.01 s and absent from the host's top CPU
consumers. Do not go debugging the store — check the loader's RSS first.

Note also that concurrency **multiplies** the other knobs, since the delete
semaphore is per `index_chunks` call rather than per pipeline: N files means
N x `delete_concurrency` deletes and N x `upsert_concurrency` upserts in flight.
Another reason not to reach for it.

`no_delete_prior` and `bulk_refresh` are unaffected and both behave as intended;
the serial run with both enabled is stable and materially faster than the
unflagged path. Only file concurrency is held back.

### Stopping a load: use SIGINT, never SIGTERM

`bulk_refresh` parks the text index's refresh interval and restores it in a
`finally`. **`finally` does not run on SIGTERM**, so a `kill` (or `pkill`) leaves
the index with `refresh_interval: -1` — it silently stops refreshing, and every
count-based check reads stale from then on.

Use `kill -INT` so the restore runs. If a load was killed any other way, or
crashed, set it back by hand and force one refresh before trusting any count.
Verify with the index settings endpoint; the value should read as the default,
not `-1`.

`no_delete_prior` deserves the emphasis. It is safe precisely when chunk ids
cannot have moved — the load reads ids *from* the embedding file rather than
recomputing them, so the delete removes exactly what the upsert is about to
rewrite. It is unsafe the moment boundaries shift, because the old chunks then
survive as orphans under ids nothing will overwrite. A prior defect in this
repository produced exactly that divergence, which is why this is opt-in per run
and never inferred from the file looking unchanged.

Rule of thumb: **fresh batch → leave it false. Re-running a batch you already
loaded from the same staged files → true.**

## Enabling

Add to the driver's inputs template:

```yaml
bulk_refresh: true
file_concurrency: 1         # leave at 1 — see #328, do not raise
no_delete_prior: false      # true ONLY for an id-stable replay
```

Then restart the driver as usual. It skips `done` batches, so a restart is
always safe.

## Verify on the first batch, before trusting it

- [ ] Task log shows `refresh_interval parked` at the start and
      `refresh_interval restored + index refreshed` at the end. **If the restore
      line is missing, set the refresh interval back manually** — the index will
      not refresh on its own until you do, and search results will silently
      stop updating.
- [ ] Both legs match at rest. They should now converge *during* the load too,
      not only at the end — the legs are gathered rather than sequential, so a
      vector-count lead is no longer expected mid-batch.
- [ ] Batch wall time against the ~6 h steady-state norm.
- [ ] **Loader RSS stays flat near ~5 GB.** A serial load plateaus there. If it
      climbs across files, stop — that is #328, and the store errors it produces
      will point you at the wrong subsystem.
- [ ] Refresh interval is back to the default after the run, not `-1`.

## Rollback

Every change is opt-in. Remove the three keys from the inputs template and the
behaviour is byte-for-byte what it was — no redeploy, no image rebuild.

## Related, deliberately not in this change

- The vector store holds 4096-dimensional float32 with no quantization and no
  memory-mapping threshold, so vectors are fully resident. Projected at the full
  corpus this is roughly 777 GB of vectors alone, against 1.5 TB of host memory
  shared with the page cache the production stores read through. Decide on
  scalar quantization before the remaining batches land.
- The text index has 1 replica configured on a single-node cluster, so shards
  are permanently unassigned and cluster health is stuck yellow. Set it to 0.
- The index is a single shard for a projected ~47M documents. Not changeable
  without a reindex; note it for the next collection build.
