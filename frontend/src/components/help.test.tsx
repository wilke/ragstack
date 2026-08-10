import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GLOSSARY, lookupTerm } from "../lib/glossary";
import { GlossaryPanel } from "./GlossaryPanel";
import { HelpTip } from "./HelpTip";

// Same static-render approach as render.test.tsx: no DOM, so this checks the
// CLOSED/initial markup — that the trigger renders, the panel doesn't, and the
// `term` prop really resolves through lib/glossary.
const render = (node: ReactElement): string => renderToStaticMarkup(node);

describe("HelpTip", () => {
  it("renders a collapsed disclosure whose description is already attached", () => {
    const html = render(createElement(HelpTip, { term: "top_k" }));
    expect(html).toContain("<button");
    expect(html).toContain("top_k");
    // A disclosure, not an ARIA tooltip: it opens on click and stays pinned.
    expect(html).not.toContain('role="tooltip"');
    expect(html).toContain('aria-expanded="false"');
    // The panel ships hidden rather than unmounted, so aria-describedby resolves
    // from first render — screen readers that snapshot the description at focus
    // time would otherwise announce nothing.
    expect(html).toContain("aria-describedby");
    expect(html).toContain('hidden=""');
  });

  it("renders the ? affordance with an accessible name", () => {
    const html = render(createElement(HelpTip, { term: "drift", icon: true }));
    expect(html).toContain("?");
    expect(html).toContain('aria-label="About drift"');
  });

  it("degrades to plain text when the term has no definition", () => {
    const html = render(createElement(HelpTip, { term: "not-a-real-term" }));
    expect(html).not.toContain("<button");
    expect(html).toContain("not-a-real-term");
    expect(lookupTerm("not-a-real-term")).toBeUndefined();
  });

  it("still opens for an unknown term when children supply the copy", () => {
    const html = render(
      createElement(HelpTip, { term: "not-a-real-term", children: "explained here" }),
    );
    expect(html).toContain("<button");
  });
});

describe("GlossaryPanel", () => {
  it("mounts collapsed, showing the trigger and no definitions", () => {
    const html = render(createElement(GlossaryPanel, {}));
    expect(html).toContain("Glossary");
    expect(html).toContain("Expand ▾");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("Reciprocal Rank Fusion");
  });

  it("renders the requested groups when opened", () => {
    const html = render(
      createElement(GlossaryPanel, { open: true, onToggle: () => {}, groups: ["Stores"] }),
    );
    expect(html).toContain("Collapse ▴");
    expect(html).toContain("vector store");
    expect(html).toContain("graph store");
    expect(html).not.toContain("Kendall"); // filtered out with its group
  });

  it("filters to groups that exist — a renamed group must not empty a screen", () => {
    for (const g of GROUPS_IN_USE) {
      expect(GLOSSARY.some((entry) => entry.group === g), g).toBe(true);
    }
  });
});

// Every group name a screen passes to <GlossaryPanel groups={…}/>. An unknown
// name matches nothing and silently renders an empty panel, so renaming a group
// in lib/glossary has to be a two-file change; this is the second file.
const GROUPS_IN_USE = [
  // EvidenceView
  "Retrieval mode",
  "Reranking",
  "Fusion & scoring",
  "Corpus & indexing",
  "Stores",
  "Runs & evidence",
  // CollectionView
  "Chunking",
  "Access & sharing",
  // OpsDashboard
  "Operations",
  // CompareView / ExploreView
  "Query rewriting",
  "Lane levers",
  "Model overrides",
  "Agreement metrics",
];

// A bodyless <HelpTip icon term="x"/> renders NOTHING when the term stops
// resolving (HelpTip returns null rather than an empty panel), so the tip would
// disappear without any test failing.
//
// The list is READ OUT OF THE SOURCE rather than maintained by hand: a
// hand-kept one silently misses the next tip somebody adds. Only self-closing
// tags count — a tip with children has a body of its own to fall back on — and
// only literal term="…" (a computed term={…} is checked by its own screen).
function tsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return tsxFiles(p);
    return e.name.endsWith(".tsx") && !e.name.endsWith(".test.tsx") ? [p] : [];
  });
}

function bodylessTerms(src: string): string[] {
  const out: string[] = [];
  const TAG = "<HelpTip";
  for (let i = src.indexOf(TAG); i !== -1; i = src.indexOf(TAG, i + 1)) {
    // Walk to the tag's closing ">", ignoring any inside a JSX expression or a
    // string literal.
    let depth = 0;
    let quote = "";
    let j = i + TAG.length;
    for (; j < src.length; j++) {
      const ch = src[j];
      if (quote) {
        if (ch === quote) quote = "";
      } else if (ch === '"' || ch === "'" || ch === "`") {
        quote = ch;
      } else if (ch === "{") {
        depth++;
      } else if (ch === "}") {
        depth--;
      } else if (ch === ">" && depth === 0) {
        break;
      }
    }
    if (src[j - 1] !== "/") continue; // has children
    const m = src.slice(i + TAG.length, j).match(/\bterm="([^"]+)"/);
    if (m) out.push(m[1]);
  }
  return out;
}

describe("terms carried by a bodyless tip", () => {
  const terms = [
    ...new Set(
      tsxFiles(fileURLToPath(new URL("..", import.meta.url))).flatMap((f) =>
        bodylessTerms(readFileSync(f, "utf8")),
      ),
    ),
  ];

  it("finds the tips it is meant to guard", () => {
    expect(terms.length).toBeGreaterThan(20);
    expect(terms).toContain("passage highlighting"); // SourceViewer's, missed by the old hand-kept list
    expect(terms).toContain("lane");
  });

  it("all resolve", () => {
    for (const t of terms) expect(lookupTerm(t), t).toBeDefined();
  });
});
