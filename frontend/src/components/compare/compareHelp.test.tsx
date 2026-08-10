import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { lookupTerm } from "../../lib/glossary";
import { CompareView } from "../CompareView";
import { AgreementBand } from "./AgreementBand";

// Static markup only (same approach as render.test.tsx): every HelpTip is closed,
// so these assert the TRIGGERS exist and that no native title="" tooltip came
// back — the panels themselves are covered by help.test.tsx.

function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const BAND = {
  full: 3,
  partial: 2,
  unique: 4,
  total: 9,
  laneCount: 2,
  overlapPct: 41,
  uniques: [{ letter: "A", count: 4 }],
  fastest: { letter: "B", ms: 1234 },
};

describe("Compare help", () => {
  it("makes the agreement heading a help trigger without changing the readout", () => {
    const html = render(
      createElement(AgreementBand, {
        stats: BAND,
        glossaryOpen: false,
        onToggleGlossary: () => {},
      }),
    );
    expect(html).toContain("Agreement");
    expect(html).toContain("3 of 9 docs in all lanes"); // the numbers still read the same
    expect(html).toContain("overlap 41%");
    // The heading is now a button; the panel is not in the closed markup.
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain('role="tooltip"');
    expect(html).toContain("What do these mean?"); // the glossary toggle survives
  });

  it("explains the shared defaults and keeps the glossary at the foot", () => {
    const html = render(
      createElement(CompareView, { apiKey: "", setApiKey: () => {} }),
    );
    expect(html).toContain("Applies to every lane unless overridden");
    expect(html).toContain("underline decoration-dotted"); // it is a help trigger now
    expect(html).toContain("Glossary");
    expect(html).toContain("Expand ▾");
  });

  // The lever labels used to carry the copy in title="", which is invisible to
  // keyboard and touch. It must not reappear as an attribute anywhere — and the
  // copy itself now comes from lib/glossary, not a second string table.
  it("never renders the lever copy as a native title attribute", () => {
    const html = render(
      createElement(CompareView, { apiKey: "", setApiKey: () => {} }),
    );
    for (const term of ["retrieval mode", "query rewriting", "rerank", "top_k"]) {
      const def = lookupTerm(term);
      expect(def, term).toBeDefined();
      expect(html).not.toContain(`title="${def}"`);
    }
  });
});
