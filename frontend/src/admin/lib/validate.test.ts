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

// ---------------------------------------------------------------------------
// PR-F F6: `x-ctl-op-args.update-code` and `CreateArgs.image` — the
// planner's refusals (ops/update.go planUpdateCode, ops/create.go).
// ---------------------------------------------------------------------------

import {
  artifactsAtCommit,
  createImageProblem,
  SERVER_IMAGE_NAME,
  shortSha,
  updateCodeArgsProblem,
  type UpdateCodeInput,
} from "./validate";

const C1 = "4c1322e1430f7ae1d1d869c2917fa01fe1d98fa6";
const C2 = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d";
const B1 = "ragstack-server-v1.6.6-b1.sif";
const B2 = "ragstack-server-v1.6.6-b2.sif";
const N1 = "ragstack-server-v1.6.7-b1.sif";
const IMAGES = [
  { name: B1, commit: C1 },
  { name: B2, commit: C1 },
  { name: N1, commit: C2 },
];
const ARTS = [
  { id: "v1.6.6", sha: C1 },
  { id: "v1.6.2", sha: "0123456789abcdef0123456789abcdef01234567" },
];
const OK: UpdateCodeInput = { image: B2, rebuildUi: true, artifactId: "v1.6.6", uiMode: "static", currentImage: B1 };

describe("SERVER_IMAGE_NAME (registry.json ServerImageName)", () => {
  it("accepts the build script's names, +sha dev builds included", () => {
    for (const v of [B1, "ragstack-server-v1.6.6+4c1322e-b3.sif", "ragstack-server-1.7.0rc1-b12.sif"]) {
      expect(SERVER_IMAGE_NAME.test(v), v).toBe(true);
    }
    for (const v of ["ragstack-server-v1.6.6.sif", "ragstack-tools-v1.6.6-b1.sif", "../ragstack-server-v1-b1.sif", "ragstack-server-v1-b1.sif.bak", ""]) {
      expect(SERVER_IMAGE_NAME.test(v), v).toBe(false);
    }
  });
});

describe("updateCodeArgsProblem", () => {
  it("accepts image → image with a rebuild from an artifact at the image's commit", () => {
    expect(updateCodeArgsProblem(OK, IMAGES, ARTS)).toBeNull();
  });

  it("accepts the API-only patch (no rebuild, no artifact) and the worktree migration", () => {
    expect(updateCodeArgsProblem({ ...OK, rebuildUi: false, artifactId: "" }, IMAGES, ARTS)).toBeNull();
    expect(updateCodeArgsProblem({ ...OK, currentImage: null }, IMAGES, ARTS)).toBeNull();
    expect(updateCodeArgsProblem({ ...OK, uiMode: "external", rebuildUi: false, artifactId: "" }, IMAGES, ARTS)).toBeNull();
  });

  it("requires a prepared image with a valid name", () => {
    expect(updateCodeArgsProblem({ ...OK, image: "" }, IMAGES, ARTS)).toMatch(/Choose/);
    expect(updateCodeArgsProblem({ ...OK, image: "ragstack-server-x.sif" }, IMAGES, ARTS)).toMatch(/not a server image name/);
    expect(updateCodeArgsProblem({ ...OK, image: "ragstack-server-v9-b1.sif" }, IMAGES, ARTS)).toMatch(/not prepared/);
    // Not loaded yet: the membership check waits, the server decides.
    expect(updateCodeArgsProblem({ ...OK, image: "ragstack-server-v9-b1.sif" }, null, null)).toBeNull();
  });

  it("a rebuild needs a static UI and an artifact at the image's commit", () => {
    expect(updateCodeArgsProblem({ ...OK, uiMode: "external" }, IMAGES, ARTS)).toMatch(/static build/);
    expect(updateCodeArgsProblem({ ...OK, artifactId: "" }, IMAGES, ARTS)).toMatch(new RegExp(shortSha(C1)));
    expect(updateCodeArgsProblem({ ...OK, artifactId: "v9.9.9" }, IMAGES, ARTS)).toMatch(/not a prepared artifact/);
    const mismatch = updateCodeArgsProblem({ ...OK, artifactId: "v1.6.2" }, IMAGES, ARTS);
    expect(mismatch).toMatch(/different code/);
    expect(mismatch).toContain(shortSha(C1));
    // No artifact is at N1's commit.
    expect(updateCodeArgsProblem({ ...OK, image: N1, artifactId: "v1.6.6" }, IMAGES, ARTS)).toMatch(/different code/);
  });

  it("an artifact without a rebuild is refused", () => {
    expect(updateCodeArgsProblem({ ...OK, rebuildUi: false }, IMAGES, ARTS)).toMatch(/Rebuild UI is off/);
  });

  it("the same image again is a restart unless the UI is rebuilt", () => {
    expect(updateCodeArgsProblem({ ...OK, image: B1, rebuildUi: false, artifactId: "" }, IMAGES, ARTS)).toMatch(/restart/);
    expect(updateCodeArgsProblem({ ...OK, image: B1 }, IMAGES, ARTS)).toBeNull();
  });
});

describe("createImageProblem", () => {
  it("a static UI needs an artifact at the image's commit; another UI mode does not", () => {
    expect(createImageProblem({ image: B1, artifactId: "v1.6.6", uiMode: "static" }, IMAGES, ARTS)).toBeNull();
    expect(createImageProblem({ image: B1, artifactId: "", uiMode: "static" }, IMAGES, ARTS)).toMatch(/static UI/);
    expect(createImageProblem({ image: B1, artifactId: "", uiMode: "external" }, IMAGES, ARTS)).toBeNull();
    expect(createImageProblem({ image: B1, artifactId: "v1.6.2", uiMode: "external" }, IMAGES, ARTS)).toMatch(/different code/);
    expect(createImageProblem({ image: "", artifactId: "", uiMode: "static" }, IMAGES, ARTS)).toMatch(/Choose/);
    expect(createImageProblem({ image: "ragstack-server-v9-b1.sif", artifactId: "", uiMode: "external" }, IMAGES, ARTS)).toMatch(/not prepared/);
  });

  it("artifactsAtCommit filters by sha", () => {
    expect(artifactsAtCommit(ARTS, C1).map((a) => a.id)).toEqual(["v1.6.6"]);
    expect(artifactsAtCommit(ARTS, C2)).toEqual([]);
  });
});
