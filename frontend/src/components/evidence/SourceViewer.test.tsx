import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ChunkOut, Source } from "../../api/client";
import { ChunkCard, SourceViewer } from "./SourceViewer";

// Render tests for the source viewer (same no-DOM, no-fetch approach as
// render.test.tsx). SourceViewer covers the initial (matched-chunk) render:
// lexical marks + caption, and the walk controls' id-absent states. The
// walked/loading/error states only exist after a click, which static markup
// cannot perform — ChunkCard is rendered directly with those props instead.

function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

// First chunk of its document: no prev_chunk_id, a walkable next_chunk_id.
const matched: Source = {
  doc_id: "doc-1",
  chunk_id: "c-15",
  content:
    "Gut microbes modulate dopamine signalling in the honeybee brain. Sample sizes varied across cohorts.",
  score: 0.91,
  metadata: {
    title: "Microbiota and motor control",
    doc_type: "article",
    year: 2024,
    chunk_index: 15,
    next_chunk_id: "c-16",
  },
};

// Quotes the first chunk sentence exactly → matchSpans marks it (score 1).
const quotingAnswer =
  "Gut microbes modulate dopamine signalling in the honeybee brain [1].";

const neighbour: ChunkOut = {
  doc_id: "doc-1",
  chunk_id: "c-16",
  content: "Next passage about dopamine pathways in foragers.",
  metadata: { chunk_index: 16, prev_chunk_id: "c-15" },
};

function viewer(answer: string): string {
  return render(
    createElement(SourceViewer, { source: matched, answer, collection: null, apiKey: "" }),
  );
}

describe("SourceViewer — lexical answer marks", () => {
  it("marks the overlapping sentence and captions it as client-side", () => {
    const html = viewer(quotingAnswer);
    expect(html).toMatch(
      /<mark[^>]*>Gut microbes modulate dopamine signalling in the honeybee brain\.<\/mark>/,
    );
    // The non-matching sentence stays outside the mark.
    expect(html).toContain("Sample sizes varied across cohorts.");
    expect(html).not.toContain("cohorts.</mark>");
    expect(html).toContain("highlights = lexical match with the answer (client-side)");
    expect(html).toContain('aria-label="About passage highlighting"');
  });

  it("adds no marks and no caption when nothing clears the threshold", () => {
    const html = viewer("A completely unrelated statement about tax policy.");
    expect(html).not.toContain("<mark");
    expect(html).not.toContain("lexical match");
    // The whole-passage frame still carries the content.
    expect(html).toContain("Gut microbes modulate dopamine signalling");
  });
});

describe("SourceViewer — walk controls on the matched chunk", () => {
  it("disables only the direction whose chunk id is absent", () => {
    const html = viewer(quotingAnswer);
    expect(html).toContain("‹ prev");
    expect(html).toContain("next ›");
    expect(html).toContain("No earlier chunk in this document"); // no prev_chunk_id
    expect(html).not.toContain("No later chunk"); // next_chunk_id present
    expect(html).toContain('aria-label="About chunk walking"'); // what walking means, once
  });

  it("shows the score and no walk framing at the match", () => {
    const html = viewer(quotingAnswer);
    expect(html).toContain("article · 2024 · chunk 15");
    expect(html).toContain("0.91");
    expect(html).not.toContain("walked from match");
    // The button, not the phrase: the "chunk walking" definition quotes it, and
    // that panel ships hidden in every render.
    expect(html).not.toContain("← back to match");
  });
});

function card(props: Partial<Parameters<typeof ChunkCard>[0]>): string {
  return render(
    createElement(ChunkCard, {
      source: matched,
      chunk: neighbour,
      atMatch: false,
      walkStatus: "ready",
      answer: "Nothing shared with that passage.",
      onWalk: () => {},
      onBackToMatch: () => {},
      ...props,
    }),
  );
}

describe("ChunkCard — walked / loading / error states", () => {
  it("labels a walked neighbour and drops the matched chunk's score", () => {
    const html = card({});
    expect(html).toContain("chunk 16 · walked from match");
    expect(html).toContain("Next passage about dopamine pathways in foragers.");
    expect(html).toContain("← back to match");
    expect(html).not.toContain("0.91"); // score belongs to the matched chunk only
    // Walking continues from the DISPLAYED chunk's ids: prev exists, next does not.
    expect(html).toContain("No later chunk in this document");
    expect(html).not.toContain("No earlier chunk");
  });

  it("shows an honest loading state", () => {
    const html = card({ chunk: null, walkStatus: "loading" });
    expect(html).toContain("loading chunk…");
    expect(html).toContain("Loading chunk…");
    expect(html).not.toContain("walked from match");
  });

  it("shows an honest error state with the way back", () => {
    const html = card({ chunk: null, walkStatus: "error" });
    expect(html).toContain("chunk unavailable");
    expect(html).toContain("could not be loaded");
    expect(html).toContain("← back to match");
    expect(html).not.toContain("0.91");
  });
});
