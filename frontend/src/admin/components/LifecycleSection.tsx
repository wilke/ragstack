// TenantView's Lifecycle section: take a tenant out of service (decommission —
// archive, then quarantine) and, once it is quarantined, delete it (purge).
//
// OPERATOR ONLY, and absent rather than disabled for a viewer: TenantView does
// not list the section for a viewer, `TenantSection` answers the 403 panel if
// it is reached anyway, and `LifecycleView` renders nothing for a viewer role.
//
// The two verbs, as the daemon defines them (go/internal/ctl/ops/decommission.go,
// purge.go):
//
//   * `decommission {archive}` — destructive, typed confirm = the tenant name.
//     With `archive` (the default) the job takes a FENCED full backup with the
//     tenant's secrets sealed (`secrets: require`), deep-checks it, records it as
//     `last_backup`, and then quarantines: units removed, the gateway publishes a
//     generation without the tenant, the data directory is renamed aside with a
//     RECOVERY.json inside. The API stops for the fence and is NOT started again.
//     Without `archive` the row's `last_backup` must already be fenced and
//     checked-or-verified. Refused at plan time for a `manual` tenant, for an
//     archive without a backup recipient, and for an archive of a stopped
//     tenant. Nothing is deleted.
//   * `purge {keep_archive}` — the only verb that destroys data, and only on a
//     row at `state: quarantined` with a `quarantine.dir`. Every removal step is
//     irreversible; afterwards the tenant is gone (404, absent from the fleet).
//
// Server prose is never rendered: a refused dry run shows through OpFlow's
// ErrorBanner (code + request id). For the decommission refusals this section
// can predict, `classifyRefusal` reads the refusal to pick a FIXED hint (the CLI
// command to run, or what to change) — the detail itself never reaches the page.
//
// `LifecycleView` is the props-only seam; `LifecycleSection` reads the tenant
// and holds the form, the open flow's round and the classified refusal.

import { useState } from "react";
import { CtlError } from "../api/http";
import { submitOp } from "../api/ops";
import { ctlKeys, useCtlQuery } from "../api/queries";
import type { CtlRole, CtlTenant, Job, TenantOpVerb } from "../api/types";
import { bytes, since } from "../lib/format";
import { JOB_ID } from "../lib/validate";
import { ErrorBanner } from "./ErrorBanner";
import { OperatorRequired } from "./JobsView";
import { OpFlow, type OpRun, type OpRunRequest } from "./OpFlow";
import { StateChip } from "./StateChip";

// ---------------------------------------------------------------------------
// Forms, specs and the gates (pure)
// ---------------------------------------------------------------------------

export interface LifecycleForms {
  /** decommission `archive` — default ON, as the contract's default. */
  archive: boolean;
  /** purge `keep_archive` — default OFF, as the contract's default. */
  keepArchive: boolean;
}

export const EMPTY_LIFECYCLE_FORMS: LifecycleForms = { archive: true, keepArchive: false };

export type LifecycleVerb = Extract<TenantOpVerb, "decommission" | "purge">;

/** The daemon's destructive flag for both verbs (go/internal/ctl/ops/ops.go). */
export const LIFECYCLE_DESTRUCTIVE: Readonly<Record<LifecycleVerb, boolean>> = {
  decommission: true,
  purge: true,
};

export interface LifecycleOpSpec {
  verb: LifecycleVerb;
  args: Record<string, boolean>;
  title: string;
  destructive: boolean;
  /** The typed confirmation: the tenant name, for both verbs (engine rule). */
  confirmValue: string;
}

/**
 * What one verb submits. Both arguments are sent EXPLICITLY, default or not:
 * for the two verbs that end a tenant the audit row should say what the
 * operator chose, not leave it to a default the reader has to know.
 */
export function lifecycleOpSpec(name: string, verb: LifecycleVerb, f: LifecycleForms): LifecycleOpSpec {
  if (verb === "decommission") {
    return {
      verb,
      args: { archive: f.archive },
      title: f.archive ? `archive and decommission ${name}` : `decommission ${name} (no new archive)`,
      destructive: LIFECYCLE_DESTRUCTIVE.decommission,
      confirmValue: name,
    };
  }
  return {
    verb,
    args: { keep_archive: f.keepArchive },
    title: f.keepArchive ? `purge ${name} (keep the archive)` : `purge ${name}`,
    destructive: LIFECYCLE_DESTRUCTIVE.purge,
    confirmValue: name,
  };
}

type TenantState = CtlTenant["summary"]["state"];
type Supervisor = CtlTenant["summary"]["supervisor"];

/** Decommission is offered on a tenant in service or deliberately stopped. */
export function decommissionOffered(state: TenantState): boolean {
  return state === "active" || state === "stopped";
}

/** Purge is offered ONLY on a quarantined tenant. */
export function purgeOffered(state: TenantState): boolean {
  return state === "quarantined";
}

const HANDOVER_FIRST =
  "This tenant is supervisor: manual — the control plane decommissions and purges only what it runs. Hand it over to the service account first (handover), then decommission it here.";

/**
 * Why Preview is disabled for decommission, or null. Only refusals the daemon
 * makes on EVERY such row are gated here; a missing qualifying bundle is
 * warned about instead (a selftest sandbox needs none).
 */
export function decommissionBlocker(
  t: { state: TenantState; supervisor: Supervisor },
  name: string,
  archive: boolean,
): string | null {
  if (t.supervisor === "manual") return HANDOVER_FIRST;
  if (archive && t.state === "stopped") {
    return `${name} is stopped, and the archive snapshots its stores through their running APIs: start it first (Actions → start), or untick archive to quarantine over its last fenced bundle that is checked or verified.`;
  }
  return null;
}

/** Why Preview is disabled for purge, or null. */
export function purgeBlocker(t: { supervisor: Supervisor; quarantineDir: string | null }): string | null {
  if (t.supervisor === "manual") return HANDOVER_FIRST;
  if (!t.quarantineDir) {
    return "The row records no quarantine.dir (it was quarantined before the registry recorded where), so purge would be refused: record the renamed directory on the row, or remove it by hand.";
  }
  return null;
}

type BackupFacts = { fenced: boolean; verified: boolean; checked?: boolean };

/** `decommission --archive=false`'s precondition: fenced AND (verified OR checked). */
export function backupQualifies(lb: BackupFacts | null | undefined): boolean {
  return Boolean(lb && lb.fenced && (lb.verified || lb.checked));
}

/** The decommission refusals this section has a fixed hint for. */
export type DecommissionRefusal = "no_recipient" | "stopped" | "unmanaged" | "no_backup" | "other";

export interface ClassifiedRefusal {
  reason: DecommissionRefusal;
  code: string;
  requestId: string | null;
}

/**
 * Sort a refused decommission dry run into a reason with a FIXED hint. The
 * detail is matched, never shown: the copy on screen is this file's.
 * `null` for anything that is not a 409 `refused`.
 */
export function classifyRefusal(err: unknown): ClassifiedRefusal | null {
  if (!(err instanceof CtlError) || err.code !== "refused") return null;
  const d = typeof err.detail === "string" ? err.detail : "";
  const reason: DecommissionRefusal = /backup-identity|age recipient/.test(d)
    ? "no_recipient"
    : /is stopped/.test(d)
      ? "stopped"
      : /neither a tenant this ctl runs|not something the ctl can stop/.test(d)
        ? "unmanaged"
        : /fenced|bundle|backup/.test(d)
          ? "no_backup"
          : "other";
  return { reason, code: err.code, requestId: err.requestId };
}

/**
 * The OpFlow `run` for one verb: `submitOp` with the spec's args bound. A
 * failed decommission call is reported to `onError` (for `classifyRefusal`)
 * and rethrown, so OpFlow still shows its refusal. `submit` is the seam the
 * tests replace.
 */
export function lifecycleRun(
  name: string,
  spec: LifecycleOpSpec,
  onError: (err: unknown) => void,
  submit: typeof submitOp = submitOp,
): OpRun {
  return async (req: OpRunRequest) => {
    try {
      return await submit(name, spec.verb, { ...req, args: spec.args });
    } catch (err) {
      if (spec.verb === "decommission") onError(err);
      throw err;
    }
  };
}

/** Whether a settled job is the purge of `name` that succeeded: the tenant is gone. */
export function purgedTenant(job: Job, name: string): boolean {
  return job.op === "purge" && job.state === "succeeded" && job.tenant === name;
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

const H3 = "mb-2 mt-7 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong first:mt-0";
const EYEBROW = "mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const MONO = "font-mono text-[11.5px]";
const LABEL = "flex items-start gap-2 text-[12.5px] text-body";
const LINK = "font-mono text-[11.5px] text-link underline-offset-2 hover:underline";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={EYEBROW}>{label}</div>
      <div className="text-[12.5px] text-strong">{children}</div>
    </div>
  );
}

function Code({ children }: { children: string }) {
  return <code className="font-mono text-[11.5px]">{children}</code>;
}

/** The last backup's facts: when, fenced, checked, verified, which bundle — and whether it qualifies. */
function LastBackupFacts({ tenant }: { tenant: CtlTenant }) {
  const reg = tenant.registry?.last_backup ?? null;
  const sum = tenant.summary.last_backup;
  if (!reg && !sum) {
    return (
      <div className="space-y-1">
        <span className={`${MONO} text-dim`}>never</span>
        <div>
          <Chip tone="warn">does not qualify for decommission without archive</Chip>
        </div>
      </div>
    );
  }
  const facts: BackupFacts & { at: string } = reg
    ? { at: reg.at, fenced: reg.fenced, verified: reg.verified, checked: reg.checked === true }
    : { at: sum!.at, fenced: sum!.fenced, verified: sum!.verified, checked: sum!.checked };
  const ok = backupQualifies(facts);
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={MONO} title={facts.at}>
          {since(facts.at)}
        </span>
        <Chip tone={facts.fenced ? "ok" : "warn"}>{facts.fenced ? "fenced" : "best effort"}</Chip>
        <Chip tone={facts.checked ? "ok" : "neutral"}>{facts.checked ? "checked" : "unchecked"}</Chip>
        <Chip tone={facts.verified ? "ok" : "neutral"}>{facts.verified ? "verified" : "unverified"}</Chip>
      </div>
      {reg && (
        <div className={`${MONO} break-all text-dim`} aria-label="last backup bundle">
          {reg.bundle}
        </div>
      )}
      <div>
        <Chip
          tone={ok ? "ok" : "warn"}
          title="decommission without an archive needs a fenced bundle that is checked or verified"
        >
          {ok ? "qualifies for decommission without archive" : "does not qualify for decommission without archive"}
        </Chip>
      </div>
    </div>
  );
}

function QuarantineBlock({
  name,
  tenant,
  onOpenJob,
}: {
  name: string;
  tenant: CtlTenant;
  onOpenJob?: (id: string) => void;
}) {
  const q = tenant.registry?.quarantine ?? null;
  return (
    <div role="note" aria-label="quarantine" className="mt-4 rounded-card border border-rust/30 bg-rustSoft/40 p-3">
      <p className="text-[12.5px] text-body">
        <span className="font-medium text-strong">{name}</span> is quarantined: it is stopped and
        unrouted — the API is down, the gateway no longer routes to it, its units are gone and its
        data directory is renamed aside. Nothing has been deleted yet: it is recoverable until it is
        purged.
      </p>
      {q ? (
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="quarantined data">
            <span className={`${MONO} break-all`}>{q.dir}</span>
          </Field>
          <Field label="since">
            <span className={MONO} title={q.at}>
              {since(q.at)}
            </span>
          </Field>
          <Field label="by job">
            {JOB_ID.test(q.job_id) && onOpenJob ? (
              <button type="button" onClick={() => onOpenJob(q.job_id)} className={LINK}>
                {q.job_id}
              </button>
            ) : (
              <span className={MONO}>{q.job_id}</span>
            )}
          </Field>
          <Field label="archive bundle">
            {q.bundle ? (
              <span className={`${MONO} break-all`}>{q.bundle}</span>
            ) : (
              <span className={`${MONO} text-dim`}>none recorded</span>
            )}
          </Field>
        </div>
      ) : (
        <p className="mt-2 text-[12.5px] text-dim">
          The row carries no quarantine record (it was quarantined before the registry recorded one).
        </p>
      )}
    </div>
  );
}

const REFUSAL_HINT: Record<DecommissionRefusal, React.ReactNode> = {
  no_recipient: (
    <>
      No backup identity is configured, so an archive could not carry the tenant's secrets. On the
      host, as the service account, run <Code>ragstack-ctl fleet backup-identity init</Code> and
      restart the daemon — or untick archive to quarantine over an existing fenced bundle that is
      checked or verified.
    </>
  ),
  stopped: (
    <>
      The tenant is stopped and the archive needs its stores running: start it first (Actions →
      start), or untick archive.
    </>
  ),
  unmanaged: (
    <>
      The control plane decommissions only tenants it runs (supervisor systemd or instance, owned by
      the service account). Hand the tenant over first.
    </>
  ),
  no_backup: (
    <>
      Without an archive the last backup must be fenced and checked or verified. Take a fenced
      backup (Actions → backup, fence), or keep archive on.
    </>
  ),
  other: <>The plan was refused. The request id above finds the reason in the daemon's audit log.</>,
};

function RefusalHint({ refusal }: { refusal: ClassifiedRefusal }) {
  return (
    <div role="note" aria-label="refusal hint" className="mt-3 rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
      <div className="mb-1 font-mono text-[11px]">
        refused · <span className="font-medium">{refusal.code}</span> · {refusal.reason}
      </div>
      <div>{REFUSAL_HINT[refusal.reason]}</div>
    </div>
  );
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

/**
 * What a settled decommission or purge job reports. Every member is picked by
 * NAME and shape-checked; nothing else in `result` is printed.
 */
export function LifecycleResult({ job }: { job: Job }) {
  const r = (job.result ?? null) as Record<string, unknown> | null;
  if (!r || job.state !== "succeeded") return null;
  if (job.op === "decommission") {
    const dir = typeof r.quarantine_dir === "string" ? r.quarantine_dir : null;
    const bundle = typeof r.archive_bundle === "string" ? r.archive_bundle : null;
    if (!dir && !bundle) return null;
    return (
      <div role="note" aria-label="decommission result" className="rounded-card border border-line bg-paper p-3">
        <div className={EYEBROW}>result</div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="quarantined data">
            <span className={`${MONO} break-all`}>{dir ?? "—"}</span>
          </Field>
          <Field label="archive bundle">
            <span className={`${MONO} break-all`}>{bundle ?? "none (no new archive)"}</span>
          </Field>
        </div>
      </div>
    );
  }
  if (job.op === "purge") {
    const removed = strings(r.removed);
    const freed = typeof r.bytes_freed === "number" ? r.bytes_freed : null;
    const kept = r.archive_kept === true;
    const t = r.tombstone && typeof r.tombstone === "object" ? (r.tombstone as Record<string, unknown>) : null;
    const tombBase = t && typeof t.base === "number" ? t.base : null;
    const tombName = t && typeof t.manifest_name === "string" ? t.manifest_name : null;
    return (
      <div role="note" aria-label="purge result" className="rounded-card border border-line bg-paper p-3">
        <div className={EYEBROW}>result</div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Field label="bytes freed">
            <span className={`${MONO} tabular-nums`}>{freed === null ? "—" : bytes(freed)}</span>
          </Field>
          <Field label="archive">
            <span className={MONO}>{kept ? "kept" : "deleted"}</span>
          </Field>
          <Field label="tombstone">
            <span className={MONO}>
              {t ? `${tombName ?? "?"} · block ${tombBase ?? "?"}` : "none (sandbox block)"}
            </span>
          </Field>
        </div>
        {removed.length > 0 && (
          <div className="mt-3">
            <div className={EYEBROW}>removed</div>
            <ul className="space-y-0.5">
              {removed.map((p, i) => (
                <li key={i} className={`${MONO} break-all text-body`}>
                  {p}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }
  return null;
}

export interface LifecycleViewProps {
  name: string;
  role: CtlRole;
  /** The operator's tenant body (`registry` carries `quarantine` and `last_backup.bundle`). */
  tenant: CtlTenant;
  forms: LifecycleForms;
  /** The decommission dry run's classified refusal, if the last one was refused. */
  refusal: ClassifiedRefusal | null;
  /** The settled job of the open flow, for its result. */
  settled: Job | null;
  /** Bumped whenever a flow closes or the form changes, so the next flow starts clean. */
  round: number;
  onForms: (f: LifecycleForms) => void;
  /** A decommission call failed: the error, for `classifyRefusal`. */
  onRunError: (err: unknown) => void;
  onDone: (job: Job) => void;
  onClose: () => void;
  onOpenJob?: (id: string) => void;
}

export function LifecycleView(p: LifecycleViewProps) {
  if (p.role !== "operator") return null;
  const s = p.tenant.summary;
  const reg = p.tenant.registry;
  const supervisor: Supervisor = reg?.supervisor ?? s.supervisor;
  const state: TenantState = reg?.state ?? s.state;
  const quarantined = purgeOffered(state);
  const lbQualifies = backupQualifies(
    reg?.last_backup
      ? { ...reg.last_backup, checked: reg.last_backup.checked === true }
      : s.last_backup,
  );

  const runWith = (spec: LifecycleOpSpec) => lifecycleRun(p.name, spec, p.onRunError);

  const decommission = lifecycleOpSpec(p.name, "decommission", p.forms);
  const decBlock = decommissionBlocker({ state, supervisor }, p.name, p.forms.archive);
  const purge = lifecycleOpSpec(p.name, "purge", p.forms);
  const purgeBlock = purgeBlocker({ supervisor, quarantineDir: reg?.quarantine?.dir ?? null });

  return (
    <div>
      {/* ---------------------------------------------------------- status */}
      <h3 className={H3}>Status</h3>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Field label="state">
          <StateChip kind="state" value={state} />
        </Field>
        <Field label="supervisor">
          <span className={MONO}>{supervisor}</span>
        </Field>
        <Field label="owner">
          <span className={MONO}>{reg?.owner ?? s.owner}</span>
        </Field>
        <Field label="last backup">
          <LastBackupFacts tenant={p.tenant} />
        </Field>
      </div>
      {quarantined && <QuarantineBlock name={p.name} tenant={p.tenant} onOpenJob={p.onOpenJob} />}

      {/* ---------------------------------------------------- decommission */}
      {decommissionOffered(state) && (
        <section aria-label="decommission">
          <h3 className={H3}>Decommission</h3>
          <p className="mb-3 text-[12.5px] text-body">
            Takes <span className="font-medium text-strong">{p.name}</span> out of service without
            deleting anything: the API and its stores stop and are taken off boot, the units are
            removed, the gateway publishes a generation without it, and the data directory is renamed
            aside with a <Code>RECOVERY.json</Code> inside. The row stays, marked quarantined, and keeps
            its port block. Deleting it is Purge, a separate step offered once it is quarantined.
          </p>
          <div className="mt-3 space-y-3">
            <OpFlow
              key={`decommission-${p.round}`}
              op="decommission"
              title={decommission.title}
              destructive={decommission.destructive}
              confirmValue={decommission.confirmValue}
              run={runWith(decommission)}
              disabled={decBlock !== null}
              disabledReason={decBlock ?? undefined}
              onDone={p.onDone}
              onOpenJob={p.onOpenJob}
              onClose={p.onClose}
            >
              <div className="space-y-3">
                <label className={LABEL}>
                  <input
                    type="checkbox"
                    name="archive"
                    checked={p.forms.archive}
                    onChange={(e) => p.onForms({ ...p.forms, archive: e.target.checked })}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="font-mono">archive</span> — take a fenced full backup first
                    (recommended)
                  </span>
                </label>
                {p.forms.archive ? (
                  <p className="text-[12.5px] text-body">
                    The job first takes a <strong>fenced full backup</strong> with the tenant's secrets
                    sealed to the control plane's backup identity, <strong>checks</strong> it and
                    records it as the last backup, and only then quarantines. The API stops for the
                    fence and <strong>stays stopped</strong>. This needs the backup identity: without
                    one the dry run is refused — run <Code>ragstack-ctl fleet backup-identity init</Code>{" "}
                    on the host (then restart the daemon).
                  </p>
                ) : (
                  <p className="text-[12.5px] text-body">
                    No new archive: the quarantine relies on an <strong>existing fenced bundle that is
                    checked or verified</strong> — the last backup above.{" "}
                    {lbQualifies
                      ? "It qualifies."
                      : "It does not qualify, so the dry run will be refused (a selftest sandbox excepted): take a fenced backup first, or keep archive on."}
                  </p>
                )}
              </div>
            </OpFlow>
            {p.refusal && <RefusalHint refusal={p.refusal} />}
            {p.settled && p.settled.op === "decommission" && <LifecycleResult job={p.settled} />}
          </div>
        </section>
      )}

      {/* ----------------------------------------------------------- purge */}
      {quarantined && (
        <section aria-label="purge" className="mt-7 rounded-card border-2 border-rust bg-rustSoft/40 p-4">
          <h3 className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[.14em] text-rust">
            Purge — irreversible
          </h3>
          <p className="mb-3 text-[12.5px] text-body">
            Deletes everything of <span className="font-medium text-strong">{p.name}</span>: the
            quarantined data, the code checkout, the units and — unless you keep it — the archive.
            Every removal is <strong className="text-rust">irreversible</strong>; there is no
            rollback. Afterwards the tenant is gone from the fleet: only a tombstone (which keeps its
            port block from being handed out again) and the audit log remain.
          </p>
          <OpFlow
            key={`purge-${p.round}`}
            op="purge"
            title={purge.title}
            destructive={purge.destructive}
            confirmValue={purge.confirmValue}
            run={runWith(purge)}
            disabled={purgeBlock !== null}
            disabledReason={purgeBlock ?? undefined}
            onDone={p.onDone}
            onOpenJob={p.onOpenJob}
            onClose={p.onClose}
          >
            <label className={LABEL}>
              <input
                type="checkbox"
                name="keep_archive"
                checked={p.forms.keepArchive}
                onChange={(e) => p.onForms({ ...p.forms, keepArchive: e.target.checked })}
                className="mt-0.5"
              />
              <span>
                <span className="font-mono">keep_archive</span> — keep the tenant's backup bundles and
                delete everything else
              </span>
            </label>
          </OpFlow>
          {p.settled && p.settled.op === "purge" && (
            <div className="mt-3">
              <LifecycleResult job={p.settled} />
            </div>
          )}
        </section>
      )}

      {!decommissionOffered(state) && !quarantined && (
        <p className="mt-7 text-[12.5px] text-dim">
          Decommission is offered for an active or stopped tenant; this one is{" "}
          <span className="font-mono">{state}</span>.
        </p>
      )}
    </div>
  );
}

export function LifecycleSection({
  name,
  role,
  onOpenJob,
  onPurged,
}: {
  name: string;
  role: CtlRole;
  onOpenJob?: (id: string) => void;
  /** The tenant is gone: a purge of it succeeded. */
  onPurged?: (name: string) => void;
}) {
  const operator = role === "operator";
  const tenant = useCtlQuery<CtlTenant>(ctlKeys.tenant(name), `/v1/tenants/${name}`, { enabled: operator });
  const [forms, setForms] = useState<LifecycleForms>(EMPTY_LIFECYCLE_FORMS);
  const [refusal, setRefusal] = useState<ClassifiedRefusal | null>(null);
  const [settled, setSettled] = useState<Job | null>(null);
  const [round, setRound] = useState(0);

  if (!operator) {
    return <OperatorRequired what="Decommissioning and purging a tenant are operator actions." />;
  }
  if (tenant.error) return <ErrorBanner error={tenant.error} onRetry={() => void tenant.refetch()} />;
  if (!tenant.data) return <p className="text-[12.5px] text-dim">Loading {name}…</p>;

  return (
    <LifecycleView
      name={name}
      role={role}
      tenant={tenant.data}
      forms={forms}
      refusal={refusal}
      settled={settled}
      round={round}
      onForms={(f) => {
        setForms(f);
        setRefusal(null);
      }}
      onRunError={(err) => setRefusal(classifyRefusal(err))}
      onDone={(job) => {
        setSettled(job);
        if (purgedTenant(job, name)) onPurged?.(name);
      }}
      onClose={() => {
        setRefusal(null);
        setSettled(null);
        setRound((r) => r + 1);
      }}
      onOpenJob={onOpenJob}
    />
  );
}
