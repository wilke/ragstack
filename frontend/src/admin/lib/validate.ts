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

// ---------------------------------------------------------------------------
// PR-G3.3: the create wizard's arguments (schemas/create_request.json
// `CreateArgs`; the daemon's checks in go/internal/ctl/ops/create.go
// `planCreateWith` and go/internal/ctl/paths/paths.go `ValidateName`).
// ---------------------------------------------------------------------------

/**
 * `paths.Reserved` (go/internal/ctl/paths/paths.go), copied verbatim: names
 * that collide with a shared instance, a gateway route or a built-in.
 */
export const RESERVED_TENANT_NAMES: readonly string[] = [
  "qdrant", "elasticsearch", "neo4j", "postgres", "redis", "embedding",
  "crossencoder", "faiss", "tenants", "manifest", "default", "public",
  "admin", "services", "health", "ragstack", "api", "ui", "gowe", "vaxpipe",
  "grafana", "sfr", "ctl",
];

/** `ops.sandboxPrefix`: selftest names, which `create` refuses (they belong to `create-sandbox`). */
export const SANDBOX_PREFIX = "ctltest-";

/**
 * Why `name` cannot be a new tenant's name, or null. `paths.ValidateName`
 * (empty, grammar, reserved), then `planCreate`'s sandbox-prefix refusal, then
 * — when the caller knows the fleet — a name the registry already holds.
 */
export function validateTenantName(name: string, existing: readonly string[] = []): string | null {
  if (name === "") return "The tenant name is empty.";
  if (!TENANT_NAME.test(name)) {
    return "Lowercase letters, digits and dashes, starting with a letter, at most 32 characters (^[a-z][a-z0-9-]{0,31}$).";
  }
  if (RESERVED_TENANT_NAMES.includes(name)) {
    return `"${name}" is reserved: it collides with a shared instance, a gateway route or a built-in.`;
  }
  if (name.startsWith(SANDBOX_PREFIX)) {
    return `"${SANDBOX_PREFIX}…" names are selftest sandboxes; create allocates production blocks.`;
  }
  if (existing.includes(name)) return `A tenant named "${name}" already exists.`;
  return null;
}

/* `CreateArgs.admin_subjects[]` uses ADMIN_SUBJECT above (`issuer:sub`). */

/**
 * Why a create's admin subjects would be refused, or null: none at all when
 * the provider is `none`; each one `issuer:sub` with the issuer equal to the
 * provider; no duplicates (`uniqueItems`).
 */
export function validateAdminSubjects(subjects: readonly string[], provider: "bvbrc" | "none"): string | null {
  if (provider === "none") {
    return subjects.length > 0
      ? "Admin subjects need an identity provider: with none there are no bearer subjects to admit."
      : null;
  }
  const seen = new Set<string>();
  for (const s of subjects) {
    if (!ADMIN_SUBJECT.test(s)) {
      return `"${s}" is not issuer:subject (e.g. ${provider}:alice@patricbrc.org).`;
    }
    const issuer = s.slice(0, s.indexOf(":"));
    if (issuer !== provider) return `"${s}" is issued by ${issuer}, but the identity provider is ${provider}.`;
    if (seen.has(s)) return `"${s}" is listed twice.`;
    seen.add(s);
  }
  return null;
}

/** `CreateArgs.es_heap`. */
export const ES_HEAP = /^[1-9][0-9]*[mg]$/;

/** Why an ES heap would be refused, or null (e.g. `1g`, `512m`). */
export function validateESHeap(heap: string): string | null {
  if (heap === "") return "The Elasticsearch heap is empty (the default is 1g).";
  if (!ES_HEAP.test(heap)) return "A size in m or g with no leading zero, e.g. 1g or 512m.";
  return null;
}

/** `PublicSettingKey`'s grammar (and `x-ctl-op-args.env-set.key`). */
export const SETTING_KEY = /^[A-Z][A-Z0-9_]{0,127}$/;

/** `settings.go` `secretPattern`: a secret-shaped NAME. */
const SECRET_SHAPED = /(API_KEY[A-Z_]*|_KEY$|SECRET|PASSWORD|TOKEN|DSN|AUTH)/i;

/** `settings.go` `publicDespitePattern`: secret-shaped names that are public. */
const PUBLIC_DESPITE_PATTERN: readonly string[] = [
  "CHUNK_MAX_TOKENS",
  "CHUNK_TOKEN_COUNTER",
  "EMBEDDING_MAX_BATCH_TOKENS",
  "EMBEDDING_CHARS_PER_TOKEN",
  "GOWE_RECEIPTS_OUTPUT_KEY",
  "GOWE_SHARDS_INPUT_KEY",
];

export interface SettingCheck {
  /** The contract would refuse it outright: the row cannot be sent. */
  problem: string | null;
  /**
   * Sendable, but the daemon will probably refuse it. The UI warns and does
   * NOT enforce the classification — the daemon's table (public / secret /
   * executable-surface) is the authority, and a copy here would drift.
   */
  warning: string | null;
}

/** One `settings` row of a create. */
export function validateSetting(key: string, value: string): SettingCheck {
  if (key === "") return { problem: "The setting name is empty.", warning: null };
  if (!SETTING_KEY.test(key)) {
    return { problem: `"${key}" is not a setting name (^[A-Z][A-Z0-9_]{0,127}$).`, warning: null };
  }
  if (/[\r\n]/.test(value)) return { problem: `${key}: the value must be one line.`, warning: null };
  if (value.length > 4096) return { problem: `${key}: the value is longer than 4096 characters.`, warning: null };
  if (SECRET_SHAPED.test(key) && !PUBLIC_DESPITE_PATTERN.includes(key)) {
    return {
      problem: null,
      warning: `${key} looks like a secret: create accepts public settings only and will refuse it. Secrets are minted by the ctl or set on the CLI.`,
    };
  }
  return { problem: null, warning: null };
}

// ---------------------------------------------------------------------------
// PR-F: server images — `x-ctl-op-args.update-code` and `CreateArgs.image`
// (go/internal/ctl/ops/update.go `planUpdateCode`, ops/create.go). Each rule
// is the planner's own refusal, mirrored so a form can hold Preview back; the
// daemon decides.
// ---------------------------------------------------------------------------

/** `ServerImageName` (registry.json): `apptainer/build-image.sh --kind server`'s file name. */
export const SERVER_IMAGE_NAME = /^ragstack-server-[A-Za-z0-9._+-]+-b[0-9]+\.sif$/;

/** `ArtifactId` (create_request.json / x-ctl-op-args.update-code.artifact_id). */
export const ARTIFACT_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;

type UIMode = "static" | "dev" | "external";
interface ImageLike {
  name: string;
  commit: string;
}
interface ArtifactLike {
  id: string;
  sha: string;
}

/** A commit as the UI names it: the first 12 hex digits. */
export function shortSha(sha: string): string {
  return sha.slice(0, 12);
}

/** The prepared artifacts whose `sha` is `commit` — the only ones a UI rebuild for that image may use. */
export function artifactsAtCommit<A extends ArtifactLike>(artifacts: readonly A[], commit: string): A[] {
  return artifacts.filter((a) => a.sha === commit);
}

/** Why an image name would be refused before the registry is consulted, or null. */
function imageNameProblem(image: string, images: readonly ImageLike[] | null): string | null {
  if (image === "") return "Choose a prepared server image.";
  if (!SERVER_IMAGE_NAME.test(image)) return `"${image}" is not a server image name (ragstack-server-<version>-b<N>.sif).`;
  if (images && !images.some((i) => i.name === image)) {
    return `"${image}" is not prepared on this host (ragstack-ctl fleet image prepare --sif …).`;
  }
  return null;
}

/** An artifact chosen for `image` must exist and sit at the image's commit. */
function artifactForImageProblem(
  artifactId: string,
  image: ImageLike | undefined,
  artifacts: readonly ArtifactLike[] | null,
): string | null {
  if (!ARTIFACT_ID.test(artifactId)) return `"${artifactId}" is not an artifact id.`;
  const a = artifacts?.find((x) => x.id === artifactId);
  if (artifacts && !a) return `"${artifactId}" is not a prepared artifact.`;
  if (a && image && a.sha !== image.commit) {
    return `Artifact ${artifactId} is at commit ${shortSha(a.sha)}, but ${image.name} was built at ${shortSha(image.commit)}: the UI and the API would come from different code.`;
  }
  return null;
}

export interface UpdateCodeInput {
  image: string;
  /** The EFFECTIVE choice (the form's, or the default `uiMode === "static"`). */
  rebuildUi: boolean;
  /** "" = none. */
  artifactId: string;
  /** The tenant's registry `ui.mode`. */
  uiMode: UIMode;
  /** The image the tenant runs now (registry `server_image.name`), or null in worktree mode. */
  currentImage: string | null;
}

/**
 * Why an `update-code` would be refused at plan time, or null. `images` /
 * `artifacts` null = not loaded yet (the membership checks are skipped).
 */
export function updateCodeArgsProblem(
  a: UpdateCodeInput,
  images: readonly ImageLike[] | null,
  artifacts: readonly ArtifactLike[] | null,
): string | null {
  const nameProblem = imageNameProblem(a.image, images);
  if (nameProblem) return nameProblem;
  const image = images?.find((i) => i.name === a.image);
  if (a.rebuildUi && a.uiMode !== "static") {
    return `Rebuild UI asks for a static build, and this tenant's UI is ${a.uiMode}: turn it off (or set the UI mode to static first).`;
  }
  if (a.rebuildUi) {
    if (a.artifactId === "") {
      return image
        ? `Rebuilding the UI needs a prepared artifact at the image's commit ${shortSha(image.commit)}: choose one, or turn Rebuild UI off.`
        : "Rebuilding the UI needs a prepared artifact at the image's commit: choose one, or turn Rebuild UI off.";
    }
    const p = artifactForImageProblem(a.artifactId, image, artifacts);
    if (p) return p;
  } else if (a.artifactId !== "") {
    return "An artifact is the source of a UI rebuild, and Rebuild UI is off: nothing would read it.";
  }
  if (!a.rebuildUi && a.currentImage === a.image) {
    return `The tenant already runs ${a.image}: without a UI rebuild this would be a restart, not an upgrade (restart --only api).`;
  }
  return null;
}

export interface CreateImageInput {
  image: string;
  /** "" = none. */
  artifactId: string;
  uiMode: UIMode;
}

/**
 * Why a create in image mode would be refused, or null: a prepared image; an
 * artifact at the image's commit when the UI is static (it is built from
 * that artifact); any artifact given must sit at that commit.
 */
export function createImageProblem(
  a: CreateImageInput,
  images: readonly ImageLike[] | null,
  artifacts: readonly ArtifactLike[] | null,
): string | null {
  const nameProblem = imageNameProblem(a.image, images);
  if (nameProblem) return nameProblem;
  const image = images?.find((i) => i.name === a.image);
  if (a.artifactId === "") {
    if (a.uiMode !== "static") return null;
    return image
      ? `A static UI is built from a prepared artifact at the image's commit ${shortSha(image.commit)}: choose one (or choose ui_mode external under Options).`
      : "A static UI is built from a prepared artifact at the image's commit: choose one (or choose ui_mode external under Options).";
  }
  return artifactForImageProblem(a.artifactId, image, artifacts);
}
