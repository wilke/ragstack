// Thin typed client for the RAGStack API.
//
// The hand-written types below cover the query surface used by the scaffold.
// Run `npm run gen:api` to generate `src/api/schema.d.ts` from
// contracts/openapi.yaml (the source of truth) and migrate these to the
// generated types as the UI grows.

// Scholarly chunk metadata. Every field is OPTIONAL — it exists only if the
// ingester stamped it — so the UI reads each defensively. This is a frontend
// convenience type; the contract keeps `metadata` an open object (source.json),
// so unknown keys are still allowed via the index signature.
//
// NOTE on offsets: `start_char`/`end_char` (when present) are offsets into the
// ORIGINAL DOCUMENT, not into `content` (the already-sliced passage), so they
// must NOT be used to slice `content`. Intra-passage highlighting waits on a
// backend `match_start`/`match_end` that is chunk-relative; until then the whole
// passage is framed as the match. See lib/highlight.ts.
import { apiUrl, getStoredCredential, getStoredToken } from "./config";
import {
  credentialHeaders,
  sendableCredential,
  type CredentialInput,
  type IdentityFailure,
} from "../lib/auth";

export interface SourceMetadata {
  title?: string;
  authors?: string | string[];
  year?: number | string;
  doi?: string;
  doc_type?: string;
  n_citations?: number;
  chunk_index?: number;
  prev_chunk_id?: string;
  next_chunk_id?: string;
  start_char?: number;
  end_char?: number;
  // Chunk-relative match span — not emitted by the API yet (follow-up backend
  // issue). When present, lib/highlight.ts marks it inside the passage.
  match_start?: number;
  match_end?: number;
  // Off-topic / below-threshold flag — not emitted by the API yet (handoff
  // README backend gap #4). When present, SourceCard shows the off-topic chip;
  // the client never infers it from scores.
  off_topic?: boolean;
  tenant_id?: string;
  [key: string]: unknown;
}

export interface Source {
  doc_id: string;
  chunk_id: string;
  content: string;
  score: number;
  metadata: SourceMetadata;
}

export interface QueryRequest {
  query: string;
  top_k?: number;
  rewrite_strategies?: string[];
  filters?: Record<string, unknown>;
  use_graph?: boolean;
  rerank?: boolean | null;
  collection?: string; // registry collection id; omit for the default
  retrieval_mode?: "hybrid" | "vector" | "bm25"; // which retrieval legs run; omit for hybrid
  llm?: string; // registered model id to generate with (this request only); omit for default
  reranker?: string; // registered model id to rerank with (this request only); omit for default
}

export interface QueryResponse {
  answer: string;
  sources: Source[];
  rewritten_queries: string[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    // The server's correlation id for the failed request (#427). Read from the
    // `X-Request-Id` response header, with the error body's `request_id` as a
    // fallback. Shown to the user as "Reference: <id>" so a bug report — or a
    // screenshot — is one `grep rid=<id>` away from the server's own log lines.
    public requestId?: string,
    // The machine-readable failure class on a store-unavailable 503:
    // "timeout" | "unreachable" | "error" (contracts/schemas/error.json). It is
    // the ONE thing that distinguishes "the search was too slow, a retry hits a
    // warm read" from "the backend is not answering, a retry will not help" —
    // which read identically to a user before this. Deliberately typed `string`
    // rather than a union: an unrecognised value from a newer server must
    // degrade to the conservative message, not fail to type-check.
    public reason?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The one place a failed `Response` becomes an `ApiError`. Five call sites
 * duplicated this and all five discarded the `Response` — so neither the
 * `X-Request-Id` header nor the body's `reason` reached the user, and a store
 * timeout and a dead backend produced the same sentence (#427).
 *
 * `message` stays EXACTLY what it was: the raw body text, or `statusText` when
 * the body cannot be read. `lib/auth.ts` renders sign-in sentences from it via
 * `apiFailure`, and `ErrorBanner` deliberately never shows it. The JSON parse
 * below is additive — it only ever populates `reason` and the `requestId`
 * fallback.
 *
 * NOTHING here may throw. This runs on the failure path, where a non-JSON body
 * (an nginx 502 page, a truncated response) is normal; a parse error escaping
 * would replace a useful `ApiError` with a `SyntaxError` and lose the status.
 * Hence `catch {}` around both the read and the parse.
 *
 * NOT used by `getApiVersion`, whose `!res.ok` returns `null` rather than
 * throwing — an absent version must read as "unknown", never as an error.
 */
async function throwForResponse(res: Response): Promise<never> {
  const detail = await res.text().catch(() => res.statusText);

  let bodyRequestId: string | undefined;
  let reason: string | undefined;
  try {
    const parsed = JSON.parse(detail) as unknown;
    if (parsed && typeof parsed === "object") {
      const obj = parsed as Record<string, unknown>;
      if (typeof obj.request_id === "string") bodyRequestId = obj.request_id;
      if (typeof obj.reason === "string") reason = obj.reason;
    }
  } catch {
    // Not JSON. Expected — see above.
  }

  // Header first: it is present on every response including the ones with no
  // body at all, and it is the value the server logged. The body field is the
  // same id, carried redundantly for copy-paste, so it is a pure fallback.
  const requestId = res.headers.get("x-request-id") || bodyRequestId || undefined;

  throw new ApiError(res.status, detail || res.statusText, requestId, reason);
}

/**
 * Normalize whatever a failed request threw into the {status, body} pair
 * lib/auth.ts renders a sentence from.
 *
 * A rejected `fetch` throws a TypeError with a browser-specific, unhelpful
 * message ("Failed to fetch", "NetworkError when attempting to fetch resource"),
 * and there is no status because no reply arrived — that is `status: null`, which
 * `signInMessage` words as "could not reach the API" rather than as a refusal.
 * The message of a non-ApiError is deliberately DROPPED: it is not a sentence for
 * a user, and it is not guaranteed free of the URL or credential.
 */
export function apiFailure(error: unknown): IdentityFailure {
  if (error instanceof ApiError) return { status: error.status, body: error.message };
  return { status: null, body: "" };
}

// The ONE place a credential becomes a header. Every request helper below calls
// it, so the exclusivity rule the server enforces (X-API-Key or Authorization,
// never both — a 400) is satisfied by construction, and an empty credential
// produces NO header rather than an empty one (an empty X-API-Key still counts
// as "present" server-side and would 400 a good bearer request).
//
// The credential VALUE is still passed in per call, never read from storage; the
// MODE and the token→base binding are read HERE, at call time, the way apiUrl()
// reads getApiBase() — and `sendableCredential` refuses to emit a header when
// the two disagree. That check is what makes the token→base binding real: a
// backend switch (or another tab's) changes storage while every already-created
// react-query closure still holds the old token, and without it those refetches
// would carry it to the new host. A call site may pass an explicit {mode, value}
// to pin the kind for one request — see CompareView's per-lane keys.
function authHeaders(apiKey?: CredentialInput): Record<string, string> {
  return credentialHeaders(
    sendableCredential(apiKey, getStoredCredential(), getStoredToken()),
  );
}

// Same-origin by default (dev: Vite proxies /v1 → the API; prod: the API serves
// the SPA). The credential is held by the caller (App.tsx owns it, config.ts
// persists it) and passed per request.
async function post<T>(path: string, body: unknown, apiKey?: CredentialInput): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authHeaders(apiKey),
  };
  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) await throwForResponse(res);
  return (await res.json()) as T;
}

export function queryRag(req: QueryRequest, apiKey?: CredentialInput): Promise<QueryResponse> {
  return post<QueryResponse>("/v1/query", req, apiKey);
}

// --- Ingest upload (multipart) + job polling (demo Collection view) ---

// Per-document progress for a batch/directory ingest. Present once the job has
// enumerated its documents; may be null for a job that hasn't started yet.
export interface IngestItemCounts {
  total?: number;
  completed?: number;
  failed?: number;
  pending?: number;
}

// Response shape shared by POST /v1/ingest/upload and GET /v1/ingest/{job_id}.
export interface IngestResponse {
  job_id: string;
  status: string; // accepted | running | completed | failed | unknown
  chunk_ids?: string[];
  items?: IngestItemCounts | null;
}

// A terminal ingest status: polling should stop once one of these is seen.
export function isTerminalIngestStatus(status: string): boolean {
  return status === "completed" || status === "failed" || status === "unknown";
}

// Multipart upload of one or more PDFs. Does NOT set Content-Type — the browser
// must add the multipart boundary itself. The tenant/collection default is
// derived server-side from the API key; `collection` overrides the target.
// 415 (non-PDF) / 413 (too large or too many) surface as ApiError with the code.
export async function uploadPdfs(
  files: File[],
  collection?: string,
  apiKey?: CredentialInput,
): Promise<IngestResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  if (collection) form.append("collection", collection);
  const headers = authHeaders(apiKey);
  const res = await fetch(apiUrl("/v1/ingest/upload"), {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) await throwForResponse(res);
  return (await res.json()) as IngestResponse;
}

// Poll a single ingest job. An unknown job_id returns status "unknown" (HTTP 200).
export function getIngestJob(jobId: string, apiKey?: CredentialInput): Promise<IngestResponse> {
  return get<IngestResponse>(`/v1/ingest/${encodeURIComponent(jobId)}`, apiKey);
}

// --- Ops dashboard read endpoints (#85) ---

async function get<T>(path: string, apiKey?: CredentialInput): Promise<T> {
  const headers = authHeaders(apiKey);
  const res = await fetch(apiUrl(path), { headers });
  if (!res.ok) await throwForResponse(res);
  return (await res.json()) as T;
}

// DELETE with no response body (204). Errors carry the raw body like get/post so
// the caller can unwrap FastAPI's `detail`.
async function del(path: string, apiKey?: CredentialInput): Promise<void> {
  const headers = authHeaders(apiKey);
  const res = await fetch(apiUrl(path), { method: "DELETE", headers });
  if (!res.ok) await throwForResponse(res);
}

// DELETE that answers 200 + a body (the collection purge report). Same error
// handling as `del`; separate so the 204 callers keep a `Promise<void>`.
async function delJson<T>(path: string, apiKey?: CredentialInput): Promise<T> {
  const headers = authHeaders(apiKey);
  const res = await fetch(apiUrl(path), { method: "DELETE", headers });
  if (!res.ok) await throwForResponse(res);
  return (await res.json()) as T;
}

export interface StoreStat {
  backend: string;
  available: boolean;
  count: number | null;
}

export interface StoreStats {
  tenants: string[];
  vector: StoreStat;
  text: StoreStat;
  graph: StoreStat;
}

export interface DeepCheck {
  name: string;
  ok: boolean;
  detail?: string | null;
  latency_ms?: number | null;
}

export interface DeepHealth {
  status: string;
  checks: DeepCheck[];
}

export function getStoreStats(apiKey?: CredentialInput): Promise<StoreStats> {
  return get<StoreStats>("/v1/stats/stores", apiKey);
}

// --- Tenancy: who we are, what we can reach, and where the data actually sits ---
// StoreStats collapses the readable tenants into one number per store; this splits
// that union into a tenant x collection grid.

export interface TenantCollectionCount {
  collection: string;
  label: string;
  vector_count?: number | null;
  text_count?: number | null;
}

export interface TenantRow {
  tenant: string;
  own: boolean; // our own tenant, vs the shared public corpus we may also read
  collections: TenantCollectionCount[];
}

export interface TenantsInfo {
  tenant: string;
  role: string;
  readable: string[];
  restricted_to?: string[] | null; // collection allowlist; null = unrestricted
  auth_enabled: boolean;
  policy?: Record<string, string[]> | null; // admin-only: full TENANT_COLLECTIONS map
  tenants: TenantRow[];
}

export function getTenants(apiKey?: CredentialInput): Promise<TenantsInfo> {
  return get<TenantsInfo>("/v1/stats/tenants", apiKey);
}

/**
 * The same call WITHOUT the count grid — the whoami path (App.tsx).
 *
 * `counts=false` returns the identity and reach fields with every
 * `vector_count`/`text_count` null and no store probed. It matters because this
 * runs on mount and on every credential change: counted, it is one probe per
 * tenant x collection x store, which on the production deployments is a
 * consistent ~5s (Qdrant's exact count times out and estimates) to learn three
 * fields. Anything that needs the numbers — the Ops tenants panel — calls
 * `getTenants` instead.
 */
export function getIdentity(apiKey?: CredentialInput): Promise<TenantsInfo> {
  return get<TenantsInfo>("/v1/stats/tenants?counts=false", apiKey);
}

export function getDeepHealth(apiKey?: CredentialInput): Promise<DeepHealth> {
  return get<DeepHealth>("/v1/health/deep", apiKey);
}

// --- Model status + throughput (#85, admin-only) ---

export interface EndpointStatus {
  url: string;
  reachable: boolean;
  latency_ms?: number | null;
  detail?: string | null;
  in_flight?: number | null; // live pool view (fan-out embedding only)
  pool_healthy?: boolean | null;
}

export interface ModelStatus {
  role: string; // "embedding" | "llm" | "reranker"
  model: string;
  backend?: string | null;
  dim?: number | null;
  endpoints: EndpointStatus[];
  reachable: boolean;
  note?: string | null; // "not configured" | "disabled" | null
}

export interface ModelsStatus {
  models: ModelStatus[];
}

export interface BenchResult {
  model: string;
  ok: boolean;
  seconds?: number | null;
  items?: number | null;
  items_per_sec?: number | null;
  tokens_per_sec?: number | null;
  detail?: string | null;
}

export interface BenchmarkResult {
  embedding: BenchResult;
  llm: BenchResult;
}

export function getModelsStatus(apiKey?: CredentialInput): Promise<ModelsStatus> {
  return get<ModelsStatus>("/v1/stats/models", apiKey);
}

// Runs a small real workload on the serving fleet — call on demand, not on a poll.
export function runModelBenchmark(apiKey?: CredentialInput): Promise<BenchmarkResult> {
  return post<BenchmarkResult>("/v1/stats/models/benchmark", {}, apiKey);
}

// --- Effective config (#95 config viewer, admin-only) ---

// Flat snapshot from GET /v1/config. Typed loosely (index signature) since the
// backend may add fields; the dashboard renders a curated subset in groups.
export interface AppConfig {
  vector_backend?: string;
  text_backend?: string;
  graph_backend?: string;
  job_store_backend?: string;
  qdrant_collection_explicit?: string | null;
  qdrant_collection?: string;
  elasticsearch_index?: string;
  embedding_api?: string;
  embedding_model?: string;
  embedding_model_dim?: number;
  embedding_endpoints?: string[];
  embedding_max_concurrency?: number;
  chunk_method?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  top_k?: number;
  rerank_enabled?: boolean;
  rerank_candidates?: number;
  reranker_model?: string;
  kg_extraction_enabled?: boolean;
  ingest_concurrency?: number;
  tenant_max_concurrency?: number;
  log_level?: string;
  [key: string]: unknown;
}

export function getConfig(apiKey?: CredentialInput): Promise<AppConfig> {
  return get<AppConfig>("/v1/config", apiKey);
}

// --- Server version ---

/**
 * The API's declared version. There is NO version endpoint: `/health` returns
 * only a status and `/v1/config` carries no version field, so the one honest
 * source is the OpenAPI document's `info.version` (FastAPI's `version=`).
 *
 * That document is ~80KB, so callers must cache it hard (staleTime Infinity) —
 * it changes only on redeploy. Unauthenticated, like the docs it describes, and
 * a deployment that does not serve it degrades to null rather than erroring:
 * an absent version must read as "unknown", never as a wrong number.
 */
export async function getApiVersion(): Promise<string | null> {
  try {
    const res = await fetch(apiUrl("/openapi.json"));
    if (!res.ok) return null;
    const doc = (await res.json()) as { info?: { version?: string } };
    return doc.info?.version ?? null;
  } catch {
    return null;
  }
}

// --- Ingest jobs (#95, admin-only) ---

export interface JobItemCounts {
  pending: number;
  completed: number;
  failed: number;
}

export interface JobSummary {
  job_id: string;
  status: string;
  source: string;
  error: string;
  chunks: number;
  items: JobItemCounts;
}

export interface JobsResponse {
  jobs: JobSummary[];
}

export function getJobs(limit = 25, apiKey?: CredentialInput): Promise<JobsResponse> {
  return get<JobsResponse>(`/v1/jobs?limit=${limit}`, apiKey);
}

// --- Collections registry (query-time selection) ---

export interface Provenance {
  collection?: string; // physical store name the manifest describes
  model?: string; // embedding model as *built* — compare against the registry label
  dim?: number | null;
  embedding_api?: string;
  chunk_method?: string | null;
  chunk_size?: number | null;
  chunk_overlap?: number | null;
  chunk_params?: Record<string, unknown>;
  spec_hash?: string;
  corpus?: string;
  chunk_count?: number | null;
  ingested_at?: string;
  ragstack_version?: string;
  source?: string; // "ingest" (verified) | "config" (declared from the registry spec)
}

export interface CollectionInfo {
  id: string;
  label: string;
  model: string;
  dim: number;
  chunk_method?: string | null;
  chunk_size?: number | null;
  // The GLOBAL registry-pointer flag: true on the entry DEFAULT_COLLECTION_ID
  // names. NOT this caller's target — that is `CollectionsResponse.default`
  // below. A caller who cannot read the pointer's target sees this false on
  // EVERY entry (zero true, not exactly one). Reading it as "the one I'm
  // querying" is the mis-read that produced #420; use it only for registry
  // facts, e.g. OpsDashboard's "cannot be unregistered or deleted".
  default: boolean;
  // The same flag under its canonical spelling (#276). Optional so older
  // servers stay conformant. Also GLOBAL — see the note on `default`.
  is_default?: boolean;
  count?: number | null; // vector-store tenant-scoped count
  text_count?: number | null; // text-index (BM25) tenant-scoped count; compare with count for parity
  provenance?: Provenance | null; // verified lineage from the manifest
}

export interface CollectionsResponse {
  collections: CollectionInfo[];
  // THIS CALLER's effective target: the id a request that omits `collection`
  // resolves to — the registry default when this caller can actually read it,
  // else their first readable collection ("" when they can read none). Read
  // THIS, never the per-item flag, to decide what a request will hit. Its one
  // consumer is lib/collectionTarget.ts; go through that, so the label and the
  // request body stay the same computation (#420).
  default: string;
}

export function getCollections(apiKey?: CredentialInput): Promise<CollectionsResponse> {
  return get<CollectionsResponse>("/v1/collections", apiKey);
}

// Create a new (empty) collection. Open to any authenticated principal
// (ADR-0003); the `embedding`/`chunk` build-spec overrides are ADMIN-ONLY —
// omit both (the common case) and the server resolves its default build spec
// into the collection. Returns the created CollectionInfo (201).
// 409 = id already exists · 404 = unknown embedding model · 400 = bad model/chunk
// · 403 = build-spec override without the admin role.
export interface ChunkConfig {
  method: string;
  size?: number | null;
  overlap?: number | null;
  params?: Record<string, unknown>;
}

export interface CollectionCreateRequest {
  embedding?: string; // id of a registered embedding model; omit → server default (admin-only to supply)
  chunk?: ChunkConfig; // omit → server default chunk strategy (admin-only to supply)
  id?: string; // explicit collection id; omit → content-addressed
  label?: string;
}

export function createCollection(
  req: CollectionCreateRequest,
  apiKey?: CredentialInput,
): Promise<CollectionInfo> {
  return post<CollectionInfo>("/v1/collections", req, apiKey);
}

// Drop a collection's REGISTRY BINDING (admin-only, 204). The physical Qdrant
// collection and Elasticsearch index are deliberately NOT dropped — the server
// documents this, and callers must say so out loud before confirming.
// 409 = it's the default collection · 404 = unknown id.
export function deleteCollection(id: string, apiKey?: CredentialInput): Promise<void> {
  return del(`/v1/collections/${encodeURIComponent(id)}`, apiKey);
}

// One target the purge could not remove. Its presence means the physical
// resource may STILL EXIST — the server does not roll back — so this is an
// operator to-do, not a warning to dismiss.
export interface PurgeFailure {
  target: string; // vectors | text_index | manifest
  error: string;
}

// What DELETE /v1/collections/{id}?purge=true actually destroyed. Three lists
// rather than a boolean because the purge touches four independent systems
// (registry binding, Qdrant, Elasticsearch, manifest file) that can each
// succeed, be already-gone, or fail on their own.
export interface CollectionPurgeReport {
  collection_id: string;
  purged: boolean;
  store: string; // physical Qdrant collection name
  text_index: string; // physical Elasticsearch index name
  deleted: string[]; // actually removed
  absent: string[]; // already gone (idempotent, not an error)
  failed: PurgeFailure[]; // errored, NOT rolled back
  ok: boolean; // failed is empty
}

// DESTROY a collection and its data (admin-only, 200 + report). Unlike
// deleteCollection this deletes the physical Qdrant collection, the ES index and
// the provenance manifest — the embeddings are gone and only a re-ingest brings
// them back. Never call this without a typed-confirmation gate in front of it.
// 409 = the default collection, or a physical store shared with other registry
// entries (the detail names them) · 404 = unknown id.
export function purgeCollection(id: string, apiKey?: CredentialInput): Promise<CollectionPurgeReport> {
  return delJson<CollectionPurgeReport>(
    `/v1/collections/${encodeURIComponent(id)}?purge=true`,
    apiKey,
  );
}

// --- Collection shares (issue #244) — grant / list / revoke, owner-or-admin ---

// One share row as surfaced by GET/POST /v1/collections/{id}/shares. Mirrors the
// server's ShareInfo and the ShareRecord contract schema. `active` is derived
// server-side from `revoked_at` (empty string == active); `revoked_by`/
// `revoked_at` are "" while the row is active. A user grantee's `grantee_id` is a
// resolved `issuer:subject` string; the built-in public group's is `public`.
export interface ShareRecord {
  id: string;
  collection_id: string;
  grantee_type: string; // "user" | "group"
  grantee_id: string; // resolved subject ("issuer:sub") or group id ("public")
  permission: string; // "read" in v1; "owner" on the surfaced owner row
  granted_by: string;
  granted_at: string;
  revoked_by: string; // "" while active
  revoked_at: string; // "" while active
  active: boolean;
}

export interface SharesResponse {
  shares: ShareRecord[];
  owner: string | null; // active owner subject, or null
}

// POST body. v1 is read-only: `permission` defaults to (and only accepts) "read".
// `grantee` is resolved server-side — the literal "@public"/"public" → the public
// group, a value containing ":" is kept verbatim as a full issuer:subject, and a
// bare username is prefixed to "<issuer>:<username>" (issuer defaults to "bvbrc").
export interface ShareGrantRequest {
  grantee: string;
  permission?: string;
  issuer?: string;
}

// List a collection's shares + its current owner (owner-or-admin; 403 for a
// readable non-owned collection, 404 for an unknown/unreadable one).
export function getShares(id: string, apiKey?: CredentialInput): Promise<SharesResponse> {
  return get<SharesResponse>(`/v1/collections/${encodeURIComponent(id)}/shares`, apiKey);
}

// Grant a share (owner-or-admin). Returns the resolved row (201) so a typo'd
// grantee is visible. 409 = duplicate/no-op-owner · 422 = empty grantee or
// non-read permission · 403/404 as above.
export function createShare(
  id: string,
  req: ShareGrantRequest,
  apiKey?: CredentialInput,
): Promise<ShareRecord> {
  return post<ShareRecord>(`/v1/collections/${encodeURIComponent(id)}/shares`, req, apiKey);
}

// Revoke a share (owner-or-admin, soft + cascading, 204). Un-publishing a public
// collection is revoking its `public` share. A share_id from another collection
// (or an unknown id) is a 404; the active owner row is not revocable here (409).
export function deleteShare(id: string, shareId: string, apiKey?: CredentialInput): Promise<void> {
  return del(
    `/v1/collections/${encodeURIComponent(id)}/shares/${encodeURIComponent(shareId)}`,
    apiKey,
  );
}

// --- Groups (issue #245) — RAGStack-native named bags of user subjects that a
// share can target via `@group:<id>`. Group create is open to any authenticated
// caller (they own what they create); view is owner-or-member (a non-member gets
// a leak-safe 404); manage (delete, add/remove members) is owner-or-admin. ---

// One group row. Mirrors the server's GroupInfo and the GroupRecord contract
// schema. `active` is derived server-side from `deleted_at` (empty string ==
// active); `deleted_by`/`deleted_at` are "" while the group is active. The
// built-in world-readable `public` group has id "public", `built_in` true and an
// empty `owner_subject` — but it is never returned by listGroups.
export interface GroupRecord {
  id: string;
  name: string;
  owner_subject: string; // "issuer:sub" of the creating user; "" for the public group
  built_in: boolean;
  created_at: string;
  deleted_by: string; // "" while active
  deleted_at: string; // "" while active
  active: boolean;
}

// One membership row. `subject` is always a resolved user subject ("issuer:sub"),
// never a group id (no nesting). `active` is derived from `removed_at`.
export interface GroupMemberRecord {
  id: string;
  group_id: string;
  subject: string;
  added_by: string;
  added_at: string;
  removed_by: string; // "" while active
  removed_at: string; // "" while active
  active: boolean;
}

export interface GroupsResponse {
  groups: GroupRecord[];
}

export interface GroupDetailResponse {
  group: GroupRecord;
  members: GroupMemberRecord[];
}

// POST /v1/groups body. `name` is non-empty, unique per owner among active
// groups, and may not be the reserved literal "public".
export interface GroupCreateRequest {
  name: string;
}

// POST /v1/groups/{id}/members body. `subject` is resolved server-side exactly
// like a share grantee — a value containing ":" is kept verbatim, a bare username
// is prefixed to "<issuer>:<username>" (issuer defaults to "bvbrc"). The resolved
// subject is echoed back so a typo is visible.
export interface GroupMemberAddRequest {
  subject: string;
  issuer?: string;
}

// The groups the caller owns or is an active member of (the built-in `public`
// group is not listed). Any authenticated caller may read this; 503 on a store
// outage (fail closed).
export function listGroups(apiKey?: CredentialInput): Promise<GroupsResponse> {
  return get<GroupsResponse>("/v1/groups", apiKey);
}

// Create a group owned by the caller (201). 409 = name collision for this owner
// (or the reserved "public") · 422 = empty/whitespace name · 503 = store outage.
export function createGroup(req: GroupCreateRequest, apiKey?: CredentialInput): Promise<GroupRecord> {
  return post<GroupRecord>("/v1/groups", req, apiKey);
}

// A group and its active membership (owner-or-member-or-admin). A non-member gets
// a leak-safe 404. 503 on a store outage.
export function getGroup(id: string, apiKey?: CredentialInput): Promise<GroupDetailResponse> {
  return get<GroupDetailResponse>(`/v1/groups/${encodeURIComponent(id)}`, apiKey);
}

// Soft-delete a group (owner-or-admin, 204). Shares granted to it become inert
// immediately. 409 = the built-in `public` group · 404 = unknown/unviewable · 403
// = a member who is not the owner · 503 = store outage.
export function deleteGroup(id: string, apiKey?: CredentialInput): Promise<void> {
  return del(`/v1/groups/${encodeURIComponent(id)}`, apiKey);
}

// Add a user to a group (owner-or-admin, 201). Returns the resolved membership row
// so a typo'd subject is visible. 409 = duplicate active membership (or the public
// group) · 422 = a group-target form (no nesting) · 404 = unknown group · 403 =
// non-owner member · 503 = store outage.
export function addGroupMember(
  id: string,
  req: GroupMemberAddRequest,
  apiKey?: CredentialInput,
): Promise<GroupMemberRecord> {
  return post<GroupMemberRecord>(`/v1/groups/${encodeURIComponent(id)}/members`, req, apiKey);
}

// Remove a member (owner-or-admin, 204). `subject` is the resolved subject as
// stored ("issuer:sub"). Removing a non-member is a 204 no-op. 404 = unknown group
// · 403 = non-owner member · 503 = store outage.
export function removeGroupMember(id: string, subject: string, apiKey?: CredentialInput): Promise<void> {
  return del(
    `/v1/groups/${encodeURIComponent(id)}/members/${encodeURIComponent(subject)}`,
    apiKey,
  );
}

// --- Model registry (admin-only) ---

// One registered model. Mirrors ragstack.api.model_registry.ModelEntry. Note
// `base_urls` IS returned by this admin endpoint (unlike /v1/models/available);
// the UI shows only how many, never the URLs.
export interface RegisteredModel {
  id: string;
  task: string; // embedding | tokenizer | llm | reranker
  provider: string; // sidecar | openai | vllm
  base_urls: string[];
  model: string;
  dim?: number | null;
  params?: Record<string, unknown>;
}

export interface ModelsRegistryResponse {
  models: RegisteredModel[];
  assignments: Record<string, string>;
}

// The full registry — the only place the *embedding* models are listed, and so
// the only source for "which embedder can a new collection bind to?". Admin-only:
// a non-admin caller gets 403, which the UI degrades to an advisory.
export function getModelsRegistry(apiKey?: CredentialInput): Promise<ModelsRegistryResponse> {
  return get<ModelsRegistryResponse>("/v1/admin/models/registry", apiKey);
}

// A registered model assignable per-request to a hot-swappable task (llm/reranker).
export interface AvailableModel {
  id: string;
  task: "llm" | "reranker";
  label: string;
  model: string;
  provider: string;
}

export interface AvailableModelsResponse {
  models: AvailableModel[];
}

// Models the Compare per-lane pickers can select (base_urls are not exposed).
export function getAvailableModels(apiKey?: CredentialInput): Promise<AvailableModelsResponse> {
  return get<AvailableModelsResponse>("/v1/models/available", apiKey);
}

// --- Knowledge graph (Evidence's "Entities in this answer") ---

// One KG entity as listed by GET /v1/graph/entities (tenant-scoped).
export interface EntityInfo {
  name: string;
  triple_count?: number;
}

// One neighbourhood triple from GET /v1/graph/neighbors/{entity}.
export interface Triple {
  subject: string;
  predicate: string;
  object: string;
}

// Entities in the knowledge graph. Errors (graph store disabled/unreachable)
// are the caller's cue to hide the KG section, not an error to surface.
export function getGraphEntities(limit = 100, apiKey?: CredentialInput): Promise<EntityInfo[]> {
  return get<EntityInfo[]>(`/v1/graph/entities?limit=${limit}`, apiKey);
}

// Neighbourhood triples for one entity, depth 1–5.
export function getGraphNeighbors(
  entity: string,
  depth = 1,
  apiKey?: CredentialInput,
): Promise<Triple[]> {
  return get<Triple[]>(`/v1/graph/neighbors/${encodeURIComponent(entity)}?depth=${depth}`, apiKey);
}

// A chunk fetched by id (no retrieval score) — used to expand a source's
// neighbouring context (its prev_chunk_id / next_chunk_id).
export interface ChunkOut {
  doc_id: string;
  chunk_id: string;
  content: string;
  metadata: SourceMetadata;
}

export interface ChunksResponse {
  chunks: ChunkOut[];
}

// Fetch chunks by id from a collection (tenant-scoped by the key). Missing/
// out-of-scope ids are omitted; order follows the request.
export function fetchChunks(
  ids: string[],
  collection?: string,
  apiKey?: CredentialInput,
): Promise<ChunksResponse> {
  const params = new URLSearchParams({ ids: ids.join(",") });
  if (collection) params.set("collection", collection);
  return get<ChunksResponse>(`/v1/chunks?${params.toString()}`, apiKey);
}
