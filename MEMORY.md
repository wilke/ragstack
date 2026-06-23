# RAGStack — Project Memory

Persistent rules, conventions, and lessons-learned. Read before working in the repo. Update when a non-obvious gotcha bites you.

> This file is **project memory** (lives in the repo, persists across machines and contributors). It is distinct from any local AI-agent memory (e.g. `~/.claude/.../memory/`), which is per-machine and per-user.

## Conventions

- **Ports**: Python API = `8000`, Go API = `8080`. Don't swap them — conformance Make targets and `.env.example` hardcode this split.
- **Sidecar ports**: embedding = `50053`, crossencoder = `50052`, faiss = `50051`.
- **Worktrees and subagent isolation dirs**: place under `~/Development/worktrees/`. Not next to this repo, not in `/tmp`.
- **Container persistence model**: for every container we wrap (apptainer, docker, k8s), enumerate each writable directory the service needs and bind-mount it explicitly to `apptainer/data/<service>/<purpose>/` (or analogous host path). **Do not** use `--writable-tmpfs` or opaque overlays as a catch-all — state must survive restarts and be observable on the host.
- **Contracts are authoritative**: when Python and Go disagree on a field name or shape, the JSON schema / OpenAPI in `contracts/` wins; the diverging implementation is the bug.

## Hardware

- Dev host `coconut`: 8× NVIDIA H200 NVL (144 GB VRAM each). Prefer GPU-backed paths (vLLM, sentence-transformers `device=cuda`) when sizing services. CPU-only is a temporary fallback worth flagging.
- `vm.max_map_count` defaults to `65530` on this host; Elasticsearch refuses to start below `262144`. One-time fix: `sudo sysctl -w vm.max_map_count=262144`.
- No subuid/subgid mapping for the user account → `apptainer --fakeroot` doesn't work. Plan rootless flows around this.

## Failures encountered + resolutions

These bit us once. Don't repeat them.

### Apptainer

- **No `--cwd` / `--pwd` flag.** When a container CMD uses CWD-relative paths (qdrant's `./entrypoint.sh` which internally calls `./qdrant`), wrap the CMD via the runscript override: `sh -c 'cd /qdrant && exec ./entrypoint.sh'`.
- **`--env` shell-sources values.** Env keys with dots (Elasticsearch `discovery.type`, `xpack.security.enabled`) can't be passed via `--env` *or* `--env-file` (both are shell-eval'd; dot is an invalid var-name char). Use the service's native CLI: `-Ediscovery.type=single-node` etc.
- **tini wrapper consumes `-E` flags.** When skipping the relative-path entrypoint to add `-E` args to ES, also skip the `/bin/tini` wrapper — tini greedy-parses `-E` as its own option. Tini isn't PID 1 inside apptainer anyway, so dropping it only costs a warning.
- **Image FS is read-only by default.** Every path the service writes to must be a bind mount (or pay the persistence cost of `--writable-tmpfs`, which we don't want). Includes non-obvious paths: `/var/run/postgresql` (postgres socket), `/var/lib/neo4j/conf` (neo4j writes `neo4j.conf` at startup), `/usr/share/elasticsearch/config` (ES auto-keystore + autoconfig certs), `/qdrant/snapshots` (snapshots/tmp lock), `/logs` (neo4j asserts writable).
- **Some bind targets need image content to start.** ES `config/` and Neo4j `conf/` are populated by the image build. Bind-mounting an empty host dir to them breaks the service. Solution: `seed_if_empty()` step copies the image's contents into the host dir on first run (`apptainer/up.sh`).
- **`pip install --target` console scripts have shebang issues.** When the deps dir is bind-mounted at a different path than the install path (we install at host `apptainer/data/embedding/deps/` and mount at `/deps`), the `uvicorn` console script's shebang breaks. Use `python -m uvicorn` instead.
- **Instance logs are append-only.** `~/.apptainer/instances/logs/<host>/<user>/<svc>.{err,out}` keep growing across restarts. Historical errors from earlier failed runs persist after fix — don't trust them as current state; check `ss -ltn` and `curl` the service instead.

### Neo4j

- **Neo4j 5 rejects the literal string `neo4j` as a password.** `apptainer/up.sh` defaults to `NEO4J_PASSWORD=ragstack`. `.env.example` still has the broken default; docker-compose path will hit this until fixed.

### qdrant-client

- **`AsyncQdrantClient.search()` was removed in v1.10+** in favor of `query_points()`. New return shape: `response.points` (list), not a flat list. Param renamed: `query=` not `query_vector=`. The `QdrantVectorStore` adapter uses `query_points()`.
- **Point IDs must be UUID or int.** Arbitrary string chunk IDs need to be hashed deterministically — we use `uuid.uuid5(NAMESPACE_URL, chunk_id)` so re-ingest overwrites in place, with the original ID preserved in payload as `chunk_id`.

### Embedding sidecar

- **First request blocks ~90s** while sentence-transformers downloads BGE (~440 MB) and loads the model. Subsequent requests are fast. Bind the HF cache to a host dir (`apptainer/data/embedding/cache/`) so the download persists.
- **Dependency footprint**: torch + CUDA libs + sentence-transformers = ~5 GB on disk, even on CPU-only hosts. The deps live in `apptainer/data/embedding/deps/` and are installed once by `sidecars-up.sh`.

## Git / GitHub

- Tag releases on `main` as `v<major>.<minor>.<patch>`. Push commits and tags separately:
  ```bash
  git push origin main && git push origin v<x.y.z>
  ```
- HTTPS push works once `gh auth setup-git` is run (uses the gh CLI's token as a credential helper). SSH push requires `ssh-add ~/.ssh/<key>` after agent restarts. Prefer HTTPS for headless / fresh sessions.
- Don't commit `dump.rdb` (Redis dumps a working file when run without `/data` bound; happens during early apptainer iteration). `*.rdb` is gitignored.

## When to update this file

Add an entry when:
- You spent more than ~15 minutes debugging a problem whose cause was non-obvious from the code or docs.
- A version upgrade silently broke an API and you had to find the migration.
- A host-specific assumption (kernel param, sudo requirement, hardware quirk) blocked progress.

Don't add entries for things obvious from the code or already in [CLAUDE.md](CLAUDE.md) / [SPEC.md](SPEC.md).
