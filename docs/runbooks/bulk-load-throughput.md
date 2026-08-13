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
