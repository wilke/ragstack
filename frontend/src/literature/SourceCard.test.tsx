import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SourceCard } from "./SourceCard";

function render(metadata: Record<string, unknown>) {
  return renderToStaticMarkup(
    createElement(SourceCard, {
      rank: 1,
      source: { chunk_id: "c1", doc_id: "d1", content: "body", score: 1, metadata },
    } as never),
  );
}

describe("SourceCard provenance", () => {
  it("flags a passage the ingester marked as boilerplate", () => {
    expect(render({ is_boilerplate: true })).toContain("reference list");
  });

  it("flags a passage whose section is the reference list", () => {
    expect(render({ section: "references" })).toContain("reference list");
  });

  it("leaves body text unflagged", () => {
    expect(render({ section: "body" })).not.toContain("reference list");
  });

  it("recovers the PMCID from the staged filename when none is stamped", () => {
    // A PDF uploaded through the API carries no pmcid field, but the staged
    // name does — and it is the id people paste into PubMed.
    expect(render({ filename: "Dengue_PMC5925603.pdf" })).toContain("PMC5925603");
  });

  it("prefers a real pmcid over the filename", () => {
    // The filename also appears as the heading when no title is stamped, so
    // assert on the TAG specifically rather than anywhere in the markup.
    const html = render({ pmcid: "PMC1", filename: "x_PMC9999999.pdf" });
    expect(html).toContain(">PMC1<");
    expect(html).not.toContain(">PMC9999999<");
  });
});
