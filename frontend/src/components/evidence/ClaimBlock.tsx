// One sentence of the answer with its citation chips. Per-claim grounding does
// not exist server-side yet (handoff "Backend gaps" fallback: claims render
// UNGRADED) — so the left rule and tint are NEUTRAL, there is no grounding
// readout, no color grading, and no "claim dropped" card. The chip's score is
// the SOURCE's retrieval score (which the API does return), never a grounding
// value. Chips select the cited source in the viewer.

import type { Claim } from "../../lib/claims";

export function ClaimBlock({
  claim,
  scores,
  onSelectSource,
}: {
  claim: Claim;
  scores: number[]; // retrieval score per 0-based source index
  onSelectSource: (index: number) => void; // 0-based source index
}) {
  return (
    <div className="border-l-2 border-white/25 bg-white/[0.06] px-[15px] py-[13px]">
      {/* Answer text is untrusted → rendered as React text. */}
      <p className="text-[14.5px] leading-[1.7] text-white">{claim.text}</p>
      {claim.cited.length > 0 ? (
        <div className="mt-[9px] flex flex-wrap items-center gap-1.5">
          {claim.cited.map((i) => (
            <button
              key={i}
              type="button"
              onClick={() => onSelectSource(i)}
              className="rounded-[3px] bg-white/[0.14] px-[7px] py-1 font-mono text-[10px] font-medium text-[#c7d8e8] hover:bg-white/25"
            >
              src {i + 1}
              {scores[i] != null ? ` · ${scores[i].toFixed(2)}` : ""}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
