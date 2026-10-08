// Clipboard helpers for the admin bundle.
//
// The tenant UI's copies live in `src/lib/citation.ts`, which imports
// `api/client` — and through it the localStorage credential store this bundle
// must never pull in (`bundle.test.ts`). These are the same two functions,
// reimplemented with no imports at all.
//
// The only caller that copies a secret is RevealOnce, after an explicit click on
// a value that is already on screen. Nothing here reads the clipboard.

/** True when `navigator.clipboard` exists and the page is a secure context. */
export function clipboardAvailable(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.clipboard &&
    (typeof window === "undefined" || window.isSecureContext)
  );
}

/** Copy `text`; resolves false instead of throwing when unavailable or denied. */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!clipboardAvailable()) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
