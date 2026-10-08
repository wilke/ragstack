import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ADMIN_SUBJECT,
  ES_HEAP,
  RESERVED_TENANT_NAMES,
  SETTING_KEY,
  TENANT_NAME,
  validateAdminSubjects,
  validateESHeap,
  validateSetting,
  validateTenantName,
} from "./validate";

// PR-G3.3: the create wizard's validators. Kept in their own file (not
// validate.test.ts) so the PR-G3.1 credentials validators can land beside them
// without a merge.

const REPO = resolve(__dirname, "../../../..");
const schema = JSON.parse(readFileSync(resolve(REPO, "contracts/ctl/schemas/create_request.json"), "utf8"));
const props = schema.$defs.CreateArgs.properties;

describe("the patterns are the contract's", () => {
  it("mirrors create_request.json and paths.go byte for byte", () => {
    expect(TENANT_NAME.source).toBe(props.name.pattern);
    expect(ADMIN_SUBJECT.source).toBe(props.admin_subjects.items.pattern);
    expect(ES_HEAP.source).toBe(props.es_heap.pattern);
    expect(SETTING_KEY.source).toBe(schema.$defs.PublicSettingKey.pattern);
  });

  it("copies paths.Reserved exactly (same names, same order)", () => {
    const go = readFileSync(resolve(REPO, "go/internal/ctl/paths/paths.go"), "utf8");
    const block = /var Reserved = \[\]string\{([^}]*)\}/.exec(go);
    expect(block).not.toBeNull();
    const names = [...block![1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    expect(RESERVED_TENANT_NAMES).toEqual(names);
  });
});

describe("validateTenantName", () => {
  it("accepts a good name", () => {
    expect(validateTenantName("lab-west")).toBeNull();
    expect(validateTenantName("a")).toBeNull();
    expect(validateTenantName("a" + "b".repeat(31))).toBeNull();
  });

  it("refuses an empty name and a bad grammar", () => {
    expect(validateTenantName("")).toMatch(/empty/);
    for (const bad of ["Lab", "1lab", "-lab", "lab_west", "lab west", "lab.west", "a" + "b".repeat(32), "läb"]) {
      expect(validateTenantName(bad), bad).toMatch(/Lowercase letters/);
    }
  });

  it("refuses every reserved name, with the reason", () => {
    expect(RESERVED_TENANT_NAMES).toHaveLength(23);
    for (const r of RESERVED_TENANT_NAMES) {
      expect(validateTenantName(r), r).toMatch(/is reserved/);
    }
    // A reserved word as a PART of a name is fine.
    expect(validateTenantName("qdrant-lab")).toBeNull();
  });

  it("refuses a selftest sandbox name and an existing tenant", () => {
    expect(validateTenantName("ctltest-x")).toMatch(/selftest sandboxes/);
    expect(validateTenantName("dev", ["dev", "demo"])).toMatch(/already exists/);
    expect(validateTenantName("dev2", ["dev", "demo"])).toBeNull();
  });
});

describe("validateAdminSubjects", () => {
  it("accepts bvbrc subjects and an empty list", () => {
    expect(validateAdminSubjects([], "bvbrc")).toBeNull();
    expect(validateAdminSubjects([], "none")).toBeNull();
    expect(validateAdminSubjects(["bvbrc:alice@patricbrc.org", "bvbrc:bob"], "bvbrc")).toBeNull();
  });

  it("refuses any subject when the provider is none", () => {
    expect(validateAdminSubjects(["bvbrc:alice@patricbrc.org"], "none")).toMatch(/need an identity provider/);
  });

  it("refuses a malformed subject", () => {
    for (const bad of ["alice", "bvbrc:", "bvbrc:a b", "bvbrc:a:b", "BVBRC:alice", ":alice", "bvbrc:" + "x".repeat(129)]) {
      expect(validateAdminSubjects([bad], "bvbrc"), bad).toMatch(/not issuer:subject/);
    }
  });

  it("refuses another issuer and a duplicate", () => {
    expect(validateAdminSubjects(["globus:alice"], "bvbrc")).toMatch(/issued by globus/);
    expect(validateAdminSubjects(["bvbrc:a", "bvbrc:a"], "bvbrc")).toMatch(/listed twice/);
  });
});

describe("validateESHeap", () => {
  it("accepts sizes in m and g", () => {
    for (const ok of ["1g", "512m", "16g", "1024m"]) expect(validateESHeap(ok), ok).toBeNull();
  });
  it("refuses everything else", () => {
    expect(validateESHeap("")).toMatch(/empty/);
    for (const bad of ["0g", "01g", "1G", "1", "1gb", "1.5g", " 1g", "g"]) {
      expect(validateESHeap(bad), bad).toMatch(/size in m or g/);
    }
  });
});

describe("validateSetting", () => {
  it("accepts a public key", () => {
    expect(validateSetting("LOG_LEVEL", "debug")).toEqual({ problem: null, warning: null });
    expect(validateSetting("CHUNK_MAX_TOKENS", "512")).toEqual({ problem: null, warning: null });
  });

  it("refuses a malformed key or value outright", () => {
    expect(validateSetting("", "x").problem).toMatch(/empty/);
    for (const bad of ["log_level", "1LOG", "LOG-LEVEL", "LOG LEVEL", "A" + "B".repeat(128)]) {
      expect(validateSetting(bad, "x").problem, bad).toMatch(/not a setting name/);
    }
    expect(validateSetting("LOG_LEVEL", "a\nb").problem).toMatch(/one line/);
    expect(validateSetting("LOG_LEVEL", "x".repeat(4097)).problem).toMatch(/4096/);
  });

  it("WARNS (does not refuse) on a secret-shaped name", () => {
    for (const k of ["API_KEYS", "ADMIN_API_KEY", "GOWE_TOKEN", "NEO4J_PASSWORD", "USER_STORE_DSN", "CLIENT_SECRET", "BVBRC_AUTH_URL", "SIGNING_KEY"]) {
      const c = validateSetting(k, "x");
      expect(c.problem, k).toBeNull();
      expect(c.warning, k).toMatch(/looks like a secret/);
    }
  });

  it("knows the secret-shaped names that are public", () => {
    for (const k of [
      "CHUNK_MAX_TOKENS",
      "CHUNK_TOKEN_COUNTER",
      "EMBEDDING_MAX_BATCH_TOKENS",
      "EMBEDDING_CHARS_PER_TOKEN",
      "GOWE_RECEIPTS_OUTPUT_KEY",
      "GOWE_SHARDS_INPUT_KEY",
    ]) {
      expect(validateSetting(k, "1").warning, k).toBeNull();
    }
  });

  it("keeps the public exceptions equal to the contract's", () => {
    const listed = schema.$defs.PublicSettingKey.anyOf[0].enum as string[];
    for (const k of listed) expect(validateSetting(k, "1").warning, k).toBeNull();
  });
});
