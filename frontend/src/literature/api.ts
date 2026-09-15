// The two backends this demo talks to, and the one credential that opens both.
//
// RETRIEVAL is ragstack (`/v1/retrieve`), reached SAME-ORIGIN through the
// coconut gateway. GENERATION is BV-BRC's Copilot (`/chatbrc/chat-only`),
// cross-origin, which answers `Access-Control-Allow-Origin: *`.
//
// ONE TOKEN, SENT RAW. BV-BRC's own wire format has no `Bearer ` prefix
// (CopilotApi.js sends `Authorization: <token>`), and ragstack accepts the
// prefix as OPTIONAL — python/ragstack/api/security.py uses APIKeyHeader rather
// than HTTPBearer for exactly this reason and strips `Bearer ` when present. So
// the raw form is the one value both services accept; do not add a prefix for
// one of them.

// --- where the API is -------------------------------------------------------

// Baked in at build time (VITE_RAGSTACK_TENANT), overridable at runtime with
// `?tenant=` so one bundle can be repointed on stage without a rebuild. The
// value is a single path segment; anything else is ignored rather than
// interpolated, so a crafted link cannot aim the app (and the token it is
// about to send) at an arbitrary host.
const BUILD_TENANT = (import.meta.env.VITE_RAGSTACK_TENANT as string) || "dev";

export function tenant(): string {
  const q = new URLSearchParams(window.location.search).get("tenant");
  return q && /^[A-Za-z0-9_-]+$/.test(q) ? q : BUILD_TENANT;
}

/**
 * The ragstack API base — always a RELATIVE path.
 *
 * Under https://www.bv-brc.org/ragstack/litdemo/ this resolves same-origin to
 * the tenant's API. It must stay relative: the BV-BRC front proxy terminates
 * TLS and forwards to coconut:9000 over plain http, so an absolute
 * `http://coconut…:9000` here would be blocked as mixed content on the very
 * page we ship. In dev, vite.literature.config.ts proxies this same path to the
 * gateway, so there is no environment branch anywhere in the app.
 */
export function ragstackBase(): string {
  return `/ragstack/${tenant()}/api`;
}

const COPILOT_CHAT =
  (import.meta.env.VITE_COPILOT_CHAT as string) ||
  "https://www.bv-brc.org/services/copilot-api/copilot-api/chatbrc";
const COPILOT_DB =
  (import.meta.env.VITE_COPILOT_DB as string) ||
  "https://www.bv-brc.org/services/copilot-api/copilot-api/db";

// --- wire types -------------------------------------------------------------

/** A retrieved chunk. Mirrors contracts/schemas/source.json. */
export interface Source {
  doc_id: string;
  chunk_id: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown>;
  collection?: string;
}

export interface RetrieveRequest {
  query: string;
  top_k?: number;
  use_graph?: boolean;
  collection?: string;
  /**
   * Metadata equality constraints, ANDed. Per the contract these are matched BY
   * TYPE and never coerced: `year` is an integer field, so {"year": "2025"} is a
   * 400 rather than a silent zero-hit read (issue #471). Callers building this
   * from form text must convert numerics — see `numericFilter` below.
   */
  filters?: Record<string, string | number | boolean | string[] | number[]>;
  retrieval_mode?: "hybrid" | "vector" | "bm25";
  rerank?: boolean;
}

export interface RetrieveResponse {
  sources: Source[];
}

/** An error carrying the HTTP status, so callers can special-case 401. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(token: string): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  // Sent raw — see the note at the top of this file.
  if (token.trim()) h.Authorization = token.trim();
  return h;
}

async function postJson<T>(url: string, body: unknown, token: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body),
    });
  } catch (e) {
    // fetch rejects (rather than resolving non-2xx) for network/CORS/mixed
    // content — the failure modes that look identical in the console and are
    // the likeliest thing to go wrong on a stage.
    throw new ApiError(0, `Could not reach ${new URL(url, window.location.href).origin}: ${String(e)}`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, humanError(text) || res.statusText);
  }
  return (await res.json()) as T;
}

/**
 * A readable message out of an error body, instead of pasting raw JSON at the user.
 *
 * Handles both envelopes we actually see: RAGStack's FastAPI `{"detail": ...}`
 * and Copilot's `{"message": ..., "error": {"name": ...}}`. Falls back to the
 * raw text, truncated.
 */
function humanError(text: string): string {
  if (!text) return "";
  try {
    const j = JSON.parse(text) as Record<string, unknown>;
    if (typeof j.detail === "string") return j.detail;
    const err = j.error as Record<string, unknown> | undefined;
    const name = err && typeof err.name === "string" ? err.name : "";
    const msg = typeof j.message === "string" ? j.message : "";
    if (name && msg) return `${msg} (${name})`;
    return name || msg || text.slice(0, 300);
  } catch {
    return text.slice(0, 300);
  }
}

// --- ragstack ---------------------------------------------------------------

export async function retrieve(req: RetrieveRequest, token: string): Promise<RetrieveResponse> {
  return postJson<RetrieveResponse>(`${ragstackBase()}/v1/retrieve`, req, token);
}

/**
 * A registry collection, per contracts/schemas/collection_info.json.
 *
 * `label` is REQUIRED by the contract and is the human name ("OA JATS prototype
 * (dev) — mixed prose+table/figure units"). There is no `name` and no
 * `description` field; an earlier version of this interface invented both, so
 * the picker fell back to rendering raw ids.
 */
export interface CollectionInfo {
  id: string;
  label: string;
  model?: string;
  dim?: number;
  chunk_method?: string | null;
  chunk_size?: number | null;
  is_default?: boolean;
  /**
   * Lifecycle state. `active` serves reads; `dormant`/`restoring` answer 503 +
   * Retry-After; `lost` answers 409. Null/absent for a collection the registry
   * does not track (the settings-derived default), which is NOT an error.
   */
  state?: "active" | "archiving" | "dormant" | "restoring" | "lost" | null;
  /**
   * Tenant-scoped vector-store chunk count — and **null when unavailable**,
   * which is emphatically not the same as zero. Only `0` means empty; treating
   * null as empty would hide a perfectly good collection whose count the server
   * could not compute.
   */
  count?: number | null;
  text_count?: number | null;
}

/** The caller's readable collections. Needs auth, so a 401 here means the token is bad. */
export async function listCollections(token: string): Promise<CollectionInfo[]> {
  let res: Response;
  try {
    res = await fetch(`${ragstackBase()}/v1/collections`, { headers: authHeaders(token) });
  } catch (e) {
    throw new ApiError(0, String(e));
  }
  if (!res.ok) {
    // Same envelope handling as postJson — this path used to surface a bare
    // status text while every other call gave a readable message.
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, humanError(text) || res.statusText);
  }
  const body = (await res.json()) as { collections?: CollectionInfo[] };
  return body.collections ?? [];
}

/**
 * A server-side prompt template (ADR-0008), as GET /v1/prompt-templates reports
 * it. The `system`/`user` bodies are deliberately not exposed — a caller needs
 * to know which knobs exist, not what the server says to the model.
 */
export interface PromptTemplate {
  id: string;
  version: number;
  hash: string;
  label: string;
  output: "text" | "table";
  columns?: string[];
  slots: { name: string; required: boolean; max_len: number; label?: string }[];
  max_output_tokens?: number;
}

/**
 * The templates this tenant offers, or [] when it offers none.
 *
 * An EMPTY LIST and a 404 mean the same thing to this app — "generation cannot
 * be steered here" — and both are normal. A tenant on a build predating
 * ADR-0008 answers 404; one that simply has no templates configured answers 200
 * with an empty list. Either way the app falls back to the two-leg path, which
 * is why this resolves rather than throws.
 */
export class TemplatesUnavailable extends Error {}

/**
 * The templates this tenant offers, or [] when it offers none.
 *
 * VALIDATES the payload rather than trusting the cast. A 200 whose `templates`
 * is a map keyed by id, a string, or an array with a null entry used to flow
 * straight into a `useMemo` that runs DURING RENDER — and with no error boundary
 * above it, that is a blank page, not a fallback. A cast is a promise about a
 * value we did not produce; at a trust boundary it has to be checked.
 *
 * Distinguishes ABSENT from BROKEN, which the previous version collapsed:
 *   * 404 or an empty list — the capability is not there. Normal. Returns [].
 *   * anything else (5xx, unparseable, wrong shape) — it IS there and is
 *     misconfigured, e.g. a templates file the operator broke. Throws, so the
 *     caller can say so instead of silently degrading to the two-leg path and
 *     leaving nobody able to tell the two apart.
 */
export async function listPromptTemplates(token: string): Promise<PromptTemplate[]> {
  let res: Response;
  try {
    res = await fetch(`${ragstackBase()}/v1/prompt-templates`, { headers: authHeaders(token) });
  } catch (e) {
    throw new TemplatesUnavailable(`could not reach the template service: ${String(e)}`);
  }
  // 404 is the documented "this build predates the capability" answer.
  if (res.status === 404) return [];
  if (!res.ok) throw new TemplatesUnavailable(`template service returned HTTP ${res.status}`);

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new TemplatesUnavailable("template service returned a body that is not JSON");
  }
  const raw = (body as { templates?: unknown } | null)?.templates;
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw)) throw new TemplatesUnavailable("`templates` was not a list");
  // Drop entries that cannot be rendered rather than letting one bad row take
  // the page down; an id and a slot list are the minimum this app needs.
  return raw.filter(
    (t): t is PromptTemplate =>
      !!t && typeof t === "object" && typeof (t as PromptTemplate).id === "string" && Array.isArray((t as PromptTemplate).slots),
  );
}

export interface QueryRequest extends RetrieveRequest {
  template?: string;
  template_vars?: Record<string, string>;
}

export interface QueryResponse {
  answer: string;
  sources: Source[];
  rewritten_queries: string[];
  /** Present only on a templated request — absent, not null. */
  template?: string;
  template_version?: number;
  template_hash?: string;
  /** The model that actually generated, after the server resolves its default. */
  model?: string;
  /** True when the model hit its token ceiling mid-answer; absent otherwise. */
  truncated?: boolean;
}

/**
 * Retrieve AND generate in one call, with the prompt rendered server-side.
 *
 * The whole point of the template path: no second service, no cross-origin hop,
 * and the prompt is a named, versioned thing the server can attribute a result
 * to — rather than a string this browser assembled and nobody can replay.
 */
export async function query(req: QueryRequest, token: string): Promise<QueryResponse> {
  return postJson<QueryResponse>(`${ragstackBase()}/v1/query`, req, token);
}

// --- BV-BRC Copilot ---------------------------------------------------------

/**
 * Generate an answer. `system_prompt` is omitted when empty, matching
 * CopilotApi.submitQueryChatOnly — the service treats an absent prompt and an
 * empty one differently.
 */
export async function generate(
  prompt: string,
  model: string,
  token: string,
  userId: string,
  systemPrompt?: string,
): Promise<string> {
  const body: Record<string, unknown> = { query: prompt, model, user_id: userId };
  if (systemPrompt) body.system_prompt = systemPrompt;
  const r = await postJson<{ response?: unknown; message?: string }>(
    `${COPILOT_CHAT}/chat-only`,
    body,
    token,
  );
  const text = r.response;
  if (typeof text === "string") return text;
  // The service has been seen to return an object here; show it rather than
  // rendering "[object Object]".
  return text == null ? "" : JSON.stringify(text, null, 2);
}

export interface ModelInfo {
  model: string;
  label: string;
  isDefault: boolean;
}

const MODEL_LABELS: Record<string, string> = {
  "Llama-4-Scout-17B-16E-Instruct-quantized.w4a16": "Llama-4-Scout",
  "Llama-3.3-70B-Instruct": "Llama-3.3-70B",
};

/** Accepts the list as an array or as a JSON string, as CopilotApi does. */
function parseList(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value === "string") {
    try {
      const p = JSON.parse(value);
      return Array.isArray(p) ? p : [];
    } catch {
      return [];
    }
  }
  return [];
}

/**
 * The chat models, DEFAULT FIRST.
 *
 * The ordering is not cosmetic. The service advertises several models with
 * `active: true`, but they are not equally usable — as of 2026-09-14 the dev
 * list holds two Llama-4-Scout builds and only the `is_default` one answers;
 * the other returns 500 LLMServiceError on every call. Picking the first
 * element of the raw array to seed the form would therefore be luck, not a
 * choice, so the default is hoisted and the caller can seed from index 0
 * deterministically.
 *
 * The payload's `models` is a JSON STRING rather than an array (see parseList),
 * and the key has been seen as both `models` and `model_list`.
 */
export async function listModels(token: string): Promise<ModelInfo[]> {
  const r = await postJson<Record<string, unknown>>(
    `${COPILOT_DB}/get-model-list`,
    { project_id: "test" },
    token,
  );
  const raw = parseList(r.model_list ?? r.models);
  const models = raw
    .map((m) => {
      const rec = m as Record<string, unknown>;
      const full = typeof m === "string" ? m : (rec?.model ?? rec?.name);
      if (typeof full !== "string" || !full) return null;
      if (typeof m !== "string" && rec.active === false) return null;
      const short = full.split("/").pop() as string;
      return { model: full, label: MODEL_LABELS[short] ?? short, isDefault: rec?.is_default === true };
    })
    .filter((m): m is ModelInfo => m !== null);
  return [...models.filter((m) => m.isDefault), ...models.filter((m) => !m.isDefault)];
}
