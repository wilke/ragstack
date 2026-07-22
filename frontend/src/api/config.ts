// Runtime API target + admin key, persisted in localStorage so a dev can point
// the UI at any backend (and carry an admin key) without env vars or restarting
// Vite. The client (client.ts) reads getApiBase() at call time and prefixes
// every request; "" means same-origin (the Vite dev proxy / prod SPA host).
//
// CORS: the API sets allow_origins=["*"], allow_headers/methods=["*"] by default,
// so cross-origin browser calls (including the X-API-Key preflight) succeed
// against any of these targets.

export interface BackendPreset {
  id: string;
  label: string;
  url: string; // "" = same-origin (Vite proxy)
}

// Known local targets. "Custom" (empty preset id) lets a dev type any URL.
export const BACKEND_PRESETS: BackendPreset[] = [
  { id: "proxy", label: "Default (Vite proxy)", url: "" },
  { id: "demo", label: "Phase 3 demo · :8020", url: "http://localhost:8020" },
  { id: "asm", label: "asm (prod) · :8000", url: "http://localhost:8000" },
  { id: "lucid", label: "lucid (prod) · :8010", url: "http://localhost:8010" },
];

const BASE_STORAGE_KEY = "ragstack.apiBase";
const KEY_STORAGE_KEY = "ragstack.apiKey";

function read(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return ""; // storage disabled (e.g. private mode) → in-memory defaults
  }
}

function write(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function getApiBase(): string {
  return read(BASE_STORAGE_KEY);
}

export function setApiBase(url: string): void {
  write(BASE_STORAGE_KEY, url.replace(/\/$/, ""));
}

export function getStoredApiKey(): string {
  return read(KEY_STORAGE_KEY);
}

export function setStoredApiKey(key: string): void {
  write(KEY_STORAGE_KEY, key);
}

// Resolve a request path against the selected backend. Relative ("") stays
// same-origin so the Vite proxy / prod host handles it unchanged.
export function apiUrl(path: string): string {
  const base = getApiBase();
  return base ? base + path : path;
}
