import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEFAULT_CHUNK_FORM } from "../lib/chunkers";
import { ChunkStrategyPicker } from "./ChunkStrategyPicker";
import { CollectionView } from "./CollectionView";
import { NewCollectionForm } from "./NewCollectionForm";
import { ShareDialog } from "./ShareDialog";

// The upload tab's help: one affordance per concept the screen can't say in its
// own labels. Static markup, so a closed tip is just its named trigger.
function render(node: ReactElement): string {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(createElement(QueryClientProvider, { client: qc }, node));
}

describe("Collection tab help", () => {
  const html = render(createElement(CollectionView, { apiKey: "", setApiKey: () => {} }));

  it("explains each step: what a collection is, and what Ingest actually does", () => {
    expect(html).toContain('aria-label="About collection"');
    expect(html).toContain('aria-label="About ingest job"');
    expect(html).not.toContain('role="tooltip"'); // closed until asked
  });

  it("closes with the glossary, collapsed", () => {
    expect(html).toContain("Glossary");
    expect(html).toContain("Expand ▾");
    expect(html).toContain("chunker"); // the Chunking group's teaser term
    expect(html).not.toContain("Reciprocal Rank Fusion"); // retrieval groups stay out
  });

  it("names the collection id/label trade in the create form", () => {
    const form = render(
      createElement(NewCollectionForm, {
        onCreate: () => {},
        onCancel: () => {},
        pending: false,
        error: null,
      }),
    );
    expect(form).toContain('aria-label="About collection name"');
  });
});

describe("chunk strategy help", () => {
  it("explains the chunker and its size/overlap levers", () => {
    const sized = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: DEFAULT_CHUNK_FORM, // fixed_token
        onChange: () => {},
      }),
    );
    expect(sized).toContain('aria-label="About chunker"');
    expect(sized).toContain('aria-label="About chunk size"');
    expect(sized).toContain('aria-label="About overlap"');
    // The label text itself is untouched — help never restates the label.
    expect(sized).toContain("Chunk size (tokens)");
  });

  it("swaps to the semantic tunables' help when size/overlap don't apply", () => {
    const semantic = render(
      createElement(ChunkStrategyPicker, {
        idPrefix: "t",
        form: { ...DEFAULT_CHUNK_FORM, method: "semantic" },
        onChange: () => {},
      }),
    );
    expect(semantic).toContain('aria-label="About semantic tunables"');
    expect(semantic).not.toContain('aria-label="About chunk size"');
    expect(semantic).toContain('aria-label="About chunker"');
  });
});

describe("share help", () => {
  it("covers read-only grants, grantee resolution and soft revoke", () => {
    const dialog = render(
      createElement(ShareDialog, {
        collectionId: "andy",
        collectionLabel: "Andy",
        apiKey: "",
        onClose: () => {},
      }),
    );
    expect(dialog).toContain('aria-label="About share"');
    expect(dialog).toContain('aria-label="About grantee"');
    expect(dialog).toContain('aria-label="About revoke"');
  });
});
