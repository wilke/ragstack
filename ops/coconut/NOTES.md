# coconut service inventory — notes for the 2026-09-10 patch-and-reboot

**Scope.** Everything that answers on a port on `coconut` plus the non-listening processes that
have to come back (GoWe workers, the confirmation-run labelers), what each one is for, how it was
actually launched (read from `/proc`, not from the older scripts), and what an unattended reboot
would lose. `mango` is covered only as far as it can be seen from here. The generated tables
below this note come from `snapshot.sh`; the baseline for tomorrow lives in
`/rag/backups/reboot-2026-09-10/`.

**Uptime is 113 days** (since 2026-05-19). Nothing in this stack has ever been through a reboot,
and **nothing in it starts at boot**: no systemd units, no user lingering, no cron. After the
reboot the host comes up with the system daemons only. Every service below is restored by
`restore.sh` in dependency order, except the items in "needs an admin" and "not restored".

## The four things that need an admin while they have root tomorrow

1. **Persist `vm.max_map_count=262144`.** It is set now but not in `/etc/sysctl.conf` or
   `/etc/sysctl.d/`, so it reverts to 65530 on boot and **all three Elasticsearch instances refuse
   to start** (the tenants' BM25 leg, i.e. every hybrid query, dies with them). One line:
   `echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/90-elasticsearch.conf`. While there,
   `vm.overcommit_memory=1` silences Redis's background-save warning.
2. **Install the gateway unit.** nginx `:9000` (the only path users reach the tenant APIs and UIs
   through) runs as `svcbvbrc` from an interactive start on 2026-08-05; `coconut-proxy.service` is
   written but not installed. `sudo cp /rag/config/proxy/coconut-proxy.service /etc/systemd/system/
   && sudo systemctl daemon-reload && sudo systemctl enable --now coconut-proxy`. Without it the
   gateway stays down until someone with the `svcbvbrc` key starts it by hand, or `restore.sh
   --proxy` starts it under `wilke` with a regenerated self-signed cert.
3. **`loginctl enable-linger wilke`** (optional) so a future move of the tenant APIs to user
   systemd units survives reboots. Not needed for tomorrow.
4. **On mango**, restart the three vLLM endpoints (`:8000` Qwen3.6-27B, `:8003` Llama-4-Scout,
   `:8004` Qwen3.6-35B-A3B) with the same served ids; the `verify.sh` table shows the served model,
   `max_model_len` and cache config they had. While `:8004` is being restarted: `--max-num-seqs 64`
   (it is 4 today, which is the labeler's throughput ceiling — see the memory note).

## Findings that were not known before this inventory

- **The production Neo4j has been dead since 2026-06-04.** Its apptainer instance is listed, but
  the JVM inside exited that day and nothing listens on 7474/7687. No tenant noticed because all
  four have `GRAPH_BACKEND=disabled`. `restore.sh` starts it best-effort and does not fail on it.
- **The legacy APIs `:8000` (asm) and `:8010` (lucid) are already down**; the gateway answers 502
  for `/ragstack/asm/` and `/ragstack/lucid/`. Their UIs (`:5173`, `:5175`) are still up and point
  at dead backends (`:5175` at `:8020`, which is also gone). They are not restored by default.
- **Live launch parameters differ from the scripts in `apptainer/`**: the shared Elasticsearch runs
  with a 1 GB heap (script says 512 MB), the shared Qdrant with `OPTIMIZER_CPU_BUDGET=12` and
  `MAX_OPTIMIZATION_THREADS=1` (the #140 cap) plus a `/rag/cache/load3corpus` bind (an artefact of the
  directory it was launched from, reproduced anyway), `qdrant2` on
  6343/6344 has no script at all, and `neo4j-dev` was started by hand from a runbook. `restore.sh`
  reproduces the live parameters. The `demo` tenant's `up.sh` would start a `qdrant-demo` /
  `elasticsearch-demo` pair that is **not** in use (demo reads the production stores) — do not run it.
- **`/scout/wf/gowe/server.log` is 8 GB** with no rotation. Not a reboot problem; worth a logrotate.
- The GoWe pid files under `/scout/wf/gowe/pids/` are from June and stale; 19 of the 21 workers
  were started by hand and have no pid file. `pre-reboot.sh` resolves them from `/proc` with a cwd
  check instead. `restore.sh` records pids under `/rag/backups/reboot-2026-09-10/pids/` by resolving
  the port owner (or the `--name` on the cmdline) after the health check — an independent review
  caught that `$!` of a detached launch is the wrapper shell, not the service.

## What the reboot loses even with a perfect restore

- The Qwen labeler will be mid-run (≈ 37 h of work left on 2026-09-09; Scout finished its 74,662
  records on 2026-09-09 09:00 UTC). Qwen is checkpointed per record; `restore.sh labelers` resumes
  it and skips Scout because its heartbeat says finished. Expect the in-flight requests to be redone.
- The docs server on `:8899` serves from `/tmp`, which is cleared. Rebuild if wanted.
- Anything under `/tmp` and `/dev/shm`. The study's working data is under `/rag/tmp` (a real disk).
- GPU placement: `restore.sh` pins SFR `:900N` to GPU `N-1` and the crossencoder to GPU 0, as
  today. GPUs 6 and 7 stay free by convention. **Known tight spot:** GPU 0 has 1.3 GB free today with
  the warm crossencoder at 12.4 GB; a freshly started crossencoder holds less, so vLLM's 0.9
  utilisation will size a larger cache and the crossencoder can OOM as it warms. If that happens,
  restart the crossencoder on GPU 6 (`CROSSENCODER_GPU=6 sidecars-up.sh`) — a placement decision
  for the owner, not made here.

## Order of operations tomorrow

1. Before the window: re-take the baseline **immediately before** stopping anything —
   `FORCE=1 ops/coconut/snapshot.sh /rag/backups/reboot-2026-09-10` (the script refuses to overwrite
   a baseline without `FORCE=1`, so a stray run after the reboot cannot destroy it) — then
   `ops/coconut/pre-reboot.sh` (SIGTERM with a 120 s grace per instance, stores last). `--dry-run` first.
2. Admin patches and reboots both hosts; applies items 1–2 above (and 4 on mango).
3. After boot, as `wilke`, **from a login shell** (`bash -l`; node/npx live under `$HOME`, so a bare
   `ssh coconut cmd` has no `npx` and the UIs fail): `ops/coconut/restore.sh` — it refuses to continue
   past preflight if `/rag` or `/scout` is not mounted or `vm.max_map_count` is wrong, and it does
   **not** start the labelers until `mango:8004` answers (`restore.sh --only labelers` later). Then
   `ops/coconut/verify.sh`, which diffs health, instances, store collection counts, GPU placement and
   mango against the baseline and exits non-zero on any regression.
4. Copies of all five scripts and the baseline are in `/rag/backups/reboot-2026-09-10/` in case
   `/home` (NFS, autofs) is slow to come back.

## Not restored (personal / interactive — owner's call)

`:3000` p3-web (BV-BRC-Web dev), `:5174` VaxpipeApp Vite, `codex` (interactive), `:8899` docs
server, `:5173`/`:5175` legacy UIs (`restore.sh --only legacy-ui` brings the last two back).
