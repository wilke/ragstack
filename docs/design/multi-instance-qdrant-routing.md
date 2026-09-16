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
- Elasticsearch is **not** VMA-constrained, so the ES table below exists for a
  different reason — placement, not a per-process ceiling.

## The text leg: `ES_COLLECTION_ROUTES`

The BM25 half has the same table, `ES_COLLECTION_ROUTES`, and the same semantics:

```jsonc
// ES_COLLECTION_ROUTES (JSON env). An index not listed uses ELASTICSEARCH_URL.
{ "ragstack_sfr_semantic": "http://localhost:9243" }
```

`store_routing.es_url_for(index, settings)` answers it,
`deps._build_text_index_for` connects there, and empty routes ⇒ byte-for-byte
today's behaviour. Everything that must name a per-collection text store resolves
through that one function: the API's own BM25 leg, the `es_url` seeded into a GoWe
ingest submission, the bulk CLIs' `IngestTarget.es_url`, and `store_inventory`'s
text claims.

**Why, given ES is not VMA-constrained.** Not a ceiling — placement. A tenant can
point a collection's text leg at a cluster that already holds the index (a shared
corpus, a pre-built index, the cluster with the disk or the heap for it) instead of
copying it, so a collection's vector and text halves can each live wherever they
already are. And, like the Qdrant table, it is the per-index, reversible cut-over
lever onto a new cluster.

**The key is the physical INDEX name, not the collection id** — deliberately
asymmetric with the Qdrant table, whose key is the physical *collection* name. Each
table is keyed by the store its leg actually addresses; for the text leg that is
whatever `text_index` / `es_index()` resolves to, which is not the id (a blank
`text_index` falls back to the Qdrant collection name, and several ids may
deliberately alias one index). Keying on the id would route one alias and strand
the rest on the default cluster — one corpus, silently split across two.

**A routed store is shared state.** A route names an instance this deployment does
not own and whose other readers no registry here can enumerate, so
`DELETE /v1/collections/{id}?purge=true` refuses (409) when either leg is routed,
the way it already refuses a store another registry id serves. `purge=false` still
works and is how a routed collection is released: the binding goes, the store stays.

**Known gap.** The `restore-collection` submission seeds the bare
`QDRANT_URL`/`ELASTICSEARCH_URL` — the gate is built once at startup, before any
record exists, so neither leg is routed there. Restoring a routed collection aims
the worker at the default instances; `COLLECTION_RESTORE_INPUTS_JSON` is the
override until both legs are resolved per record inside
`CollectionRestorer.inputs_for`.

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
