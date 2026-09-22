import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { MAX_TOPK } from "./QueryOptionsMenu";

// #622 review: MAX_TOPK went 20 -> 100 with no test — set it back to 20 and the
// whole suite passed. The number is not the UI's to choose: it is the contract's
// `top_k.maximum`, which the Python API enforces with a 422. Pin the two together
// so the constant cannot drift from the schema in either direction.
describe("MAX_TOPK", () => {
  it("equals the contract's top_k maximum", () => {
    const schema = JSON.parse(
      readFileSync(new URL("../../../contracts/schemas/query_request.json", import.meta.url), "utf8"),
    ) as { properties: { top_k: { maximum?: number } } };
    expect(schema.properties.top_k.maximum).toBeDefined();
    expect(MAX_TOPK).toBe(schema.properties.top_k.maximum);
  });
});
