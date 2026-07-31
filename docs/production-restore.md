# Production restore runbook

Recorded 2026-07-31, before any restart carrying the #195/#196/#207 security fixes.
Verified read-only from `/proc/<pid>/{cmdline,cwd,environ}` on coconut.

## Current fleet — restore-to state

| Port | Role | Code | Branch / SHA | Config source |
|---|---|---|---|---|
| **8000** | asm, production | `/rag/repos/ragstack/python` | `fix/es-keyword-ignore-above-and-drain-completeness` @ `6d6fcf6` | **inline env only — no `.env` file** |
| **8010** | lucid, production | `/rag/repos/ragstack/python` | same checkout, same SHA | `/rag/config/lucid.env` |
| **8020** | unified demo | `/home/wilke/Development/ragstack/python` | `main` | `/rag/config/unified.env` |

`/rag/repos/ragstack` is **10 commits ahead of `origin/main`** and **134 behind**. The 10 are
unmerged: `#141` decoupled embed-to-file ingest, `embed_pool` admission-control and
weighted-random routing, `--max-doc-chars`, ES keyword `ignore_above`. **A `git checkout main`
in that checkout silently discards all of them.** See "Deploying" below.

## Restore procedure

Nothing in #195/#196/#207 writes, migrates, or re-keys stored data, so **restore is code +
process only. No data rollback exists because none is needed.** Qdrant, Elasticsearch and Neo4j
are untouched by all three changes.

### 1. Stop

Identify by port, never by a pattern that could match the stopping command itself:

```bash
ss -lntp | grep -E ':(8000|8010|8020)'      # confirm pid
kill <pid>                                   # SIGTERM; uvicorn drains
```

Do **not** `pkill -f "port 8000"` — the pattern matches the shell running it (this has already
caused a self-kill in this repo's history, exit 144).

### 2. Restore the code

```bash
cd /rag/repos/ragstack
git status                                   # expect: branch above, clean but for .claude/
git checkout fix/es-keyword-ignore-above-and-drain-completeness
git reset --hard 6d6fcf6                     # ONLY if the working tree was changed
```

For :8020, the checkout is `/home/wilke/Development/ragstack`; restore with
`git checkout main && git reset --hard <sha>`.

### 3. Relaunch — exact commands

**:8000 (asm)** — inline env, no config file. Every variable must be on the command line:

```bash
cd /rag/repos/ragstack/python
QDRANT_URL=http://localhost:6333 \
QDRANT_COLLECTION_EXPLICIT=ragstack_sfr_tok256 \
ELASTICSEARCH_INDEX=ragstack_sfr_tok256 \
EMBEDDING_API=openai \
EMBEDDING_MODEL=Salesforce/SFR-Embedding-Mistral \
EMBEDDING_MODEL_DIM=4096 \
EMBEDDING_SIDECAR_URL=http://localhost:9001 \
nohup /rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app \
  --host 0.0.0.0 --port 8000 >> /rag/cache/api_tok256.log 2>&1 &
```

**:8010 (lucid)**:

```bash
cd /rag/repos/ragstack/python
set -a; . /rag/config/lucid.env; set +a
export HF_HOME=/rag/cache
nohup /rag/envs/ragstack/bin/uvicorn ragstack.api.main:app \
  --host 0.0.0.0 --port 8010 >> /rag/config/lucid.uvicorn.log 2>&1 &
```

**:8020 (unified demo)**:

```bash
cd /home/wilke/Development/ragstack/python
set -a; . /rag/config/unified.env; set +a
export HF_HOME=/rag/cache PYTHONPATH=/home/wilke/Development/ragstack/python
nohup /rag/envs/ragstack/bin/uvicorn ragstack.api.main:app \
  --host 0.0.0.0 --port 8020 --log-level warning >> /rag/cache/api_unified.log 2>&1 &
```

### 4. Verify

```bash
curl -s localhost:8000/v1/health                     # {"status":"ok"}
curl -s localhost:8010/v1/health
curl -s localhost:8020/v1/health
# collections still served, counts unchanged:
curl -s localhost:8020/v1/collections | jq '.collections[]|{id,count}'
```

Expect on :8020 — `default` 24,830,600 · `asm-tok512` 12,587,981 · `asm-semantic` 2,982,219.

## What could break, and the symptom

| Change | Symptom if it misbehaves | Reversible by |
|---|---|---|
| **#195** `/v1/ingest` 503s when `INGEST_ROOT` unset | Any automated ingest against :8000 or :8020 starts failing with 503. **This is the most likely disruption** — it is a deliberate behaviour change. | Set `INGEST_ROOT`, or revert the commit |
| **#196** empty multi-value filter matches nothing | A caller passing `filters: {"x": []}` gets zero results instead of unfiltered. No in-repo caller does this. | Revert the commit |
| **#207** graph pseudo-chunks stamped + re-scoped | Fewer graph-leg results if a store returns unstamped triples. Verified impossible for both real stores. | Revert the commit |

None touches stored data, so a revert is a code checkout plus a restart — no reindex, no restore
from backup.

## Deploying the fixes (separate problem)

**A restart does not deploy them to :8000 or :8010.** Those run from a checkout that predates
`main` by 134 commits and carries 10 unmerged ones. Options, in order of preference:

1. **Merge `fix/es-keyword-ignore-above-and-drain-completeness` into `main` first**, then move
   `/rag/repos/ragstack` to `main`. Correct, and clears a long-lived divergence.
2. **Cherry-pick the three fix commits onto the production branch.** Fast, keeps the divergence.
3. Do nothing on :8000/:8010 and accept the exposure until (1) happens.

:8020 is different — it runs `main` from the dev checkout via `PYTHONPATH`, so a restart *does*
deploy. Note the coupling: the demo server depends on a developer working tree, and any
uncommitted edit there is live. Worth moving to its own checkout.

## Snapshot

A machine-readable capture of the state above lives at
`/rag/cache/prod-state-2026-07-31.json`. Re-generate before any future deployment with the same
`/proc/<pid>` reads used here.
