// Thin typed client for the RAGStack API.
//
// The hand-written types below cover the query surface used by the scaffold.
// Run `npm run gen:api` to generate `src/api/schema.d.ts` from
// contracts/openapi.yaml (the source of truth) and migrate these to the
// generated types as the UI grows.

export interface Source {
  doc_id: string;
  chunk_id: string;
  content: string;
  score: number;
  metadata: Record<string, unknown>;
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
