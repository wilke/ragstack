import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { uploadErrorMessage } from "./CollectionView";

// #609. A gowe-backed deployment refuses an upload into a collection whose
// chunk method its out-of-process ingest cannot run. The refusal names the
// methods that WOULD work — and chunk config is build-time identity, so there
// is nothing else on screen that tells the owner what to do next.
//
// The reported user's experience before this: "Upload failed (error 422)."

const DETAIL =
  "chunk_method='semantic' is not yet wired for out-of-process bulk ingest: " +
  "it embeds sentence buffers while chunking and the shard step builds no " +
  "embedding bridge (issue #609). Supported on this path: fixed, fixed_token, " +
  "sentence, words.";

describe("uploadErrorMessage", () => {
  it("shows the server's reason on a 422 that carries one", () => {
    const msg = uploadErrorMessage(new ApiError(422, "{...}", undefined, undefined, DETAIL));
    expect(msg).toBe(DETAIL);
    expect(msg).not.toContain("error 422");
  });

  it("falls back to the status when a 422 carries no string detail", () => {
    // FastAPI's validation 422 has an array detail, which throwForResponse
    // deliberately does not extract. The generic sentence is then correct —
    // better than rendering "[object Object]".
    expect(uploadErrorMessage(new ApiError(422, "{...}"))).toBe("Upload failed (error 422).");
  });

  it("does not let a detail override the statuses that have a better sentence", () => {
    // These paraphrases say more than the server's body does (415 names PDFs,
    // 403 names ownership). A blanket "prefer detail" would regress them.
    expect(uploadErrorMessage(new ApiError(415, "b", undefined, undefined, "nope"))).toContain(
      "not PDFs",
    );
    expect(uploadErrorMessage(new ApiError(403, "b", undefined, undefined, "nope"))).toContain(
      "owner",
    );
    expect(uploadErrorMessage(new ApiError(503, "b", undefined, undefined, "nope"))).toContain(
      "INGEST_ROOT",
    );
  });

  it("still handles a non-ApiError as unreachable", () => {
    expect(uploadErrorMessage(new Error("boom"))).toContain("could not reach the API");
  });
});
