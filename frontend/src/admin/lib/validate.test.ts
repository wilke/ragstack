import { describe, expect, it } from "vitest";
import {
  PURPOSE_MAX,
  validateKeyLabel,
  validatePurpose,
  validateRole,
  validateServiceAccountSubject,
  validateSubject,
  validateTenantString,
} from "./validate";

// The credential argument patterns of contracts/ctl/openapi.yaml
// `x-ctl-op-args` (key-mint, key-revoke, admin-add/remove, sa-*): valid,
// invalid and the edges of each.

describe("validateKeyLabel (key-mint.label, key-revoke.id: ^[a-z0-9][a-z0-9-]{0,63}$)", () => {
  it("accepts", () => {
    for (const v of ["a", "0", "ingest-worker", "asm-ops", "k-01", "a".repeat(64), "9-"]) {
      expect(validateKeyLabel(v)).toBeNull();
    }
  });
  it("refuses", () => {
    for (const v of ["", "-a", "A", "Ingest", "a_b", "a.b", "a b", "a".repeat(65), "ä", "a\n", "a:b"]) {
      expect(validateKeyLabel(v)).not.toBeNull();
    }
  });
});

describe("validateSubject (admin-add/remove.subject: ^[a-z][a-z0-9]*:[^:\\s]{1,128}$)", () => {
  it("accepts", () => {
    for (const v of ["bvbrc:alice", "oidc2:a@b.org", "b:x", `bvbrc:${"x".repeat(128)}`, "bvbrc:Alice-Smith_1"]) {
      expect(validateSubject(v)).toBeNull();
    }
  });
  it("refuses", () => {
    for (const v of [
      "",
      "alice",
      "bvbrc:",
      ":alice",
      "1bvbrc:alice",
      "BVBRC:alice",
      "bv-brc:alice",
      "bvbrc:al:ice",
      "bvbrc:al ice",
      "bvbrc:alice\t",
      `bvbrc:${"x".repeat(129)}`,
    ]) {
      expect(validateSubject(v)).not.toBeNull();
    }
  });
});

describe("validateServiceAccountSubject (sa-*.subject: ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$)", () => {
  it("accepts", () => {
    for (const v of ["svc-asm-web", "S", "svc.batch_01", "9", "a".repeat(64)]) {
      expect(validateServiceAccountSubject(v)).toBeNull();
    }
  });
  it("refuses, including an issuer:sub identity", () => {
    for (const v of ["", "bvbrc:alice", "-svc", ".svc", "svc web", "a".repeat(65), "svc/web"]) {
      expect(validateServiceAccountSubject(v)).not.toBeNull();
    }
  });
});

describe("validateRole (Role: admin | user)", () => {
  it("accepts", () => {
    expect(validateRole("admin")).toBeNull();
    expect(validateRole("user")).toBeNull();
  });
  it("refuses", () => {
    for (const v of ["", "Admin", "operator", "viewer", " user", "user "]) {
      expect(validateRole(v)).not.toBeNull();
    }
  });
});

describe("validatePurpose (sa-create.purpose: maxLength 256)", () => {
  it("accepts empty, ordinary and exactly-256", () => {
    expect(validatePurpose("")).toBeNull();
    expect(validatePurpose("the ASM web front end")).toBeNull();
    expect(validatePurpose("x".repeat(PURPOSE_MAX))).toBeNull();
    // Characters, not UTF-16 units: 256 astral characters are still 256.
    expect(validatePurpose("😀".repeat(PURPOSE_MAX))).toBeNull();
  });
  it("refuses 257", () => {
    expect(validatePurpose("x".repeat(PURPOSE_MAX + 1))).not.toBeNull();
  });
});

describe("validateTenantString (key-mint.tenant_string: optional, ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$)", () => {
  it("accepts empty (the ledger's own convention) and the adopted values", () => {
    for (const v of ["", "asm-ops", "svc-asm-web", "asm-ro", "A.b_c-1", "x".repeat(64)]) {
      expect(validateTenantString(v)).toBeNull();
    }
  });
  it("refuses", () => {
    for (const v of ["-asm", "_asm", "asm ops", "asm:ops", "x".repeat(65), "asm/ops"]) {
      expect(validateTenantString(v)).not.toBeNull();
    }
  });
});
