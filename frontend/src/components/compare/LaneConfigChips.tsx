import type { ReactNode } from "react";
import { LeverPopover } from "./LeverPopover";

// A lane's retrieval config, read-only: one grey "defaults" chip when the lane
// inherits the shared defaults, otherwise a yellow-bordered chip per overridden
// lever — plus the "edit ▾" chip whose popover (passed as children) carries the
// actual controls.
export function LaneConfigChips({ chips, children }: { chips: string[]; children: ReactNode }) {
  return (
    <div className="mb-3.5 flex flex-wrap items-center gap-[5px] font-mono text-[10px]">
      {chips.length === 0 ? (
        <span className="rounded-[9px] bg-[#f2f1ed] px-[9px] py-[5px] text-muted">defaults</span>
      ) : (
        chips.map((c) => (
          <span
            key={c}
            className="rounded-[9px] border border-accent bg-accent-soft px-[9px] py-[5px] text-accent-text"
          >
            {c}
          </span>
        ))
      )}
      <LeverPopover
        label="edit ▾"
        buttonClassName="rounded-[9px] border border-[#d8d7d2] px-[9px] py-[5px] font-mono text-[10px] text-link"
      >
        {children}
      </LeverPopover>
    </div>
  );
}
