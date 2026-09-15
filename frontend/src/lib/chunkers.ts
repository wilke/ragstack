// Chunk-strategy choices for the "New collection" flows (the `chunk` object on
// POST /v1/collections) plus the read-side helper that renders how an existing
// collection was built. Shared by the demo NewCollectionForm and the Ops admin
// panel via components/ChunkStrategyPicker.
//
// NAMING: these build *collections* (registry entry: model + dim + chunker -> an
// index), never "libraries" — a library is one-to-one with a collection, so
// "collection" is the only word. See docs/adr/0003-access-control.md.
//
// SOURCE OF TRUTH for the method list: python/ragstack/ingestion/chunkers.py ::
// CHUNK_METHODS. The API validates `chunk.method` against that tuple and returns
// 400 for anything else, so the list below must mirror it exactly. It is NOT
// served by any endpoint today, so it is mirrored here rather than fetched —
// python/tests/api/test_chunk_choice.py parses THIS file and fails if the
// two ever drift, so a chunker added (or removed) server-side cannot silently go
// missing from the picker.
export const CHUNK_METHODS = [
  "fixed",
  "fixed_token",
  "sentence",
  "words",
  "semantic",
  "semantic_pooled",
] as const;

export type ChunkMethod = (typeof CHUNK_METHODS)[number];

// Methods whose boundaries come from embedding-similarity breakpoints rather
// than a size budget. `size`/`overlap` are meaningless for these (the server
// derives boundaries from the text), so the form must NOT send them — a bogus
// size lands in the collection's manifest and misreports how it was built.
const SEMANTIC_METHODS: readonly string[] = ["semantic", "semantic_pooled"];

export function isSemanticMethod(method: string): boolean {
  return SEMANTIC_METHODS.includes(method);
}

export interface ChunkMethodInfo {
  label: string;
  blurb: string;
  // Unit that `size`/`overlap` are counted in; null → the method takes neither.
  unit: "characters" | "tokens" | null;
  // Short unit label for the compact one-line summary.
  short: string;
  // sentence/words accept -1 as "don't chunk — one chunk per document".
  allowsWholeDoc: boolean;
}

export const CHUNK_METHOD_INFO: Record<ChunkMethod, ChunkMethodInfo> = {
  fixed: {
    label: "Fixed (characters)",
    blurb: "Sliding character window. Cheapest, but cuts mid-sentence.",
    unit: "characters",
    short: "chars",
    allowsWholeDoc: false,
  },
  fixed_token: {
    label: "Fixed (tokens)",
    blurb:
      "Sliding window counted in the embedding model's own tokens, so no chunk overflows its context. The default.",
    unit: "tokens",
    short: "tok",
    allowsWholeDoc: false,
  },
  sentence: {
    label: "Sentence",
    blurb: "Packs whole sentences up to the size budget — never splits a sentence.",
    unit: "characters",
    short: "chars",
    allowsWholeDoc: true,
  },
  words: {
    label: "Words",
    blurb: "Packs whole words up to the size budget — never splits a word.",
    unit: "characters",
    short: "chars",
    allowsWholeDoc: true,
  },
  semantic: {
    label: "Semantic",
    blurb:
      "Splits at topic boundaries found by embedding overlapping sentence buffers. Slowest (it embeds twice) but the most coherent.",
    unit: null,
    short: "",
    allowsWholeDoc: false,
  },
  semantic_pooled: {
    label: "Semantic (pooled)",
    blurb:
      "Semantic boundaries, but each sentence is embedded once and mean-pooled — cheaper than `semantic` and reproducible across hosts.",
    unit: null,
    short: "",
    allowsWholeDoc: false,
  },
};

export function isChunkMethod(m: string): m is ChunkMethod {
  return (CHUNK_METHODS as readonly string[]).includes(m);
}

// --- Semantic tunables ------------------------------------------------------
// These ride in `chunk.params` (a free-form object in the contract). Left blank
// in the form they are omitted entirely and the server's configured defaults
// apply — the placeholders below show what those defaults are so "blank" is not
// a mystery. Keys/defaults track ragstack.config.Settings.chunk_buffer_size /
// chunk_breakpoint_percentile / chunk_min_length.
export interface SemanticParamSpec {
  key: "buffer_size" | "breakpoint_percentile_threshold" | "min_chunk_length";
  label: string;
  serverDefault: number;
  help: string;
  integer: boolean;
  min: number;
  max: number;
}

export const SEMANTIC_PARAMS: readonly SemanticParamSpec[] = [
  {
    key: "buffer_size",
    label: "Buffer size",
    serverDefault: 3,
    help: "Sentences of context taken on each side when scoring a boundary, so each comparison sees a (2\u00d7N+1)-sentence window. Higher = smoother, less jumpy boundaries.",
    integer: true,
    min: 1,
    max: 50,
  },
  {
    key: "breakpoint_percentile_threshold",
    label: "Breakpoint percentile",
    serverDefault: 80,
    help: "Split only where the gap between neighbouring sentences is unusually large. This is a percentile of the gaps *within each document*, so it self-scales. Higher = splits less often, so chunks are longer and there are fewer of them.",
    integer: false,
    min: 1,
    max: 100,
  },
  {
    key: "min_chunk_length",
    label: "Min chunk length",
    serverDefault: 500,
    help: "Characters (not tokens). A chunk shorter than this is merged into a neighbour rather than emitted, so no text is ever dropped.",
    integer: true,
    min: 0,
    max: 100000,
  },
];

// --- Form state -------------------------------------------------------------
// Everything is a string: these are raw <input> values, validated on submit so a
// half-typed number never becomes NaN in the request body.
export interface ChunkForm {
  method: ChunkMethod;
  size: string;
  overlap: string;
  params: Record<string, string>;
}

// The one-click common path stays exactly what the UI used to hardcode.
export const DEFAULT_CHUNK_FORM: ChunkForm = {
  method: "fixed_token",
  size: "512",
  overlap: "64",
  params: {},
};

function parseIntStrict(raw: string): number | null {
  const t = raw.trim();
  if (!/^-?\d+$/.test(t)) return null;
  return Number(t);
}

function parseNumStrict(raw: string): number | null {
  const t = raw.trim();
  if (t === "" || !/^-?\d+(\.\d+)?$/.test(t)) return null;
  return Number(t);
}

/**
 * Human-readable reason the form can't be submitted, or null when it's valid.
 *
 * The size/overlap rules are not cosmetic: a chunker whose overlap is >= its
 * size never advances (the sliding-window loop makes no progress), and a size of
 * 0 is the same trap, so both are refused here rather than becoming a stuck
 * ingest later.
 */
export function validateChunkForm(form: ChunkForm): string | null {
  if (!isChunkMethod(form.method)) return `Unknown chunk method "${form.method}".`;
  const info = CHUNK_METHOD_INFO[form.method];

  if (info.unit !== null) {
    const size = parseIntStrict(form.size);
    if (size === null) return "Chunk size must be a whole number.";
    if (size === -1 && !info.allowsWholeDoc)
      return `${info.label} does not support -1 (whole document) — give a size of 1 or more.`;
    if (size !== -1 && size < 1) return "Chunk size must be at least 1.";
    const overlap = parseIntStrict(form.overlap);
    if (overlap === null) return "Overlap must be a whole number.";
    if (overlap < 0) return "Overlap cannot be negative.";
    if (size !== -1 && overlap >= size)
      return "Overlap must be smaller than the chunk size, or chunking never advances.";
  } else {
    for (const spec of SEMANTIC_PARAMS) {
      const raw = (form.params[spec.key] ?? "").trim();
      if (raw === "") continue; // blank → omitted → server default
      const v = spec.integer ? parseIntStrict(raw) : parseNumStrict(raw);
      if (v === null)
        return `${spec.label} must be a ${spec.integer ? "whole number" : "number"}.`;
      if (v < spec.min || v > spec.max)
        return `${spec.label} must be between ${spec.min} and ${spec.max}.`;
    }
  }
  return null;
}

// Mirrors the `chunk` object in contracts/schemas/collection_create_request.json.
export interface ChunkConfigBody {
  method: string;
  size?: number;
  overlap?: number;
  params?: Record<string, number>;
}

/**
 * The `chunk` object to POST. Size/overlap are included ONLY for methods that
 * actually measure by them — a semantic collection sends neither, so its manifest
 * and provenance don't claim a window it never used. Blank semantic params are
 * omitted so the server's defaults apply (and are recorded) rather than a
 * client-side guess at them.
 *
 * Call `validateChunkForm` first; this assumes the form parses.
 */
export function buildChunkConfig(form: ChunkForm): ChunkConfigBody {
  const info = CHUNK_METHOD_INFO[form.method];
  if (info.unit !== null) {
    return {
      method: form.method,
      size: parseIntStrict(form.size) ?? undefined,
      overlap: parseIntStrict(form.overlap) ?? undefined,
    };
  }
  const params: Record<string, number> = {};
  for (const spec of SEMANTIC_PARAMS) {
    const raw = (form.params[spec.key] ?? "").trim();
    if (raw === "") continue;
    const v = spec.integer ? parseIntStrict(raw) : parseNumStrict(raw);
    if (v !== null) params[spec.key] = v;
  }
  const body: ChunkConfigBody = { method: form.method };
  if (Object.keys(params).length > 0) body.params = params;
  return body;
}

// --- Read side --------------------------------------------------------------

// The subset of CollectionInfo/Provenance this module reads. Structural so it
// accepts a CollectionInfo without importing the API client (keeping this module
// dependency-free and unit-testable).
export interface ChunkingSource {
  chunk_method?: string | null;
  chunk_size?: number | null;
  provenance?: {
    chunk_method?: string | null;
    chunk_size?: number | null;
    chunk_overlap?: number | null;
    chunk_params?: Record<string, unknown>;
  } | null;
}

/**
 * One-line "how was this collection built" summary, e.g. `fixed_token · 512/64 tok`
 * or `semantic · buffer 3`. Prefers the manifest (what an ingest actually
 * recorded) over the registry label (what an operator asserted). Empty string
 * when nothing is known, so callers can skip the row.
 */
export function describeChunking(c: ChunkingSource): string {
  const p = c.provenance ?? null;
  const method = p?.chunk_method ?? c.chunk_method ?? "";
  if (!method) return "";
  if (isSemanticMethod(method)) {
    const raw = p?.chunk_params ?? {};
    const bits = SEMANTIC_PARAMS.map((s) =>
      typeof raw[s.key] === "number" ? `${s.label.toLowerCase()} ${String(raw[s.key])}` : "",
    ).filter(Boolean);
    return bits.length > 0 ? `${method} · ${bits.join(", ")}` : method;
  }
  const size = p?.chunk_size ?? c.chunk_size ?? null;
  if (size == null) return method;
  const overlap = p?.chunk_overlap ?? null;
  const unit = isChunkMethod(method) ? CHUNK_METHOD_INFO[method].short : "";
  const span = overlap != null ? `${size}/${overlap}` : `${size}`;
  return `${method} · ${span}${unit ? ` ${unit}` : ""}`;
}

/**
 * The `detail` member of an error body, parsed — `undefined` when the response
 * was not JSON (an nginx HTML page, a bare "Internal Server Error") or carried
 * no `detail` at all.
 *
 * Split out of `apiDetail` because `detail` is deliberately UNTYPED in the
 * contract (contracts/schemas/error.json): a string nearly everywhere, an array
 * for FastAPI's own 422, and an OBJECT for the structured `owner_quota_exceeded`
 * 409. Callers that need the object's fields — the numbers, or the `error`
 * discriminator — must not each re-implement "is this body even JSON".
 */
function parsedDetail(raw: string): unknown {
  const text = (raw ?? "").trim();
  if (!text.startsWith("{") && !text.startsWith("[")) return undefined;
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return undefined;
  }
  return (body as { detail?: unknown })?.detail;
}

/**
 * The structured `detail` object, or null when this body's `detail` is a string,
 * an array, absent, or the body isn't JSON.
 *
 * This is how a caller branches on the server's own machine-readable error CODE
 * (`detail.error`) instead of pattern-matching its prose. Prose drifts — a
 * reworded sentence silently reroutes the UI — while the code is part of the
 * contract. Arrays are excluded on purpose: `typeof [] === "object"` in JS, and
 * a 422's validation list is emphatically not a structured error object.
 *
 * Note what actually keeps a 422 array out of `apiDetail`'s object branch: the
 * ARRAY BRANCH'S unconditional `return ""`, not the order of the branches. A
 * review proved it — moving the object branch above the array one
 * non-terminally changes nothing, because a JSON array has no own `message` or
 * `error` and falls through anyway. The `Array.isArray` guard HERE is separately
 * load-bearing and has its own test.
 */
export function apiDetailObject(raw: string): Record<string, unknown> | null {
  const detail = parsedDetail(raw);
  if (detail === null || typeof detail !== "object" || Array.isArray(detail)) return null;
  return detail as Record<string, unknown>;
}

/**
 * Pull the human-readable reason out of a FastAPI error body.
 *
 * The client surfaces the raw response text, which is `{"detail": "..."}` for an
 * HTTPException, a nested list of objects for a 422 validation error, and an
 * object with a `message` for the structured errors (today: the
 * `owner_quota_exceeded` 409). Return a single readable sentence, or "" when the
 * body isn't something worth showing a user (so the caller can fall back to a
 * generic message rather than dumping JSON on screen).
 *
 * The object branch is the #555 fix: this used to fall through to "" for an
 * object, so a 409 that explained itself perfectly well arrived at the UI as
 * "no detail" and every caller printed its generic fallback instead. Never
 * JSON.stringify the object as a consolation prize — that is how an internal
 * setting name or a raw store error ends up in front of an attendee.
 */
export function apiDetail(raw: string): string {
  const detail = parsedDetail(raw);
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (typeof (d as { msg?: unknown })?.msg === "string" ? (d as { msg: string }).msg : ""))
      .filter(Boolean);
    if (msgs.length > 0) return msgs.join("; ");
    return "";
  }
  if (detail !== null && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    const message = obj.message;
    if (typeof message === "string" && message.trim() !== "") return message.trim();
    // No `message`: the error CODE is the only readable thing left, and a
    // de-underscored code ("owner quota exceeded") is still a truthful summary,
    // where "" would make the caller claim the server said nothing. An object
    // with neither is the one case that really is unshowable — "" there is the
    // documented "fall back to your generic sentence", not a dropped message.
    const code = obj.error;
    if (typeof code === "string" && code.trim() !== "") return code.trim().replace(/_/g, " ");
  }
  return "";
}
