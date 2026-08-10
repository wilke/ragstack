// The 300px run rail: what the LAST run verifiably did, plus the jump-off
// points to Evidence/Compare and the session's recent questions. Only facts
// the client truly has go in the mono block (kept count, wall-clock, levers as
// sent) — the mockup's per-store candidate counts ("vector 260 · ES 141") and
// reranker model id need pipeline introspection /v1/query does not return
// (handoff README backend gap #5), so they are not invented here; render them
// once the response carries them.

import type { ReactNode } from "react";
import type { RunRecord } from "../../lib/run";
import { HelpTip } from "../HelpTip";
import { Eyebrow } from "./Eyebrow";

function RailButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-chip border border-line bg-white py-2.5 text-center text-xs font-medium text-ink-900 transition-colors hover:bg-accent-soft"
    >
      {children}
    </button>
  );
}

export function RunRail({
  run,
  serverRerank,
  recent,
  onPick,
  onOpenEvidence,
  onSendToCompare,
}: {
  run: RunRecord | null;
  serverRerank: boolean | null;
  recent: string[]; // newest first; [0] is the current question
  onPick: (q: string) => void; // re-fill the query input (no auto-submit)
  onOpenEvidence: () => void;
  onSendToCompare: () => void;
}) {
  // `rerank: null` means the run was sent without the field — the server's
  // default applied. Name it "default" unless /v1/config told us which way
  // that default points; guessing "off" here would misdescribe a past run.
  const rerank =
    run?.options.rerank ?? (serverRerank == null ? "default" : serverRerank ? "on" : "off");

  return (
    <aside aria-label="Run summary" className="rounded-card bg-paper px-[18px] py-5">
      <div className="mb-3 flex items-center gap-2">
        <Eyebrow>This run</Eyebrow>
        <HelpTip icon side="left" term="run">
          <span className="mb-1.5 block">
            The last completed query, described by what the client actually knows: kept =
            how many sources came back, beside the round-trip wall clock, then the levers
            the request was sent with.
          </span>
          <span className="block">
            rerank &ldquo;default&rdquo; means the request omitted the field, so the
            server&rsquo;s own setting applied and we can&rsquo;t say which way. Per-leg
            candidate counts (how many the vector and keyword legs each proposed) are not
            in the /v1/query response, so they are not shown.
          </span>
        </HelpTip>
      </div>
      {run ? (
        <>
          <div className="mb-[18px] font-mono text-[11.5px] leading-[1.85] text-[#6a6a64]">
            <div>
              kept {run.response.sources.length}
              {run.ms != null ? ` · ${(run.ms / 1000).toFixed(2)}s` : ""}
            </div>
            <div>mode {run.options.mode}</div>
            <div>rerank {rerank}</div>
            {run.options.rewrite !== "none" ? <div>rewrite {run.options.rewrite}</div> : null}
          </div>
          <div className="mb-[22px] flex flex-col gap-[9px]">
            <RailButton onClick={onOpenEvidence}>Open in Evidence →</RailButton>
            <RailButton onClick={onSendToCompare}>Send to Compare →</RailButton>
          </div>
        </>
      ) : (
        <p className="mb-[22px] font-mono text-[11.5px] leading-[1.85] text-dim">no run yet</p>
      )}

      <div className="mb-3 flex items-center gap-2">
        <Eyebrow>Recent questions</Eyebrow>
        <HelpTip icon side="left" term="recent questions">
          The questions asked in this browser session, newest first (the last five, kept
          in memory only — a reload clears them). Clicking one puts it back in the query
          box; it does not re-run it, so you can edit it or change the levers first.
        </HelpTip>
      </div>
      {recent.length === 0 ? (
        <p className="text-[12.5px] leading-[1.4] text-dim">Questions you ask appear here.</p>
      ) : (
        <div className="flex flex-col gap-2.5">
          {recent.map((q, i) => (
            <button
              key={`${i}-${q}`}
              type="button"
              onClick={() => onPick(q)}
              className={`border-l-2 pl-[9px] text-left text-[12.5px] leading-[1.4] text-[#4a4a44] transition-colors hover:text-ink-900 ${
                i === 0 ? "border-accent" : "border-line"
              }`}
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
