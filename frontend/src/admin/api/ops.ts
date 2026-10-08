// Every mutation the admin UI can make, and the one secret read.
//
// The per-request key rule (contracts/ctl/openapi.yaml, info.description):
//
//   * A session authenticates READS ONLY. Each mutation re-presents a ctl API
//     key in its BODY as `ctl_api_key`; the session header still rides along as
//     the request's authentication, and the daemon requires the key to belong
//     to the session's subject (403 otherwise — not 401, so a mistyped key does
//     not sign the operator out).
//   * The key arrives here as a FUNCTION ARGUMENT (`req.ctlKey`) typed by the
//     operator for this one call. It is copied into the request body and
//     nowhere else: not a header, not a module variable, not storage, not a
//     log line, not the thrown error (CtlError carries the server's answer,
//     never the request). The caller wipes its own copy after the submit.
//   * `GET /v1/jobs/{id}/secrets` is the exception: it refuses sessions, so
//     `fetchSecrets` sends the key as `X-API-Key` INSTEAD of the session header
//     (http.ts `authHeaders` replaces, never merges) and a 401 there is about
//     the key, not the session, so it does not sign anyone out.
//
// The answer of a mutation is decided by its status: 200 is a Plan (dry run),
// 202 is a Job with `Location: /v1/jobs/{id}`. Refusals (409 `plan_stale`,
// `locked`, `doctor_red`, `duplicate`, `refused`; 428 `confirm_required`; 422
// `validation`) throw `CtlError`, whose `extra` carries the specifics —
// lib/opflow.ts maps each one to the next screen.

import { CtlError, get, send } from "./http";
import type {
  CreateArgs,
  Job,
  Plan,
  SecretsResponse,
  SettingsResponse,
  TenantOpVerb,
} from "./types";

/** op_request.json's `idempotency_key` pattern. */
export const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]{8,128}$/;

/** One mutation call, as the UI holds it for exactly as long as the call. */
export interface MutationRequest<A = Record<string, unknown>> {
  /** The verb's typed arguments (`x-ctl-op-args[verb]`); `{}` for none. */
  args: A;
  /** The operator's ctl API key, typed for THIS request. Goes in the body only. */
  ctlKey: string;
  dryRun: boolean;
  /** The plan's `confirm_value`, when the plan said `requires_confirm`. */
  confirm?: string;
  /** A YELLOW plan's `doctor.hash`, to proceed past its warnings. */
  forceWithDoctorDiff?: string;
  /** From `newIdempotencyKey`; the same key for the dry run and the execute. */
  idempotencyKey: string;
  signal?: AbortSignal;
}

/** Continuations, the gateway apply and settings take no verb args by default. */
export type BareMutationRequest = Omit<MutationRequest, "args"> & { args?: Record<string, unknown> };

export type MutationOutcome =
  | { kind: "plan"; plan: Plan }
  | { kind: "job"; job: Job; location: string | null };

export type JobContinuation = "resume" | "continue" | "cancel";

type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K] };

/**
 * What `PUT /v1/settings` accepts as `args`: a PARTIAL settings document, and
 * only `retention`, `images` and `ctl` — `recipients`, `python_env_default`
 * and `registry_generation` are CLI-only (409 `refused`).
 */
export type SettingsPatch = DeepPartial<Pick<SettingsResponse, "retention" | "images" | "ctl">>;

function clientValidation(detail: string): CtlError {
  // Status 0: refused in the browser, never sent. Same type as a server
  // refusal so one ErrorBanner/OpFlow path renders both.
  return new CtlError({ status: 0, code: "validation", detail, requestId: null });
}

/**
 * The `op_request` / `create_request` envelope.
 *
 * Refuses (throws, sends nothing) without a key: the contract makes
 * `ctl_api_key` mandatory over a session, and a mutation that left the browser
 * without one would be a 403 at best and a habit at worst.
 */
function envelope(req: MutationRequest<unknown>): Record<string, unknown> {
  if (typeof req.ctlKey !== "string" || req.ctlKey.length === 0) {
    throw clientValidation("a mutation needs the ctl API key");
  }
  if (!IDEMPOTENCY_KEY_PATTERN.test(req.idempotencyKey)) {
    throw clientValidation("idempotency_key does not match the contract pattern");
  }
  const body: Record<string, unknown> = {
    dry_run: req.dryRun,
    idempotency_key: req.idempotencyKey,
    ctl_api_key: req.ctlKey,
    args: req.args ?? {},
  };
  if (req.confirm !== undefined) body.confirm = req.confirm;
  if (req.forceWithDoctorDiff !== undefined) body.force_with_doctor_diff = req.forceWithDoctorDiff;
  return body;
}

async function mutate(
  method: "POST" | "PUT",
  path: string,
  req: MutationRequest<unknown>,
): Promise<MutationOutcome> {
  const r = await send<Plan | Job>(path, { method, body: envelope(req), signal: req.signal });
  if (r.status === 200 && r.body) return { kind: "plan", plan: r.body as Plan };
  if (r.status === 202 && r.body) return { kind: "job", job: r.body as Job, location: r.location };
  // Any other 2xx is outside the contract; say so rather than guess.
  throw new CtlError({
    status: r.status,
    code: null,
    detail: `unexpected ${r.status} from a mutation`,
    requestId: null,
  });
}

const seg = encodeURIComponent;

/** `POST /v1/tenants/{name}/ops/{verb}` — dry run → Plan, execute → Job. */
export function submitOp(
  name: string,
  verb: TenantOpVerb,
  req: MutationRequest,
): Promise<MutationOutcome> {
  return mutate("POST", `/v1/tenants/${seg(name)}/ops/${seg(verb)}`, req);
}

/** `POST /v1/tenants` — commission a tenant (`create_request`, typed `CreateArgs`). */
export function createTenant(req: MutationRequest<CreateArgs>): Promise<MutationOutcome> {
  return mutate("POST", "/v1/tenants", req);
}

/** `POST /v1/jobs/{id}/{resume|continue|cancel}` — re-authorized like any mutation. */
export function jobContinuation(
  id: string,
  action: JobContinuation,
  req: BareMutationRequest,
): Promise<MutationOutcome> {
  return mutate("POST", `/v1/jobs/${seg(id)}/${action}`, { ...req, args: req.args ?? {} });
}

/** `POST /v1/gateway/apply` — publish the next gateway generation. */
export function applyGateway(req: BareMutationRequest): Promise<MutationOutcome> {
  return mutate("POST", "/v1/gateway/apply", { ...req, args: req.args ?? {} });
}

/** `PUT /v1/settings` — `args` is the partial settings document. */
export function putSettings(req: MutationRequest<SettingsPatch>): Promise<MutationOutcome> {
  return mutate("PUT", "/v1/settings", req);
}

/**
 * `GET /v1/jobs/{id}/secrets` — the minted values, delivered ONCE (a second
 * read is 410) within 15 minutes of the job.
 *
 * The key goes in `X-API-Key` and the session header is NOT sent (the endpoint
 * refuses a session with 403; both would be 400). `skipUnauthorizedHandler`:
 * a 401 here means the key was wrong, and the session is fine.
 *
 * The result must not enter the React Query cache or any state that outlives
 * the reveal; RevealOnce owns it and clears it on unmount.
 */
export async function fetchSecrets(
  jobId: string,
  ctlKey: string,
  signal?: AbortSignal,
): Promise<SecretsResponse> {
  if (typeof ctlKey !== "string" || ctlKey.length === 0) {
    throw clientValidation("reading secrets needs the ctl API key");
  }
  const r = await send<SecretsResponse>(`/v1/jobs/${seg(jobId)}/secrets`, {
    method: "GET",
    authHeaders: { "X-API-Key": ctlKey },
    skipUnauthorizedHandler: true,
    signal,
  });
  if (!r.body) {
    throw new CtlError({ status: r.status, code: null, detail: "empty secrets body", requestId: null });
  }
  return r.body;
}

/** Re-read a job (also available as `useJob` for polling). */
export function fetchJob(id: string, signal?: AbortSignal): Promise<Job> {
  return get<Job>(`/v1/jobs/${seg(id)}`, signal);
}

function uuid(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  // RFC 4122 v4 from getRandomValues — randomUUID needs a secure context, and
  // a dev server reached over plain http on a LAN address is not one.
  const b = new Uint8Array(16);
  c.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/**
 * A fresh idempotency key, `ui-<prefix>-<uuid>`, valid against op_request.json.
 *
 * One per intended mutation: the dry run and the execute share it, and a retry
 * of the execute under it returns the original job instead of a second one.
 * `prefix` names the op for the audit reader (`start`, `create`, `gateway`);
 * characters outside the contract's alphabet are replaced with `_`.
 */
export function newIdempotencyKey(prefix: string): string {
  const p = prefix.replace(/[^A-Za-z0-9._:-]/g, "_").slice(0, 64);
  const key = `ui-${p}-${uuid()}`;
  if (!IDEMPOTENCY_KEY_PATTERN.test(key)) {
    throw new Error("generated idempotency key does not match the contract pattern");
  }
  return key;
}
