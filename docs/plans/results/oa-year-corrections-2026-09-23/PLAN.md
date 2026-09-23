# oa-year-corrections — plan (2026-09-23)

Target: `ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc`
Qdrant `http://localhost:6333` (never :6343) + ES `http://localhost:9200`. Corrections ONLY. No fills. No optimizer changes.
Python: /rag/envs/ragstack/bin/python. Working dir: this directory. Plan files: ../oa-year-backfill/plan/correct_*.txt

Semantics replicated from canary_write.py / canary_write_es.py:
  Qdrant: POST /points/payload?wait=true {"payload":{"year":Y,"date":Y*10000},"points":[ids]}
  ES:     _bulk update _id="public:"+chunk_id {"doc":{"metadata":{"year":Y,"date":Y*10000}}}
Rollback (rollback.py corrections branch): Qdrant set year=prior + delete key date; ES script year=prior, remove date.

Gates (each must hold before the next step):
 G0 (done, read-only): :6333 points_count 47,625,155 green; ES count 47,625,155; ES year>2026 == 8,208;
    ES has-year 7,294,845; ES has-date 253,008; df /rag free 1152G >= 400G; no leftover pids alive.
 1. Remaining set = union(plan/correct_*.txt) [8,408] minus canary_done.txt -> must be exactly 8,208 ids.
 2. Ledger: read chunk_id,pmcid,year,date for all 8,208 from Qdrant by id -> corrections_ledger.jsonl.
    Checks: 8,208 lines; every prior_year > 2026; prior_year == plan-file prior; prior_date None; all found.
    Cross-check ES: _mget of the 8,208 chunk ids -> metadata.year == prior_year for all.
 3. Dry run: 10 points (seeded sample). Write both stores, verify both, roundtrip rollback_corrections.py on the
    same 10 (prior restored in both stores, date gone), re-apply, re-verify. Report before/after.
 4. Full run: nohup, pid file, BATCH 1000, set_payload idempotent, done-file corrections_done.txt (Qdrant) and
    corrections_es_done.txt (ES). ES mirror via point-id -> chunk_id fetched from Qdrant (as reconcile_es.py).
    Floor: abort if df /rag < 400G or points_count != 47,625,155.
 5. Verify: ES year>2026 == 0; ES has-year == 7,294,845 (unchanged); ES has-date == 253,008 + 8,208 = 261,216;
    Qdrant points_count 47,625,155; random 50 corrected ids == discovery year in BOTH stores;
    all 8,208 in Qdrant year==new_year and date==year*10000; update_queue 0; three du samples identical.
 6. Record docs/plans/results/oa-year-corrections-2026-09-23.md in worktree ~/Development/worktrees/oa-year-corrections
    (from origin/main), commit, push, PR (no merge).
Stop rule: any count off expectation -> stop, report numbers, no fix-forward.
