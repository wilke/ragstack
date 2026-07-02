// Pure citation/DOI helpers. All inputs are untrusted ingested metadata, so the
// DOI is validated against a DOI shape and percent-encoded before it is ever put
// in an href — a `javascript:`/`data:` payload can't survive the fixed
// `https://doi.org/` origin + encoding.

import type { SourceMetadata } from "../api/client";

// DOI syntax: "10." + registrant digits + "/" + non-empty suffix. Strict enough
// that a `javascript:`/`data:` payload can never match (and it's encoded + placed
// after a fixed https origin anyway).
const DOI_RE = /^10\.\d+\/\S+$/;

export function isValidDoi(doi: unknown): doi is string {
  return typeof doi === "string" && DOI_RE.test(doi.trim());
}

/** Resolver URL for a valid DOI, else null (so the action can be disabled). */
export function doiUrl(doi: unknown): string | null {
  if (!isValidDoi(doi)) return null;
  return `https://doi.org/${encodeURIComponent(doi.trim())}`;
}

function authorsText(authors: SourceMetadata["authors"]): string {
  if (Array.isArray(authors)) return authors.filter(Boolean).join(", ");
  return typeof authors === "string" ? authors : "";
}

/** Plain-text citation from whatever metadata exists (all fields optional). */
export function formatCitation(m: SourceMetadata, fallbackTitle: string): string {
  const parts = [
    authorsText(m.authors),
    m.year != null && m.year !== "" ? `(${m.year})` : "",
    m.title ?? fallbackTitle,
    isValidDoi(m.doi) ? `https://doi.org/${m.doi.trim()}` : "",
  ].filter(Boolean);
  return parts.join(". ");
}

/** navigator.clipboard is only defined in a secure context (https/localhost). */
export function clipboardAvailable(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.clipboard &&
    (typeof window === "undefined" || window.isSecureContext)
  );
}

/** Copy text; resolves false instead of throwing when unavailable/denied. */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!clipboardAvailable()) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
