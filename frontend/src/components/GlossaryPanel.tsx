import { useId, useState, type ReactNode } from "react";
import { GLOSSARY } from "../lib/glossary";

// The shared glossary disclosure — extracted from CompareView so every screen
// renders the same grouped definitions from lib/glossary instead of its own copy.
//
// Collapsed by default and uncontrolled; pass `open` + `onToggle` to drive it
// from elsewhere (Compare's agreement band toggles the same bit from its
// "What do these mean? ▾" link). `groups` narrows it to the groups a screen
// actually needs — an unknown group name simply matches nothing.

export function GlossaryPanel({
  groups,
  dark = false,
  open,
  onToggle,
  regionId,
  title = "Glossary",
  summary,
  className = "",
  inset = "px-0",
}: {
  groups?: string[]; // GLOSSARY group names to show; omit for all
  dark?: boolean; // Evidence's ink grounds
  open?: boolean; // controlled state; omit for internal state
  onToggle?: () => void;
  // Id of the expanded region. Pass one when a REMOTE trigger drives this panel
  // (Compare's agreement band), so that trigger can name it in aria-controls.
  regionId?: string;
  title?: string;
  summary?: ReactNode; // teaser line beside the title; defaults to the shown groups' first terms
  className?: string; // extra classes on the <section> (e.g. a negative margin bleed)
  inset?: string; // horizontal padding utility for the header row and the grid
}) {
  const autoId = useId();
  const panelId = regionId ?? autoId;
  const [selfOpen, setSelfOpen] = useState(false);
  const isOpen = open ?? selfOpen;
  const toggle = onToggle ?? (() => setSelfOpen((o) => !o));

  const shown = groups ? GLOSSARY.filter((g) => groups.includes(g.group)) : GLOSSARY;
  const teaser = summary ?? shown.map((g) => g.items[0]?.term).filter(Boolean).join(" · ");

  return (
    <section
      className={`mt-6 border-t ${dark ? "border-white/15 bg-white/[0.04]" : "border-line bg-paper"} ${className}`}
    >
      <button
        type="button"
        onClick={toggle}
        aria-expanded={isOpen}
        aria-controls={panelId}
        className={`flex w-full flex-wrap items-center gap-3.5 py-4 text-left ${inset}`}
      >
        <span
          className={`font-display text-[13px] font-semibold ${dark ? "text-white" : "text-ink-900"}`}
        >
          {title}
        </span>
        {/* text-body, not text-dim: at 12px the dim ramp is 2.5:1 on paper in
            the default vision mode. */}
        <span className={`text-[12px] ${dark ? "text-[#8fb3d4]" : "text-body"}`}>{teaser}</span>
        <span
          className={`ml-auto text-[12px] font-medium ${dark ? "text-accent" : "text-link"}`}
        >
          {isOpen ? "Collapse ▴" : "Expand ▾"}
        </span>
      </button>
      {isOpen ? (
        <div
          id={panelId}
          // Focusable only programmatically: a REMOTE trigger (Compare's band)
          // opens content a page away, so it moves focus here — aria-controls
          // alone is not a navigation aid in most screen readers.
          tabIndex={-1}
          className={`grid gap-x-6 gap-y-5 border-t pb-6 pt-4 sm:grid-cols-2 lg:grid-cols-3 ${
            dark ? "border-white/15" : "border-line"
          } ${inset}`}
        >
          {shown.map((g) => (
            <div key={g.group}>
              <div
                className={`mb-1.5 font-mono text-[10px] font-medium uppercase tracking-[.12em] ${
                  dark ? "text-[#8fb3d4]" : "text-[#6a6a64]"
                }`}
              >
                {g.group}
              </div>
              <dl className="space-y-1.5">
                {g.items.map((i) => (
                  <div key={i.term}>
                    <dt
                      className={`text-xs font-medium ${dark ? "text-[#dce8f3]" : "text-strong"}`}
                    >
                      {i.term}
                    </dt>
                    <dd className={`text-xs leading-snug ${dark ? "text-[#a8c6e0]" : "text-body"}`}>
                      {i.def}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
