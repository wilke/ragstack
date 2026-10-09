// TenantView's Actions section: start / stop / restart / backup / restore, each
// a form whose arguments are `x-ctl-op-args[verb]` and whose submission is one
// OpFlow (dry run → plan → key → job) — and upgrade (`update-code`, PR-F),
// whose form lives in UpgradeAction.tsx.
//
// OPERATOR ONLY, and absent rather than disabled for a viewer: TenantView does
// not put this section in a viewer's rail, and `TenantSection` answers the
// 403 panel if it is reached anyway. The daemon refuses a viewer's POST on its
// own authority; this is about not offering what cannot be done.
//
// Arguments are sent as the contract defines them and nothing more: a default
// (`force: false`, an empty `only`) is OMITTED rather than spelled out, so the
// plan and the audit row show what the operator actually chose.

import { useState } from "react";
import { submitOp } from "../api/ops";
import {
  BACKUP_SCOPES,
  BACKUP_SECRETS,
  SERVICES,
  backupArgsProblem,
  restoreArgsProblem,
  type BackupScope,
  type BackupSecrets,
  type Service,
} from "../lib/validate";
import { OpFlow } from "./OpFlow";
import { UpgradeAction } from "./UpgradeAction";

export const ACTION_VERBS = ["start", "stop", "restart", "backup", "restore"] as const;
export type ActionVerb = (typeof ACTION_VERBS)[number];

/**
 * The tabs: the five form verbs, then `upgrade` (PR-F) — `update-code`, whose
 * form needs the tenant's registry row and the prepared images, so it is its
 * own component (UpgradeAction.tsx) rather than another `ActionFields` case.
 */
export const ACTION_TABS = [...ACTION_VERBS, "upgrade"] as const;
export type ActionTab = (typeof ACTION_TABS)[number];

export interface ActionForm {
  only: Service[];
  force: boolean;
  keepEnabled: boolean;
  fence: boolean;
  scope: BackupScope[];
  secrets: BackupSecrets;
  from: string;
  as: string;
}

export const EMPTY_FORM: ActionForm = {
  only: [],
  force: false,
  keepEnabled: false,
  fence: false,
  scope: [],
  secrets: "include",
  from: "",
  as: "",
};

/** The verb's typed `args`, defaults omitted. */
export function actionArgs(verb: ActionVerb, f: ActionForm): Record<string, unknown> {
  const a: Record<string, unknown> = {};
  switch (verb) {
    case "start":
    case "restart":
    case "stop":
      if (f.only.length > 0) a.only = SERVICES.filter((s) => f.only.includes(s));
      if (f.force) a.force = true;
      if (verb === "stop" && f.keepEnabled) a.keep_enabled = true;
      return a;
    case "backup":
      if (f.fence) a.fence = true;
      if (f.scope.length > 0) a.scope = BACKUP_SCOPES.filter((s) => f.scope.includes(s));
      if (f.secrets !== "include") a.secrets = f.secrets;
      return a;
    case "restore":
      return { from: f.from.trim(), as: f.as.trim() };
  }
}

/** Why the form cannot be previewed yet, or null. */
export function actionProblem(verb: ActionVerb, f: ActionForm): string | null {
  if (verb === "backup") return backupArgsProblem(f);
  if (verb === "restore") return restoreArgsProblem({ from: f.from.trim(), as: f.as.trim() });
  return null;
}

const LABEL = "flex items-center gap-1.5 text-[12.5px] text-body";
const INPUT =
  "w-72 rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px] focus:border-ink-900 focus:outline-none";
const EYEBROW = "mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";

function toggle<T>(list: readonly T[], item: T, on: boolean): T[] {
  return on ? [...list.filter((x) => x !== item), item] : list.filter((x) => x !== item);
}

const HELP: Record<ActionVerb, string> = {
  start: "Start the tenant's services (all of them, or only those ticked).",
  stop: "Stop the tenant's services. By default the tenant is also taken off boot; keep it enabled to stop it for now only.",
  restart: "Restart the tenant's services (all of them, or only those ticked).",
  backup:
    "Take a bundle. A fenced backup puts the gateway read-only and stops the API so the stores hold still; only fenced bundles can be verified.",
  restore:
    "Restore a bundle of this tenant into a FRESH tenant (v1 never restores in place). Minted credentials for the new tenant are revealed once when the job succeeds.",
};

/** The form fields for `verb` — props only. */
export function ActionFields({
  verb,
  form,
  onChange,
}: {
  verb: ActionVerb;
  form: ActionForm;
  onChange: (f: ActionForm) => void;
}) {
  const set = (patch: Partial<ActionForm>) => onChange({ ...form, ...patch });
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-body">{HELP[verb]}</p>
      {(verb === "start" || verb === "stop" || verb === "restart") && (
        <>
          <div>
            <div className={EYEBROW}>only (none ticked = all)</div>
            <div className="flex flex-wrap gap-3">
              {SERVICES.map((s) => (
                <label key={s} className={LABEL}>
                  <input
                    type="checkbox"
                    name={`only-${s}`}
                    checked={form.only.includes(s)}
                    onChange={(e) => set({ only: toggle(form.only, s, e.target.checked) })}
                  />
                  <span className="font-mono">{s}</span>
                </label>
              ))}
            </div>
          </div>
          <label className={LABEL}>
            <input type="checkbox" name="force" checked={form.force} onChange={(e) => set({ force: e.target.checked })} />
            <span>
              <span className="font-mono">force</span>
              {verb === "stop" ? " — stop even over running ingest jobs" : ""}
            </span>
          </label>
          {verb === "stop" && (
            <label className={LABEL}>
              <input
                type="checkbox"
                name="keep_enabled"
                checked={form.keepEnabled}
                onChange={(e) => set({ keepEnabled: e.target.checked })}
              />
              <span>
                <span className="font-mono">keep_enabled</span> — leave it on boot
              </span>
            </label>
          )}
        </>
      )}
      {verb === "backup" && (
        <>
          <label className={LABEL}>
            <input type="checkbox" name="fence" checked={form.fence} onChange={(e) => set({ fence: e.target.checked })} />
            <span>
              <span className="font-mono">fence</span> — read-only gateway + API stopped for the copy
            </span>
          </label>
          <div>
            <div className={EYEBROW}>scope (none ticked = the full bundle)</div>
            <div className="flex flex-wrap gap-3">
              {BACKUP_SCOPES.map((s) => (
                <label key={s} className={LABEL}>
                  <input
                    type="checkbox"
                    name={`scope-${s}`}
                    checked={form.scope.includes(s)}
                    onChange={(e) => set({ scope: toggle(form.scope, s, e.target.checked) })}
                  />
                  <span className="font-mono">{s}</span>
                </label>
              ))}
            </div>
          </div>
          <label className={LABEL}>
            <span className="font-mono">secrets</span>
            <select
              name="secrets"
              value={form.secrets}
              onChange={(e) => set({ secrets: e.target.value as BackupSecrets })}
              className="rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px]"
            >
              {BACKUP_SECRETS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <span className="text-[11.5px] text-dim">
              include: sealed when a recipient exists · skip: never · require: refuse without one
            </span>
          </label>
        </>
      )}
      {verb === "restore" && (
        <>
          <label className="block text-[12.5px] text-body">
            <div className={EYEBROW}>from (bundle id)</div>
            <input
              type="text"
              name="from"
              autoComplete="off"
              spellCheck={false}
              value={form.from}
              placeholder="20261008T120000Z-backup"
              onChange={(e) => set({ from: e.target.value })}
              className={INPUT}
            />
          </label>
          <label className="block text-[12.5px] text-body">
            <div className={EYEBROW}>as (new tenant name)</div>
            <input
              type="text"
              name="as"
              autoComplete="off"
              spellCheck={false}
              value={form.as}
              placeholder="dev-restored"
              onChange={(e) => set({ as: e.target.value })}
              className={INPUT}
            />
          </label>
        </>
      )}
    </div>
  );
}

export function TenantActions({
  name,
  onOpenJob,
  initialVerb = "restart",
}: {
  name: string;
  onOpenJob?: (id: string) => void;
  initialVerb?: ActionTab;
}) {
  const [verb, setVerb] = useState<ActionTab>(initialVerb);
  const [form, setForm] = useState<ActionForm>(EMPTY_FORM);
  // Bumped on every finished or abandoned flow, so the next one starts clean.
  const [round, setRound] = useState(0);
  const formVerb: ActionVerb | null = verb === "upgrade" ? null : verb;
  const args = formVerb ? actionArgs(formVerb, form) : {};
  const problem = formVerb ? actionProblem(formVerb, form) : null;

  return (
    <div className="space-y-4">
      <div role="tablist" aria-label="Action" className="flex flex-wrap gap-1">
        {ACTION_TABS.map((v) => (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={verb === v}
            onClick={() => {
              setVerb(v);
              setForm(EMPTY_FORM);
              setRound((r) => r + 1);
            }}
            className={`rounded-panel px-3 py-1.5 font-mono text-[12px] ${
              verb === v ? "bg-accent-soft font-medium text-ink-900" : "text-dim hover:text-ink-900"
            }`}
          >
            {v}
          </button>
        ))}
      </div>
      {formVerb === null ? (
        <UpgradeAction key={`upgrade-${round}`} name={name} onOpenJob={onOpenJob} />
      ) : (
        <OpFlow
          key={`${formVerb}-${round}`}
          op={formVerb}
          title={`${formVerb} ${name}`}
          run={(req) => submitOp(name, formVerb, { ...req, args })}
          disabled={problem !== null}
          disabledReason={problem ?? undefined}
          onOpenJob={onOpenJob}
          onClose={() => setRound((r) => r + 1)}
        >
          <ActionFields verb={formVerb} form={form} onChange={setForm} />
        </OpFlow>
      )}
    </div>
  );
}
