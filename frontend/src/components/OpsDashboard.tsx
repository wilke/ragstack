import { useQuery } from "@tanstack/react-query";
import { ApiError, getDeepHealth, getStoreStats, type StoreStat } from "../api/client";

// Ops module (slice of #95): read-only operational view fed by the tenant-scoped
// read endpoints (#85). Store stats work for any caller; deep health is admin-only
// (start the API with DEFAULT_ROLE=admin, or pass an admin key) — a 403 degrades to
// a note rather than an error. Counts auto-refresh so an in-progress ingest is visible.

const fmt = (n: number | null | undefined): string => (n == null ? "—" : n.toLocaleString());

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="text-xs uppercase tracking-wide text-gray-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-gray-900">{value}</div>
      {sub ? <div className="mt-1 truncate text-xs text-gray-500">{sub}</div> : null}
    </div>
  );
}

function StorePill({ label, s }: { label: string; s: StoreStat }) {
  const ok = s.available;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
        ok ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
      }`}
      title={`${label}: ${s.backend}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-green-500" : "bg-red-500"}`} />
      {label} · {s.backend}
    </span>
  );
}

export function OpsDashboard({ apiKey }: { apiKey?: string }) {
  const stats = useQuery({
    queryKey: ["stats-stores", apiKey],
    queryFn: () => getStoreStats(apiKey || undefined),
    refetchInterval: 5000,
  });

  const health = useQuery({
    queryKey: ["health-deep", apiKey],
    queryFn: () => getDeepHealth(apiKey || undefined),
    refetchInterval: 15000,
    retry: false,
  });

  const healthErr = health.error as ApiError | undefined;

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">Stores</h2>
        {stats.isFetching ? <span className="text-xs text-gray-400">refreshing…</span> : null}
      </div>

      {stats.isError ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          Failed to load store stats: {(stats.error as Error).message}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <KpiCard label="Vectors" value={fmt(stats.data?.vector.count)} sub={stats.data?.vector.backend} />
            <KpiCard label="Text · BM25" value={fmt(stats.data?.text.count)} sub={stats.data?.text.backend} />
            <KpiCard label="Graph" value={fmt(stats.data?.graph.count)} sub={stats.data?.graph.backend} />
            <KpiCard
              label="Tenants"
              value={fmt(stats.data?.tenants.length)}
              sub={stats.data?.tenants.join(", ")}
            />
          </div>
          {stats.data ? (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <StorePill label="vector" s={stats.data.vector} />
              <StorePill label="text" s={stats.data.text} />
              <StorePill label="graph" s={stats.data.graph} />
            </div>
          ) : null}
        </>
      )}

      <h2 className="mb-2 mt-8 text-sm font-semibold text-gray-700">Deep health</h2>
      {health.isError ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {healthErr?.status === 403
            ? "Deep health is admin-only. Start the API with DEFAULT_ROLE=admin (keyless callers default to 'researcher'), or enter an admin key above."
            : `Unavailable: ${(health.error as Error).message}`}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-400">
              <tr>
                <th className="px-3 py-2 font-medium">Check</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Latency</th>
                <th className="px-3 py-2 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {(health.data?.checks ?? []).map((c) => (
                <tr key={c.name} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-medium text-gray-800">{c.name}</td>
                  <td className="px-3 py-2">
                    {c.ok ? (
                      <span className="text-green-600">● ok</span>
                    ) : (
                      <span className="text-red-600">● down</span>
                    )}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-500">
                    {c.latency_ms != null ? `${c.latency_ms} ms` : "—"}
                  </td>
                  <td className="px-3 py-2 text-gray-500">{c.detail ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
