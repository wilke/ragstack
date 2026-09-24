# Plan — Salmonella AMR corpus: semantic vs semantic_pooled on hackathon (2026-09-24)

Live tenant with real users. Every step gated on the previous. Stop on any surprise.

## Rollback
Delete exactly the two collections this run creates, by id, through the API:
`DELETE /v1/collections/salmonella-amr-semantic` and `DELETE /v1/collections/salmonella-amr-pooled`.
Nothing else is ever deleted or modified. `Salmonella_AMR2` (another user's) is never touched.

## Steps and gates

1. Plan file (this). Gate: written.
2. Baseline: `GET /v1/collections` (owner), Qdrant `/collections`, ES `_cat/indices` → recorded to
   `baseline-*.{json,txt}`. Gate: no `salmonella-amr-semantic` / `salmonella-amr-pooled` in any store.
   Upload bounds on origin/main: max_upload_files=50, max_upload_bytes_per_request=500 MB,
   max_document_bytes=50 MB (tenant sets MAX_DOCUMENT_BYTES=50000000). 20 PDFs / 17 MB → one request,
   one GoWe submission per arm.
3. Arm A: `POST /v1/collections` id=salmonella-amr-semantic method=semantic params={} → 201.
   `POST /v1/ingest/upload` 20 PDFs → 202 job_id. Poll `GET /v1/ingest/{job_id}` every 10 s ≤ 30 min.
   Gate: status=completed; Qdrant points > 0 and == ES doc count for the physical name;
   `gowe: submitted sub_…` in API log; extract/ingest/pack in worker log. On failure: capture, no retry, report.
4. Arm B: same with id=salmonella-amr-pooled method=semantic_pooled. Same gate. Only after A passes.
5. Read back three arms from ES (index = physical name): counts, token-length distribution (HF tokenizer
   if cached under HF_HOME=/rag/cache, else chars), per-doc chunk counts, metadata fields. Gate: 20 docs each.
6. Offline boundary analysis on a COPY of batch-00000.jsonl inside ragstack-worker-v1.6.4.sif,
   endpoints :9005/:9006 only, buffer 3 / p80 / min 500. Save distance series to files. Control run.
7. Record: worktree ~/Development/worktrees/salmonella-compare from origin/main;
   docs/plans/results/salmonella-amr-semantic-vs-pooled-2026-09-24.md + artifacts dir. Commit, push, PR. No merge.

## Credentials
Token only as `$(tr -d '\n' < ~/.patric_token)` inline in curl. Never printed, never in a file.
Any printed log line goes through `sed -E 's/Bearer [^ "]+/Bearer <redacted>/g'`.

## Constraints
No pkill/killall. Backgrounded processes get a pidfile and are stopped by pid, with an `ss -ltn` proof.
No writes to /rag, /scout, hackathon Postgres. ~/Development/ragstack untouched.
