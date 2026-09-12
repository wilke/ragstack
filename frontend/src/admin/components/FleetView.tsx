// The fleet dashboard: the band of host facts, the doctor verdict, and one row
// per registry tenant.
//
// Everything on this screen comes from three reads — `/v1/fleet`, `/v1/doctor`
// and `/v1/version` — and `/v1/fleet` is designed as the dashboard's ONE poll:
// its whole body is the viewer-safe summary shape, so a viewer and an operator
// see the same table and there is no second request to assemble it.

import { useState } from "react";
import { ctlKeys, FLEET_POLL_MS, pollWhenVisible, useCtlQuery } from "../api/queries";
import type { CtlDoctor, CtlFleet, CtlVersion, FleetRow } from "../api/types";
import { bytes, since } from "../lib/format";
import { ErrorBanner } from "./ErrorBanner";
import { HealthDot, StateChip } from "./StateChip";

function BandFact({ label, value, tone }: { label: string; value: string; tone?: "bad" | "warn" }) {
  return (
    <div className="rounded-card bg-white/[.06] px-[16px] py-3">
      <div className="mb-1.5 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-ink-dim">
        {label}
      </div>
      <div
        className={`font-display text-[19px] font-extrabold leading-none ${
          tone === "bad" ? "text-ink-bad" : tone === "warn" ? "text-accent" : "text-white"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

/** `up` counts tenants whose API health probe is `ok` — the only honest reading. */
export function fleetUpDown(rows: readonly FleetRow[]): { up: number; down: number } {
  const up = rows.filter((r) => r.health.api === "ok").length;
  return { up, down: rows.length - up };
}

function DoctorChip({
  doctor,
  expanded,
  onToggle,
}: {
  doctor: CtlDoctor | undefined;
  expanded: boolean;
  onToggle: () => void;
}) {
  if (!doctor) {
    return <span className="font-mono text-[11px] text-dim">doctor …</span>;
  }
  const n = doctor.findings.length;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className="inline-flex items-center gap-2 rounded-chip px-1 py-0.5 hover:bg-lineSoft"
      title={`doctor ${doctor.status} — ${n} finding${n === 1 ? "" : "s"}`}
    >
      <StateChip kind="status" value={doctor.status} />
      <span className="font-mono text-[11px] text-dim">
        {n} finding{n === 1 ? "" : "s"}
      </span>
    </button>
  );
}

export function DoctorFindings({ doctor }: { doctor: CtlDoctor }) {
  if (doctor.findings.length === 0) {
    return <p className="mt-3 text-[12.5px] text-dim">No findings — doctor is green.</p>;
  }
  return (
    <table className="mt-3 w-full border-collapse text-left">
      <thead>
        <tr className="border-b border-line text-[10px] uppercase tracking-[.12em] text-muted">
          <th className="py-1.5 pr-3 font-mono font-medium">code</th>
          <th className="py-1.5 pr-3 font-mono font-medium">tenant</th>
          <th className="py-1.5 pr-3 font-mono font-medium">level</th>
          <th className="py-1.5 font-mono font-medium">detail</th>
        </tr>
      </thead>
      <tbody>
        {doctor.findings.map((f, i) => (
          <tr key={`${f.code}-${f.tenant ?? "host"}-${i}`} className="border-b border-lineSoft align-top">
            <td className="py-1.5 pr-3 font-mono text-[11.5px] text-strong">{f.code}</td>
            <td className="py-1.5 pr-3 font-mono text-[11.5px] text-dim">{f.tenant ?? "host"}</td>
            <td className="py-1.5 pr-3">
              <StateChip kind="level" value={f.level} />
            </td>
            <td className="py-1.5 text-[12px] leading-snug text-body">
              {f.detail}
              {f.repair && (
                <span className="ml-2 font-mono text-[11px] text-link">repair: {f.repair}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const TH = "py-2 pr-3 font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted";
const TD = "py-2 pr-3 align-middle";

export function FleetView({ onSelectTenant }: { onSelectTenant: (name: string) => void }) {
  const [showFindings, setShowFindings] = useState(false);

  const fleet = useCtlQuery<CtlFleet>(ctlKeys.fleet(), "/v1/fleet", {
    refetchInterval: pollWhenVisible(FLEET_POLL_MS),
  });
  const doctor = useCtlQuery<CtlDoctor>(ctlKeys.doctor(), "/v1/doctor", {
    refetchInterval: pollWhenVisible(FLEET_POLL_MS),
  });
  const version = useCtlQuery<CtlVersion>(ctlKeys.version(), "/v1/version", {
    staleTime: Infinity,
  });

  const rows = fleet.data?.tenants ?? [];
  const host = fleet.data?.host;
  const { up, down } = fleetUpDown(rows);
  const now = fleet.data ? Date.parse(fleet.data.generated_at) : Date.now();

  return (
    <div>
      {/* Host band — navy, the same full-width device the tenant Ops screen uses. */}
      <div className="bg-ink-900 px-5 py-5 md:px-8">
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="font-display text-[17px] font-bold text-white">Fleet</h1>
          <span className="font-mono text-[11px] text-ink-dim">
            registry generation {fleet.data?.registry_generation ?? "—"} · polled every{" "}
            {FLEET_POLL_MS / 1000} s
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2.5 md:grid-cols-6">
          <BandFact label="tenants up" value={fleet.data ? `${up} / ${rows.length}` : "—"} tone={down > 0 ? "warn" : undefined} />
          <BandFact label="host disk free" value={bytes(host?.disk_free_bytes)} />
          <BandFact
            label="linger"
            value={host ? (host.linger ? "yes" : "no") : "—"}
            tone={host && !host.linger ? "bad" : undefined}
          />
          <BandFact
            label="ctl unit"
            value={host ? (host.ctl_unit_active ? "active" : "inactive") : "—"}
            tone={host && !host.ctl_unit_active ? "warn" : undefined}
          />
          <BandFact
            label="vm.max_map_count"
            value={host ? host.vm_max_map_count.toLocaleString() : "—"}
            // 262144 is the floor Elasticsearch needs; below it an ES tenant
            // will not start and the reason is invisible from the tenant's own
            // logs (MEMORY.md, the VMA crash).
            tone={host && host.vm_max_map_count < 262144 ? "bad" : undefined}
          />
          <BandFact label="ctl version" value={version.data?.version ?? "—"} />
        </div>
      </div>

      <div className="px-5 py-5 md:px-8">
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-[11px] font-medium uppercase tracking-[.14em] text-strong">
            Tenants
          </h2>
          <DoctorChip
            doctor={doctor.data}
            expanded={showFindings}
            onToggle={() => setShowFindings((v) => !v)}
          />
        </div>

        {showFindings && doctor.data && <DoctorFindings doctor={doctor.data} />}

        {fleet.error && <ErrorBanner error={fleet.error} onRetry={() => void fleet.refetch()} />}

        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                <th className={TH}>name</th>
                <th className={TH}>manifest</th>
                <th className={TH}>state</th>
                <th className={TH}>owner</th>
                <th className={TH}>supervisor</th>
                <th className={TH}>stores</th>
                <th className={TH}>code tag</th>
                <th className={TH}>drift</th>
                <th className={TH}>ports api/qdrant/es</th>
                <th className={TH}>health</th>
                <th className={TH}>disk</th>
                <th className={TH}>last backup</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.name}
                  onClick={() => onSelectTenant(r.name)}
                  className="cursor-pointer border-b border-lineSoft hover:bg-accent-soft"
                >
                  <td className={TD}>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectTenant(r.name);
                      }}
                      className="font-mono text-[12.5px] font-medium text-link underline-offset-2 hover:underline"
                    >
                      {r.name}
                    </button>
                  </td>
                  <td className={`${TD} font-mono text-[11.5px] text-dim`}>{r.manifest_name}</td>
                  <td className={TD}>
                    <StateChip kind="state" value={r.state} />
                  </td>
                  <td className={`${TD} font-mono text-[11.5px] text-body`}>{r.owner}</td>
                  <td className={`${TD} font-mono text-[11.5px] text-body`}>{r.supervisor}</td>
                  <td className={TD}>
                    <StateChip kind="mode" value={r.stores_mode} />
                  </td>
                  <td className={`${TD} font-mono text-[11.5px] text-body`}>{r.code_tag}</td>
                  <td className={TD}>
                    {r.drift_count > 0 ? (
                      <StateChip kind="level" value="warn" title={`${r.drift_count} drift rows`} />
                    ) : null}
                    <span className="ml-1 font-mono text-[11.5px] tabular-nums text-dim">
                      {r.drift_count}
                    </span>
                  </td>
                  <td className={`${TD} font-mono text-[11.5px] tabular-nums text-body`}>
                    {r.ports.api} · {r.ports.qdrant_http} · {r.ports.es_http}
                  </td>
                  <td className={TD}>
                    <span className="inline-flex items-center gap-1.5">
                      <HealthDot label="api" state={r.health.api} />
                      <HealthDot label="qdrant" state={r.health.qdrant} />
                      <HealthDot label="es" state={r.health.es} />
                    </span>
                  </td>
                  <td className={`${TD} font-mono text-[11.5px] tabular-nums text-body`}>
                    {bytes(r.disk_bytes)}
                  </td>
                  <td className={`${TD} font-mono text-[11.5px] text-dim`}>
                    {r.last_backup ? (
                      <span title={`${r.last_backup.fenced ? "fenced" : "best effort"}, ${r.last_backup.verified ? "verified" : "unverified"}`}>
                        {since(r.last_backup.at, now)}
                      </span>
                    ) : (
                      "never"
                    )}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && !fleet.error && (
                <tr>
                  <td colSpan={12} className="py-6 text-center text-[12.5px] text-dim">
                    {fleet.isLoading ? "Loading the fleet…" : "The registry has no tenants."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
