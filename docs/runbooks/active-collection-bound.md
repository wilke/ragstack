# The active-collection bound `n` — measured per-collection cost at 35k chunks

Part 1 of #359 (part of #353). This runbook records **how** the per-collection cost
of a *loaded* collection was measured on the development tenant's stores, the
**numbers**, and the **derivation** of the active bound `n` that `MAX_COLLECTIONS`
should be set from. ADR-0003's figures were taken on *empty* collections; #353 asked
for the loaded curve. Measured 2026-08-24 on the dev deployment — Qdrant 1.18.0 (single
node, one instance per tenant), Elasticsearch 8.13.4 (single node, 1 GiB heap).

The three ceilings, per #353/#359: memory mappings vs `vm.max_map_count` and threads
vs the process limit, each budgeted at 60 %; resident RAM vs the tenant's share. `n` is
the minimum of the three.

## Headline

| | |
|---|---|
| Per-collection cost at 35k chunks (Qdrant) | **+306 memory mappings, +0.8 threads, +704 MiB RSS, +1 fd, 1,107 MiB on disk** |
| Per-collection cost at 35k chunks (ES) | **+2 shards (1 primary + 1 unassigned replica), +47 MiB RSS, +30 MiB store, ~0 threads, heap flat** |
| Binding ceiling | **RAM** (assumed 200 GB share ÷ 751 MiB per collection) → **n = 254** at the full share, **152** with the same 60 % budget the other ceilings get |
| Next ceilings | ES shards 300 (60 % of `max_shards_per_node`), Qdrant mappings 514, threads ≫ 10⁶ |
| Recommended `MAX_COLLECTIONS` for the dev tenant | **keep 100 now; ~150 is defensible without further measurement; 250 only once the share is stated and the RSS split is measured** (see below) |
| Cold build of one 35k-chunk collection | **108.6 s mean** wall (107–112 s), of which **~101 s store-side** (create + upsert + settle) and ~8 s client-side vector generation |

## Measurement table

Ten collections `nmeasure_1 … nmeasure_10`, 35,000 points each, built one after another
on the dev tenant's Qdrant and ES. Every row is a sample taken from the host once the
optimizer reported the collection green and ES had no running merges (plus 10 s).
Baseline (row 0) holds the tenant's two pre-existing collections; "10-late" is a repeat
sample a few minutes after the last build, "after-cleanup" is after deleting all ten.

| after N | Qdrant maps | Qdrant threads | Qdrant RSS (MiB) | Qdrant fds | Qdrant on-disk `nmeasure_*` (MiB) | ES maps | ES threads (`/proc`) | ES JVM threads | ES RSS (MiB) | ES heap used (MiB) | ES shards | ES `nmeasure_*` store (MiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 9,536 | 1,548 | 2,113 | 1,565 | 0 | 9,585 | 2,348 | 2,034 | 2,709 | 516 | 4 | 0 |
| 1 | 9,907 | 1,551 | 2,768 | 1,566 | 1,018 | 10,860 | 2,664 | 2,350 | 3,011 | 234 | 6 | 29 |
| 2 | 10,219 | 1,553 | 3,464 | 1,567 | 2,138 | 10,879 | 2,665 | 2,349 | 3,006 | 424 | 8 | 59 |
| 3 | 10,523 | 1,555 | 4,157 | 1,568 | 3,258 | 10,914 | 2,671 | 2,349 | 2,997 | 387 | 10 | 89 |
| 4 | 10,817 | 1,555 | 4,875 | 1,569 | 4,378 | 10,969 | 2,674 | 2,349 | 3,107 | 396 | 12 | 118 |
| 5 | 11,118 | 1,555 | 5,584 | 1,570 | 5,498 | 10,992 | 2,676 | 2,350 | 3,154 | 410 | 14 | 148 |
| 6 | 11,413 | 1,555 | 6,301 | 1,571 | 6,618 | 11,003 | 2,669 | 2,351 | 3,164 | 395 | 16 | 178 |
| 7 | 11,710 | 1,556 | 7,012 | 1,572 | 7,706 | 11,014 | 2,667 | 2,350 | 3,170 | 395 | 18 | 207 |
| 8 | 12,005 | 1,556 | 7,725 | 1,573 | 8,826 | 11,025 | 2,665 | 2,350 | 3,174 | 396 | 20 | 237 |
| 9 | 12,300 | 1,556 | 8,442 | 1,574 | 9,946 | 11,036 | 2,668 | 2,350 | 3,179 | 398 | 22 | 267 |
| **10** | **12,595** | **1,556** | **9,153** | **1,575** | **11,067** | **11,047** | **2,672** | **2,350** | **3,178** | **399** | **24** | **297** |
| 10-late | 12,595 | 1,556 | 9,153 | 1,575 | 11,067 | 11,047 | 2,664 | 2,350 | 3,178 | 399 | 24 | 297 |
| after-cleanup | 9,669 | 1,550 | 2,099 | 1,565 | 0 | 10,937 | 2,665 | 2,351 | 3,151 | 427 | 4 | 0 |

Every collection ended green with 35,000 points in 7–8 segments,
`indexed_vectors_count` 34,560–35,000 (so the HNSW graphs *were* built — the optimizer
merged the small segments past `indexing_threshold`), 1,018–1,120 MiB on disk. Every ES
index: 35,000 docs, 29.7 MiB, 1 primary + 1 replica, yellow.

The curve is linear — the step-to-step deltas are flat from 1 to 10 (Qdrant maps
+294…+371, RSS +655…+717 MiB). Deleting the ten collections returned Qdrant to within
133 mappings, 2 threads and −14 MiB of the baseline; ES kept its warmed-up thread pools
(see below) and ~440 MiB RSS, which is JVM/page-cache slack, not a leak.

### Cold-build wall time per collection

| collection | create (s) | Qdrant upsert (s) | ES bulk (s) | client-side generation (s) | Qdrant settle to green (s) | ES refresh+flush (s) | store-side (s) | total wall (s) |
|---|---|---|---|---|---|---|---|---|
| nmeasure_1 | 0.4 | 92.0 | 3.6 | 7.6 | 8.0 | 0.6 | 104.7 | 112.3 |
| nmeasure_2 | 0.3 | 89.7 | 3.1 | 7.8 | 6.0 | 0.6 | 99.6 | 107.4 |
| nmeasure_3 | 0.3 | 89.5 | 4.1 | 7.8 | 6.0 | 0.6 | 100.5 | 108.3 |
| nmeasure_4 | 0.3 | 89.4 | 4.2 | 7.8 | 6.0 | 0.8 | 100.8 | 108.6 |
| nmeasure_5 | 0.4 | 88.9 | 4.1 | 8.3 | 6.0 | 0.9 | 100.3 | 108.6 |
| nmeasure_6 | 0.4 | 88.5 | 4.1 | 7.9 | 6.0 | 0.6 | 99.6 | 107.5 |
| nmeasure_7 | 0.4 | 89.1 | 4.1 | 8.0 | 6.0 | 0.6 | 100.2 | 108.2 |
| nmeasure_8 | 0.3 | 88.9 | 3.9 | 7.5 | 6.0 | 0.6 | 99.8 | 107.3 |
| nmeasure_9 | 0.3 | 89.3 | 3.9 | 8.0 | 6.0 | 0.6 | 100.1 | 108.1 |
| nmeasure_10 | 0.3 | 89.5 | 3.8 | 7.5 | 8.0 | 0.6 | 102.2 | 109.7 |
| **mean** | 0.3 | 89.5 | 3.9 | 7.8 | 6.4 | 0.6 | **100.8** | **108.6** |

"Client-side generation" is the time the script spent producing the random vectors and
chunks between requests (`t_upsert − t_upsert_qdrant − t_upsert_es`); it is inside the
wall total but is not store work. **Store-side: ~100 s per 35k-chunk collection**
(99.6–104.7 s) — create + 137 serial batches of 256 at ~0.65 s each
(`upsert_concurrency=1`, the product default) + a 6–8 s optimizer settle; ES is
negligible (4 s of bulk, sub-second refresh). That ~100 s is the **floor** for the
*restore* cold start #358 budgeted at ~1 min: a real restore also has to read the
archive and re-embed or replay vectors, and these builds ran back to back on an
otherwise idle tenant.

## Per-collection deltas

Delta at 10 = (value after 10 − value after 0) / 10, as #359 prescribes. The 1→10
slope is shown beside it because ES's first index absorbs a one-off warm-up.

| Resource | Δ per collection (0→10) | Δ per collection (1→10) | Note |
|---|---|---|---|
| Qdrant memory mappings | **305.9** | 298.7 | ≈ 38 per segment × 8 segments; the bulk are the mmapped segment files |
| Qdrant threads | **0.8** | 0.6 | per-collection threads are ~nil — Qdrant's runtimes are sized once from the 384 cores |
| Qdrant RSS | **704 MiB** (720,938 kB) | 710 MiB | 547 MiB is the raw 35,000 × 4096 × f32 vectors held in RAM (`on_disk=false`); the rest is HNSW graph + segment overhead |
| Qdrant fds | 1.0 | 1.0 | |
| Qdrant disk | 1,107 MiB | 1,117 MiB | 2× the vectors: WAL + segment files |
| ES mappings | 146.2 | 20.8 | the 0→1 jump is JVM warm-up, the steady state is ~21 |
| ES threads (`/proc`) | 32.4 | **0.9** | +316 on the first index only (write/bulk pool threads sized to 384 cores, created lazily); flat thereafter |
| ES JVM threads | 31.6 | **0.0** | same |
| ES RSS | 47 MiB | 19 MiB | |
| ES heap used | flat (GC noise; 1,024 MiB max) | | ten 30 MiB indices say nothing about heap at hundreds of shards on a 1 GiB heap — **not measured** |
| ES shards | **2.0** | 2.0 | 1 primary + 1 replica (unassigned on a single node but counted against `cluster.max_shards_per_node`) |
| ES store | 30 MiB | 30 MiB | |

## Derivation of `n`

Formulas as set in #359: 60 % of the limit divided by the per-collection delta at 10
for mappings and threads; the tenant's RAM share divided by the per-collection RSS
delta.

| Ceiling | Limit | 60 % budget | Δ per collection | n |
|---|---|---|---|---|
| Qdrant memory mappings | `vm.max_map_count` = 262,144 (per process) | 157,286 | 305.9 | **514** |
| Qdrant threads | user-slice cgroup `pids.max` = 1,384,119 (binds before `ulimit -u` 6,189,434 and `threads-max` 12,378,869; shared by every instance in the user slice) | 830,471 | 0.8 | ~1,038,000 — not a constraint |
| RAM (Qdrant + ES RSS) | **assumed** 200 GB tenant share = 190,735 MiB (nothing in `tenant.env`/`provision.env` states it — see "Limits" below) | — (#359's formula uses the full share; at 60 %: 114,441 MiB) | 704 + 47 = 751 MiB | **254** at the full share (Qdrant alone: 271); **152** with the 60 % budget |
| ES shards (extra, ADR-0003 named it) | `cluster.max_shards_per_node` = 1,000 × 1 data node | 600 | 2 | **300** (298 net of the 4 shards in use) |
| ES memory mappings | `vm.max_map_count` = 262,144 | 157,286 | 146.2 | 1,075 |

**`n = min(514, ~10⁶, 254) = 254 — RAM binds**, under the 200 GB assumption.

**The 60 % budget was applied to mappings and threads but not to RAM, and RAM is the
ceiling that binds** — that single choice is the difference between n = 254 and
n = 152. #359 prescribed the formulas that way (RAM as "the tenant's share divided by
the delta"), so 254 is the number the issue asked for; 152 is what a uniform 60 %
headroom policy gives. Both are recorded because the recommendation below rests on the
gap between them. Two smaller notes on the RSS delta: ES RSS is not linear — two-thirds
of its 47 MiB is the first index's JVM warm-up, and the 1→10 slope of 19 MiB gives
n = 264 at the full share — so 254 is the conservative reading; and Qdrant's `VmHWM`
tracked `VmRSS` throughout (peak 7,196 MiB at step 7 against 7,012 MiB steady, i.e. the
optimizer-merge transient was ≤ 184 MiB in this run), which is what headroom at the top
of the range would have to absorb.

It is not a comfortable margin over the next two (ES shards 300, mappings 514), and it
is the one ceiling that depends on an assumed number, so the two things to revisit are
(a) the actual RAM share for the tenant and (b) whether the product should put vectors
`on_disk` for dormant-able collections — that would move RAM off the top of the list
and make ES shards (300) the bound.

Three caveats that matter more than the arithmetic:

1. **RAM is a chunk budget, not a collection budget.** 547 of the 704 MiB per collection
   is the raw vectors, so a 100k-chunk collection costs ~3× a 35k one. n = 254 is
   valid for a tenant whose collections *average* ≤ 35k chunks at 4096-d; the
   underlying budget is 190,735 MiB ÷ 20.6 KiB per chunk ≈ 9.5 M chunks resident across
   all active collections. Mappings also scale with data — #288 measured 162 VMAs for an
   empty collection and ~20,000 for multi-million-point ones — so n_maps = 514 is a
   35k-chunk figure just like n_RAM. Only threads are independent of both.
2. **Nothing here measured query-time cost** — a cross-collection fan-out or Qdrant's
   consensus/restart time with 250 collections (ADR-0003 lists "restart time" as the
   second failure as the count grows). Set the cap, then measure restart with the
   collections in place before relying on the top of the range.
3. **The vectors and documents are synthetic.** #359 asked for real embed outputs; this
   run used random unit-norm float32 vectors and ~300-byte random documents because
   the cost being measured is process-level. Raw-vector RSS (547 of the 704 MiB) and the
   mapping count are content-invariant — the headline ceilings move by less than ~2 %
   with real data. The HNSW graph may be slightly *over*-estimated (random data keeps
   near the maximum number of links per node; real embeddings prune more). The ES store
   and Qdrant payload disk are *under*-estimated by several-fold for real 1–2 KB chunks
   (30 MiB per index here) — but ES is not the binder, so `n` is unaffected.

### Recommended `MAX_COLLECTIONS` for the dev tenant

- **Keep 100 now.** It is well inside every ceiling and nothing needs it raised before
  eviction (#359 part 2) exists. At 100 active 35k-chunk collections Qdrant will hold
  ~69 GiB RSS (~53 GiB / 57 GB of it raw vectors) and ~40k mappings.
- **~150 is defensible without further measurement**: it is the uniform-60 % number
  (152) rounded, and it clears the ES-shard (300) and mapping (514) ceilings by 2× and
  3×.
- **250 (n = 254 rounded down) only once** (a) the tenant's RAM share is actually
  stated — in `provision.env` or ADR-0005 — rather than assumed, and (b) the RSS split
  has been measured so the share is compared against the right number: RSS here mixes
  anonymous pages (the in-RAM vectors and graph, which only RAM can hold) with
  file-backed pages (mmapped segments, which the kernel can drop under pressure).

The measurement for (b) is one line per sample — add it to the script's `proc_sample`
next time, together with PSS from `smaps_rollup`:

```bash
grep -E 'RssAnon|RssFile|RssShmem|VmHWM' /proc/<qdrant-pid>/status
grep -E '^Pss:' /proc/<qdrant-pid>/smaps_rollup
```

`VmHWM` was collected this time (see above); the anon/file split was not.

`MAX_COLLECTIONS` means *active* collections once part 2 lands.

## Comparison with ADR-0003's empty-collection figures

ADR-0003 (measured 2026-08-04, 17 empty collections): *"an empty collection already
costs 8 segments, ~104 files and ~496 KB, and 17 collections drive 1,561 threads and
61,219 mmaps"*. #288 has since corrected both attributions: the thread pools are sized
by `nproc` (384), not by collection count, and the mmap total was 70 % two
multi-million-point collections — the measured cost of an **empty** collection is
**162 VMAs** (8 segments, 178 files / 728 KB on the current path), and a collection
holding millions of points costs **~20,000**. This run supplies the middle point of
that curve.

| | ADR-0003 as written (totals ÷ 17) | #288 (empty, attributed) | This run (35k chunks, marginal) | #288 (multi-million points) |
|---|---|---|---|---|
| threads per collection | ~92 | ~0–1 | **0.8** | ~0–1 |
| mappings per collection | ~3,600 | **162** | **306** | ~20,000 |
| segments per collection | 8 | 8 | 7–8 | — |
| threads with N collections | 1,561 at N = 17 | — | 1,548 at N = 2, 1,556 at N = 12 | — |

So the marginal thread cost is nil, as #288 said, and mappings scale with data: 162
empty → 306 at 35k chunks → ~20k at millions. The mapping ceiling is therefore a
per-chunk-size figure (514 collections *at 35k chunks*), not a constant, and what
binds first on this tenant is RAM, which neither the ADR nor #288 measured. ADR-0003's
"budget ~100–150 per instance" stands as an operational default — #288's suggested
amendment (VMA ceiling instead of thread exhaustion, 162 VMAs per empty collection) can
now add the loaded marginal figures from here: +306 VMAs, +704 MiB RSS, +1,107 MiB disk
per 35k-chunk collection at 4096-d.

## What the numbers say

- **Thread exhaustion is not the failure mode on this host.** The ADR's reported
  failure order (RAM → restart time → thread exhaustion ~1000 → crash ~2000) came from
  reports on small hosts; here the process already runs 1,550 threads at rest and adds
  <1 per collection.
- **Mappings are comfortably second — at this chunk size.** 514 collections before
  60 % of `vm.max_map_count`; the sysctl is per-process, so the tenant's Qdrant and ES
  do not compete for it. But #288's ~20,000 VMAs for a multi-million-point collection
  means a handful of large collections would consume the same address space as
  hundreds of 35k ones; the mapping budget, like RAM, is really a data budget.
- **ES is cheap per collection** (30 MiB store, 19 MiB RSS, no threads) but
  `max_shards_per_node` counts the unassigned replica, so each collection costs 2 of
  the 1,000. `number_of_replicas: 0` on single-node tenants would double that ceiling
  to 600 — a product change, out of scope here.
- **Deletion reclaims cleanly**: Qdrant returned to baseline (+133 maps, −14 MiB) and
  the storage directories were gone; eviction (part 2) can rely on that.

## Method

Everything ran from the host, against the dev tenant's stores **only** — the URLs come
from the tenant's `tenant.env` (`QDRANT_URL`, `ELASTICSEARCH_URL`; nothing in
`secrets.env` is needed), and are written below as `<dev-tenant Qdrant>` /
`<dev-tenant ES>`. The API was deliberately bypassed (a create through the API counts
against the tenant's registry and cap); the stores were written through the product's
own adapters, `QdrantVectorStore` and `ElasticsearchTextIndex`
(`python/ragstack/stores/`), so the collections carry exactly the product's
configuration: `VectorParams(size=4096, distance=Cosine)` with Qdrant's default
HNSW (`m=16`, `ef_construct=100`, `indexing_threshold=10000`, `on_disk=false`,
`on_disk_payload=true`), keyword payload indexes on `tenant_id` and `doc_id`, upserts in
batches of **256**; ES indices with the adapter's `_MAPPINGS` and default settings
(1 primary, 1 replica — the replica is unassignable on a single node, so every index is
*yellow*; that is the product's shape, not an artefact).

Stores were named `nmeasure_<i>` so they are unmistakable, and deleted at the end.

### Finding the processes to sample

The apptainer instance pid is the `appinit` starter, not the service; the service is a
grandchild (Qdrant sits under a `bash` wrapper because of the `cd /qdrant` runscript
workaround; ES sits under `tini` → launcher `java` → the real JVM). Confirm the pid by
matching its listening port against the port in `tenant.env`:

```bash
apptainer instance list                      # qdrant-dev / elasticsearch-dev → instance pids
pstree -p <instance-pid> | head -5           # appinit → bash → qdrant ; appinit → tini → java → java
ss -ltnp | grep "pid=<candidate-pid>,"       # must show the port from tenant.env's *_URL
```

### Host-side samples (taken after every collection)

```bash
wc -l /proc/<qdrant-pid>/maps                # memory mappings
grep -E 'Threads|VmRSS|VmHWM' /proc/<qdrant-pid>/status
ls /proc/<qdrant-pid>/fd | wc -l
du -sb /rag/data/tenants/dev/qdrant/storage/collections/nmeasure_<i>
curl -s <dev-tenant Qdrant>/collections/nmeasure_<i>      # status, points_count, segments_count

wc -l /proc/<es-pid>/maps
grep -E 'Threads|VmRSS' /proc/<es-pid>/status
curl -s '<dev-tenant ES>/_nodes/stats/jvm'                 # jvm.threads.count, jvm.mem.heap_used_in_bytes / heap_max_in_bytes
curl -s '<dev-tenant ES>/_cat/shards?h=index,shard,prirep,state,store'
curl -s '<dev-tenant ES>/_cat/indices?bytes=b&h=index,pri,rep,docs.count,store.size,health'
```

### Limits (what each ceiling is measured against)

```bash
sysctl vm.max_map_count kernel.threads-max
ulimit -u
grep -E 'processes|open files|locked memory' /proc/<qdrant-pid>/limits
cat /proc/<qdrant-pid>/cgroup                              # → pids:/user.slice/user-<uid>.slice/session-<n>.scope
cat /sys/fs/cgroup/pids/user.slice/user-<uid>.slice/pids.max
cat /sys/fs/cgroup/pids/user.slice/user-<uid>.slice/session-<n>.scope/pids.max
free -g
```

Observed on the dev host:

| Limit | Value | Scope |
|---|---|---|
| `vm.max_map_count` | 262,144 | per process |
| `kernel.threads-max` | 12,378,869 | host |
| `ulimit -u` / `Max processes` (Qdrant, ES) | 6,189,434 | per user (rlimit) |
| `pids.max`, session scope cgroup | `max` | — |
| **`pids.max`, user-slice cgroup** | **1,384,119** | **every task of the user — all 11 apptainer instances share it** |
| `Max locked memory` (Qdrant) | 202,854,146,048 B = MemTotal ÷ 8 — the session-default `RLIMIT_MEMLOCK` every process inherits (`ulimit -l` in a plain host shell gives the same 198,099,752 KiB); it caps `mlock`'d pages, never RSS, and says nothing about a tenant share | per process |
| Host RAM | 1,511 GiB, no swap | host |

The thread ceiling that binds is therefore the **user-slice `pids.max` (1,384,119)** —
tighter than the rlimit and than `threads-max`, and shared by every service in the user
slice, not just this tenant's Qdrant.

**The 200 GB RAM share is an unenforced policy assumption**, taken as #359 instructed.
Nothing in `tenant.env`, `provision.env` or the provisioning script states a share, no
cgroup memory limit applies to the instance (`memory.limit_in_bytes` is unset at both
the session and user-slice level), and the true ceiling is the host's 1,511 GiB shared
with the other tenants' Qdrant/ES instances, the sidecars and vLLM. Whoever states the
share — `provision.env` or ADR-0005 — sets `n`.

### Build + sample script

The script below was run as
`PYTHONPATH=python /rag/envs/ragstack/bin/python3.12 nmeasure.py build --qdrant-pid <qdrant-pid> --es-pid <es-pid> --out <dir> --collections 10 --points 35000`
from a checkout of `main`, after a 2,000-point smoke run (`--prefix nmeasure_smoke_`,
built, sampled and deleted) confirmed the loop end to end. Per collection it: creates
both stores through the adapters; upserts 35,000 synthetic chunks in batches of 256
(random unit-norm float32 4096-d vectors, ~300-byte random content, a small metadata
map — `tenant_id`, `collection`, `title`, `year`); polls `GET /collections/<name>` until
`status == green` for three consecutive polls 2 s apart; refreshes + flushes the ES
index and waits for `merges.current == 0`; sleeps 10 s; samples.

The settle condition is *green*, not `indexed_vectors_count == points_count`. The
script's docstring justifies that by "segments may stay under `indexing_threshold`",
which is wrong in its reasoning though right in its effect: Qdrant's
`indexing_threshold` is denominated in **kilobytes of vectors per segment** (default
10,000 KB, "1 kB = 1 vector of size 256" — qdrant-client 1.18 `OptimizersConfig`),
so at 4096-d a segment crosses it at ~625 vectors and every segment here (~4.4k
points ≈ 70 MB) was indexed, as the observed `indexed_vectors_count` 34,560–35,000
shows. The small unindexed tail is the freshly appended segment that has not yet been
optimized; waiting for it to reach exactly `points_count` could stall on a segment that
stays under the threshold, so green plus a stability window is the right condition.

Cleanup: `nmeasure.py cleanup ...` deletes every `nmeasure_*` collection and index,
re-lists both stores and the Qdrant storage directory, and takes one more sample.

### Leave nothing behind (do this every time)

1. Delete every `nmeasure_*` collection and index (`nmeasure.py cleanup`).
2. Re-list both stores and the host storage directory and require all three empty —
   the run above ended with:

   ```
   remaining nmeasure_* qdrant collections: []
   remaining nmeasure_* es indices: []
   remaining nmeasure_* qdrant storage dirs on host: []
   ```

   and an independent `GET /collections` / `_cat/indices` / `_cat/shards` check
   confirmed the tenant back at its two pre-existing collections and indices, zero
   `nmeasure_*` shards.
3. Take one more host sample (the `after-cleanup` row) — it is the reclaim evidence and
   the baseline-drift check.
4. The smoke run's prefix (`nmeasure_smoke_`) was verified the same way, with the same
   three empty listings, before the real run started.

The raw samples from this run are committed beside this file as
[`active-collection-bound.steps.jsonl`](active-collection-bound.steps.jsonl) (one JSON
object per line: thirteen host samples and ten build-timing records; no pids or URLs).

<details><summary><code>nmeasure.py</code> (verbatim)</summary>

```python
"""Per-collection cost measurement for the active bound n (#359, part 1).

Builds nmeasure_<i> collections (Qdrant + ES) on the dev tenant's stores with
synthetic 4096-d vectors and small docs, through the product's own store
adapters (QdrantVectorStore / ElasticsearchTextIndex), and samples the two
processes from the host after every collection.

Usage:
  nmeasure.py build   --qdrant-pid P --es-pid P --out DIR [--collections 10] [--points 35000] [--prefix nmeasure_]
  nmeasure.py measure --qdrant-pid P --es-pid P --out DIR --label LABEL
  nmeasure.py cleanup --qdrant-pid P --es-pid P --out DIR [--prefix nmeasure_]
  nmeasure.py list

Store URLs come from the tenant.env (QDRANT_URL / ELASTICSEARCH_URL only) and are
never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import string
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np

TENANT_ENV = Path("/rag/data/tenants/dev/config/tenant.env")
DIM = 4096
BATCH = 256          # the product's qdrant upsert batch size
QDRANT_DATA = Path("/rag/data/tenants/dev/qdrant/storage/collections")


def tenant_urls() -> tuple[str, str]:
    q = e = None
    for line in TENANT_ENV.read_text().splitlines():
        if line.startswith("QDRANT_URL="):
            q = line.split("=", 1)[1].strip()
        elif line.startswith("ELASTICSEARCH_URL="):
            e = line.split("=", 1)[1].strip()
    assert q and e, "tenant.env lacks QDRANT_URL/ELASTICSEARCH_URL"
    return q, e


QURL, EURL = tenant_urls()


# ----------------------------------------------------------------------------
# host-side sampling
# ----------------------------------------------------------------------------
def proc_sample(pid: int) -> dict:
    status = Path(f"/proc/{pid}/status").read_text()
    d: dict = {}
    for line in status.splitlines():
        if line.startswith(("Threads:", "VmRSS:", "VmHWM:", "VmSize:")):
            k, v = line.split(":", 1)
            d[k] = int(v.split()[0])
    d["maps"] = sum(1 for _ in open(f"/proc/{pid}/maps"))
    d["fds"] = len(os.listdir(f"/proc/{pid}/fd"))
    return d


def du_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    out = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out else 0


def sample(label: str, qpid: int, epid: int, prefix: str) -> dict:
    with httpx.Client(timeout=60) as c:
        cols = c.get(f"{QURL}/collections").json()["result"]["collections"]
        qcols = {}
        for col in cols:
            name = col["name"]
            r = c.get(f"{QURL}/collections/{name}").json()["result"]
            qcols[name] = {
                "status": r["status"],
                "points": r["points_count"],
                "indexed": r.get("indexed_vectors_count"),
                "segments": r["segments_count"],
                "du_bytes": du_bytes(QDRANT_DATA / name),
            }
        jvm = list(c.get(f"{EURL}/_nodes/stats/jvm").json()["nodes"].values())[0]["jvm"]
        shards = c.get(f"{EURL}/_cat/shards?format=json&h=index,shard,prirep,state,store").json()
        indices = c.get(f"{EURL}/_cat/indices?format=json&bytes=b&h=index,pri,rep,docs.count,store.size,health").json()
    meas = {}
    for name in qcols:
        if name.startswith(prefix):
            meas[name] = qcols[name]
    return {
        "label": label,
        "ts": time.time(),
        "qdrant": {
            "proc": proc_sample(qpid),
            "collections_total": len(qcols),
            "measure_collections": meas,
            "measure_du_bytes": sum(v["du_bytes"] for v in meas.values()),
        },
        "es": {
            "proc": proc_sample(epid),
            "jvm_threads": jvm["threads"]["count"],
            "jvm_threads_peak": jvm["threads"]["peak_count"],
            "heap_used_mb": jvm["mem"]["heap_used_in_bytes"] // 2**20,
            "heap_max_mb": jvm["mem"]["heap_max_in_bytes"] // 2**20,
            "shards_total": len(shards),
            "shards_started": sum(1 for s in shards if s["state"] == "STARTED"),
            "shards_measure": sum(1 for s in shards if s["index"].startswith(prefix)),
            "indices_total": len(indices),
            "measure_indices": {
                i["index"]: {"docs": int(i["docs.count"] or 0), "store_bytes": int(i["store.size"] or 0),
                             "health": i["health"], "pri": i["pri"], "rep": i["rep"]}
                for i in indices if i["index"].startswith(prefix)
            },
            "measure_store_bytes": sum(int(i["store.size"] or 0) for i in indices if i["index"].startswith(prefix)),
        },
    }


def fmt(s: dict) -> str:
    q, e = s["qdrant"], s["es"]
    return (f"[{s['label']}] qdrant: maps={q['proc']['maps']} threads={q['proc']['Threads']} "
            f"rss_mb={q['proc']['VmRSS']//1024} fds={q['proc']['fds']} cols={q['collections_total']} "
            f"du_mb={q['measure_du_bytes']//2**20} | es: maps={e['proc']['maps']} threads={e['proc']['Threads']} "
            f"jvm_threads={e['jvm_threads']} rss_mb={e['proc']['VmRSS']//1024} heap_mb={e['heap_used_mb']}/{e['heap_max_mb']} "
            f"shards={e['shards_total']} (started {e['shards_started']}) store_mb={e['measure_store_bytes']//2**20}")


def write_step(out: Path, rec: dict) -> None:
    with open(out / "steps.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(fmt(rec) if "qdrant" in rec else json.dumps(rec), flush=True)


# ----------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------
def rand_text(rng: random.Random, n_words: int) -> str:
    return " ".join("".join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 9))) for _ in range(n_words))


async def wait_green(name: str, hard_timeout: float = 900.0) -> float:
    """Poll /collections/<name> until status is green for 3 consecutive polls 2 s apart.
    Never waits on indexed_vectors_count (segments may stay under indexing_threshold)."""
    t0 = time.time()
    ok = 0
    async with httpx.AsyncClient(timeout=30) as c:
        while True:
            r = await c.get(f"{QURL}/collections/{name}")
            st = r.json()["result"]["status"]
            ok = ok + 1 if st == "green" else 0
            if ok >= 3:
                return time.time() - t0
            if time.time() - t0 > hard_timeout:
                raise TimeoutError(f"{name} not green after {hard_timeout}s (status={st})")
            await asyncio.sleep(2)


async def es_settle(index: str, hard_timeout: float = 300.0) -> float:
    """Refresh the index, then wait until the node reports no running merges."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=60) as c:
        await c.post(f"{EURL}/{index}/_refresh")
        await c.post(f"{EURL}/{index}/_flush")
        while True:
            st = await c.get(f"{EURL}/_nodes/stats/indices/merge")
            merges = list(st.json()["nodes"].values())[0]["indices"]["merges"]["current"]
            if merges == 0:
                return time.time() - t0
            if time.time() - t0 > hard_timeout:
                return time.time() - t0
            await asyncio.sleep(2)


async def build_one(i: int, name: str, n_points: int, seed: int) -> dict:
    from ragstack.models import Chunk
    from ragstack.stores.elasticsearch import ElasticsearchTextIndex
    from ragstack.stores.qdrant import QdrantVectorStore

    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    vs = QdrantVectorStore(url=QURL, collection=name, vector_size=DIM, timeout=120,
                           upsert_batch_size=BATCH, upsert_concurrency=1)
    ti = ElasticsearchTextIndex(url=EURL, index=name, refresh_on_write=False, bulk_batch_size=BATCH)
    t0 = time.time()
    await vs.ensure_collection()
    await ti.ensure_index()
    t_create = time.time() - t0

    t1 = time.time()
    t_q = t_e = 0.0
    done = 0
    while done < n_points:
        k = min(BATCH, n_points - done)
        vecs = nrng.standard_normal((k, DIM), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        chunks = []
        for j in range(k):
            idx = done + j
            chunks.append(Chunk(
                id=f"{name}-{idx}",
                doc_id=f"{name}-doc{idx // 20}",
                content=rand_text(rng, 45),                      # ~300 bytes
                embedding=vecs[j].tolist(),
                metadata={"tenant_id": "nmeasure", "collection": name,
                          "title": rand_text(rng, 6), "year": 2000 + idx % 26},
                start_char=0, end_char=300,
            ))
        a = time.time()
        await vs.upsert(chunks)
        t_q += time.time() - a
        a = time.time()
        await ti.index(chunks)
        t_e += time.time() - a
        done += k
    t_upsert = time.time() - t1

    t_settle_q = await wait_green(name)
    t_settle_e = await es_settle(name)
    await vs._client.close()
    await ti._es.close()
    return {"collection": name, "points": n_points, "t_create_s": round(t_create, 1),
            "t_upsert_s": round(t_upsert, 1), "t_upsert_qdrant_s": round(t_q, 1),
            "t_upsert_es_s": round(t_e, 1), "t_settle_qdrant_s": round(t_settle_q, 1),
            "t_settle_es_s": round(t_settle_e, 1),
            "t_total_s": round(time.time() - t0, 1)}


async def build(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_step(out, sample("0", args.qdrant_pid, args.es_pid, args.prefix))
    for i in range(1, args.collections + 1):
        name = f"{args.prefix}{i}"
        rec = await build_one(i, name, args.points, seed=359_000 + i)
        rec["label"] = f"build-{i}"
        write_step(out, rec)
        await asyncio.sleep(10)   # let RSS/threads settle after the optimizer finishes
        write_step(out, sample(str(i), args.qdrant_pid, args.es_pid, args.prefix))


# ----------------------------------------------------------------------------
# cleanup / list
# ----------------------------------------------------------------------------
def list_stores(prefix: str) -> tuple[list[str], list[str]]:
    with httpx.Client(timeout=60) as c:
        cols = [x["name"] for x in c.get(f"{QURL}/collections").json()["result"]["collections"]]
        idx = [x["index"] for x in c.get(f"{EURL}/_cat/indices?format=json&h=index").json()]
    return sorted(n for n in cols if n.startswith(prefix)), sorted(n for n in idx if n.startswith(prefix))


def cleanup(args) -> None:
    qc, ei = list_stores(args.prefix)
    with httpx.Client(timeout=300) as c:
        for n in qc:
            r = c.delete(f"{QURL}/collections/{n}")
            print(f"qdrant delete {n}: {r.status_code}")
        for n in ei:
            r = c.delete(f"{EURL}/{n}")
            print(f"es delete {n}: {r.status_code}")
    qc2, ei2 = list_stores(args.prefix)
    print(f"remaining {args.prefix}* qdrant collections: {qc2}")
    print(f"remaining {args.prefix}* es indices: {ei2}")
    leftover = [p.name for p in QDRANT_DATA.iterdir() if p.name.startswith(args.prefix)]
    print(f"remaining {args.prefix}* qdrant storage dirs on host: {leftover}")
    if args.out:
        time.sleep(10)
        write_step(Path(args.out), sample("after-cleanup", args.qdrant_pid, args.es_pid, args.prefix))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "measure", "cleanup", "list"])
    ap.add_argument("--qdrant-pid", type=int)
    ap.add_argument("--es-pid", type=int)
    ap.add_argument("--out")
    ap.add_argument("--label", default="manual")
    ap.add_argument("--collections", type=int, default=10)
    ap.add_argument("--points", type=int, default=35_000)
    ap.add_argument("--prefix", default="nmeasure_")
    args = ap.parse_args()
    if args.cmd == "list":
        qc, ei = list_stores(args.prefix)
        print(f"{args.prefix}* qdrant collections: {qc}")
        print(f"{args.prefix}* es indices: {ei}")
    elif args.cmd == "measure":
        Path(args.out).mkdir(parents=True, exist_ok=True)
        write_step(Path(args.out), sample(args.label, args.qdrant_pid, args.es_pid, args.prefix))
    elif args.cmd == "cleanup":
        cleanup(args)
    else:
        asyncio.run(build(args))


if __name__ == "__main__":
    main()
```

</details>
