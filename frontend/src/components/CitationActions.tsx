// Per-source citation actions: Copy DOI, Copy citation, Open at resolver. Every
// action is disabled when its backing metadata is absent/invalid. The resolver
// is a real <a> (Enter + middle-click work) with a validated, encoded DOI URL and
// rel="noopener noreferrer". Clipboard is feature-detected (secure-context only).

import { useState } from "react";
import type { SourceMetadata } from "../api/client";
import {
  clipboardAvailable,
  copyToClipboard,
  doiUrl,
  formatCitation,
  isValidDoi,
} from "../lib/citation";

export function CitationActions({
  metadata,
  fallbackTitle,
}: {
  metadata: SourceMetadata;
  fallbackTitle: string;
}) {
  const [status, setStatus] = useState("");
  const canCopy = clipboardAvailable();
  const doi = metadata.doi;
  const url = doiUrl(doi);

  const copy = async (text: string, label: string) => {
    const ok = await copyToClipboard(text);
    setStatus(ok ? `${label} copied` : "Copy unavailable");
    window.setTimeout(() => setStatus(""), 2000);
  };

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <button
        type="button"
        disabled={!canCopy || !isValidDoi(doi)}
        onClick={() => copy(String(doi), "DOI")}
        className="min-h-6 rounded border border-gray-300 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
        title={!canCopy ? "Clipboard needs a secure (https) context" : undefined}
      >
        Copy DOI
      </button>

      <button
        type="button"
        disabled={!canCopy}
        onClick={() => copy(formatCitation(metadata, fallbackTitle), "Citation")}
        className="min-h-6 rounded border border-gray-300 px-2 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
        title={!canCopy ? "Clipboard needs a secure (https) context" : undefined}
      >
        Copy citation
      </button>

      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="min-h-6 rounded border border-gray-300 px-2 py-1 text-blue-600 hover:bg-gray-50"
        >
          Open ↗
        </a>
      ) : (
        <span aria-disabled="true" className="min-h-6 rounded border border-gray-200 px-2 py-1 text-gray-300">
          Open ↗
        </span>
      )}

      <span aria-live="polite" className="text-gray-400">
        {status}
      </span>
    </div>
  );
}
