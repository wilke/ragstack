import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEFAULT_CHUNK_FORM } from "../lib/chunkers";
import { ChunkStrategyPicker } from "./ChunkStrategyPicker";
import { CollectionView } from "./CollectionView";
import { NewCollectionForm } from "./NewCollectionForm";
import { OpsDashboard } from "./OpsDashboard";

// Render smoke tests: no DOM, no fetch — `renderToStaticMarkup` just proves each
// screen mounts and produces the text it promises. Cheap insurance for the parts
// TypeScript can't check (a hook called conditionally, a missing Fragment around
// the multi-row table body, a component renamed in one place only).

function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

describe("static render", () => {
  it("mounts the Ops dashboard with its Collections section", () => {
    const html = render(createElement(OpsDashboard, { apiKey: "" }));
    expect(html).toContain("Collections");
    expect(html).toContain("New collection"); // the admin create control
  });

  it("mounts the demo Collection view and says 'collection', never 'library'", () => {
    const html = render(createElement(CollectionView, { apiKey: "", setApiKey: () => {} }));
    expect(html).toContain("New collection");
    expect(html.toLowerCase()).not.toContain("librar");
  });

  it("mounts the new-collection form", () => {
    const html = render(
      createElement(NewCollectionForm, {
        onCreate: () => {},
        onCancel: () => {},
        pending: false,
        error: null,
      }),
    );
    expect(html).toContain("Create collection");
  });

  it("labels size/overlap with the unit the chosen method counts in", () => {
    const chars = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: { ...DEFAULT_CHUNK_FORM, method: "sentence" },
        onChange: () => {},
      }),
    );
    expect(chars).toContain("Chunk size (characters)");
    expect(chars).toContain("Overlap (characters)");

    const toks = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: DEFAULT_CHUNK_FORM, // fixed_token
        onChange: () => {},
      }),
    );
    expect(toks).toContain("Chunk size (tokens)");
  });

  it("swaps size/overlap for the semantic tunables on a semantic method", () => {
    const html = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: { ...DEFAULT_CHUNK_FORM, method: "semantic_pooled" },
        onChange: () => {},
      }),
    );
    expect(html).not.toContain("Chunk size (");
    expect(html).toContain("Buffer size");
    expect(html).toContain("Breakpoint percentile");
    expect(html).toContain("Min chunk length");
  });
});
