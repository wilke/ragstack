// The retrieval-pipeline bar for one run. The API returns NO per-leg candidate
// counts (handoff "Backend gaps"), so segments are sized EQUALLY and carry only
// leg names — the mockup's counts, recall@50 and count-proportional widths are
// deliberately absent rather than fabricated. Colors follow the store tokens
// (vector = accent yellow, ES = sky).

import { HelpTip } from "../HelpTip";
import type { QueryOptions } from "../QueryOptionsMenu";

interface Leg {
  name: string;
  color: string; // bg utility per the store color tokens
}

// Which retrieval legs actually ran, per the mode the request was SENT with.
function legsFor(mode: QueryOptions["mode"]): Leg[] {
  const vector = { name: "VECTOR", color: "bg-accent" };
  const es = { name: "ES", color: "bg-sky" };
  if (mode === "vector") return [vector];
  if (mode === "bm25") return [es];
  return [vector, es];
}

export function PipelineStrip({
  options,
  kept,
  ms,
}: {
  options: QueryOptions; // levers snapshotted at submit (RunRecord.options)
  kept: number; // sources actually returned
  ms?: number; // round-trip wall clock, when recorded
}) {
  const legs = legsFor(options.mode);
  // RRF only happens when there is more than one leg to fuse; the cross-encoder
  // stage is claimed only when rerank was EXPLICITLY on — `null` means the
  // server default applied and we cannot know whether it ran.
  const fused = [
    legs.length > 1 ? "RRF" : null,
    options.rerank === "on" ? "CROSS-ENCODER" : null,
    `${kept} KEPT`,
  ]
    .filter(Boolean)
    .join(" → ");
  const caption = [ms != null ? `${(ms / 1000).toFixed(2)}s` : null, `rewrite ${options.rewrite}`]
    .filter(Boolean)
    .join(" · ");

  return (
    <div>
      <div className="mb-[5px] flex h-[26px] items-stretch gap-[2px]">
        {legs.map((leg, i) => (
          <div
            key={leg.name}
            className={`flex flex-1 items-center pl-2.5 font-mono text-[10px] font-medium text-ink-600 ${leg.color} ${
              i === 0 ? "rounded-l-[3px]" : ""
            }`}
          >
            {leg.name}
          </div>
        ))}
        <div className="flex flex-[2] items-center rounded-r-[3px] bg-white/[0.09] pl-2.5 font-mono text-[10px] font-medium text-[#9dbdda]">
          {fused}
        </div>
      </div>
      <div className="flex items-center gap-1.5 font-mono text-[10.5px] text-[#8fb3d4]">
        {caption}
        <HelpTip icon dark side="bottom" term="pipeline strip" />
      </div>
    </div>
  );
}
