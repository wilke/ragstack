import { describe, expect, it } from "vitest";
import {
  ID_BLANK_HINT,
  ID_EXPLICIT_HINT,
  collectionCreateMessage,
  collectionDeleteMessage,
} from "./collections";

describe("collectionCreateMessage", () => {
  it("names the conflict for a duplicate id", () => {
    expect(collectionCreateMessage(409, '{"detail":"collection \'x\' already exists"}')).toContain(
      "already exists",
    );
  });

  it("points at the registry for an unknown embedding model", () => {
    const msg = collectionCreateMessage(404, '{"detail":"unknown model \'nope\'"}');
    expect(msg).toContain("registry");
  });

  it("surfaces the server's own sentence for a 400, not the raw body", () => {
    const body = '{"detail":"chunk overlap (64) must be smaller than the chunk size (32)"}';
    const msg = collectionCreateMessage(400, body);
    expect(msg).toContain("chunk overlap (64) must be smaller than the chunk size (32)");
    expect(msg).not.toContain("{");
  });

  it("falls back to a generic sentence when the body is not JSON", () => {
    expect(collectionCreateMessage(400, "<html>502</html>")).toBe(
      "The server rejected the collection config (bad model or chunk strategy).",
    );
  });

  it("joins 422 validation messages", () => {
    const body = '{"detail":[{"msg":"field required"},{"msg":"not a number"}]}';
    expect(collectionCreateMessage(422, body)).toContain("field required; not a number");
  });

  it("says admin for 401/403", () => {
    expect(collectionCreateMessage(403, "")).toContain("admin");
    expect(collectionCreateMessage(401, "")).toContain("admin");
  });

  it("reports a transport failure distinctly from an HTTP status", () => {
    expect(collectionCreateMessage(null, "")).toContain("could not reach the API");
    expect(collectionCreateMessage(500, "")).toContain("error 500");
  });
});

describe("collectionDeleteMessage", () => {
  it("uses the server's reason for a 409 when it gives one", () => {
    expect(collectionDeleteMessage(409, '{"detail":"cannot delete the default collection"}')).toBe(
      "cannot delete the default collection",
    );
  });

  it("has a fallback for a 409 with no detail", () => {
    expect(collectionDeleteMessage(409, "")).toContain("default collection");
  });

  it("treats 404 as already gone", () => {
    expect(collectionDeleteMessage(404, "")).toContain("already gone");
  });

  it("says admin for 403", () => {
    expect(collectionDeleteMessage(403, "")).toContain("admin");
  });
});

describe("id hints", () => {
  // These two lines are the UI's only explanation of the sharing behaviour that
  // caused a real data-sharing bug, so assert they actually say the thing.
  it("explain isolation vs sharing", () => {
    expect(ID_EXPLICIT_HINT).toMatch(/own physical/i);
    expect(ID_BLANK_HINT).toMatch(/content-addressed/i);
    expect(ID_BLANK_HINT).toMatch(/shares the same physical store/i);
  });
});
