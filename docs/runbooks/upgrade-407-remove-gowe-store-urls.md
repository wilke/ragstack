# Upgrade step — remove the store URLs from `GOWE_WORKFLOW_INPUTS_JSON` (#407)

**Applies to:** the first deploy that carries the #407 fix. One-time, one file.
**Blast radius: exactly one tenant — `dev`.** Verified by a read-only sweep of all
four tenant config dirs plus `/rag/config`, `/rag/bin` and `/rag/env.sh`.

## Why there is a step at all

The #407 workaround is a per-tenant env var:

```
GOWE_WORKFLOW_INPUTS_JSON='{"qdrant_url":"...","es_url":"..."}'
```

The fix makes the API seed those two inputs itself, per run, from `QDRANT_URL` /
`ELASTICSEARCH_URL` (honouring `QDRANT_COLLECTION_ROUTES`). Because
`GoWeBackend.run` merges `{**static_inputs, **per_run_inputs}`, the per-run value
wins — so after the upgrade the blob's copies are **inert**. Config that is inert
while an operator believes it is live is the same failure mode as #407 itself, so
the API **refuses to start** when the blob carries either key, with the remedy in
the message. Remove them in the same deploy.

## Who is affected

| Tenant | `INGEST_BACKEND` | Carries `GOWE_WORKFLOW_INPUTS_JSON` | Action |
|---|---|---|---|
| `dev` | `gowe` | **yes** | edit `tenant.env`, see below |
| `asm-next` | not gowe | no | none |
| `demo` | not gowe | no | none |
| `lucid-next` | not gowe | no | none |

The refusal only runs where the gowe backend is built, so a tenant on the local
backend is untouched even if a stale blob is left behind. Re-verify the table
before the deploy rather than trusting it — a tenant may have been switched:

```bash
grep -l 'INGEST_BACKEND=gowe' /rag/data/tenants/*/config/tenant.env
grep -l 'GOWE_WORKFLOW_INPUTS_JSON' /rag/data/tenants/*/config/tenant.env
```

Only a file in **both** lists needs the edit.

## The edit

In `/rag/data/tenants/dev/config/tenant.env`:

1. Confirm `QDRANT_URL` and `ELASTICSEARCH_URL` in that same file name the
   tenant's own stores (`:24041` / `:24043` for `dev`). **These are now what the
   ingest writes to** — they were previously only the API's own read/serve
   targets, so a file where they disagree with the blob is exactly the case to
   check before deleting anything.
2. Delete the whole `GOWE_WORKFLOW_INPUTS_JSON=` line if the blob has no other
   keys; otherwise drop just `qdrant_url` and `es_url` from the JSON and keep the
   rest (the blob still carries genuine per-deployment extras).
3. Restart that tenant's API **by the pid recorded at launch** (or by resolving
   the port and checking `/proc/<pid>/cwd` first). Never by process-name pattern:
   production runs the same command line as every scratch server (#402).
4. Verify: `/health` answers, and the next ingest's engine-echoed `inputs` carry
   `qdrant_url` = the tenant's `QDRANT_URL`. If the API refuses to boot, the
   error names the remaining key — step 2 was incomplete.

## Rollback

Re-adding the keys puts the API back in the refusing state; it will not restore
the old behaviour, because the seeding is unconditional. To roll back, deploy the
previous build — at which point the workaround becomes load-bearing again and the
blob must be restored with it.

## Related

- `docs/runbooks/live-validation-run-record.md` § BLOCKER B1 — the incident that
  produced the workaround, and the production collection/index it left behind.
- `cwl/README.md` § "Store targets are required inputs" — the CWL half of the fix.
