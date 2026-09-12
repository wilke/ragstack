// One tenant, read-only.
//
// The section rail is the Ops dashboard's device (src/components/OpsDashboard.tsx):
// a fixed list of sections, each a heading the rail links to. Here it is a real
// tab switch rather than an anchor scroll, because two of the sections (Config,
// Logs) are separate reads and one of them (Logs) is operator-only — fetching
// them because the user scrolled past would mean a viewer's dashboard firing a
// 403 on every visit.
//
// NOTHING on this screen mutates. The contract answers 409 for every op verb
// until PR-C lands the job engine, and a disabled button that will work "later"
// is a worse thing to ship than no button.

import { useState } from "react";
import { ctlKeys, LOGS_POLL_MS, pollWhenVisible, useCtlQuery } from "../api/queries";
import type { CtlDoctor, CtlEnv, CtlLogs, CtlTenant, LogFile } from "../api/types";
import type { CtlRole } from "../api/types";
import { bytes, since } from "../lib/format";
import { DoctorFindings } from "./FleetView";
import { ErrorBanner } from "./ErrorBanner";
import { SecretSafeValue } from "./SecretSafeValue";
import { HealthDot, StateChip } from "./StateChip";
import { CtlError } from "../api/http";

const SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "config", label: "Config" },
  { id: "drift", label: "Drift" },
  { id: "doctor", label: "Doctor" },
  { id: "logs", label: "Logs" },
] as const;

export type SectionId = (typeof SECTIONS)[number]["id"];

const LOG_FILES: LogFile[] = ["api", "qdrant", "es", "ui"];
const LOG_LINES = 200;

const TH = "py-1.5 pr-3 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const TD = "py-1.5 pr-3 align-top";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted">
        {label}
      </div>
      <div className="text-[12.5px] text-strong">{children}</div>
    </div>
  );
}

function Overview({ tenant }: { tenant: CtlTenant }) {
  const s = tenant.summary;
  const st = tenant.status;
  return (
    <div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Field label="state">
          <StateChip kind="state" value={s.state} />
        </Field>
        <Field label="owner">
          <span className="font-mono text-[11.5px]">{s.owner}</span>
        </Field>
        <Field label="supervisor">
          <span className="font-mono text-[11.5px]">{s.supervisor}</span>
        </Field>
        <Field label="stores">
          <StateChip kind="mode" value={s.stores_mode} />
        </Field>
        <Field label="manifest name">
          <span className="font-mono text-[11.5px]">{s.manifest_name}</span>
        </Field>
        <Field label="code tag">
          <span className="font-mono text-[11.5px]">{s.code_tag}</span>
        </Field>
        <Field label="ports api/qdrant/es">
          <span className="font-mono text-[11.5px] tabular-nums">
            {s.ports.api} · {s.ports.qdrant_http} · {s.ports.es_http}
          </span>
        </Field>
        <Field label="disk">
          <span className="font-mono text-[11.5px] tabular-nums">{bytes(s.disk_bytes)}</span>
        </Field>
        <Field label="health">
          <span className="inline-flex items-center gap-1.5">
            <HealthDot label="api" state={s.health.api} />
            <HealthDot label="qdrant" state={s.health.qdrant} />
            <HealthDot label="es" state={s.health.es} />
            <HealthDot label="deep" state={s.health.deep} />
          </span>
        </Field>
        <Field label="api pid">
          <span className="font-mono text-[11.5px] tabular-nums">
            {st.api_pid ?? "—"}
            {st.api_pid_owner ? ` (${st.api_pid_owner})` : ""}
          </span>
        </Field>
        <Field label="restart pending">
          <span className="font-mono text-[11.5px]">{st.restart_pending ? "yes" : "no"}</span>
        </Field>
        <Field label="last backup">
          <span className="font-mono text-[11.5px]">
            {s.last_backup
              ? `${since(s.last_backup.at)} · ${s.last_backup.fenced ? "fenced" : "best effort"} · ${
                  s.last_backup.verified ? "verified" : "unverified"
                }`
              : "never"}
          </span>
        </Field>
      </div>

      <h3 className="mb-2 mt-7 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
        Listening
      </h3>
      <div className="flex flex-wrap gap-2">
        {(
          [
            ["api", st.listening.api],
            ["qdrant", st.listening.qdrant_http],
            ["es", st.listening.es_http],
            ["ui", st.listening.ui],
          ] as [string, boolean][]
        ).map(([k, v]) => (
          <StateChip key={k} kind="health" value={v ? "ok" : "down"} title={`${k} port`} />
        ))}
        <span className="font-mono text-[11px] text-dim">
          api · qdrant · es · ui (from /proc/net/tcp)
        </span>
      </div>

      <h3 className="mb-2 mt-7 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
        Units
      </h3>
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line">
            <th className={TH}>kind</th>
            <th className={TH}>unit</th>
            <th className={TH}>ActiveState</th>
            <th className={TH}>SubState</th>
            <th className={TH}>main pid</th>
            <th className={TH}>since</th>
          </tr>
        </thead>
        <tbody>
          {tenant.units.target && (
            <tr className="border-b border-lineSoft">
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>target</td>
              <td className={`${TD} font-mono text-[11.5px]`}>{tenant.units.target.name}</td>
              <td className={TD}>
                <StateChip kind="unit" value={tenant.units.target.active_state} />
              </td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>—</td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>—</td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>
                {tenant.units.target.enabled ? "enabled" : "disabled"}
              </td>
            </tr>
          )}
          {tenant.units.services.map((svc) => (
            <tr key={svc.kind} className="border-b border-lineSoft">
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>{svc.kind}</td>
              <td className={`${TD} font-mono text-[11.5px]`}>{svc.name}</td>
              <td className={TD}>
                <StateChip kind="unit" value={svc.active_state} />
              </td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>{svc.sub_state ?? "—"}</td>
              <td className={`${TD} font-mono text-[11.5px] tabular-nums text-dim`}>
                {svc.main_pid ?? "—"}
              </td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>
                {svc.since ? since(svc.since) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* The registry row is the operator's business: it carries secret_refs,
          key fingerprints, pidfile and log paths and the rollback descriptor.
          A viewer gets `null` — say so, rather than rendering an empty block
          that reads like "this tenant has no registry row". */}
      <h3 className="mb-2 mt-7 font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
        Registry row
      </h3>
      {tenant.registry ? (
        <p className="text-[12.5px] text-body">
          The full registry row is available to this credential. It is not rendered here in PR-B —
          the fields on this page are its summary projection.
        </p>
      ) : (
        <p className="text-[12.5px] text-dim">
          Withheld: the registry row carries secret names, key fingerprints and the rollback
          descriptor, so the control plane returns it to operators only.
        </p>
      )}
    </div>
  );
}

function Config({ name }: { name: string }) {
  const env = useCtlQuery<CtlEnv>(ctlKeys.tenantEnv(name), `/v1/tenants/${name}/env`);
  if (env.error) return <ErrorBanner error={env.error} onRetry={() => void env.refetch()} />;
  if (!env.data) return <p className="text-[12.5px] text-dim">Loading configuration…</p>;
  return (
    <div>
      <p className="mb-3 text-[12.5px] text-body">
        env layout <span className="font-mono">{env.data.env_layout}</span> · a viewer receives the{" "}
        <span className="font-mono">public</span> keys only, so this table is what the control plane
        was willing to show this credential — not the whole file.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line">
              <th className={TH}>key</th>
              <th className={TH}>class</th>
              <th className={TH}>value</th>
              <th className={TH}>source</th>
              <th className={TH}>drift</th>
            </tr>
          </thead>
          <tbody>
            {env.data.keys.map((k) => (
              <tr key={`${k.source}:${k.key}`} className="border-b border-lineSoft">
                <td className={`${TD} font-mono text-[11.5px] font-medium text-strong`}>{k.key}</td>
                <td className={TD}>
                  <StateChip kind="class" value={k.class} />
                </td>
                <td className={TD}>
                  <SecretSafeValue name={k.key} value={k.value_redacted} />
                </td>
                <td className={`${TD} font-mono text-[11.5px] text-dim`}>{k.source}</td>
                <td className={TD}>
                  {k.drift ? <StateChip kind="level" value="warn" title={k.drift} /> : null}
                  {k.drift && (
                    <span className="ml-1.5 font-mono text-[11px] text-dim">{k.drift}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Drift({ tenant }: { tenant: CtlTenant }) {
  if (tenant.drift.length === 0) {
    return <p className="text-[12.5px] text-dim">No drift: the registry matches what was observed.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line">
            <th className={TH}>code</th>
            <th className={TH}>level</th>
            <th className={TH}>field</th>
            <th className={TH}>expected</th>
            <th className={TH}>actual</th>
            <th className={TH}>observed</th>
          </tr>
        </thead>
        <tbody>
          {tenant.drift.map((d, i) => (
            <tr key={`${d.code}-${i}`} className="border-b border-lineSoft">
              <td className={`${TD} font-mono text-[11.5px] text-strong`}>{d.code}</td>
              <td className={TD}>
                <StateChip kind="level" value={d.level} />
              </td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>{d.field ?? "—"}</td>
              {/* Keyed by the FIELD name: a drift row about a secret-shaped key
                  would otherwise print both of its values side by side. */}
              <td className={TD}>
                <SecretSafeValue name={d.field ?? ""} value={d.expected} />
              </td>
              <td className={TD}>
                <SecretSafeValue name={d.field ?? ""} value={d.actual} />
              </td>
              <td className={`${TD} font-mono text-[11.5px] text-dim`}>{since(d.observed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Doctor({ name }: { name: string }) {
  const doctor = useCtlQuery<CtlDoctor>(ctlKeys.doctor(), "/v1/doctor");
  if (doctor.error) return <ErrorBanner error={doctor.error} onRetry={() => void doctor.refetch()} />;
  if (!doctor.data) return <p className="text-[12.5px] text-dim">Loading doctor…</p>;
  const scoped: CtlDoctor = {
    ...doctor.data,
    findings: doctor.data.findings.filter((f) => f.tenant === name),
  };
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <StateChip kind="status" value={doctor.data.status} />
        <span className="font-mono text-[11px] text-dim">fleet verdict · findings for {name}</span>
      </div>
      <DoctorFindings doctor={scoped} />
    </div>
  );
}

function Logs({ name, role }: { name: string; role: CtlRole }) {
  const [file, setFile] = useState<LogFile>("api");
  const [auto, setAuto] = useState(false);

  const logs = useCtlQuery<CtlLogs>(
    ctlKeys.tenantLogs(name, file, LOG_LINES),
    `/v1/tenants/${name}/logs?file=${file}&lines=${LOG_LINES}`,
    {
      enabled: role === "operator",
      refetchInterval: auto ? pollWhenVisible(LOGS_POLL_MS) : undefined,
    },
  );

  // Two independent reasons to show the same message, and both must: the role
  // we were told (so a viewer never fires the request at all) and the answer we
  // got (so a role that changed server-side is not a blank panel).
  const forbidden =
    role !== "operator" || (logs.error instanceof CtlError && logs.error.status === 403);

  if (forbidden) {
    return (
      <p className="rounded-card bg-accent-soft p-3 text-[12.5px] text-accent-text">
        Operator credential required. Tenant logs are redacted before they leave the daemon, but
        they are still an operator-only read.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <label className="font-mono text-[11px] text-dim" htmlFor="log-file">
          file
        </label>
        <select
          id="log-file"
          value={file}
          onChange={(e) => setFile(e.target.value as LogFile)}
          className="rounded-panel border border-line bg-white px-2 py-1 font-mono text-[12px]"
        >
          {LOG_FILES.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-[12px] text-body">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          auto-refresh ({LOGS_POLL_MS / 1000} s)
        </label>
        <span className="font-mono text-[11px] text-dim">
          last {LOG_LINES} lines · redacted by the daemon
        </span>
      </div>

      {logs.error && <ErrorBanner error={logs.error} onRetry={() => void logs.refetch()} />}

      {logs.data && (
        <>
          {logs.data.truncated && (
            <p className="mb-2 font-mono text-[11px] text-accent-text">
              truncated — the file has more than {logs.data.requested} lines, or a line exceeded the
              per-line cap
            </p>
          )}
          <pre className="max-h-[520px] overflow-auto rounded-card bg-ink-700 p-3 font-mono text-[11.5px] leading-[1.5] text-ink-body">
            {logs.data.lines.join("\n")}
          </pre>
        </>
      )}
    </div>
  );
}

/**
 * One section's body.
 *
 * Split out of `TenantView` because which section is showing is internal state
 * a string render cannot click into — this is the seam the render tests mount
 * Config, Drift and Logs through, and the same component the rail switches. It
 * reads the tenant through the shared query key, so a test that seeds the cache
 * seeds both.
 */
export function TenantSection({
  section,
  name,
  role,
}: {
  section: SectionId;
  name: string;
  role: CtlRole;
}) {
  const tenant = useCtlQuery<CtlTenant>(ctlKeys.tenant(name), `/v1/tenants/${name}`);

  if (section === "config") return <Config name={name} />;
  if (section === "logs") return <Logs name={name} role={role} />;
  if (section === "doctor") return <Doctor name={name} />;

  if (tenant.error) return <ErrorBanner error={tenant.error} onRetry={() => void tenant.refetch()} />;
  if (!tenant.data) return <p className="text-[12.5px] text-dim">Loading {name}…</p>;

  return section === "drift" ? <Drift tenant={tenant.data} /> : <Overview tenant={tenant.data} />;
}

export function TenantView({
  name,
  role,
  onBack,
}: {
  name: string;
  role: CtlRole;
  onBack: () => void;
}) {
  const [section, setSection] = useState<SectionId>("overview");
  const tenant = useCtlQuery<CtlTenant>(ctlKeys.tenant(name), `/v1/tenants/${name}`);

  return (
    <div>
      <div className="bg-ink-900 px-5 py-4 md:px-8">
        <button
          type="button"
          onClick={onBack}
          className="mb-1 font-mono text-[11px] text-ink-dim hover:text-white"
        >
          ← fleet
        </button>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="font-display text-[20px] font-extrabold text-white">{name}</h1>
          {tenant.data && (
            <span className="font-mono text-[11px] text-ink-dim">
              manifest {tenant.data.summary.manifest_name} · api {tenant.data.summary.ports.api} ·
              observed {since(tenant.data.status.observed_at)}
            </span>
          )}
        </div>
      </div>

      <div className="md:flex">
        <div className="sticky top-0 z-10 border-b border-line bg-paper md:static md:z-auto md:w-[168px] md:shrink-0 md:border-b-0 md:border-r">
          <nav
            aria-label="Sections"
            className="flex items-center gap-1 overflow-x-auto px-4 py-2 md:block md:overflow-visible md:py-6"
          >
            <div className="hidden font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted md:mb-3.5 md:block">
              Sections
            </div>
            <div className="flex items-center gap-1 md:flex-col md:items-stretch md:gap-[3px]">
              {SECTIONS.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  aria-current={section === s.id ? "true" : undefined}
                  onClick={() => setSection(s.id)}
                  className={`rounded-row px-2.5 py-1.5 text-left text-[12.5px] ${
                    section === s.id
                      ? "bg-accent-soft font-medium text-ink-900"
                      : "text-dim hover:text-ink-900"
                  }`}
                >
                  {s.label}
                  {s.id === "logs" && role !== "operator" && (
                    <span className="ml-1.5 font-mono text-[10px] text-faint">operator</span>
                  )}
                </button>
              ))}
            </div>
          </nav>
        </div>

        <div className="min-w-0 flex-1 px-5 py-6 md:px-8">
          <TenantSection section={section} name={name} role={role} />
        </div>
      </div>
    </div>
  );
}
