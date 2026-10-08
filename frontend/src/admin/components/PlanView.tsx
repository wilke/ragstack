// What a mutation WOULD do — the `dry_run: true` answer, rendered.
//
// Every mutation passes through this view before a key is asked for: the
// operator must have seen the plan the execute call pins (`plan_hash`). It is a
// pure, props-only component (the OpFlow owns the request).
//
// Server text on this screen — step titles, targets, file previews, argv,
// warnings, doctor details — is printed as React TEXT children (escaped, never
// HTML), and each string first passes `redact.ts`: a value next to a
// secret-named key is withheld, the preview of a secret-named file is not
// printed at all, and the argument after a secret-named flag is blanked. The
// daemon redacts the same things before it answers; this view does not rely on
// that.

import { redactArgv, redactText, isSecretPath } from "./redact";
import type { PlanT, PlannedStepT } from "./schemaTypes";
import { StateChip } from "./StateChip";

const EYEBROW = "font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const TH = `py-1.5 pr-3 ${EYEBROW}`;
const TD = "py-2 pr-3 align-top";

/** `sha256:` + the first 12 hex — enough to compare two plans by eye. */
export function shortHash(hash: string): string {
  const hex = hash.startsWith("sha256:") ? hash.slice(7) : hash;
  return `sha256:${hex.slice(0, 12)}`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={`mb-1 ${EYEBROW}`}>{label}</div>
      <div className="text-[12.5px] text-strong">{children}</div>
    </div>
  );
}

function Warnings({ items, className = "" }: { items: readonly string[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <ul className={`list-disc space-y-0.5 pl-5 text-[12px] text-accent-text ${className}`}>
      {items.map((w, i) => (
        <li key={i}>{redactText(w)}</li>
      ))}
    </ul>
  );
}

function WouldWrite({ w }: { w: PlannedStepT["would_write"][number] }) {
  const secret = isSecretPath(w.path);
  return (
    <li className="mb-1">
      <code className="font-mono text-[11.5px] text-strong">{redactText(w.path)}</code>{" "}
      <span className="font-mono text-[11px] text-dim">{w.mode}</span>
      {secret || w.preview === null ? (
        <span className="ml-2 font-mono text-[11px] text-faint">
          {secret ? "content withheld (credential file)" : "no preview (binary or secret)"}
        </span>
      ) : (
        <details className="mt-1">
          <summary className="cursor-pointer font-mono text-[11px] text-link">preview</summary>
          <pre className="mt-1 max-h-[320px] overflow-auto rounded-card bg-ink-700 p-3 font-mono text-[11px] leading-[1.5] text-ink-body">
            {redactText(w.preview)}
          </pre>
        </details>
      )}
    </li>
  );
}

function StepRow({ s }: { s: PlannedStepT }) {
  return (
    <tr className={`border-b border-lineSoft ${s.destructive ? "bg-rustSoft/60" : ""}`}>
      <td className={`${TD} font-mono text-[11.5px] tabular-nums text-dim`}>{s.n}</td>
      <td className={`${TD} font-mono text-[11.5px] text-dim`}>{s.kind}</td>
      <td className={TD}>
        <div className={`text-[12.5px] ${s.destructive ? "font-semibold text-rust" : "text-strong"}`}>
          {redactText(s.title)}
        </div>
        {s.destructive && (
          <span className="mt-1 inline-flex rounded-chip bg-rustSoft px-2 py-[2px] font-mono text-[10.5px] font-medium text-rust">
            destructive
          </span>
        )}
        <Warnings items={s.warnings} className="mt-1" />
      </td>
      <td className={TD}>
        {s.targets.length === 0 ? (
          <span className="font-mono text-[11px] text-faint">—</span>
        ) : (
          <ul>
            {s.targets.map((t, i) => (
              <li key={i} className="font-mono text-[11.5px] text-strong">
                {redactText(t)}
              </li>
            ))}
          </ul>
        )}
      </td>
      <td className={TD}>
        {s.would_write.length === 0 && s.would_run.length === 0 ? (
          <span className="font-mono text-[11px] text-faint">—</span>
        ) : (
          <>
            {s.would_write.length > 0 && (
              <ul>
                {s.would_write.map((w, i) => (
                  <WouldWrite key={i} w={w} />
                ))}
              </ul>
            )}
            {s.would_run.length > 0 && (
              <ul>
                {s.would_run.map((r, i) => (
                  <li key={i} className="mb-1">
                    <code className="break-all font-mono text-[11px] text-strong">
                      {redactArgv(r.argv).join(" ")}
                    </code>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </td>
    </tr>
  );
}

export function PlanView({ plan }: { plan: PlanT }) {
  const findings = plan.doctor.findings;
  const destructive = plan.steps.filter((s) => s.destructive).length;
  return (
    <section aria-label="plan" className="rounded-card border border-line bg-white p-4">
      <div className={`mb-3 ${EYEBROW}`}>plan · dry run</div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="op">
          <span className="font-mono">{plan.op}</span>
        </Field>
        <Field label="tenant">
          <span className="font-mono">{plan.tenant ?? "—"}</span>
        </Field>
        <Field label="registry gen">
          <span className="font-mono tabular-nums">{plan.registry_generation}</span>
        </Field>
        <Field label="plan hash">
          <span className="font-mono" title={plan.plan_hash}>
            {shortHash(plan.plan_hash)}
          </span>
        </Field>
        <Field label="doctor">
          <span className="inline-flex items-center gap-2">
            <StateChip kind="status" value={plan.doctor.status} />
            <span className="font-mono text-[11px] text-dim" title={plan.doctor.hash}>
              {shortHash(plan.doctor.hash)} · {findings.length} finding{findings.length === 1 ? "" : "s"}
            </span>
          </span>
        </Field>
        <Field label="confirm">
          {plan.requires_confirm ? (
            <span className="font-mono text-rust">
              required: <strong>{plan.confirm_value ?? "?"}</strong>
            </span>
          ) : (
            <span className="font-mono text-dim">not required</span>
          )}
        </Field>
      </div>

      {plan.doctor.status === "red" && (
        <p role="status" className="mt-3 rounded-card bg-rustSoft p-2 text-[12px] text-rust">
          Doctor is red for this scope: running this plan will be refused until the error findings
          below are cleared.
        </p>
      )}
      {findings.length > 0 && (
        <ul className="mt-3 space-y-1">
          {findings.map((f, i) => (
            <li key={i} className="flex items-start gap-2 text-[12px]">
              <StateChip kind="level" value={f.level} />
              <code className="font-mono text-[11.5px] text-strong">{f.code}</code>
              <span className="text-body">{redactText(f.detail)}</span>
              {f.repair && <span className="font-mono text-[11px] text-dim">repair: {f.repair}</span>}
            </li>
          ))}
        </ul>
      )}

      <Warnings items={plan.warnings} className="mt-3" />

      <h3 className="mb-2 mt-5 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
        {plan.steps.length} step{plan.steps.length === 1 ? "" : "s"}
        {destructive > 0 && <span className="ml-2 text-rust">· {destructive} destructive</span>}
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line">
              <th className={TH}>#</th>
              <th className={TH}>kind</th>
              <th className={TH}>step</th>
              <th className={TH}>targets</th>
              <th className={TH}>writes / runs</th>
            </tr>
          </thead>
          <tbody>
            {plan.steps.map((s) => (
              <StepRow key={s.n} s={s} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
