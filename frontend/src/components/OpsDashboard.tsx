import { useMutation, useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import {
  ApiError,
  getCollections,
  getConfig,
  getDeepHealth,
  getJobs,
  getModelsStatus,
  getStoreStats,
  getTenants,
  runModelBenchmark,
  type AppConfig,
  type BenchmarkResult,
  type JobSummary,
  type ModelStatus,
  type Provenance,
  type StoreStat,
} from "../api/client";

// Ops module (slice of #95): read-only operational view fed by the tenant-scoped
// read endpoints (#85). Store stats work for any caller; deep health is admin-only
// (start the API with DEFAULT_ROLE=admin, or pass an admin key) — a 403 degrades to
// a note rather than an error. Counts auto-refresh so an in-progress ingest is visible.

const fmt = (n: number | null | undefined): string => (n == null ? "—" : n.toLocaleString());

// --- Section registry / table of contents ---------------------------------

// One list drives both the TOC and every <h2>: SectionHeading renders its text
// from here, so a section can't exist without a nav entry (or vice versa).
// Order matches the render order below.
const SECTIONS = [
  { id: "stores", label: "Stores" },
  { id: "config", label: "Config" },
  { id: "collections", label: "Collections" },
  { id: "tenants", label: "Tenants" },
  { id: "models", label: "Models" },
  { id: "jobs", label: "Ingest jobs" },
  { id: "health", label: "Deep health" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

const SECTION_LABEL = Object.fromEntries(SECTIONS.map((s) => [s.id, s.label])) as Record<
  SectionId,
  string
>;

// Headings report their own availability upward so the TOC can dim the sections
// that 403'd (admin-only) or failed, instead of advertising them as live.
const ReportSection = createContext<(id: SectionId, available: boolean) => void>(() => {});

function SectionHeading({
  id,
  unavailable,
  children,
}: {
  id: SectionId;
  unavailable?: boolean;
  children?: ReactNode;
}) {
  const report = useContext(ReportSection);
  useEffect(() => report(id, !unavailable), [report, id, unavailable]);
  return (
    <div className="mb-2 mt-8 flex items-center justify-between">
      {/* scroll-mt clears the sticky TOC bar so the heading isn't hidden under it */}
      <h2 id={id} className="scroll-mt-16 text-sm font-semibold text-gray-700">
        {SECTION_LABEL[id]}
      </h2>
      {children}
    </div>
  );
}

function TableOfContents({ available }: { available: Partial<Record<SectionId, boolean>> }) {
  return (
    <nav
      aria-label="Sections"
      className="sticky top-0 z-10 -mx-4 flex flex-wrap items-center gap-1.5 border-b border-gray-200 bg-white/90 px-4 py-2 backdrop-blur"
    >
      {SECTIONS.map((s) => {
        const off = available[s.id] === false;
        return (
          <a
            key={s.id}
            href={`#${s.id}`}
            title={off ? "unavailable — admin-only or failed to load" : undefined}
            className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
              off
                ? "border-dashed border-gray-200 text-gray-400 hover:text-gray-600"
                : "border-gray-200 text-gray-600 hover:bg-gray-50 hover:text-gray-900"
            }`}
          >
            {s.label}
            {off ? <span className="ml-1 text-gray-300">n/a</span> : null}
          </a>
        );
      })}
    </nav>
  );
}

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
      <SectionHeading id="models" unavailable={models.isError}>
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
      </SectionHeading>

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
      <SectionHeading id="config" unavailable={cfg.isError} />
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

// --- Collections registry -------------------------------------------------

// Vector count vs text (BM25) count for a collection. They should match — both
// legs index the same chunks — so a drift flags a half-broken ingest (one store
// missing rows). "~" when a count is approximate (an estimate on a huge
// collection can differ slightly from the exact other leg); tolerate a small
// relative delta before crying drift.
function ParityBadge({ vec, text }: { vec?: number | null; text?: number | null }) {
  if (vec == null || text == null) {
    return <span className="text-xs text-gray-300" title="a count is unavailable">—</span>;
  }
  const delta = Math.abs(vec - text);
  const rel = delta / Math.max(vec, text, 1);
  if (delta === 0) {
    return <span className="rounded bg-green-50 px-1.5 py-0.5 text-xs text-green-700">✓ match</span>;
  }
  if (rel <= 0.02) {
    return (
      <span
        className="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700"
        title={`vector and text counts differ by ${delta.toLocaleString()} (~${(rel * 100).toFixed(1)}%) — likely an approximate count on a large collection`}
      >
        ≈ close
      </span>
    );
  }
  return (
    <span
      className="rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700"
      title={`vector and text counts differ by ${delta.toLocaleString()} (${(rel * 100).toFixed(1)}%) — one store is missing rows (incomplete ingest?)`}
    >
      ⚠ drift {delta.toLocaleString()}
    </span>
  );
}

// Hover detail for a collection's manifest. "verified" = written by a real ingest
// run through this API; "declared" = materialized from the registry spec, so it
// records what we were *told* the corpus is, not what was observed building it.
function provenanceDetail(p: Provenance): string {
  const parts = [
    p.source === "ingest"
      ? "verified — recorded by an ingest run"
      : "declared — materialized from the registry spec, not observed",
    p.collection ? `store: ${p.collection}` : "",
    p.model ? `built with: ${p.model}${p.dim ? ` (${p.dim}d)` : ""}` : "",
    p.embedding_api ? `embedding api: ${p.embedding_api}` : "",
    p.spec_hash ? `spec: ${p.spec_hash}` : "",
    p.chunk_params && Object.keys(p.chunk_params).length
      ? `chunk params: ${JSON.stringify(p.chunk_params)}`
      : "",
    p.chunk_count != null ? `chunks at ingest: ${p.chunk_count.toLocaleString()}` : "",
    p.corpus ? `corpus: ${p.corpus}` : "",
    p.ingested_at ? `ingested: ${p.ingested_at}` : "",
    p.ragstack_version ? `ragstack ${p.ragstack_version}` : "",
  ];
  return parts.filter(Boolean).join("\n");
}

function CollectionsPanel({ apiKey }: { apiKey?: string }) {
  const cols = useQuery({
    queryKey: ["collections-ops", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    refetchInterval: 15000,
    retry: false,
  });
  const rows = cols.data?.collections ?? [];

  return (
    <>
      <SectionHeading id="collections" unavailable={cols.isError} />
      {cols.isError ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          Unavailable: {(cols.error as Error).message}
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 p-4 text-center text-sm text-gray-400">
          No collections registered.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-400">
              <tr>
                <th className="px-3 py-2 font-medium">Collection</th>
                <th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 font-medium">Chunking</th>
                <th className="px-3 py-2 font-medium">Provenance</th>
                <th
                  className="px-3 py-2 text-right font-medium"
                  title="Chunks in the vector store (Qdrant), tenant-filtered — the dense/embedding leg of hybrid retrieval."
                >
                  Vectors
                </th>
                <th
                  className="px-3 py-2 text-right font-medium"
                  title="Chunks in the text index (Elasticsearch BM25), tenant-filtered — the lexical leg of hybrid retrieval."
                >
                  Text
                </th>
                <th className="px-3 py-2 text-center font-medium">Parity</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const p = c.provenance;
                // Prefer verified manifest values over the operator-asserted label.
                const method = p?.chunk_method ?? c.chunk_method;
                const size = p?.chunk_size ?? c.chunk_size;
                const chunking = method
                  ? `${method}${size ? "/" + size : ""}${p?.chunk_overlap != null ? " · ov " + p.chunk_overlap : ""}`
                  : "—";
                return (
                  <tr key={c.id} className="border-t border-gray-100">
                    <td className="px-3 py-2 font-medium text-gray-800">
                      {c.label}
                      {c.default ? (
                        <span className="ml-1 rounded bg-gray-100 px-1 text-xs text-gray-500">default</span>
                      ) : null}
                    </td>
                    <td className="max-w-xs truncate px-3 py-2 font-mono text-xs text-gray-600" title={c.model}>
                      {c.model}
                      <span className="text-gray-400"> · {c.dim}d</span>
                    </td>
                    <td className="px-3 py-2 text-gray-600">{chunking}</td>
                    <td className="px-3 py-2 text-xs">
                      {p ? (
                        <span title={provenanceDetail(p)}>
                          <span
                            className={`rounded px-1 ${p.source === "ingest" ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"}`}
                          >
                            {p.source === "ingest" ? "verified" : "declared"}
                          </span>
                          {p.ingested_at ? (
                            <span className="ml-1 text-gray-400">{p.ingested_at.slice(0, 10)}</span>
                          ) : null}
                        </span>
                      ) : (
                        <span
                          className="text-gray-300"
                          title="No build manifest for this collection — set COLLECTION_MANIFEST_DIR and restart to materialize one from the registry spec (an ingest through this API then upgrades it to a verified record)."
                        >
                          none
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-600">
                      {c.count != null ? c.count.toLocaleString() : "—"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-600">
                      {c.text_count != null ? c.text_count.toLocaleString() : "—"}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <ParityBadge vec={c.count} text={c.text_count} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 ? (
        <p className="mt-2 text-xs text-gray-400">
          <span className="font-medium text-gray-500">Vectors</span> counts the dense
          embeddings in Qdrant; <span className="font-medium text-gray-500">Text</span>{" "}
          counts the BM25 documents in Elasticsearch. Hybrid retrieval queries both legs
          over the <em>same</em> chunks, so equal numbers are the healthy state — a drift
          means one store is missing rows (a partial or failed ingest), not extra data.
          Both are filtered to your readable tenants; very large counts may be
          approximate.
        </p>
      ) : null}
    </>
  );
}

// --- Tenancy --------------------------------------------------------------

// /v1/stats/stores reports one number per store for the UNION of readable tenants
// (own + public). This panel splits that union apart per collection, which is how
// you spot a corpus sitting entirely in `public` when you expected it under an org's
// own tenant. Fetched on demand (no refetchInterval): it costs a count per
// tenant x collection x store.
function TenantsPanel({ apiKey }: { apiKey?: string }) {
  const t = useQuery({
    queryKey: ["tenants", apiKey],
    queryFn: () => getTenants(apiKey || undefined),
    retry: false,
  });
  const data = t.data;
  const cols = data?.tenants[0]?.collections ?? [];

  return (
    <>
      <SectionHeading id="tenants" unavailable={t.isError} />
      {t.isError ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          Unavailable: {(t.error as Error).message}
        </div>
      ) : !data ? (
        <div className="rounded-lg border border-dashed border-gray-200 p-4 text-center text-sm text-gray-400">
          Loading tenancy…
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <KpiCard label="Tenant" value={data.tenant} sub={`role ${data.role}`} />
            <KpiCard
              label="Readable"
              value={String(data.readable.length)}
              sub={data.readable.join(" + ")}
            />
            <KpiCard
              label="Collections"
              value={data.restricted_to ? String(data.restricted_to.length) : "all"}
              sub={data.restricted_to ? data.restricted_to.join(", ") : "unrestricted"}
            />
            <KpiCard
              label="Auth"
              value={data.auth_enabled ? "API keys" : "keyless"}
              sub={data.auth_enabled ? "per-key tenant" : "everyone is `default`"}
            />
          </div>

          {!data.auth_enabled ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              Keyless mode — every caller is the <code>default</code> tenant with the
              server's default role. Fine for dev; production startup forbids it.
            </div>
          ) : null}

          {cols.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-200 p-4 text-center text-sm text-gray-400">
              No collections reachable by this tenant.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-gray-200">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-400">
                  <tr>
                    <th className="px-3 py-2 font-medium">Tenant</th>
                    {cols.map((c) => (
                      <th key={c.collection} className="px-3 py-2 text-right font-medium">
                        {c.label}
                      </th>
                    ))}
                    <th className="px-3 py-2 text-right font-medium">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.tenants.map((row) => {
                    const total = row.collections.reduce((n, c) => n + (c.vector_count ?? 0), 0);
                    return (
                      <tr key={row.tenant} className="border-t border-gray-100">
                        <td className="px-3 py-2 font-medium text-gray-800">
                          {row.tenant}
                          <span className="ml-1 rounded bg-gray-100 px-1 text-xs font-normal text-gray-500">
                            {row.own ? "you" : "shared"}
                          </span>
                        </td>
                        {row.collections.map((c) => (
                          <td
                            key={c.collection}
                            className="px-3 py-2 text-right tabular-nums text-gray-600"
                            title={`text index: ${fmt(c.text_count)}`}
                          >
                            {fmt(c.vector_count)}
                          </td>
                        ))}
                        <td className="px-3 py-2 text-right font-medium tabular-nums text-gray-800">
                          {total.toLocaleString()}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-gray-400">
            Vector-store chunks owned by each tenant (hover a cell for its text-index
            count). Rows cover only the tenants you may read — another tenant's corpus
            size is never shown. Reads are filtered to{" "}
            <code>{data.readable.join(" + ")}</code>, so a collection that looks empty
            for your own tenant may still be fully served from <code>public</code>.
          </p>

          {data.policy ? (
            <div className="rounded-lg border border-gray-200 p-3">
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-gray-400">
                Access policy (admin)
              </div>
              {Object.keys(data.policy).length === 0 ? (
                <p className="text-xs text-gray-400">
                  <code>TENANT_COLLECTIONS</code> unset — every tenant may reach every
                  collection.
                </p>
              ) : (
                <ul className="space-y-1 text-xs text-gray-600">
                  {Object.entries(data.policy).map(([tenant, ids]) => (
                    <li key={tenant}>
                      <span className="font-medium text-gray-800">{tenant}</span>
                      <span className="text-gray-400"> → </span>
                      <span className="font-mono">{ids.join(", ") || "(none)"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
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
      <SectionHeading id="jobs" unavailable={jobs.isError}>
        {jobs.isFetching && !jobs.isError ? (
          <span className="text-xs text-gray-400">refreshing…</span>
        ) : null}
      </SectionHeading>
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
    retry: false,  // fail fast on 401/403 like the sibling queries — no retry storm
  });

  const health = useQuery({
    queryKey: ["health-deep", apiKey],
    queryFn: () => getDeepHealth(apiKey || undefined),
    refetchInterval: 15000,
    retry: false,
  });

  const healthErr = health.error as ApiError | undefined;

  const [available, setAvailable] = useState<Partial<Record<SectionId, boolean>>>({});
  const report = useCallback((id: SectionId, ok: boolean) => {
    setAvailable((prev) => (prev[id] === ok ? prev : { ...prev, [id]: ok }));
  }, []);

  return (
    <section>
      <ReportSection.Provider value={report}>
        <TableOfContents available={available} />

        <SectionHeading id="stores" unavailable={stats.isError}>
          {stats.isFetching ? <span className="text-xs text-gray-400">refreshing…</span> : null}
        </SectionHeading>

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

        <CollectionsPanel apiKey={apiKey} />

        <TenantsPanel apiKey={apiKey} />

        <ModelsPanel apiKey={apiKey} />

        <JobsPanel apiKey={apiKey} />

        <SectionHeading id="health" unavailable={health.isError} />
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
      </ReportSection.Provider>
    </section>
  );
}
