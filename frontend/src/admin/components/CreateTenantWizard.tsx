// The create-tenant wizard (`#/create`): eight steps that assemble one
// `CreateArgs` (contracts/ctl/schemas/create_request.json), then the same
// OpFlow every other mutation uses — dry run → plan → key → job → the minted
// keys revealed once.
//
//   1 Name        `name`             — live validation, the reserved list
//   2 Code        `artifact_id`      — a PREPARED artifact from GET /v1/artifacts
//   3 Identity    `identity_provider`, `admin_subjects[]`
//   4 Stores      `postgres`, `es_heap`
//   5 Settings    `template_from`, `settings{}` (public keys only)
//   6 Credentials `keys[]`, `service_accounts[]`
//   7 Options     `ui_mode`, `start`, `gateway`, `supervisor`
//   8 Review      the assembled args, read-only, then OpFlow
//
// OPERATOR ONLY: a viewer gets the 403 panel, never a disabled form (the
// daemon refuses a viewer's POST on its own authority either way).
//
// Arguments are sent as the contract defines them and nothing more: a value
// equal to the contract default is OMITTED, so the plan and the audit row show
// what the operator chose — and `supervisor` is absent unless chosen, because
// absent means the deployment's default (ctl.env CTL_DEFAULT_SUPERVISOR).
//
// The key never touches this file: OpFlow asks for it per request.
// Navigation to the new tenant is the operator's click, NOT automatic on
// success — leaving the page would unmount the RevealOnce card before the
// one-shot keys were collected.
//
// `CreateTenantWizardView` is the props-only seam the string-render tests
// mount at every step; `CreateTenantWizard` is the stateful wrapper.

import { useState, type ReactNode } from "react";
import type { CtlError } from "../api/http";
import { createTenant, type MutationOutcome } from "../api/ops";
import { ctlKeys, useCtlQuery } from "../api/queries";
import type { ArtifactRow, ArtifactsResponse, CreateArgsInput, CtlFleet, CtlRole, Job } from "../api/types";
import { since } from "../lib/format";
import {
  TENANT_NAME,
  validateAdminSubjects,
  validateESHeap,
  validateSetting,
  validateTenantName,
} from "../lib/validate";
import { ErrorBanner } from "./ErrorBanner";
import { OperatorRequired } from "./JobsView";
import { OpFlow, type OpRunRequest } from "./OpFlow";
import { redactText } from "./redact";

// ---------------------------------------------------------------------------
// The form and its mapping onto CreateArgs
// ---------------------------------------------------------------------------

export const WIZARD_STEPS = [
  "name",
  "code",
  "identity",
  "stores",
  "settings",
  "credentials",
  "options",
  "review",
] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

const STEP_TITLE: Record<WizardStep, string> = {
  name: "Name",
  code: "Code",
  identity: "Identity",
  stores: "Stores",
  settings: "Settings",
  credentials: "Credentials",
  options: "Options",
  review: "Review",
};

export type Role = "admin" | "user";
export interface KeyRow {
  label: string;
  role: Role;
}
export interface SARow {
  subject: string;
  role: Role;
  purpose: string;
}
export interface SettingRow {
  key: string;
  value: string;
}

export interface WizardForm {
  name: string;
  artifactId: string;
  identity: "bvbrc" | "none";
  adminSubjects: string[];
  postgres: "sqlite" | "local";
  esHeap: string;
  /** "" = no template. */
  templateFrom: string;
  settings: SettingRow[];
  keys: KeyRow[];
  serviceAccounts: SARow[];
  uiMode: "static" | "dev" | "external";
  start: boolean;
  gateway: boolean;
  /** "" = the deployment's default (the member is omitted). */
  supervisor: "" | "systemd" | "instance";
}

export const EMPTY_WIZARD: WizardForm = {
  name: "",
  artifactId: "",
  identity: "bvbrc",
  adminSubjects: [],
  postgres: "sqlite",
  esHeap: "1g",
  templateFrom: "",
  settings: [],
  keys: [],
  serviceAccounts: [],
  uiMode: "static",
  start: true,
  gateway: true,
  supervisor: "",
};

/** The label `create` always mints (ops/create.go `bootstrapAdminLabel`). */
export const BOOTSTRAP_ADMIN_LABEL = "bootstrap-admin";

/** create_request.json `keys[].label`. */
const KEY_LABEL = /^[a-z0-9][a-z0-9-]{0,63}$/;
/** create_request.json `service_accounts[].subject`. */
const SA_SUBJECT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/**
 * The request's `args`: required members always, everything else only when it
 * differs from the contract default (or, for `supervisor`, when chosen).
 */
export function createArgs(f: WizardForm): CreateArgsInput {
  const a: CreateArgsInput = { name: f.name.trim(), artifact_id: f.artifactId };
  const esHeap = f.esHeap.trim();
  if (esHeap !== "" && esHeap !== "1g") a.es_heap = esHeap;
  if (f.postgres !== "sqlite") a.postgres = f.postgres;
  if (f.identity !== "none") a.identity_provider = f.identity;
  const subjects = f.identity === "none" ? [] : f.adminSubjects.map((s) => s.trim());
  if (subjects.length > 0) a.admin_subjects = subjects;
  if (f.keys.length > 0) a.keys = f.keys.map((k) => ({ label: k.label.trim(), role: k.role }));
  if (f.serviceAccounts.length > 0) {
    a.service_accounts = f.serviceAccounts.map((s) => ({
      subject: s.subject.trim(),
      role: s.role,
      purpose: s.purpose.trim(),
    }));
  }
  if (f.templateFrom !== "") a.template_from = f.templateFrom;
  if (f.settings.length > 0) {
    const set: Record<string, string> = {};
    for (const r of f.settings) set[r.key.trim()] = r.value;
    a.settings = set;
  }
  if (f.supervisor !== "") a.supervisor = f.supervisor;
  if (f.uiMode !== "static") a.ui_mode = f.uiMode;
  if (!f.start) a.start = false;
  if (!f.gateway) a.gateway = false;
  return a;
}

/** What a step's validation can see besides the form. */
export interface WizardContext {
  /** Registry tenant names (for "already exists" and the template picker). */
  fleetNames: readonly string[];
  /** The prepared artifacts, or null while the list has not loaded. */
  artifacts: readonly ArtifactRow[] | null;
}

function credentialsProblem(f: WizardForm): string | null {
  const labels = new Set<string>();
  for (const k of f.keys) {
    const label = k.label.trim();
    if (!KEY_LABEL.test(label)) {
      return `Key label "${label}": lowercase letters, digits and dashes, starting with a letter or digit (^[a-z0-9][a-z0-9-]{0,63}$).`;
    }
    if (labels.has(label)) return `Key label "${label}" is listed twice.`;
    if (label === BOOTSTRAP_ADMIN_LABEL && k.role !== "admin") {
      return `"${BOOTSTRAP_ADMIN_LABEL}" is the ctl's own admin key: its role must be admin.`;
    }
    labels.add(label);
  }
  const subjects = new Set<string>();
  for (const s of f.serviceAccounts) {
    const subject = s.subject.trim();
    if (!SA_SUBJECT.test(subject)) {
      return `Service account "${subject}": letters, digits, dot, underscore and dash, starting with a letter or digit (at most 64).`;
    }
    if (subjects.has(subject)) return `Service account "${subject}" is listed twice.`;
    if (s.purpose.trim().length > 256) return `Service account "${subject}": the purpose is longer than 256 characters.`;
    subjects.add(subject);
  }
  return null;
}

function settingsProblem(f: WizardForm, ctx: WizardContext): string | null {
  if (f.templateFrom !== "") {
    if (!TENANT_NAME.test(f.templateFrom)) return "The template is not a tenant name.";
    if (!ctx.fleetNames.includes(f.templateFrom)) return `"${f.templateFrom}" is not a tenant in the registry.`;
  }
  const seen = new Set<string>();
  for (const r of f.settings) {
    const key = r.key.trim();
    const check = validateSetting(key, r.value);
    if (check.problem) return check.problem;
    if (seen.has(key)) return `${key} is set twice.`;
    seen.add(key);
  }
  return null;
}

/** Why `step` cannot be left forward, or null. */
export function stepProblem(step: WizardStep, f: WizardForm, ctx: WizardContext): string | null {
  switch (step) {
    case "name":
      return validateTenantName(f.name.trim(), ctx.fleetNames);
    case "code":
      if (f.artifactId === "") return "Choose a prepared artifact.";
      if (ctx.artifacts && !ctx.artifacts.some((a) => a.id === f.artifactId)) {
        return `"${f.artifactId}" is not a prepared artifact.`;
      }
      return null;
    case "identity":
      return validateAdminSubjects(
        f.adminSubjects.map((s) => s.trim()),
        f.identity,
      );
    case "stores":
      return validateESHeap(f.esHeap.trim());
    case "settings":
      return settingsProblem(f, ctx);
    case "credentials":
      return credentialsProblem(f);
    case "options":
      if (f.uiMode === "dev" && f.supervisor === "instance") {
        return "ui_mode dev cannot run under the instance supervisor: a Vite dev server is not supervised there.";
      }
      return null;
    case "review":
      return null;
  }
}

/** The first step that does not validate, or null — Review re-checks everything. */
export function firstInvalidStep(f: WizardForm, ctx: WizardContext): { step: WizardStep; problem: string } | null {
  for (const step of WIZARD_STEPS) {
    const problem = stepProblem(step, f, ctx);
    if (problem) return { step, problem };
  }
  return null;
}

/** Newest first, by `prepared_at` (RFC 3339 UTC, so string order is time order). */
export function sortArtifacts(rows: readonly ArtifactRow[]): ArtifactRow[] {
  return [...rows].sort((a, b) => (a.prepared_at < b.prepared_at ? 1 : a.prepared_at > b.prepared_at ? -1 : 0));
}

// ---------------------------------------------------------------------------
// The seam
// ---------------------------------------------------------------------------

const EYEBROW = "mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const LABEL = "flex items-center gap-1.5 text-[12.5px] text-body";
const INPUT =
  "rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px] focus:border-ink-900 focus:outline-none";
const SELECT = "rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px]";
const PRIMARY =
  "rounded-panel bg-ink-900 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40";
const SECONDARY =
  "rounded-panel border border-line bg-white px-3 py-1.5 text-[12.5px] font-medium text-strong hover:bg-paper disabled:cursor-not-allowed disabled:opacity-40";
const SMALL =
  "rounded-panel border border-line bg-white px-2 py-0.5 text-[11.5px] font-medium text-strong hover:bg-paper";
const HELP = "text-[12.5px] leading-snug text-body";

export interface CreateTenantWizardViewProps {
  step: WizardStep;
  form: WizardForm;
  /** The prepared artifacts (any order; the view sorts newest first), or null while loading. */
  artifacts: readonly ArtifactRow[] | null;
  artifactsError?: CtlError | null;
  fleetNames: readonly string[];
  /**
   * The current step's problem is shown only once the operator tried to leave
   * it (Next), so an empty first field does not open on a red line. The Name
   * step validates live as soon as something is typed.
   */
  showProblem?: boolean;
  /** A job was accepted: the form is frozen and Back/Cancel are gone. */
  locked?: boolean;
  /** The settled job, once there is one (for the "open the tenant" link). */
  settled?: Job | null;
  onChange: (f: WizardForm) => void;
  onNext: () => void;
  onBack: () => void;
  onCancel: () => void;
  onOpenTenant?: (job: Job) => void;
  onRetryArtifacts?: () => void;
  /** The Review step's OpFlow (the stateful wrapper supplies it). */
  flow?: ReactNode;
}

function Stepper({ step }: { step: WizardStep }) {
  const at = WIZARD_STEPS.indexOf(step);
  return (
    <ol aria-label="Steps" className="mb-4 flex flex-wrap gap-x-3 gap-y-1">
      {WIZARD_STEPS.map((s, i) => (
        <li
          key={s}
          aria-current={s === step ? "step" : undefined}
          className={`font-mono text-[11px] ${
            s === step ? "font-semibold text-strong" : i < at ? "text-moss" : "text-dim"
          }`}
        >
          {i + 1}. {STEP_TITLE[s]}
        </li>
      ))}
    </ol>
  );
}

function NameStep({ form, set, problem }: { form: WizardForm; set: (p: Partial<WizardForm>) => void; problem: string | null }) {
  return (
    <div className="space-y-2">
      <p className={HELP}>
        The registry name of the new tenant. It names its directories, its units and its gateway
        route, and cannot be changed later.
      </p>
      <label className="block">
        <div className={EYEBROW}>name</div>
        <input
          type="text"
          name="name"
          autoComplete="off"
          spellCheck={false}
          value={form.name}
          placeholder="e.g. lab-west"
          aria-invalid={problem ? true : undefined}
          onChange={(e) => set({ name: e.target.value })}
          className={`${INPUT} w-72`}
        />
      </label>
    </div>
  );
}

function ArtifactPicker({
  form,
  set,
  artifacts,
  error,
  onRetry,
}: {
  form: WizardForm;
  set: (p: Partial<WizardForm>) => void;
  artifacts: readonly ArtifactRow[] | null;
  error?: CtlError | null;
  onRetry?: () => void;
}) {
  const rows = artifacts ? sortArtifacts(artifacts) : [];
  return (
    <div className="space-y-2">
      <p className={HELP}>
        The code the tenant runs: a PREPARED artifact — a worktree at a reviewed commit with a built
        UI — never a branch or a path. New ones are prepared on the CLI (
        <code className="font-mono">ragstack-ctl fleet artifact prepare --tag …</code>).
      </p>
      {error && <ErrorBanner error={error} onRetry={onRetry} />}
      {!artifacts && !error && <p className="text-[12.5px] text-dim">Loading the prepared artifacts…</p>}
      {artifacts && rows.length === 0 && (
        <p className="text-[12.5px] text-dim">No artifact is prepared on this host yet.</p>
      )}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                <th className={`py-1.5 pr-3 ${EYEBROW}`} aria-label="choose" />
                <th className={`py-1.5 pr-3 ${EYEBROW}`}>tag</th>
                <th className={`py-1.5 pr-3 ${EYEBROW}`}>sha</th>
                <th className={`py-1.5 pr-3 ${EYEBROW}`}>prepared</th>
                <th className={`py-1.5 pr-3 ${EYEBROW}`}>schema</th>
                <th className={`py-1.5 pr-3 ${EYEBROW}`}>used by</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} className="border-b border-lineSoft align-middle">
                  <td className="py-1.5 pr-3">
                    <input
                      type="radio"
                      name="artifact"
                      value={a.id}
                      aria-label={`artifact ${a.id}`}
                      checked={form.artifactId === a.id}
                      onChange={() => set({ artifactId: a.id })}
                    />
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[12px] text-strong">
                    {redactText(a.tag)}
                    {a.id !== a.tag && (
                      <span className="ml-1.5 text-[11px] text-dim">id {redactText(a.id)}</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[11.5px] text-dim" title={a.sha}>
                    {a.sha.slice(0, 12)}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[11.5px] text-dim">
                    <span title={a.prepared_at}>{since(a.prepared_at, Date.now())}</span> by{" "}
                    {redactText(a.prepared_by)}
                  </td>
                  <td className="py-1.5 pr-3">
                    {a.schema_compatible ? (
                      <span className="inline-flex items-center rounded-chip bg-mossSoft px-2 py-[2px] font-mono text-[10.5px] font-medium text-moss">
                        schema compatible
                      </span>
                    ) : (
                      <span
                        title="Prepared without --schema-compatible: the stores' schema may differ from the fleet's."
                        className="inline-flex items-center rounded-chip bg-accent-soft px-2 py-[2px] font-mono text-[10.5px] font-medium text-accent-text"
                      >
                        schema unchecked
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[11.5px] text-body">
                    {a.tenants.length > 0 ? a.tenants.join(", ") : <span className="text-dim">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ListEditor({
  label,
  items,
  placeholder,
  onChange,
}: {
  label: string;
  items: string[];
  placeholder: string;
  onChange: (items: string[]) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className={EYEBROW}>{label}</div>
      {items.map((v, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            name={`${label}-${i}`}
            autoComplete="off"
            spellCheck={false}
            value={v}
            placeholder={placeholder}
            onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))}
            className={`${INPUT} w-96`}
          />
          <button type="button" className={SMALL} onClick={() => onChange(items.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className={SMALL} onClick={() => onChange([...items, ""])}>
        Add subject
      </button>
    </div>
  );
}

function IdentityStep({ form, set }: { form: WizardForm; set: (p: Partial<WizardForm>) => void }) {
  return (
    <div className="space-y-3">
      <p className={HELP}>
        Who may sign in to the tenant with a bearer token. With <code className="font-mono">bvbrc</code>,
        the admin subjects are the BV-BRC users who are the tenant&apos;s admins from the start; with{" "}
        <code className="font-mono">none</code> the tenant takes API keys only and admin subjects are
        refused.
      </p>
      <div className="flex flex-wrap gap-4">
        {(["bvbrc", "none"] as const).map((p) => (
          <label key={p} className={LABEL}>
            <input
              type="radio"
              name="identity"
              value={p}
              checked={form.identity === p}
              onChange={() => set({ identity: p })}
            />
            <span className="font-mono">{p}</span>
          </label>
        ))}
      </div>
      {form.identity === "bvbrc" ? (
        <ListEditor
          label="admin subjects"
          items={form.adminSubjects}
          placeholder="bvbrc:alice@patricbrc.org"
          onChange={(adminSubjects) => set({ adminSubjects })}
        />
      ) : (
        form.adminSubjects.length > 0 && (
          <p className="text-[12px] text-accent-text">
            {form.adminSubjects.length} admin subject{form.adminSubjects.length === 1 ? "" : "s"} entered
            under bvbrc will not be sent with none.{" "}
            <button type="button" className={SMALL} onClick={() => set({ adminSubjects: [] })}>
              Clear them
            </button>
          </p>
        )
      )}
    </div>
  );
}

function StoresStep({ form, set }: { form: WizardForm; set: (p: Partial<WizardForm>) => void }) {
  return (
    <div className="space-y-3">
      <div>
        <div className={EYEBROW}>postgres</div>
        <div className="space-y-1">
          <label className={LABEL}>
            <input
              type="radio"
              name="postgres"
              value="sqlite"
              checked={form.postgres === "sqlite"}
              onChange={() => set({ postgres: "sqlite" })}
            />
            <span>
              <span className="font-mono">sqlite</span> — ACL, job and collection state as files under
              the tenant&apos;s state directory; no server
            </span>
          </label>
          <label className={LABEL}>
            <input
              type="radio"
              name="postgres"
              value="local"
              checked={form.postgres === "local"}
              onChange={() => set({ postgres: "local" })}
            />
            <span>
              <span className="font-mono">local</span> — the tenant&apos;s own postgres on its block&apos;s
              +5 port, as a unit the ctl owns
            </span>
          </label>
        </div>
      </div>
      <label className="block">
        <div className={EYEBROW}>Elasticsearch heap</div>
        <input
          type="text"
          name="es_heap"
          autoComplete="off"
          spellCheck={false}
          value={form.esHeap}
          placeholder="1g"
          onChange={(e) => set({ esHeap: e.target.value })}
          className={`${INPUT} w-28`}
        />
        <span className="ml-2 text-[11.5px] text-dim">
          e.g. 1g or 512m; admission checks the fleet&apos;s heap sum against host memory
        </span>
      </label>
    </div>
  );
}

function SettingsStep({
  form,
  set,
  fleetNames,
}: {
  form: WizardForm;
  set: (p: Partial<WizardForm>) => void;
  fleetNames: readonly string[];
}) {
  const rows = form.settings;
  const update = (i: number, patch: Partial<SettingRow>) =>
    set({ settings: rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  return (
    <div className="space-y-3">
      <label className="block">
        <div className={EYEBROW}>template from</div>
        <select
          name="template_from"
          value={form.templateFrom}
          onChange={(e) => set({ templateFrom: e.target.value })}
          className={SELECT}
        >
          <option value="">(none)</option>
          {fleetNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <span className="ml-2 text-[11.5px] text-dim">
          copies that tenant&apos;s PUBLIC settings only — never ports, paths, store URLs or secrets
        </span>
      </label>
      <div className="space-y-1.5">
        <div className={EYEBROW}>settings (public keys; applied over the template)</div>
        {rows.map((r, i) => {
          const check = r.key.trim() === "" ? null : validateSetting(r.key.trim(), r.value);
          return (
            <div key={i}>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  name={`setting-key-${i}`}
                  autoComplete="off"
                  spellCheck={false}
                  value={r.key}
                  placeholder="CHUNK_MAX_TOKENS"
                  onChange={(e) => update(i, { key: e.target.value })}
                  className={`${INPUT} w-64`}
                />
                <span className="font-mono text-[12px] text-dim">=</span>
                <input
                  type="text"
                  name={`setting-value-${i}`}
                  autoComplete="off"
                  spellCheck={false}
                  value={r.value}
                  onChange={(e) => update(i, { value: e.target.value })}
                  className={`${INPUT} w-72`}
                />
                <button
                  type="button"
                  className={SMALL}
                  onClick={() => set({ settings: rows.filter((_, j) => j !== i) })}
                >
                  Remove
                </button>
              </div>
              {check?.warning && (
                <p role="note" className="mt-1 text-[11.5px] text-accent-text">
                  {check.warning}
                </p>
              )}
            </div>
          );
        })}
        <button type="button" className={SMALL} onClick={() => set({ settings: [...rows, { key: "", value: "" }] })}>
          Add setting
        </button>
      </div>
    </div>
  );
}

function RoleSelect({ name, value, onChange }: { name: string; value: Role; onChange: (r: Role) => void }) {
  return (
    <select name={name} value={value} onChange={(e) => onChange(e.target.value as Role)} className={SELECT}>
      <option value="user">user</option>
      <option value="admin">admin</option>
    </select>
  );
}

function CredentialsStep({ form, set }: { form: WizardForm; set: (p: Partial<WizardForm>) => void }) {
  const keys = form.keys;
  const sas = form.serviceAccounts;
  return (
    <div className="space-y-4">
      <p className={HELP}>
        Keys are described here and MINTED by the ctl; their values are shown once, after the job
        succeeds, and are kept nowhere you can read them back. An admin key labelled{" "}
        <code className="font-mono">{BOOTSTRAP_ADMIN_LABEL}</code> is always minted — the ctl uses it to
        register the service accounts.
      </p>
      <div className="space-y-1.5">
        <div className={EYEBROW}>extra API keys</div>
        {keys.map((k, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              name={`key-label-${i}`}
              autoComplete="off"
              spellCheck={false}
              value={k.label}
              placeholder="ops"
              onChange={(e) => set({ keys: keys.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })}
              className={`${INPUT} w-56`}
            />
            <RoleSelect
              name={`key-role-${i}`}
              value={k.role}
              onChange={(role) => set({ keys: keys.map((x, j) => (j === i ? { ...x, role } : x)) })}
            />
            <button type="button" className={SMALL} onClick={() => set({ keys: keys.filter((_, j) => j !== i) })}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" className={SMALL} onClick={() => set({ keys: [...keys, { label: "", role: "user" }] })}>
          Add key
        </button>
      </div>
      <div className="space-y-1.5">
        <div className={EYEBROW}>service accounts</div>
        {sas.map((s, i) => {
          const upd = (patch: Partial<SARow>) =>
            set({ serviceAccounts: sas.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
          return (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <input
                type="text"
                name={`sa-subject-${i}`}
                autoComplete="off"
                spellCheck={false}
                value={s.subject}
                placeholder="gowe"
                onChange={(e) => upd({ subject: e.target.value })}
                className={`${INPUT} w-48`}
              />
              <RoleSelect name={`sa-role-${i}`} value={s.role} onChange={(role) => upd({ role })} />
              <input
                type="text"
                name={`sa-purpose-${i}`}
                autoComplete="off"
                value={s.purpose}
                placeholder="purpose, e.g. workflows"
                onChange={(e) => upd({ purpose: e.target.value })}
                className={`${INPUT} w-72`}
              />
              <button
                type="button"
                className={SMALL}
                onClick={() => set({ serviceAccounts: sas.filter((_, j) => j !== i) })}
              >
                Remove
              </button>
            </div>
          );
        })}
        <button
          type="button"
          className={SMALL}
          onClick={() => set({ serviceAccounts: [...sas, { subject: "", role: "user", purpose: "" }] })}
        >
          Add service account
        </button>
      </div>
    </div>
  );
}

function OptionsStep({ form, set }: { form: WizardForm; set: (p: Partial<WizardForm>) => void }) {
  return (
    <div className="space-y-3">
      <label className={LABEL}>
        <span className="font-mono">ui_mode</span>
        <select
          name="ui_mode"
          value={form.uiMode}
          onChange={(e) => set({ uiMode: e.target.value as WizardForm["uiMode"] })}
          className={SELECT}
        >
          <option value="static">static</option>
          <option value="dev">dev</option>
          <option value="external">external</option>
        </select>
        <span className="text-[11.5px] text-dim">
          static: nginx serves the built UI · dev: a Vite dev server · external: someone else runs it
        </span>
      </label>
      <label className={LABEL}>
        <span className="font-mono">supervisor</span>
        <select
          name="supervisor"
          value={form.supervisor}
          onChange={(e) => set({ supervisor: e.target.value as WizardForm["supervisor"] })}
          className={SELECT}
        >
          <option value="">(the deployment&apos;s default)</option>
          <option value="instance">instance</option>
          <option value="systemd">systemd</option>
        </select>
        <span className="text-[11.5px] text-dim">left at the default, nothing is sent and the ctl decides</span>
      </label>
      {form.uiMode === "dev" && form.supervisor === "" && (
        <p role="note" className="text-[11.5px] text-accent-text">
          ui_mode dev is refused under the instance supervisor; if this host&apos;s default is instance, choose
          systemd.
        </p>
      )}
      <label className={LABEL}>
        <input type="checkbox" name="start" checked={form.start} onChange={(e) => set({ start: e.target.checked })} />
        <span>Start the tenant once provisioned (readiness-gated)</span>
      </label>
      <label className={LABEL}>
        <input
          type="checkbox"
          name="gateway"
          checked={form.gateway}
          onChange={(e) => set({ gateway: e.target.checked })}
        />
        <span>Publish a gateway generation that routes it</span>
      </label>
    </div>
  );
}

function SummaryRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <tr className="border-b border-lineSoft align-top">
      <th scope="row" className="py-1 pr-4 font-mono text-[11.5px] font-medium text-dim">
        {k}
      </th>
      <td className="py-1 font-mono text-[12px] text-strong">{children}</td>
    </tr>
  );
}

const DEFAULT = <span className="text-dim">(default)</span>;

/** The assembled args, read-only. Every member, with defaults labelled as such. */
export function CreateArgsSummary({ args }: { args: CreateArgsInput }) {
  const settings = Object.entries(args.settings ?? {});
  return (
    <table aria-label="create arguments" className="w-full border-collapse text-left">
      <tbody>
        <SummaryRow k="name">{args.name}</SummaryRow>
        <SummaryRow k="artifact_id">{args.artifact_id}</SummaryRow>
        <SummaryRow k="identity_provider">{args.identity_provider ?? <>none {DEFAULT}</>}</SummaryRow>
        <SummaryRow k="admin_subjects">
          {args.admin_subjects?.length ? args.admin_subjects.join(", ") : <span className="text-dim">—</span>}
        </SummaryRow>
        <SummaryRow k="postgres">{args.postgres ?? <>sqlite {DEFAULT}</>}</SummaryRow>
        <SummaryRow k="es_heap">{args.es_heap ?? <>1g {DEFAULT}</>}</SummaryRow>
        <SummaryRow k="template_from">{args.template_from ?? <span className="text-dim">—</span>}</SummaryRow>
        <SummaryRow k="settings">
          {settings.length ? (
            <ul>
              {settings.map(([key, v]) => (
                <li key={key}>
                  {key}={v}
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-dim">—</span>
          )}
        </SummaryRow>
        <SummaryRow k="keys">
          <ul>
            <li>
              {BOOTSTRAP_ADMIN_LABEL}:admin <span className="text-dim">(always)</span>
            </li>
            {(args.keys ?? [])
              .filter((k) => k.label !== BOOTSTRAP_ADMIN_LABEL)
              .map((k) => (
                <li key={k.label}>
                  {k.label}:{k.role}
                </li>
              ))}
          </ul>
        </SummaryRow>
        <SummaryRow k="service_accounts">
          {args.service_accounts?.length ? (
            <ul>
              {args.service_accounts.map((s) => (
                <li key={s.subject}>
                  {s.subject}:{s.role}
                  {s.purpose ? ` — ${s.purpose}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <span className="text-dim">—</span>
          )}
        </SummaryRow>
        <SummaryRow k="ui_mode">{args.ui_mode ?? <>static {DEFAULT}</>}</SummaryRow>
        <SummaryRow k="supervisor">
          {args.supervisor ?? <span className="text-dim">(the deployment&apos;s default — not sent)</span>}
        </SummaryRow>
        <SummaryRow k="start">{args.start === false ? "no" : <>yes {DEFAULT}</>}</SummaryRow>
        <SummaryRow k="gateway">{args.gateway === false ? "no" : <>yes {DEFAULT}</>}</SummaryRow>
      </tbody>
    </table>
  );
}

export function CreateTenantWizardView(p: CreateTenantWizardViewProps) {
  const { step, form } = p;
  const set = (patch: Partial<WizardForm>) => p.onChange({ ...p.form, ...patch });
  const ctx: WizardContext = { fleetNames: p.fleetNames, artifacts: p.artifacts };
  const problem = stepProblem(step, form, ctx);
  // Name validates live once something is typed; the rest after a Next.
  const showProblem = Boolean(p.showProblem) || (step === "name" && form.name !== "");
  const invalid = step === "review" ? firstInvalidStep(form, ctx) : null;
  const first = step === WIZARD_STEPS[0];

  return (
    <section aria-label="create tenant" className="rounded-card border border-line bg-white p-4">
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h1 className="font-display text-[17px] font-bold text-strong">Create tenant</h1>
        <span className="font-mono text-[11px] text-dim">
          step {WIZARD_STEPS.indexOf(step) + 1} of {WIZARD_STEPS.length} · {STEP_TITLE[step]}
        </span>
      </div>
      <Stepper step={step} />

      <fieldset disabled={p.locked} className="disabled:opacity-60">
        {step === "name" && <NameStep form={form} set={set} problem={showProblem ? problem : null} />}
        {step === "code" && (
          <ArtifactPicker
            form={form}
            set={set}
            artifacts={p.artifacts}
            error={p.artifactsError}
            onRetry={p.onRetryArtifacts}
          />
        )}
        {step === "identity" && <IdentityStep form={form} set={set} />}
        {step === "stores" && <StoresStep form={form} set={set} />}
        {step === "settings" && <SettingsStep form={form} set={set} fleetNames={p.fleetNames} />}
        {step === "credentials" && <CredentialsStep form={form} set={set} />}
        {step === "options" && <OptionsStep form={form} set={set} />}
      </fieldset>

      {step === "review" && (
        <div className="space-y-3">
          {invalid ? (
            <p role="alert" className="rounded-card bg-rustSoft p-3 text-[12.5px] text-rust">
              {STEP_TITLE[invalid.step]}: {invalid.problem} Go back and fix it before planning.
            </p>
          ) : (
            <>
              <CreateArgsSummary args={createArgs(form)} />
              {p.flow}
            </>
          )}
        </div>
      )}

      {showProblem && problem && step !== "review" && (
        <p role="alert" className="mt-3 text-[12.5px] text-rust">
          {problem}
        </p>
      )}

      {p.settled && p.settled.state === "succeeded" && p.onOpenTenant && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-card bg-mossSoft p-3 text-[12.5px] text-moss">
          <span>
            Created. Collect the keys above first — they are shown once and leaving this page discards
            them.
          </span>
          <button type="button" className={SECONDARY} onClick={() => p.settled && p.onOpenTenant?.(p.settled)}>
            Open {p.settled.tenant ?? form.name}
          </button>
        </div>
      )}

      {!p.locked && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {!first && (
            <button type="button" className={SECONDARY} onClick={p.onBack}>
              Back
            </button>
          )}
          {step !== "review" && (
            <button
              type="button"
              className={PRIMARY}
              onClick={p.onNext}
              disabled={showProblem && problem !== null}
            >
              Next
            </button>
          )}
          <button type="button" className={SECONDARY} onClick={p.onCancel}>
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// The stateful wrapper
// ---------------------------------------------------------------------------

export interface CreateTenantWizardProps {
  role: CtlRole;
  /** The operator asked to open the new tenant (after the job succeeded). */
  onDone: (job: Job) => void;
  onCancel: () => void;
  onOpenJob?: (id: string) => void;
  initialForm?: WizardForm;
  initialStep?: WizardStep;
}

/** The OpFlow `run` for a create: the key goes in the body via ops.ts, nowhere else. */
export function createRun(
  args: CreateArgsInput,
  onAccepted?: () => void,
): (req: OpRunRequest) => Promise<MutationOutcome> {
  return async (req) => {
    const out = await createTenant({ ...req, args });
    if (out.kind === "job") onAccepted?.();
    return out;
  };
}

export function CreateTenantWizard(props: CreateTenantWizardProps) {
  if (props.role !== "operator") {
    return (
      <div className="px-5 py-5 md:px-8">
        <OperatorRequired what="Creating a tenant is an operator action." />
      </div>
    );
  }
  return <OperatorWizard {...props} />;
}

function OperatorWizard(props: CreateTenantWizardProps) {
  const [form, setForm] = useState<WizardForm>(props.initialForm ?? EMPTY_WIZARD);
  const [step, setStep] = useState<WizardStep>(props.initialStep ?? "name");
  const [tried, setTried] = useState(false);
  const [locked, setLocked] = useState(false);
  const [settled, setSettled] = useState<Job | null>(null);

  const artifacts = useCtlQuery<ArtifactsResponse>(ctlKeys.artifacts(), "/v1/artifacts");
  const fleet = useCtlQuery<CtlFleet>(ctlKeys.fleet(), "/v1/fleet");
  const fleetNames = (fleet.data?.tenants ?? []).map((t) => t.name);
  const ctx: WizardContext = { fleetNames, artifacts: artifacts.data?.artifacts ?? null };

  const go = (to: WizardStep) => {
    setTried(false);
    setStep(to);
  };
  const next = () => {
    if (stepProblem(step, form, ctx)) {
      setTried(true);
      return;
    }
    const i = WIZARD_STEPS.indexOf(step);
    if (i < WIZARD_STEPS.length - 1) go(WIZARD_STEPS[i + 1]);
  };
  const back = () => {
    const i = WIZARD_STEPS.indexOf(step);
    if (i > 0) go(WIZARD_STEPS[i - 1]);
  };

  const args = createArgs(form);
  const flow = (
    <OpFlow
      // A changed form is a different request: a fresh flow, never a stale plan.
      key={JSON.stringify(args)}
      title={`create ${args.name}`}
      op="create"
      follow
      previewLabel="Plan the create"
      run={createRun(args, () => setLocked(true))}
      onDone={(job) => setSettled(job)}
      onOpenJob={props.onOpenJob}
      onClose={() => {
        // Start over / Done: a settled failure unfreezes the form so it can be
        // fixed; a success stays frozen (the tenant exists).
        if (!settled || settled.state !== "succeeded") {
          setLocked(false);
          setSettled(null);
        }
      }}
    />
  );

  return (
    <div className="px-5 py-5 md:px-8">
      <CreateTenantWizardView
        step={step}
        form={form}
        artifacts={artifacts.data?.artifacts ?? null}
        artifactsError={artifacts.error}
        fleetNames={fleetNames}
        showProblem={tried}
        locked={locked}
        settled={settled}
        onChange={(f) => {
          setForm(f);
          setTried(false);
        }}
        onNext={next}
        onBack={back}
        onCancel={props.onCancel}
        onOpenTenant={props.onDone}
        onRetryArtifacts={() => void artifacts.refetch()}
        flow={flow}
      />
    </div>
  );
}
