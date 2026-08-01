# Spec — user-owned libraries (RAGStack ⇄ BV-BRC)

Rev 4. MUST/MUST NOT are normative. Target: implementable without inventing decisions.

**What changed in rev 4.** Eight decisions taken after rev 3 merged, folded into the sections they
affect rather than appended: server-side multi-library fusion (§4, §9); §5.0 rewritten as a
provider-agnostic OAuth **resource-server** contract, reversing G4's choice of introspection; `doc_id`
derivation pinned with a sha256 fallback (§3); `max_segment_size` verified per-collection against live
Qdrant (§4); ingest inputs and the storage/cleanup model (§6); OCR shaped-for-but-not-in v1 (§6);
**multi-worker as the assumption, not single-worker** (new §8.2); and a two-layer sharing model with a
RAGStack-owned share table (§2, §8.1). §11 Q2, Q4, Q6 and Q8 are closed and moved into the sections
that answer them.

---

## §-1. Blocking gates

**G1 — tune retrieval for small libraries (#200). A required experiment, NOT a go/no-go.** All
quality numbers here were measured at ≥5.8k chunks with `--retrieve-pool 300 --rerank-pool 100`;
shipping defaults (`rrf_k=60`, `candidate_multiplier=2`, `top_k=5`, `rerank_enabled=False`) were
never under measurement. **The pipeline is parameterised, so the outcome is a settings change, not a
redesign** — sweep `rrf_k`, candidate multiplier, rerank on/off and dense-only via the eval seam
(#125/PR #126) and publish the winner as a normative `LibraryRetrievalDefaults` block in this spec.
Pre-registered, staged protocol: **[`docs/g1-retrieval-protocol.md`](g1-retrieval-protocol.md)** — a
pilot at **50 / 100 / 200 documents** first, the distractor ladder second. Run it early because it
decides whether a per-library-size parameter branch exists at all, which touches the config surface.

*G1 — corrected scale (rev 3 was wrong).* Measured `fixed_tok512` chunks/doc on real ASM articles is
**36.2** (`reports/chunking-comparison-overview.md:223`; 1,500 docs → 54,270 chunks; 33.8 on a 300-doc
subset, `chunking_compare_7way_report.md:96`). A ~200-doc library is therefore **~7k chunks, not ~4k**,
and a 1,000-doc library is **~36k, not ~72k** (§4 carried the same error). Rev 3's ~4k corresponds to a
~110-doc library.

*G1 — registered hypothesis, NOT an assumption (rev 3 stated it as fact).* Rev 3 asserted "BM25 may
return 3–4 hits against dense's 20." That is unverified and probably backwards.
`ElasticsearchTextIndex.search` (`stores/elasticsearch.py:143-168`) issues `size=top_k` against a
single `match` — an exact search returning `min(size, |matching docs|)` — so a BM25 leg asked for 10
returns 10 unless fewer than 10 chunks in the whole index contain any query term. Conversely
`QdrantVectorStore.search` (`stores/qdrant.py:243-249`) passes `limit=top_k` with **no `search_params`**
— no `hnsw_ef`, no `exact`, no oversampling — so the **dense** leg is the one that can silently
under-return. The claim is now H1b of the protocol, decided by returned-hit counters in the pilot. It
MUST NOT be used to justify a design decision until then.

*G1 — config gap: part of the deliverable is not currently expressible.* `settings.top_k`
(`config.py:305`) has **no reader in the retrieval path** — the API default is the literal `5` at
`api/routers/query.py`'s query and retrieve handlers and the effective value comes from `request.top_k`; the only thing that touches
the setting is the reflective loop at `api/routers/admin.py`'s reflective-config loop. `GET /v1/admin/config` (`api/routers/admin.py`)
exposes neither `rrf_k` nor `retrieval_candidate_multiplier`. So a `top_k` recommendation cannot be
shipped as configuration, and a deployed instance cannot self-report the two constants G1 tunes, until
both are fixed (~40 LOC; protocol §10 gap 9).

**G2 — re-measure the Qdrant filter in the v1 shape (#199).** Harness: `python/scripts/bench_filter_truncation.py` — scrolls real 4096-d SFR vectors read-only from prod into a guarded `g2bench_*` scratch collection. Note §4.3 makes every retrieval **leg** single-valued — rev 4 moves the fan-out and the RRF fusion server-side, but the leg constraint is unchanged and is what keeps G2 narrow. So G2 still does not gate *multi-scope* retrieval; it gates whether **one** library's slice of a large shared index retrieves correctly, which is now the unit that §9.2 fuses. **If anything rev 4 raises G2's stakes:** an under-returning leg no longer produces an obviously-empty answer, it produces a fused answer that quietly under-weights one library, and `legs[]` (§9) is the only thing that would make that visible. #199 measured a single key/value at 1% selectivity on synthetic 128-d vectors. v1 issues `library_id == X AND tenant_id ANY […]` at ~0.005%. Sweep 10⁻²→10⁻⁵ on real 4096-d SFR vectors. **Pass: returned-hits == `min(k, |library|)`.**

**G3 — RESOLVED, downgraded to a deployment question (§11 Q1).** The earlier framing assumed
the BV-BRC App Service would schedule ingest onto *BV-BRC* compute. It does not: **GoWe is the
execution plane and it runs on coconut** (live at `*:8091`, all interfaces; Qdrant `0.0.0.0:6333`).
A GoWe worker reaching the embedding fleet and Qdrant is a same-host call. The BV-BRC app is a
*submission surface*, not an execution target. §6 is therefore decidable now and no longer gated.

**G4 — RESOLVED, and rev 3's resolution is REVERSED.** Rev 3 chose token introspection over
offline verification and left rev 3's §11 Q6 open on "which endpoint introspects". Rev 4 inverts the
order: §5.0 is rewritten as a provider-agnostic OAuth **resource-server** contract whose **tier 1
verifies the existing BV-BRC token offline against a pinned issuer allowlist**. Introspection
needs an endpoint BV-BRC has not committed to and puts a synchronous third-party round-trip on
every cold request; offline verification needs only the issuer's public key, which the token's
own `sig=` field already presumes exists. Introspection becomes tier 3 — the thing we swap to
*if* BV-BRC ever runs an authorization server. Rev 3's §11 Q6 closes; the new §11 Q5 asks whether that authorization server will ever exist.

**What replaces G4 is not a gate but a named risk.** BV-BRC tokens carry **no audience claim**,
so one token is equally valid at GoWe, at the Workspace and at RAGStack. This is not theoretical:
GoWe performs **no signature verification at all**. `internal/server/auth.go` (`extractToken`,
`apiAuthMiddleware`) accepts the header, calls `internal/bvbrc/auth.go`'s `ParseToken` — which
splits on `|`, reads only `un` and `expiry`, and **does not even extract `sig`** — and then
promotes to admin off that self-asserted `un`. A repo-wide grep of GoWe for `createVerify`,
`VerifyPKCS1v15`, `rsa.Verify`, `PublicKey`, `jwt`, `jwks` returns **zero hits**. `pkg/bvbrc/auth.go`
has a second `ParseToken` that *does* extract `sig` into `Token.Signature`, and nothing ever reads
that field — the appearance of signature handling without the substance. `TokenInfo.IsExpired`
additionally treats an absent `expiry` as *not expired*. The live server binds `*:8091` with
`--admins awilke,awilke@bvbrc,olson,olson@bvbrc`, so a forged `un=olson|expiry=9999999999` is
network-reachable admin there today.

**Consequence for this spec, stated plainly: a credential presented to GoWe is a credential that
works at RAGStack.** §5.0 carries the mitigations (short cache TTL; never accept a credential
RAGStack did not receive directly from the client) and the honest bound on each. Reporting the
GoWe finding upstream is out of scope here but MUST NOT be skipped.

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
                       #   Workspace: ObjectID  (§3.1; residual doubt is §11 Q4,
                       #              non-blocking because §3.1 records BOTH keys)
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

@dataclass(frozen=True)
class Identity:
    subject: str          # AUTHORITATIVE. From the VERIFIED credential (§5.0), never parsed
                          # out of an unverified one.
    issuer: str           # "bvbrc" | "google" | …  MUST be in the pinned allowlist (§5.0).
    token_id: str         # stable per credential; cache key. Used only AFTER verification.
    expires_at: int | None    # absent/None => treated as EXPIRED (§5.0), not as eternal.
    scopes: frozenset[str] = frozenset()
                          # RESERVED, UNUSED in v1. Providers MUST return empty; v1
                          # authorization MUST NOT read it, and empty MUST NOT be read as
                          # "no permissions". Present now so scoped credentials are not a
                          # breaking change when §5.0 tier 3 lands.

class IdentityProvider(Protocol):
    """Who is this caller? Raises IdentityInvalid (401) | IdentityUnavailable (503).
    Implementations authenticate by VERIFYING the credential against the issuer —
    offline signature check (v1: BvbrcSignedToken, Oidc) or, at §5.0 tier 3,
    introspection. Both are authoritative. What is forbidden is reading an identity
    out of an UNVERIFIED credential. RAGStack is a resource server and MUST NOT
    implement a mint(...) counterpart to this method."""
    async def authenticate(self, credential: str) -> Identity: ...

class AuthzDecision(StrEnum):
    ALLOW = "allow"; DENY = "deny"; UNAVAILABLE = "unavailable"

class AuthorizationProvider(Protocol):
    async def access(self, p: Principal, root: str) -> AuthzDecision: ...
    async def owner_of(self, root: str) -> str: ...
    async def list_readable(self, p: Principal, roots: list[str]) -> dict[str, AuthzDecision]: ...
```

`UNAVAILABLE` ≠ `DENY`: both refuse, but they map to different HTTP statuses and different alerts. A `-> bool` interface cannot express that and MUST NOT be used.

**Three interfaces, three questions:** `IdentityProvider` (who are you?), `AuthorizationProvider` (may you read this?), `BlobStore` (give me the bytes). Keeping them apart is what lets a non-BV-BRC deployment exist at all — an S3 backend has no ACLs and a Google-authenticated user has no Workspace.

**Provider selection is PER LIBRARY** (`libraries.authz_backend`), not global. At startup every non-deleted library's `authz_backend` MUST resolve to a configured provider or that library is **unmounted and logged** — the process still starts. (Rev-2 had a global setting; it was unsatisfiable, since prod needs `bvbrc` for user libraries and `local` for the ASM/lucid whole-index rows simultaneously.)

**Write / index / delete are owner-only**, decided by `owner_of(root) == principal.tenant`, never by `access`.

---

## §2. Workspace ACL constraint and the two-layer sharing model

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

### §2.1 Two layers, deliberately delineated

One-library-per-top-level-directory is the *only* Workspace-native sharing, and it is all-or-nothing
per directory. The operator calls that **"barely acceptable"** and wants sharing at the granularity of
an individual grantee. Rev 3 answered by deferring to §11 Q4 ("will users accept it?"). Rev 4 answers
by splitting the question in two, because *file* access and *library* access are not the same
question and only one of them is the Workspace's to decide.

| Layer | Authority | Stores | Scope |
|---|---|---|---|
| **File access** | the **Workspace ACL**, via the §5.1 root probe | nothing — probed live, cached by TTL | may this principal read the bytes? |
| **Library access** | RAGStack's **`library_shares`** table (§8.1) | grantee rows, never file permissions | may this principal see and query this library? |

**The invariant, normative and non-negotiable: a share grant is NECESSARY BUT NOT SUFFICIENT.**
Every library read is the **conjunction** of both layers. The §5.1 probe still runs on every request,
still fails closed, and a `library_shares` row **MUST NOT** override a probe `DENY` or a probe
`UNAVAILABLE`. RAGStack therefore can never grant access to files the caller cannot already read.
Reject any implementation that consults the share table *instead of* the probe, or that caches an
ALLOW derived from a share row.

**This is a bounded reversal of rev 3's "RAGStack never stores an ACL", and the bound is the point.**
RAGStack stores **library shares** — a grantee list scoped to a `library_id`. It **MUST NOT** store,
mirror, cache or infer file permissions, per-document ACLs, or anything keyed on a Workspace path.
There is exactly one new table (§8.1 `library_shares`) and it has exactly one column that names a
principal.

**What this buys, honestly:**
- **Narrowing is fully supported.** Within the set of principals the Workspace already lets read the
  directory, the owner decides who may query the library in RAGStack — and can revoke instantly,
  without touching the Workspace and without the "unshare vs delete" hazard of §8.
- **Discovery becomes a first-class query**, not a scan. See `ux_libshares_grantee` in §8.1 and the
  rewritten `GET /v1/libraries` in §9.
- **Non-Workspace backends get genuine individual sharing today.** `LocalAclAuthz`, S3 and
  Google-authenticated libraries have no top-level-directory constraint, so for them the two layers
  collapse and the share table is the whole answer. This is also what §14 tests.

**What this does NOT buy, stated so nobody discovers it in review:** for a Workspace-backed library,
a grantee who is *not* on the Workspace directory ACL still cannot read it, share row or no share
row. Widening past the Workspace ACL is *technically* reachable — the chunks live in Qdrant, which
RAGStack owns, not in the Workspace — but it would require RAGStack to serve a grantee off the
**owner's** stored credential, which §5.0 forbids outright ("mints no long-lived credentials of its
own"; "never accept a credential RAGStack did not receive directly from the client"). If "barely
acceptable" becomes unacceptable, **§5.0's no-stored-credentials rule is the rule to revisit**, and
that is a security decision, not a schema one. It MUST NOT be worked around incrementally.

### §2.2 Discovery MUST NOT depend on a Workspace "shared with me" call

Rev 3's §11 Q8 asked whether the Workspace offers one. **The answer no longer matters and the
question is closed:** discovery goes through an internal interface that can be backed either way.

```python
@dataclass(frozen=True)
class Share:
    library_id: str
    grantee: str          # a tenant string "issuer:subject", or the reserved "public"
    access: str           # "reader" — v1 mints no other value
    granted_by: str
    created_at: str

class ShareResolver(Protocol):
    """Which libraries is this principal a grantee of? Never answers 'may they read it' —
    that is AuthorizationProvider's question and it is asked separately, every request."""
    async def shared_with(self, p: Principal, *, limit: int = 50, cursor: str | None = None
                          ) -> tuple[list[Share], str | None]: ...
    async def grantees_of(self, library_id: str) -> list[Share]: ...
    async def grant(self, library_id: str, grantee: str, *, by: Principal) -> Share: ...
    async def revoke(self, library_id: str, grantee: str, *, by: Principal) -> None: ...
```

`LocalShareResolver` (reads `library_shares`, §8.1) is v1. A `BvbrcShareResolver` backed by the
BV-BRC API slots in behind the same interface with no change above it — the same swap §5.0 makes for
identity. `grant`/`revoke` are **owner-only**, decided by `owner_of(root) == principal.tenant` per §1,
never by `access`.

---

## §3. Identity keys

- `library_id` — server-minted, opaque, **not a secret**. `^lib_[0-9a-f]{12}$`, carried as a schema `pattern`; a non-matching path param is 400 before any store lookup. §9's fan-out reserves two additional literals, `@me` and `@public`, which are **not** library ids and are expanded server-side.
- `doc_id` — derived per **§3.1**. **MUST NOT** derive from a filesystem path.
- `chunk_id` — unchanged.

### §3.1 doc_id derivation — identity and change are two different questions

**These are two questions and they MUST NOT be answered by one key.** The ObjectID answers
*"is this the same document?"*. The content hash answers *"has it changed?"*. Collapsing them
produces the failure below.

1. **Primary: `doc_id = uuid5(NAMESPACE_URL, blob_meta.id)`.** For a Workspace-backed library
   `blob_meta.id` is the ObjectID, which is stable across rename and move (§11 Q4 records the
   residual doubt — it is no longer blocking, because rule 3 below records both keys either way).
2. **Fallback, for backends with no stable id: `doc_id = uuid5(NAMESPACE_URL, f"sha256:{content_sha256}")`.**
   This is the LocalFs and S3 case in practice. **sha256, never md5** — md5 collisions are
   constructible, and a collision here silently merges two documents into one `doc_id` across every
   store. The cost is zero: §6 stage 2 already emits `content_sha256` on every `extracted.jsonl`
   record and §8.1 already persists it, so the fallback reads a value the pipeline computed anyway.
   *(Note the repo does not compute a content hash today — `grep -rn sha256 python/ragstack python/scripts`
   hits only `scripts/eval/g1_library_sweep.py`. The field is new work in §6 stage 2, not an
   existing value being reused. It is on the critical path for the fallback and for §7's change
   detection alike, so it is one implementation, not two.)*
3. **Record both, always, for every backend.** `library_documents.ws_object_id` and
   `library_documents.content_sha256` are both populated even when only one of them fed the
   `doc_id`. Without both, question 2 is unanswerable for Workspace libraries and question 1 is
   unanswerable for LocalFs ones.

**Normative: a changed hash under an unchanged ObjectID is an UPDATE, not a new document.** Same
`doc_id`, `state` → `stale`, and the next run re-extracts and replaces its chunks under §8's
delete-after-durable-upsert ordering. It MUST NOT mint a new `doc_id`, and the prior chunks MUST NOT
survive the replacement.

**Why a `filename + hash` composite is rejected.** It moves on rename *and* on edit. Replacing
`smith2019.pdf` with a corrected version therefore reads as an **insert plus an orphan**: the new
composite has no inventory row so it is indexed fresh, and the old composite has no surviving blob
so §8's reconciliation flags it `orphaned` — the corpus now answers the same query twice, from two
generations of the same paper, and the stale one is only removed if the operator runs a sweep. The
same argument rejects any path-derived key, which is why `blob_meta.id` for LocalFs is
`f"local:{st_dev}:{st_ino}"` and not `sha256(realpath)` (§1).

**Target the code §6 actually runs:** `scripts/ingest_jsonl.py` — `_doc_id_key` and the three
`deterministic_doc_id` call sites. It **never imports `JsonlLoader`**, so patching `ingestion/loaders.py`'s
`JsonlLoader` would ship a green diff while the real path keeps minting `uuid5(resolve(path))`
against the worker CWD. `ingestion/loaders.py`'s `deterministic_doc_id` and the loader classes that
call it cover the API path.

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

**Query-path branch point.** New `library_scope_filters(filters, principal, lib) -> LibraryScope` in `tenancy.py`, replacing `scope_filters` in `api/routers/query.py` and the `/v1/query` equivalent. It branches on `lib.kind` and pins scope keys **last**, exactly as `scope_filters` does (`tenancy.py`). A library query resolves its collection from `libraries.collection_id` and **MUST bypass `_effective_collection` / `allowed_collection_ids`** (`api/routers/query.py`) — the library row is the authority.

**`filters` MUST reject `library_id` and `tenant_id`** → 400. `additionalProperties:false` does not catch it (`filters` is free-form).

**Payload index is PER COLLECTION.** `_ensure_tenant_index` (`qdrant.py:176-188`) runs from `ensure_collection`, which `deps.py:298` calls for **every registry entry at every startup** and swallows errors. Generalizing it globally would fire a `create_payload_index("library_id")` against 24.8M / 12.6M / 3.0M / lucid at every restart — exactly the §16 Tier-2 operation that requires a maintenance window. **Only `ragstack_lib_v1` gets `library_id` indexed.**

**Index name is HAND-PINNED: `ragstack_lib_v1`.** Its build spec is identical to prod `ragstack_sfr_tok512`, so content-addressing would **collide**. Created with **both `max_segment_size` and `full_scan_threshold` pinned explicitly**, on its own Qdrant instance via `QDRANT_COLLECTION_ROUTES`.

### §4.1 Segment tuning is PER COLLECTION — verified live, not inferred

Rev 3 hedged on whether pinning these knobs could perturb the production corpora. It cannot, and this
is now measured rather than assumed. Against the live Qdrant (1.18.0):

- **`max_segment_size` lives in `optimizers_config`, is set at `create_collection`, and is mutable
  afterwards via `PATCH /collections/{name}`.** Verified end-to-end on a scratch collection: created
  with `max_segment_size=20000` and read back as `20000`; `PATCH`ed to `50000` and read back as
  `50000`. **There is no global override** — the value is absent from every server-level surface and
  is `null` (unset, i.e. Qdrant's default) on all four production collections.
- **`full_scan_threshold` lives in `hnsw_config`**, likewise per collection and likewise settable at
  create and by `PATCH`.
- **Per-collection divergence is already a fact of this deployment**, which is the strongest available
  proof these are not global: `ragstack_sfr_tok256` runs `indexing_threshold=10000` while
  `ragstack_sfr_tok512` and `ragstack_sfr_semantic` run `20000`. Two live collections on one server
  already disagree.

**Therefore pinning `max_segment_size` and `full_scan_threshold` on `ragstack_lib_v1` is entirely
local and cannot perturb the production corpora.** No maintenance window, no §16 Tier-2 concern, no
coupling to the shared instance. This closes the only cost objection to pinning them.

**Per-request override, precisely.** `full_scan_threshold` itself is **not** a `search_params` field —
`SearchParams` in qdrant-client 1.18.0 carries exactly `hnsw_ef`, `exact`, `quantization`,
`indexed_only`, `acorn`. The per-request lever is **`exact: true`**, which forces the full-scan path
regardless of the collection's threshold (and `indexed_only`, which forces the opposite). Rev 4 states
this explicitly because "overridable per request" is true of the *behaviour* and false of the *field*,
and a reader who goes looking for `search_params.full_scan_threshold` will not find it.

**Live baseline for §4.2's arithmetic** (`points_count` / `segments_count`, read from the running
instances): `ragstack_sfr_tok256` 24,830,600 / **99**; `ragstack_sfr_tok512` 12,587,981 / **51**;
`ragstack_sfr_semantic` 2,982,219 / **13**; `lucid_sfr_tok256` 1,554,790 / **8**. All four sit near
~230k–250k points per segment, and all four run `full_scan_threshold=10000` with `on_disk_payload:true`.

### §4.2 Which side of the full-scan handoff a library falls on

At `full_scan_threshold=10000` KB and 16 KiB/vector the planner hands off from payload-index full scan
to HNSW at ~625 vectors **per segment**. **Corrected (rev 3 arithmetic was wrong; its directional
conclusion is withdrawn, not replaced).** Rev 3 put a 1000-PDF library at ~72,000 chunks over ~99
segments ≈ 730/segment — "on the boundary" — but 72 chunks/doc is the measured `fixed_tok256` figure
(`reports/chunking-comparison-overview.md`), not `fixed_tok512`'s **36.2** (same report, and §-1), so a
1000-PDF library is **~36,000 chunks**, not ~72,000.

The ~99-segment divisor does not survive the correction either. It belongs to the 24.8M-point prod
collection (§4.1's baseline; §16 Tier 2), has never been measured on a library-sized index, and
**cannot apply to this collection at all**: the capacity rail below caps `ragstack_lib_v1` at ~800/82 ≈
**7–10 libraries ≈ 250k–360k points in total** — roughly *one* prod segment's worth — so the whole
index never reaches the point count that produced ~99 segments. The only counts near this scale point
the other way: G2 built **500k points across 8 segments** (§-1 G2), live `lucid_sfr_tok256` holds
**1.55M across 8**, and the G1 protocol's manifest example records **6,108 points in 2 segments**
(`docs/g1-retrieval-protocol.md` §8.2). At a plausible **2–8 segments** a 36.2k-chunk library is
**4,500–18,000 points per segment, 7–29× above the ~625 handoff — firmly on the HNSW side**.
Equivalently, the handoff falls at **~1,250–5,000 chunks per library ≈ 35–140 documents**, so
essentially every v1-sized library lands on the HNSW side, not the full-scan side.

That is a plausibility range, not a measurement: **which side of the handoff a library falls on is
undetermined until `segments_count` is recorded on a real library-sized `ragstack_lib_v1`** (the G1
pilot records it per rung — `g1-retrieval-protocol.md` §8.1). Two things follow regardless of how it
resolves. First, the uncertainty **strengthens** the requirement this section exists to justify: with
the boundary this sensitive to `max_segment_size` and merge timing, **both** `max_segment_size` **and**
`full_scan_threshold` MUST be pinned explicitly, so the side is a deliberate config decision rather
than a consequence of segment-merge timing — and per §4.1 that pinning is free. Second, if libraries do
sit on the HNSW side — the direction every available segment count indicates — then **G2's
filtered-HNSW result is more relevant to v1, not less**, because the approximate path is the one v1
actually exercises. ~82 VMAs/library, ~800 per process; at 70% registration returns **503** and an
operator provisions the next index (there is no automatic placement in v1).

### §4.3 Multi-library queries: single-valued legs, server-side RRF fusion

**Every retrieval LEG carries exactly ONE `library_id`.** A single-valued filter cannot enter the
`MatchAny` truncation band, so the failure G2 measures is structurally unreachable for any scope built
this way. This constraint is unchanged from rev 3 and is the whole reason the design is safe.

**What changed in rev 4: the fan-out and the fusion are RAGStack's job, not the client's.** Rev 3
specified `POST /v1/query {library}` — singular — and left the caller to issue N queries and merge
them. That is wrong. **The chatbot MUST NOT implement RRF.** Rank fusion is a retrieval concern with
retrieval-specific failure modes (rank-vs-score, per-leg over-fetch, tie handling); pushing it across
the API boundary guarantees every client reimplements it differently and none of them can be
evaluated by our own eval seam. So:

**`POST /v1/query` and `/v1/retrieve` take `libraries: [...]`, a LIST. RAGStack fans out one
single-valued query per library and merges the ranked lists with `RRFScorer` server-side.** Wire
format, expansion, per-leg authorization and error semantics are normative in §9.

- **Reuses existing machinery, exactly.** `HybridRetriever` (`ragstack/retrieval/retriever.py`) already
  fuses ranked lists (vector + BM25 + graph) through `RRFScorer` (`ragstack/scoring/scorers.py`), which
  it holds as `self.rrf`. Per-library legs are the same operation with more legs. This is not a new
  component; it is a second application of a shipped one.
- **RRF, not score-averaging.** Rank fusion is robust to score-scale differences between legs.
  `ragstack_lib_v1` and `asm-tok512` share a build spec so raw scores would be comparable, but
  `asm-semantic` is semantically chunked — same model and dim, different chunk lengths, biased raw
  cosine. Ranks are unaffected. G1's `rrf_k` result applies to library fusion too, and MUST be reused
  rather than re-tuned separately.
- **Over-fetch per leg:** request `top_k` from each leg and fuse down to `top_k`; the global
  distribution is not knowable in advance.
- Legs are independent → `asyncio.gather`, so latency is the slowest leg, not the sum. Note each leg is
  itself hybrid, so L legs is 2L store calls plus up to L authorization probes — hence the cap.
- **Bounded fan-out: `LIBRARY_FUSION_MAX_LEGS`, default 8.** Rev 3 said 4; 4 cannot serve the
  convenience form, because §9a permits **20 libraries per user** and `@me` expands to all of them.
  Exceeding the cap is a **400 naming the cap**, never a silent truncation (§6 stage 0's rule, applied
  to the query path). **Recorded tension:** 8 < 20, so a user at the §9a ceiling cannot say "search
  everything I own" in one call. Accepted for v1 — the alternative is either an unbounded fan-out or a
  `MatchAny` filter, and the second is the exact failure this design exists to avoid. Revisit by
  raising the cap once real per-leg latency is measured, not by widening the filter.

**This settles the question §4 previously deferred.** "My papers + BV-BRC's literature" is two
retrievers fused — and it works **across physical collections**, which a payload filter could never
do. The 40.4M public chunks are therefore reachable as a *leg*, not via a widened filter. §9's
response carries **per-source attribution** so a citation traces to the leg it came from; that is a
hard requirement of this design, not a nicety, because a fused result set is otherwise unciteable.

---

## §5. AuthN / AuthZ

### §5.0 Identity — an OAuth resource-server contract, provider-agnostic

**RAGStack is a RESOURCE SERVER. It is NEVER an authorization server.** It validates credentials
minted elsewhere and MUST NOT mint any long-lived credential of its own — no refresh tokens, no
service accounts standing in for a user, no "RAGStack API key that means Alice". The only
credentials it issues are the existing operator `X-API-Key` values, which are an admin/ops surface
and MUST NOT be minted per end user. This is one sentence but it is the load-bearing one: it is what
makes the audience gap below bounded rather than unbounded, and it is what forbids the widening
workaround §2.1 rejects.

BV-BRC and Google auth are two **prototypes of one interface**, not two subsystems. Everything below
is `IdentityProvider` (§1); `BvbrcSignedToken` is the first implementation and `Oidc` is the proof
the border is real.

**The invariant, for every provider:** the subject is established by **verifying** the credential
against the issuer — either by verifying a signed claim offline against the issuer's published key,
or by asking the issuer directly. Reading an identity out of an unverified credential is forbidden
in all cases. This is the invariant GoWe violates (§-1 G4); RAGStack MUST NOT inherit the habit.

`tenant = f"{issuer}:{subject}"`. Namespacing keeps a BV-BRC `alice` distinct from a Google `alice`
and both clear of the reserved `public` / `default`. An identity pairs with whichever
`authz_backend` its library declares — a Google-authenticated user has no Workspace, so those
libraries use `LocalAclAuthz`.

**Evidence for the starting state:** nothing in the repo verifies a BV-BRC token —
`ragstack/ingestion/gowe_client.py` only forwards one in an `Authorization` header, and
`contracts/openapi.yaml` declares exactly one security scheme, `ApiKeyAuth` (`X-API-Key`), under
`components.securitySchemes`. Every token-handling behaviour in this section is new work.

#### Three tiers, in this order

| Tier | What RAGStack does | Ships |
|---|---|---|
| **1** | Verify the **existing** BV-BRC token **offline** against a **pinned issuer allowlist**. No new BV-BRC endpoint, no new BV-BRC work, no synchronous third-party call on the request path. | v1 |
| **2** | Formalise `IdentityProvider` with `BvbrcSignedToken` and a generic `Oidc` implementation (discovery document + JWKS + `Authorization: Bearer`). | v1 — the interface; `Oidc` is the conformance proof |
| **3** | If BV-BRC ever runs an authorization server, swap the implementation. Introspection, `aud`, scopes and rotation all arrive **behind the interface, with no change above it.** | later, no redesign |

Tier 3 is the whole point of tiers 1 and 2: the migration is a provider swap in one factory, not a
re-architecture. Rev 3 put introspection at tier 1 (G4); rev 4 reverses that, because tier 1 must not
depend on an endpoint BV-BRC has not committed to.

#### Tier 1 — offline verification, pinned issuer allowlist

- Wire scheme for BV-BRC: `type: apiKey, in: header, name: Authorization` — *not* `http`/`bearer`;
  the BV-BRC wire format has no `Bearer ` prefix. Tier 2's `Oidc` provider uses
  **`Authorization: Bearer <jwt>`**, the standard form, and the two are distinguished by shape, not
  by a second header.
- Format `un=…|tokenid=…|expiry=…|sig=…`. **The identity comes from the VERIFIED signature, never
  from parsing `un=`.** Verification order is normative: parse → look up the issuer key → **verify
  `sig` over the signed portion** → *then* read `un` and `expiry` from the now-trusted bytes. A
  parser that reads `un` first and verifies later is the GoWe failure with an extra step.
- **Pinned issuer allowlist**, `BVBRC_ISSUER_KEYS`: an explicit map of issuer → public key,
  configured, not discovered. **No key is fetched from a URL named in the credential**, which is the
  standard confused-deputy in offline JWT verification. An unknown issuer is **401**, never a fetch.
  Rotation is an operator config change; the allowlist holds multiple keys per issuer so rotation
  needs no downtime.
- `Oidc` verifies the JWT against a JWKS reached from a **configured** discovery URL — same rule, and
  it takes `sub`. Both return the same `Identity`.
- **`expiry` absent MUST be treated as EXPIRED → 401.** GoWe's `TokenInfo.IsExpired` treats an absent
  expiry as *not expired* (§-1 G4); RAGStack MUST invert that. Local expiry is checked **every
  request, uncached**, so an expired credential never reaches the provider or the cache.
- Cache on `(issuer, token_id)`, TTL `min(300s, expires_at − now)`, bounded LRU. The cache key is
  used **only after** successful verification — otherwise it is attacker-chosen, and a forged
  credential bearing a victim's `token_id` poisons the victim's entry.
- Yields `Principal(tenant=f"{identity.issuer}:{identity.subject}", role=ROLE_RESEARCHER, token=…,
  token_id=…, token_exp=…)`. **The role is explicit and MUST NOT fall through to `default_role`** —
  prod runs `DEFAULT_ROLE=admin` (`/rag/config/unified.env`), which would make every researcher a
  superuser.
- Both `X-API-Key` and `Authorization` present → **400**. Enforced in a dedicated dependency;
  `APIKeyHeader(auto_error=False)` cannot do it alone.
- Failures: malformed / bad signature / expired / unknown issuer → **401**. A provider that must
  reach the network and cannot → **503**, never 401, never allow. Tier 1 has no such path by
  construction, which is a second reason to prefer it.
- **`TenantQuota._sems` MUST gain LRU eviction** before tenant derives from a username (`quota.py`
  says so in-code) — see §8.2, which also makes it a *shared*-state problem, not only a growth one.

#### RISK — the audience gap (named, accepted, bounded)

**BV-BRC tokens carry no `aud` claim.** One token is therefore equally valid at GoWe, at the
Workspace and at RAGStack: there is nothing in the credential that says which service it was
presented to, so possession anywhere is possession everywhere. Combined with §-1 G4's finding that
**GoWe performs no signature verification at all** and exposes `*:8091` on all interfaces, the
practical statement is: **a credential presented to GoWe is a credential that works at RAGStack.**
Offline verification does not fix this — it fixes forgery, not replay across audiences.

Mitigations, each with its honest bound:

| Mitigation | Bound |
|---|---|
| **Short cache TTL** (`min(300s, exp − now)`, and 60 s on a DENY) | Caps how long a *stale authorization* is honoured. Does **not** cap the credential's own lifetime. |
| **Never accept a credential RAGStack did not receive directly from the client.** No credential arrives via a query parameter, a request body field, a redirect, or a third party's forwarded header. RAGStack MUST NOT store a user credential at rest, and MUST NOT act on an owner's credential to serve a different principal (§2.1). | Keeps RAGStack from *widening* the blast radius. Does nothing about a credential the attacker already holds. |
| **Log `token_id_hash`, never the credential** (§13); `Principal.__repr__` redacts `token`. | Keeps RAGStack out of the leak path. |

**Revocation before expiry is impossible without introspection. State it plainly: the cache TTL is
the honest bound on revocation lag, and the token's own `expiry` is the honest bound on
compromise.** RAGStack cannot do better at tier 1, and pretending otherwise in an operator-facing
document would be worse than the gap. Tier 3 is what closes it, and closing it is the single
strongest argument for BV-BRC running an authorization server.

Rev 3 floated *"the §5.1 Workspace probe may subsume token validation, so auth costs zero extra
round-trips."* **Rejected.** It inverts the layering — `IdentityProvider` and
`AuthorizationProvider` are separate for the reason §1 gives — and it makes authentication depend on
a per-library backend, so a `local`-authz library would have no authentication path at all. Tier 1
costs no round-trip anyway, which removes the only motivation. Rev 3's §11 Q6 closes with this.

#### `Identity.scopes` — reserved, unused

`Identity` (§1) gains one field now, so that scoped credentials are not a breaking change later:

```python
scopes: frozenset[str] = frozenset()   # RESERVED. v1 providers MUST return empty.
                                       # v1 authorization MUST NOT read it.
```

Normative for v1: providers MUST populate it empty, and no authorization decision may consult it —
an empty set MUST NOT be interpreted as "no permissions". When tier 3 arrives, a provider starts
populating it and the enforcement point is added in one place. Adding the field later instead would
change `Identity`'s constructor across every implementation and every test.

### §5.1 Per-query authorization

```
1. verify token (§5.0)                              every request, uncached
2. library_id -> libraries row                      RAGStack table
2a. owner? OR unrevoked library_shares row?         RAGStack table, NEVER memoized (§2.1)
        -> no  => 404 (list/get) | 403 (query)      short-circuits; step 3 not reached
3. authz.access(principal, row.root)                memoized
        -> DENY | UNAVAILABLE => refuse (§9.2)      a share row MUST NOT override this
4. library_scope_filters(...) per §4
```

**Step 2a is NECESSARY, step 3 is also necessary, and neither is sufficient alone** (§2.1). 2a is
placed first only because it is a local read that cheaply avoids a network probe; the ordering is an
optimization and MUST NOT be read as precedence. Reversing them would be equally correct and
strictly slower. **For a fan-out query (§9.2) this whole sequence runs once per leg.**

**A `whole_index` library skips 2a** — those are shared by registration (§16 Tier 1) and carry no
share rows by §8.1's rule. Their step 3 runs against the synthetic `local:collection/{id}` root.

**Probe the ROOT**, never `.ragstack/library.json` — §2 invites the user to delete that folder, and probing it would turn a cache clear into a permanent lockout.

| Probe outcome | Decision | Cached |
|---|---|---|
| 200 | ALLOW | yes, TTL `min(300s, token_exp − now)` |
| 401 / 403 | DENY | yes, TTL 60 s |
| 404 | **not visible** — read-only | yes, TTL 60 s |
| 429 / 5xx / timeout | UNAVAILABLE | **no** |

**A non-owner probe MUST NOT mutate library state.** Rev 2 mapped 404 → "mark `orphaned`", but the probe runs with the *caller's* token and Workspaces return 404 for objects you cannot see. Any holder of a `library_id` (explicitly "not a secret") could mark **someone else's** library orphaned and feed it to the deletion sweeper. `orphaned` is set **only** by a reconciliation run or an owner-credentialed probe.

**HTTP mapping lives in §9, not here.** This section returns an `AuthzDecision`.

**Cache.** Memoizes **step 3 only** — never step 2a, whose whole value is instant revocation (§8 events). Key `(token_id, library_id)`. Bounded LRU 10 000, **per process** (N workers ⇒ N caches, N× probe volume — see §8.2; this one is *acceptable* per-process state, because a cache that is merely cold is correct, unlike a quota that is merely local). **Single-flight per key**, which also bounds an 8-leg fan-out to one probe per distinct library rather than one per leg-request. Revocation lag == TTL, documented, and is the §5.0 audience-gap bound. On `UNAVAILABLE` the request is refused — the house default of degrading to empty is the #196 fail-open class, and §9.2 extends the refusal to the whole fused request.

---

## §6. Ingest

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

### §6.1 Ingest inputs — Workspace upload AND direct Shock over HTTP

**Both MUST be supported, because GoWe reads from both.** A library's source documents arrive either
by Workspace upload (the `p3-cp` shape a BV-BRC user already knows) or as a direct Shock URL fetched
over HTTP. Neither is a fallback for the other; they are the two ways a BV-BRC user already has bytes.

**Verified GoWe capability** (function/file-level citations, `/rag/repos/GoWe`):

| Capability | State | Evidence |
|---|---|---|
| Workspace JSON-RPC client | **exists, complete** | `pkg/bvbrc/workspace.go` — `WorkspaceLs`, `WorkspaceGet`, `WorkspaceCreate`, `WorkspaceUpload`, `WorkspaceGetDownloadURL`, `WorkspaceSetPermissions`, `WorkspaceListPermissions`, … |
| Workspace stager | **exists and is LIVE** | `pkg/staging/workspace.go` — `WorkspaceStager` (`StageIn`/`StageOut`), registered as scheme `ws` in `internal/worker/worker.go` **only when `--workspace-stager` is passed** (`cmd/worker/main.go`) |
| Shock stager | **exists, NOT registered** | `pkg/staging/shock.go`, registered as scheme `shock` in `internal/worker/worker.go` **only when `--shock-host` is non-empty** |
| Generic HTTP stager | **exists, ALWAYS registered**, supports upload | `internal/execution/http_stager.go` — `HTTPStager`; `internal/worker/worker.go` seeds `http`/`https` (and `file`) unconditionally; `HTTPStagerConfig.UploadMethod` defaults to **PUT** |

**Live worker survey (5 online, matching `GET /api/v1/health` → `workers.online: 5`):** four workers
(`cpu-worker-1`, `cpu-worker-2`, `worker-1`, `worker-2`) carry `--workspace-stager`. **Zero workers
carry `--shock-host`, so `shock://` is an unregistered scheme in production today** — a config flip on
the worker command line, **not new code**.

**Correction to the assumption this section was written under:** `--workspace-stager` is on *four* of
five workers, and the one that lacks it is **`ragstack-cpu-1`** — the `--runtime none --group
ragstack-cpu` worker, i.e. precisely the group a RAGStack ingest would target. So the Workspace stager
is live on the fleet but **not on the group this workflow runs in**. Treat that as a second config
flip, and as a gate on §6 stage 0/6: **a RAGStack ingest MUST NOT be scheduled to a group whose
workers lack the `ws` scheme handler**, and stage 0 MUST fail fast with a legible error rather than
producing an unresolvable `ws://` URI mid-run.

**GAP — large-file Workspace upload is new work, not a config flip.** `WorkspaceStager.StageOut` does
`os.ReadFile(srcPath)` and hands the whole thing to `upload` → `Client.WorkspaceUpload` →
`WorkspaceCreate`, which embeds the content **inline in the JSON-RPC params** and JSON-encodes it. The
payload is resident at least three times (bytes, Go string, JSON-escaped body), with no streaming, no
multipart and no size guard. The intended alternative — the Workspace mints a Shock node and the
client uploads to it — is `WorkspaceCreateInput.CreateUploadNodes`, and a repo-wide grep finds
**exactly three lines**: the field declaration, its doc comment, and the `if input.CreateUploadNodes`
inside `WorkspaceCreate`. **Zero callers set it.** (One raw-JSON literal in `internal/ui/handlers.go`
sets `"createUploadNodes": true`, bypassing the typed client — a standalone UI path, not the stager,
and its own adjacent comment concedes the stager cannot do this.) `docs/BVBRC-API.md` advertises
`createUploadNodes` as the fix for "File too large for inline", which is the exact problem, unwired.

**Normative consequence:** v1 MUST NOT route library documents through `WorkspaceStager.StageOut` for
anything above a small size bound. Either (a) fetch via `WorkspaceGetDownloadURL` + the always-on HTTP
stager, which streams; or (b) wire `CreateUploadNodes` through `WorkspaceStager`, which is new GoWe
work. §9a's 20 GB per-library ceiling is meaningless against a stager that materialises each file
three times in RAM. *(This is the concrete answer to rev 3's §11 Q2 — "is there an upload analogue to
`get_download_url`?" Yes: `Workspace.create` with `createUploadNodes`, mediating a Shock node. It is
declared and unreachable. Q2 closes with that answer and the gap moves here.)*

### §6.2 Storage model and scratch reclamation

**Normative: the Workspace holds INPUTS and FINAL RESULTS only. Everything else is temporary local
disk.** Intermediate artifacts — `manifest_in.jsonl`, `triaged.jsonl`, `extracted.jsonl`,
`chunks.jsonl`, `*.emb.jsonl` — live in the worker's task directory and MUST NOT be staged back to the
Workspace. Only stage 6's `.ragstack/` publication (manifest, text, chunk index, run records) is
written to the Workspace. This keeps the Workspace round-trips bounded per run instead of per stage,
which is also the strongest lever on §11 Q3's rate-limit exposure.

**GoWe has NO cleanup at all, and this is now our operational problem.** Evidence: `internal/worker/`
contains **zero** `os.RemoveAll` and **zero** `os.Remove` — `worker.go` does `os.MkdirAll(taskDir, …)`
per task and `stagein.go` creates more, with no matching deletion anywhere. The only repo-wide
`os.RemoveAll` is in a throwaway script (`scripts/test-shock-stager.go`).

Live state on coconut, `/scout/wf/gowe/workdir`: **67 GB across 3,833 entries in 27 worker
subdirectories.** The two live GPU workers hold 350 and 351 entries at 17 GB each — and note those 350
entries are **~120 tasks × 3 directories** (`task_<uuid>`, `..._tmp`, `..._output`), not 350 tasks.
**Worse than the headline: ~33 GB sits in `worker-4` and `worker-18`, which have no live process at
all** — orphaned scratch that nothing will ever reclaim, since ownership died with the worker and
there is no reaper.

**The documented `--cleanup` flag does not exist.** `docs/Remote-Worker-Analysis.md` specifies
`--cleanup=auto|immediate|manual`; `cmd/worker` registers no such flag.
`docs/GoWe-Implementation-Plan.md` carries it as "DEFER: cleanup(workDir)".
`docs/Workflow-Engines-Comparison.md` claims the worker "handles … cleanup", which is true of container
lifecycle and false of disk. The only cleanup-ish flag in the repo is `--no-cleanup` in
`cmd/smoke-test`, which deletes an API record, not disk state. `docs/tools/worker.md` already concedes
the answer: "configure automatic cleanup via cron."

**Therefore scratch reclamation is RAGStack's operational responsibility, and MUST ship with §6, not
after it:**
- A **cron reaper** on coconut over `/scout/wf/gowe/workdir/*/task_*`, deleting task trees older than
  a retention window (start at 72 h) **and** any subtree under a worker directory with no live
  process. It MUST key on directory mtime and MUST NOT delete a `task_*` whose run is still
  `finished_at IS NULL` in `library_runs` (§8.1) — the fence discipline applies to the reaper too.
- A **free-space precondition at stage 0**: estimate `20 GB × concurrent runs` and fail the submit
  with a legible error rather than filling the volume mid-embed.
- The reaper is **RAGStack-operated infrastructure, not a GoWe patch**. Sending the `--cleanup` flag
  upstream is the right long-term fix and MUST NOT be treated as a blocker for v1.

### §6.3 OCR — out of v1, but the workflow is SHAPED for it

**OCR remains out of scope for v1.** Stage 1 already emits `needs_ocr` per document and §7 already
carries it as a document `state`, so scanned PDFs are *surfaced*, never silently dropped.

**Normative shaping requirement: an OCR stage MUST slot between triage (1) and extract (2) without
restructuring anything.** Concretely, that means v1 MUST hold to all of:
- Stage boundaries are artifact boundaries. `triaged.jsonl` is a complete, self-describing input;
  an OCR stage consumes it and emits a `triaged.jsonl` of the same schema with `needs_ocr` documents
  resolved. Stage 2 MUST NOT be able to tell whether stage 1.5 ran.
- Stage numbering is **not** an ordinal contract. `library_runs.stage` is the `TEXT` name
  (`discover|triage|extract|chunk|embed|load|publish`, §9's `library_run.json`), so inserting `ocr`
  is an added enum value, not a renumbering.
- The per-document `state` vocabulary already contains `needs_ocr`, so no state machine changes.
- Nothing downstream of stage 2 may read `needs_ocr` — it is resolved before extract or it stays a
  terminal verdict. A stage-4 or stage-5 branch on it would have to be unpicked later.

**OCR is the ONLY 100× term in the sizing model, which is why it is out and why the seam matters.**
Born-digital extraction of ≤1000 PDFs runs 20–40 min (single-job best case; under #204 one job uses ≤4
of 8 GPUs). OCR is per-page model inference and moves that to the tens of hours, changing the job
shape, the GPU budget, the §6 token-delegation window (which already only barely covers 20–40 min) and
§9a's concurrency limit simultaneously. It is not a stage that can be "just enabled": it is a
re-sizing. Ship the seam, not the stage.

Depends on **#203, #135, #86**.

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
| Unshared (Workspace ACL) | Probe denies within TTL. Nothing deleted. **Unshare and delete MUST NOT share a UI control.** |
| Share revoked (RAGStack, §2.1) | `library_shares.revoked_at` set. Takes effect on the next request — **no TTL**, because the share table is read directly, not memoized. The §5.1 probe cache is untouched and irrelevant: revoking a share removes a *necessary* condition, so a cached ALLOW cannot resurrect access. Nothing deleted. |
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
  gowe_submission_id TEXT,                 -- §15 step 9: poll it, never wait() on it
  started_at TEXT, finished_at TEXT)
CREATE INDEX IF NOT EXISTS ix_libruns_lib ON library_runs(library_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_libruns_active
  ON library_runs(library_id) WHERE finished_at IS NULL AND dry_run = 0;

-- §2.1 layer 2: LIBRARY-level sharing. NOT a file ACL. Necessary, never sufficient:
-- the §5.1 Workspace probe still runs on every request and still fails closed.
library_shares(
  library_id TEXT NOT NULL,
  grantee TEXT NOT NULL,        -- tenant string "issuer:subject", or the reserved 'public'
  access TEXT NOT NULL,         -- 'reader' only in v1; no writer grant exists
  granted_by TEXT NOT NULL,     -- the granting principal's tenant; owner-only, checked in Python
  created_at TEXT NOT NULL, revoked_at TEXT,
  PRIMARY KEY (library_id, grantee))
-- the index that makes "shared with me" ONE query instead of an O(all libraries) scan:
CREATE INDEX IF NOT EXISTS ix_libshares_grantee
  ON library_shares(grantee) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_libshares_lib ON library_shares(library_id);
```

**`library_shares` notes, normative.**
- `grantee` is a **tenant string**, `f"{issuer}:{subject}"` (§5.0) — never a bare username, or a
  BV-BRC `alice` and a Google `alice` collide. The reserved literal `public` is permitted and is what
  `@public` expansion reads; `default` MUST be rejected.
- `access` is `'reader'` and only `'reader'` in v1. Write/index/delete stay owner-only, decided by
  `owner_of(root)` (§1), and a share row MUST NOT participate in that decision.
- **Rows are never deleted; `revoked_at` is set.** A revoke-then-regrant must not lose the audit
  trail, and the primary key is `(library_id, grantee)` so regrant is an update.
- **The row is read directly on every request, never memoized.** The §5.1 cache memoizes step 3
  (the probe) only. Caching the share lookup would reintroduce a revocation lag that the table
  exists to eliminate — and unlike the probe, this read is local.
- **A `library_shares` row MUST NOT be created for a `whole_index` library.** Those are shared by
  being registered (§16 Tier 1) and their `root` is synthetic; a share row there would imply
  RAGStack can grant access to the whole production corpus per user, which §4's `whole_index` MUST
  NOTs forbid. Enforced in Python, like §4's kind/collection guard — no SQL constraint spans tables
  here.

**Migration convention.** There is no tooling in this repo (`jobstore.py` executes hardcoded `CREATE TABLE IF NOT EXISTS`, which cannot alter). **Decision: additive-only, via one helper `ensure_columns(conn, table, cols: dict[str,str])`** — postgres `ADD COLUMN IF NOT EXISTS`, sqlite via `PRAGMA table_info`. Consequences to honour: sqlite `ADD COLUMN` forbids `UNIQUE` and forbids `NOT NULL` without a default, so **every future column is nullable-or-defaulted**; and a `CHECK` inside `CREATE TABLE IF NOT EXISTS` never reaches an existing deployment, so **the kind/root invariant is enforced in Python, not SQL**. Record the Alembic gap as repo-wide debt.

**Run lock = lease + fence.** `finished_at IS NULL` is the lock predicate; `lease_expires_at` (heartbeat every 30 s, lease 120 s) lets a reaper set `finished_at='reclaimed'` on a dead run. A partitioned worker whose lease expired can still be mid-`delete_except`, so **the delete phase MUST re-read and verify its `fence` is still the row's fence** before deleting. Without that, "two reconciliations delete each other's work" is not actually prevented.

**Write ordering** (no transaction spans five stores): inventory row `pending` → Qdrant + ES + graph → flip `indexed` → publish manifest. A crash leaves a detectable half-state.

### §8.2 MULTI-WORKER is the assumption — single-worker correctness is a bug, not a baseline

Rev 3 wrote §8.1's lease+fence for a multi-worker world and left the rest of the spec assuming one
process. **Rev 4 makes the assumption uniform: every component in the library path MUST be correct
with N > 1 processes.** Two independent reasons, one already true and one arriving:

1. **The execution plane is already multi-worker.** GoWe runs **5 online workers** on coconut
   (`GET /api/v1/health` → `workers.online: 5`, matching five `gowe-worker` processes: `cpu-worker-1`,
   `cpu-worker-2`, `worker-1`, `worker-2`, `ragstack-cpu-1`), plus 4 registered-but-offline. Any
   library ingest stage runs on one of them, and which one is not knowable in advance.
2. **The API plane is single-worker *today* but already exhibits two of the failures below.** No
   `--workers`, `UVICORN_WORKERS` or `WEB_CONCURRENCY` appears anywhere in `deploy/`, `apptainer/`,
   `scripts/`, `python/docker/Dockerfile` or `/rag/config/*.env`, so each uvicorn runs one process.
   But prod runs **three separate API instances** (ports 8000, 8010, 8020) which **share a
   `collections_file`** — so items 3 and 4 below are live defects right now, not hypotheticals. And
   the moment anyone sets `--workers > 1` to serve L-leg fan-out concurrency (§4.3), all five fire at
   once.

**Normative: each of the following MUST be fixed before, or as part of, the component that depends on
it. None may be deferred with "we only run one worker."**

| # | Component | What breaks under N processes | Required fix |
|---|---|---|---|
| 1 | `TenantQuota` (`ragstack/quota.py`; `slot`) | `_sems` is a plain in-process `dict[str, asyncio.Semaphore]`, and `asyncio.Semaphore` is loop-local. The in-code docstring's atomicity argument ("no await between lookup and insert") is true intra-process and meaningless across processes. **Effective per-tenant concurrency becomes N × `tenant_max_concurrency`** — the quota multiplies by the worker count. Constructed per process in `api/deps.py`'s lifespan. | A shared counter (Redis, or a Postgres advisory lock keyed on tenant) behind the same `slot()` interface. Plus the LRU eviction §5.0 already requires — under §5.0 the key becomes a username, so the dict is unbounded *and* wrong. |
| 2 | `PooledEmbedder` (`ragstack/embed_pool.py`) | Every piece of its state is per process: `_sem` (the "global" concurrency cap — global only within one process), each `Endpoint`'s mutable `healthy`/`active` counters, `_last_health`, `_health_lock`. Consequences: the in-flight cap becomes N × `embedding_max_concurrency`; `_select`'s least-loaded routing sees only its own worker's load, so **all N workers herd onto the same "least loaded" endpoint**; health demotion/recovery is not shared, so each worker probes independently; and `endpoint_load()` (surfaced to the Ops dashboard via `api/routers/models.py`) reports one worker's slice while presenting as fleet state. | Shared load/health state, or accept the herding and **document the cap as per-worker** so the operator multiplies it themselves. Herding is the one that actually degrades throughput and MUST be measured before §6 sizing is trusted. |
| 3 | `CollectionRegistry.add` / `.remove` (`ragstack/api/collections.py`) | Backing store is `self._entries: dict[str, CollectionEntry]` — an in-process dict with no shared backing. `POST /v1/collections` (`api/routers/collections.py`) mutates only the worker that served it; the other N−1 `KeyError` in `resolve` and return 400/404 **until restart**. `CollectionEntry` also holds live objects (`retriever`, `vector_store`, `text_index`, `embedder`) which are inherently unshareable, so the fix is not "share the dict". | Make the **persisted spec** the source of truth and the dict a per-process cache with an invalidation signal (re-read on miss, plus a version/mtime check). Live objects are rebuilt per process from the spec, which is what startup already does. **Already broken across the three prod instances.** |
| 4 | `persist_collection_spec` / `forget_collection_spec` (`ragstack/api/collections.py`) | Unlocked read-modify-write: `os.path.exists` → `json.load` → `append` → `_atomic_write_json`. `_atomic_write_json` does `os.replace` from a temp file, so readers never see a torn file — but **concurrent writers lose updates** (two workers each read N entries and each write N+1; one spec vanishes), and the temp path is a fixed `path + ".tmp"`, so the writers also stomp each other's temp file. The docstring concedes "same single-worker caveat as the model registry" (`ragstack/api/model_registry.py` has the identical shape). | `flock`/`fcntl` around read-modify-write, **and** a per-writer unique temp path. Both are small; the second is required even with the lock, for crash safety. **Already broken across the three prod instances.** |
| 5 | `PostgresJobStore.fail_interrupted` (`ragstack/jobstore.py`) | A hard-coded `return 0`. Its own comment explains why: the sweep is unscoped and would mark every non-terminal job failed, including ones legitimately running in sibling workers (tracked as issue #7). Sole caller is `api/deps.py`'s lifespan, which logs a count that is always zero on Postgres. **Net effect: on the multi-process backend, jobs killed with a worker stay non-terminal forever.** The in-memory and sqlite stores do a real sweep, so the single-worker backends are the only ones that work. | This is **exactly the problem §8.1 already solved** for `library_runs`: `lease_owner` + `lease_expires_at` + heartbeat, reaped by expiry rather than by a blanket sweep. Port that discipline to `JobStore` — do not invent a second mechanism. |

**Consistency requirement.** §8.1's lease+fence already assumes multi-worker; items 1–5 are what make
the rest of the system agree with it. A design where run state is fenced but the quota, the embedder
pool, the collection registry and the job store are all per-process is not multi-worker-safe — it
merely *looks* safe at the one place someone thought about it.

**§14 addition.** The library conformance suite MUST include at least one assertion that fails under
naive per-process state — the natural one is item 3: register a collection, then read it back through
a *different* worker. That requires the infra-backed target (`run_libraries_infra.sh`) to run the API
with `--workers 2`, which is a one-line change and the only way any of this is verified rather than
asserted.

---

## §9. API

Contract-first: `openapi.yaml` + schemas + fixtures, then Python, then **Go returns 501 for the whole surface in v1** (so `conformance/test_libraries.py` skips on `impl == "go"`), then conformance.

**New schemas required** (all `additionalProperties: false`):

- **`error.json`** — none exists today; every error in `openapi.yaml` is a bare `description`. `{detail: string, code: string, library_id?: string|null, run_id?: string|null}`. Needed because 409 must carry the existing `library_id` / in-flight `run_id`. `Retry-After` declared as a response header (precedent: `X-Next-Cursor`, `openapi.yaml:149`).
- **`library.json`** — `library_id, name, kind, root, owner, state, access, created_at, updated_at, totals|null, last_run|null, spec_hash|null`. `state` ∈ `created|indexing|partial|ready|failed|needs_reindex|orphaned|deleted`. `access` ∈ `owner|reader`. `collection` is NOT exposed (operator concept).
- **`library_run.json`** — `{run_id, library_id, state, stage, dry_run, started_at, finished_at|null, documents:{total,indexed,needs_ocr,failed,pending}, gates:[GateResult], failures:[{doc_id, path, state, error}]}`. `stage` ∈ `discover|triage|extract|chunk|embed|load|publish`. `outcome`/`state` naming reconciled to **`state`**. `GateResult = {stage, gate: string, expected: string, actual: string, verdict: "pass"|"fail"}`.
- **`library_document.json`** — exposes `doc_id, path, state, error, chunk_count, indexed_at`. NOT `content_sha256`, NOT `text_path`, NOT `ws_object_id`. `error` is a caller-safe label only, never a raw path (`jobstore.py`'s error-label rule).
- **`library_share.json`** — `{library_id, grantee, access, granted_by, created_at}` for §9.1's share endpoints. `revoked_at` is NOT exposed; a revoked share is simply absent.

**Two SHIPPED schemas widen** (both are §10 fix-first items, because they change the existing contract):

- **`source.json`** gains `library_id: ["string","null"]` — the leg a chunk came from. It is
  `additionalProperties: false`, so §4.3's per-source attribution **cannot** ride in `metadata` as a
  convention; it needs the field. `null` outside library queries, and `null` for a `whole_index` leg's
  chunks (whose payloads carry no `library_id` by §4's MUST NOTs) — in that case the leg is identified
  by `legs[]` below, which is why both are needed. Go must match or `test_schema_validation` fails.
- **`query_response.json`** gains `legs` alongside the §10.8 widening:
  `legs: [{library_id, name, kind, retrieved: int, contributed: int}] | null` — one entry per fan-out
  leg, `retrieved` = hits that leg returned, `contributed` = hits that survived fusion into `sources`.
  `null` for a non-library query. This is what makes a fused answer auditable: without it, "which of
  my libraries actually answered this?" is unanswerable, and a zero-contribution leg is
  indistinguishable from a leg that was never run.

**Count-shape reconciliation (canonical):** `failed = extract_failed + embed_failed`; `pending` counts `pending`; `totals` (§7) and run `documents` use the same five keys plus `stale`/`orphaned`/`skipped`/`chunks` in `totals` only.

`BvbrcToken` on every endpoint. All may return 401/429/503. **Every route not in this table returns 404 for a library principal.**

| Endpoint | Semantics |
|---|---|
| `POST /v1/libraries {root, name}` | **Registers** an existing top-level dir (upload is §12). **Ownership checked BEFORE the duplicate lookup**, else 409-with-existing-id is an existence oracle. `root` matched against `^/[^/]+@[^/]+/[^/]+$` **hand-rolled in the handler → 400** (a pydantic `pattern` yields 422). Duplicate active root → 409 + existing id. Collection from `LIBRARY_COLLECTION_ID`; unset → 503. 201 + `Location`. |
| `POST /v1/admin/libraries {collection_id, name, authz_backend, owner, tenant_id}` | **Admin only.** Creates a `whole_index` row with synthetic root (§4). `owner` and `tenant_id` are `NOT NULL` in §8.1 and operator-supplied; `owner` is a display marker that MUST NOT enter any filter (§4), and `tenant_id` is the row's bookkeeping tenant, not a query scope. Rejects (400) per §4's Python guard. |
| `GET /v1/libraries` | **THE DISCOVERY CALL.** This is what a chatbot calls to populate `libraries` on the next row, so it MUST return enough to choose: `library_id, name, kind, access, state, totals`. `?limit=` (1..1000, default 50), opaque `?cursor=`, `X-Next-Cursor`. Owned rows come from `libraries`; **shared-with-me comes from `ShareResolver.shared_with` (§2.2), which is ONE indexed query over `ix_libshares_grantee`, not a scan.** `list_readable` then runs over that bounded candidate set to apply the §2.1 probe gate — so a library the caller was granted but cannot read in the Workspace is **omitted**, not errored. **`LIBRARY_SHARE_SCAN_CAP` and its 503 are DELETED**: rev 3's O(all libraries) scan 503'd for *everyone* once the global row count passed 500, which was the worst property of the whole surface. **Pages may return fewer than `limit`; `X-Next-Cursor` presence, not fullness, signals more.** |
| `GET /v1/libraries/{id}` | **404, not 403**, when unreadable — 403 makes `library_id` an existence oracle. Holds surface-wide except `POST /v1/query`/`/v1/retrieve`, which 403 because the caller demonstrably knows the id. |
| `POST /v1/libraries/{id}/index {force?, dry_run?}` | Owner-only; **405 for `whole_index`**. `force` = re-extract even when the content hash matches. `dry_run` reports the diff, writes no chunks, takes no lock. In-flight → 409 + `run_id`. `token_exp` must cover the estimate → 400. 202 `{run_id}`. |
| `GET /v1/libraries/{id}/runs/{run_id}` | Tenant-scoped. The user-facing status surface (resolves #130 — `GET /v1/ingest/{job_id}` stays admin-only). `failures[]` populated **incrementally**, filtered by `last_run_id`. |
| `GET /v1/libraries/{id}/documents` | Paginated as above. Reads `library_documents`, not the manifest. `?state=`. |
| `DELETE /v1/libraries/{id}?purge=` | **`purge` mandatory** (no default) — an un-purged row-drop would destroy the only chunk inventory. **`kind='whole_index'`: admin-only, and `purge=true` → 405** (it would delete 24.8M production points; deregistration is `purge=false`). State matrix for `scoped`: active+`false` → 202 soft delete (`deleted_at`, stop serving, retain 30 d); active+`true` → 202 `{run_id}`, purge all three stores + `.ragstack/`, **never source files**; soft-deleted+`true` → 202 (always permitted after soft delete, else orphans are permanent); soft-deleted+`false` → 204; unknown → 404; run in flight → 409. |
| `POST /v1/query {libraries: […], …}` | **`libraries` is a LIST — see §9.2.** `libraries` and `collection` **mutually exclusive** → 400. Omitting `libraries` keeps today's behaviour and MUST be an explicit choice, never a fallback from a failed lookup. `use_graph` forced `false` (§10.6). |
| `POST /v1/retrieve {libraries: […], …}` | Identical rules. *(Rev 2 omitted this shipped route entirely — `api/routers/query.py`'s `/v1/retrieve` handler, free-form `filters` + `collection`.)* |
| `GET /v1/chunks?library=…` | **Singular, and deliberately so** — chunk fetch is by id within one library, not a fused search, so there is nothing to fuse. `library` **required** for library principals. Uses the §4 widened tenant set and §10.3's post-filter. *(Rev 2 omitted it; without this, neighbour/context expansion returns zero rows on every shared library.)* |
| `GET /v1/libraries/{id}/shares` | Owner-only → `[library_share.json]` from `ShareResolver.grantees_of`. **404 (not 403) for a non-owner**, per the existence-oracle rule. `whole_index` → **405**. |
| `PUT /v1/libraries/{id}/shares/{grantee}` `{access}` | Owner-only. `access` MUST be `"reader"` → else 400. Idempotent: regrant of a revoked share clears `revoked_at`. 200 + `library_share.json`. `grantee` MUST match the tenant-string shape `^[a-z0-9_]+:[^\s]+$` or be the literal `public`; `default` → 400. `whole_index` → **405** (§8.1). **MUST NOT validate that the grantee exists** — that would make the endpoint a user-enumeration oracle, and a grant to a non-existent principal is harmless because §5.1 still gates. |
| `DELETE /v1/libraries/{id}/shares/{grantee}` | Owner-only. Sets `revoked_at`; row retained (§8.1). 204, idempotent. |

### §9.1 Sharing endpoints — what they do and do not decide

These write §2.1's **library** layer only. They MUST NOT call the Workspace, MUST NOT attempt to
alter a Workspace ACL, and MUST NOT be presented to the user as "sharing the files" — the operator
still shares the top-level directory in the Workspace, and RAGStack decides who may query the library
on top of that. A UI that conflates the two will produce grants that silently do nothing, which is
the failure mode §2.1's "necessary but not sufficient" sentence exists to prevent. **`PUT` SHOULD
therefore return the current probe outcome for the grantee where cheaply available**, so the operator
learns immediately that a grant is inert.

### §9.2 `libraries` — wire format, expansion, authorization, errors

```jsonc
{"query": "efflux pump inhibitors",
 "libraries": ["lib_a1b2c3d4e5f6", "@public"],   // 1..LIBRARY_FUSION_MAX_LEGS after expansion
 "top_k": 5}
```

- **Type: array of strings**, each matching `^(lib_[0-9a-f]{12}|@me|@public)$` as a schema `pattern`,
  so a malformed element is 400 before any store lookup (§3's rule, extended). `null`/absent = today's
  non-library behaviour. **An empty array is 400**, never "search everything".
- **Reserved expansions, minted server-side:**
  `@me` → every non-deleted library where the caller is `owner` **or** an unrevoked `library_shares`
  grantee. `@public` → every `whole_index` row with a `public` share grant. Both expand **before** the
  cap check and **before** authorization, and duplicates after expansion are collapsed.
- **Cap:** more than `LIBRARY_FUSION_MAX_LEGS` (default 8, §4.3) legs after expansion → **400 naming
  the cap and the resulting leg count**. Never a truncation. This is the one place a convenience form
  can fail, and it MUST fail loudly.
- **Per-leg authorization is the full §5.1 sequence, per library, `asyncio.gather`-ed.** The §5.1
  cache (keyed `(token_id, library_id)`) makes repeat fan-outs cheap; a cold 8-leg query is 8 probes.
- **Error semantics, normative:**

  | Condition | Result |
  |---|---|
  | An **explicitly named** library the caller cannot read | **403** for the whole request — the caller demonstrably knows the id (existing §9 rule), and silently dropping it would misrepresent the answer's coverage. |
  | A library id that does not exist, explicitly named | **404** for the whole request. |
  | A library that arrived via `@me`/`@public` expansion and is unreadable | **omitted**, no error. By construction the caller never asserted it, and `legs[]` records what actually ran. |
  | Any leg returns `UNAVAILABLE` (§5.1) | **503 for the whole request.** MUST NOT degrade to the surviving legs: a partial fusion returned as a complete answer is the #196 fail-open class with better manners. `legs[]` cannot rescue this, because the LLM has already seen a truncated context by the time the response is built. |
  | A leg returns zero hits | Fine. Recorded in `legs[]` with `retrieved: 0`. The zero-result rule below applies to the **fused** set, not per leg. |

  **Recorded consequence:** one flaky Workspace probe fails an otherwise-good "my libraries + public"
  query. Accepted, and partly self-limiting — `whole_index`/public legs use `local` authz, which has
  no network path and therefore no `UNAVAILABLE`. Revisit only with an explicit
  `allow_partial: true` request flag that also forces `legs[]` into the response; **MUST NOT** be
  made the default.
- **Fusion is `RRFScorer` over the per-leg ranked lists** (§4.3). `rrf_k` comes from G1's result, not
  from a second constant.
- **`filters` still MUST reject `library_id` and `tenant_id`** (§4). The list form makes this more
  important, not less: with fan-out there is now a plausible-looking wrong way to express scope.

**Rev 3's singular `library` MUST NOT ship.** It is not a compatibility break — `library` never
existed in `contracts/schemas/query_request.json`, so the singular form was spec text and nothing
more. There is no deprecation period and no dual-accept: a request carrying `library` is **400**, so
a client written against rev 3 fails loudly instead of silently querying one library and calling it
an answer.

**Zero-result rule.** MUST NOT call the LLM when retrieval returns zero chunks — today `generate(query, [])` runs with `"(no relevant passages found)"` (`llm.py:122`) and the answer depends on model compliance. **This widens the shipped contract**, so it moves to §10 fix-first: `query_response.json` `answer` becomes `["string","null"]`, and `reason` (`library_empty|library_indexing|no_match`), `library_state`, `indexed_documents`, `total_documents` are **always present, nullable outside library queries**. Go must match or `test_schema_validation` fails.

### §9a Limits

Per library ≤1000 documents, ≤20 GB — enforced at stage 0 as a **job failure**, never a silent truncate. Per user 1 concurrent run, ≤20 libraries. `top_k` **max 100**, `rerank_candidates` **max 500** (both are `ge=1` with no ceiling today). **`libraries` ≤ `LIBRARY_FUSION_MAX_LEGS` (default 8) after expansion → 400** (§9.2); note 8 < the 20-library ceiling, which is §4.3's recorded tension. Rate limit → 429 (#87). `tenant_max_concurrency` MUST be non-zero in the BV-BRC deployment; the shipping default of 0 is a dev default and a production misconfiguration here — **and per §8.2 item 1 it is enforced per process, so with N workers the real ceiling is N× whatever is configured. Set it knowing that, or fix item 1 first.**

**Free-space precondition (§6.2):** stage 0 MUST refuse a submit that cannot fit `20 GB × concurrent runs` on the worker volume. GoWe reclaims nothing, so this and the cron reaper are the only backstops.

---

## §10. Fix-first — ships and is verified before any library code

1. **#198** — one shared three-store teardown (vector + text + **graph**) with a collection parameter.
2. **#196** — empty-list fail-open in **Qdrant, ES *and* `stores/memory.py:24-25`**. Memory matters because §14 runs on it — otherwise the fail-closed assertions pass against an impl with no guard, the exact "green while proving nothing" failure §14 exists to avoid. `library_id`/`tenant_id` fail closed by **raising**, not via `_build_filter`.
3. **#197 `get_chunks`** — derive ids over `chunk_ids × tenants × {library_id}` (2-part key when `library_id` is absent), then **post-filter payloads** on both keys. Redundant on Qdrant, **load-bearing on memory** (`memory.py:103-117`). Thread `library` through the endpoint, `fetchChunks` (`client.ts:362`) and Compare (`CompareView.tsx:401`).
4. **#130** — resolved by §9's runs endpoint.
5. **#195** — `INGEST_ROOT` unset.
6. **Graph leg** — `_graph_context` pseudo-chunks carry **no metadata at all** (`retriever.py:92-98`) and reach the LLM prompt. **`use_graph` forced `false` for library queries in v1**; `/v1/graph/*` → 404 for library principals.
7. **`_final_status`** (`api/routers/documents.py`) — currently `FAILED` only if `total>0 and completed==0`, so **zero items reads `completed`**. Replace with an **ordered** rule (rev 2's version was ill-formed — overlapping predicates): `if total==0: empty; elif completed==total: completed; elif completed>0 or failed>0: partial; else: failed`. `partial`/`empty` are **new wire values**, and the contract does not constrain the vocabulary at all today: `contracts/schemas/ingest_response.json`'s `status` is a bare `"type": "string"` with **no enum** (rev 3 cited a `DocumentInfo.status` enum in `openapi.yaml`; there is none). So this item means *adding* the enum as well as updating Go, fixtures, and `python/tests/unit/test_final_status.py` — whose `_counts(completed=2, failed=1) == COMPLETED` and `_counts() == COMPLETED` assertions encode the old behaviour and will fail.
8. **`query_response.json` widening** (above), **plus `source.json`** — §4.3's per-source attribution needs a `library_id` field and `source.json` is `additionalProperties: false`, so attribution is a contract change, not a metadata convention. See §9.
9. **`GET /v1/documents`** bypasses `scope_filters` (`api/routers/documents.py`) → 404 for library principals until it takes a library scope. Same for `count_tenants`/`list_documents`, which take no filter dict.

---

## §11. Open questions — BLOCKING, for BV-BRC

Rev 4 renumbers. **Four of rev 3's eight are closed** and their answers now live in the sections that
depend on them, per the rule that a settled question belongs next to the design it settles:

| Rev 3 | Closed by | Answer now lives in |
|---|---|---|
| Q2 — upload analogue to `get_download_url`? | **Yes: `Workspace.create` with `createUploadNodes`, mediating a Shock node.** Declared in GoWe (`WorkspaceCreateInput.CreateUploadNodes`) with **zero callers** in the staging path. | **§6.1**, as a named implementation gap plus the streaming alternative. |
| Q4 — will users accept one top-level workspace per shareable library? | **No — the operator calls it "barely acceptable".** Resolved by design rather than by asking: two layers, Workspace ACL for files, `library_shares` for library access. | **§2.1**, including what the resolution does *not* buy. |
| Q6 — which endpoint introspects a token? | **Moot.** §5.0 tier 1 verifies offline against a pinned issuer allowlist, so no introspection endpoint is required to ship. The "probe subsumes validation" sub-question is **rejected on layering grounds**, not deferred. | **§5.0** and **§-1 G4**. |
| Q8 — is there a "workspaces shared with me" listing? | **Moot.** §2.2 defines a `ShareResolver` interface that can be backed by our own table *or* the BV-BRC API; v1 uses `library_shares` with an index on `grantee`, so the O(all libraries) scan and its global 503 are deleted. | **§2.2**, **§8.1**, **§9**'s `GET /v1/libraries`. |

**Still open, renumbered:**

1. **Firewall only, not architecture.** (a) Can BV-BRC's app service / chatbot reach coconut's
   GoWe `:8091` to submit? It already binds all interfaces (`ss -ltnp` shows `*:8091`) and uses
   BV-BRC token auth with anonymous disabled, so it is built for external callers — the open part
   is the network path (NAT / VPN / allowlist). **Note this cuts both ways after §-1 G4:** GoWe
   verifies no signature, so a wider network path is a wider forgery surface, and the answer to (a)
   should not be "open it up" before that is fixed. (b) Can GoWe workers on coconut reach
   bv-brc.org's Workspace and Shock outbound? **Narrowed, not closed:** four of five live workers run
   with `--workspace-stager` registered, which shows the capability is *deployed*, not that a
   Workspace call from coconut *succeeds*. One `WorkspaceLs` against a real root settles it.
2. **Chatbot server-to-server or browser?** Determines delegation vs CORS/CSRF; if browser, a bearer
   token sits behind an XSS boundary (the UI persists keys in `localStorage`, `web/src/config.ts`).
   **Raised in priority by §5.0's audience gap:** with no `aud` claim, a token leaked from a browser
   is a token that works at GoWe and the Workspace, not only at RAGStack.
3. **Workspace rate limits for a service; does `create` batch?** Gates §6 stage 6. **Narrowed by
   §6.2's storage model** — intermediates never touch the Workspace, so the per-run write volume is
   one `.ragstack/` publication, not one per stage. The remaining exposure is stage 6's per-document
   `text/{doc_id}.txt` and `index/{doc_id}.json`, i.e. 2×N writes for an N-document library. If
   `create` does not batch, that is 2,000 calls for a 1,000-document library and stage 6 needs a
   different artifact layout (one packed file, not N).
4. **Is `ObjectID` genuinely stable across move?** §3.1 rests on it. **Downgraded from blocking:**
   §3.1 rule 3 records `content_sha256` alongside `ws_object_id` on every document regardless of
   which key fed the `doc_id`, so a negative answer is a *migration* (re-key affected rows from the
   recorded hash) rather than a redesign. Still worth an authoritative answer before §15 step 7.
5. **Will BV-BRC run an authorization server?** New in rev 4, and the only one that closes §5.0's
   audience gap. Specifically: an OAuth 2.0 / OIDC issuer emitting audience-scoped, introspectable
   tokens. §5.0 tier 3 is designed to consume it with no change above `IdentityProvider`, so a "yes,
   eventually" costs nothing now — but a "no, ever" means the gap is permanent and should be recorded
   as an accepted risk in the deployment sign-off, not carried as an open item.

---

## §12. Non-goals (v1)

OCR (the *stage*; §6.3's seam IS in scope) · **~~multi-library search~~ — NO LONGER A NON-GOAL: §4.3/§9.2 make server-side fusion a v1 requirement** · sharing UI in RAGStack (the §9.1 *endpoints* are in scope; the UI is not) · per-library chunk-spec choice · per-document delete · automatic index placement · RAGStack-fronted upload (if ever built it writes **through** the Workspace API with the user's token — an object outside the Workspace hierarchy has no `FullObjectPath` and cannot be authorized) · full OTEL (#89/#114).

---

## §13. Rollback

`LIBRARIES_ENABLED`, **default off** ⇒ routes unmounted, `libraries` → 400. No public-corpus path behaves differently, because no public chunk is written, migrated or re-keyed. Rollback = flag off, optionally drop `ragstack_lib_v1`.

**Exceptions that change shipped behaviour and each ship independently:** §10 items 1–3, 6–9, and `resolve_profile` raising (a typo'd profile that silently works today becomes a hard failure).

Every library request logs `{token_id_hash, tenant, library_id, run_id, endpoint, stage, duration_ms, outcome}`; `library_id` and `run_id` on every ingest and query log line. **Never the credential**, and never the `un=` value of an unverified one (§5.0). A fused query logs one line per **request** carrying `legs` (the expanded library ids) and `leg_count`, plus one line per **leg** carrying that leg's `library_id`, `retrieved` and `authz_outcome` — without the per-leg lines a 503 from an eight-leg fan-out names no culprit.

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

**Rev 4 additions.** Each of these tests a decision that is otherwise only asserted:

- **§2.1's "necessary but not sufficient".** Grant `reader@test` a `library_shares` row on **lib-b**,
  whose `acl.json` entry gives them nothing. `GET /v1/libraries` MUST still omit lib-b, and
  `POST /v1/query {"libraries":["<lib-b>"]}` MUST still 403. This is the single most important new
  assertion in the suite: it is what proves the share table cannot widen access. A green suite
  without it means §2.1 shipped as prose.
- **Share revocation has no TTL.** Grant, query (200), `DELETE .../shares/{grantee}`, query again —
  MUST be 403 on the *next* request, with no sleep.
- **§9.2 fan-out.** Two-library fusion returns `sources` drawn from both, every source carries a
  non-null `library_id` matching one of the requested legs, and `legs[]` has one entry per leg with
  `contributed ≤ retrieved`. Then: `libraries: []` → 400; `libraries` with a bad element → 400;
  `libraries` + `collection` → 400; an explicitly-named unreadable library → **403**; `@me` expansion
  past `LIBRARY_FUSION_MAX_LEGS` → **400**; a leg forced `UNAVAILABLE` via `__unavailable__` →
  **503 for the whole request, and MUST NOT return the surviving leg's results**. That last one is
  the fail-open regression test.
- **§3.1 update-vs-insert.** Rewrite `doc1.pdf` in place (LocalFs keeps `st_dev:st_ino`, so
  `blob_meta.id` is unchanged while the content hash changes), re-index, and assert the `doc_id` is
  **the same**, the document count did **not** grow, and the old chunks are gone. Then rename it and
  assert the same three things. A filename+hash composite fails both halves, which is the point.
- **§5.0 tier 1.** Absent `expiry` → **401** (not "never expires"); unknown issuer → **401** with no
  outbound fetch attempted; a credential whose signature does not verify → **401** and the identity
  in `un=` MUST NOT appear in any log line or `Principal`.
- **§8.2 item 3**, on the infra target only: run the API with **`--workers 2`**, `POST /v1/collections`,
  then poll until the same id resolves through a different worker. Without this, every claim in §8.2
  is untested — and it is a one-line change to `run_libraries_infra.sh`.

Unit: legacy point-id/`_es_id` pin (Qdrant, ES) and the **identity-tuple widening** (memory — it has no UUID to pin). Plus `Identity.scopes` defaults empty and no authorization path reads it (§5.0).

**Infra-backed target** (`run_libraries_infra.sh`, not the memory suite): returned-hits == `min(k, |library|)` — a Qdrant segment-truncation property `InMemoryVectorStore` cannot exhibit. The `--workers 2` run belongs here too.

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

**Enforcement caveat — read this before believing the ACL claim.** Today `_effective_collection` (`api/routers/query.py`) returns the caller's `collection` unchanged whenever `allowed_collection_ids` yields `None`, which `tenancy.py`'s `allowed_collection_ids` does for an empty mapping — and `tenant_collections` is `{}` by default and **commented out in prod** (`unified.env:64`). ASM chunks are stamped `public`, which `readable_tenants` hands to everyone. So a caller can send `POST /v1/query {"collection":"asm-tok512"}` and read the corpus without touching a library row.

**Therefore Tier 1 enforces nothing unless v1 also ships:** (a) `allowed_collection_ids` **default-deny for library principals** (an unlisted tenant gets `[]`, not `None`), and (b) a whole_index authz check on the `collection=` path, not only the `libraries` path (§9.2). **Both are in scope for v1**; without them §16's "public becomes an ACL state, not a separate code path" is aspirational, and the spec MUST NOT claim otherwise.

### Tier 2 — subdivide an existing corpus (only if actually needed)

Only if one collection must become several independently-shared libraries. Payload backfill, **still no re-embedding**: create the `library_id` index on that collection first (a background op at 24.8M points — and note §4 forbids doing this automatically at startup), `set_payload` over a selecting filter, verify subset and total counts, rollback via `delete_payload`. Cost is real (`on_disk_payload: true` ⇒ one payload write per point plus an index build, optimizer churn across ~99 segments): rehearse on a copy, schedule a window. **Tier 1 covers the actual requirement; Tier 2 is not routine.**

**Rejected: "absent `library_id` means public."** Needs `IsEmpty OR MatchAny`, which `_build_filter` cannot express, Qdrant cannot serve from a keyword index (an unindexed-field filter measured 8.9 s cold / 1.5 s warm vs 12 ms indexed), and which is exactly the multi-clause `should` shape G2 shows truncating.

### Tier 3 — build spec changes

Not a migration: content-addressing makes a different `(model, dim, chunk)` a different index by construction.

---

## §15. Build order

0. **G2** (§-1) — the only true gate left. G1 is a tuning experiment, run early. (G3, G4 resolved.)
1. **§10 fix-first 1–3, 6–9**, then #130/#195. Authorization bugs; nothing user-owned lands on top. Each ships independently (§13).
2. **§8.1** state store + `ensure_columns()` — including `library_shares` and `library_runs.gowe_submission_id`.
   **2a. §8.2 items 3 and 4** (`CollectionRegistry`, `persist_collection_spec`). Moved this early
   because they are **already broken across the three live API instances**, independently of libraries.
   Items 1, 2 and 5 are sequenced with their dependents (1 before §9a's quota claim means anything,
   2 before §6 sizing is trusted, 5 alongside step 9).
3. **§1 protocols** — `Principal` extension, `Identity` (with the reserved `scopes`), `IdentityProvider`, `BlobStore`, `AuthorizationProvider`, **`ShareResolver` (§2.2)** — plus `LocalFs`/`LocalAclAuthz`/`LocalShareResolver` and the §14 seam. Build against the fake; BV-BRC impls slot in behind the same interfaces last.
4. **`LIBRARIES_ENABLED`** flag + Go 501 stubs + the conformance skeleton.
5. **§5.0 tier 1 + tier 2** — offline verifier with the pinned issuer allowlist, then the generic `Oidc` provider as the proof the border is real. **No longer blocked on G4 or on any BV-BRC answer**, which is the main scheduling gain of the reversal: it needs a public key, not an endpoint. Still the long pole; start in parallel with 1–3.
6. Contract + read-only endpoints (register, admin-register, list, get, documents, **`GET`/`PUT`/`DELETE .../shares`**) and **`DELETE ?purge=false` only** — `purge=true` returns 501 until step 7, since it needs §3's library-aware deletes and §10.1. The conformance row for `purge=true` is written now and marked xfail until step 7 lands.
7. **§3/§3.1 id change + `ragstack_lib_v1` (with `max_segment_size` and `full_scan_threshold` pinned per §4.1). Atomic single commit.** Then `purge=true`.
   **7a. §16 Tier 0 verification gate — merge blocker.**
8. §4/§5.1 query scoping (`library_scope_filters`) + §16 Tier 1's two enforcement changes.
   **8a. §4.3/§9.2 fan-out and RRF fusion**, plus the `source.json` / `query_response.json` widenings.
   Strictly after 8 — fusion over an unscoped leg is worse than no fusion — and it is what makes the
   `libraries` list shippable rather than a one-element array.
9. §6 ingest workflow. **No longer gated** — GoWe on coconut is the execution plane.
   **9a. §6.2's cron reaper and the stage-0 free-space precondition ship WITH step 9, not after.**
   GoWe reclaims nothing and ~33 GB of orphaned scratch already exists; adding 20 GB per run to an
   unreaped volume is how step 9 takes the host down rather than how it fails a job.

   **No stage is a BV-BRC app.** Every stage is RAGStack code running as a CWL
   `CommandLineTool` on a GoWe worker; the only BV-BRC touchpoints are Workspace JSON-RPC
   calls (`ls`, `get_download_url`, `create`), which are service calls, not App Service jobs.
   Nothing goes through `start_app`, so there is one scheduler and no nesting. GoWe's `bvbrc`
   executor is therefore not on this path at all, and its not-conformance-validated status is
   irrelevant here. If a BV-BRC app is ever added as a *submission surface*, it MUST return a
   handle immediately and MUST NOT block on the GoWe run.

   **GoWe's API is asynchronous and the client already implements it:** `submit()` →
   `POST /api/v1/submissions` returns a submission id, and `get_submission(id)` →
   `GET /api/v1/submissions/{id}` polls it (`gowe_client.py:106,125`). `wait()` (`:128`) is a
   blocking convenience wrapper on top, nothing more.

   So the only defect here is client-side misuse, and the correct primitives already exist:
   `GoWeBackend.run_shards` calls `wait()` and then converts its timeout into **all items
   failed** (`gowe_backend.py:93-94`, default 7200 s at `config.py:229`), so a legitimately long
   run manufactures a false failure while a live run continues. **Fix: on the library path use
   `submit()`, persist the submission id on `library_runs`, and poll `get_submission()` from the
   §9 runs endpoint. Do not call `wait()`, and never turn a client-side timeout into item
   failures.** No new GoWe capability is required.
