// Client-side mirrors of the contract's argument patterns
// (contracts/ctl/openapi.yaml `x-ctl-op-args`, schemas/job.json).
//
// The daemon validates every one of these again (422 `validation`); the copy
// here exists so a form can refuse to Preview an argument the contract would
// reject, and so a deep link (`#/job/<id>`) cannot carry anything but an id.

/** A tenant name — `x-ctl-op-args.restore.as`, the `{name}` path parameter. */
export const TENANT_NAME = /^[a-z][a-z0-9-]{0,31}$/;

/** A backup bundle id (`<ts>-<kind>`) — `x-ctl-op-args.restore.from`. Never a path. */
export const BUNDLE_ID = /^[0-9]{8}T[0-9]{6}Z-(backup|pre-update|recovery)$/;

/** A job id: a ULID (job.json `JobId`). */
export const JOB_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

/** The services `only[]` may name for start / stop / restart. */
export const SERVICES = ["api", "ui", "qdrant", "es", "postgres"] as const;
export type Service = (typeof SERVICES)[number];

/** The legs a backup `scope` may name. */
export const BACKUP_SCOPES = ["config", "state", "stores"] as const;
export type BackupScope = (typeof BACKUP_SCOPES)[number];

export const BACKUP_SECRETS = ["include", "skip", "require"] as const;
export type BackupSecrets = (typeof BACKUP_SECRETS)[number];

/**
 * Why a backup's arguments would be refused, or null — the planner's two
 * rules (go/internal/ctl/ops/backup.go `backupScopeOf`): a scope without
 * `config` restores nothing (an empty scope means all three legs), and a fence
 * is refused with a light (store-less) scope — it would stop the API for a
 * copy it is not taking.
 */
export function backupArgsProblem(a: { fence: boolean; scope: readonly BackupScope[] }): string | null {
  if (a.scope.length > 0 && !a.scope.includes("config")) {
    return "A bundle without config cannot be restored from: add config to the scope.";
  }
  if (a.fence && a.scope.length > 0 && !a.scope.includes("stores")) {
    return "A fence is refused for a light bundle (no stores): clear fence or add stores.";
  }
  return null;
}

/** Why restore arguments would be refused, or null. */
export function restoreArgsProblem(a: { from: string; as: string }): string | null {
  if (!BUNDLE_ID.test(a.from)) {
    return "from: a bundle id such as 20261008T120000Z-backup (an id, never a path).";
  }
  if (!TENANT_NAME.test(a.as)) {
    return "as: a fresh tenant name — lowercase letters, digits and dashes, starting with a letter.";
  }
  return null;
}

// ---------------------------------------------------------------------------
// Credentials (PR-G3.1): `x-ctl-op-args` for key-mint / key-revoke /
// admin-add / admin-remove / sa-create / sa-disable / sa-enable. Each
// `validate*` answers why the value would be refused, or null.
// ---------------------------------------------------------------------------

/** `key-mint.label` and `key-revoke.id`: the ledger id a key is minted and revoked by. */
export const KEY_LABEL = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** `admin-add/remove.subject`: an `issuer:sub` identity (`bvbrc:alice`). */
export const ADMIN_SUBJECT = /^[a-z][a-z0-9]*:[^:\s]{1,128}$/;

/** `sa-*.subject`: colon-free — a service account is never an `issuer:sub` identity. */
export const SERVICE_ACCOUNT_SUBJECT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** `key-mint.tenant_string`: the `API_KEY_TENANTS` principal (`asm-ops`, `svc-asm-web`). */
export const TENANT_STRING = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** The contract's `Role` enum. */
export const ROLES = ["admin", "user"] as const;
export type CredentialRole = (typeof ROLES)[number];

/** `sa-create.purpose` maxLength. */
export const PURPOSE_MAX = 256;

export function validateKeyLabel(v: string): string | null {
  return KEY_LABEL.test(v)
    ? null
    : "label: lowercase letters, digits and dashes, starting with a letter or digit, at most 64.";
}

/** An admin subject (`issuer:sub`). */
export function validateSubject(v: string): string | null {
  return ADMIN_SUBJECT.test(v)
    ? null
    : "subject: issuer:sub — a lowercase issuer, one colon, then up to 128 characters with no colon or space.";
}

/** A service-account subject (no colon). */
export function validateServiceAccountSubject(v: string): string | null {
  return SERVICE_ACCOUNT_SUBJECT.test(v)
    ? null
    : "subject: letters, digits, dot, underscore and dash, starting with a letter or digit, at most 64 — no colon.";
}

export function validateRole(v: string): string | null {
  return (ROLES as readonly string[]).includes(v) ? null : "role: admin or user.";
}

/** Optional; the length is counted in characters, as JSON Schema does. */
export function validatePurpose(v: string): string | null {
  return Array.from(v).length <= PURPOSE_MAX ? null : `purpose: at most ${PURPOSE_MAX} characters.`;
}

/** Optional: empty means "the convention the tenant's own ledger follows". */
export function validateTenantString(v: string): string | null {
  if (v === "") return null;
  return TENANT_STRING.test(v)
    ? null
    : "tenant string: letters, digits, dot, underscore and dash, starting with a letter or digit, at most 64.";
}
