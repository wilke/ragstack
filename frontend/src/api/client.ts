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
