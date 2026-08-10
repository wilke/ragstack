import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OpsDashboard, PurgeConfirm } from "../OpsDashboard";

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
