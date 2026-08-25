# RAGStack — Project Memory

Persistent rules, conventions, and lessons-learned. Read before working in the repo. Update when a non-obvious gotcha bites you.

> This file is **project memory** (lives in the repo, persists across machines and contributors). It is distinct from any local AI-agent memory (e.g. `~/.claude/.../memory/`), which is per-machine and per-user.

## Conventions

- **Ports**: Python API = `8000`, Go API = `8080`. Don't swap them — conformance Make targets and `.env.example` hardcode this split.
- **Sidecar ports**: embedding = `50053`, crossencoder = `50052`, faiss = `50051`.
- **Worktrees and subagent isolation dirs**: place under `~/Development/worktrees/`. Not next to this repo, not in `/tmp`.
- **Container persistence model**: for every container we wrap (apptainer, docker, k8s), enumerate each writable directory the service needs and bind-mount it explicitly to `apptainer/data/<service>/<purpose>/` (or analogous host path). **Do not** use `--writable-tmpfs` or opaque overlays as a catch-all — state must survive restarts and be observable on the host.
- **Contracts are authoritative**: when Python and Go disagree on a field name or shape, the JSON schema / OpenAPI in `contracts/` wins; the diverging implementation is the bug.
- **sqlite floor is 3.35** (`UPDATE … RETURNING`): `CollectionStore.next_version` (#203) relies on it in the sqlite backend. Python 3.12's bundled sqlite on the dev env is 3.53; an older system sqlite would fail that statement at runtime, not at import.

## Hardware

- Dev host `coconut`: 8× NVIDIA H200 NVL (144 GB VRAM each). Prefer GPU-backed paths (vLLM, sentence-transformers `device=cuda`) when sizing services. CPU-only is a temporary fallback worth flagging.
- `vm.max_map_count` defaults to `65530` on this host; Elasticsearch refuses to start below `262144`. One-time fix: `sudo sysctl -w vm.max_map_count=262144`.
- No subuid/subgid mapping for the user account → `apptainer --fakeroot` doesn't work. Plan rootless flows around this.

## Failures encountered + resolutions

These bit us once. Don't repeat them.

### Ingestion / IDs

- **Re-ingest silently duplicated the corpus.** Qdrant point IDs are `uuid5(chunk_id)` (deterministic), but `loaders.py` assigned a random `uuid4` *doc* id and `chunkers.py` a random `uuid4` *chunk* id on every load — so each re-ingest produced new IDs → new points. Fix is **two-layer**: deterministic doc id (resolved path / content) *and* chunk id (`uuid5(f"{doc.id}:{start}:{end}")`). Fixing only the chunker is not enough while the doc id is random. Guard: a double-ingest test asserting the Qdrant point count is unchanged.
- **Qdrant collections are now scoped to `(model, dim)`** (`collection_name()` in `stores/qdrant.py`); `QDRANT_COLLECTION` is the base/prefix, not the literal name. `ensure_collection` hard-fails (`VectorDimMismatch`) if an existing collection's vector size ≠ the configured `embedding_model_dim` — switching embedding models to a different dim under the same base is safe (new collection), but expect old literal-named collections (e.g. `ragstack`) to be invisible to the API. The `scripts/` CLIs still take a literal `--collection`.
- **`require_durable_backends=true` is the production marker.** It makes a missing/unreachable Qdrant a fatal startup error (no silent in-memory degrade) and requires `API_KEYS` + `INGEST_ROOT` to be set (auth + LFI confinement). The text index only *warns* under it (no Elasticsearch backend yet). Don't set it in dev/tests.

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
- **Neo4j Community serves exactly one user database**, so per-collection isolation cannot be "one store instance per collection" the way Qdrant/ES do it — N `Neo4jGraphStore` objects all point at the same graph. The collection boundary is therefore stamped into the data: `(:Entity {name, tenant_id, collection})` and `[:REL {…, collection}]`, filtered on every read and delete (#209). Don't "simplify" this back to per-collection store objects: `InMemoryGraphStore` would then isolate by object identity, the unit suite would pass, and Neo4j would leak.
- **A triple with no `collection` stamp is invisible to any collection-scoped read** — deliberate fail-closed, same rule as an unstamped `tenant_id`. Pre-#209 graphs go dark for query/graph endpoints until re-ingested; the KG is small and derived, so re-ingest is the migration. `ensure_schema` drops the old `entity_name_tenant` constraint (it would *reject* one name existing in two collections) and creates `entity_name_tenant_collection`; both statements are idempotent.

### qdrant-client

- **`AsyncQdrantClient.search()` was removed in v1.10+** in favor of `query_points()`. New return shape: `response.points` (list), not a flat list. Param renamed: `query=` not `query_vector=`. The `QdrantVectorStore` adapter uses `query_points()`.
- **An empty match list matches *nothing*, in both backends.** Verified against the running Qdrant 1.18 (`{"match": {"any": []}}` → count 0 on a populated collection) and Elasticsearch 8.13 (`{"terms": {field: []}}` → count 0). This is what lets the filter builders fail *closed* on an empty scope list (#196) without a special match-nothing condition type — don't "optimise" the empty list away as "no constraint".
- **Point IDs must be UUID or int.** Arbitrary string chunk IDs need to be hashed deterministically — we use `uuid.uuid5(NAMESPACE_URL, chunk_id)` so re-ingest overwrites in place, with the original ID preserved in payload as `chunk_id`.

### Elasticsearch

- **The `elasticsearch` Python client's major version must match the server's major version.** A 9.x client stamps every request with `Accept: application/vnd.elasticsearch+json; compatible-with=9`; an ES 8.x server rejects that outright with HTTP 400 `media_type_header_exception`. There is no negotiation and no fallback — every call fails. Deployed servers are 8.x (`deploy/docker-compose.infra.yml` and the CI integration job both run `elasticsearch:8.13.4`; prod runs 8.13.4/8.19.3), so `python/pyproject.toml`'s `text` extra is bounded `elasticsearch[async]>=8.13,<9`. **Do not remove that upper bound.** It fails at *runtime on the first ES call*, not at install time, so an unbounded `>=8.13` breaks silently on any fresh install or resolver refresh — which is exactly how the CWL worker image picked up 9.4.1 and the load step died (#225). Raise it to `<10` only in the same change that moves the servers to ES 9.
- **The bound lives in `pyproject.toml` only.** `apptainer/ragstack-worker.def` and `apptainer/Dockerfile` install the package via its extras and deliberately do *not* repeat the constraint — a second copy would disagree with the API env on an ES 9 migration.

### Embedding sidecar

- **First request blocks ~90s** while sentence-transformers downloads BGE (~440 MB) and loads the model. Subsequent requests are fast. Bind the HF cache to a host dir (`apptainer/data/embedding/cache/`) so the download persists.
- **Dependency footprint**: torch + CUDA libs + sentence-transformers = ~5 GB on disk, even on CPU-only hosts. The deps live in `apptainer/data/embedding/deps/` and are installed once by `sidecars-up.sh`.

## Conda / shared envs

- **`conda activate` requires `conda.sh` sourced in the current shell.** Having `miniconda3/bin` on `PATH` makes `conda` runnable but `conda activate` is a *shell function* — it only exists after `source <miniconda>/etc/profile.d/conda.sh`. Non-interactive subshells (e.g. `bash -c '...'`, scripts, hooks) inherit `PATH` but not the function, so they hit `CommandNotFoundError`. Always source the conda hook before activating, even when `command -v conda` succeeds. `/rag/bin/activate` does this unconditionally.
- **Path-based envs (`conda create --prefix <path>`) are the right shape for shared/multi-user envs.** Activate by the same path (`conda activate /rag/envs/ragstack`); they don't appear in `conda env list` by name.
- **Editable installs (`pip install -e`) bind to a specific path.** If you move the source tree, re-run `pip install -e .[…]` from the new path. The console scripts still work but `python -c "import ragstack"` loads from wherever the editable-install pointer goes.

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
