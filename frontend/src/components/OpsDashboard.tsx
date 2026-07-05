import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ApiError,
  getConfig,
  getDeepHealth,
  getJobs,
  getModelsStatus,
  getStoreStats,
  runModelBenchmark,
  type AppConfig,
  type BenchmarkResult,
  type JobSummary,
  type ModelStatus,
  type StoreStat,
} from "../api/client";

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

function StatusDot({ ok, note }: { ok: boolean; note?: string | null }) {
  if (note) return <span className="text-xs text-gray-400">{note}</span>;
  return ok ? (
    <span className="text-green-600">● up</span>
  ) : (
    <span className="text-red-600">● down</span>
  );
}

function endpointSummary(m: ModelStatus): string {
  if (!m.endpoints.length) return "—";
  const up = m.endpoints.filter((e) => e.reachable).length;
  const lats = m.endpoints
    .map((e) => e.latency_ms)
    .filter((x): x is number => x != null);
  const fastest = lats.length ? `${Math.min(...lats).toFixed(0)} ms` : "";
  const count = m.endpoints.length > 1 ? `${up}/${m.endpoints.length} up` : "";
  // Live in-flight requests across the fan-out pool (embedding only).
  const flight = m.endpoints.reduce((n, e) => n + (e.in_flight ?? 0), 0);
  const load = m.endpoints.some((e) => e.in_flight != null) ? `${flight} in-flight` : "";
  return [count, fastest, load].filter(Boolean).join(" · ") || "reachable";
}

// Pull the throughput cell for a role out of a completed benchmark run.
function throughputCell(role: string, bench: BenchmarkResult | undefined): string {
  if (!bench) return "—";
  const r = role === "embedding" ? bench.embedding : role === "llm" ? bench.llm : undefined;
  if (!r) return "—";
  if (!r.ok) return `failed`;
  const parts: string[] = [];
  if (r.items_per_sec != null) parts.push(`${r.items_per_sec}/s`);
  if (r.tokens_per_sec != null) parts.push(`${r.tokens_per_sec} tok/s`);
  return parts.join(" · ") || "—";
}

function ModelsPanel({ apiKey }: { apiKey?: string }) {
  const models = useQuery({
    queryKey: ["models-status", apiKey],
    queryFn: () => getModelsStatus(apiKey || undefined),
    refetchInterval: 8000,
    retry: false,
  });

  const bench = useMutation({
    mutationFn: () => runModelBenchmark(apiKey || undefined),
  });

  const err = models.error as ApiError | undefined;

  return (
    <>
      <div className="mb-2 mt-8 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">Models</h2>
        {!models.isError && (
          <button
            type="button"
            onClick={() => bench.mutate()}
            disabled={bench.isPending}
            className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {bench.isPending ? "measuring…" : "Measure throughput"}
          </button>
        )}
      </div>

      {models.isError ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {err?.status === 403
            ? "Model status is admin-only. Start the API with DEFAULT_ROLE=admin, or enter an admin key above."
            : `Unavailable: ${(models.error as Error).message}`}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-400">
              <tr>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Endpoints</th>
                <th className="px-3 py-2 font-medium">Throughput</th>
              </tr>
            </thead>
            <tbody>
              {(models.data?.models ?? []).map((m) => (
                <tr key={m.role} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-medium capitalize text-gray-800">{m.role}</td>
                  <td className="max-w-xs truncate px-3 py-2 font-mono text-xs text-gray-600" title={m.model}>
                    {m.model}
                    {m.dim ? <span className="text-gray-400"> · {m.dim}d</span> : null}
                  </td>
                  <td className="px-3 py-2">
                    <StatusDot ok={m.reachable} note={m.note} />
                  </td>
                  <td
                    className="px-3 py-2 tabular-nums text-gray-500"
                    title={m.endpoints.map((e) => `${e.url} — ${e.reachable ? "up" : "down"}${e.latency_ms != null ? ` (${e.latency_ms} ms)` : ""}`).join("\n")}
                  >
                    {endpointSummary(m)}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-700">
                    {throughputCell(m.role, bench.data)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {bench.isError ? (
            <p className="px-3 py-2 text-xs text-red-600">
              Benchmark failed: {(bench.error as Error).message}
            </p>
          ) : bench.data ? (
            <p className="px-3 py-2 text-xs text-gray-400">
              Throughput is a one-shot estimate over the live fleet (single batched
              call), not a saturation benchmark.
            </p>
          ) : null}
        </div>
      )}
    </>
  );
}

// --- Config viewer (#95) --------------------------------------------------

function Row({ k, v }: { k: string; v: unknown }) {
  const val = Array.isArray(v) ? v.join(", ") : v == null || v === "" ? "—" : String(v);
  return (
    <div className="flex justify-between gap-3 py-1">
      <dt className="shrink-0 text-gray-500">{k}</dt>
      <dd className="truncate text-right font-mono text-xs text-gray-800" title={val}>
        {val}
      </dd>
    </div>
  );
}

function ConfigGroup({ title, rows }: { title: string; rows: [string, unknown][] }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400">{title}</div>
      <dl className="text-sm">
        {rows.map(([k, v]) => (
          <Row key={k} k={k} v={v} />
        ))}
      </dl>
    </div>
  );
}

function ConfigPanel({ apiKey }: { apiKey?: string }) {
  const cfg = useQuery({
    queryKey: ["config", apiKey],
    queryFn: () => getConfig(apiKey || undefined),
    refetchInterval: 30000,
    retry: false,
  });
  const err = cfg.error as ApiError | undefined;
  const c: AppConfig = cfg.data ?? {};

  return (
    <>
      <h2 className="mb-2 mt-8 text-sm font-semibold text-gray-700">Config</h2>
      {cfg.isError ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {err?.status === 403
            ? "Config is admin-only. Start the API with DEFAULT_ROLE=admin, or enter an admin key above."
            : `Unavailable: ${(cfg.error as Error).message}`}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <ConfigGroup
            title="Collection"
            rows={[
              ["active", c.qdrant_collection_explicit || c.qdrant_collection],
              ["es index", c.elasticsearch_index],
            ]}
          />
          <ConfigGroup
            title="Backends"
            rows={[
              ["vector", c.vector_backend],
              ["text", c.text_backend],
              ["graph", c.graph_backend],
              ["jobs", c.job_store_backend],
            ]}
          />
          <ConfigGroup
            title="Embedding"
            rows={[
              ["api", c.embedding_api],
              ["model", c.embedding_model],
              ["dim", c.embedding_model_dim],
              ["endpoints", c.embedding_endpoints?.length],
            ]}
          />
          <ConfigGroup
            title="Retrieval"
            rows={[
              ["top_k", c.top_k],
              ["rerank", c.rerank_enabled ? `on (${c.rerank_candidates})` : "off"],
              ["reranker", c.reranker_model],
              ["kg extract", c.kg_extraction_enabled ? "on" : "off"],
            ]}
          />
          <ConfigGroup
            title="Chunking"
            rows={[
              ["method", c.chunk_method],
              ["size", c.chunk_size],
              ["overlap", c.chunk_overlap],
            ]}
          />
          <ConfigGroup
            title="Ingest / limits"
            rows={[
              ["ingest conc.", c.ingest_concurrency],
              ["tenant conc.", c.tenant_max_concurrency || "unbounded"],
              ["log level", c.log_level],
            ]}
          />
        </div>
      )}
    </>
  );
}

// --- Ingest jobs (#95) ----------------------------------------------------

function jobStatusClass(status: string): string {
  if (status === "completed") return "text-green-600";
  if (status === "failed") return "text-red-600";
  if (status === "running" || status === "accepted") return "text-blue-600";
  return "text-gray-500";
}

function jobProgress(j: JobSummary): string {
  const { pending, completed, failed } = j.items;
  const tracked = pending + completed + failed;
  if (tracked > 0) {
    const total = tracked;
    const parts = [`${completed}/${total} done`];
    if (failed) parts.push(`${failed} failed`);
    if (pending) parts.push(`${pending} pending`);
    return parts.join(" · ");
  }
  // Single-doc runs don't register per-item rows — fall back to chunk count.
  return j.chunks ? `${j.chunks} chunks` : "—";
}

function JobsPanel({ apiKey }: { apiKey?: string }) {
  const jobs = useQuery({
    queryKey: ["jobs", apiKey],
    queryFn: () => getJobs(25, apiKey || undefined),
    refetchInterval: 5000,
    retry: false,
  });
  const err = jobs.error as ApiError | undefined;
  const rows = jobs.data?.jobs ?? [];

  return (
    <>
      <div className="mb-2 mt-8 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">Ingest jobs</h2>
        {jobs.isFetching && !jobs.isError ? (
          <span className="text-xs text-gray-400">refreshing…</span>
        ) : null}
      </div>
      {jobs.isError ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {err?.status === 403
            ? "Ingest jobs are admin-only. Start the API with DEFAULT_ROLE=admin, or enter an admin key above."
            : `Unavailable: ${(jobs.error as Error).message}`}
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 p-4 text-center text-sm text-gray-400">
          No ingest jobs yet. Run one via <code className="font-mono">POST /v1/ingest</code>.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-400">
              <tr>
                <th className="px-3 py-2 font-medium">Job</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Progress</th>
                <th className="px-3 py-2 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((j) => (
                <tr key={j.job_id} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-mono text-xs text-gray-600" title={j.job_id}>
                    {j.job_id.slice(0, 8)}
                  </td>
                  <td className={`px-3 py-2 font-medium ${jobStatusClass(j.status)}`}>
                    {j.status}
                    {j.error ? <span className="ml-1 text-xs text-red-400">({j.error})</span> : null}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-600">{jobProgress(j)}</td>
                  <td className="max-w-xs truncate px-3 py-2 text-gray-500" title={j.source}>
                    {j.source || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
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

      <ConfigPanel apiKey={apiKey} />

      <ModelsPanel apiKey={apiKey} />

      <JobsPanel apiKey={apiKey} />

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
