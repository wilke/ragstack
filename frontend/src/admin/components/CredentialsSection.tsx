// TenantView's Credentials section: the tenant API's keys, its admin subjects
// and its service accounts — what exists, and (one OpFlow each) minting,
// revoking, adding, removing, creating, disabling and enabling.
//
// OPERATOR ONLY, and absent rather than disabled for a viewer: TenantView does
// not list the section for a viewer, `TenantSection` answers the 403 panel if
// it is reached anyway, and `CredentialsView` renders nothing for a viewer
// role. (A viewer's tenant body has `registry: null`, so there is nothing to
// list in the first place.)
//
// What may be rendered:
//
//   * Keys by FINGERPRINT (`sha256:<16 hex>`) and the registry's descriptive
//     fields — never a key value. Every column is picked by name from the
//     `KeyRecord`, so a member the server should never have sent (`api_key`,
//     `value`) is never reached. A minted value reaches the screen only
//     through OpFlow's `RevealOnceCard` after the job succeeded and the
//     operator clicked: `key-mint` is in `SECRET_BEARING_OPS`.
//   * Admin subjects from the PUBLIC setting `ADMIN_SUBJECTS` (tenant.env, via
//     `GET /v1/tenants/{name}/env`) — identities, not secrets. When the env is
//     withheld or the key is not public, only the registry's count is shown.
//
// The destructive set mirrors the daemon's (go/internal/ctl/ops/ops.go
// `add(verb, destructive, …)`): key-revoke, admin-remove, sa-disable and the
// restart. Each takes the typed confirm, whose value is the tenant name.
//
// `CredentialsView` is the props-only seam (the form state and the open
// operation are props); `CredentialsSection` reads the tenant and its env and
// holds that state.

import { useState } from "react";
import { submitOp } from "../api/ops";
import { ctlKeys, useCtlQuery } from "../api/queries";
import type {
  CtlEnv,
  CtlRole,
  CtlTenant,
  Job,
  KeyRecord,
  ServiceAccountRecord,
  TenantOpVerb,
} from "../api/types";
import { since } from "../lib/format";
import {
  ADMIN_SUBJECT,
  KEY_LABEL,
  ROLES,
  SERVICE_ACCOUNT_SUBJECT,
  validateKeyLabel,
  validatePurpose,
  validateRole,
  validateServiceAccountSubject,
  validateSubject,
  validateTenantString,
  type CredentialRole,
} from "../lib/validate";
import { ErrorBanner } from "./ErrorBanner";
import { OperatorRequired } from "./JobsView";
import { OpFlow } from "./OpFlow";

// ---------------------------------------------------------------------------
// Forms and the argument building (pure)
// ---------------------------------------------------------------------------

export type Supervisor = CtlTenant["summary"]["supervisor"];

export interface MintForm {
  label: string;
  role: CredentialRole;
  tenantString: string;
  restart: boolean;
  prove: boolean;
}

/** key-revoke's options (the id comes from the row). */
export interface RevokeForm {
  restart: boolean;
  prove: boolean;
}

export interface ServiceAccountForm {
  subject: string;
  role: CredentialRole;
  purpose: string;
}

export interface CredentialForms {
  mint: MintForm;
  revoke: RevokeForm;
  adminSubject: string;
  sa: ServiceAccountForm;
}

// `user` by default: the lesser role is the one a slip should produce.
export const EMPTY_CREDENTIAL_FORMS: CredentialForms = {
  mint: { label: "", role: "user", tenantString: "", restart: false, prove: false },
  revoke: { restart: false, prove: false },
  adminSubject: "",
  sa: { subject: "", role: "user", purpose: "" },
};

/** The one operation open on the section, if any. */
export type CredOp =
  | { kind: "key-mint" }
  | { kind: "key-revoke"; id: string }
  | { kind: "admin-add" }
  | { kind: "admin-remove"; subject: string }
  | { kind: "sa-create" }
  | { kind: "sa-disable"; subject: string }
  | { kind: "sa-enable"; subject: string }
  | { kind: "restart-api" };

/** The daemon's destructive flag for each verb this section submits. */
export const DESTRUCTIVE: Readonly<Record<CredOp["kind"], boolean>> = {
  "key-mint": false,
  "key-revoke": true,
  "admin-add": false,
  "admin-remove": true,
  "sa-create": false,
  "sa-disable": true,
  "sa-enable": false,
  "restart-api": true,
};

/**
 * Why the ctl cannot restart this tenant, or null. A `manual` tenant was
 * started by hand: the plan refuses `restart: true` on key-mint/key-revoke and
 * the restart op itself (`requireSupervised`).
 */
export function restartRefusal(supervisor: Supervisor | null | undefined): string | null {
  return supervisor === "manual"
    ? "This tenant is supervisor: manual — the ctl cannot restart an API somebody else started. Restart it the way it was started; until then the change stays pending."
    : null;
}

/** key-mint's typed args, defaults omitted; `prove` only rides with `restart`. */
export function mintArgs(f: MintForm, supervisor?: Supervisor | null): Record<string, unknown> {
  const a: Record<string, unknown> = { label: f.label.trim(), role: f.role };
  const ts = f.tenantString.trim();
  if (ts) a.tenant_string = ts;
  if (f.restart && restartRefusal(supervisor) === null) {
    a.restart = true;
    if (f.prove) a.prove = true;
  }
  return a;
}

export function revokeArgs(id: string, f: RevokeForm, supervisor?: Supervisor | null): Record<string, unknown> {
  const a: Record<string, unknown> = { id };
  if (f.restart && restartRefusal(supervisor) === null) {
    a.restart = true;
    if (f.prove) a.prove = true;
  }
  return a;
}

export function saCreateArgs(f: ServiceAccountForm): Record<string, unknown> {
  const a: Record<string, unknown> = { subject: f.subject.trim(), role: f.role };
  if (f.purpose.trim()) a.purpose = f.purpose.trim();
  return a;
}

export interface OpSpec {
  verb: TenantOpVerb;
  args: Record<string, unknown>;
  title: string;
  destructive: boolean;
  /** Why Preview is disabled, or null. */
  problem: string | null;
}

/** What one open operation submits: verb, args, title, danger, and whether it may. */
export function credentialOpSpec(
  name: string,
  op: CredOp,
  forms: CredentialForms,
  supervisor?: Supervisor | null,
): OpSpec {
  const destructive = DESTRUCTIVE[op.kind];
  switch (op.kind) {
    case "key-mint": {
      const f = forms.mint;
      const problem =
        validateKeyLabel(f.label.trim()) ??
        validateRole(f.role) ??
        validateTenantString(f.tenantString.trim()) ??
        (f.prove && !f.restart ? "prove needs restart: an unrestarted API still serves the old ledger." : null);
      return { verb: "key-mint", args: mintArgs(f, supervisor), title: `mint a key on ${name}`, destructive, problem };
    }
    case "key-revoke":
      return {
        verb: "key-revoke",
        args: revokeArgs(op.id, forms.revoke, supervisor),
        title: `revoke key ${op.id} on ${name}`,
        destructive,
        problem: validateKeyLabel(op.id) ? "this key's id is outside the contract pattern" : null,
      };
    case "admin-add": {
      const s = forms.adminSubject.trim();
      return {
        verb: "admin-add",
        args: { subject: s },
        title: `add an admin subject on ${name}`,
        destructive,
        problem: validateSubject(s),
      };
    }
    case "admin-remove":
      return {
        verb: "admin-remove",
        args: { subject: op.subject },
        title: `remove admin ${op.subject} on ${name}`,
        destructive,
        problem: validateSubject(op.subject),
      };
    case "sa-create": {
      const f = forms.sa;
      return {
        verb: "sa-create",
        args: saCreateArgs(f),
        title: `create a service account on ${name}`,
        destructive,
        problem: validateServiceAccountSubject(f.subject.trim()) ?? validateRole(f.role) ?? validatePurpose(f.purpose.trim()),
      };
    }
    case "sa-disable":
    case "sa-enable":
      return {
        verb: op.kind,
        args: { subject: op.subject },
        title: `${op.kind === "sa-disable" ? "disable" : "enable"} service account ${op.subject} on ${name}`,
        destructive,
        problem: validateServiceAccountSubject(op.subject),
      };
    case "restart-api":
      return {
        verb: "restart",
        args: { only: ["api"] },
        title: `restart ${name}'s API`,
        destructive,
        problem: restartRefusal(supervisor),
      };
  }
}

/**
 * `ADMIN_SUBJECTS` in either form the daemon accepts (a JSON list, or
 * comma-separated — go/internal/ctl/adopt `adminSubjects`). `null` when the
 * setting is withheld: absent from the env body, or not of class `public`.
 */
export function parseAdminSubjects(env: CtlEnv | null | undefined): string[] | null {
  const row = env?.keys.find((k) => k.key === "ADMIN_SUBJECTS");
  if (!row || row.class !== "public" || typeof row.value_redacted !== "string") return null;
  const v = row.value_redacted.trim();
  if (v === "") return [];
  if (v.startsWith("[")) {
    try {
      const arr: unknown = JSON.parse(v);
      if (Array.isArray(arr) && arr.every((s) => typeof s === "string")) return arr as string[];
    } catch {
      // fall through to the comma form, as the daemon does
    }
  }
  return v
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

const TONE_CLASS: Record<Tone, string> = {
  ok: "bg-mossSoft text-moss",
  warn: "bg-accent-soft text-accent-text",
  bad: "bg-rustSoft text-rust",
  info: "bg-linkSoft text-link",
  neutral: "bg-lineSoft text-dim",
};

function Chip({ tone, children, title }: { tone: Tone; children: string; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-chip px-2 py-[2px] font-mono text-[10.5px] font-medium leading-[15px] ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * A key's state from its two facts. `effective` is the PROVEN state: a revoked
 * key stays effective until the API restarted and the old key answered 401,
 * and a fresh mint is not effective until the API has read the new ledger.
 */
export function keyState(k: Pick<KeyRecord, "effective" | "revoked_at">): { label: string; tone: Tone } {
  if (k.revoked_at) {
    return k.effective ? { label: "revoke pending", tone: "warn" } : { label: "revoked", tone: "neutral" };
  }
  return k.effective ? { label: "effective", tone: "ok" } : { label: "pending restart", tone: "warn" };
}

const FINGERPRINT = /^sha256:[0-9a-f]{16}$/;
const PROOF_NAME = /^[A-Za-z0-9_-]{1,40}$/;

/**
 * The credential facts a settled job's `result` carries: `pending_until_restart`,
 * `effective`, `proof` (per check: fingerprint, status, expected) and a service
 * account's new status. Every member is read by name and shape-checked; a
 * proof entry with an unexpected shape is reported as unreadable, not printed.
 */
export function CredentialResult({ job }: { job: Job }) {
  const r = (job.result ?? null) as Record<string, unknown> | null;
  if (!r) return null;
  const pending = r.pending_until_restart === true;
  const effective = typeof r.effective === "boolean" ? r.effective : null;
  const saStatus =
    r.service_account_status === "active" || r.service_account_status === "disabled"
      ? r.service_account_status
      : null;
  const proof = r.proof && typeof r.proof === "object" && !Array.isArray(r.proof) ? (r.proof as Record<string, unknown>) : null;
  if (!pending && effective === null && !proof && !saStatus) return null;

  return (
    <div role="note" aria-label="credential result" className="rounded-card border border-line bg-paper p-3">
      <div className="mb-2 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted">result</div>
      <div className="flex flex-wrap items-center gap-2">
        {effective === true && <Chip tone="ok">effective</Chip>}
        {effective === false && <Chip tone="warn">not yet effective</Chip>}
        {pending && <Chip tone="warn">pending until restart</Chip>}
        {saStatus && <Chip tone={saStatus === "active" ? "ok" : "neutral"}>{`service account ${saStatus}`}</Chip>}
      </div>
      {pending && (
        <p className="mt-2 text-[12.5px] text-body">
          The tenant API reads its credentials at start-up: this change is recorded but not live
          until the API restarts.
        </p>
      )}
      {proof && (
        <div className="mt-2">
          <div className="mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted">proof</div>
          <ul className="space-y-1">
            {Object.entries(proof).map(([what, v], i) => {
              const e = v && typeof v === "object" ? (v as Record<string, unknown>) : null;
              const status = typeof e?.status === "number" ? e.status : null;
              const expected = typeof e?.expected === "number" ? e.expected : null;
              const fp = typeof e?.fingerprint === "string" && FINGERPRINT.test(e.fingerprint) ? e.fingerprint : null;
              const name = PROOF_NAME.test(what) ? what : `check ${i + 1}`;
              const ok = status !== null && status === expected;
              return (
                <li key={`${i}`} className="flex flex-wrap items-center gap-2 text-[12px] text-body">
                  <span className="font-mono text-[11.5px] text-strong">{name}</span>
                  {status === null ? (
                    <Chip tone="neutral">unreadable</Chip>
                  ) : (
                    <Chip tone={ok ? "ok" : "bad"}>{`${status}${expected !== null ? ` (expected ${expected})` : ""}`}</Chip>
                  )}
                  {fp && <span className="font-mono text-[11px] text-dim">{fp}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

const TH = "py-1.5 pr-3 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const TD = "py-1.5 pr-3 align-top";
const MONO = "font-mono text-[11.5px]";
const H3 = "mb-2 mt-7 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong first:mt-0";
const EYEBROW = "mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const LABEL = "flex items-center gap-1.5 text-[12.5px] text-body";
const INPUT =
  "w-72 rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px] focus:border-ink-900 focus:outline-none";
const SELECT = "rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px]";
const SMALL =
  "rounded-row border border-line bg-white px-2 py-0.5 text-[11.5px] font-medium text-strong hover:bg-paper disabled:cursor-not-allowed disabled:opacity-40";
const OPEN =
  "rounded-panel border border-line bg-white px-3 py-1.5 text-[12.5px] font-medium text-strong hover:bg-paper disabled:cursor-not-allowed disabled:opacity-40";

function RoleSelect({ name, value, onChange }: { name: string; value: CredentialRole; onChange: (r: CredentialRole) => void }) {
  return (
    <label className={LABEL}>
      <span className="font-mono">role</span>
      <select name={name} value={value} onChange={(e) => onChange(e.target.value as CredentialRole)} className={SELECT}>
        {ROLES.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
    </label>
  );
}

/** restart + prove, shared by mint and revoke. Prove only with restart; restart never on a manual tenant. */
function RestartOptions({
  restart,
  prove,
  supervisor,
  proveWhat,
  onChange,
}: {
  restart: boolean;
  prove: boolean;
  supervisor: Supervisor | null;
  proveWhat: string;
  onChange: (o: { restart: boolean; prove: boolean }) => void;
}) {
  const refusal = restartRefusal(supervisor);
  const restartOn = restart && refusal === null;
  return (
    <div className="space-y-2">
      <label className={LABEL} title={refusal ?? undefined}>
        <input
          type="checkbox"
          name="restart"
          checked={restartOn}
          disabled={refusal !== null}
          onChange={(e) => onChange({ restart: e.target.checked, prove: e.target.checked ? prove : false })}
        />
        <span>
          <span className="font-mono">restart</span> — restart the tenant API so the change is live now
        </span>
      </label>
      {refusal && (
        <p role="note" className="text-[11.5px] text-dim">
          {refusal}
        </p>
      )}
      <label className={LABEL} title={restartOn ? undefined : "prove needs restart"}>
        <input
          type="checkbox"
          name="prove"
          checked={restartOn && prove}
          disabled={!restartOn}
          onChange={(e) => onChange({ restart, prove: e.target.checked })}
        />
        <span>
          <span className="font-mono">prove</span> — after the restart, {proveWhat}
        </span>
      </label>
    </div>
  );
}

function OpFields({
  op,
  forms,
  supervisor,
  onForms,
}: {
  op: CredOp;
  forms: CredentialForms;
  supervisor: Supervisor | null;
  onForms: (f: CredentialForms) => void;
}) {
  switch (op.kind) {
    case "key-mint": {
      const f = forms.mint;
      const set = (patch: Partial<MintForm>) => onForms({ ...forms, mint: { ...f, ...patch } });
      return (
        <div className="space-y-3">
          <p className="text-[12.5px] text-body">
            Mint a tenant API key. Its value is delivered ONCE, through “Reveal once” after the job
            succeeds; the ledger keeps only its fingerprint.
          </p>
          <label className="block text-[12.5px] text-body">
            <div className={EYEBROW}>label (the key id)</div>
            <input
              type="text"
              name="label"
              autoComplete="off"
              spellCheck={false}
              value={f.label}
              placeholder="ingest-worker"
              pattern={KEY_LABEL.source}
              onChange={(e) => set({ label: e.target.value })}
              className={INPUT}
            />
          </label>
          <RoleSelect name="role" value={f.role} onChange={(role) => set({ role })} />
          <label className="block text-[12.5px] text-body">
            <div className={EYEBROW}>tenant string (optional)</div>
            <input
              type="text"
              name="tenant_string"
              autoComplete="off"
              spellCheck={false}
              value={f.tenantString}
              placeholder="the ledger's own convention"
              onChange={(e) => set({ tenantString: e.target.value })}
              className={INPUT}
            />
          </label>
          <RestartOptions
            restart={f.restart}
            prove={f.prove}
            supervisor={supervisor}
            proveWhat="dial the API: the new key answers 200 and can see the tenant's collections"
            onChange={(o) => set(o)}
          />
        </div>
      );
    }
    case "key-revoke":
      return (
        <div className="space-y-3">
          <p className="text-[12.5px] text-body">
            Withdraw key <span className="font-mono">{op.id}</span>. Requests made with it keep
            working until the API restarts.
          </p>
          <RestartOptions
            restart={forms.revoke.restart}
            prove={forms.revoke.prove}
            supervisor={supervisor}
            proveWhat="dial the API: the revoked key answers 401 and a surviving admin key 200"
            onChange={(o) => onForms({ ...forms, revoke: o })}
          />
        </div>
      );
    case "admin-add":
      return (
        <label className="block text-[12.5px] text-body">
          <div className={EYEBROW}>subject (issuer:sub)</div>
          <input
            type="text"
            name="subject"
            autoComplete="off"
            spellCheck={false}
            value={forms.adminSubject}
            placeholder="bvbrc:alice"
            onChange={(e) => onForms({ ...forms, adminSubject: e.target.value })}
            className={INPUT}
          />
        </label>
      );
    case "sa-create": {
      const f = forms.sa;
      const set = (patch: Partial<ServiceAccountForm>) => onForms({ ...forms, sa: { ...f, ...patch } });
      return (
        <div className="space-y-3">
          <label className="block text-[12.5px] text-body">
            <div className={EYEBROW}>subject (no colon)</div>
            <input
              type="text"
              name="sa_subject"
              autoComplete="off"
              spellCheck={false}
              value={f.subject}
              placeholder="svc-ingest"
              onChange={(e) => set({ subject: e.target.value })}
              className={INPUT}
            />
          </label>
          <RoleSelect name="sa_role" value={f.role} onChange={(role) => set({ role })} />
          <label className="block text-[12.5px] text-body">
            <div className={EYEBROW}>purpose (optional, at most 256)</div>
            <input
              type="text"
              name="purpose"
              autoComplete="off"
              value={f.purpose}
              maxLength={256}
              onChange={(e) => set({ purpose: e.target.value })}
              className={INPUT}
            />
          </label>
        </div>
      );
    }
    case "admin-remove":
      return (
        <p className="text-[12.5px] text-body">
          Remove <span className="font-mono">{op.subject}</span> from the tenant's admin subjects.
          Pending until the API restarts.
        </p>
      );
    case "sa-disable":
    case "sa-enable":
      return (
        <p className="text-[12.5px] text-body">
          {op.kind === "sa-disable" ? "Disable" : "Enable"} service account{" "}
          <span className="font-mono">{op.subject}</span> on the tenant API.
        </p>
      );
    case "restart-api":
      return (
        <p className="text-[12.5px] text-body">
          Restart the tenant API only (<span className="font-mono">only: [api]</span>) so pending
          credential changes take effect. Open requests are cut off.
        </p>
      );
  }
}

/** Which subsection an operation's flow is drawn under. */
function groupOf(op: CredOp): "keys" | "admins" | "sa" {
  if (op.kind === "key-mint" || op.kind === "key-revoke") return "keys";
  if (op.kind === "sa-create" || op.kind === "sa-disable" || op.kind === "sa-enable") return "sa";
  return "admins";
}

export interface CredentialsViewProps {
  name: string;
  role: CtlRole;
  /** The operator's tenant body; `registry: null` means withheld. */
  tenant: CtlTenant;
  /** Parsed `ADMIN_SUBJECTS`, or null when the env withholds it. */
  adminSubjects: string[] | null;
  /** The open operation, or null. */
  active: CredOp | null;
  forms: CredentialForms;
  /** The settled job of the open operation, for its result notes. */
  settled: Job | null;
  /** Bumped whenever a flow closes, so the next one starts clean. */
  round: number;
  onOpen: (op: CredOp) => void;
  onClose: () => void;
  onForms: (f: CredentialForms) => void;
  onDone: (job: Job) => void;
  onOpenJob?: (id: string) => void;
}

export function CredentialsView(p: CredentialsViewProps) {
  if (p.role !== "operator") return null;
  const reg = p.tenant.registry;
  if (!reg) {
    return (
      <p role="note" className="text-[12.5px] text-dim">
        Withheld: the tenant's credential ledger is part of the registry row, which the control
        plane returns to operators only.
      </p>
    );
  }
  const supervisor: Supervisor = reg.supervisor;
  const restartBlocked = restartRefusal(supervisor);
  const keys: KeyRecord[] = reg.keys;
  const sas: ServiceAccountRecord[] = reg.service_accounts;
  const adminCount = p.adminSubjects?.length ?? reg.identity.admin_subjects_count;
  const lastAdmin = adminCount <= 1;

  const flow = (group: "keys" | "admins" | "sa") => {
    if (!p.active || groupOf(p.active) !== group) return null;
    const spec = credentialOpSpec(p.name, p.active, p.forms, supervisor);
    const opKey =
      "id" in p.active ? p.active.id : "subject" in p.active ? p.active.subject : "";
    return (
      <div className="mt-3 space-y-3">
        <OpFlow
          key={`${p.active.kind}-${opKey}-${p.round}`}
          op={spec.verb}
          title={spec.title}
          destructive={spec.destructive}
          confirmValue={spec.destructive ? p.name : undefined}
          run={(req) => submitOp(p.name, spec.verb, { ...req, args: spec.args })}
          disabled={spec.problem !== null}
          disabledReason={spec.problem ?? undefined}
          onDone={p.onDone}
          onOpenJob={p.onOpenJob}
          onClose={p.onClose}
        >
          <OpFields op={p.active} forms={p.forms} supervisor={supervisor} onForms={p.onForms} />
        </OpFlow>
        {p.settled && <CredentialResult job={p.settled} />}
      </div>
    );
  };

  return (
    <div>
      {/* ------------------------------------------------------------ keys */}
      <h3 className={H3}>Keys</h3>
      {keys.length === 0 ? (
        <p className="text-[12.5px] text-dim">No keys in this tenant's ledger.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left" aria-label="keys">
            <thead>
              <tr className="border-b border-line">
                <th className={TH}>label</th>
                <th className={TH}>role</th>
                <th className={TH}>tenant string</th>
                <th className={TH}>fingerprint</th>
                <th className={TH}>created</th>
                <th className={TH}>by</th>
                <th className={TH}>state</th>
                <th className={TH}>revoked</th>
                <th className={TH} />
              </tr>
            </thead>
            <tbody>
              {keys.map((k, i) => {
                const st = keyState(k);
                return (
                  <tr key={`${k.id}-${k.created_at}-${i}`} className="border-b border-lineSoft">
                    <td className={`${TD} ${MONO} font-medium text-strong`} title={k.id !== k.label ? `id ${k.id}` : undefined}>
                      {k.label}
                    </td>
                    <td className={`${TD} ${MONO}`}>{k.role}</td>
                    <td className={`${TD} ${MONO} text-dim`}>{k.tenant_string}</td>
                    <td className={`${TD} ${MONO} text-dim`}>{FINGERPRINT.test(k.fingerprint) ? k.fingerprint : "—"}</td>
                    <td className={`${TD} ${MONO} text-dim`} title={k.created_at}>{since(k.created_at)}</td>
                    <td className={`${TD} ${MONO} text-dim`}>{k.created_by}</td>
                    <td className={TD}>
                      <Chip tone={st.tone}>{st.label}</Chip>
                    </td>
                    <td className={`${TD} ${MONO} text-dim`} title={k.revoked_at ?? undefined}>
                      {k.revoked_at ? since(k.revoked_at) : "—"}
                    </td>
                    <td className={TD}>
                      {!k.revoked_at && (
                        <button type="button" onClick={() => p.onOpen({ kind: "key-revoke", id: k.id })} className={SMALL}>
                          Revoke…
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-3">
        <button type="button" onClick={() => p.onOpen({ kind: "key-mint" })} className={OPEN}>
          Mint key…
        </button>
      </div>
      {flow("keys")}

      {/* ---------------------------------------------------------- admins */}
      <h3 className={H3}>Admin subjects</h3>
      <p role="note" className="mb-2 rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
        Admin subjects are read when the tenant API starts: an add or remove is pending until the
        next restart.
        {restartBlocked && " This tenant is supervisor: manual — restart it the way it was started."}
      </p>
      {p.adminSubjects === null ? (
        <p className="text-[12.5px] text-body">
          {reg.identity.admin_subjects_count} admin subject
          {reg.identity.admin_subjects_count === 1 ? "" : "s"} (the{" "}
          <span className="font-mono">ADMIN_SUBJECTS</span> setting itself was not returned).
        </p>
      ) : p.adminSubjects.length === 0 ? (
        <p className="text-[12.5px] text-dim">No admin subjects.</p>
      ) : (
        <ul aria-label="admin subjects" className="space-y-1">
          {p.adminSubjects.map((s, i) => (
            <li key={`${s}-${i}`} className="flex items-center gap-3">
              <span className={`${MONO} text-strong`}>{s}</span>
              {ADMIN_SUBJECT.test(s) && (
                <button
                  type="button"
                  disabled={lastAdmin}
                  title={lastAdmin ? "The last admin subject cannot be removed." : undefined}
                  onClick={() => p.onOpen({ kind: "admin-remove", subject: s })}
                  className={SMALL}
                >
                  Remove…
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => p.onOpen({ kind: "admin-add" })} className={OPEN}>
          Add admin…
        </button>
        <button
          type="button"
          disabled={restartBlocked !== null}
          title={restartBlocked ?? undefined}
          onClick={() => p.onOpen({ kind: "restart-api" })}
          className={OPEN}
        >
          Restart API…
        </button>
      </div>
      {flow("admins")}

      {/* -------------------------------------------------- service accounts */}
      <h3 className={H3}>Service accounts</h3>
      {sas.length === 0 ? (
        <p className="text-[12.5px] text-dim">No service accounts.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left" aria-label="service accounts">
            <thead>
              <tr className="border-b border-line">
                <th className={TH}>subject</th>
                <th className={TH}>role</th>
                <th className={TH}>purpose</th>
                <th className={TH}>status</th>
                <th className={TH}>created</th>
                <th className={TH} />
              </tr>
            </thead>
            <tbody>
              {sas.map((sa) => (
                <tr key={sa.subject} className="border-b border-lineSoft">
                  <td className={`${TD} ${MONO} font-medium text-strong`}>{sa.subject}</td>
                  <td className={`${TD} ${MONO}`}>{sa.role}</td>
                  <td className={`${TD} text-[12px] text-body`}>{sa.purpose}</td>
                  <td className={TD}>
                    <Chip tone={sa.status === "active" ? "ok" : "neutral"}>{sa.status}</Chip>
                  </td>
                  <td className={`${TD} ${MONO} text-dim`} title={sa.created_at}>{since(sa.created_at)}</td>
                  <td className={TD}>
                    {SERVICE_ACCOUNT_SUBJECT.test(sa.subject) &&
                      (sa.status === "active" ? (
                        <button type="button" onClick={() => p.onOpen({ kind: "sa-disable", subject: sa.subject })} className={SMALL}>
                          Disable…
                        </button>
                      ) : (
                        <button type="button" onClick={() => p.onOpen({ kind: "sa-enable", subject: sa.subject })} className={SMALL}>
                          Enable…
                        </button>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-3">
        <button type="button" onClick={() => p.onOpen({ kind: "sa-create" })} className={OPEN}>
          Create service account…
        </button>
      </div>
      {flow("sa")}
    </div>
  );
}

export function CredentialsSection({
  name,
  role,
  onOpenJob,
}: {
  name: string;
  role: CtlRole;
  onOpenJob?: (id: string) => void;
}) {
  const operator = role === "operator";
  const tenant = useCtlQuery<CtlTenant>(ctlKeys.tenant(name), `/v1/tenants/${name}`, { enabled: operator });
  const env = useCtlQuery<CtlEnv>(ctlKeys.tenantEnv(name), `/v1/tenants/${name}/env`, { enabled: operator });
  const [active, setActive] = useState<CredOp | null>(null);
  const [forms, setForms] = useState<CredentialForms>(EMPTY_CREDENTIAL_FORMS);
  const [settled, setSettled] = useState<Job | null>(null);
  const [round, setRound] = useState(0);

  if (!operator) {
    return <OperatorRequired what="Minting and revoking keys, admin subjects and service accounts are operator actions." />;
  }
  if (tenant.error) return <ErrorBanner error={tenant.error} onRetry={() => void tenant.refetch()} />;
  if (!tenant.data) return <p className="text-[12.5px] text-dim">Loading {name}…</p>;

  return (
    <CredentialsView
      name={name}
      role={role}
      tenant={tenant.data}
      // A failed env read is "withheld": the registry's count still shows.
      adminSubjects={env.error ? null : parseAdminSubjects(env.data)}
      active={active}
      forms={forms}
      settled={settled}
      round={round}
      onOpen={(op) => {
        setActive(op);
        setForms(EMPTY_CREDENTIAL_FORMS);
        setSettled(null);
        setRound((r) => r + 1);
      }}
      onClose={() => {
        setActive(null);
        setForms(EMPTY_CREDENTIAL_FORMS);
        setSettled(null);
        setRound((r) => r + 1);
      }}
      onForms={setForms}
      onDone={setSettled}
      onOpenJob={onOpenJob}
    />
  );
}
