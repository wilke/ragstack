import { HelpTip } from "../HelpTip";

// The headline agreement readout between the toolbar and the lanes: a stacked
// shared/partial/unique doc bar plus the summary stats. Purely presentational —
// CompareView computes everything from its doc_id + rank based agreement
// helpers (scores are never compared across lanes).

export interface AgreementBandStats {
  full: number; // docs retrieved by every answered lane
  partial: number; // by ≥2 lanes but not all
  unique: number; // by exactly one lane
  total: number; // distinct docs across all answered lanes
  laneCount: number;
  overlapPct: number | null; // mean pairwise Jaccard (chunk-level when comparable)
  uniques: { letter: string; count: number }[]; // non-zero unique-to-lane counts
  fastest: { letter: string; ms: number } | null;
}

export function AgreementBand({
  stats,
  glossaryOpen,
  onToggleGlossary,
  glossaryRegionId,
}: {
  stats: AgreementBandStats;
  glossaryOpen: boolean;
  onToggleGlossary: () => void;
  // The panel this button expands lives at the foot of the page, not next to it,
  // so the relationship has to be named rather than implied by position.
  glossaryRegionId?: string;
}) {
  const { full, partial, unique, total, laneCount, overlapPct, uniques, fastest } = stats;
  // End radii go on the first/last VISIBLE segment — zero-count segments are
  // filtered out, so hard-coding them on full/unique would square a bar end.
  const segments = [
    { n: full, cls: "bg-ink-900" },
    { n: partial, cls: "bg-sky" },
    { n: unique, cls: "bg-[#d8d7d2]" },
  ].filter((s) => s.n > 0);

  return (
    <div className="-mx-[34px] flex flex-wrap items-center gap-x-[26px] gap-y-2 border-y border-line bg-paper px-[34px] py-3.5">
      <div className="flex items-center gap-[9px]">
        <HelpTip
          label="Agreement"
          side="bottom"
          className="font-mono text-[10px] font-medium uppercase tracking-[.12em]"
        >
          The bar splits the documents the answered lanes retrieved into three:
          found by every lane, by some of them, by exactly one. Membership and
          ordering come from doc_id and rank — lane scores are never compared,
          because different models and fusions put them on different scales.
          &ldquo;overlap&rdquo; is the mean pairwise Jaccard, measured on chunk_ids
          when every lane queried the same collection and on doc_ids otherwise.
        </HelpTip>
        <div className="flex h-3.5 w-[150px] gap-[2px]" aria-hidden="true">
          {segments.map((s, i) => (
            <div
              key={i}
              className={`${s.cls}${i === 0 ? " rounded-l-[3px]" : ""}${
                i === segments.length - 1 ? " rounded-r-[3px]" : ""
              }`}
              style={{ flexGrow: s.n }}
            />
          ))}
        </div>
        <span className="font-mono text-[11px] text-[#6a6a64]">
          {full} of {total} docs in all lanes
        </span>
      </div>
      <div className="flex gap-5 font-mono text-[11px] text-[#6a6a64]">
        {overlapPct != null ? <span>overlap {overlapPct}%</span> : null}
        {uniques.map((u) => (
          <span key={u.letter}>
            unique to {u.letter} {u.count}
          </span>
        ))}
        {fastest ? (
          <span>
            fastest {fastest.letter} {(fastest.ms / 1000).toFixed(2)}s
          </span>
        ) : null}
        <span className="sr-only">{laneCount} lanes answered</span>
      </div>
      <button
        type="button"
        onClick={onToggleGlossary}
        aria-expanded={glossaryOpen}
        aria-controls={glossaryRegionId}
        className="ml-auto text-[11.5px] font-medium text-link"
      >
        What do these mean? {glossaryOpen ? "▴" : "▾"}
      </button>
    </div>
  );
}
