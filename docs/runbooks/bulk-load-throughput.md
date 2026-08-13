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
2. Rebuild the worker image from the merged tree and place it where the CWL
   resolves `ragstack-worker.sif`.
3. Verify the image actually has the flags before submitting a real batch:

   ```
   <run the worker image> python /opt/ragstack/scripts/load_embeddings.py --help \
     | grep -E "no-delete-prior|file-concurrency|bulk-refresh"
   ```

   Three matches or stop. This one check prevents the whole failure mode above.

## Settings to enable, and when each is safe

| flag | when to use | when NOT to |
|---|---|---|
| `bulk_refresh: true` | always, for a bulk load | when something must search the index live during the load |
| `file_concurrency: 2` | start here and raise only against measured **free memory** | see the memory note below — this is not a free dial |
| `no_delete_prior: true` | **only** a replay from unchanged embedding files | any batch whose inputs were re-extracted or re-chunked |

### `file_concurrency` costs memory, and multiplies the other knobs

Each in-flight file is read **fully into RAM**. Measured on a production shard:
a 1.33 GB embedding file leaves the loader at **6.1 GB resident** — roughly a
4.6x expansion, dominated by Python float lists. So peak loader memory is
about **6 GB per concurrent file**.

Size it against free memory, **not** against store headroom. The stores have
plenty of CPU headroom, which is what makes concurrency tempting; the binding
constraint is elsewhere. Overshooting does not fail loudly — it evicts the page
cache the production stores read through, and shows up as query latency on an
unrelated service.

It also **multiplies** the other concurrency settings, because the delete
semaphore is created per `index_chunks` call rather than per pipeline:

```
effective deletes  = file_concurrency x delete_concurrency   (4 x 8  = 32)
effective upserts  = file_concurrency x upsert_concurrency   (4 x 4  = 16)
```

So raising `file_concurrency` from 1 to 4 does not merely quadruple file
throughput — it quadruples the load on both stores at the same time. Raise one
dial at a time and measure.

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
file_concurrency: 2         # ~12 GB peak loader RSS; raise only against free memory
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
- [ ] **Loader peak RSS**, against free memory — expect ~6 GB per concurrent
      file. If page cache shrank materially, lower `file_concurrency`; the cost
      lands on unrelated services, not on this load, so it will not announce
      itself.
- [ ] The store did not become the new bottleneck: if the vector store
      saturates, lower `file_concurrency` before anything else.

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
