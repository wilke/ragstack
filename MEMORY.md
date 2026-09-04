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
- **`QdrantClient(url=None)` does NOT fail — it resolves to host `localhost`, port 6333.** Verified directly: `url=None` and omitting `url` entirely both produce `host='localhost' port=6333`, which on this host is production. So setting a module-level `QDRANT_URL = None` is **not** self-guarding, and a "fix" that only removes a hardcoded URL can still write to production wherever a caller forgets to assign (#476). Guard explicitly at every client construction — refuse by name when the URL is unset — instead of trusting the client library to fail. Note the asymmetry: an f-string REST call with an unset URL *does* raise (`"None/collections/x"` is not an absolute URL), so only the constructor sites need the guard.
- **Point IDs must be UUID or int.** Arbitrary string chunk IDs need to be hashed deterministically — we use `uuid.uuid5(NAMESPACE_URL, chunk_id)` so re-ingest overwrites in place, with the original ID preserved in payload as `chunk_id`.

### Elasticsearch

- **The `elasticsearch` Python client's major version must match the server's major version.** A 9.x client stamps every request with `Accept: application/vnd.elasticsearch+json; compatible-with=9`; an ES 8.x server rejects that outright with HTTP 400 `media_type_header_exception`. There is no negotiation and no fallback — every call fails. Deployed servers are 8.x (`deploy/docker-compose.infra.yml` and the CI integration job both run `elasticsearch:8.13.4`; prod runs 8.13.4/8.19.3), so `python/pyproject.toml`'s `text` extra is bounded `elasticsearch[async]>=8.13,<9`. **Do not remove that upper bound.** It fails at *runtime on the first ES call*, not at install time, so an unbounded `>=8.13` breaks silently on any fresh install or resolver refresh — which is exactly how the CWL worker image picked up 9.4.1 and the load step died (#225). Raise it to `<10` only in the same change that moves the servers to ES 9.
- **The bound lives in `pyproject.toml` only.** `apptainer/ragstack-worker.def` and `apptainer/Dockerfile` install the package via its extras and deliberately do *not* repeat the constraint — a second copy would disagree with the API env on an ES 9 migration.

### BV-BRC Workspace

- **A usermeta field name may not contain a dot** — the service stores user metadata in Mongo, which forbids dots in field names, and the two write paths disagree about how loudly to say so. `Workspace.create` accepts a dotted key in the object tuple, creates the object and stores **nothing** for that key, with no error (#408); `Workspace.update_metadata` **rejects the whole call**: `The dotted field 'ragstack.spec_hash' in 'metadata.ragstack.spec_hash' is not valid for storage.` Together those cost a collection every upload after its first (#414): the create silently dropped the keys, so the next `ensure_collection_folder` took the backfill branch and raised out of the route as a 500. Folder metadata is therefore flat and underscore-separated (`ragstack_format`, `ragstack_collection_id`, `ragstack_tenant`, `ragstack_spec_hash`), with string values — GoWe's client types usermeta as `map[string]string` and drops anything else, so a nested JSON object would be invisible to it even if the Workspace kept it.
- **Never assume a Workspace metadata write landed.** `create` reports success either way, so `WorkspaceClient._stamp` reads the folder back and backfills through `update_metadata`; metadata that still does not persist is a WARNING, never an ingest failure. The test double in `python/tests/workspace_support.py` encodes both constraints — do not "fix" it into accepting dotted keys.

### Embedding sidecar

- **First request blocks ~90s** while sentence-transformers downloads BGE (~440 MB) and loads the model. Subsequent requests are fast. Bind the HF cache to a host dir (`apptainer/data/embedding/cache/`) so the download persists.
- **Dependency footprint**: torch + CUDA libs + sentence-transformers = ~5 GB on disk, even on CPU-only hosts. The deps live in `apptainer/data/embedding/deps/` and are installed once by `sidecars-up.sh`.

### Shell / deploy scripts

- **`bash -c "$(declare -f fn); fn ..."` carries the *function*, not the variables.** `declare -f` serialises only the function body; the child shell inherits only **exported** variables. `deploy/start-ragstack-workers.sh` dispatched vLLM this way with `EMBED_API_KEY`, `EMBED_MODEL`, `HF_HOME`, `GPU_MEM_UTIL`, `MAX_MODEL_LEN` and `VLLM_IMAGE` all unexported, so every replica launched with `--api-key ""`, `--model ""` and no image argument (#480). Reproduce with a two-line harness before believing a variable reaches a child. Corollary: a committed credential default can look live while being entirely inert.
- **Put `${VAR:?}` at the point of use, not in the top-level config block.** That block runs for *every* subcommand under `set -euo pipefail`, so a top-level `:?` breaks `stop`/`status`/`urls` as well as the launch path. And prefer `${VAR?msg}` over `${VAR:?msg}` when empty is a legitimate value — the coconut embedding fleet is keyless, so an empty key must stay expressible.

### Per-tenant state paths

- **The collection registry lives in a different filename per tenant.** `lucid` and `asm` use `state/collections.db`; `dev` and `demo` use `state/ragstack_collections.db`. Both filenames *exist* in some tenants, and querying the wrong one returns `Error: no such table: collections` — which reads like a failed health gate rather than a wrong query, and once nearly caused a rollback of two healthy deploys. Derive the expectation from the tenant's own registry immediately before a restart; never hardcode another tenant's value.
- **A tenant's boot log line can legitimately disagree with its registry.** `asm`'s last boot line said `4 collections` while its registry held 6 — two were created through the API since that boot, so the line changed 4 → 6 across a restart with nothing wrong. Capture the pre-stop registry state, not the previous boot line.

### Evaluation corpora

- **BEIR corpora cannot test chunk sizes above ~512 tokens.** Measured with the SFR tokenizer: `scifact` 5,183 docs, median **348** tokens, exactly one over 2,048; `BeIR/trec-covid` 171,332 docs, median 378, **max 925** — no document could ever split at 1,024. A size sweep there silently produces near-identical indexes at the large cells and reads as "size doesn't matter". Check the corpus length distribution *before* designing a size experiment, and note the target corpus (PMC OA full text, median ~10.1k tokens) is ~29× longer.
- **A proxy that predicts an experiment will fail is not a substitute for running it.** A BM25 lead-only-vs-full ablation on TREC CDS measured a null gap and implied chunking configs would not separate; the real run separated them by +0.137 nDCG@10 (CI [+0.051,+0.225]). The proxy tested the *coverage* axis while the decision was about the *granularity* axis, and it was BM25-only where the pipeline is dense + reranking — the reranker is the component that reads passages, and it reversed the finding. State which axis a proxy measures before letting it veto anything.

## Conda / shared envs

- **`conda activate` requires `conda.sh` sourced in the current shell.** Having `miniconda3/bin` on `PATH` makes `conda` runnable but `conda activate` is a *shell function* — it only exists after `source <miniconda>/etc/profile.d/conda.sh`. Non-interactive subshells (e.g. `bash -c '...'`, scripts, hooks) inherit `PATH` but not the function, so they hit `CommandNotFoundError`. Always source the conda hook before activating, even when `command -v conda` succeeds. `/rag/bin/activate` does this unconditionally.
- **Path-based envs (`conda create --prefix <path>`) are the right shape for shared/multi-user envs.** Activate by the same path (`conda activate /rag/envs/ragstack`); they don't appear in `conda env list` by name.
- **Editable installs (`pip install -e`) bind to a specific path.** If you move the source tree, re-run `pip install -e .[…]` from the new path. The console scripts still work but `python -c "import ragstack"` loads from wherever the editable-install pointer goes.
- **On this host that pointer is `/rag/repos/ragstack/python` — a legacy PRODUCTION checkout — in both `~/miniconda3/envs/ragstack` and `/rag/envs/ragstack` (#432).** The editable finder is *appended* to `sys.meta_path`, so it loses to any `sys.path` hit; running from `python/` wins by CWD. Under **pytest** you get a second, accidental layer — prepend importmode re-inserts the rootdir, so even a wrong-CWD pytest run resolves correctly. Do not rely on either: neither holds for a console script (`uvicorn`, which puts its own `bin/` on `sys.path[0]`), for `python scripts/...` from the wrong directory, or for a plugin that imports `ragstack` during startup. That first case is the one that actually bit. `make test-conformance-authz` had been contract-testing that checkout for its whole life, because its runner booted uvicorn before it `cd`'d. **Always pin `PYTHONPATH=<checkout>/python` when you invoke Python against a checkout.** `python/tests/conftest.py` now fails any pytest run whose `ragstack` resolves outside the rootdir, naming both paths; escape hatch `RAGSTACK_TEST_ALLOW_FOREIGN_IMPORT=1` warns instead of failing.
- **Do not remove the editable install from `/rag/envs/ragstack`.** The `:8010` (lucid) restart recipe in `docs/production-restore.md` runs the *console script* `/rag/envs/ragstack/bin/uvicorn` with no `PYTHONPATH`; a console script puts its own `bin/` dir on `sys.path[0]`, not the CWD, so that editable install is the only thing resolving `ragstack` for it, and the legacy trio being down makes that recipe the only way back up. Removing it from `~/miniconda3/envs/ragstack` (the dev env) is safe and desirable — it kills the default wrong-import path for agent sessions, and the reversal is one command: `pip install -e ".[all,dev]"` from `/rag/repos/ragstack/python`.

## Test opt-ins that must never default to production

Every one of these was a live-store default that fired unattended (#363, #369, #392, #407, #432). The pattern when you fix one: **rename the variable**, so a stale export from before the fix cannot re-arm the run, and make unset mean *skip*, never a fallback URL.

| variable | gates | renamed from |
|---|---|---|
| `RAGSTACK_TEST_ES_URL` | `python/tests/integration/test_elasticsearch.py` — creates and deletes indices on the **cluster** it names; the old default was the production ES holding the open-access index | `TEST_ES_URL` (#432) |
| `RAGSTACK_TEST_PG_DSN` | `pg_test_dsn` in `python/tests/conftest.py` | `TEST_PG_DSN` (#369) |
| `RAGSTACK_TEST_POSTGRES_DSN` | the Postgres store tests — a second, coexisting name; both skip on unset so both are safe, but they are worth unifying | — |

Tests that hand work to a child process build the child's environment from `python/tests/pinned_env_support.py`, which pins every store and model URL to `127.0.0.1:1`. A child otherwise inherits `ragstack.config`'s defaults, and on this host those name live services — a scratch run once reached the real cross-encoder sidecar on `:50052`. The conformance runner scripts pin the same set (plus `PYTHONPATH`) via `conformance/boot_env.sh` — enforced by `test_the_conformance_runners_pin_the_same_set_as_the_python_tests`, which *runs* the shell function and compares, because the two files drifted the first time a comment was all that held them together.

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
