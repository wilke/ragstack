import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { RunRecord } from "../lib/run";
import { EvidenceView } from "./EvidenceView";

// Render smoke tests for the Evidence screen (same no-DOM, no-fetch approach as
// render.test.tsx): the empty state, and a populated run — asserting the parts
// the backend actually provides render, and the parts it does NOT provide
// (grounding scores, recall, per-leg counts) are never fabricated.

function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

const run: RunRecord = {
  id: "0k3f2",
  query: "What is the role of bees?",
  collection: "oa_jats_dev",
  options: { mode: "hybrid", rewrite: "none", rerank: "on", topK: 5 },
  startedAt: 1754700000000,
  ms: 1120,
  response: {
    answer:
      "Bees are used as a model organism to study Parkinson's disease [1]. Passage 2 discusses ants, not bees [2].",
    rewritten_queries: [],
    sources: [
      {
        doc_id: "doc-bees",
        chunk_id: "c-1",
        content: "Honeybees were colonised with faecal microbiota from PD patients.",
        score: 0.91,
        metadata: {
          title: "Gut microbiota causes motor deficits in honeybees",
          authors: ["Zeng", "Li"],
          year: 2024,
          doc_type: "article",
          chunk_index: 41,
        },
      },
      {
        doc_id: "doc-ants",
        chunk_id: "c-2",
        content: "Cooperative disease defence in ant colonies.",
        score: 0.61,
        metadata: { title: "Cooperative disease defence in ant colonies" },
      },
      {
        doc_id: "doc-amp",
        chunk_id: "c-3",
        content: "Antimicrobial peptides in insect immunity.",
        score: 0.58,
        metadata: {}, // uncited AND metadata-free: row must fall back to doc_id
      },
    ],
  },
};

function evidence(r: RunRecord | null): string {
  return render(
    createElement(EvidenceView, { run: r, apiKey: "", onSendToCompare: () => {} }),
  );
}

describe("EvidenceView", () => {
  it("renders the empty state when no run exists", () => {
    const html = evidence(null);
    expect(html).toContain("No run to verify yet");
    expect(html).not.toContain("Export report");
  });

  it("renders a populated run: run bar, pipeline, claims, viewer, retrieved set", () => {
    const html = evidence(run);
    // Run bar + export action.
    expect(html).toContain("RUN");
    expect(html).toContain("What is the role of bees?");
    expect(html).toContain("0k3f2 · oa_jats_dev · hybrid · k5");
    expect(html).toContain("Export report");
    // Pipeline strip: both hybrid legs, explicit rerank stage, honest caption.
    expect(html).toContain("VECTOR");
    expect(html).toContain("ES");
    expect(html).toContain("RRF → CROSS-ENCODER → 3 KEPT");
    expect(html).toContain("1.12s · rewrite none");
    // Claims: split into sentences, chips per citation, honestly ungraded.
    expect(html).toContain("claims ungraded");
    expect(html).toContain("Bees are used as a model organism");
    expect(html).toContain("src 1");
    expect(html).toContain("src 2");
    // Source viewer defaults to the first cited source.
    expect(html).toContain("Source 1 of 3");
    expect(html).toContain("Gut microbiota causes motor deficits in honeybees");
    expect(html).toContain("article · 2024 · chunk 41");
    expect(html).toContain("Zeng, Li");
    expect(html).toContain("0.91");
    // Retrieved set: the OTHER sources, metadata-free row falls back to doc_id.
    expect(html).toContain("Retrieved set");
    expect(html).toContain("Cooperative disease defence in ant colonies");
    expect(html).toContain("doc-amp");
    // Footer actions.
    expect(html).toContain("Save run");
    expect(html).toContain("Send to Compare →");
  });

  it("never fabricates data the API does not return", () => {
    const html = evidence(run);
    expect(html).not.toContain("grounded"); // no per-claim grounding scores
    expect(html).not.toContain("recall"); // no recall@50
    expect(html).not.toContain("Claim dropped"); // needs the grounding post-pass
    // KG section renders only from resolved /v1/graph data; nothing was fetched
    // in this static render, so it must be hidden — never invented.
    expect(html).not.toContain("Entities in this answer");
  });

  it("offers help on the terms and actions whose consequence is not visible", () => {
    const html = evidence(run);
    expect(html).toContain('aria-label="About run selector"');
    expect(html).toContain('aria-label="About Export report"');
    expect(html).toContain('aria-label="About pipeline strip"');
    expect(html).toContain('aria-label="About claim"');
    expect(html).toContain('aria-label="About Retrieved set"');
    expect(html).toContain('aria-label="About saved run"');
    expect(html).toContain('aria-label="About Send to Compare"');
    // Every tip is closed on first paint — no panel, no describedby.
    expect(html).not.toContain('role="tooltip"');
    // …and the screen ends with the glossary, collapsed.
    expect(html).toContain("Glossary");
    expect(html).toContain("Expand ▾");
  });

  it("omits the RRF stage when only one retrieval leg ran", () => {
    const single: RunRecord = {
      ...run,
      options: { ...run.options, mode: "vector", rerank: null },
    };
    const html = evidence(single);
    expect(html).toContain("3 KEPT");
    // Matched on the strip's own separators: the bare names also occur in the
    // "pipeline strip" definition, which ships hidden in every render.
    expect(html).not.toContain("RRF →");
    expect(html).not.toContain("→ CROSS-ENCODER"); // rerank null = server default, unknowable
  });
});
