# Multi-instance Qdrant routing (and the path to a sharded cluster)

## Why

The `vm.max_map_count` (VMA) limit is **per process**, and Qdrant memory-maps every
indexed segment's vector storage into ~15–58 MB chunk files — **~1 VMA each, ~1,140
VMAs per million points** (measured on `coconut`). No collection config avoids it:
`vectors.on_disk` true *or* false, and any `memmap_threshold`, all still mmap indexed
vectors (verified empirically). So a single Qdrant process has a hard ceiling on total
indexed points across *all* its collections.

On `coconut` (`vm.max_map_count=65530`, un-raisable without root for now):

| collection | points | VMAs |
|---|---|---|
| `ragstack_sfr_tok256` | 24.8M | 27.6k |
| `ragstack_sfr_tok512` | 12.6M | 14.4k |
| baseline (threads/anon) | — | ~8k |
| **full `ragstack_sfr_semantic`** | **34.6M** | **~38k** |

tok256 + tok512 + baseline already sit at ~55.8k. Adding full semantic (~38k) would
reach ~90k ≫ 65,530 → the Qdrant VMA-exhaustion crash (see
`/rag/documents/vma-exhaustion-incident-2026-07-04.md`). semantic **cannot** coexist
with tok256+tok512 in one process.

## The fix: route a collection to its own process

Because the VMA budget is per-process, a **second Qdrant instance** (own PID) gets a
fresh 65,530. semantic (~38k + ~8k baseline ≈ 46k) fits comfortably there while
tok256/tok512 stay put on instance 1 — **no migration of the 37M existing points.**

The API resolves each collection to its instance via one config field:

```jsonc
// QDRANT_COLLECTION_ROUTES (JSON env). A collection not listed uses QDRANT_URL.
{ "ragstack_sfr_semantic": "http://localhost:6343" }
```

`deps._qdrant_url_for(collection)` returns the routed URL (else `qdrant_url`), and
`_build_vector_store` connects there. Empty routes ⇒ single-instance, byte-for-byte
unchanged. Tenancy is unaffected — tenants remain a payload filter *within* a
collection, so it composes: tenant → (its) collection → instance.

### Operating it

- **Instance 2** (`coconut`): `bringup_qdrant2.sh` — same optimizer cap as instance 1
  (`OPTIMIZER_CPU_BUDGET=12`; same host, so an uncapped bulk build would still
  VMA-crash), ports 6343/6344, data `/rag/data/qdrant2`.
- **Ingest** (CLI) targets the instance directly: `qdrant_ingest_agent.py
  --qdrant-url http://localhost:6343`.
- **Serving** (API): set `QDRANT_COLLECTION_EXPLICIT=ragstack_sfr_semantic` +
  `QDRANT_COLLECTION_ROUTES` as above.
- Elasticsearch is **not** VMA-constrained (separate service) → BM25 stays on the one
  ES instance; routing is vector-only. If ES ever needs splitting, mirror this field.

## The migration path: → a sharded cluster

Two independent instances is a *static* split. The scalable end state is a native
**Qdrant cluster** where collections are sharded across nodes (each node a process with
its own VMA budget → per-node VMAs ≈ total/N), with a unified query API (Qdrant fans
shards out internally). The blocker to going there directly is that the existing
single-node tok256/tok512 (37M points) would have to be re-created as sharded
collections — a big-bang migration.

**The routing table is the migration lever — cut over one collection at a time:**

1. Stand up a **sharded cluster as instance 3** (2+ nodes, `QDRANT__CLUSTER__ENABLED`,
   Raft bootstrap) for testing — separate from the serving instances.
2. Build/copy a collection into the cluster as a **sharded** collection
   (`shard_number = N`): snapshot-restore or re-ingest from the durable embed-to-file
   shards (the `#141` embed output is reusable — re-drain into the cluster).
3. **Cut over** that collection by pointing its route at the cluster URL:
   `{"ragstack_sfr_semantic": "http://cluster-node:6333"}`. Verify, then retire its
   old instance. Reversible — flip the route back if needed.
4. Repeat per collection (semantic first, then tok512, tok256) until everything is on
   the cluster; then the per-collection routes collapse to a single cluster `qdrant_url`.

This turns a risky big-bang into a per-collection, reversible rollout gated by the same
config field, with no serving downtime.
