import { useEffect, useRef, useState } from "react";
import type { QueryRequest } from "../api/client";
import {
  OPTION_TIP,
  rewriteStrategies,
  type Mode,
  type Rewrite,
} from "../lib/queryOptions";

// Explore's "Options" popover: the query-pipeline levers (retrieval mode,
// rewrite strategy, rerank on/off, top_k) behind one button, so the console
// stays a search box by default. Same open/close mechanics as UserMenu
// (outside click + Escape), same lever semantics as Compare (lib/queryOptions).

export interface QueryOptions {
  mode: Mode;
  rewrite: Rewrite;
  // The reranker is a plain on/off — no "server default" choice. `null` means
  // the user hasn't touched it: the select DISPLAYS the server's configured
  // default (rerank_enabled from /v1/config, surfaced by the caller) and the
  // request omits `rerank` so the server applies that same default. Once
  // toggled, an explicit boolean is sent.
  rerank: "on" | "off" | null;
  topK: number;
}

export const DEFAULT_QUERY_OPTIONS: QueryOptions = {
  mode: "hybrid",
  rewrite: "none",
  rerank: null,
  topK: 5,
};

// Matches Compare's lane cap so the two views can't request different ranges.
const MAX_TOPK = 20;

// Fallback when /v1/config is unreadable (it is admin-only): the backend's
// compiled default is rerank_enabled=False (config.py), so display "off".
const CODE_DEFAULT_RERANK = false;

// The /v1/query fields these options become — spread into the request body.
export function queryOptionsRequest(
  o: QueryOptions,
): Pick<QueryRequest, "top_k" | "retrieval_mode" | "rerank" | "rewrite_strategies"> {
  return {
    top_k: o.topK,
    retrieval_mode: o.mode,
    ...(o.rerank != null ? { rerank: o.rerank === "on" } : {}),
    rewrite_strategies: rewriteStrategies(o.rewrite),
  };
}

// The non-default levers as short chips, so a tuned pipeline is visible while
// the menu is closed — otherwise "why are my results different?" has no answer
// on screen. Rerank counts as tuned only when it differs from the server's
// default; explicitly setting it to what the server does anyway is not a
// deviation worth badging.
function optionTags(o: QueryOptions, serverRerank: boolean): string[] {
  const t: string[] = [];
  if (o.mode !== "hybrid") t.push(o.mode === "bm25" ? "es" : o.mode);
  if (o.rewrite !== "none") t.push(o.rewrite);
  if (o.rerank != null && (o.rerank === "on") !== serverRerank) t.push(`rerank:${o.rerank}`);
  if (o.topK !== DEFAULT_QUERY_OPTIONS.topK) t.push(`k=${o.topK}`);
  return t;
}

export function QueryOptionsMenu({
  value,
  onChange,
  serverRerank,
}: {
  value: QueryOptions;
  onChange: (patch: Partial<QueryOptions>) => void;
  // rerank_enabled from GET /v1/config; null while unknown (still loading, or
  // the caller isn't an admin and got a 403).
  serverRerank?: boolean | null;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape — a menu that can only be closed by the
  // button that opened it is a trap on touch.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const rerankDefault = serverRerank ?? CODE_DEFAULT_RERANK;
  const rerankShown = value.rerank ?? (rerankDefault ? "on" : "off");
  const tags = optionTags(value, rerankDefault);
  const sel = "min-w-0 flex-1 rounded border border-gray-300 px-2 py-1 text-sm";

  return (
    <div className="relative ml-auto" ref={wrap}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="true"
        className="flex items-center gap-1.5 rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50"
      >
        Options
        {tags.length > 0 ? (
          <span className="rounded-full bg-blue-100 px-1.5 text-xs font-medium text-blue-700">
            {tags.length}
          </span>
        ) : null}
        <span aria-hidden="true" className="text-gray-400">
          ▾
        </span>
      </button>
      {!open && tags.length > 0 ? (
        <span className="sr-only">Active options: {tags.join(", ")}</span>
      ) : null}

      {open ? (
        <div className="absolute right-0 z-10 mt-1 w-72 space-y-2 rounded-md border border-gray-200 bg-white p-3 shadow-lg">
          <label className="flex items-center gap-2" title={OPTION_TIP.mode}>
            <span className="w-20 shrink-0 cursor-help text-xs font-medium text-gray-500 underline decoration-dotted underline-offset-2">
              Query mode
            </span>
            <select
              value={value.mode}
              onChange={(e) => onChange({ mode: e.target.value as Mode })}
              className={sel}
            >
              <option value="hybrid">hybrid (vector + ES)</option>
              <option value="vector">vector</option>
              <option value="bm25">es (bm25)</option>
            </select>
          </label>

          <label className="flex items-center gap-2" title={OPTION_TIP.rewrite}>
            <span className="w-20 shrink-0 cursor-help text-xs font-medium text-gray-500 underline decoration-dotted underline-offset-2">
              Rewrite
            </span>
            <select
              value={value.rewrite}
              onChange={(e) => onChange({ rewrite: e.target.value as Rewrite })}
              className={sel}
            >
              <option value="none">none</option>
              <option value="multiquery">multiquery</option>
              <option value="hyde">hyde</option>
            </select>
          </label>

          <label
            className="flex items-center gap-2"
            title="Cross-encoder re-scoring of the results. Preset to this server's configured default."
          >
            <span className="w-20 shrink-0 cursor-help text-xs font-medium text-gray-500 underline decoration-dotted underline-offset-2">
              Reranker
            </span>
            <select
              value={rerankShown}
              onChange={(e) => onChange({ rerank: e.target.value as "on" | "off" })}
              className={sel}
            >
              <option value="on">on</option>
              <option value="off">off</option>
            </select>
          </label>

          <label className="flex items-center gap-2" title={OPTION_TIP.top_k}>
            <span className="w-20 shrink-0 cursor-help text-xs font-medium text-gray-500 underline decoration-dotted underline-offset-2">
              Top k
            </span>
            <input
              type="number"
              min={1}
              max={MAX_TOPK}
              value={value.topK}
              onChange={(e) =>
                onChange({
                  topK: Math.max(1, Math.min(MAX_TOPK, Number(e.target.value) || 1)),
                })
              }
              className={`${sel} tabular-nums`}
            />
          </label>

          <div className="flex justify-end border-t border-gray-100 pt-2">
            <button
              type="button"
              onClick={() => onChange({ ...DEFAULT_QUERY_OPTIONS })}
              disabled={tags.length === 0}
              className="text-xs text-gray-500 hover:text-gray-800 disabled:opacity-40"
            >
              Reset to defaults
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
