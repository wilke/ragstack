# Spec — user-owned libraries (RAGStack ⇄ BV-BRC)

Rev 3. MUST/MUST NOT are normative. Target: implementable without inventing decisions.

---

## §-1. Blocking gates

**G1 — retrieval quality at library scale (#200).** All quality numbers here were measured at ≥5.8k chunks with `--retrieve-pool 300 --rerank-pool 100`; shipping defaults (`rrf_k=60`, `candidate_multiplier=2`, `top_k=5`, `rerank_enabled=False`) were never under measurement. Score a ~200-doc library through the eval seam (#125/PR #126). If constants need a small-N branch, that branch is normative and part of v1. **One day. Can invalidate the design.**

**G2 — re-measure the Qdrant filter in the v1 shape (#199).** #199 measured a single key/value at 1% selectivity on synthetic 128-d vectors. v1 issues `library_id == X AND tenant_id ANY […]` at ~0.005%. Sweep 10⁻²→10⁻⁵ on real 4096-d SFR vectors. **Pass: returned-hits == `min(k, |library|)`.**

**G3 — §11 Q1** (can BV-BRC compute reach coconut?). A "no" rewrites §6.

**G4 — §11 Q6** (is BV-BRC's token signing key published and offline-verifiable?). A "no" collapses §5's cache design. Gates §5.0.

---

## §0. Naming

| Term | Meaning |
|---|---|
| **index** | one physical Qdrant collection + matching ES index |
| **collection** | **SHIPPED, UNCHANGED.** Registry entry binding (model + dim + chunker) → index. |
| **library** | **NEW.** Access-controlled document set. Two kinds, §4. |

**Creating a library MUST NOT create a Qdrant collection or ES index.** Supersedes #201's "one user = one tenant"; §2's ACL constraint makes the shareable unit a directory, which one tenant cannot represent. Update #201.

---

## §1. Interfaces

```python
# security.py — EXTENDED. Required by §5.1 cache key, §5.0 expiry, §6 submit check, §13 logging.
@dataclass(frozen=True)
class Principal:
    tenant: str
    role: str
    token: str | None = None       # never logged; __repr__ MUST redact
    token_id: str | None = None    # set ONLY after signature verification
    token_exp: int | None = None

@dataclass(frozen=True)
class BlobRef:
    root: str          # library root key
    rel: str = ""      # "" = the root itself

@dataclass(frozen=True)
class BlobMeta:
    id: str            # MUST be stable across rename AND move.
                       #   Workspace: ObjectID  (verify — §11 Q7)
                       #   LocalFs:   f"local:{st_dev}:{st_ino}"   NOT sha256(realpath):
                       #              a path-derived id breaks §3 and re-mints doc_id on rename,
                       #              and LocalFs is the ONLY backend §14 tests.
                       #   S3:        key + versionId
    ref: BlobRef
    size: int
    ctime: str         # NOT an mtime (Workspace creation_time is client-settable).
                       # MUST NOT be used alone as a change signal.
    owner: str
    etag: str | None

class BlobStore(Protocol):
    """Raises BlobDenied | BlobNotFound | BlobUnavailable | BlobTooLarge.
    MUST NOT return a bare bool for an authorization-relevant outcome."""
    def for_principal(self, p: Principal) -> BlobStore: ...
        # LocalFs: returns an instance asserting ref.root is under the principal's
        # permitted prefix. An UNBOUND LocalFs MUST raise on every operation, so the
        # "can't use a service credential on a user path" guarantee is testable.
    async def stat(self, ref: BlobRef) -> BlobMeta: ...
    async def list(self, ref, *, recursive=False, limit=1000, cursor=None
                   ) -> tuple[list[BlobMeta], str | None]: ...
    def open(self, ref: BlobRef, *, max_bytes: int) -> AsyncIterator[bytes]: ...  # async generator
    async def put(self, ref, data: AsyncIterator[bytes], *, overwrite=True) -> BlobMeta: ...
    async def delete(self, ref, *, recursive=False) -> None: ...   # idempotent

class AuthzDecision(StrEnum):
    ALLOW = "allow"; DENY = "deny"; UNAVAILABLE = "unavailable"

class AuthorizationProvider(Protocol):
    async def access(self, p: Principal, root: str) -> AuthzDecision: ...
    async def owner_of(self, root: str) -> str: ...
    async def list_readable(self, p: Principal, roots: list[str]) -> dict[str, AuthzDecision]: ...
```

`UNAVAILABLE` ≠ `DENY`: both refuse, but they map to different HTTP statuses and different alerts. A `-> bool` interface cannot express that and MUST NOT be used.

**Provider selection is PER LIBRARY** (`libraries.authz_backend`), not global. At startup every non-deleted library's `authz_backend` MUST resolve to a configured provider or that library is **unmounted and logged** — the process still starts. (Rev-2 had a global setting; it was unsatisfiable, since prod needs `bvbrc` for user libraries and `local` for the ASM/lucid whole-index rows simultaneously.)

**Write / index / delete are owner-only**, decided by `owner_of(root) == principal.tenant`, never by `access`.

---

## §2. Workspace ACL constraint

`Workspace.spec`: *"only top-level directories can have permissions altered."*

**A scoped library IS a top-level Workspace directory.** Two libraries under one top-level directory share permissions permanently.

```
/{user}@bvbrc/{library-name}/       <- top-level. shareable unit. THE probe target (§5.1).
    smith2019.pdf                   <- never moved, never copied
    .ragstack/                      <- ONLY regenerable artifacts; user may delete at will
        library.json                <- published summary. NOT the probe target. NOT trusted.
        text/{doc_id}.txt           <- start_char/end_char index into THIS
        index/{doc_id}.json         <- chunk index: ids + spans only
        runs/{run_id}.json
```

---

## §3. Identity keys

- `library_id` — server-minted, opaque, **not a secret**. `^lib_[0-9a-f]{12}$`, carried as a schema `pattern`; a non-matching path param is 400 before any store lookup.
- `doc_id = uuid5(NAMESPACE_URL, blob_meta.id)`. **MUST NOT** derive from a filesystem path.
  **Target the code §6 actually runs:** `scripts/ingest_jsonl.py` — `_doc_id_key` (`:86-88`) and the three `deterministic_doc_id` call sites (`:975`, `:990`, `:998`). It **never imports `JsonlLoader`**, so patching `loaders.py:185` would ship a green diff while the real path keeps minting `uuid5(resolve(path))` against the worker CWD. Also `loaders.py:58`, `:116`, `:185` for the API path.
- `chunk_id` — unchanged.

### Point ids — CONDITIONAL

`_point_id` (`qdrant.py:405-408`) is global and used at **read** time (`get_chunks`, `qdrant.py:318`), upsert (`:210`) and `delete_except` (`:379`). An unconditional change makes 24.8M public points unaddressable and undeletable.

```python
def _point_id(chunk_id, tenant_id=DEFAULT_TENANT, library_id=None):
    key = f"{library_id}:{tenant_id}:{chunk_id}" if library_id else f"{tenant_id}:{chunk_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

Byte-identical to today when `library_id` is absent; pinned by a unit test against a real prod `(tenant, chunk_id)`. Same for `_es_id` (`elasticsearch.py:47-48`).

**`stores/memory.py` has no derived id** — `upsert` (`:51-53`) and `index` (`:129-134`) key on the tuple `(tenant_of(c), c.id)`. The rule there is **widen the identity tuple** to `(tenant, library_id, chunk_id)`, not pin a UUID. §14 must test it that way.

**`delete_except` keep-set bug — fix in the same commit.** `qdrant.py:379`: `keep = {_point_id(cid, tenant_id or DEFAULT_TENANT) …}`. With `tenant_id=None` the keep-set is computed under `default` while the scroll returns the real tenant ⇒ **every point classifies as stale and the whole document is deleted.** Both §8's reconciliation and §9's purge call this. Require an explicit non-None tenant; raise otherwise.

**Atomic commit:** `stores/qdrant.py`, `stores/elasticsearch.py`, `stores/memory.py`, `ingestion/loaders.py`, `ingestion/pipeline.py`, `scripts/ingest_jsonl.py`, **`scripts/load_embeddings.py`** (the tool the CWL load stage runs; needs `--library-id` too). *(Rev-2 also listed `segmentation_cache.py`, `embed_shard.py`, `ingest_shard.py` — they derive no ids; removed.)*

### Deletes MUST carry `library_id`

`VectorStore.delete`/`delete_except` (`protocols.py:44,46`), `TextIndex.delete`/`delete_except` (`:82,84`), `GraphStore.delete_by_doc` (**`:135`**). Impls: `qdrant.py:356,370`, `elasticsearch.py:251,262`, `memory.py:75,82`, `neo4j.py:191`. In **both** the keep-set derivation and the scroll/query filter.

**`ingestion/pipeline.py:242-243`** (`_delete_prior`) also passes only `tenant_id` — it is the `/v1/ingest` path and recreates the "re-indexing B destroys A" bug. Add `library_id`.

**`DELETE /v1/documents/{doc_id}`** stays doc_id+tenant and therefore destroys every library's copy. It is **404 for library principals** (§9) and **admin-only**, with a documented warning until it takes a library scope.

**Scope keys are server-written, never caller-carried.** Qdrant spreads `c.metadata` into the payload minus `_PAYLOAD_RESERVED` (`qdrant.py:46,196-204`). A JSONL record carrying `metadata.library_id` would today write into another library.

**Do NOT simply add them to `_PAYLOAD_RESERVED`.** `tenant_id` reaches the payload *only* through that metadata spread, and `_chunk_from_payload` (`:411-422`) pops exactly the reserved keys and returns the rest as metadata — so reserving `tenant_id` stops writing it and `_build_filter({"tenant_id": […]})` matches nothing on every new point; reserving `library_id` does the same to §4's entire filter. That failure is **silent**: ingest reports success and every query returns zero rows, and §-1's G2 has already pre-installed the wrong explanation for that exact symptom.

Correct rule, both stores:
1. **Write `tenant_id` and `library_id` into the payload explicitly from server-derived values**, the way `chunk_id`/`doc_id` already are — not via the metadata spread.
2. **Drop any caller-supplied copy** of either key before the spread.
3. `_chunk_from_payload` continues to surface them as metadata, so reads are unchanged.
4. ES has no `_PAYLOAD_RESERVED` and stores `metadata.tenant_id`; apply the same drop-then-write-explicitly rule there so the two stores do not diverge.

Assert in §14: ingest a record whose `metadata.library_id` names another library, then confirm the chunk is queryable in its true library and absent from the named one.

---

## §4. Two kinds of library

| kind | Backing | Query filter | Purpose |
|---|---|---|---|
| `scoped` | shares `ragstack_lib_v1` | `library_id == L AND tenant_id ∈ (readable ∪ {owner_of(L)})` | BV-BRC user libraries |
| `whole_index` | **IS** an entire existing collection | **collection selection only — no `library_id`, no tenant widening** | ASM, lucid, the public corpus (§16) |

**`whole_index` — normative MUST NOTs** (each of these was a live hole in rev 2):
- The tenant widening MUST NOT apply. `owner` on a whole_index row is a **display marker only and MUST NOT enter any filter** — it is an operator-typed string, and `default` is the tenant of every unmapped key.
- A collection hosting any `scoped` library MUST NOT be registrable as `whole_index` — otherwise one row over `ragstack_lib_v1` grants every grantee every user's chunks. **Enforced by a Python guard in the admin handler, NOT by a SQL constraint:** no uniqueness rule can express this, since every scoped library shares `LIBRARY_COLLECTION_ID`, so a unique index on `collection_id` would forbid scoped libraries outright. The partial index in §8.1 only prevents two whole_index rows on one collection. The guard rejects (400) when `collection_id == LIBRARY_COLLECTION_ID` or when any non-deleted `scoped` row references it.
- `root` is a **synthetic non-null ACL key**: `local:collection/{collection_id}`. There is no NULL root; §5.1 step 3 always has an argument.
- Ingress is **admin-only** `POST /v1/admin/libraries {collection_id, name, authz_backend}` → 201. Not `POST /v1/libraries`.
- `owner_of(root)` returns the row's `owner`. `count_library` falls back to `count_tenants`.

**Shared-read tenant rule (scoped only).** Chunks carry the *ingesting* tenant (`pipeline.py:146-147`); a reader's `readable_tenants` is `[reader, public]`, so a naive filter returns **zero rows for every shared library**. `owner_of(L)` is resolved server-side from the libraries table, never from the request. The widened set is carried in a distinct `LibraryScope` type — **not a bare dict** — so it cannot structurally flow into `/v1/documents`, `/v1/stats/*` or graph.

**Query-path branch point.** New `library_scope_filters(filters, principal, lib) -> LibraryScope` in `tenancy.py`, replacing `scope_filters` at `query.py:353` and the `/v1/query` equivalent. It branches on `lib.kind` and pins scope keys **last**, exactly as `scope_filters` does (`tenancy.py:51-54`). A library query resolves its collection from `libraries.collection_id` and **MUST bypass `_effective_collection` / `allowed_collection_ids`** (`query.py:266-292`) — the library row is the authority.

**`filters` MUST reject `library_id` and `tenant_id`** → 400. `additionalProperties:false` does not catch it (`filters` is free-form).

**Payload index is PER COLLECTION.** `_ensure_tenant_index` (`qdrant.py:176-188`) runs from `ensure_collection`, which `deps.py:298` calls for **every registry entry at every startup** and swallows errors. Generalizing it globally would fire a `create_payload_index("library_id")` against 24.8M / 12.6M / 3.0M / lucid at every restart — exactly the §16 Tier-2 operation that requires a maintenance window. **Only `ragstack_lib_v1` gets `library_id` indexed.**

**Index name is HAND-PINNED: `ragstack_lib_v1`.** Its build spec is identical to prod `ragstack_sfr_tok512`, so content-addressing would **collide**. Created with an explicit `max_segment_size` (G2's cliff is per-segment and moves as the optimizer merges), on its own Qdrant instance via `QDRANT_COLLECTION_ROUTES`. ~82 VMAs/library, ~800 per process; at 70% registration returns **503** and an operator provisions the next index (there is no automatic placement in v1).

**A scoped-library query returns that library ONLY.** The 40.4M public chunks are in other indexes and unreachable. **If "my papers + BV-BRC literature" is the requirement — and #201 reads that way — it is two searches fused client-side** (comparable because the build specs are identical). **Decide before the index exists**; it changes the response shape and citation model.

---

## §5. AuthN / AuthZ

### §5.0 Token verification — new subsystem, gated on G4

Nothing in the repo verifies a BV-BRC token; `gowe_client.py:83-84` only forwards one, and `openapi.yaml:674` declares one scheme.

- Scheme `BvbrcToken`: **`type: apiKey, in: header, name: Authorization`** — *not* `http`/`bearer`; the wire format has no `Bearer ` prefix.
- Format `un=…|tokenid=…|expiry=…|sig=…`. **MUST verify `sig` offline** against the published key. **MUST NOT parse `un=` from an unverified token.** `token_id` is set **only after** verification, else the cache key is attacker-chosen and a forged token bearing a victim's `tokenid` poisons the victim's entry.
- Key from `BVBRC_TOKEN_PUBKEY_URL`, cached, refetched on unknown key id; ≤300 s skew. `expiry` checked **every request, uncached**.
- Yields `Principal(tenant=f"bvbrc:{un}", role=ROLE_RESEARCHER, token=…, token_id=…, token_exp=…)`. **The role is explicit and MUST NOT fall through to `default_role`** — prod runs `DEFAULT_ROLE=admin` (`/rag/config/unified.env:19`), which would make every researcher a superuser.
- Both `X-API-Key` and `Authorization` present → **400**. Enforced in a dedicated dependency; `APIKeyHeader(auto_error=False)` cannot do it alone.
- Failures: malformed / bad sig / expired → **401**. Key server unreachable → **503**, never 401, never allow.
- **`TenantQuota._sems` MUST gain LRU eviction** before tenant derives from a username (`quota.py:22-24` says so in-code).

### §5.1 Per-query authorization

```
1. verify token (§5.0)                              every request, uncached
2. library_id -> libraries row                      RAGStack table
3. authz.access(principal, row.root)                memoized
4. library_scope_filters(...) per §4
```

**Probe the ROOT**, never `.ragstack/library.json` — §2 invites the user to delete that folder, and probing it would turn a cache clear into a permanent lockout.

| Probe outcome | Decision | Cached |
|---|---|---|
| 200 | ALLOW | yes, TTL `min(300s, token_exp − now)` |
| 401 / 403 | DENY | yes, TTL 60 s |
| 404 | **not visible** — read-only | yes, TTL 60 s |
| 429 / 5xx / timeout | UNAVAILABLE | **no** |

**A non-owner probe MUST NOT mutate library state.** Rev 2 mapped 404 → "mark `orphaned`", but the probe runs with the *caller's* token and Workspaces return 404 for objects you cannot see. Any holder of a `library_id` (explicitly "not a secret") could mark **someone else's** library orphaned and feed it to the deletion sweeper. `orphaned` is set **only** by a reconciliation run or an owner-credentialed probe.

**HTTP mapping lives in §9, not here.** This section returns an `AuthzDecision`.

**Cache.** Memoizes **step 3 only**. Key `(token_id, library_id)`. Bounded LRU 10 000, **per process** (N workers ⇒ N caches, N× probe volume). **Single-flight per key.** Revocation lag == TTL, documented. On `UNAVAILABLE` the request is refused — the house default of degrading to empty is the #196 fail-open class.

---

## §6. Ingest — gated on G3

CWL `CommandLineTool` per stage, following `cwl/embed-bulk.cwl`. Stages exist to be **gated**; that is why this is a workflow.

| # | Stage | Artifact out | Gate |
|---|---|---|---|
| 0 | discover `ls(root)` | `manifest_in.jsonl` | count ≤ §9a limit → else **fail**, never silently truncate |
| 1 | triage | `triaged.jsonl` | per-doc verdict; scanned → `needs_ocr`, job continues |
| 2 | extract | `extracted.jsonl` | `with_text + skipped + failed == docs_in` |
| 3 | chunk (**new `scripts/chunk_shard.py`**) | `chunks.jsonl` | `chunk_count > 0`/doc; `spec_hash` == library binding |
| 4 | embed | `*.emb.jsonl` | `dim == index dim`; count matches; quarantined reported |
| 5 | load | — | `count_library` delta == expected; **`count(tenant_id='public' AND library_id=L) == 0`** |
| 6 | publish | manifest, text, index | all writes acked |

Stage 3 is new work: `embed_shard.py` does chunk+embed in one call, so chunking cannot be gated independently today — and stage 6's `.ragstack/index/{doc_id}.json` needs those spans.

**Every artifact's line 1 is a run header** (the `.emb.jsonl` precedent): `{schema_version, run_id, library_id, tenant_id, collection, spec_hash, extractor, tokenizer, ragstack_version, docs_in}`. Without `docs_in`, stage 2's gate is uncheckable; without `spec_hash`, stage 3's is unsourceable.

**`extracted.jsonl` record** — note the key is **`path`**, not `ws_path`: `ingest_jsonl.py` reads exactly `text`, `path`, `metadata` (`_doc_id_key` `:86-88`, `enrich()`), so `ws_path` would silently yield `path=""`, hash the text for the doc_id, and kill the filename→DOI rule.

```jsonc
{"doc_id":"3f2b…","library_id":"lib_…","tenant_id":"bvbrc:awilke@bvbrc",
 "path":"/awilke@bvbrc/efflux-pumps/smith2019.pdf","ws_object_id":"0A1A…",
 "content_sha256":"9ad1…","state":"extracted","error":null,
 "text":"…","metadata":{"title":"…","pages":7}}
```

**Four changes `ingest_jsonl.py` needs:**
1. `--library-id`, stamped on every chunk and threaded to the store deletes.
2. Honour a record-supplied `doc_id`; delete `_doc_id_key` on the library path.
3. A real **`neutral`** enrichment profile, and make `resolve_profile` **raise** on unknown names (`enrich.py:122-132` currently degrades to ASM silently, so `--publisher-profile neutral` would invent DOIs today). `_kept` (`:252`) must emit a per-doc `skipped` verdict instead of dropping `EMPTY` — that *is* stage 2's gate.
4. **`--tenant` required with no default on the library path.** It currently defaults to `"public"` (`:1122-1123`) and stamps every chunk (`:934`), ignoring per-record values — a stage-5 invocation that omits it writes private papers into the world-readable tenant. Honour per-record `tenant_id`.

**`count_library(library_id)`** — new on `VectorStore`/`TextIndex`; `count_tenants` is tenant-scoped so the delta would be noise. **Fails closed on empty/None.** For `whole_index`, falls back to `count_tenants`.

**Run state lives in `library_runs`, not `JobStore`** — `IngestJob` has no tenant, stage, timestamps, and a 3-value item status against §7's 8.

**Token delegation:** stages 0/2/6 need the caller's token for a 20–40 min run. Check `token_exp` covers the estimate at submit → else 400; on mid-run expiry fail at the next stage boundary with a resumable checkpoint. **How the token reaches a CWL worker is unresolved** — `embed-bulk.cwl` passes `embedding_api_key` as a plain string input that lands in the GoWe job record. Decide: `EnvVarRequirement`, staged secret File, or GoWe-side injection. **Also: nothing in this repo reads CWL step outputs back into a store**, so `library_runs.gate_results` needs a write-back path that does not exist. Both are new work alongside #203.

**OCR out of scope** (`needs_ocr`, surfaced). Sizing ≤1000 PDFs: 20–40 min born-digital, **single-job best case** — under #204 one job uses ≤4 of 8 GPUs. Depends on **#203, #135, #86**.

---

## §7. Manifest — scoped libraries only

A **published summary** (start / stage boundaries / end), user-writable, **never trusted on read-back**. Absent or malformed ⇒ `needs_reindex`; still owned, listed and queryable. Schema per rev 2, plus per-doc `state` ∈ `pending | skipped | needs_ocr | extract_failed | embed_failed | indexed | stale | orphaned`.

`stale` = hash mismatch **or** last run retained prior chunks because new content failed to embed (`pipeline.py:217-227`) — unrepresentable today, and the case where the index silently disagrees with the source.

**Change detection:** no checksum in `ObjectMeta` and `ctime` is client-settable, so prefilter on `(blob.id, size, etag)` and full-read+SHA only the suspects. A hash-everything reconcile re-downloads the library and is not in the sizing model.

---

## §8. Lifecycle

### Per-kind applicability (normative — rev 2 omitted this and every row below fired)

| Behaviour | `scoped` | `whole_index` |
|---|---|---|
| `.ragstack/` manifest | yes | **none** — absent manifest does NOT mean `needs_reindex` |
| `library_documents` inventory | yes | **empty by design** — `GET /documents` returns `totals: null`, not "0 documents" |
| `POST /{id}/index` | yes | **405** |
| Orphan sweeper | yes | **excluded** — otherwise the entire production corpus is flagged for deletion |
| Zero-result `reason` | from inventory | `no_match` only — never `library_empty` |
| `DELETE` | yes | admin-only, deregisters; **never purges** |

### Events (scoped)

| Event | Behaviour |
|---|---|
| Document deleted in Workspace | Nothing until re-index. Re-index is a **reconciliation**: `ls`, diff, three-store delete for rows with no surviving blob. |
| Library deleted | Root probe 404s → queries stop. RAGStack's own inventory is what keeps the chunks deletable. |
| `.ragstack/` deleted | `needs_reindex`. Not a lockout (§5.1 probes the root). |
| Unshared | Probe denies within TTL. Nothing deleted. **Unshare and delete MUST NOT share a UI control.** |
| Chunk spec changed | **400.** A different spec ⇒ a different index ⇒ two silent copies. |
| Concurrent index | **409** with the in-flight `run_id`. |
| Query during re-index | Permitted, may be stale. **Delete phase runs only after every replacement chunk is durably upserted.** |

### §8.1 State store

`library_store_backend = memory | sqlite | postgres`. **DDL follows `jobstore.py:63-82`'s shared-dialect discipline: `TEXT` only** — no `JSONB` (sqlite gives it NUMERIC affinity), no `TIMESTAMPTZ` (store ISO-8601 UTC `TEXT`), structured data as `json.dumps`.

```sql
libraries(
  library_id TEXT PRIMARY KEY, name TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- 'scoped' | 'whole_index'
  root TEXT NOT NULL,                     -- synthetic for whole_index (§4). NEVER NULL.
  owner TEXT NOT NULL, tenant_id TEXT NOT NULL, collection_id TEXT NOT NULL,
  authz_backend TEXT NOT NULL,
  state TEXT NOT NULL, spec_hash TEXT, manifest_schema_version INTEGER,
  created_at TEXT, updated_at TEXT, deleted_at TEXT)

-- root uniqueness must exclude soft-deleted rows, or a purge=false delete
-- blocks re-registration for 30 days:
CREATE UNIQUE INDEX IF NOT EXISTS ux_libraries_root
  ON libraries(root) WHERE deleted_at IS NULL;
-- one whole_index library per collection, and never over a collection with scoped libraries:
CREATE UNIQUE INDEX IF NOT EXISTS ux_libraries_whole
  ON libraries(collection_id) WHERE kind = 'whole_index' AND deleted_at IS NULL;

library_documents(
  library_id TEXT, doc_id TEXT, ws_object_id TEXT, path TEXT,
  size_bytes INTEGER, content_sha256 TEXT, state TEXT, error TEXT,
  chunk_count INTEGER, chunks_quarantined INTEGER, text_path TEXT,
  last_run_id TEXT,                        -- so §9's failures[] is per-run
  indexed_at TEXT, indexed_spec_hash TEXT,
  PRIMARY KEY (library_id, doc_id))
CREATE INDEX IF NOT EXISTS ix_libdocs_lib ON library_documents(library_id);

library_runs(
  run_id TEXT PRIMARY KEY, library_id TEXT NOT NULL, stage TEXT, outcome TEXT,
  gate_results TEXT,                       -- json.dumps
  dry_run INTEGER NOT NULL DEFAULT 0,
  fence INTEGER NOT NULL,                  -- monotonic; checked at the delete phase
  lease_owner TEXT, lease_expires_at TEXT,
  started_at TEXT, finished_at TEXT)
CREATE INDEX IF NOT EXISTS ix_libruns_lib ON library_runs(library_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_libruns_active
  ON library_runs(library_id) WHERE finished_at IS NULL AND dry_run = 0;
```

**Migration convention.** There is no tooling in this repo (`jobstore.py` executes hardcoded `CREATE TABLE IF NOT EXISTS`, which cannot alter). **Decision: additive-only, via one helper `ensure_columns(conn, table, cols: dict[str,str])`** — postgres `ADD COLUMN IF NOT EXISTS`, sqlite via `PRAGMA table_info`. Consequences to honour: sqlite `ADD COLUMN` forbids `UNIQUE` and forbids `NOT NULL` without a default, so **every future column is nullable-or-defaulted**; and a `CHECK` inside `CREATE TABLE IF NOT EXISTS` never reaches an existing deployment, so **the kind/root invariant is enforced in Python, not SQL**. Record the Alembic gap as repo-wide debt.

**Run lock = lease + fence.** `finished_at IS NULL` is the lock predicate; `lease_expires_at` (heartbeat every 30 s, lease 120 s) lets a reaper set `finished_at='reclaimed'` on a dead run. A partitioned worker whose lease expired can still be mid-`delete_except`, so **the delete phase MUST re-read and verify its `fence` is still the row's fence** before deleting. Without that, "two reconciliations delete each other's work" is not actually prevented.

**Write ordering** (no transaction spans five stores): inventory row `pending` → Qdrant + ES + graph → flip `indexed` → publish manifest. A crash leaves a detectable half-state.

---

## §9. API

Contract-first: `openapi.yaml` + schemas + fixtures, then Python, then **Go returns 501 for the whole surface in v1** (so `conformance/test_libraries.py` skips on `impl == "go"`), then conformance.

**New schemas required** (all `additionalProperties: false`):

- **`error.json`** — none exists today; every error in `openapi.yaml` is a bare `description`. `{detail: string, code: string, library_id?: string|null, run_id?: string|null}`. Needed because 409 must carry the existing `library_id` / in-flight `run_id`. `Retry-After` declared as a response header (precedent: `X-Next-Cursor`, `openapi.yaml:149`).
- **`library.json`** — `library_id, name, kind, root, owner, state, access, created_at, updated_at, totals|null, last_run|null, spec_hash|null`. `state` ∈ `created|indexing|partial|ready|failed|needs_reindex|orphaned|deleted`. `access` ∈ `owner|reader`. `collection` is NOT exposed (operator concept).
- **`library_run.json`** — `{run_id, library_id, state, stage, dry_run, started_at, finished_at|null, documents:{total,indexed,needs_ocr,failed,pending}, gates:[GateResult], failures:[{doc_id, path, state, error}]}`. `stage` ∈ `discover|triage|extract|chunk|embed|load|publish`. `outcome`/`state` naming reconciled to **`state`**. `GateResult = {stage, gate: string, expected: string, actual: string, verdict: "pass"|"fail"}`.
- **`library_document.json`** — exposes `doc_id, path, state, error, chunk_count, indexed_at`. NOT `content_sha256`, NOT `text_path`, NOT `ws_object_id`. `error` is a caller-safe label only, never a raw path (`jobstore.py:37`).

**Count-shape reconciliation (canonical):** `failed = extract_failed + embed_failed`; `pending` counts `pending`; `totals` (§7) and run `documents` use the same five keys plus `stale`/`orphaned`/`skipped`/`chunks` in `totals` only.

`BvbrcToken` on every endpoint. All may return 401/429/503. **Every route not in this table returns 404 for a library principal.**

| Endpoint | Semantics |
|---|---|
| `POST /v1/libraries {root, name}` | **Registers** an existing top-level dir (upload is §12). **Ownership checked BEFORE the duplicate lookup**, else 409-with-existing-id is an existence oracle. `root` matched against `^/[^/]+@[^/]+/[^/]+$` **hand-rolled in the handler → 400** (a pydantic `pattern` yields 422). Duplicate active root → 409 + existing id. Collection from `LIBRARY_COLLECTION_ID`; unset → 503. 201 + `Location`. |
| `POST /v1/admin/libraries {collection_id, name, authz_backend, owner, tenant_id}` | **Admin only.** Creates a `whole_index` row with synthetic root (§4). `owner` and `tenant_id` are `NOT NULL` in §8.1 and operator-supplied; `owner` is a display marker that MUST NOT enter any filter (§4), and `tenant_id` is the row's bookkeeping tenant, not a query scope. Rejects (400) per §4's Python guard. |
| `GET /v1/libraries` | `?limit=` (1..1000, default 50), opaque `?cursor=`, `X-Next-Cursor`. Owned rows from the DB. **Shared-with-me: v1 scans all non-deleted rows via `list_readable`, capped at `LIBRARY_SHARE_SCAN_CAP` (default 500), and returns 503 if exceeded**. Note the cap is a **global row count**, so at ~25 users × 20 libraries this endpoint 503s for *everyone*, not just a heavy user — accepted for v1, and the reason §11 Q8 matters — there is no "shared with me" query (added to §11 Q8). **Pages may return fewer than `limit`; `X-Next-Cursor` presence, not fullness, signals more.** |
| `GET /v1/libraries/{id}` | **404, not 403**, when unreadable — 403 makes `library_id` an existence oracle. Holds surface-wide except `POST /v1/query`/`/v1/retrieve`, which 403 because the caller demonstrably knows the id. |
| `POST /v1/libraries/{id}/index {force?, dry_run?}` | Owner-only; **405 for `whole_index`**. `force` = re-extract even when the content hash matches. `dry_run` reports the diff, writes no chunks, takes no lock. In-flight → 409 + `run_id`. `token_exp` must cover the estimate → 400. 202 `{run_id}`. |
| `GET /v1/libraries/{id}/runs/{run_id}` | Tenant-scoped. The user-facing status surface (resolves #130 — `GET /v1/ingest/{job_id}` stays admin-only). `failures[]` populated **incrementally**, filtered by `last_run_id`. |
| `GET /v1/libraries/{id}/documents` | Paginated as above. Reads `library_documents`, not the manifest. `?state=`. |
| `DELETE /v1/libraries/{id}?purge=` | **`purge` mandatory** (no default) — an un-purged row-drop would destroy the only chunk inventory. **`kind='whole_index'`: admin-only, and `purge=true` → 405** (it would delete 24.8M production points; deregistration is `purge=false`). State matrix for `scoped`: active+`false` → 202 soft delete (`deleted_at`, stop serving, retain 30 d); active+`true` → 202 `{run_id}`, purge all three stores + `.ragstack/`, **never source files**; soft-deleted+`true` → 202 (always permitted after soft delete, else orphans are permanent); soft-deleted+`false` → 204; unknown → 404; run in flight → 409. |
| `POST /v1/query {library, …}` | `library` and `collection` **mutually exclusive** → 400. Omitting `library` keeps today's behaviour and MUST be an explicit choice, never a fallback from a failed lookup. `use_graph` forced `false` (§10.6). |
| `POST /v1/retrieve {library, …}` | Identical rules. *(Rev 2 omitted this shipped route entirely — `query.py:340-359`, free-form `filters` + `collection`.)* |
| `GET /v1/chunks?library=…` | `library` **required** for library principals. Uses the §4 widened tenant set and §10.3's post-filter. *(Rev 2 omitted it; without this, neighbour/context expansion returns zero rows on every shared library.)* |

**Zero-result rule.** MUST NOT call the LLM when retrieval returns zero chunks — today `generate(query, [])` runs with `"(no relevant passages found)"` (`llm.py:122`) and the answer depends on model compliance. **This widens the shipped contract**, so it moves to §10 fix-first: `query_response.json` `answer` becomes `["string","null"]`, and `reason` (`library_empty|library_indexing|no_match`), `library_state`, `indexed_documents`, `total_documents` are **always present, nullable outside library queries**. Go must match or `test_schema_validation` fails.

### §9a Limits

Per library ≤1000 documents, ≤20 GB — enforced at stage 0 as a **job failure**, never a silent truncate. Per user 1 concurrent run, ≤20 libraries. `top_k` **max 100**, `rerank_candidates` **max 500** (both are `ge=1` with no ceiling today). Rate limit → 429 (#87). `tenant_max_concurrency` MUST be non-zero in the BV-BRC deployment; the shipping default of 0 is a dev default and a production misconfiguration here.

---

## §10. Fix-first — ships and is verified before any library code

1. **#198** — one shared three-store teardown (vector + text + **graph**) with a collection parameter.
2. **#196** — empty-list fail-open in **Qdrant, ES *and* `stores/memory.py:24-25`**. Memory matters because §14 runs on it — otherwise the fail-closed assertions pass against an impl with no guard, the exact "green while proving nothing" failure §14 exists to avoid. `library_id`/`tenant_id` fail closed by **raising**, not via `_build_filter`.
3. **#197 `get_chunks`** — derive ids over `chunk_ids × tenants × {library_id}` (2-part key when `library_id` is absent), then **post-filter payloads** on both keys. Redundant on Qdrant, **load-bearing on memory** (`memory.py:103-117`). Thread `library` through the endpoint, `fetchChunks` (`client.ts:362`) and Compare (`CompareView.tsx:401`).
4. **#130** — resolved by §9's runs endpoint.
5. **#195** — `INGEST_ROOT` unset.
6. **Graph leg** — `_graph_context` pseudo-chunks carry **no metadata at all** (`retriever.py:92-98`) and reach the LLM prompt. **`use_graph` forced `false` for library queries in v1**; `/v1/graph/*` → 404 for library principals.
7. **`_final_status`** (`documents.py:40-51`) — currently `FAILED` only if `total>0 and completed==0`, so **zero items reads `completed`**. Replace with an **ordered** rule (rev 2's version was ill-formed — overlapping predicates): `if total==0: empty; elif completed==total: completed; elif completed>0 or failed>0: partial; else: failed`. `partial`/`empty` are **new wire values** → update `openapi.yaml:103`, Go, fixtures, and `python/tests/unit/test_final_status.py` (lines 16 and 25 assert the old behaviour and will fail).
8. **`query_response.json` widening** (above).
9. **`GET /v1/documents`** bypasses `scope_filters` (`documents.py:275-277`) → 404 for library principals until it takes a library scope. Same for `count_tenants`/`list_documents`, which take no filter dict.

---

## §11. Open questions — BLOCKING, for BV-BRC

1. Can BV-BRC compute reach coconut (`:9001–9008`, Qdrant `:6333`)? **(G3)**
2. Is there an upload analogue to `get_download_url`? A yes deletes most of #202/#195.
3. Chatbot server-to-server or browser? Determines delegation vs CORS/CSRF; if browser, a bearer token sits behind an XSS boundary (the UI persists keys in `localStorage`, `config.ts:30`).
4. Will users accept one top-level workspace per shareable library? Forced by §2.
5. Workspace rate limits for a service; does `create` batch? Gates §6 stage 6.
6. Is the token signing key published, and how does it rotate? **(G4)**
7. **Is `ObjectID` genuinely stable across move?** §3 rests on it and it is asserted without a citation.
8. **Is there a "workspaces shared with me" listing?** Without one, §9's shared enumeration is an O(all libraries) scan.

---

## §12. Non-goals (v1)

OCR · multi-library search · sharing UI in RAGStack · per-library chunk-spec choice · per-document delete · automatic index placement · RAGStack-fronted upload (if ever built it writes **through** the Workspace API with the user's token — an object outside the Workspace hierarchy has no `FullObjectPath` and cannot be authorized) · full OTEL (#89/#114).

---

## §13. Rollback

`LIBRARIES_ENABLED`, **default off** ⇒ routes unmounted, `library` → 400. No public-corpus path behaves differently, because no public chunk is written, migrated or re-keyed. Rollback = flag off, optionally drop `ragstack_lib_v1`.

**Exceptions that change shipped behaviour and each ship independently:** §10 items 1–3, 6–9, and `resolve_profile` raising (a typo'd profile that silently works today becomes a hard failure).

Every library request logs `{token_id_hash, tenant, library_id, run_id, endpoint, stage, duration_ms, outcome}`; `library_id` and `run_id` on every ingest and query log line.

---

## §14. Testability

`conformance/` is black-box HTTP with no Workspace, so **`LocalFs` + `LocalAclAuthz` ARE the test strategy.**

Env (following `run_authz_keyed.sh`, which exists because unset keys made every assertion silently skip): `LIBRARIES_ENABLED`, `BLOB_STORE_BACKEND`, `AUTHZ_BACKEND`, `LOCALFS_ROOT`, `LOCAL_ACL_FILE`, `LIBRARY_STORE_BACKEND`, `LIBRARY_COLLECTION_ID`, `RAGSTACK_LIB_KEY_OWNER`, `RAGSTACK_LIB_KEY_READER`.

Fixture tree — the root regex requires `@` in the first segment:
```
$LOCALFS_ROOT/owner@test/lib-a/{doc1.pdf, doc2.pdf}
$LOCALFS_ROOT/owner@test/lib-b/{doc3.pdf}
$LOCALFS_ROOT/acl.json   {"/owner@test/lib-a": {"owner@test":"a","reader@test":"r"},
                          "/owner@test/lib-b": {"owner@test":"a"}}
# keys are row.root verbatim — the §9 regex requires the LEADING SLASH
```
`__unavailable__` marker file at `$LOCALFS_ROOT/<root>/__unavailable__` forces `UNAVAILABLE`.

Required assertions: two-principal/two-library isolation; `GET /v1/libraries` omits lib-b for reader; duplicate root → 409; unreadable → **404 not 403**; missing `?purge` → 400; `X-API-Key` + `Authorization` → 400; `filters` containing `library_id` → 400; zero-result `reason` values; `__unavailable__` → 503 **not** 403 and **not** results.

Unit: legacy point-id/`_es_id` pin (Qdrant, ES) and the **identity-tuple widening** (memory — it has no UUID to pin).

**Infra-backed target** (`run_libraries_infra.sh`, not the memory suite): returned-hits == `min(k, |library|)` — a Qdrant segment-truncation property `InMemoryVectorStore` cannot exhibit.

---

## §16. Existing corpora — preservation and migration

**No plan here re-embeds anything.** 40.4M chunks (`ragstack_sfr_tok256` 24.8M, `tok512` 12.6M, `semantic` 3.0M) plus lucid.

### Tier 0 — preservation (default)

Guaranteed by §3's conditional point id and §4's separate index. **Verification gate before the §3 commit merges:**

Script `python/scripts/verify_point_id_invariance.py`, query set pinned as a fixture under `contracts/fixtures/queries/` (precedent exists):
1. Baseline counts via `client.count(exact=True)` **with a raised timeout** — `count_tenants` falls back to a segment estimate at `_COUNT_TIMEOUT_S` (`qdrant.py:283-299`) and an estimate is not stable across runs, so the gate would fail on unchanged code.
2. Three fixed queries per collection at `retrieval_mode=vector` with a **cached query vector** (HNSW is nondeterministic under concurrent optimizer activity), `top_k=20`.
3. Apply, re-run: counts identical, result ids identical, **scores equal within abs tol 1e-6**.
4. `GET /v1/chunks` round-trips a known chunk id per collection.

Rollback: the change is additive and conditional; reverting restores prior behaviour with no data touched.

### Tier 1 — adopt an existing corpus as a library (zero data cost)

`POST /v1/admin/libraries {collection_id, name, authz_backend:'local'}` → a `whole_index` row with synthetic root `local:collection/<collection_id>`. **No payload write, no index build, no downtime.**

**Enforcement caveat — read this before believing the ACL claim.** Today `_effective_collection` (`query.py:275-292`) returns the caller's `collection` unchanged whenever `allowed_collection_ids` yields `None`, which `tenancy.py:37-41` does for an empty mapping — and `tenant_collections` is `{}` by default and **commented out in prod** (`unified.env:64`). ASM chunks are stamped `public`, which `readable_tenants` hands to everyone. So a caller can send `POST /v1/query {"collection":"asm-tok512"}` and read the corpus without touching a library row.

**Therefore Tier 1 enforces nothing unless v1 also ships:** (a) `allowed_collection_ids` **default-deny for library principals** (an unlisted tenant gets `[]`, not `None`), and (b) a whole_index authz check on the `collection=` path, not only the `library=` path. **Both are in scope for v1**; without them §16's "public becomes an ACL state, not a separate code path" is aspirational, and the spec MUST NOT claim otherwise.

### Tier 2 — subdivide an existing corpus (only if actually needed)

Only if one collection must become several independently-shared libraries. Payload backfill, **still no re-embedding**: create the `library_id` index on that collection first (a background op at 24.8M points — and note §4 forbids doing this automatically at startup), `set_payload` over a selecting filter, verify subset and total counts, rollback via `delete_payload`. Cost is real (`on_disk_payload: true` ⇒ one payload write per point plus an index build, optimizer churn across ~99 segments): rehearse on a copy, schedule a window. **Tier 1 covers the actual requirement; Tier 2 is not routine.**

**Rejected: "absent `library_id` means public."** Needs `IsEmpty OR MatchAny`, which `_build_filter` cannot express, Qdrant cannot serve from a keyword index (an unindexed-field filter measured 8.9 s cold / 1.5 s warm vs 12 ms indexed), and which is exactly the multi-clause `should` shape G2 shows truncating.

### Tier 3 — build spec changes

Not a migration: content-addressing makes a different `(model, dim, chunk)` a different index by construction.

---

## §15. Build order

0. **G1, G2** (§-1). G3 before §6, G4 before §5.0.
1. **§10 fix-first 1–3, 6–9**, then #130/#195. Authorization bugs; nothing user-owned lands on top. Each ships independently (§13).
2. **§8.1** state store + `ensure_columns()`.
3. **§1 protocols** — including the `Principal` extension (prerequisite for `for_principal`, not part of the verifier) — plus `LocalFs`/`LocalAclAuthz` and the §14 seam. Build against the fake; BV-BRC impls slot in behind the same interfaces last.
4. **`LIBRARIES_ENABLED`** flag + Go 501 stubs + the conformance skeleton.
5. **§5.0 token verifier** — independently sized, blocked on G4. **Long pole; start in parallel with 1–3.**
6. Contract + read-only endpoints (register, admin-register, list, get, documents) and **`DELETE ?purge=false` only** — `purge=true` returns 501 until step 7, since it needs §3's library-aware deletes and §10.1. The conformance row for `purge=true` is written now and marked xfail until step 7 lands.
7. **§3 id change + `ragstack_lib_v1`. Atomic single commit.** Then `purge=true`.
   **7a. §16 Tier 0 verification gate — merge blocker.**
8. §4/§5.1 query scoping (`library_scope_filters`) + §16 Tier 1's two enforcement changes.
9. §6 ingest workflow — gated on G3.
