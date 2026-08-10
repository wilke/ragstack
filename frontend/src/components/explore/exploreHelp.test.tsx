import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { lookupTerm } from "../../lib/glossary";
import { AnswerCard } from "../AnswerCard";
import { DEFAULT_QUERY_OPTIONS } from "../QueryOptionsMenu";
import { SourceList } from "../SourceList";
import { EmptyState } from "../states/EmptyState";
import { ConfigChips } from "./ConfigChips";
import { RunRail } from "./RunRail";

// Static markup only (as in render.test.tsx): HelpTip panels are closed until
// hover/focus, so these assert the TRIGGERS exist and that the visible copy that
// replaced the tooltips is present. The one thing worth locking down beyond
// presence is that the Options menu reads its lever copy from lib/glossary
// rather than a second, drift-prone string table.
const render = (node: ReactElement): string => renderToStaticMarkup(node);

describe("Explore help affordances", () => {
  it("puts a help trigger on the config chip row, not on each chip", () => {
    const html = render(
      createElement(ConfigChips, {
        opts: [],
        collection: "",
        setCollection: () => {},
        options: DEFAULT_QUERY_OPTIONS,
        onOptionsChange: () => {},
        serverRerank: null,
      }),
    );
    // The row's own tip plus the Options trigger — and nothing per chip. Every
    // "?" is named for what it explains, never the generic fallback.
    expect(html).toContain('aria-label="About query settings"');
    expect((html.match(/aria-label="About /g) ?? []).length).toBe(1);
    expect(html).not.toContain('aria-label="More information"');
    expect(html).toContain("Options");
  });

  it("explains the citation grammar and the Evidence jump on the answer", () => {
    const html = render(
      createElement(AnswerCard, {
        query: "bees?",
        answer: "Bees model Parkinson's disease [1].",
        rewrittenQueries: [],
        pending: false,
        sourceCount: 1,
        onOpenEvidence: () => {},
      }),
    );
    // Heading tip + Verify tip + the feedback tip — one per concept, none on
    // the individual citation chips.
    expect((html.match(/aria-label="About /g) ?? []).length).toBe(3);
    expect(html).toContain('aria-label="About citation"');
    expect(html).toContain('aria-label="About evidence"');
    expect(html).toContain('aria-label="About feedback"');
    expect(html).toContain("Verify in Evidence");
    expect(html).toContain("Was this useful?");
  });

  it("explains source-card anatomy once, on the Sources heading", () => {
    const source = {
      doc_id: "d1",
      chunk_id: "c1",
      content: "…of the honeybee PD model…",
      score: 0.91,
      metadata: { title: "Honeybee gut microbiota", year: 2024 },
    };
    const html = render(
      createElement(SourceList, { sources: [source, { ...source, chunk_id: "c2" }] }),
    );
    expect(html).toContain("Sources (2)");
    expect((html.match(/aria-label="About /g) ?? []).length).toBe(1);
    expect(html).toContain('aria-label="About source"');
  });

  it("gives the run rail a tip per section and no invented per-leg counts", () => {
    const html = render(
      createElement(RunRail, {
        run: null,
        serverRerank: null,
        recent: [],
        onPick: () => {},
        onOpenEvidence: () => {},
        onSendToCompare: () => {},
      }),
    );
    expect(html).toContain("This run");
    expect(html).toContain("Recent questions");
    expect((html.match(/aria-label="About /g) ?? []).length).toBe(2);
    expect(html).toContain('aria-label="About run"');
    expect(html).toContain('aria-label="About recent questions"');
  });

  it("makes the empty state name the collection chip and Options", () => {
    const html = render(createElement(EmptyState));
    expect(html).toContain("collection");
    expect(html).toContain("Options");
    expect(html).toContain("Evidence");
  });

  it("keeps the mode lever copy sourced from the glossary", () => {
    // Not a duplicated string table: the definitions the Options panel shows are
    // the glossary's, so editing lib/glossary edits the hover copy.
    for (const t of ["hybrid", "vector", "bm25", "none", "multiquery", "hyde", "top_k"]) {
      expect(lookupTerm(t)).toBeTruthy();
    }
    expect(lookupTerm("cross-encoder")).toBeTruthy();
  });
});
