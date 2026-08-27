# Live validation — state and plan

The first exposure of the personal-collections work to live infrastructure, on the **dev tenant
only**. This file is both the **resume point** (below) and the executable plan (from
"Global rules" on). Written 2026-08-25; the plan section was produced by a planning pass that
verified every fact read-only against the host.

## Where it stands (resume here)

**Done and passing** — Phases 0–4 and the first half of 5:

| | |
|---|---|
| dev tenant code | `main` @ `873090b`, deployed, API healthy on the tenant's port |
| migrations | additive only: `collections` +9 columns, `jobs` +5; **zero rows touched** |
| conformance | 66 passed / 0 failed **when keyed** (see #405 — the suite cannot run keyed as a whole) |
| worker image | rebuilt, all 7 scripts `--help`-clean; sha256 recorded in the run record |
| workers | 4 `ragstack`-group workers restarted **with** `--env-file`/`--secret-file` (they had none — that is why every engine-side task used to fail) |
| Stage A (cwltool) | 5 PDFs → 3 batch chains → verified archive (392 chunks, 5 docs), 31 s, purged back to baseline |
| the user story | **worked live**: token sign-in → create → upload 3 PDFs → job completed (3/3 items, 241 chunks) → archive delivered to the caller's Workspace |

**Blocked, waiting on the GoWe server rebuild:**

- **GoWe#172 / PR #174 (merged, `4a1305e8`) — not yet deployed.** The engine's Workspace
  stage-out marshalled file bytes through a JSON string, so every binary output was corrupted
  (U+FFFD substitution; arithmetic in the issue). Until the engine binary is rebuilt and
  restarted, **every archive the engine delivers is unrestorable** and Phase 9 cannot pass.
  Archives written before the rebuild — including `devlive_mylib`'s `versions/1/` — are
  permanently corrupt and must be regenerated.
- Phases 6 (limits), 7 (the 250-PDF measurement M1), 9 (evict → 503 → restore) are unstarted.
- Phase 8 (graph) additionally needs the rebuilt **worker image** carrying the `graph` extra
  (#404, fixed in `327db76`); the driver is now installed in the shared venv (`neo4j` 5.28.4,
  capped below 6 — `>=5.20` resolved to 6.2.0 against a 5.26 server).

**Live state left in place:** dev worktree at `873090b`; the window settings block in the dev
tenant's env (two `RATE_LIMIT_*` raises revert at cleanup; `GOWE_WORKFLOW_INPUTS_JSON` stays —
it is the WORKAROUND for #407, and must be removed in the same deploy that ships the fix:
see `docs/runbooks/upgrade-407-remove-gowe-store-urls.md`, or that tenant's API refuses to boot); `ADMIN_SUBJECTS` still de-admined for the owner (restore at cleanup —
the original is saved in the run record); a scratch Neo4j 5.26.25 in the tenant's port block;
`devlive_mylib` kept deliberately, corrupt `versions/1` included, as evidence for #172.

**To resume:** rebuild and restart the GoWe engine (queue must be empty; the OA driver shares
those workers), rebuild the worker SIF from `main` so it carries the `graph` extra, then
re-issue Phases 6 → 7 → 9, and Phase 8 once the image lands. Apply the plan defects listed at
the end of the run record (P1–P6) — notably `/v1/whoami` does not exist at this SHA, and
`collection` on the upload endpoint is a **form field**, not a query parameter.

**Findings this run produced:** #404 (no neo4j driver anywhere — `GRAPH_BACKEND=neo4j` cannot
boot), #405 (conformance sends no auth header), #406 (`/v1/config` hides the settings operators
are told to set), #407 (**a dev-tenant ingest wrote to production** — the API never seeds store
URLs and the CWL defaults to production), #408 (`ragstack.*` folder metadata does not persist),
GoWe#172 and GoWe#171.

---

---

## Decisions the owner must make before start (D1–D6)

- **D1 — De-admin `bvbrc:awilke@bvbrc` for the window.** Recommended: yes. Admins are exempt from
  rate limits, quotas, the chunk cap and the in-flight guards, so with the owner as admin the user
  story runs as an admin and **no limit can fire**. Admin operations during the window use the
  admin API key instead. Reverted in Phase 10. (The token authorization itself is settled; this
  narrower point was not explicitly covered.)
- **D2 — Keep or delete `devlive_mylib`** (and its Workspace archive) at the end. Default: delete.
  Keeping it leaves a real personal collection as a demo.
- **D3 — The 35k restore build**: 4× renamed copies of the corpus (~4× Phase 7 wall time, ~2.9 GB
  written into the owner's Workspace) vs measuring restore at ~8k chunks and normalizing per-chunk.
  Default: build it if the 250-PDF run finishes in under ~45 min.
- **D4 — Worker env files stay after the run?** They pin group `ragstack`'s default registry to
  the dev tenant (`jats-ingest.cwl` is unaffected — it passes its own `registry_db` input).
  Default: leave, documented in the report.
- **D5 — Scratch Neo4j retention.** It becomes the dev tenant's `GRAPH_BACKEND`. Default: keep
  the instance running after the run.
- **D6 — Engine binary drift** (deployed v0.14.0 vs source `59b9b73` the reviews read): this plan
  runs against v0.14.0 and does **not** upgrade the engine. See Flag F8 for the accepted risk.

---

## Global rules (bind every step)

- **Token discipline** (owner's constraints — follow verbatim):
  - Read `~/.patric_token` only via `TOKEN=$(cat ~/.patric_token)` in the same shell that uses it.
  - Never echo it; never put it on a command line visible in `ps`. Use a header file:
    `umask 077; printf 'Authorization: Bearer %s\n' "$(cat ~/.patric_token)" > "$SCRATCH/auth.hdr"`
    then `curl -H @"$SCRATCH/auth.hdr" …` (curl ≥ 7.55 — check once with `curl --version`).
  - Never write it into a log, ledger, PR, issue, or captured output; scrub before pasting
    anything anywhere.
  - `shred -u "$SCRATCH/auth.hdr"` in cleanup.
  - The non-secret username is derived once:
    `SUBJ_UN=$(tr '|' '\n' < ~/.patric_token | sed -n 's/^un=//p')` — `$SUBJ_UN` may be printed;
    nothing else from the token may.
- **Process discipline:** never `pkill`/`pgrep -f` patterns — a pattern kill has taken down every
  API on this host before. Stop only by a pid resolved from a port (`ss -lntp`) with
  `/proc/<pid>/cwd` verified, or by a pid file this run wrote. Every server/worker this plan
  starts writes a pid file: `… & echo $! > <name>.pid`.
- **Secrets in env files:** `tenant.env`/`secrets.env` may be sourced and grepped for key NAMES;
  values are never printed. Derive the admin API key into a shell var without echoing, e.g. after
  `set -a; . /rag/data/tenants/dev/config/tenant.env; . /rag/data/tenants/dev/config/secrets.env; set +a`:
  `ADMIN_KEY=$(python3 -c "import os,json; m=json.loads(os.environ['API_KEY_ROLES']); print(next(k for k,v in m.items() if v=='admin'))")`
  (adjust to the actual `API_KEY_ROLES` shape after inspecting `python3 -c "import os;print(type(os.environ['API_KEY_ROLES']))"`
  — never `echo $ADMIN_KEY`). Never read or print `secrets.env` contents beyond sourcing it.
- **Do not touch:** the lucid/asm/demo tenants (`/rag/data/tenants/{lucid,asm,demo}`, port blocks
  24000-24019, 24020-24039, 24060-24079), `:8000/:8010/:8020` (currently down — leave down), the
  shared infra apptainer instances (`qdrant`, `qdrant2`, `elasticsearch`, `elasticsearch-lucid`,
  `postgres`, `redis`, `embedding`, `crossencoder`, and the dead `neo4j`), `/rag/repos/ragstack`,
  the GoWe **server** process, and the non-`ragstack`-group workers (`cpu-worker-1/2`,
  `worker-1/2`, `ragstack-cpu-1`).
- **Scratch naming:** every throwaway collection id starts `devsmoke374_` (Stage A / mechanics)
  or `devlive_` (Stage B / user story). No other names.
- `SCRATCH` = the executing session's scratchpad directory. All temp files, inputs YMLs, header
  files, downloaded archives go there.

## Stop conditions — stop and report rather than continue, on any of:

1. Boot performs anything other than additive `ADD COLUMN` and the known legacy-`default` row
   DELETE (which is a no-op here — the dev registry has only `oa-dev`). Concretely: the
   `collections` row count before/after first boot must be 1 and the id `oa-dev`; **any** row
   deleted at boot = stop.
2. Post-deploy `/health` fails, keyed conformance fails on a test that passed pre-deploy, or any
   unexpected 5xx on `/v1/collections`, `/v1/query`, `/v1/whoami`.
3. A GoWe submission whose echoed inputs (engine UI or DB) name any store other than
   `http://localhost:24041` / `http://localhost:24043`, or any write observed outside
   `/rag/data/tenants/dev/`, `/scout/wf/` (engine work dirs), `$SCRATCH`, and the token owner's
   own Workspace `/<un>@patricbrc.org/home/.ragstack/`.
4. A Workspace write landing outside `/<un>@patricbrc.org/home/.ragstack/`.
5. The token found in any log, `ps` output, or captured artifact → stop, tell the owner to rotate
   it. Do not continue on "probably fine".
6. Fewer than 4 workers re-register after the worker restart, or the probe task (step 5.3) is
   unclaimed after 5 minutes.
7. Qdrant `GET :24041/collections` or ES `:24043/_cat/indices` showing an object this plan didn't
   create that wasn't in the Phase-0 baseline.
8. Production Qdrant (`:6333`, `:6343`) or shared ES (`:9200`) gaining any object during the run
   (baselined in 0.4).

---

## Phase 0 — Snapshot and preconditions (read-only, ~10 min)

**0.1 Fleet snapshot.**
```bash
ss -lntp | grep -E ':(8000|8010|8020|8091|24000|24020|24040|24041|24043|24060)\b'
```
Expected: 24040 (dev uvicorn), 24041 (qdrant), 24043 (ES java), 8091 (gowe-server); :8000/:8010/
:8020 absent (if they have come back up, record their pids and leave them alone). Save the full
`ps -eo pid,args | grep gowe-worker | grep -v grep` output to `$SCRATCH/workers-before.txt` —
it is the rollback record for Phase 3.6.

**0.2 Baseline dev API.** `curl -s localhost:24040/health` → `{"status":"ok"}`.
With the admin key: `curl -s -H "X-API-Key: $ADMIN_KEY" localhost:24040/v1/collections` → record
(expect `oa-dev` plus the settings-derived default surface).

**0.3 Baseline stores (leak-proof reference for Phase 10).**
```bash
curl -s localhost:24041/collections | jq -r '.result.collections[].name' | sort | tee $SCRATCH/qdrant-baseline.txt
curl -s 'localhost:24043/_cat/indices?h=index,docs.count' | sort | tee $SCRATCH/es-baseline.txt
```
Expected Qdrant names: `ragstack_lib_oa_dev_salesforce_sfr_embedding_4096_fixed_token_512_64_e788c5be`
and `ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe`.

**0.4 Baseline production stores** (stop-condition 8 reference — counts only, read-only):
```bash
curl -s localhost:6333/collections | jq '.result.collections|length'
curl -s localhost:6343/collections | jq '.result.collections|length'
curl -s 'localhost:9200/_cat/indices?h=index' | wc -l
```

**0.5 Engine queue.**
```bash
sqlite3 "file:/scout/wf/gowe/gowe.db?mode=ro" \
  "SELECT id,state,workflow_name FROM submissions WHERE state IN ('PENDING','RUNNING')"
```
Expect empty. If a `ragstack`-group submission is live, wait — no worker restart while one runs.

**0.6 Tools.** `command -v cwltool || ls /rag/envs/ragstack/bin/cwltool`; `apptainer --version`;
`curl -s --max-time 5 http://mango.cels.anl.gov:8003/v1/models | head -c 120` (must list the
Scout model); `curl --version | head -1` (≥ 7.55 for `-H @file`).

---

## Phase 1 — Deploy code to the dev tenant (restart 1: code only, ~15 min)

**1.1 Stop the API by pid.**
```bash
PID=$(ss -lntp | sed -n 's/.*:24040 .*pid=\([0-9]*\),.*/\1/p')
readlink /proc/$PID/cwd    # MUST print /rag/repos/tenants/dev/python — else STOP
kill "$PID"; while kill -0 "$PID" 2>/dev/null; do sleep 1; done
```
Check: `ss -lntp | grep :24040` → empty.

**1.2 Back up state (sqlite now quiescent).**
```bash
B=/rag/data/tenants/dev/state/backup-$(date +%Y%m%d); mkdir -p "$B"
cp -a /rag/data/tenants/dev/state/*.db "$B"/
cp -a /rag/data/tenants/dev/config/tenant.env "$B"/tenant.env
ls -la "$B"     # 3 DBs + tenant.env
```

**1.3 Move the worktree.**
```bash
cd /rag/repos/tenants/dev
git status --porcelain     # MUST be empty; if dirty → STOP and report
git checkout --detach 873090b
git log --oneline -1       # expect: 873090b docs(claude): how work gets done ...
```
(The worktree shares its object DB with `/home/wilke/Development/ragstack`, which has the commit —
no fetch needed. Runtime deps are unchanged `74b2a01..873090b` — the only pyproject change is the
dev-extra `pyyaml` — so **no pip install**.)
Rollback: `git checkout --detach 74b2a01`.

**1.4 Start (same env as before — deliberately NO new settings yet).**
```bash
cd /rag/repos/tenants/dev/python
set -a; . /rag/data/tenants/dev/config/tenant.env; . /rag/data/tenants/dev/config/secrets.env; set +a
export HF_HOME=/rag/cache PYTHONPATH=/rag/repos/tenants/dev/python
nohup /rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 0.0.0.0 --port 24040 \
  >> /rag/data/tenants/dev/logs/api-dev.log 2>&1 & echo $! > /rag/data/tenants/dev/api-dev.pid
```

**1.5 Deploy gate — all four must pass:**
1. `curl -s localhost:24040/health` → ok; `tail -50 /rag/data/tenants/dev/logs/api-dev.log` shows
   no traceback.
2. Migration check (additive only):
   ```bash
   sqlite3 /rag/data/tenants/dev/state/ragstack_collections.db "PRAGMA table_info(collections)" \
     | grep -cE 'state|versions|archive_pending|last_accessed_at|graph_archived_versions|archive_version|max_chunks|owner'
   sqlite3 /rag/data/tenants/dev/state/ragstack_jobs.db "PRAGMA table_info(jobs)" | grep kind
   sqlite3 /rag/data/tenants/dev/state/ragstack_collections.db "SELECT count(*), group_concat(id) FROM collections"
   ```
   Last line MUST be `1|oa-dev` (stop-condition 1 otherwise).
3. `curl -s -H "X-API-Key: $ADMIN_KEY" localhost:24040/v1/collections` → `oa-dev` present, state
   null-or-active, counts match 0.2.
4. Keyed conformance — run now, while `INGEST_BACKEND` is still local (the gowe backend refuses
   server-path ingest, which conformance exercises; running it before the env flip keeps clean
   attribution):
   ```bash
   cd /rag/repos/tenants/dev
   RAGSTACK_BASE_URL=http://localhost:24040 RAGSTACK_IMPL=python RAGSTACK_API_KEY="$ADMIN_KEY" \
     /rag/envs/ragstack/bin/python -m pytest conformance/ -q
   ```
   → all pass.

**Rollback (trigger: any gate failure, stop-condition 1 or 2):**
`kill $(cat /rag/data/tenants/dev/api-dev.pid)`; `cd /rag/repos/tenants/dev && git checkout
--detach 74b2a01`; restart with the 1.4 block. Restore `$B`/*.db over `state/` ONLY if the DBs
show corruption — the new columns are ignored by old code, so prefer not restoring. Re-verify
0.2. Report before any further step.

---

## Phase 2 — Scratch Neo4j + window settings (restart 2: env only, ~20 min)

**2.1 Scratch Neo4j** (nothing serves bolt on this host; the graph leg needs a real one; ports
stay inside dev's 24040-24059 block):
```bash
T=/rag/data/tenants/dev/neo4j; mkdir -p $T/{data,logs,conf}
# Seed conf from the image (MEMORY.md: bind targets need image content; Neo4j writes its conf at startup)
apptainer exec --bind $T/conf:/__seed /rag/apptainer/images/neo4j.sif sh -c 'cp -R /var/lib/neo4j/conf/. /__seed/'
NEOPW=$(openssl rand -hex 12)   # NEVER "neo4j" — Neo4j 5 rejects it (MEMORY.md)
# Append to secrets.env WITHOUT printing: 
printf 'NEO4J_PASSWORD=%s\n' "$NEOPW" >> /rag/data/tenants/dev/config/secrets.env
apptainer instance start \
  --bind $T/data:/data --bind $T/logs:/logs --bind $T/conf:/var/lib/neo4j/conf \
  --env NEO4J_AUTH="neo4j/${NEOPW}" \
  --env NEO4J_server_bolt_listen__address=0.0.0.0:24047 \
  --env NEO4J_server_http_listen__address=0.0.0.0:24046 \
  /rag/apptainer/images/neo4j.sif neo4j-dev
```
Check: `ss -lnt | grep -E ':2404[67]\b'` shows both listeners (give it ~60 s), then a bolt
round-trip:
```bash
/rag/envs/ragstack/bin/python - <<'EOF'
import os
from neo4j import GraphDatabase
d = GraphDatabase.driver("bolt://localhost:24047", auth=("neo4j", os.environ["NEO4J_PASSWORD"]))
with d.session() as s: print(s.run("RETURN 1 AS ok").single()["ok"])
EOF
```
(export `NEO4J_PASSWORD` by sourcing `secrets.env`, not by pasting.) If the env-to-conf
translation does not take under apptainer (the shared instance's `NEO4J_AUTH` did), set
`server.bolt.listen_address=0.0.0.0:24047` and `server.http.listen_address=0.0.0.0:24046` in
`$T/conf/neo4j.conf` and restart the instance.
Rollback: `apptainer instance stop neo4j-dev` — the instance and its data dir are dev-scoped
scratch.

**2.2 Edit `tenant.env`.** Editing is safe while the API runs — the file is only read when a new
process sources it; restart 2.3 picks it up. Append one block, every line commented
`# window-2026-08-25`:

Required for the run:
```
INGEST_BACKEND=gowe
GOWE_URL=http://localhost:8091
GOWE_WORKFLOW_CWL=/rag/repos/tenants/dev/cwl/pdf-ingest-scatter.cwl
GOWE_WORKER_GROUP=ragstack
WORKSPACE_URL=https://p3.theseed.org/services/Workspace
GRAPH_BACKEND=neo4j
NEO4J_URI=bolt://localhost:24047
NEO4J_USER=neo4j
```
(`NEO4J_PASSWORD` already lives in `secrets.env` from 2.1.)

Policy made explicit (the #387 gap; values per the tenant-scale-out runbook and the design page):
```
MAX_COLLECTIONS=100            # already present — leave as is
MAX_COLLECTIONS_PER_OWNER=5
MAX_CHUNKS_PER_COLLECTION=50000
ALLOW_USER_COLLECTION_CREATE=true
```

Window-only overrides (REVERT in Phase 10):
```
RATE_LIMIT_COLLECTIONS_CREATE_PER_HOUR=30   # default 5 would fire on the 6th create and mask the owner-quota test
RATE_LIMIT_INGEST_PER_HOUR=60               # the 35k build is ~20 upload jobs; default 10/h blocks it
```

**De-admin the owner (decision D1, default yes):** if `bvbrc:${SUBJ_UN}` appears in
`ADMIN_SUBJECTS`, remove exactly that entry (keep `clark.cucinell`/`olson`). Admin operations in
this plan use `$ADMIN_KEY`.

**2.3 Restart 2** (env only — attribution stays clean against restart 1):
```bash
kill $(cat /rag/data/tenants/dev/api-dev.pid); sleep 2
# then the full 1.4 start block again
```
Check: `/health` ok; `curl -s -H "X-API-Key: $ADMIN_KEY" localhost:24040/v1/config` shows
`ingest_backend: gowe`, `graph_backend: neo4j`, `max_chunks_per_collection: 50000`,
`max_collections_per_owner: 5`. Log clean.
Rollback: remove the appended block (`$B/tenant.env` is the original), restart.

---

## Phase 3 — Worker SIF rebuild + deploy + worker env (the #374 umbrella, ~45 min)

**3.1 Build from `main`** (in `/home/wilke/Development/ragstack`; `git status` must be clean at
`873090b`; sandbox route — `--fakeroot` is unavailable on this host):
```bash
cd /home/wilke/Development/ragstack
cp apptainer/images/ragstack-worker.sif apptainer/images/ragstack-worker.sif.bak-20260809
apptainer build --sandbox /rag/tmp/ragstack-worker.sbx apptainer/ragstack-worker.def
apptainer build apptainer/images/ragstack-worker.sif /rag/tmp/ragstack-worker.sbx
sha256sum apptainer/images/ragstack-worker.sif    # RECORD — #374's image digest
```

**3.2 Mandatory in-container script checks** (a missing script has bitten before — #327):
```bash
SIF=apptainer/images/ragstack-worker.sif
for s in archive_version.py extract_graph.py load_graph.py ingest_shard.py load_embeddings.py embed_shard.py merge_receipts.py; do
  apptainer exec $SIF python /opt/ragstack/scripts/$s --help >/dev/null 2>&1 || echo "MISSING: $s"
done
apptainer exec $SIF python /opt/ragstack/scripts/ingest_shard.py --help | grep -E -- '--extract-report|--max-chunks|--embedding-file'
apptainer exec $SIF python /opt/ragstack/scripts/load_embeddings.py --help | grep -- --replay
apptainer exec $SIF python /opt/ragstack/scripts/archive_version.py --help | grep -- --tombstone
```
All greps must hit; no MISSING lines. Note: issue #374's stated check
`python -m ragstack.ingestion.archive verify` does NOT exist (`ragstack/ingestion/archive.py`
has no `__main__`) — the `verify_version` one-liner in 4.5 substitutes; record that on #374.

**3.3 Tokenizer measurement (M2 — do this BEFORE deploying anything):**
```bash
SNIP='import time;t=time.time();from transformers import AutoTokenizer;AutoTokenizer.from_pretrained("Salesforce/SFR-Embedding-Mistral");print(f"{time.time()-t:.2f}s")'
apptainer exec -B /rag/cache $SIF python -c "$SNIP"            # warm HF_HOME bind: expect ~0.4-1.5 s
timeout 300 apptainer exec $SIF python -c "$SNIP"; echo "exit=$?"   # NO bind — the default-group-worker condition; expect failure or a full download
```
Record both numbers and the unbound failure mode → #203/#374.

**3.4 Deploy the SIF atomically.** Workers resolve the image per-task from `--image-dir` by
filename — the copy alone needs **no** worker restart, and running containers keep the old inode:
```bash
cp /scout/containers/ragstack-worker.sif /scout/containers/ragstack-worker.sif.bak-20260809
cp apptainer/images/ragstack-worker.sif /scout/containers/.ragstack-worker.sif.new
mv /scout/containers/.ragstack-worker.sif.new /scout/containers/ragstack-worker.sif
sha256sum /scout/containers/ragstack-worker.sif    # must equal 3.1
```

**3.5 Worker env files** — closes the engine-path registry gap (Flag F1: the scatter/restore/
graph CWLs carry no registry input; `restore-collection.cwl:31` says the worker env must supply
it; `python/ragstack/ops/ingest_target.py:459-466` exits 2 without it). Create, mode 600:

`/scout/wf/gowe/ragstack-worker-env.env`:
```
COLLECTION_STORE_BACKEND=sqlite
COLLECTION_STORE_PATH=/rag/data/tenants/dev/state/ragstack_collections.db
HF_HOME=/rag/cache
NEO4J_URI=bolt://localhost:24047
NEO4J_USER=neo4j
```
`/scout/wf/gowe/ragstack-worker-secrets.env` (write via a subshell reading `secrets.env`, never
echo): `NEO4J_PASSWORD=<the 2.1 value>`.

**3.6 Restart the 4 `ragstack`-group workers (owner-authorized).**
Blast radius: only group `ragstack`. The OA batch driver (`jats-ingest.cwl`) uses these workers
but supplies its own `registry_db` input, so the new env is a no-op for it; nothing else routes
to the group. The env pins the group's default registry to the dev tenant — decision D4.
Pre-check: repeat 0.5 — **no restart while a PENDING/RUNNING submission exists**.
```bash
for n in 1 2 3 4; do
  P=$(ps -eo pid,args | awk -v pat="--name ragstack-oa-$n " '$0 ~ pat && /gowe-worker/ {print $1}')
  tr '\0' ' ' < /proc/$P/cmdline | grep -q -- "--name ragstack-oa-$n" && kill $P   # verify argv, kill by pid — NEVER a pattern kill
done
cd /scout/Experiments/GoWe
for n in 1 2 3 4; do
  nohup ./bin/gowe-worker --server http://localhost:8091 --name ragstack-oa-$n --group ragstack \
    --runtime apptainer --image-dir /scout/containers --extra-bind /rag \
    --stage-out file:///scout/wf/data --workdir /scout/wf/gowe/workdir/ragstack-oa-$n \
    --poll 500ms --log-level info \
    --env-file /scout/wf/gowe/ragstack-worker-env.env \
    --secret-file /scout/wf/gowe/ragstack-worker-secrets.env \
    >> /scout/wf/gowe/logs/ragstack-oa-$n.restart.log 2>&1 & echo $! > /scout/wf/gowe/pids-ragstack-oa-$n.pid
done
```
Check: `ps -eo args | grep -c 'gowe-worker.*--group ragstack '` → 4; each `.restart.log` shows
registration. The real probe is Stage B's first submission (5.3): claimed by a `ragstack` worker
and COMPLETED. Fewer than 4 registered, or the probe unclaimed after 5 min → stop-condition 6.
Rollback: kill by the pid files, restart each with its ORIGINAL argv from
`$SCRATCH/workers-before.txt`, and `mv /scout/containers/ragstack-worker.sif.bak-20260809` back
into place.

---

## Phase 4 — Stage A: cwltool, no credential (~30 min)

Proves without any token or engine: the new SIF runs every step; the `batch` ExpressionTool
scatter works; `ingest_shard` writes the dev stores; the archive step emits a verified
`versions/1/`. **Hard dependency on Phase 1**: the SIF's `ingest_target` SELECTs the new
lifecycle columns from the dev sqlite — against the unmigrated DB it dies on
`no such column: state`.

**4.1 Registry entry** (admin key — Stage A is mechanics, not the user story):
```bash
curl -s -X POST -H "X-API-Key: $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"id":"devsmoke374_a"}' localhost:24040/v1/collections     # → 201
```

**4.2 Physical names + build spec:**
```bash
sqlite3 /rag/data/tenants/dev/state/ragstack_collections.db \
  "SELECT collection,text_index,chunk_method,chunk_size,chunk_overlap FROM collections WHERE id='devsmoke374_a'"
```

**4.3 Inputs** `$SCRATCH/stageA.yml` (values from 4.2 where marked):
```yaml
pdfs:
  - {class: File, path: /rag/data/g1-corpus/pdfs/PMC10100743.pdf}
  - {class: File, path: /rag/data/g1-corpus/pdfs/PMC10100882.pdf}
  - {class: File, path: /rag/data/g1-corpus/pdfs/PMC10105658.pdf}
  - {class: File, path: /rag/data/g1-corpus/pdfs/PMC10433920.pdf}
  - {class: File, path: /rag/data/g1-corpus/pdfs/PMC6521588.pdf}
batch_size: 2                       # 5 PDFs -> 3 task chains: exercises the scatter
collection: <collection from 4.2>
es_index: <text_index from 4.2>
tenant: devsmoke374_t
chunk_method: <from 4.2>
chunk_size: <from 4.2>
chunk_overlap: <from 4.2>           # a mismatch is refused by the build-spec check — that refusal would itself be a finding
embedding_url: [http://localhost:9001, http://localhost:9002]
embedding_model: Salesforce/SFR-Embedding-Mistral
qdrant_url: http://localhost:24041
es_url: http://localhost:24043
version: "1"
collection_id: devsmoke374_a
```

**4.4 Run** (from the deployed worktree so CWL and SIF are the same SHA):
```bash
cd /rag/repos/tenants/dev
CWL_SINGULARITY_CACHE=/home/wilke/Development/ragstack/apptainer/images \
APPTAINER_BIND=/rag HF_HOME=/rag/cache \
APPTAINERENV_COLLECTION_STORE_BACKEND=sqlite \
APPTAINERENV_COLLECTION_STORE_PATH=/rag/data/tenants/dev/state/ragstack_collections.db \
/usr/bin/time -v cwltool --singularity --outdir "$SCRATCH/stageA-out" \
  cwl/pdf-ingest-scatter.cwl "$SCRATCH/stageA.yml"
```
(`APPTAINERENV_*` injects into the container regardless of cwltool's env sanitization. Fallback
if the registry still isn't visible in-container: add
`--preserve-environment COLLECTION_STORE_BACKEND --preserve-environment COLLECTION_STORE_PATH`.)
Expected: 3 extract→ingest chains + one pack; `$SCRATCH/stageA-out/1/` exists.

**4.5 Verify the archive:**
```bash
ls "$SCRATCH/stageA-out/1"    # manifest.json chunks.jsonl.gz vectors.f32 receipt.json — nothing else
/rag/envs/ragstack/bin/python - <<EOF
from ragstack.ingestion.archive import verify_version
import json; print(json.dumps(verify_version("$SCRATCH/stageA-out/1")["counts"]))
EOF
```
`verify_version` checks every sha256 + vector geometry; expect `docs: 5`, chunks ~100-180.
`receipt.json` is a JSON array of 3 ShardReceipts, each with a `docs` row per PDF.
Store check: `curl -s localhost:24041/collections/<physical>` points count matches; the ES index
has docs.

**4.6 Record and clean.** Record command, wall time, image digest → **#374/#357**. Then:
```bash
curl -s -X DELETE -H "X-API-Key: $ADMIN_KEY" 'localhost:24040/v1/collections/devsmoke374_a?purge=true'
```
Confirm the physical store and index are gone (compare to 0.3 baselines). Failure rollback:
nothing persists except the store writes — the purge is the rollback.

---

## Phase 5 — Stage B1: the user story, end to end (~30 min + job time)

What Stage B proves that Stage A cannot: the **real Workspace** RPC shape (#372 has never called
one), submit-**as-user** auth through the API (#375), engine pre-stage of `ws://` inputs and
post-stage of the archive with the caller's token, the delivery wait (#382), and — later —
restore and graph delivery. Runs as a **plain user** (post-2.2 de-admin), with the owner's token
under the global rules.

**5.1 Sign in.**
```bash
umask 077; printf 'Authorization: Bearer %s\n' "$(cat ~/.patric_token)" > "$SCRATCH/auth.hdr"
curl -s -H @"$SCRATCH/auth.hdr" localhost:24040/v1/whoami
```
→ identity `bvbrc:${SUBJ_UN}`, role **user**. If it says admin, the de-admin edit didn't take —
stop and fix before any limit test.

**5.2 Create.**
```bash
curl -s -X POST -H @"$SCRATCH/auth.hdr" -H 'Content-Type: application/json' \
  -d '{"id":"devlive_mylib"}' localhost:24040/v1/collections    # → 201
sqlite3 /rag/data/tenants/dev/state/ragstack_collections.db \
  "SELECT owner FROM collections WHERE id='devlive_mylib'"      # → bvbrc:<un>
```

**5.3 Upload 3 PDFs** (doubles as the worker-restart probe):
```bash
curl -s -H @"$SCRATCH/auth.hdr" \
  -F 'files=@/rag/data/g1-corpus/pdfs/PMC10100743.pdf' \
  -F 'files=@/rag/data/g1-corpus/pdfs/PMC10100882.pdf' \
  -F 'files=@/rag/data/g1-corpus/pdfs/PMC10105658.pdf' \
  'localhost:24040/v1/ingest/upload?collection=devlive_mylib'   # → 202 + job_id
```
Watch `/scout/wf/gowe/logs/ragstack-oa-*.restart.log` for the task claim.

**5.4 Poll** `curl -s -H @"$SCRATCH/auth.hdr" localhost:24040/v1/ingest/<job_id>` every ~5 s →
`completed`, all 3 items `completed` with non-empty `chunk_ids`. Expected first-contact failure
points, in likelihood order: (a) `Workspace.create`/`Workspace.update_metadata` RPC rejection
(Flag F5 — capture the scrubbed error verbatim); (b) engine pre-stage failure; (c)
`OUTPUT_STAGING_FAILED` after the 600 s delivery wait (Flag F8). Each is stop-and-report with the
engine submission id — do not retry blindly.

**5.5 Query grounded.**
```bash
curl -s -H @"$SCRATCH/auth.hdr" -H 'Content-Type: application/json' \
  -d '{"query":"<a question about one of the 3 papers>","collection":"devlive_mylib"}' \
  localhost:24040/v1/query
```
→ an answer whose sources cite the uploaded documents. **This completes the design page's user
story — record the full walk (scrubbed request/response snippets) → epic #201 / design page (M6).**

**5.6 Archive layout vs the page.** With a python snippet using
`ragstack.workspace.WorkspaceClient` and the token from an env var (never argv):
- `ls /<SUBJ_UN>@patricbrc.org/home/.ragstack/collections/devlive_mylib/` → `sources/` holds the
  3 PDFs; `versions/1/` holds exactly `manifest.json, chunks.jsonl.gz, vectors.f32, receipt.json`.
- Folder metadata carries the four `ragstack.*` keys.
- Download `versions/1/` to `$SCRATCH` and run the 4.5 `verify_version` check on the **Workspace
  copy** — sha256s verify end-to-end.
- Confirm no Qdrant snapshot is archived; `manifest.json` says `graph: false`.
Divergences from the page to RECORD, not fix: the chunks/triples files are **`.gz`, not `.zst`**
(`docs/ingest-paths.md:137-160` is the shipped truth); anything else observed.

---

## Phase 6 — Limits, live (~20 min)

All as the plain user. Record each firing → **#87/#377/#291/#384** (M7).

**6.1 Per-request bound (cheap):** upload 51 tiny files in one request
(`mkdir $SCRATCH/up; for i in $(seq 51); do cp <small.pdf> $SCRATCH/up/f$i.pdf; done`; build the
51 `-F` args with a loop) → **413** (`max_upload_files=50`). Note in the report that the 500 MB
bound is deliberately not exercised.

**6.2 In-flight guard:** while a job from 6.3 or Phase 7 is running, submit another small upload
→ **429 + Retry-After**.

**6.3 Chunk cap, cheap (#291):** create `devlive_captest` (user header), then:
```bash
sqlite3 /rag/data/tenants/dev/state/ragstack_collections.db \
  "UPDATE collections SET max_chunks=50 WHERE id='devlive_captest'"
kill $(cat /rag/data/tenants/dev/api-dev.pid); sleep 2   # registry entries build at startup — a live UPDATE is invisible (F10)
# restart with the 1.4 block
```
Upload 3 PDFs (~90-100 chunks) → whole job `failed`, label `chunk_cap_exceeded`, refusal string
`chunk_cap_exceeded: live=0 incoming=<n> cap=50 would_fit=<w>` on every item, **nothing written**
(Qdrant count for its physical store = 0). Purge `devlive_captest` (admin key). State explicitly:
the full-size 50k cap is deliberately not exercised.

**6.4 Owner quota (#384):** with `devlive_mylib` counting as 1, create owned collections up to 5
total, then a 6th → refused with the quota error, NOT a rate limit (that's what the window's
`RATE_LIMIT_COLLECTIONS_CREATE_PER_HOUR=30` guarantees). Purge the fillers (admin key).

**6.5 MAX_COLLECTIONS / admission:** not force-fired at 100 (would need ~100 collections); the
admission logic is exercised for real in Phase 9. Say so in the report.

---

## Phase 7 — The 250-PDF measurement (M1; ~1-2 h)

One submission cannot carry 250 files: `max_upload_files=50` (`config.py:396`) bounds the upload
path, and `POST /v1/ingest`'s ws:// path takes a single source reference with folder enumeration
unproven (Flag F9). So: **5 sequential upload jobs of 50** into `devlive_big`, serialized
naturally by the in-flight guard. `batch_size` stays the workflow default **20** (the API doesn't
pass it) → each 50-file job = 3 batches (20/20/10); 13 batch-tasks total across the 4 `ragstack`
workers.

**7.1** Create `devlive_big` (user header).
**7.2** For j = 1..5: timestamp; upload files 50(j-1)+1..50j
(`ls /rag/data/g1-corpus/pdfs/*.pdf | sed -n "$((50*j-49)),$((50*j))p"` to pick them; build the
`-F` list with a loop); poll to `completed`; timestamp. A submit landing while the previous job
runs gets the 6.2 429 — that is the guard working; honor `Retry-After`.
**7.3 Numbers to record → #203 (2b budgets), noted on #382/#357:**
- total wall (first submit → last job complete); per-job walls;
- per-task walls from the engine:
  `sqlite3 "file:/scout/wf/gowe/gowe.db?mode=ro" "SELECT ... FROM tasks WHERE submission_id=..."`
  (discover the schema with `.tables`/`.schema tasks` first);
- per-PDF wall = total/250; per-task fixed overhead = task wall − (extract+embed time from the
  receipt), vs the budgeted ~2-4 s/task amortized to ~0.1-0.2 s/PDF at batch 20;
- worker distribution (all 4 workers claimed tasks).
**7.4 Post:** live Qdrant count for `devlive_big`'s physical store (~7-8k chunks at ~30/PDF);
Workspace `versions/1..5` exist; registry row `versions=[1..5]`, `archive_pending=false` (0 =
false in sqlite).
**7.5 The 35k build (decision D3; default: proceed if 7.3's wall < ~45 min).** Re-ingesting the
SAME files does not grow the live count — doc ids are path-deterministic
(`ragstack/ingestion/loaders.py:34,94`), so delete-prior nets to zero. Build ~1,000 distinct
docs by renaming:
```bash
mkdir -p $SCRATCH/big
for c in 2 3 4 5; do for f in /rag/data/g1-corpus/pdfs/*.pdf; do cp "$f" "$SCRATCH/big/c$c-$(basename $f)"; done; done
```
then 15 more 50-file upload jobs into `devlive_big` (the window's `RATE_LIMIT_INGEST_PER_HOUR=60`
covers it). End state: ~35k chunks, `versions=[1..20]`, ~2.9 GB total in the owner's Workspace
(flagged to the owner; deleted in cleanup unless D2 says keep). Fallback if too slow: keep
`devlive_big` at ~8k chunks and normalize the Phase 9 restore number per-chunk against the
runbook's ~100 s / 35k-chunk store-side floor.

---

## Phase 8 — Graph leg: real LLM + real Neo4j (~30 min + extraction time)

**8.1 Pre-flight.** One keyless chat call:
```bash
curl -s --max-time 30 -H 'Content-Type: application/json' \
  -d '{"model":"RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic","messages":[{"role":"user","content":"say ok"}],"max_tokens":5}' \
  http://mango.cels.anl.gov:8003/v1/chat/completions | head -c 200
```
If it 401s, `OPENAI_API_KEY` must be added to the worker secrets file (a worker restart) —
surface to the owner first. Hermetic baseline: `cd /home/wilke/Development/ragstack && make
perf-python` (or `cd python && pytest tests/perf/test_extract_graph_perf.py -m perf -v`) → the
fake-LLM rate for comparison.

**8.2 Small live run first.** `POST /v1/collections/devlive_mylib/graph` (user header) → 202 +
job. The engine runs `graph-extract` on a `ragstack` worker (Neo4j creds from 3.5 —
`graph-extract.cwl:29`). On completion:
- the delta is post-staged **onto** `versions/1/`: `manifest.json` overwritten (`graph: true`,
  `triples` role), `triples.jsonl.gz` added, chunk/vector/receipt files untouched — verify by
  re-downloading the version and running `verify_version` + `verify_triples` on it;
- registry `graph_archived_versions=[1]`;
- Neo4j holds the triples (driver query, physical name from the registry row):
  `MATCH ()-[r:REL {collection:$c}]->() RETURN count(r)` → > 0. First live execution of the
  #391/#398/#399 Cypher.

**8.3 Idempotency:** repeat the POST → 202 with `job_id: null` (leg exists).

**8.4 Throughput (M3):** `POST /v1/collections/devlive_big/graph` → chunks/s =
`n_chunks / wall` from the `graph_extraction` summary (concurrency 8). Budget warning: at
~0.5-2 s per LLM call this is 0.5-2.5 h for 8k chunks and proportionally more at 35k — run it on
the **~8k state** (before 7.5's growth, or skip growth until after; chunks/s is the metric, size
is not). Record → **#350** (sizes `graph_extraction_jobs_per_owner`, #355).

**8.5 Query-side (#398):** `POST /v1/query` (user header) with an entity term known to be in the
triples → 200, no 5xx. If the response surfaces the graph leg, record it; if inert, record that
too (fail-open by design).

**8.6 Guard:** a second graph POST while 8.4 runs → **429** (`graph_extraction_jobs_per_owner=1`).

---

## Phase 9 — Evict → 503 → restore → active (M4; ~15 min + restore time)

Target: `devlive_big` (archive current per 7.4: `archive_pending=false`, `versions` non-empty;
`oa-dev` has no archive → never an eviction candidate).

**9.1 Dry run** (admin key):
`POST 'localhost:24040/v1/admin/collections/evict?need=1&dry_run=true'` → `devlive_big` is the
LRU candidate (touch the other collections first — any authenticated read — if it is not).

**9.2 Evict:** same without `dry_run` → row `dormant`; Qdrant collection and ES index **gone**
(check against the 0.3 baselines). **Graph expectation — eviction does NOT drop triples:**
`run_eviction` deliberately passes no `graph_store=` (`ragstack/api/eviction.py:179-183` — the
leg stays until #350's archive can restore it; #380 remains open for the gate). So the Neo4j
triple count for `devlive_big`'s physical name must be **UNCHANGED** after eviction:
```bash
# same driver query as 8.2: MATCH ()-[r:REL {collection:$c}]->() RETURN count(r)
```
→ equal to the 8.2/8.4 count. **A count of 0 here is a finding (something dropped the leg that
shouldn't have), not a pass.** Purge and tombstone-replay are the paths that DO drop triples —
the purge half is verified in Phase 10.

**9.3 Access as user:** `POST /v1/query` on `devlive_big` (user header) → **503 + Retry-After**,
and the restore submission auto-triggers (or trigger explicitly:
`POST /v1/collections/devlive_big/restore` with the user header). Timestamp.

**9.4 Poll to `active`** (admin key listing, filtered on the id). **Restore cold start** = 9.3 →
active wall. Expected: at least the ~100 s / 35k-chunk store-side floor **plus** archive download
+ replay of up to 20 versions; at 8k chunks normalize per-chunk. Record → **#358** (against its
~1 min assumption) and the active-collection-bound runbook.

**9.5 Verify:** chunk count equals pre-evict; the 5.5-style query answers again; the graph leg is
back in Neo4j — replay loads a version's triples after its chunks when the worker has a graph
store, and versions with a leg exist. Compare the post-restore triple count to 9.2's (should
match; the MERGE keys make replay idempotent over the surviving edges).

Rollback if restore fails (`lost` → 409): the archive is intact in the Workspace; capture the
loader's exit-3 stderr (`ArchiveCorrupt:` / `SpecMismatch:` line) — that is a first-contact
finding, not data loss; the collection is scratch.

---

## Phase 10 — Cleanup and leak proof (~20 min)

**10.1 Verify #399's purge-drops-triples live, then purge everything scratch.** First, with
`devlive_mylib` still holding triples (from 8.2), purge it (admin key; subject to D2):
```bash
curl -s -X DELETE -H "X-API-Key: $ADMIN_KEY" 'localhost:24040/v1/collections/devlive_mylib?purge=true'
# then the 8.2 driver query for its physical name -> MUST now be 0  (the #399 purge half, live)
```
Then purge every remaining `devsmoke374_*` / `devlive_*` id the same way. Purge removes
registry + Qdrant + ES + manifest + triples — **not** the Workspace archive.

**10.2 Workspace cleanup** (token, WorkspaceClient snippet): delete
`/<SUBJ_UN>@patricbrc.org/home/.ragstack/collections/<id>` for each purged id; then list
`.ragstack/collections/` → only ids the owner chose to keep (D2).

**10.3 Leak-proof listings — must match the Phase-0 baselines exactly:**
```bash
curl -s localhost:24041/collections | jq -r '.result.collections[].name' | sort | diff - $SCRATCH/qdrant-baseline.txt
curl -s 'localhost:24043/_cat/indices?h=index,docs.count' | sort | diff - $SCRATCH/es-baseline.txt
sqlite3 /rag/data/tenants/dev/state/ragstack_collections.db "SELECT id FROM collections"   # -> oa-dev only
# Neo4j: total REL count -> 0 (or only kept collections' counts)
# production unchanged (0.4 counts):
curl -s localhost:6333/collections | jq '.result.collections|length'; curl -s localhost:6343/collections | jq '.result.collections|length'
cd /rag/repos/tenants/dev/python && python scripts/store_inventory.py \
  --tenants-dir /rag/data/tenants --env dev=/rag/data/tenants/dev/config/tenant.env   # no unclaimed stores
```

**10.4 Revert window-only settings** in `tenant.env`: the two `RATE_LIMIT_*` raises; re-add
`bvbrc:awilke@bvbrc` to `ADMIN_SUBJECTS` (unless the owner says otherwise). **Keep** the
deployment settings (`INGEST_BACKEND=gowe`, `GOWE_*`, `WORKSPACE_URL`, `GRAPH_BACKEND=neo4j`,
`NEO4J_*`, the explicit `MAX_*`/`ALLOW_*` policy lines — they are the point of the deploy).
Restart (pid discipline, 1.4 block); `/health` + `GET /v1/config` re-check.

**10.5 Token hygiene:** `shred -u "$SCRATCH/auth.hdr"`; leakage scan without printing the token:
```bash
grep -RIl 'sig=' /rag/data/tenants/dev/logs "$SCRATCH" | head    # BV-BRC tokens carry sig=; any hit = stop-condition 5
```

**10.6 Leave in place, stated in the report:** the new SIF (+ dated `.bak`s in both locations),
the worker env files and restarted workers (D4), the scratch Neo4j (D5), the deployed `873090b`
worktree.

**10.7 Pid accounting:** `ls $SCRATCH/*.pid /rag/data/tenants/dev/*.pid /scout/wf/gowe/pids-*.pid`
— account for every pid file: the API and the 4 workers stay (their files stay beside them);
anything else this run started is killed by its pid file.

---

## Measurements → where each number lands

| # | Measurement | Lands on |
|---|---|---|
| M1 | 250-PDF wall, per-job/per-task walls, per-PDF + per-task overhead at batch_size 20 on 4 workers | **#203** (2b budgets); noted on #382/#357 |
| M2 | Tokenizer load in-container: warm `/rag/cache` bind vs unbound (incl. failure mode) | **#203 / #374** |
| M3 | extract-graph chunks/s vs the real LLM (mango Scout, concurrency 8) + hermetic-fake baseline | **#350** (sizes #355's `graph_extraction_jobs_per_owner`) |
| M4 | Restore cold start of a real archived collection (503→active wall; per-chunk normalized vs the ~100 s/35k store-side floor) | **#358**; active-collection-bound runbook |
| M5 | SIF sha256 + in-container `--help` matrix + Stage A run record (command, wall, digest) | **#374**, closing **#357** |
| M6 | User story walk transcript + Workspace archive-layout verification vs the design page | **#201** / design page corrections |
| M7 | Limits fired live: 413 per-request, 429 in-flight, owner quota, `chunk_cap_exceeded`, 429 graph-per-owner | **#87/#377/#291/#384/#350** |

## Flags — expected first-contact failures and findings (evidence, file:line)

- **F1 (will fail; fixed by Phase 3.5/3.6):** engine-path steps cannot see the registry.
  `pdf-ingest-scatter.cwl` / `restore-collection.cwl` / `graph-extract.cwl` carry no registry
  input (`restore-collection.cwl:31` says the worker env must supply it);
  `ingest_target.resolve_or_exit` (`python/ragstack/ops/ingest_target.py:459-466`) exits 2
  without `COLLECTION_STORE_*` env; the running `ragstack-oa` workers have no `--env-file`
  (ps capture, planning time). Every engine ingest/restore/graph task fails until the worker env
  files exist.
- **F2 (doc contradiction; resolved by `GOWE_WORKER_GROUP=ragstack`):** `cwl/README.md:126-130`
  says leave the worker group unset — but the default-group workers bind `/scout/data`, not
  `/rag`, so the image's `HF_HOME=/rag/cache` (`apptainer/ragstack-worker.def` `%environment`)
  is unbound there → per-task tokenizer download/failure. README needs correcting.
- **F3:** the shared `neo4j` apptainer instance is dead (appinit alive, no bolt listener
  host-wide) — #391/#398/#399/#401 Cypher meets a live Neo4j only via the Phase-2 scratch
  instance. Also: no `NEO4J_*` or `OPENAI_API_KEY` reaches any worker container today
  (`extract-graph.cwl:26`, `graph-extract.cwl:29`; the existing worker env files hold only HF
  cache vars).
- **F4:** issue #374's acceptance command `python -m ragstack.ingestion.archive verify` does not
  exist (`python/ragstack/ingestion/archive.py` has no `__main__`) — the `verify_version`
  one-liner (4.5) substitutes; record on #374.
- **F5 (watch in 5.4):** `WorkspaceClient` has never hit a real Workspace. Its JSON-RPC 1.1
  shape (`python/ragstack/workspace.py:439-447`: `params: [dict]`, raw token in `Authorization`)
  matches GoWe's proven Go client, but `Workspace.update_metadata` with
  `{"objects": [[path, meta]], "append": 1}` (`workspace.py:273-274`) has no proven counterpart
  in GoWe's client — the metadata-backfill path is the likeliest RPC to be shaped wrong.
- **F6:** dev `tenant.env` had `GRAPH_BACKEND=disabled` and none of the GoWe/Workspace/limit
  keys (the #387 gap, confirmed key-by-key at planning time) — nothing GoWe- or graph-shaped
  could have worked on this tenant before Phase 2.
- **F7 (page/doc corrections):** (a) the chunks/triples archive files ship as `.jsonl.gz`, not
  `.zst` (`docs/ingest-paths.md:137,155`); (b) `default` is a pointer, not a registry entry
  (commit `071fcb8`); (c) `MAX_COLLECTIONS` guidance is "100 now; ~150 defensible" per the
  measured runbook; (d) **eviction keeps the graph leg** — `run_eviction` deliberately passes no
  `graph_store=` (`ragstack/api/eviction.py:179-183`), gated until #350's leg can be restored
  (#380 stays open); purge and tombstone-replay are the paths that drop triples
  (`ops/evict.py:361` only drops when passed; `ingestion/load_embeddings.py` tombstone branch).
  The design page's "eviction keeps the graph" line is correct as written.
- **F8 (accepted risk, D6):** deployed engine is v0.14.0 while the reviews read `59b9b73`
  (5 commits ahead). Material drift: v0.14.0's post-stage loop lists only the 100 most-recently-
  **created** COMPLETED submissions per tick (`internal/scheduler/workspace.go` pre-`e73c511`;
  the engine DB already holds 1,172 COMPLETED rows) — fine during this run (our submissions are
  newest), but a submission preceded by 100+ newer ones would never be delivered on the deployed
  binary; the HEAD commits fix it. Record on the GoWe side.
- **F9 (path limitation; shaped Phase 7):** one submission cannot carry 250 PDFs —
  `max_upload_files=50` (`python/ragstack/config.py:396`) bounds the upload path, and
  `POST /v1/ingest`'s ws:// path takes a single source reference
  (`python/ragstack/api/routers/documents.py:826-828`) with folder enumeration unproven — hence
  5×50 jobs.
- **F10 (minor):** registry edits (e.g. 6.3's `max_chunks` UPDATE) are invisible until an API
  restart — the collection registry is built once at startup
  (`ragstack/api/deps.py::_build_collection_registry`; the tenant-scale-out runbook documents the
  same for hand-moved rows).
