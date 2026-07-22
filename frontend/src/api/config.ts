// Runtime API target + admin key, persisted in localStorage so a dev can point
// the UI at any backend (and carry an admin key) without env vars or restarting
// Vite. The client (client.ts) reads getApiBase() at call time and prefixes
// every request.
//
// The presets are Vite-proxy PATH PREFIXES ("/be/<name>"), not absolute URLs, so
// switching stays same-origin and reaches the backend THROUGH the dev proxy (see
// vite.config.ts). That's what makes it work over an SSH/port forward, where only
// the Vite port is reachable — a bare http://localhost:8020 in the browser would
// hit the viewer's own machine. "" = the default proxy (VITE_API_TARGET).
//
// A Custom entry takes an absolute URL and calls the backend DIRECTLY (relies on
// the API's permissive CORS). Convenient for local dev, but it does NOT traverse
// a port forward — prefer a preset (or point VITE_API_TARGET at your backend).

export interface BackendPreset {
  id: string;
  label: string;
  url: string; // "/be/<name>" proxy prefix, or "" for the default proxy
}

export const BACKEND_PRESETS: BackendPreset[] = [
  { id: "proxy", label: "Default (Vite proxy)", url: "" },
  { id: "unified", label: "Unified explorer · :8020", url: "/be/unified" },
  { id: "asm", label: "asm (prod) · :8000", url: "/be/asm" },
  { id: "lucid", label: "lucid (prod) · :8010", url: "/be/lucid" },
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
