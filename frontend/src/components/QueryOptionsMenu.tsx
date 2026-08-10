import { useRef } from "react";
import type { QueryRequest } from "../api/client";
import { lookupTerm } from "../lib/glossary";
import { HelpTip } from "./HelpTip";
import { useDismissable } from "./useDismiss";
import { rewriteStrategies, type Mode, type Rewrite } from "../lib/queryOptions";

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

// What the reranker will actually do for a request sent with these options —
// the value the closed-menu chips and the run rail display. Single source so
// the chip row can never disagree with the menu's select.
export function effectiveRerank(
  o: QueryOptions,
  serverRerank?: boolean | null,
): "on" | "off" {
  return o.rerank ?? ((serverRerank ?? CODE_DEFAULT_RERANK) ? "on" : "off");
}

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

// The lever labels ARE the help triggers (dotted underline, as they were when
// they carried title=""). A <label> may not wrap them: it would forward clicks
// on the trigger button to the control, so each row names its own select with
// aria-label instead.
const LEVER = "w-20 shrink-0 text-left text-[11px] font-medium";

// A lever's panel lists the glossary definition of every value in its select,
// so the hover copy cannot drift from lib/glossary (which the Compare glossary
// panel renders from the same source).
function LeverTip({ label, terms }: { label: string; terms: string[] }) {
  return (
    <HelpTip label={label} className={LEVER}>
      {terms.map((t) => (
        <span key={t} className="mb-1.5 block last:mb-0">
          <span className="font-medium">{t}</span> — {lookupTerm(t)}
        </span>
      ))}
    </HelpTip>
  );
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
  const wrap = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useDismissable(wrap);

  const rerankDefault = serverRerank ?? CODE_DEFAULT_RERANK;
  const rerankShown = effectiveRerank(value, serverRerank);
  const tags = optionTags(value, rerankDefault);
  const sel = "min-w-0 flex-1 rounded-panel border border-line px-2 py-1.5 text-sm text-strong";

  return (
    <div className="relative ml-1" ref={wrap}>
      {/* The trigger sits at the end of the config chip row, so it reads as a
          chip-row link (mono, link-blue) rather than a standalone button. The
          live lever values are on the chips beside it — no count badge. */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="true"
        className="font-mono text-[10.5px] text-link hover:underline"
      >
        Options <span aria-hidden="true">▾</span>
      </button>
      {!open && tags.length > 0 ? (
        <span className="sr-only">Active options: {tags.join(", ")}</span>
      ) : null}

      {open ? (
        <div className="absolute left-0 top-full z-10 mt-2 w-[330px] space-y-2.5 rounded-card border border-line bg-white p-4 shadow-popover">
          <div className="flex items-center gap-2">
            <LeverTip label="Query mode" terms={["hybrid", "vector", "bm25"]} />
            <select
              aria-label="Query mode"
              value={value.mode}
              onChange={(e) => onChange({ mode: e.target.value as Mode })}
              className={sel}
            >
              <option value="hybrid">hybrid (vector + ES)</option>
              <option value="vector">vector</option>
              <option value="bm25">es (bm25)</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <LeverTip label="Rewrite" terms={["none", "multiquery", "hyde"]} />
            <select
              aria-label="Rewrite"
              value={value.rewrite}
              onChange={(e) => onChange({ rewrite: e.target.value as Rewrite })}
              className={sel}
            >
              <option value="none">none</option>
              <option value="multiquery">multiquery</option>
              <option value="hyde">hyde</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <HelpTip label="Reranker" className={LEVER}>
              <span className="mb-1.5 block">{lookupTerm("cross-encoder")}</span>
              <span className="block">
                Preset to this server's configured default. /v1/config is admin-only, so
                without an admin key the menu falls back to the built-in default (off) —
                and until you change it, the request omits the field and the server
                applies its own setting either way.
              </span>
            </HelpTip>
            <select
              aria-label="Reranker"
              value={rerankShown}
              onChange={(e) => onChange({ rerank: e.target.value as "on" | "off" })}
              className={sel}
            >
              <option value="on">on</option>
              <option value="off">off</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <HelpTip term="top_k" label="Top k" className={LEVER} />
            <input
              type="number"
              aria-label="Top k"
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
          </div>

          <div className="flex justify-end border-t border-lineSoft pt-2">
            <button
              type="button"
              onClick={() => onChange({ ...DEFAULT_QUERY_OPTIONS })}
              disabled={tags.length === 0}
              className="text-xs text-dim hover:text-strong disabled:opacity-40"
            >
              Reset to defaults
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
