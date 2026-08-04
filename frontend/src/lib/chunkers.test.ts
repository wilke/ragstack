import { describe, expect, it } from "vitest";
import {
  CHUNK_METHODS,
  DEFAULT_CHUNK_FORM,
  apiDetail,
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
