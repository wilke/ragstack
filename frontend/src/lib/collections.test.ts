import { describe, expect, it } from "vitest";
import {
  ID_BLANK_HINT,
  ID_EXPLICIT_HINT,
  collectionCreateMessage,
  collectionDeleteMessage,
  collectionPurgeMessage,
  collectionShareMessage,
  collectionShareRevokeMessage,
  isPublicShare,
  normalizeGranteeSubject,
  purgeConfirmed,
  purgeReportSummary,
  shareGranteeLabel,
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

  it("explains the 403 as the admin-only build-spec override, not creation itself", () => {
    // Creation is open to any principal (ADR-0003); the only create-path 403 is
    // supplying embedding/chunk without the admin role — the message must steer
    // toward the server-default path, not claim creation needs an admin key.
    expect(collectionCreateMessage(403, "")).toContain("admin-only");
    expect(collectionCreateMessage(403, "")).toContain("Server default");
    expect(collectionCreateMessage(401, "")).toContain("API key or login");
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

  it("names ownership, not admin keys, for 403 (owner-or-admin delete per #243)", () => {
    expect(collectionDeleteMessage(403, "")).toContain("owner");
    expect(collectionDeleteMessage(401, "")).toContain("API key or login");
  });
});

describe("collectionPurgeMessage", () => {
  it("passes the shared-store 409 through verbatim so the other collections are named", () => {
    const body =
      '{"detail":"cannot purge collection \'a\': its physical store (phys) is also used by \'b\', \'c\', and purging would destroy their data too."}';
    const msg = collectionPurgeMessage(409, body);
    expect(msg).toContain("'b', 'c'");
    expect(msg).toContain("phys");
    expect(msg).not.toContain("{");
  });

  it("passes the default-collection 409 through too", () => {
    expect(collectionPurgeMessage(409, '{"detail":"cannot delete the default collection"}')).toBe(
      "cannot delete the default collection",
    );
  });

  it("says the store was untouched on a 404", () => {
    expect(collectionPurgeMessage(404, "")).toMatch(/not touched/i);
  });

  it("names ownership, not admin keys, for 403 (owner-or-admin purge per #243)", () => {
    expect(collectionPurgeMessage(403, "")).toContain("owner");
    expect(collectionPurgeMessage(401, "")).toContain("API key or login");
  });

  it("distinguishes an unreachable API from an HTTP status", () => {
    expect(collectionPurgeMessage(null, "")).toContain("could not reach the API");
    expect(collectionPurgeMessage(500, "")).toContain("error 500");
  });
});

describe("collectionShareMessage", () => {
  it("names ownership, not admin keys, for 403 (owner-or-admin share per #243)", () => {
    expect(collectionShareMessage(403, "")).toContain("owner");
    expect(collectionShareMessage(401, "")).toContain("API key or login");
  });

  it("treats 404 as not-found/not-visible rather than leaking existence", () => {
    const msg = collectionShareMessage(404, "");
    expect(msg).toMatch(/not found|can't see/i);
  });

  it("says a duplicate grant already exists on a bare 409", () => {
    expect(collectionShareMessage(409, "")).toContain("already exists");
  });

  it("unwraps the server's sentence for a 409, not the raw body", () => {
    const body = '{"detail":"\'bvbrc:alice\' already has an active read grant"}';
    const msg = collectionShareMessage(409, body);
    expect(msg).toContain("already has an active read grant");
    expect(msg).not.toContain("{");
  });

  it("surfaces the server's own 422 reason (empty grantee / non-read permission)", () => {
    const body = '{"detail":"v1 shares are read-only; permission \'write\' is not allowed"}';
    const msg = collectionShareMessage(422, body);
    expect(msg).toContain("read-only");
    expect(msg).not.toContain("{");
  });

  it("names the store outage for a 503 (fail closed)", () => {
    expect(collectionShareMessage(503, "")).toMatch(/authorization store|unavailable/i);
  });

  it("reports a transport failure distinctly from an HTTP status", () => {
    expect(collectionShareMessage(null, "")).toContain("could not reach the API");
    expect(collectionShareMessage(500, "")).toContain("error 500");
  });
});

describe("collectionShareRevokeMessage", () => {
  it("names ownership for a 403", () => {
    expect(collectionShareRevokeMessage(403, "")).toContain("owner");
    expect(collectionShareRevokeMessage(401, "")).toContain("API key or login");
  });

  it("treats 404 as already gone", () => {
    expect(collectionShareRevokeMessage(404, "")).toContain("already gone");
  });

  it("passes a 409 detail (the owner row can't be revoked) through, unwrapped", () => {
    const body = '{"detail":"the owner row is not revocable via the share API"}';
    const msg = collectionShareRevokeMessage(409, body);
    expect(msg).toContain("owner row is not revocable");
    expect(msg).not.toContain("{");
  });

  it("distinguishes an unreachable API from an HTTP status", () => {
    expect(collectionShareRevokeMessage(null, "")).toContain("could not reach the API");
    expect(collectionShareRevokeMessage(500, "")).toContain("error 500");
  });
});

describe("normalizeGranteeSubject", () => {
  // Mirrors the server's _resolve_grantee so the dialog can preview the stored
  // subject before the round-trip; the server stays authoritative.
  it("prefixes a bare username with the default bvbrc issuer", () => {
    expect(normalizeGranteeSubject("alice")).toBe("bvbrc:alice");
  });

  it("keeps a full issuer:subject string verbatim", () => {
    expect(normalizeGranteeSubject("oidc:alice@example.org")).toBe("oidc:alice@example.org");
  });

  it("maps the public literals to the canonical @public, never issuer-prefixed", () => {
    expect(normalizeGranteeSubject("@public")).toBe("@public");
    expect(normalizeGranteeSubject("public")).toBe("@public");
  });

  it("honours an explicit issuer for a bare username", () => {
    expect(normalizeGranteeSubject("alice", "oidc")).toBe("oidc:alice");
  });

  it("forgives surrounding whitespace from a paste", () => {
    expect(normalizeGranteeSubject("  alice\n")).toBe("bvbrc:alice");
  });

  it("resolves empty/whitespace input to the empty string (blocks the Grant button)", () => {
    expect(normalizeGranteeSubject("")).toBe("");
    expect(normalizeGranteeSubject("   ")).toBe("");
  });
});

describe("isPublicShare / shareGranteeLabel", () => {
  it("recognises the public group row and labels it for humans", () => {
    const pub = { grantee_type: "group", grantee_id: "public" };
    expect(isPublicShare(pub)).toBe(true);
    expect(shareGranteeLabel(pub)).toContain("Everyone");
  });

  it("treats a user grant as non-public and shows its subject", () => {
    const user = { grantee_type: "user", grantee_id: "bvbrc:alice" };
    expect(isPublicShare(user)).toBe(false);
    expect(shareGranteeLabel(user)).toBe("bvbrc:alice");
  });
});

describe("purgeConfirmed", () => {
  // The gate on an irreversible, GPU-expensive delete. Anything looser than an
  // exact id match would make it a click-through.
  it("unlocks only on the exact id", () => {
    expect(purgeConfirmed("my-corpus", "my-corpus")).toBe(true);
    expect(purgeConfirmed("my-corpu", "my-corpus")).toBe(false);
    expect(purgeConfirmed("My-Corpus", "my-corpus")).toBe(false);
    expect(purgeConfirmed("", "my-corpus")).toBe(false);
    expect(purgeConfirmed("delete", "my-corpus")).toBe(false);
  });

  it("forgives surrounding whitespace from a paste", () => {
    expect(purgeConfirmed("  my-corpus\n", "my-corpus")).toBe(true);
  });

  it("never unlocks for an empty id", () => {
    expect(purgeConfirmed("", "")).toBe(false);
    expect(purgeConfirmed("   ", "")).toBe(false);
  });
});

describe("purgeReportSummary", () => {
  const base = { collection_id: "gone", store: "phys_gone" };

  it("names every target that was removed", () => {
    const msg = purgeReportSummary({
      ...base,
      deleted: ["registry", "vectors", "text_index", "manifest"],
      absent: [],
      failed: [],
      ok: true,
    });
    expect(msg).toContain("Qdrant collection");
    expect(msg).toContain("Elasticsearch index");
    expect(msg).toContain("provenance manifest");
    expect(msg).toContain("phys_gone");
  });

  it("admits which targets were already gone rather than claiming a deletion", () => {
    const msg = purgeReportSummary({
      ...base,
      deleted: ["registry"],
      absent: ["vectors", "manifest"],
      failed: [],
      ok: true,
    });
    expect(msg).toContain("Already absent");
    expect(msg).toContain("Qdrant collection");
  });

  it("leads with the failure on a partial purge and says nothing was rolled back", () => {
    const msg = purgeReportSummary({
      ...base,
      deleted: ["registry", "vectors"],
      absent: [],
      failed: [{ target: "text_index", error: "ConnectionError: refused" }],
      ok: false,
    });
    expect(msg).toContain("Partly deleted");
    expect(msg).toContain("COULD NOT delete the Elasticsearch index");
    expect(msg).toContain("ConnectionError: refused");
    expect(msg).toMatch(/rolled back/);
    expect(msg).toMatch(/by hand/);
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
