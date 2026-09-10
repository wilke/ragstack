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
  `MAX_OPTIMIZATION_THREADS=1` (the #140 cap) plus a `/rag/cache/load3corpus` bind, `qdrant2` on
  6343/6344 has no script at all, and `neo4j-dev` was started by hand from a runbook. `restore.sh`
  reproduces the live parameters. The `demo` tenant's `up.sh` would start a `qdrant-demo` /
  `elasticsearch-demo` pair that is **not** in use (demo reads the production stores) — do not run it.
- **`/scout/wf/gowe/server.log` is 8 GB** with no rotation. Not a reboot problem; worth a logrotate.
- The GoWe pid files under `/scout/wf/gowe/pids/` are from June and stale; 19 of the 21 workers
  were started by hand and have no pid file. `pre-reboot.sh` resolves them from `/proc` with a cwd
  check instead.

## What the reboot loses even with a perfect restore

- The Qwen labeler will be mid-run (≈ 37 h of work left on 2026-09-09; Scout finished its 74,662
  records on 2026-09-09 09:00 UTC). Qwen is checkpointed per record; `restore.sh labelers` resumes
  it and skips Scout because its heartbeat says finished. Expect the in-flight requests to be redone.
- The docs server on `:8899` serves from `/tmp`, which is cleared. Rebuild if wanted.
- Anything under `/tmp` and `/dev/shm`. The study's working data is under `/rag/tmp` (a real disk).
- GPU placement: `restore.sh` pins SFR `:900N` to GPU `N-1` and the crossencoder to GPU 0, as
  today. GPUs 6 and 7 stay free by convention.

## Order of operations tomorrow

1. Before the window: `ops/coconut/snapshot.sh` (baseline), then `ops/coconut/pre-reboot.sh`
   (graceful stop, stores last so their WALs flush). `--dry-run` first.
2. Admin patches and reboots both hosts; applies items 1–2 above (and 4 on mango).
3. After boot, as `wilke`: `ops/coconut/restore.sh` — it refuses to continue past preflight if
   `/rag` or `/scout` is not mounted or `vm.max_map_count` is wrong. Then `ops/coconut/verify.sh`,
   which diffs health, instances, store collection counts, GPU placement and mango against the
   baseline and exits non-zero on any regression.
4. Copies of all five scripts and the baseline are in `/rag/backups/reboot-2026-09-10/` in case
   `/home` (NFS, autofs) is slow to come back.

## Not restored (personal / interactive — owner's call)

`:3000` p3-web (BV-BRC-Web dev), `:5174` VaxpipeApp Vite, `codex` (interactive), `:8899` docs
server, `:5173`/`:5175` legacy UIs (`restore.sh --only legacy-ui` brings the last two back).


---

# Generated from the live system — 2026-09-09T09:12:04Z by wilke on coconut

## System

| item | value |
|---|---|
| hostname | coconut |
| kernel | 6.8.0-111-generic |
| vm.max_map_count | 262144 |
| vm.overcommit_memory | 0 |
| max_map_count_persisted | False |
| fstab_rag_scout | ['/dev/mapper/vg1-scout /scout ext4 defaults 0 1', '/dev/mapper/vg1-rag /rag ext4 defaults 0 1'] |
| linger | Linger=no |
| coconut-proxy.service installed | False |
| uptime | 2026-05-19 14:32:23 |

## Listening services (one row per process; vLLM engine-core children omitted)

| ports | role | pid | user | since | cwd | command | GPU | restored by |
|---|---|---|---|---|---|---|---|---|
| 22 | system daemon: sshd | - |  |  | `` | `` |  | boot (systemd) |
| 25 | system daemon: postfix (localhost) | - |  |  | `` | `` |  | boot (systemd) |
| 53 | system daemon: systemd-resolved | - |  |  | `` | `` |  | boot (systemd) |
| 111 | system daemon: rpcbind | - |  |  | `` | `` |  | boot (systemd) |
| 3000 | BV-BRC web (personal dev) | 3069450 | wilke | Aug 25 15:35 | `/home/wilke/Development/BV-BRC-Web` | `node ./bin/p3-web` |  | not restored |
| 3001 | monitoring (apptainer docker://) | 1703491 | wilke | Sep  1 13:34 | `/scout/Experiments/GoWe` | `grafana server --homepath=/usr/share/grafana --config=/etc/grafana/grafana.ini --packaging=docker cfg:default.…` |  | restore.sh gowe (start-monitoring.sh) |
| 5173 | Vite dev server (legacy / personal) | 1868849 | wilke | Aug  1 17:57 | `/rag/repos/ragstack/frontend` | `node /rag/repos/ragstack/frontend/node_modules/.bin/vite --host 0.0.0.0` |  | not restored (legacy-ui or personal) |
| 5174 | Vite dev server (legacy / personal) | 4058837 | wilke | Sep  3 13:47 | `/home/wilke/Development/VaxpipeApp/ui` | `node /home/wilke/Development/VaxpipeApp/ui/node_modules/.bin/vite --host 0.0.0.0 --port 5173` |  | not restored (legacy-ui or personal) |
| 5175 | Vite dev server (legacy / personal) | 1833879 | wilke | Jul 23 23:04 | `/home/wilke/Development/ragstack/frontend` | `node /home/wilke/Development/ragstack/frontend/node_modules/.bin/vite --port 5175 --strictPort --host` |  | not restored (legacy-ui or personal) |
| 5210 | tenant UI (base-aware Vite) | 398734 | wilke | Aug 27 07:26 | `/rag/repos/tenants/demo/frontend` | `node ./node_modules/.bin/vite --host --port 5210 --strictPort --base /ragstack/demo/ui/` |  | restore.sh uis |
| 5211 | tenant UI (base-aware Vite) | 400259 | wilke | Aug 27 07:27 | `/rag/repos/tenants/lucid-next/frontend` | `node ./node_modules/.bin/vite --host --port 5211 --strictPort --base /ragstack/lucid-next/ui/` |  | restore.sh uis |
| 5212 | tenant UI (base-aware Vite) | 400587 | wilke | Aug 27 07:27 | `/rag/repos/tenants/asm-next/frontend` | `node ./node_modules/.bin/vite --host --port 5212 --strictPort --base /ragstack/asm-next/ui/` |  | restore.sh uis |
| 5432 | Postgres (apptainer instance) | 3306771 | wilke | Jun 23 15:13 | `/var/lib/postgresql/data/pgdata` | `postgres` |  | restore.sh stores |
| 5666 | system daemon: nagios nrpe | - |  |  | `` | `` |  | boot (systemd) |
| 6333,6334 | Qdrant (apptainer instance) | 2911044 | wilke | Jul  3 21:52 | `/qdrant` | `./qdrant` |  | restore.sh stores |
| 6343,6344 | Qdrant (apptainer instance) | 3718112 | wilke | Jul  4 16:16 | `/qdrant` | `./qdrant` |  | restore.sh stores |
| 6379 | Redis (apptainer instance) | 3307506 | wilke | Jun 23 15:13 | `/rag/repos/ragstack` | `redis-server *:6379` |  | restore.sh stores |
| 6556 | system daemon: check_mk agent | - |  |  | `` | `` |  | boot (systemd) |
| 6818 | system daemon: slurmd | - |  |  | `` | `` |  | boot (systemd) |
| 8081 | gateway nginx (svcbvbrc, apptainer nginx.sif) | - |  |  | `` | `` |  | admin: coconut-proxy.service, or restore.sh --proxy |
| 8090 | tenant UI (base-aware Vite) | 402139 | wilke | Aug 27 07:27 | `/rag/repos/tenants/dev/frontend` | `node ./node_modules/.bin/vite --host --port 8090 --strictPort --base /ragstack/dev/ui/` |  | restore.sh uis |
| 8091,9091 | GoWe server | 2088088 | wilke | Sep  4 03:58 | `/scout/Experiments/GoWe` | `./bin/gowe-server --addr :8091 --db /scout/wf/gowe/gowe.db --default-executor worker --scheduler-poll 1s --upl…` |  | restore.sh gowe |
| 8444 | gateway nginx (svcbvbrc, apptainer nginx.sif) | - |  |  | `` | `` |  | admin: coconut-proxy.service, or restore.sh --proxy |
| 8899 | docs http.server in /tmp (lost on reboot) | 2754210 | wilke | Aug 25 12:07 | `/tmp/docbuild/docroot/_build/html` | `/tmp/docsenv/bin/python -m http.server 8899 --bind 127.0.0.1` |  | not restored |
| 9000 | gateway nginx (svcbvbrc, apptainer nginx.sif) | - |  |  | `` | `` |  | admin: coconut-proxy.service, or restore.sh --proxy |
| 9001 | SFR embedding endpoint | 2751275 | wilke | Jul 10 13:24 | `/rag/repos/ragstack/python` | `/rag/envs/vllm/bin/python3.12 /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling …` | 0 | restore.sh sfr |
| 9002 | SFR embedding endpoint | 2751391 | wilke | Jul 10 13:24 | `/rag/repos/ragstack/python` | `/rag/envs/vllm/bin/python3.12 /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling …` | 1 | restore.sh sfr |
| 9003 | SFR embedding endpoint | 2751716 | wilke | Jul 10 13:24 | `/rag/repos/ragstack/python` | `/rag/envs/vllm/bin/python3.12 /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling …` | 2 | restore.sh sfr |
| 9004 | SFR embedding endpoint | 2752335 | wilke | Jul 10 13:24 | `/rag/repos/ragstack/python` | `/rag/envs/vllm/bin/python3.12 /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling …` | 3 | restore.sh sfr |
| 9005 | SFR embedding endpoint | 2753204 | wilke | Jul 10 13:24 | `/rag/repos/ragstack/python` | `/rag/envs/vllm/bin/python3.12 /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling …` | 4 | restore.sh sfr |
| 9006 | SFR embedding endpoint | 2753627 | wilke | Jul 10 13:25 | `/rag/repos/ragstack/python` | `/rag/envs/vllm/bin/python3.12 /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral --runner pooling …` | 5 | restore.sh sfr |
| 9090 | monitoring (apptainer docker://) | 1683930 | wilke | Sep  1 13:27 | `/scout/Experiments/GoWe` | `/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus --storage.tsdb.re…` |  | restore.sh gowe (start-monitoring.sh) |
| 9200,9300 | Elasticsearch (apptainer instance) | 2009014 | wilke | Jun 29 23:55 | `/usr/share/elasticsearch` | `/usr/share/elasticsearch/jdk/bin/java -Des.networkaddress.cache.ttl=60 -Des.networkaddress.cache.negative.ttl=…` |  | restore.sh stores |
| 9443 | gateway nginx (svcbvbrc, apptainer nginx.sif) | - |  |  | `` | `` |  | admin: coconut-proxy.service, or restore.sh --proxy |
| 24000 | tenant API lucid-next | 2510573 | wilke | Sep  2 19:39 | `/rag/repos/tenants/lucid-next/python` | `/rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 0.0.0.0 --port 24000` |  | restore.sh apis |
| 24003,24004 | Elasticsearch (apptainer instance) | 854964 | wilke | Aug  6 23:01 | `/usr/share/elasticsearch` | `/usr/share/elasticsearch/jdk/bin/java -Des.networkaddress.cache.ttl=60 -Des.networkaddress.cache.negative.ttl=…` |  | restore.sh stores |
| 24020 | tenant API asm-next | 2523938 | wilke | Sep  2 19:42 | `/rag/repos/tenants/asm-next/python` | `/rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 0.0.0.0 --port 24020` |  | restore.sh apis |
| 24040 | tenant API dev | 1938226 | wilke | Sep  2 14:41 | `/rag/repos/tenants/dev/python` | `/rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 0.0.0.0 --port 24040` |  | restore.sh apis |
| 24041,24042 | Qdrant (apptainer instance) | 445299 | wilke | Aug  6 19:34 | `/qdrant` | `./qdrant` |  | restore.sh stores |
| 24043,24044 | Elasticsearch (apptainer instance) | 3740804 | wilke | Aug  9 21:08 | `/usr/share/elasticsearch` | `/usr/share/elasticsearch/jdk/bin/java -Des.networkaddress.cache.ttl=60 -Des.networkaddress.cache.negative.ttl=…` |  | restore.sh stores |
| 24046,24047 | Neo4j (apptainer instance) | 3331342 | wilke | Aug 25 18:49 | `/home/wilke/Development/ragstack` | `/opt/java/openjdk/bin/java -cp /var/lib/neo4j/plugins/*:/var/lib/neo4j/conf/*:/var/lib/neo4j/lib/* -XX:+UseG1G…` |  | restore.sh stores |
| 24060 | tenant API demo | 2521169 | wilke | Sep  2 19:41 | `/rag/repos/tenants/demo/python` | `/rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 0.0.0.0 --port 24060` |  | restore.sh apis |
| 33359 | system daemon: unknown, root-owned | - |  |  | `` | `` |  | boot (systemd) |
| 41467 | system daemon: unknown, root-owned | - |  |  | `` | `` |  | boot (systemd) |
| 42787 | codex CLI (interactive) | 640969 | wilke | Aug 27 09:54 | `/home/wilke/Development/vaxpipe` | `codex` |  | not restored |
| 46923 | system daemon: unknown, root-owned | - |  |  | `` | `` |  | boot (systemd) |
| 50052 | sidecar (apptainer python.sif) | 3239026 | wilke | Jun 30 23:09 | `/app` | `/usr/local/bin/python -m uvicorn main:app --host 0.0.0.0 --port 50052` | 0 | restore.sh sidecars |
| 50053 | sidecar (apptainer python.sif) | 1807447 | wilke | Jul  5 23:46 | `/app` | `/usr/local/bin/python -m uvicorn main:app --host 0.0.0.0 --port 50053` |  | restore.sh sidecars |
| 50383 | system daemon: unknown, root-owned | - |  |  | `` | `` |  | boot (systemd) |

## Non-listening processes that must be restored

| pid | since | role | command |
|---|---|---|---|
| 2088175 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-1 --workdir /scout/wf/gowe/workdir/cpu-worker-1 --runtime apptainer --stage-out fil…` |
| 2088183 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-10 --workdir /scout/wf/gowe/workdir/cpu-worker-10 --runtime apptainer --stage-out f…` |
| 2088185 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-11 --workdir /scout/wf/gowe/workdir/cpu-worker-11 --runtime apptainer --stage-out f…` |
| 2088184 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-12 --workdir /scout/wf/gowe/workdir/cpu-worker-12 --runtime apptainer --stage-out f…` |
| 2088186 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-13 --workdir /scout/wf/gowe/workdir/cpu-worker-13 --runtime apptainer --stage-out f…` |
| 2088187 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-14 --workdir /scout/wf/gowe/workdir/cpu-worker-14 --runtime apptainer --stage-out f…` |
| 2088174 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-2 --workdir /scout/wf/gowe/workdir/cpu-worker-2 --runtime apptainer --stage-out fil…` |
| 2088176 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-3 --workdir /scout/wf/gowe/workdir/cpu-worker-3 --runtime apptainer --stage-out fil…` |
| 2088178 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-4 --workdir /scout/wf/gowe/workdir/cpu-worker-4 --runtime apptainer --stage-out fil…` |
| 2088181 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-5 --workdir /scout/wf/gowe/workdir/cpu-worker-5 --runtime apptainer --stage-out fil…` |
| 2088177 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-6 --workdir /scout/wf/gowe/workdir/cpu-worker-6 --runtime apptainer --stage-out fil…` |
| 2088179 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-7 --workdir /scout/wf/gowe/workdir/cpu-worker-7 --runtime apptainer --stage-out fil…` |
| 2088182 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-8 --workdir /scout/wf/gowe/workdir/cpu-worker-8 --runtime apptainer --stage-out fil…` |
| 2088180 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name cpu-worker-9 --workdir /scout/wf/gowe/workdir/cpu-worker-9 --runtime apptainer --stage-out fil…` |
| 2088190 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name ragstack-oa-1 --group ragstack --runtime apptainer --image-dir /scout/containers --extra-bind …` |
| 2088192 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name ragstack-oa-2 --group ragstack --runtime apptainer --image-dir /scout/containers --extra-bind …` |
| 2088193 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name ragstack-oa-3 --group ragstack --runtime apptainer --image-dir /scout/containers --extra-bind …` |
| 2088194 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name ragstack-oa-4 --group ragstack --runtime apptainer --image-dir /scout/containers --extra-bind …` |
| 2088188 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name worker-1 --workdir /scout/wf/gowe/workdir/worker-1 --gpu --gpu-id 1 --runtime apptainer --stag…` |
| 2088191 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --name worker-2 --workdir /scout/wf/gowe/workdir/worker-2 --gpu --gpu-id 2 --runtime apptainer --stag…` |
| 2088189 | Sep 4 03:58: | GoWe worker | `./bin/gowe-worker --server http://localhost:8091 --runtime none --name ragstack-cpu-1 --group ragstack-cpu --workdir /scout/wf/data/ragstack_gowe_smok…` |
| 1987098 | Sep 8 06:07: | labeler | `/rag/envs/ragstack/bin/python3 s0c_label.py --judge qwen --conc 5` |
| 1987088 | Sep 8 06:07: | labeler supervisor | `bash /home/wilke/Development/worktrees/confirmation-run/docs/plans/results/stage0/s0c_supervise.sh __supervise qwen` |

## Apptainer instances and their writable binds (from /proc/<pid>/mountinfo)

| instance | image | container path ← host path (relative to /rag) |
|---|---|---|
| crossencoder | python.sif | `/` ← `/`<br>`/.singularity.d/libs` ← `/libs`<br>`/.singularity.d/libs/libEGL_nvidia.so.0` ← `/usr/lib/x86_64-linux-gnu/libEGL_nvidia.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-ptxjitcompiler.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-ptxjitcompiler.so.580.95.05`<br>`/.singularity.d/libs/libGLESv2_nvidia.so.2` ← `/usr/lib/x86_64-linux-gnu/libGLESv2_nvidia.so.580.95.05`<br>`/.singularity.d/libs/libGLdispatch.so.0` ← `/usr/lib/x86_64-linux-gnu/libGLdispatch.so.0.0.0`<br>`/.singularity.d/libs/libOpenCL.so.1` ← `/usr/local/cuda-13.0/targets/x86_64-linux/lib/libOpenCL.so.1.0.0`<br>`/.singularity.d/libs/libnvidia-gtk3.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-gtk3.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-glvkspirv.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-glvkspirv.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-glcore.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-glcore.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-cfg.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-cfg.so.580.95.05`<br>`/.singularity.d/libs/libnvcuvid.so` ← `/usr/lib/x86_64-linux-gnu/libnvcuvid.so.580.95.05`<br>`/.singularity.d/libs/libOpenGL.so.0` ← `/usr/lib/x86_64-linux-gnu/libOpenGL.so.0.0.0`<br>`/.singularity.d/libs/libcuda.so` ← `/usr/lib/x86_64-linux-gnu/libcuda.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-egl-wayland.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-egl-wayland.so.1.1.20`<br>`/.singularity.d/libs/libnvidia-fbc.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-fbc.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-encode.so` ← `/usr/lib/x86_64-linux-gnu/libnvidia-encode.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-ml.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.580.95.05`<br>`/.singularity.d/libs/libGLESv1_CM_nvidia.so.1` ← `/usr/lib/x86_64-linux-gnu/libGLESv1_CM_nvidia.so.580.95.05`<br>`/.singularity.d/libs/libGLX.so.0` ← `/usr/lib/x86_64-linux-gnu/libGLX.so.0.0.0`<br>`/.singularity.d/libs/libnvidia-gtk2.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-gtk2.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-rtcore.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-rtcore.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-nvvm.so.4` ← `/usr/lib/x86_64-linux-gnu/libnvidia-nvvm.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-opencl.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-opencl.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-opticalflow.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-opticalflow.so.580.95.05`<br>`/.singularity.d/libs/libGL.so.1` ← `/usr/lib/x86_64-linux-gnu/libGL.so.1.7.0`<br>`/.singularity.d/libs/libGLX_nvidia.so.0` ← `/usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-glsi.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-glsi.so.580.95.05`<br>`/.singularity.d/libs/libcuda.so.1` ← `/usr/lib/x86_64-linux-gnu/libcuda.so.580.95.05`<br>`/.singularity.d/libs/libnvcuvid.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvcuvid.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-tls.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-tls.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-eglcore.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-eglcore.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-encode.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvidia-encode.so.580.95.05`<br>`/.singularity.d/libs/libnvidia-gpucomp.so.580.95.05` ← `/usr/lib/x86_64-linux-gnu/libnvidia-gpucomp.so.580.95.05`<br>`/.singularity.d/libs/libnvoptix.so.1` ← `/usr/lib/x86_64-linux-gnu/libnvoptix.so.580.95.05`<br>`/.singularity.d/libs/libcudadebugger.so.1` ← `/usr/lib/x86_64-linux-gnu/libcudadebugger.so.580.95.05`<br>`/.singularity.d/libs/libEGL.so.1` ← `/usr/lib/x86_64-linux-gnu/libEGL.so.1.1.0`<br>`/run/nvidia-persistenced/socket` ← `/nvidia-persistenced/socket`<br>`/usr/share/vulkan/implicit_layer.d/nvidia_layers.json` ← `/usr/share/vulkan/implicit_layer.d/nvidia_layers.json`<br>`/usr/share/vulkan/icd.d/nvidia_icd.json` ← `/usr/share/vulkan/icd.d/nvidia_icd.json`<br>`/app` ← `/repos/ragstack/sidecars/crossencoder`<br>`/deps` ← `/data/crossencoder/deps`<br>`/cache` ← `/data/crossencoder/cache`<br>`/rag/repos/ragstack` ← `/repos/ragstack` |
| elasticsearch | elasticsearch.sif | `/` ← `/`<br>`/usr/share/elasticsearch/data` ← `/data/elasticsearch/data`<br>`/usr/share/elasticsearch/logs` ← `/data/elasticsearch/logs`<br>`/usr/share/elasticsearch/config` ← `/data/elasticsearch/config`<br>`/rag/repos/ragstack` ← `/repos/ragstack` |
| elasticsearch-dev | elasticsearch.sif | `/` ← `/`<br>`/usr/share/elasticsearch/data` ← `/data/tenants/dev/elasticsearch/data`<br>`/usr/share/elasticsearch/logs` ← `/data/tenants/dev/elasticsearch/logs`<br>`/usr/share/elasticsearch/config` ← `/data/tenants/dev/elasticsearch/config`<br>`/rag` ← `/` |
| elasticsearch-lucid | elasticsearch.sif | `/` ← `/`<br>`/usr/share/elasticsearch/data` ← `/data/tenants/lucid/elasticsearch/data`<br>`/usr/share/elasticsearch/logs` ← `/data/tenants/lucid/elasticsearch/logs`<br>`/usr/share/elasticsearch/config` ← `/data/tenants/lucid/elasticsearch/config` |
| embedding | python.sif | `/` ← `/`<br>`/app` ← `/repos/ragstack/sidecars/embedding`<br>`/deps` ← `/data/embedding/deps`<br>`/cache` ← `/data/embedding/cache`<br>`/rag/repos/ragstack` ← `/repos/ragstack` |
| neo4j | neo4j.sif | `/` ← `/`<br>`/data` ← `/data/neo4j/data`<br>`/logs` ← `/data/neo4j/logs`<br>`/var/lib/neo4j/conf` ← `/data/neo4j/conf`<br>`/rag/repos/ragstack` ← `/repos/ragstack` |
| neo4j-dev | neo4j.sif | `/` ← `/`<br>`/data` ← `/data/tenants/dev/neo4j/data`<br>`/logs` ← `/data/tenants/dev/neo4j/logs`<br>`/var/lib/neo4j/conf` ← `/data/tenants/dev/neo4j/conf` |
| postgres | postgres.sif | `/` ← `/`<br>`/run/postgresql` ← `/data/postgres/run`<br>`/var/lib/postgresql/data` ← `/data/postgres/data`<br>`/rag/repos/ragstack` ← `/repos/ragstack` |
| qdrant | qdrant.sif | `/` ← `/`<br>`/qdrant/storage` ← `/data/qdrant/storage`<br>`/qdrant/snapshots` ← `/data/qdrant/snapshots`<br>`/rag/cache/load3corpus` ← `/cache/load3corpus` |
| qdrant-dev | qdrant.sif | `/` ← `/`<br>`/qdrant/storage` ← `/data/tenants/dev/qdrant/storage`<br>`/qdrant/snapshots` ← `/data/tenants/dev/qdrant/snapshots` |
| qdrant2 | qdrant.sif | `/` ← `/`<br>`/qdrant/storage` ← `/data/qdrant2/storage`<br>`/qdrant/snapshots` ← `/data/qdrant2/snapshots`<br>`/rag/repos/ragstack` ← `/repos/ragstack` |
| redis | redis.sif | `/` ← `/`<br>`/data` ← `/data/redis/data`<br>`/rag/repos/ragstack` ← `/repos/ragstack` |

## GPUs

| gpu | memory used / total | processes |
|---|---|---|
| 0 | 142463 MiB / 143771 MiB | 3239026 (12384 MiB), 2751648 (130060 MiB) |
| 1 | 130637 MiB / 143771 MiB | 2752110 (130626 MiB) |
| 2 | 130637 MiB / 143771 MiB | 2752613 (130626 MiB) |
| 3 | 130661 MiB / 143771 MiB | 2753548 (130650 MiB) |
| 4 | 130697 MiB / 143771 MiB | 2754254 (130686 MiB) |
| 5 | 130637 MiB / 143771 MiB | 2754889 (130626 MiB) |
| 6 | 0 MiB / 143771 MiB | — free |
| 7 | 0 MiB / 143771 MiB | — free |

## Stores

- **qdrant:6333** — 18 entries
  - `chunkcmp_fixed`: 31,041
  - `chunkcmp_semantic`: 6,622
  - `chunkcmp_sentence`: 32,732
  - `demo_g1_sfr_tok512`: 1,410
  - `oa_smoke_tok512`: 13,431
  - `persist_test`: 0
  - `rag_layout_test`: 5
  - `ragstack`: 0
  - `ragstack_demo`: 5
  - `ragstack_lib_catlle_50genes_salesforce_sfr_embedding_4096_fixed_512_64_915eb6bf`: 0
  - `ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc`: 47,625,155
  - `ragstack_lib_qsox1_salesforce_sfr_embedding_4096_fixed_512_64_8870376b`: 3,809
  - `ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe`: 877,343
  - `ragstack_sfr_semantic`: 2,982,219
  - `ragstack_sfr_tok256`: 24,830,600
  - `ragstack_sfr_tok512`: 12,587,981
  - `ragstack_test_sfr_8_fixed_token_256_32_d877910b`: 0
  - `ragstack_test_sfr_8_fixed_token_512_64_4037f431`: 0
- **qdrant:6343** — 3 entries
  - `lucid_sfr_tok256`: 1,554,790
  - `ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc`: 0
  - `ragstack_sfr_semantic`: 6,718,269
- **qdrant:24041** — 2 entries
  - `ragstack_lib_oa_dev_salesforce_sfr_embedding_4096_fixed_token_512_64_e788c5be`: 24,263
  - `ragstack_salesforce_sfr_embedding_mistral_4096_928f8ebe`: 0
- **elasticsearch:9200** — 14 entries
  - `chunkcmp_fixed`: 31,041
  - `chunkcmp_semantic`: 6,622
  - `chunkcmp_sentence`: 32,732
  - `demo_g1_sfr_tok512`: 1,216
  - `lucid_sfr_tok256`: 1,554,790
  - `oa_smoke_tok512`: 13,431
  - `persist_test`: 1
  - `ragstack_lib_catlle_50genes_salesforce_sfr_embedding_4096_fixed_512_64_915eb6bf`: 0
  - `ragstack_lib_open_access_salesforce_sfr_embedding_4096_fixed_token_512_64_cd24acfc`: 47,625,155
  - `ragstack_lib_qsox1_salesforce_sfr_embedding_4096_fixed_512_64_8870376b`: 3,809
  - `ragstack_sfr`: 877,343
  - `ragstack_sfr_semantic`: 2,982,219
  - `ragstack_sfr_tok256`: 24,830,600
  - `ragstack_sfr_tok512`: 12,587,981
- **elasticsearch:24003** — 1 entries
  - `lucid_sfr_tok256`: 1,554,790
- **elasticsearch:24043** — 2 entries
  - `ragstack`: 0
  - `ragstack_lib_oa_dev_salesforce_sfr_embedding_4096_fixed_token_512_64_e788c5be`: 24,263
- **neo4j-dev:24046**: UP
- **neo4j:7474**: DOWN

## Health at snapshot time (HTTP codes; 401 on a gateway path = alive)

| check | code |
|---|---|
| api:lucid-next:24000 | 200 |
| gateway:lucid-next | 401 |
| api:asm-next:24020 | 200 |
| gateway:asm-next | 401 |
| api:dev:24040 | 200 |
| gateway:dev | 401 |
| api:demo:24060 | 200 |
| gateway:demo | 401 |
| gateway:asm (legacy) | 502 |
| gateway:lucid (legacy) | 502 |
| sfr:9001 | 200 |
| sfr:9002 | 200 |
| sfr:9003 | 200 |
| sfr:9004 | 200 |
| sfr:9005 | 200 |
| sfr:9006 | 200 |
| crossencoder:50052 | 200 |
| embedding:50053 | 200 |
| gowe:8091 | 200 |
| prometheus:9090 | 200 |
| grafana:3001 | 200 |
| ui:demo:5210 | 200 |
| ui:lucid-next:5211 | 200 |
| ui:asm-next:5212 | 200 |
| ui:dev:8090 | 200 |
| ui:asm-legacy:5173 | 200 |
| ui:lucid-legacy:5175 | 200 |

## mango (remote host — restored by its admin, verified from here)

| endpoint | served model | max_model_len | vLLM cache config |
|---|---|---|---|
| mango:8000 | ['Qwen/Qwen3.6-27B'] | [262144] | {'block_size': '784', 'cache_dtype': 'auto', 'gpu_memory_utilization': '0.9', 'num_gpu_blocks': '595'} |
| mango:8003 | ['RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic'] | [60000] | {'block_size': '16', 'cache_dtype': 'auto', 'gpu_memory_utilization': '0.9', 'num_gpu_blocks': '53803'} |
| mango:8004 | ['Qwen/Qwen3.6-35B-A3B'] | [131072] | {'block_size': '2096', 'cache_dtype': 'fp8', 'gpu_memory_utilization': '0.95', 'num_gpu_blocks': '5333'} |

## Code and config identity

| path | git HEAD |
|---|---|
| `/rag/repos/ragstack` | 6d6fcf6 |
| `/rag/repos/GoWe` | 92706d7 |
| `/home/wilke/Development/ragstack` | 66ed03f |
| `/home/wilke/Development/worktrees/confirmation-run` | e9a0f5c |
| `/rag/repos/tenants/asm-next` | 652be18 |
| `/rag/repos/tenants/demo` | 652be18 |
| `/rag/repos/tenants/dev` | 652be18 |
| `/rag/repos/tenants/lucid-next` | 652be18 |

| config file | sha256[:16] |
|---|---|
| `/rag/data/tenants/asm/config/tenant.env` | 92db0c86525ddf0a |
| `/rag/data/tenants/demo/config/tenant.env` | 4f59d5f3a214f7b8 |
| `/rag/data/tenants/dev/config/tenant.env` | 4344a79d29729e23 |
| `/rag/data/tenants/lucid/config/tenant.env` | 88c02955a454f717 |
| `/rag/config/proxy/nginx.conf` | bf28c7d247ddb7d1 |
| `/rag/config/proxy/conf.d/00-maps.conf` | 776ad651f3f1dea0 |
| `/rag/config/proxy/conf.d/10-gateway.conf` | 90e491fbefd2a848 |
| `/rag/config/proxy/conf.d/20-gowe.conf` | 6fdec2627ae76353 |
| `/rag/config/unified.models.json` | b5dab25bdbfddc70 |
| `/scout/wf/gowe/worker-env.env` | 56acc79c735a3813 |
| `/scout/wf/gowe/ragstack-worker-env.env` | 6eb87e60ddff6be9 |

| image | bytes |
|---|---|
| elasticsearch.sif | 562,479,104 |
| neo4j.sif | 353,415,168 |
| nginx.sif | 25,284,608 |
| postgres.sif | 155,389,952 |
| python.sif | 42,364,928 |
| qdrant.sif | 70,643,712 |
| redis.sif | 15,892,480 |

## Labelers (quarantined confirmation run)

- heartbeat-scout.json: `{"judge": "scout", "attempt": 1, "records": 74760, "utc": "2026-09-09T08:58:54Z", "state": "finished"}`
- heartbeat-qwen.json: `{"judge": "qwen", "attempt": 1, "pid": 1987098, "records": 15684, "utc": "2026-09-09T09:07:46Z", "state": "running"}`
