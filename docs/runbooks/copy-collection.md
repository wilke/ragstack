# Copying a collection from one tenant's stores into another's

`python/scripts/copy_collection.py` copies a collection's **live store contents**
from one Qdrant instance to another, and its text index from one Elasticsearch
instance to another. No re-embedding, no API involved, resumable, verified.

Every host-specific value below is a placeholder — `<src-collection>`,
`<dst-collection>`, `<tenant>`, `<TENANT_ADMIN_KEY>`. Read the real physical
names from the destination tenant's registry (`GET /v1/collections`) rather than
substituting a guess: the physical name is *derived* by the API, and a copy into
a name the registry does not know about is an orphan store nothing can serve.

---

## 1. When to use it (and when not to)

Use it when a corpus that already exists in one deployment's stores has to appear
in another deployment's stores, and re-ingesting is not sensible — the embedding
inputs are gone, or too expensive to recompute, or the source shards are an older
format. The case it was written for: sharing the ASM semantic corpus (2.98M
chunks, 4096-d) with the hackathon tenant, which has its own Qdrant and ES.

Prefer something else when it applies:

| Situation | Use instead |
|---|---|
| You still have header-style embedding files | `load_embeddings.py` |
| The collection has an archive (`versions: [...]`) | `restore-collection` replay |
| You are moving a **whole tenant** | `ragstack-ctl restore --as` |
| The documents themselves are available and cheap to re-embed | normal ingest |

What it will not do, by design: it never deletes or recreates anything, never
writes to the source, and never invents an ES mapping. There is no
`--recreate-dst`.

## 2. The two-instance layout

A copy has four endpoints, two per leg, and they are always different instances:

```
  source (read only)                    destination (written)
  ┌──────────────────────────┐          ┌──────────────────────────┐
  │ Qdrant  :6333            │  ──────► │ Qdrant  :<tenant qdrant> │   vectors + payload
  │   <src-collection>       │          │   <dst-collection>       │
  ├──────────────────────────┤          ├──────────────────────────┤
  │ Elastic :9200            │  ──────► │ Elastic :<tenant es>     │   _id + _source
  │   <src-index>            │          │   <dst-index>            │
  └──────────────────────────┘          └──────────────────────────┘
```

The ES leg is optional: omit all four of `--src-es/--src-index/--dst-es/--dst-index`
to copy vectors only. Give some but not all, and the run is refused — a
vectors-only copy of a collection that *has* a text index is a hybrid collection
that silently answers half of every query.

Port numbers live in the destination tenant's `tenant.env` (and in the memory
note for that tenant); do not hardcode them from another runbook.

## 3. Register, then copy

The registry is the source of truth for names and access, so the destination
collection is **created through the API first** and only then filled from the
stores.

**a. Register on the destination tenant** (admin key):

```bash
curl -s -X POST "<dst-api>/v1/collections" \
  -H "X-API-Key: <TENANT_ADMIN_KEY>" -H 'Content-Type: application/json' \
  -d '{"id":"<collection-id>","label":"<label>","chunk":{"method":"semantic"}}'
```

This derives the physical name, creates the Qdrant collection (float32) and the
ES index **with ragstack's mapping** (`metadata.*` keyword with `ignore_above`,
the thing whose absence poisons an ingest — see MEMORY.md). Read the physical
name back out of `GET /v1/collections`; it is `<dst-collection>` and
`<dst-index>` below.

**b. Optional, for a large corpus — re-create the physical Qdrant collection at
reduced resolution.** The API creates a float32 collection. For a multi-million
point corpus, delete *that physical collection only* on the destination Qdrant
and let `--create-dst` rebuild it as float16 + int8:

```bash
curl -s -X DELETE "http://127.0.0.1:<tenant qdrant>/collections/<dst-collection>"
```

Only ever do this on the **destination** instance, only for a collection that was
just created and is still empty, and never to "fix" a mismatch on a collection
that holds data. `--create-dst` then builds:

```json
{"vectors": {"size": <src size>, "distance": "<src distance>",
             "datatype": "float16", "on_disk": true},
 "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": true}},
 "on_disk_payload": true,
 "payload_indexes": ["tenant_id", "doc_id"]}
```

float16 originals on disk + an int8 copy in RAM for search, with Qdrant rescoring
from the originals — roughly 24 GB + 12 GB rather than 53 GB for 2.98M 4096-d
vectors, and no ragstack code change. The `tenant_id`/`doc_id` keyword indexes
are the ones `QdrantVectorStore._ensure_payload_indexes` expects; without
`doc_id` every delete-prior becomes a full collection scan.

Skip step (b) entirely for a small collection: `--create-dst` will simply verify
the existing destination and continue.

**c. Dry run.** Read-only. It prints both source counts, the destination's
current state, and the plan, and creates nothing:

```bash
/rag/envs/ragstack/bin/python python/scripts/copy_collection.py --dry-run \
  --src-qdrant http://127.0.0.1:6333 --src-collection <src-collection> \
  --dst-qdrant http://127.0.0.1:<tenant qdrant> --dst-collection <dst-collection> \
  --src-es http://127.0.0.1:9200 --src-index <src-index> \
  --dst-es http://127.0.0.1:<tenant es> --dst-index <dst-index>
```

Check the two source counts against each other before going further: a vector
leg and a text leg that already disagree at the source mean the *source* is
incomplete, and the copy will faithfully reproduce that.

**d. Copy.** Detached, as the tenant's user, with a log and a checkpoint:

```bash
nohup /rag/envs/ragstack/bin/python python/scripts/copy_collection.py \
  --src-qdrant http://127.0.0.1:6333 --src-collection <src-collection> \
  --dst-qdrant http://127.0.0.1:<tenant qdrant> --dst-collection <dst-collection> \
  --src-es http://127.0.0.1:9200 --src-index <src-index> \
  --dst-es http://127.0.0.1:<tenant es> --dst-index <dst-index> \
  --create-dst --batch 256 --pause 0.05 \
  --checkpoint /rag/data/tenants/<tenant>/copy-<collection-id>.ckpt \
  --out /rag/data/tenants/<tenant>/copy-<collection-id>.json \
  > /rag/data/tenants/<tenant>/logs/copy-<collection-id>.log 2>&1 &
```

`--pause` is what keeps the shared production Qdrant serving its tenants while
53 GB is read out of it; do not set it to 0 on a shared source. Progress lines
(points/s and ETA) land in the log every 50,000 points.

**Interrupted?** Re-run the identical command. The checkpoint (Qdrant scroll
offset + ES `search_after` key + counts, written atomically after every batch) is
resumed by default; `--restart` starts over. Both legs are idempotent — Qdrant
upserts by point id, ES bulk uses `op_type: index` — so a re-run overwrites, it
never duplicates, and a resume cannot raise a version conflict.

> The ES resume reuses a `_shard_doc` sort key across a new point-in-time. That
> is sound **because the source index is read-only for the duration**. If
> something is writing to the source index, use `--restart` instead of resuming,
> and trust the verification rather than the checkpoint.

**e. Share** (if attendees/other users need it):

```bash
curl -s -X POST "<dst-api>/v1/collections/<collection-id>/shares" \
  -H "X-API-Key: <TENANT_ADMIN_KEY>" -H 'Content-Type: application/json' \
  -d '{"grantee":"@public","permission":"read"}'
```

## 4. Verification

The copy verifies itself and exits **3** on any mismatch (0 ok, 1 error). To
re-run verification alone, at any time:

```bash
/rag/envs/ragstack/bin/python python/scripts/copy_collection.py --verify-only \
  --spot-check 50 ... (the same source/destination arguments)
```

What it checks:

* Qdrant `points_count` and ES doc count, source vs destination, live (not from
  the checkpoint — the checkpoint's counts are for reporting only).
* N random source point ids (default 20) retrieved from both sides: payload must
  be **equal**, vectors must have cosine **>= 0.99**. Not equality: a float16
  destination rounds every component, so equality would fail every honest copy.
* The same chunk ids in ES: `_source` must be equal.

Then check the collection through the API, which is what users actually see:

```bash
curl -s -H "X-API-Key: <TENANT_ADMIN_KEY>" "<dst-api>/v1/collections?counts=true" | jq
```

`count` and `text_count` should agree with each other and with the source. Give
Qdrant's optimizer time to finish (collection `status: green`) before judging
search latency, and expect scores to differ slightly from the source's — that is
int8 search with float16 rescoring, not a bad copy. The top chunk ids for the
same `/v1/retrieve` query should overlap heavily with the source deployment's.

## 5. Rollback

Delete the collection **through the destination tenant's API**:

```bash
curl -s -X DELETE "<dst-api>/v1/collections/<collection-id>" \
  -H "X-API-Key: <TENANT_ADMIN_KEY>"
```

That purges the destination tenant's physical Qdrant collection and ES index and
removes the registry row. The source is never touched by any part of this
procedure, so there is nothing to undo there. Delete the checkpoint file
afterwards; a stale checkpoint against a deleted destination would resume
mid-collection into an empty store and report a count mismatch at the end.

## 6. Failure modes seen or designed for

| Symptom | Cause | Action |
|---|---|---|
| `destination collection ... does not match the source` | destination has a different vector size/distance | It is **not** recreated. Check you have the right physical name; delete it through the API if it really is wrong. |
| `destination index ... does not exist` | the register step (3a) was skipped | Register through the API — the tool will not guess a mapping. |
| `checkpoint ... belongs to a different copy` | checkpoint path reused across copies | Use a per-collection checkpoint path, or `--restart`. |
| exit 3, counts differ by a small number | source was written during the copy, or a partial resume | Re-run the same command (idempotent), then `--verify-only`. |
| source Qdrant latency rises for other tenants | `--pause` too small / `--batch` too large | Kill it, raise `--pause`, resume from the checkpoint. |
