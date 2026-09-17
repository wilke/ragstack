// Presentational only: one retrieved source, rendered from `Source` (see
// ./api.ts). No fetch, no app state beyond the local expand/collapse toggle.
// Metadata is untrusted ingested JSON typed as `unknown` — every field is
// narrowed with `asText` before it touches JSX, so an object value is simply
// treated as absent rather than rendered (or worse, stringified badly).

import { useState, type ReactNode } from "react";
import type { Source } from "./api";

const PREVIEW_LIMIT = 400;

type ScoreTier = "high" | "medium" | "low";

// Tiers match the original widget: >= 8 high, >= 5 medium, < 5 low.
function scoreTier(score: number): ScoreTier {
  if (score >= 8) return "high";
  if (score >= 5) return "medium";
  return "low";
}

// Three restrained, visually distinct treatments built from the existing
// semantic ramp (moss/amber/rust) rather than new colors.
const TIER_CLASSES: Record<ScoreTier, string> = {
  high: "bg-mossSoft text-moss",
  medium: "bg-amber/10 text-amber",
  low: "bg-rustSoft text-rust",
};

// Metadata values are `unknown`; only a string/number narrows to something
// safe to render. Anything else (object, boolean, null, undefined) is
// treated as absent.
function asText(value: unknown): string | undefined {
  if (typeof value === "string") return value.trim() || undefined;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

function titleOf(metadata: Record<string, unknown>): string {
  return asText(metadata.title) ?? asText(metadata.filename) ?? "Untitled Document";
}

function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-chip bg-lineSoft px-2 py-0.5 font-mono text-[10.5px] text-dim">
      {children}
    </span>
  );
}

export function SourceCard({ source, rank }: { source: Source; rank: number }) {
  const [expanded, setExpanded] = useState(false);

  const metadata = source.metadata ?? {};
  const title = titleOf(metadata);
  const doi = asText(metadata.doi);
  const year = asText(metadata.year);
  const docType = asText(metadata.doc_type);
  const nCitations = typeof metadata.n_citations === "number" ? metadata.n_citations : undefined;
  // A third of some collections is the paper's own bibliography, correctly
  // flagged at ingest and retrieved anyway. A reference-list passage names a
  // gene and a finding in one line with no substance behind it, and it carries
  // the paper's OWN citation numbers — which is how an extractor ends up citing
  // a source number that was never in its context. Say so on the card.
  const isReferences =
    metadata.is_boilerplate === true || asText(metadata.section) === "references";
  // PMCID is not stamped as its own field, but the staged filename carries it
  // (Dengue_PMC5925603.pdf). Recovering it here costs nothing and is the id
  // people actually paste into PubMed.
  const pmcid = asText(metadata.pmcid) ?? (asText(metadata.filename) ?? "").match(/PMC\d+/)?.[0];

  const tier = scoreTier(source.score);
  const content = source.content ?? "";
  const isLong = content.length > PREVIEW_LIMIT;
  const shown = expanded || !isLong ? content : `${content.slice(0, PREVIEW_LIMIT)}…`;

  const hasTags = Boolean(
    year || docType || nCitations !== undefined || doi || pmcid || isReferences,
  );

  return (
    <li className="rounded-panel border border-line px-4 py-3.5">
      <div className="mb-2 flex items-start gap-2.5">
        <span className="shrink-0 rounded-pill bg-linkSoft px-2 py-0.5 font-mono text-[11px] font-medium text-link">
          #{rank}
        </span>
        <span className="flex-1 font-display text-[15px] font-semibold leading-[1.35] text-ink-900">
          {doi ? (
            <a
              href={`https://doi.org/${doi}`}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:underline"
            >
              {title}
            </a>
          ) : (
            title
          )}
        </span>
        <span
          className={`shrink-0 rounded-pill px-2 py-0.5 font-mono text-[11px] font-medium ${TIER_CLASSES[tier]}`}
        >
          {source.score.toFixed(1)}
        </span>
      </div>

      {hasTags && (
        <div className="mb-2.5 flex flex-wrap gap-1.5">
          {isReferences && (
            <span
              className="rounded-pill bg-amber-100 px-2 py-0.5 font-mono text-[11px] font-medium text-amber-900"
              title="This passage is from the paper's reference list, not its body text. Numbers in it are that paper's own citations."
            >
              reference list
            </span>
          )}
          {year && <Tag>{year}</Tag>}
          {docType && <Tag>{docType}</Tag>}
          {nCitations !== undefined && (
            <Tag>
              {nCitations} citation{nCitations === 1 ? "" : "s"}
            </Tag>
          )}
          {pmcid && <Tag>{pmcid}</Tag>}
          {doi && <Tag>{doi}</Tag>}
        </div>
      )}

      <p className="whitespace-pre-wrap text-[13.5px] leading-[1.7] text-body">{shown}</p>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="mt-1.5 text-[11px] font-medium text-link hover:underline"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </li>
  );
}
