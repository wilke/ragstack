import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CollectionDetail, OpsDashboard, PurgeConfirm } from "../OpsDashboard";
import { LaneFacts } from "../CompareView";
import type { CollectionInfo } from "../../api/client";

// Ops carries the most operator jargon, so every section heading owns a help
// affordance and each one announces WHICH section it explains. Static markup
// only (no DOM): a closed HelpTip renders just its trigger, so what is asserted
// here is that the affordance exists, is a real button, and is named — not the
// panel copy.
function render(node: ReactElement, seed?: (qc: QueryClient) => void): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed?.(qc);
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

describe("Ops help", () => {
  const html = render(createElement(OpsDashboard, { apiKey: "" }));

  it("names one help affordance per section, after the section it belongs to", () => {
    for (const label of [
      "Deep health",
      "Stores",
      "Collections",
      "Models",
      "Data ownership",
      "Ingest jobs",
      "Config",
    ]) {
      expect(html).toContain(`aria-label="About ${label}"`);
    }
  });

  // The registry table only exists once there are rows, so seed the cache the
  // panel reads rather than asserting against the empty state.
  it("explains the access column on its header, not in every row", () => {
    const withRows = render(createElement(OpsDashboard, { apiKey: "" }), (qc) =>
      qc.setQueryData(["collections-ops", ""], {
        collections: [{ id: "andy", label: "Andy", model: "test/sfr", dim: 8, default: false }],
      }),
    );
    expect(withRows).toContain("Access");
    expect(withRows).toContain('aria-label="About access"');
    // The per-cell native tooltip it replaced must be gone — one explanation of
    // one concept, at the top of the column.
    expect(withRows).not.toContain("Share status isn't fetched on this page");
  });

  it("explains the status band's counts, the jobs card and Re-check", () => {
    expect(html).toContain('aria-label="About vector store"');
    expect(html).toContain('aria-label="About text index (BM25)"');
    expect(html).toContain('aria-label="About graph store"');
    expect(html).toContain('aria-label="About ingest job"');
    expect(html).toContain('aria-label="About re-check"');
  });

  it("keeps every panel closed until asked", () => {
    expect(html).not.toContain('role="tooltip"');
    expect(html).not.toContain('aria-expanded="true"');
  });

  // The irreversible control explains itself in full, on screen. A tip there
  // would move part of that explanation behind a hover.
  it("adds no help affordance to the permanent-delete gate", () => {
    const purge = render(
      createElement(PurgeConfirm, {
        c: { id: "throwaway", label: "Throwaway", model: "test/sfr", dim: 8, default: false },
        onCancel: () => {},
        onPurged: () => {},
      }),
    );
    expect(purge).not.toContain("aria-label=\"About");
    expect(purge).toContain("irreversible");
  });
});

// Semantic size cap (#829) in the three views that name a collection's chunking.
const base = { model: "test/sfr", dim: 8, default: false };
const CAPPED: CollectionInfo = {
  ...base, id: "capped", label: "Capped", chunk_method: "semantic", chunk_size: 512,
  provenance: { chunk_method: "semantic", chunk_size: 512, chunk_params: { size_cap: "tokens", cap_split: "topic" } },
};
const LEGACY: CollectionInfo = {
  ...base, id: "legacy", label: "Legacy", chunk_method: "semantic", chunk_size: 512,
  provenance: { chunk_method: "semantic", chunk_size: 512, chunk_overlap: 64, chunk_params: {} },
};
const BARE: CollectionInfo = { ...base, id: "bare", label: "Bare", chunk_method: "semantic", chunk_size: 512 };

describe("semantic size cap display", () => {
  it("Ops table row", () => {
    const html = render(createElement(OpsDashboard, { apiKey: "" }), (qc) =>
      qc.setQueryData(["collections-ops", ""], { collections: [CAPPED, LEGACY, BARE] }),
    );
    expect(html).toContain("semantic · cap 512 tok · topic split");
    expect(html).toContain("semantic · uncapped (legacy)");
    expect(html).toContain("semantic/512"); // no provenance: nothing claimed, registry size shown
  });

  it("Ops detail 'size cap' row", () => {
    const capped = render(createElement(CollectionDetail, { c: CAPPED }));
    expect(capped).toContain("size cap");
    expect(capped).toContain("cap 512 tok · topic split");
    expect(render(createElement(CollectionDetail, { c: LEGACY }))).toContain("uncapped (legacy)");
    expect(render(createElement(CollectionDetail, { c: BARE }))).not.toContain("size cap");
  });

  it("Compare lane header", () => {
    expect(render(createElement(LaneFacts, { c: CAPPED }))).toContain("semantic · cap 512 tok · topic split");
    expect(render(createElement(LaneFacts, { c: LEGACY }))).toContain("semantic · uncapped (legacy)");
    const bare = render(createElement(LaneFacts, { c: BARE }));
    expect(bare).toContain("semantic");
    expect(bare).not.toContain("cap");
    expect(bare).not.toContain("legacy");
  });
});
