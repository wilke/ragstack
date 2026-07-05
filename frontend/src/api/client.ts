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
  const res = await fetch(path, {
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

// --- Ops dashboard read endpoints (#85) ---

async function get<T>(path: string, apiKey?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(path, { headers });
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

export interface CollectionInfo {
  id: string;
  label: string;
  model: string;
  dim: number;
  chunk_method?: string | null;
  chunk_size?: number | null;
  default: boolean;
  count?: number | null; // tenant-scoped
}

export interface CollectionsResponse {
  collections: CollectionInfo[];
  default: string;
}

export function getCollections(apiKey?: string): Promise<CollectionsResponse> {
  return get<CollectionsResponse>("/v1/collections", apiKey);
}
