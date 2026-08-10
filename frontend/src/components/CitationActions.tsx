// Per-source citation actions: Copy DOI, Copy citation, Open at resolver. Every
// action is disabled when its backing metadata is absent/invalid. The resolver
// is a real <a> (Enter + middle-click work) with a validated, encoded DOI URL and
// rel="noopener noreferrer". Clipboard is feature-detected (secure-context only).
// `trailing` lets the card append a right-aligned action (Evidence →) to the
// same row without a second flex container.

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { SourceMetadata } from "../api/client";
import {
  clipboardAvailable,
  copyToClipboard,
  doiUrl,
  formatCitation,
  isValidDoi,
} from "../lib/citation";

const ACTION =
  "min-h-6 rounded-[4px] border border-[#d8d7d2] px-2.5 py-1.5 text-[11px] hover:bg-paper";

export function CitationActions({
  metadata,
  fallbackTitle,
  trailing,
}: {
  metadata: SourceMetadata;
  fallbackTitle: string;
  trailing?: ReactNode;
}) {
  const [status, setStatus] = useState("");
  // Hold the "Copied" reset timer so it can be cancelled — otherwise it can fire
  // after the card unmounts (a new search re-renders the list) and set state on
  // an unmounted component.
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const canCopy = clipboardAvailable();
  const doi = metadata.doi;
  const url = doiUrl(doi);

  const copy = async (text: string, label: string) => {
    const ok = await copyToClipboard(text);
    setStatus(ok ? `${label} copied` : "Copy unavailable");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setStatus(""), 2000);
  };

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={!canCopy || !isValidDoi(doi)}
        onClick={() => copy(String(doi), "DOI")}
        className={`${ACTION} text-[#5b5b55] disabled:opacity-40`}
        title={!canCopy ? "Clipboard needs a secure (https) context" : undefined}
      >
        Copy DOI
      </button>

      <button
        type="button"
        disabled={!canCopy}
        onClick={() => copy(formatCitation(metadata, fallbackTitle), "Citation")}
        className={`${ACTION} text-[#5b5b55] disabled:opacity-40`}
        title={!canCopy ? "Clipboard needs a secure (https) context" : undefined}
      >
        Copy citation
      </button>

      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" className={`${ACTION} text-link`}>
          Open ↗
        </a>
      ) : (
        <span
          aria-disabled="true"
          className="min-h-6 rounded-[4px] border border-line px-2.5 py-1.5 text-[11px] text-faint"
        >
          Open ↗
        </span>
      )}

      <span aria-live="polite" className="text-[11px] text-faint">
        {status}
      </span>

      {trailing}
    </div>
  );
}
