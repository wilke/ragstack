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
import { apiUrl } from "./config";

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
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Same-origin by default (dev: Vite proxies /v1 → the API; prod: the API serves
// the SPA). The API key is held in memory by the caller and passed per request —
// never persisted to localStorage (session/SSO replaces it in a later phase).
async function post<T>(path: string, body: unknown, apiKey?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail || res.statusText);
  }
  return (await res.json()) as T;
}

export function queryRag(req: QueryRequest, apiKey?: string): Promise<QueryResponse> {
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
  apiKey?: string,
): Promise<IngestResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  if (collection) form.append("collection", collection);
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(apiUrl("/v1/ingest/upload"), {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail || res.statusText);
  }
  return (await res.json()) as IngestResponse;
}

// Poll a single ingest job. An unknown job_id returns status "unknown" (HTTP 200).
export function getIngestJob(jobId: string, apiKey?: string): Promise<IngestResponse> {
  return get<IngestResponse>(`/v1/ingest/${encodeURIComponent(jobId)}`, apiKey);
}

// --- Ops dashboard read endpoints (#85) ---

async function get<T>(path: string, apiKey?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(apiUrl(path), { headers });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail || res.statusText);
  }
  return (await res.json()) as T;
}

// DELETE with no response body (204). Errors carry the raw body like get/post so
// the caller can unwrap FastAPI's `detail`.
async function del(path: string, apiKey?: string): Promise<void> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(apiUrl(path), { method: "DELETE", headers });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail || res.statusText);
  }
}

// DELETE that answers 200 + a body (the collection purge report). Same error
// handling as `del`; separate so the 204 callers keep a `Promise<void>`.
async function delJson<T>(path: string, apiKey?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(apiUrl(path), { method: "DELETE", headers });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail || res.statusText);
  }
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

export function getStoreStats(apiKey?: string): Promise<StoreStats> {
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

export function getTenants(apiKey?: string): Promise<TenantsInfo> {
  return get<TenantsInfo>("/v1/stats/tenants", apiKey);
}

export function getDeepHealth(apiKey?: string): Promise<DeepHealth> {
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

export function getModelsStatus(apiKey?: string): Promise<ModelsStatus> {
  return get<ModelsStatus>("/v1/stats/models", apiKey);
}

// Runs a small real workload on the serving fleet — call on demand, not on a poll.
export function runModelBenchmark(apiKey?: string): Promise<BenchmarkResult> {
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

export function getConfig(apiKey?: string): Promise<AppConfig> {
  return get<AppConfig>("/v1/config", apiKey);
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

export function getJobs(limit = 25, apiKey?: string): Promise<JobsResponse> {
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
  default: boolean;
  count?: number | null; // vector-store tenant-scoped count
  text_count?: number | null; // text-index (BM25) tenant-scoped count; compare with count for parity
  provenance?: Provenance | null; // verified lineage from the manifest
}

export interface CollectionsResponse {
  collections: CollectionInfo[];
  default: string;
}

export function getCollections(apiKey?: string): Promise<CollectionsResponse> {
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
  apiKey?: string,
): Promise<CollectionInfo> {
  return post<CollectionInfo>("/v1/collections", req, apiKey);
}

// Drop a collection's REGISTRY BINDING (admin-only, 204). The physical Qdrant
// collection and Elasticsearch index are deliberately NOT dropped — the server
// documents this, and callers must say so out loud before confirming.
// 409 = it's the default collection · 404 = unknown id.
export function deleteCollection(id: string, apiKey?: string): Promise<void> {
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
export function purgeCollection(id: string, apiKey?: string): Promise<CollectionPurgeReport> {
  return delJson<CollectionPurgeReport>(
    `/v1/collections/${encodeURIComponent(id)}?purge=true`,
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
export function getModelsRegistry(apiKey?: string): Promise<ModelsRegistryResponse> {
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
export function getAvailableModels(apiKey?: string): Promise<AvailableModelsResponse> {
  return get<AvailableModelsResponse>("/v1/models/available", apiKey);
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
  apiKey?: string,
): Promise<ChunksResponse> {
  const params = new URLSearchParams({ ids: ids.join(",") });
  if (collection) params.set("collection", collection);
  return get<ChunksResponse>(`/v1/chunks?${params.toString()}`, apiKey);
}
