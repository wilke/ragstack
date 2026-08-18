# PubMed Central open-access ingest — run record

Building the `open-access` collection on the **asm** tenant: 1.44M JATS articles →
**47,625,155 chunks** in Qdrant `:6333` + Elasticsearch `:9200`.
Started 2026-08-09, **completed 2026-08-17** — 32/32 batches.

This is the record of *how* it was run and *what went wrong*, which is the part
that does not survive in the ledger. Live state lives in
`/rag/ingest/oa/asm-run/` — `ledger.jsonl` (one verified row per batch),
`report.sh` and `collections.sh` (timestamped, re-runnable, store-verified).

## Provenance

| | |
|---|---|
| Corpus | `/rag/oa/corpus` — 1,439,753 JATS XML, 191 GB, hash-verified (0 missing, 0 mismatches) |
| Excluded | 33,154 (2.30%) — retraction notices **and their targets**, EoC notices, editorial/news/book-review, abstracts, letters, replies |
| Planned | 1,404,453 articles → 2,048 shards, CV 4.3% |
| Collection | `open-access`, registered via `POST /v1/collections` **before** any write (#263) |
| Store | server-minted `ragstack_lib_open_access_…_cd24acfc` — the plan's assumed `ragstack_oa_tok512` does not survive the API path |
| Build spec | `fixed_token` 512/64, SFR-Embedding-Mistral 4096-d, tenant `public`, `group:public` read |

## Reproducing it

```bash
# 0. exclusions (34s over 1.44M roots)
python scripts/scan_notices.py --corpus /rag/oa/corpus --out /rag/ingest/oa/notice-scan \
    --drop-types "retraction,retraction-forward,expression-of-concern,editorial,news,book-review,abstract,letter,decision-letter,reply"

# 1. plan — shard = sha1(pmcid) % N, stable while the corpus grows
python scripts/plan_shards.py --corpus /rag/oa/corpus --out /rag/ingest/oa/r1 \
    --exclude /rag/ingest/oa/notice-scan/exclusions.jsonl

# 2. register the collection (FIRST, not last)
curl -X POST .../v1/collections -d '{"id":"open-access","label":"…",
    "chunk":{"method":"fixed_token","size":512,"overlap":64}}'

# 3. run — 64-shard batches, verified and cleaned per batch, resumable
python scripts/gowe_batch_ingest.py --plan /rag/ingest/oa/r1 \
    --cwl cwl/jats-ingest.cwl --inputs-template /rag/ingest/oa/asm-run/base.yml \
    --out /rag/ingest/oa/asm-run --batch-size 64 --retries 1 \
    --batch-timeout 86400 --gowe-bin /scout/Experiments/GoWe/bin/gowe
```

Rerunning the driver is always safe: it skips `done` batches, re-attaches to a
`timeout`ed submission, and point ids are `uuid5(tenant:chunk_id)` so a repeated
load upserts rather than duplicates.

## Measured

**COMPLETE: 32/32 batches, 47,625,155 chunks.** Final verification at rest:
both legs exactly equal across three stable samples, Qdrant green, and distinct
`pmcid` cardinality at **100.27% of the 1,404,453 planned articles** (within the
aggregation's ±0.5% band). Staged intermediates reclaimed (235 GB).

The run's own arc is its summary: the first clean batches took ~6.0 h, the last
eleven took 2.94-3.54 h. Total wall ~8 days, roughly half of which was the four
big incidents; the fixes they produced then halved the batch time.

| | |
|---|---|
| Batch wall, original | **~6.0 h** (6.09, 5.95, 5.95, 5.99, 6.14 — 2.3% spread) |
| Batch wall, current | **~3.0 h** (six consecutive at 2.94-2.99) after the embed fan-out fix |
| Per batch | ~1.49M chunks, ~82 GB of intermediates, reclaimed after verification |
| Corpus rate | 29.8 chunks/article measured, vs 26.9 planned |
| Final | **47,625,155 chunks** |

Production impact: prod ES answered real queries at **7–31 ms** throughout.

### Where a batch's time goes

Measured by a 15-second probe sampling workflow task counters and all 8 GPUs
(`phase-probe.sh` / `phase-report.py`). The profile is the whole reason the
batch time halved — before it existed, the split was guesswork.

| phase | before | after | note |
|---|---|---|---|
| extract | 0.06 h | 0.06 h | never the constraint; 64 tasks in ~4 min |
| **embed** | **4.13 h** | **1.14 h** | fleet 1.30 → **4.32 of 6 GPUs** |
| load | 2.54 h | 1.79 h | now the largest phase at 60% |

The fleet reads 75% mean utilisation during embed with every card peaking at
100%. Before the fix, three of six GPUs never exceeded 5% across 937 samples.

## Incidents — the expensive knowledge

**1 · `doc_id` had no Qdrant payload index (#307).** `index_chunks` delete-priors
per document; unindexed, every delete is a full collection scan. A load past
~150k points ground to **~1 delete/s** and presented as a hung container (7 h in
`ep_poll`, zero I/O). Creating the index live took the same load to ~125/s.
`ensure_collection` now creates it and back-fills existing collections at boot.

**2 · The embed pool used one endpoint at a time (#308/#309).** `_embed_and_link`
embeds a whole shard in ONE `embed()` call and the pool routed one call to one
endpoint — a 22k-text request pinning a single GPU while five idled. The fleet
benched at 2,606 texts/s; the pipeline achieved 58. `PooledEmbedder.embed` now
splits oversized calls and gathers them concurrently: **58 → 560 chunks/s.**
An earlier "fix" (8 in-flight per endpoint instead of 8 total) changed nothing,
because there was only ever one call in flight — the real defect was upstream.

**3 · GoWe serializes scatter-over-subworkflow (GoWe#164).** The chained
`scatter(extract → embed)` shape ran children strictly serially *inline in the
scheduler loop*: 1/N speed, **every other submission on the engine blocked**, and
uncancellable (parent cancel ignored between iterations; a cancelled child
finalizes in a state the loop reads as success). Only a server restart stops it.
Workaround: two **top-level** scatters with a phase barrier (`jats-ingest.cwl`).

**4 · Doc ids depended on the process working directory (#303).** Ids keyed on
`Path(path).resolve()`, which prepends the CWD to a relative identifier like
`PMC123#table-2`. A shell run and a GoWe worker minted two id families, so a
re-load **duplicated** the corpus (24,263 → 36,496) instead of upserting.
Absolute paths keep `resolve()`; relative ones key on the literal string.
Only reachable when the same shard is ingested from two different directories —
which is exactly what the first GoWe run did.

**5 · A transient LDAP blip failed a batch at 128/130 tasks (#315).** Apptainer
could not resolve the run-as uid for ~5 s and the worker's three retries all
landed inside that window. Embeddings were intact, so the load ran host-side —
~2 h instead of re-embedding for ~6 h. The driver now retries a failed batch once.

**6 · …and that retry then fired on a TIMEOUT (#317).** A timeout means the
*driver* gave up, not that the submission died: the resubmit launched 64
redundant embeds while the original was at 129/130 with its load running.
Retry is now gated on the ledger status literally being `failed`; timeouts
re-attach instead. No data harm — idempotent ids meant the cost was compute only.

**7 · One shard was too big for a single ES bulk request (#330).** The text index
put **every chunk of a group in one bulk body**. ES caps a body at
`http.max_content_length` (100 MB) and answers an oversized one with a bare
**HTTP 413** — nothing written, no per-item error, no indication which document
was at fault. One shard holds **38,322 chunks in 1.99 GB**, so ES took none of it
while Qdrant, batching at 256 points, took all 38,322.

This is the whole explanation for the "constant 38,322 leg gap" that survived
three multi-hour reload attempts: **the legs differed by exactly one shard
because one leg batched and the other did not.** With `--fail-on-error` each
attempt failed the batch *after* the other 63 shards had loaded cleanly, and the
engine retried from zero — roughly a day of wall clock in a loop. Batch 8's
26.49 h entry is that loop.

The number was in plain sight the entire time; the gap and the shard's chunk
count are the same figure. It read as a coincidence of scale rather than an
identity. What broke it open was running the load *outside* the workflow engine,
where per-file errors are visible — inside, task stdout is only captured on
completion, and the task never completed.

**8 · Every write forced a synchronous ES refresh (#326).** Measured mid-build:
**89.1 s of a 90 s window spent refreshing** — 1,355 refreshes, ~15/s — against
1.5 s deleting and 0.0 s indexing. Refresh was ~99% of the text leg's wall clock.
Note this is *not* `index.refresh_interval`: an explicit `refresh=true` on a
request refreshes regardless of the interval. An earlier fix parked the interval
and would have been a disappointing no-op with the real cause untouched.

**9 · Embedding ran on 1.3 of 6 GPUs (#334, #335).** `iter_embed_source` streamed
documents in fixed groups of 64. One group is one `embed()` call, and the pool
spreads a call across at most `ceil(chunks / request_batch)` endpoints — so the
**group**, not the permit count or the fleet size, was the fan-out ceiling. At
~3 chunks/doc that is ~190 chunks → **1.5 sub-requests**, and 1.5 sub-requests
cannot occupy more than two GPUs.

Measured over 937 samples: GPU0 97.9% mean, GPU1 31.4%, GPUs 3-5 never above 5%.
The pool itself was innocent — a traced selector distributes near-uniformly over
production-shaped payloads. It was simply never handed enough concurrent work.
Deriving the group from the fleet (128 docs × endpoints) took embed from
**4.13 h → 1.14 h** and the fleet from **1.30 → 4.32 of 6 GPUs**.

**10 · `--file-concurrency` reached 142 GB RSS (#328, open).** Loading two files
concurrently took the loader to 142.5 GB and failed 22 of 64 files; serially it
sits at 4.8 GB with none. ~30× the memory for 2× the concurrency, so something
retains nearly every file rather than two. Held at 1. The failure surfaces as the
vector client's `ResponseHandlingException` with an **empty message**, which reads
as a store outage — the store was green, answering in 0.01 s, and absent from the
host's top CPU consumers throughout.

**11 · The batch verification raced the stores it was verifying (#338).** The
driver read both leg counts once, the instant the workflow reported COMPLETED.
Three changes — concurrent legs, parked refresh, and Qdrant's
acknowledge-before-apply — each correct alone, made that single read a race: a
16,950 "disagreement" that converged to zero in ~60 s. By then the driver had
written `failed` and its retry was re-running 64 shards of a healthy batch,
while the original load task was still going. Fixed with a settle-poll whose
first version had its own flaw — it waited the full window on *any* gap, which
would have delayed every true failure by 10 minutes and took the test suite from
35 s to 20 minutes. A real gap is *static*; a settling store is *moving* —
stability, not time, now ends the wait. Fourth consecutive incident caused by
the fix for the previous one.

**Also**: one staged `.emb.jsonl` had a torn line; the loader failed that file
loudly rather than loading it partially (re-embedded, ~6 min). demo's toy
`open-access` collided with this one's content-addressed store name — same id +
same spec ⇒ same name **across tenants**. And an unescaped `%` in an argparse
help string crashed `--help` for the whole tool — caught by the runbook's own
mandatory `--help | grep` check against a rebuilt image, which returned empty and
read as "the flags didn't make it in" (#327).

## Things to know before the next large ingest

- **Heavy read analytics on the shared instance are not free.** Batch 8 took
  >12 h against a 6 h norm while cardinality aggregations ran over 25M-document
  indices on the same ES. Set `--batch-timeout` well above the norm (24 h), or
  don't audit during a load.
- **Payload shape differs per leg**: Qdrant flat (`content_type`), ES nested
  (`metadata.content_type`). A filter correct on one silently matches nothing on
  the other.
- **A mid-load leg mismatch is expected**, not an alarm: the legs are now written
  concurrently, so a small skew either way is normal. Only an at-rest mismatch is
  real — every completed batch has matched exactly. Note the count is read from a
  *refreshed* index or it lies: with the refresh interval parked for a bulk load,
  `_count` under-reported by 244,864 documents and the report cried mismatch on a
  leg that was fine.
- **Bulk tools need `/rag/envs/ragstack/bin/python3.12` + `PYTHONPATH` +
  `HF_HOME=/rag/cache`.** The conda env lacks `transformers`, and without the
  tokenizer `fixed_token` silently degrades to a counter the chunker then rejects.
- **The worker image is resolved by the worker's `--image-dir`, not by the
  checkout.** The copy under `apptainer/images/` is not what a submitted batch
  runs. Getting this wrong is silent: the batch runs happily on the stale image
  and the new flags never take effect.
- **Stop a load with SIGINT, never SIGTERM.** The refresh-interval restore lives
  in a `finally`, and `finally` does not run on SIGTERM — a `kill` leaves the
  index at `refresh_interval: -1`, silently not refreshing, and every later
  count-based check reads stale.
- **Measure the phase split before optimising.** Four separate throughput fixes
  landed before anyone knew embed was 68% of a batch and extract was 1%. The
  15-second probe that answered it costs nothing and would have redirected the
  effort on day one.
