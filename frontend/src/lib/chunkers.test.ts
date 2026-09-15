import { describe, expect, it } from "vitest";
import {
  CHUNK_METHODS,
  DEFAULT_CHUNK_FORM,
  apiDetail,
  apiDetailObject,
  buildChunkConfig,
  describeChunking,
  isChunkMethod,
  isSemanticMethod,
  validateChunkForm,
  type ChunkForm,
} from "./chunkers";

const form = (over: Partial<ChunkForm> = {}): ChunkForm => ({
  ...DEFAULT_CHUNK_FORM,
  params: { ...DEFAULT_CHUNK_FORM.params },
  ...over,
});

describe("method list", () => {
  it("mirrors the server's CHUNK_METHODS tuple", () => {
    // Parity with python/ragstack/ingestion/chunkers.py is enforced by
    // python/tests/api/test_chunk_method_parity.py; this just pins the shape.
    expect([...CHUNK_METHODS]).toEqual([
      "fixed",
      "fixed_token",
      "sentence",
      "words",
      "semantic",
      "semantic_pooled",
    ]);
  });

  it("recognises known methods and rejects made-up ones", () => {
    expect(isChunkMethod("sentence")).toBe(true);
    expect(isChunkMethod("recursive")).toBe(false);
    expect(isSemanticMethod("semantic_pooled")).toBe(true);
    expect(isSemanticMethod("fixed_token")).toBe(false);
  });
});

describe("validateChunkForm", () => {
  it("accepts the default one-click config", () => {
    expect(validateChunkForm(form())).toBeNull();
  });

  it("rejects a non-numeric size", () => {
    expect(validateChunkForm(form({ size: "abc" }))).toMatch(/whole number/i);
  });

  it("rejects a zero or negative size", () => {
    expect(validateChunkForm(form({ size: "0" }))).toMatch(/at least 1/i);
    expect(validateChunkForm(form({ size: "-5" }))).toBeTruthy();
  });

  it("rejects overlap >= size (chunking would never advance)", () => {
    expect(validateChunkForm(form({ size: "512", overlap: "512" }))).toMatch(/smaller/i);
    expect(validateChunkForm(form({ size: "512", overlap: "600" }))).toMatch(/smaller/i);
    expect(validateChunkForm(form({ overlap: "-1" }))).toMatch(/negative/i);
  });

  it("allows -1 only for the methods that implement whole-document chunks", () => {
    expect(validateChunkForm(form({ method: "sentence", size: "-1", overlap: "0" }))).toBeNull();
    expect(validateChunkForm(form({ method: "fixed", size: "-1", overlap: "0" }))).toBeTruthy();
  });

  it("ignores size/overlap for semantic methods", () => {
    // The semantic branch never reads them, so garbage there is not an error.
    expect(validateChunkForm(form({ method: "semantic", size: "x", overlap: "y" }))).toBeNull();
  });

  it("range-checks semantic params but treats blank as 'server default'", () => {
    expect(validateChunkForm(form({ method: "semantic", params: {} }))).toBeNull();
    expect(
      validateChunkForm(form({ method: "semantic", params: { buffer_size: "  " } })),
    ).toBeNull();
    expect(
      validateChunkForm(form({ method: "semantic", params: { buffer_size: "0" } })),
    ).toMatch(/between/i);
    expect(
      validateChunkForm(
        form({ method: "semantic", params: { breakpoint_percentile_threshold: "150" } }),
      ),
    ).toMatch(/between/i);
    expect(
      validateChunkForm(form({ method: "semantic", params: { min_chunk_length: "1.5" } })),
    ).toMatch(/whole number/i);
  });
});

describe("buildChunkConfig", () => {
  it("sends size/overlap for the sized methods", () => {
    expect(buildChunkConfig(form())).toEqual({ method: "fixed_token", size: 512, overlap: 64 });
    expect(buildChunkConfig(form({ method: "words", size: "800", overlap: "80" }))).toEqual({
      method: "words",
      size: 800,
      overlap: 80,
    });
  });

  it("sends NO size/overlap for a semantic chunker", () => {
    // A semantic collection never used a window; claiming one in the request would
    // land a false size/overlap in the collection's manifest + provenance.
    const body = buildChunkConfig(form({ method: "semantic" }));
    expect(body).toEqual({ method: "semantic" });
    expect("size" in body).toBe(false);
    expect("overlap" in body).toBe(false);
  });

  it("sends only the semantic params the user actually filled in", () => {
    const body = buildChunkConfig(
      form({
        method: "semantic_pooled",
        params: { buffer_size: "5", breakpoint_percentile_threshold: "", min_chunk_length: "200" },
      }),
    );
    expect(body).toEqual({
      method: "semantic_pooled",
      params: { buffer_size: 5, min_chunk_length: 200 },
    });
  });
});

describe("describeChunking", () => {
  it("prefers manifest provenance over the registry label", () => {
    expect(
      describeChunking({
        chunk_method: "fixed",
        chunk_size: 512,
        provenance: { chunk_method: "fixed_token", chunk_size: 256, chunk_overlap: 32 },
      }),
    ).toBe("fixed_token · 256/32 tok");
  });

  it("falls back to the registry label when there is no manifest", () => {
    expect(describeChunking({ chunk_method: "sentence", chunk_size: 800 })).toBe(
      "sentence · 800 chars",
    );
  });

  it("never invents a size for a semantic collection", () => {
    expect(describeChunking({ chunk_method: "semantic", chunk_size: 512 })).toBe("semantic");
    expect(
      describeChunking({
        chunk_method: "semantic",
        provenance: { chunk_method: "semantic", chunk_params: { buffer_size: 5 } },
      }),
    ).toBe("semantic · buffer size 5");
  });

  it("is empty when nothing is known", () => {
    expect(describeChunking({})).toBe("");
    expect(describeChunking({ chunk_method: null, provenance: null })).toBe("");
  });
});

describe("apiDetail", () => {
  it("unwraps a FastAPI HTTPException detail", () => {
    expect(apiDetail('{"detail": "unknown chunk method \'nope\'; valid: fixed, sentence"}')).toBe(
      "unknown chunk method 'nope'; valid: fixed, sentence",
    );
  });

  it("joins 422 validation messages", () => {
    expect(
      apiDetail('{"detail": [{"msg": "Input should be a valid integer"}, {"msg": "field required"}]}'),
    ).toBe("Input should be a valid integer; field required");
  });

  it("returns empty for anything not worth showing a user", () => {
    expect(apiDetail("Internal Server Error")).toBe("");
    expect(apiDetail("{not json")).toBe("");
    expect(apiDetail("")).toBe("");
    expect(apiDetail('{"other": 1}')).toBe("");
  });
});

// --- object-valued detail (#555) --------------------------------------------
// `detail` is untyped in contracts/schemas/error.json: a string nearly
// everywhere, an array for FastAPI's 422, an OBJECT for the
// `owner_quota_exceeded` 409. The object shape used to fall through to "" and
// the server's explanation was dropped on the floor.
describe("apiDetail with a structured detail object", () => {
  const QUOTA =
    '{"detail": {"error": "owner_quota_exceeded", "owned": 10, "limit": 10,' +
    ' "message": "already owns 10 collection(s), at the quota of 10' +
    ' (MAX_COLLECTIONS_PER_OWNER); free one up (delete or transfer it away)' +
    ' before acquiring another"}}';

  it("prefers the object's own message", () => {
    expect(apiDetail(QUOTA)).toContain("at the quota of 10");
    expect(apiDetail(QUOTA)).not.toContain("{");
  });

  it("falls back to the error code when there is no message", () => {
    // Not "" — the caller would then claim the server explained nothing — and
    // not the JSON, which is never shown to a user.
    const msg = apiDetail('{"detail": {"error": "owner_quota_exceeded", "owned": 3, "limit": 3}}');
    expect(msg).toBe("owner quota exceeded");
    expect(msg).not.toContain("{");
    expect(msg).not.toContain("owned");
  });

  it("ignores a blank or non-string message and code", () => {
    expect(apiDetail('{"detail": {"error": "x_failed", "message": "   "}}')).toBe("x failed");
    expect(apiDetail('{"detail": {"error": "x_failed", "message": 17}}')).toBe("x failed");
    // Neither field: genuinely nothing readable, so the caller's generic
    // sentence is the right answer.
    expect(apiDetail('{"detail": {"owned": 3, "limit": 3}}')).toBe("");
    expect(apiDetail('{"detail": {"error": 7}}')).toBe("");
    expect(apiDetail('{"detail": {}}')).toBe("");
  });

  it("leaves the string and array shapes exactly as they were", () => {
    // These are the shapes every other caller relies on; the object branch must
    // not have reordered or shadowed them.
    expect(apiDetail('{"detail": "collection \'x\' already exists"}')).toBe(
      "collection 'x' already exists",
    );
    expect(apiDetail('{"detail": [{"msg": "field required"}, {"msg": "not a number"}]}')).toBe(
      "field required; not a number",
    );
    // An array whose entries carry no `msg` is still nothing to show — and an
    // array must never be treated as a structured object (typeof [] is
    // "object" in JS, which is exactly the trap here).
    expect(apiDetail('{"detail": [{"loc": ["body"]}]}')).toBe("");
    expect(apiDetail('{"detail": [{"message": "nope"}]}')).toBe("");
  });

  it("still returns empty for a malformed or null body", () => {
    expect(apiDetail('{"detail": {"error": "owner_quota_exceeded"')).toBe(""); // truncated JSON
    expect(apiDetail('{"detail": null}')).toBe("");
    expect(apiDetail("null")).toBe("");
    expect(apiDetail("<html>502 Bad Gateway</html>")).toBe("");
  });
});

describe("apiDetailObject", () => {
  it("returns the object for a structured detail", () => {
    expect(
      apiDetailObject('{"detail": {"error": "owner_quota_exceeded", "owned": 10, "limit": 10}}'),
    ).toEqual({ error: "owner_quota_exceeded", owned: 10, limit: 10 });
  });

  it("returns null for every shape that is not a structured object", () => {
    expect(apiDetailObject('{"detail": "plain sentence"}')).toBeNull();
    expect(apiDetailObject('{"detail": [{"msg": "field required"}]}')).toBeNull();
    expect(apiDetailObject('{"detail": null}')).toBeNull();
    expect(apiDetailObject('{"other": 1}')).toBeNull();
    expect(apiDetailObject("{not json")).toBeNull();
    expect(apiDetailObject("")).toBeNull();
  });
});
