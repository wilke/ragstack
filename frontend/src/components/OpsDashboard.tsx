import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Fragment,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  addGroupMember,
  createCollection,
  createGroup,
  deleteCollection,
  deleteGroup,
  getCollections,
  getConfig,
  getDeepHealth,
  getGroup,
  getJobs,
  getModelsRegistry,
  getModelsStatus,
  getStoreStats,
  getTenants,
  listGroups,
  purgeCollection,
  removeGroupMember,
  runModelBenchmark,
  type AppConfig,
  type BenchmarkResult,
  type CollectionInfo,
  type CollectionPurgeReport,
  type GroupMemberRecord,
  type GroupRecord,
  type JobSummary,
  type ModelStatus,
  type Provenance,
  type StoreStat,
} from "../api/client";
import {
  DEFAULT_CHUNK_FORM,
  buildChunkConfig,
  validateChunkForm,
  type ChunkForm,
} from "../lib/chunkers";
import {
  ID_BLANK_HINT,
  ID_EXPLICIT_HINT,
  collectionCreateMessage,
  collectionDeleteMessage,
  collectionPurgeMessage,
  groupCreateMessage,
  groupDeleteMessage,
  groupMemberAddMessage,
  groupMemberRemoveMessage,
  purgeConfirmed,
  purgeReportSummary,
} from "../lib/collections";
import { ChunkStrategyPicker } from "./ChunkStrategyPicker";

// Ops module (slice of #95): the operational view fed by the tenant-scoped read
// endpoints (#85). Store stats work for any caller; deep health, config, jobs and
// the model registry are admin-only (start the API with DEFAULT_ROLE=admin, or
// pass an admin key) — a 403 degrades to an amber note rather than an error.
// Counts auto-refresh so an in-progress ingest is visible.
//
// Everything here was read-only until the Collections section gained collection
// administration (create / inspect / unregister / permanently delete). Those are
// the only writes on this page, they are admin-gated server-side, and they live
// here because this is where collections are already listed and audited — see the
// note above CollectionsPanel for the full rationale. The last of them is the only
// irreversible action in the whole UI, which is why it sits behind a type-the-id
// gate rather than a click.

const fmt = (n: number | null | undefined): string => (n == null ? "—" : n.toLocaleString());

// --- Section registry / table of contents ---------------------------------

// One list drives both the TOC and every <h2>: SectionHeading renders its text
// from here, so a section can't exist without a nav entry (or vice versa).
// Order matches the render order below.
const SECTIONS = [
  { id: "stores", label: "Stores" },
  { id: "config", label: "Config" },
  { id: "collections", label: "Collections" },
  { id: "groups", label: "Groups" },
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

// --- Collections registry + administration --------------------------------

// This is the admin home for collections: list, inspect, create, delete.
//
// WHY HERE and not a fourth top-level tab: every write on this surface is
// admin-gated server-side (POST/DELETE /v1/collections and
// GET /v1/admin/models/registry all require the admin role), and Ops is already
// the admin surface — it owns the 403-degrades-to-amber pattern, the section
// registry/TOC, and the read-side listing these controls act on. Splitting
// "which collections exist, and are their two legs in parity?" from "make one /
// delete one" across two tabs would put the evidence and the action in different
// places. The demo Collection tab keeps its own one-click create (name + chunker
// against the demo's embedder) because that is a *demo flow*, not administration.
//
// NAMING (docs/ARCHITECTURE.md §3): a **collection** is the registry entry
// binding (embedding model + dim + chunker) to an **index** (one physical Qdrant
// collection + matching ES index). "Library" is not a separate concept —
// ADR-0003 makes it one-to-one with a collection — so everything on this panel
// is a collection and says so.

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

function ProvenanceBadge({ p }: { p?: Provenance | null }) {
  if (!p) {
    return (
      <span
        className="text-gray-300"
        title="No build manifest for this collection — set COLLECTION_MANIFEST_DIR and restart to materialize one from the registry spec (an ingest through this API then upgrades it to a verified record)."
      >
        none
      </span>
    );
  }
  return (
    <span title={provenanceDetail(p)}>
      <span
        className={`rounded px-1 ${p.source === "ingest" ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"}`}
      >
        {p.source === "ingest" ? "verified" : "declared"}
      </span>
      {p.ingested_at ? <span className="ml-1 text-gray-400">{p.ingested_at.slice(0, 10)}</span> : null}
    </span>
  );
}

// One label/value line in the expanded "what is this collection made of" panel.
function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 py-0.5">
      <dt className="w-40 shrink-0 text-gray-400">{k}</dt>
      <dd className={`break-all text-gray-700 ${mono ? "font-mono text-xs" : ""}`}>{v || "—"}</dd>
    </div>
  );
}

// The full build spec of one collection: what it was built with, and whether
// that is a verified record (an ingest wrote it) or a declaration (materialized
// from the registry spec). Everything here is rendered as text.
function CollectionDetail({ c }: { c: CollectionInfo }) {
  const p = c.provenance ?? null;
  const params = p?.chunk_params ?? {};
  const paramText = Object.entries(params)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(", ");
  const overlap = p?.chunk_overlap;
  return (
    <div className="grid gap-4 bg-gray-50 px-4 py-3 text-sm sm:grid-cols-2">
      <dl>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Binding (registry)
        </div>
        <Field k="collection id" v={c.id} mono />
        <Field k="label" v={c.label} />
        <Field k="embedding model" v={c.model} mono />
        <Field k="dimensions" v={String(c.dim)} />
        <Field k="chunk method" v={c.chunk_method ?? "—"} />
        <Field k="chunk size" v={c.chunk_size != null ? String(c.chunk_size) : "—"} />
        <Field k="default" v={c.default ? "yes — cannot be deleted" : "no"} />
      </dl>
      <dl>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Build record (manifest)
        </div>
        {p ? (
          <>
            <Field
              k="source"
              v={
                p.source === "ingest"
                  ? "verified — recorded by a real ingest run"
                  : "declared — materialized from config, never observed"
              }
            />
            <Field k="physical store" v={p.collection ?? "—"} mono />
            <Field
              k="built with"
              v={p.model ? `${p.model}${p.dim ? ` · ${p.dim}d` : ""}` : "—"}
              mono
            />
            <Field k="embedding api" v={p.embedding_api ?? "—"} />
            <Field
              k="chunking"
              v={`${p.chunk_method ?? "—"}${p.chunk_size != null ? ` · ${p.chunk_size}` : ""}${
                overlap != null ? ` / ${overlap} overlap` : ""
              }${paramText ? ` · ${paramText}` : ""}`}
            />
            <Field k="spec hash" v={p.spec_hash ?? "—"} mono />
            <Field
              k="chunks at ingest"
              v={p.chunk_count != null ? p.chunk_count.toLocaleString() : "—"}
            />
            <Field k="corpus" v={p.corpus ?? "—"} mono />
            <Field k="ingested at" v={p.ingested_at ?? "—"} />
            <Field k="ragstack version" v={p.ragstack_version ?? "—"} />
          </>
        ) : (
          <p className="text-xs text-gray-500">
            No manifest. Set <code className="font-mono">COLLECTION_MANIFEST_DIR</code> and restart
            to materialize a declared one from the registry spec; an ingest through this API then
            upgrades it to a verified record.
          </p>
        )}
      </dl>
    </div>
  );
}

// --- Create ---------------------------------------------------------------

// The embedding model comes from the real registry (GET /v1/admin/models/registry,
// task=embedding) — never a free-typed string, because the model + its dim are
// what the physical store is built for and a typo mints a collection nothing can
// ingest into. When no embedding model is registered we say exactly that, and
// what to do about it, rather than showing an empty dropdown.
function CreateCollectionForm({
  apiKey,
  onDone,
}: {
  apiKey?: string;
  onDone: (created: CollectionInfo) => void;
}) {
  const registry = useQuery({
    queryKey: ["models-registry", apiKey],
    queryFn: () => getModelsRegistry(apiKey || undefined),
    retry: false,
  });
  const embedders = (registry.data?.models ?? []).filter((m) => m.task === "embedding");

  const [embedding, setEmbedding] = useState("");
  const [form, setForm] = useState<ChunkForm>(DEFAULT_CHUNK_FORM);
  const [collectionId, setCollectionId] = useState("");
  const [label, setLabel] = useState("");
  const [touched, setTouched] = useState(false);

  // Derived rather than seeded via an effect: the first registered embedder is
  // the selection until the admin picks another, and the list arrives async.
  const chosen = embedding || embedders[0]?.id || "";
  const chosenEntry = embedders.find((m) => m.id === chosen) ?? null;

  const create = useMutation<CollectionInfo, Error, void>({
    mutationFn: () =>
      createCollection(
        {
          embedding: chosen,
          chunk: buildChunkConfig(form),
          id: collectionId.trim() || undefined,
          label: label.trim() || undefined,
        },
        apiKey || undefined,
      ),
    onSuccess: onDone,
  });

  const problem =
    chosen === ""
      ? "Pick a registered embedding model."
      : chosenEntry && !(chosenEntry.dim && chosenEntry.dim > 0)
        ? "That model has no dimension recorded in the registry, so a store can't be built for it — fix the registry entry first."
        : validateChunkForm(form);

  const regErr = registry.error as ApiError | undefined;

  if (registry.isError) {
    return (
      <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
        {regErr?.status === 403 || regErr?.status === 401
          ? "Creating a collection is admin-only: the embedding-model registry (GET /v1/admin/models/registry) refused this key. Start the API with DEFAULT_ROLE=admin, or enter an admin key above."
          : `Can't read the model registry: ${(registry.error as Error).message}`}
      </div>
    );
  }

  return (
    <div className="mb-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-800">New collection</h3>

      {registry.isLoading ? (
        <p className="text-sm text-gray-500">Loading registered models…</p>
      ) : embedders.length === 0 ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          No embedding model is registered on this server, so there is nothing to bind a collection
          to. Register one first with{" "}
          <code className="font-mono">POST /v1/admin/models/registry</code> — it takes{" "}
          <code className="font-mono">
            {"{ id, task: \"embedding\", provider, base_urls, model, dim }"}
          </code>
          , and <code className="font-mono">base_urls</code> must pass the server&apos;s SSRF
          allowlist. Then reopen this form.
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label
                htmlFor="ops-col-embedding"
                className="mb-1 block text-xs font-medium text-gray-500"
              >
                Embedding model
              </label>
              <select
                id="ops-col-embedding"
                value={chosen}
                onChange={(e) => setEmbedding(e.target.value)}
                className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
              >
                {embedders.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id} · {m.model || "(no model name)"} ·{" "}
                    {m.dim ? `${m.dim}d` : "no dim"} · {m.provider}
                  </option>
                ))}
              </select>
              {chosenEntry ? (
                <p className="mt-1 text-[11px] leading-snug text-gray-400">
                  {chosenEntry.base_urls.length} endpoint
                  {chosenEntry.base_urls.length === 1 ? "" : "s"} registered. The model and its
                  dimension are baked into the store — changing embedder later means building a
                  new collection, not editing this one.
                </p>
              ) : null}
            </div>

            <div>
              <label htmlFor="ops-col-label" className="mb-1 block text-xs font-medium text-gray-500">
                Label (optional)
              </label>
              <input
                id="ops-col-label"
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="shown in pickers"
                className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
              />
            </div>
          </div>

          <div className="mt-3">
            <label htmlFor="ops-col-id" className="mb-1 block text-xs font-medium text-gray-500">
              Collection id (optional)
            </label>
            <input
              id="ops-col-id"
              type="text"
              value={collectionId}
              onChange={(e) => setCollectionId(e.target.value)}
              placeholder="leave blank for a content-addressed, shared store"
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 font-mono text-sm sm:w-80"
            />
            {/* The distinction that caused a real data-sharing bug — one line each,
                and the one that applies right now is highlighted. */}
            <p
              className={`mt-1 text-[11px] leading-snug ${
                collectionId.trim() ? "font-medium text-gray-600" : "text-gray-400"
              }`}
            >
              {ID_EXPLICIT_HINT}
            </p>
            <p
              className={`text-[11px] leading-snug ${
                collectionId.trim() ? "text-gray-400" : "font-medium text-gray-600"
              }`}
            >
              {ID_BLANK_HINT}
            </p>
          </div>

          <div className="mt-3">
            <ChunkStrategyPicker idPrefix="ops-col" form={form} onChange={setForm} />
          </div>

          {touched && problem ? (
            <p role="alert" className="mt-3 rounded bg-amber-50 p-2 text-sm text-amber-800">
              {problem}
            </p>
          ) : null}

          {create.isError && create.error ? (
            <p role="alert" className="mt-3 rounded bg-red-50 p-2 text-sm text-red-700">
              {collectionCreateMessage(
                create.error instanceof ApiError ? create.error.status : null,
                create.error.message,
              )}
            </p>
          ) : null}

          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setTouched(true);
                if (!problem) create.mutate();
              }}
              disabled={create.isPending}
              className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white transition-opacity hover:bg-blue-700 disabled:opacity-50"
            >
              {create.isPending ? "Creating…" : "Create collection"}
            </button>
            <span className="text-xs text-gray-400">
              The collection is created empty — populate it with POST /v1/ingest (or the Collection
              tab) against the returned id.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

// --- Delete ---------------------------------------------------------------

// Deleting is a registry operation ONLY. The honest sentence is spelled out here
// because people have been surprised by the orphan: the Qdrant collection and ES
// index keep existing (and keep costing disk) after the binding is gone, and
// re-creating a collection with the same build spec re-attaches to them.
function DeleteConfirm({
  c,
  apiKey,
  onCancel,
  onDeleted,
}: {
  c: CollectionInfo;
  apiKey?: string;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const store = c.provenance?.collection ?? null;
  const del = useMutation<void, Error, void>({
    mutationFn: () => deleteCollection(c.id, apiKey || undefined),
    onSuccess: onDeleted,
  });
  return (
    <div className="border-l-4 border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
      <p className="font-medium">Remove the registry binding for “{c.id}”?</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
        <li>
          The collection disappears from <code className="font-mono">GET /v1/collections</code> and
          can no longer be queried or ingested into by that id.
        </li>
        <li>
          The physical store is <strong>not</strong> deleted: the Qdrant collection
          {store ? (
            <>
              {" "}
              <code className="font-mono">{store}</code>
            </>
          ) : null}{" "}
          and its Elasticsearch index survive with all{" "}
          {c.count != null ? c.count.toLocaleString() : "their"} chunks, and keep using disk.
          {" "}
          <strong>
            This only works while another collection still uses that store.
          </strong>{" "}
          Otherwise the server refuses (409), because unregistering the last one would
          leave the data with no collection claiming it — and so no permissions
          governing who can read it. Use Purge to delete the data as well.
        </li>
        <li>Nothing about the model registry or any other collection changes.</li>
      </ul>
      {del.isError && del.error ? (
        <p role="alert" className="mt-2 rounded bg-white p-2 text-red-700">
          {collectionDeleteMessage(
            del.error instanceof ApiError ? del.error.status : null,
            del.error.message,
          )}
        </p>
      ) : null}
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={() => del.mutate()}
          disabled={del.isPending}
          className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
        >
          {del.isPending ? "Removing…" : "Remove binding"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={del.isPending}
          className="rounded border border-red-300 bg-white px-3 py-1 text-xs text-red-700 hover:bg-red-100 disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// --- Delete permanently (purge) -------------------------------------------
//
// The destructive sibling of DeleteConfirm. Two things make it a different
// control rather than a checkbox on the same one: it destroys embeddings that
// cost GPU hours and cannot be recovered from the registry, and the gate is
// TYPING THE ID — a button you can click through is not a gate for that.
export function PurgeConfirm({
  c,
  apiKey,
  onCancel,
  onPurged,
}: {
  c: CollectionInfo;
  apiKey?: string;
  onCancel: () => void;
  onPurged: (report: CollectionPurgeReport) => void;
}) {
  const [typed, setTyped] = useState("");
  const store = c.provenance?.collection ?? null;
  const unlocked = purgeConfirmed(typed, c.id);
  const purge = useMutation<CollectionPurgeReport, Error, void>({
    mutationFn: () => purgeCollection(c.id, apiKey || undefined),
    onSuccess: onPurged,
  });
  const inputId = `purge-confirm-${c.id}`;
  return (
    <div className="border-l-4 border-red-600 bg-red-50 px-4 py-3 text-sm text-red-900">
      <p className="font-semibold">
        Permanently delete “{c.id}” and everything in it?
      </p>
      <p className="mt-1 text-xs">
        This is <strong>irreversible</strong>. The embeddings are destroyed; the only way back is a
        full re-ingest, which costs the GPU time that produced them.
      </p>
      <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
        <li>
          The Qdrant collection{" "}
          {store ? <code className="font-mono">{store}</code> : <em>backing this collection</em>} is
          dropped, with{" "}
          <strong>{c.count != null ? c.count.toLocaleString() : "all of its"}</strong> vector
          {c.count === 1 ? "" : "s"}.
        </li>
        <li>
          Its Elasticsearch index is deleted, with{" "}
          <strong>{c.text_count != null ? c.text_count.toLocaleString() : "all of its"}</strong> BM25
          document{c.text_count === 1 ? "" : "s"}.
        </li>
        <li>The provenance manifest recording how the corpus was built is removed.</li>
        <li>
          The registry binding goes too — the collection disappears from{" "}
          <code className="font-mono">GET /v1/collections</code>.
        </li>
      </ul>
      <label htmlFor={inputId} className="mt-3 block text-xs font-medium">
        Type <code className="font-mono font-semibold">{c.id}</code> to confirm:
      </label>
      <input
        id={inputId}
        type="text"
        value={typed}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => setTyped(e.target.value)}
        disabled={purge.isPending}
        placeholder={c.id}
        className="mt-1 w-64 rounded border border-red-300 bg-white px-2 py-1 font-mono text-xs text-red-900 placeholder:text-red-200 focus:border-red-500 focus:outline-none disabled:opacity-50"
      />
      {purge.isError && purge.error ? (
        <p role="alert" className="mt-2 rounded bg-white p-2 text-xs text-red-700">
          {collectionPurgeMessage(
            purge.error instanceof ApiError ? purge.error.status : null,
            purge.error.message,
          )}
        </p>
      ) : null}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => purge.mutate()}
          disabled={!unlocked || purge.isPending}
          title={unlocked ? undefined : "Type the collection id above to enable this."}
          className="rounded bg-red-700 px-3 py-1 text-xs font-semibold text-white hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {purge.isPending ? "Deleting…" : "Delete permanently"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={purge.isPending}
          className="rounded border border-red-300 bg-white px-3 py-1 text-xs text-red-700 hover:bg-red-100 disabled:opacity-50"
        >
          Cancel
        </button>
        <span className="text-xs text-red-400">
          Prefer Unregister if you only want the id freed — it keeps the data.
        </span>
      </div>
    </div>
  );
}

function CollectionsPanel({ apiKey }: { apiKey?: string }) {
  const queryClient = useQueryClient();
  const cols = useQuery({
    queryKey: ["collections-ops", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    refetchInterval: 15000,
    retry: false,
  });
  const rows = cols.data?.collections ?? [];

  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  // Which row is asking for confirmation, and for WHICH of the two deletes —
  // they are different operations with different consequences, so one row can
  // never be showing both gates at once.
  const [confirming, setConfirming] = useState<{ id: string; mode: "unregister" | "purge" } | null>(
    null,
  );
  // `warn` is the partial-failure case: some of the purge landed and some didn't,
  // which is neither a success nor an error the mutation can retry.
  const [notice, setNotice] = useState<{ text: string; tone: "ok" | "warn" } | null>(null);

  // Both the demo picker (["collections"]) and this panel read the registry, so
  // a create/delete here has to invalidate both or the other view goes stale.
  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["collections-ops", apiKey] });
    void queryClient.invalidateQueries({ queryKey: ["collections", apiKey] });
  };

  return (
    <>
      <SectionHeading id="collections" unavailable={cols.isError}>
        <button
          type="button"
          onClick={() => {
            setNotice(null);
            setCreating((v) => !v);
          }}
          className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {creating ? "Cancel" : "＋ New collection"}
        </button>
      </SectionHeading>

      {creating ? (
        <CreateCollectionForm
          apiKey={apiKey}
          onDone={(created) => {
            setCreating(false);
            setExpanded(created.id);
            setNotice({
              tone: "ok",
              text: `Created “${created.id}” — ${created.model} · ${created.dim}d${
                created.provenance?.collection ? ` → store ${created.provenance.collection}` : ""
              }. It is empty until you ingest into it.`,
            });
            refreshAll();
          }}
        />
      ) : null}

      {notice ? (
        <div
          role="status"
          className={
            notice.tone === "warn"
              ? "mb-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
              : "mb-3 rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800"
          }
        >
          {notice.text}
        </div>
      ) : null}

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
                <th className="px-3 py-2 text-right font-medium">Registry</th>
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
                const open = expanded === c.id;
                return (
                  <Fragment key={c.id}>
                    <tr className="border-t border-gray-100">
                      <td className="px-3 py-2 font-medium text-gray-800">
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : c.id)}
                          aria-expanded={open}
                          className="text-left hover:underline"
                          title="Show what this collection is made of"
                        >
                          <span className="mr-1 text-gray-400">{open ? "▾" : "▸"}</span>
                          {c.label || c.id}
                        </button>
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
                        <ProvenanceBadge p={p} />
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
                      <td className="px-3 py-2 text-right">
                        {c.default ? (
                          <span
                            className="text-xs text-gray-300"
                            title="The default collection can't be unregistered or deleted."
                          >
                            —
                          </span>
                        ) : (
                          // Unregister stays the quiet default; the destructive one
                          // is visually separate and never the primary action.
                          <span className="inline-flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setNotice(null);
                                setConfirming(
                                  confirming?.id === c.id && confirming.mode === "unregister"
                                    ? null
                                    : { id: c.id, mode: "unregister" },
                                );
                              }}
                              className="text-xs text-gray-400 hover:text-gray-700"
                              title="Drop the registry binding only. The stored chunks survive."
                            >
                              Unregister
                            </button>
                            <span className="text-gray-200">|</span>
                            <button
                              type="button"
                              onClick={() => {
                                setNotice(null);
                                setConfirming(
                                  confirming?.id === c.id && confirming.mode === "purge"
                                    ? null
                                    : { id: c.id, mode: "purge" },
                                );
                              }}
                              className="rounded border border-red-200 px-1.5 py-0.5 text-xs font-medium text-red-600 hover:border-red-400 hover:bg-red-50"
                              title="Destroy the data: the Qdrant collection, the Elasticsearch index and the manifest. Irreversible."
                            >
                              Delete permanently
                            </button>
                          </span>
                        )}
                      </td>
                    </tr>
                    {open ? (
                      <tr className="border-t border-gray-100">
                        <td colSpan={8} className="p-0">
                          <CollectionDetail c={c} />
                        </td>
                      </tr>
                    ) : null}
                    {confirming?.id === c.id ? (
                      <tr className="border-t border-gray-100">
                        <td colSpan={8} className="p-0">
                          {confirming.mode === "unregister" ? (
                            <DeleteConfirm
                              c={c}
                              apiKey={apiKey}
                              onCancel={() => setConfirming(null)}
                              onDeleted={() => {
                                setConfirming(null);
                                setExpanded(null);
                                setNotice({
                                  tone: "ok",
                                  text: `Unregistered “${c.id}”. Its physical store${
                                    c.provenance?.collection ? ` (${c.provenance.collection})` : ""
                                  } and Elasticsearch index still exist — use “Delete permanently” (or clean up in Qdrant/ES) if you want the data gone.`,
                                });
                                refreshAll();
                              }}
                            />
                          ) : (
                            <PurgeConfirm
                              c={c}
                              apiKey={apiKey}
                              onCancel={() => setConfirming(null)}
                              onPurged={(report) => {
                                setConfirming(null);
                                setExpanded(null);
                                // A partial failure is still a 200: the server does
                                // not roll back, so report it rather than claiming
                                // a clean delete.
                                setNotice({
                                  tone: report.ok ? "ok" : "warn",
                                  text: purgeReportSummary(report),
                                });
                                refreshAll();
                              }}
                            />
                          )}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 ? (
        <p className="mt-2 text-xs text-gray-400">
          Click a collection to see the model, dimension, chunk strategy and build manifest it was
          made with. <span className="font-medium text-gray-500">Vectors</span> counts the dense
          embeddings in Qdrant; <span className="font-medium text-gray-500">Text</span>{" "}
          counts the BM25 documents in Elasticsearch. Hybrid retrieval queries both legs
          over the <em>same</em> chunks, so equal numbers are the healthy state — a drift
          means one store is missing rows (a partial or failed ingest), not extra data.
          Both are filtered to your readable tenants; very large counts may be
          approximate. <span className="font-medium text-gray-500">Unregister</span> drops the
          registry binding only, never the stored chunks — the physical store keeps costing disk
          until something removes it.{" "}
          <span className="font-medium text-red-600">Delete permanently</span> is the one that
          removes it: it drops the Qdrant collection, the Elasticsearch index and the build
          manifest too, is irreversible, and is refused when another collection still shares the
          same physical store.
        </p>
      ) : null}
    </>
  );
}

// --- Groups (#245) --------------------------------------------------------
//
// RAGStack-native named bags of user subjects. A group is a share target
// (`GRANT read TO @group:<id>` via the ShareDialog's group picker), so managing
// membership here is how a shared collection reaches a set of people at once.
//
// WHY HERE: this mirrors the Collections section's home on the Ops surface —
// groups and collection sharing are the same access-control story, so the place
// you audit "who can read what" is the place you edit the groups those grants
// name. Unlike the collection writes on this page, group create/manage is NOT
// admin-only (ADR-0004): any authenticated caller owns the groups they create,
// and view is owner-or-member — so this panel works with a plain key, and its
// only degraded state is a 503 (the authorization store being down), never a 403
// on the listing itself.
//
// Vocabulary matches the shares flow: managing a group (delete, add/remove
// members) is owner-or-admin, and the error copy in lib/collections.ts says so.

function fmtDay(iso: string): string {
  return iso ? iso.slice(0, 10) : "—";
}

// The expandable membership editor for one group: its active members, a remove
// button per row (owner-or-admin), and an add-a-user input. `subject` is resolved
// server-side exactly like a share grantee, so the input mirrors ShareDialog's.
function GroupMembers({ groupId, apiKey }: { groupId: string; apiKey?: string }) {
  const queryClient = useQueryClient();
  const detailKey = ["group-detail", groupId, apiKey];
  const [subject, setSubject] = useState("");

  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => getGroup(groupId, apiKey || undefined),
    retry: false,
  });

  const refetch = () => queryClient.invalidateQueries({ queryKey: detailKey });

  const add = useMutation<GroupMemberRecord, Error, string>({
    mutationFn: (subj) => addGroupMember(groupId, { subject: subj }, apiKey || undefined),
    onSuccess: async () => {
      await refetch();
      setSubject("");
    },
  });

  const remove = useMutation<void, Error, string>({
    mutationFn: (subj) => removeGroupMember(groupId, subj, apiKey || undefined),
    onSuccess: () => refetch(),
  });

  const members = (detail.data?.members ?? []).filter((m) => m.active);
  const canAdd = subject.trim() !== "" && !add.isPending;
  const submitAdd = () => {
    if (canAdd) add.mutate(subject.trim());
  };

  const listErr = detail.isError ? (detail.error as Error) : null;

  return (
    <div className="bg-gray-50 px-4 py-3 text-sm">
      {listErr ? (
        <p role="alert" className="mb-2 rounded bg-red-50 p-2 text-sm text-red-700">
          {groupMemberRemoveMessage(
            listErr instanceof ApiError ? listErr.status : null,
            listErr.message,
          )}
        </p>
      ) : null}

      <div className="mb-3">
        <label
          htmlFor={`group-add-${groupId}`}
          className="mb-1 block text-xs font-medium text-gray-500"
        >
          Add a user
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id={`group-add-${groupId}`}
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitAdd();
            }}
            placeholder="e.g. alice or bvbrc:alice"
            className="min-w-[14rem] flex-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            type="button"
            onClick={submitAdd}
            disabled={!canAdd || listErr != null}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white transition-opacity hover:bg-blue-700 disabled:opacity-50"
          >
            {add.isPending ? "Adding…" : "Add member"}
          </button>
        </div>
        <p className="mt-1 text-[11px] leading-snug text-gray-400">
          A BV-BRC username, or a full subject like{" "}
          <span className="font-mono">bvbrc:alice</span>. Groups can't nest — a member is
          always a user.
        </p>
        {add.isError && add.error ? (
          <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-red-700">
            {groupMemberAddMessage(
              add.error instanceof ApiError ? add.error.status : null,
              add.error.message,
            )}
          </p>
        ) : null}
      </div>

      <h4 className="mb-1 text-xs font-medium text-gray-500">Members</h4>
      {detail.isLoading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : members.length === 0 && !listErr ? (
        <p className="text-sm text-gray-400">
          No members yet — add a user above. An empty group grants no one anything.
        </p>
      ) : (
        <ul className="space-y-1">
          {members.map((m) => (
            <li
              key={m.id}
              className="flex items-center justify-between rounded border border-gray-200 bg-white px-3 py-1.5 text-sm"
            >
              <span className="truncate font-mono text-xs text-gray-800" title={m.subject}>
                {m.subject}
              </span>
              <button
                type="button"
                onClick={() => remove.mutate(m.subject)}
                disabled={remove.isPending}
                className="ml-3 shrink-0 text-xs text-gray-400 hover:text-red-600 disabled:opacity-50"
                aria-label={`Remove ${m.subject}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {remove.isError && remove.error ? (
        <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-red-700">
          {groupMemberRemoveMessage(
            remove.error instanceof ApiError ? remove.error.status : null,
            remove.error.message,
          )}
        </p>
      ) : null}
    </div>
  );
}

function GroupsPanel({ apiKey }: { apiKey?: string }) {
  const queryClient = useQueryClient();
  const groupsKey = ["groups", apiKey];
  const groups = useQuery({
    queryKey: groupsKey,
    queryFn: () => listGroups(apiKey || undefined),
    retry: false,
  });
  const rows = groups.data?.groups ?? [];

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: groupsKey });

  const create = useMutation<GroupRecord, Error, string>({
    mutationFn: (n) => createGroup({ name: n }, apiKey || undefined),
    onSuccess: async (g) => {
      await refresh();
      setName("");
      setCreating(false);
      setExpanded(g.id);
    },
  });

  const del = useMutation<void, Error, string>({
    mutationFn: (id) => deleteGroup(id, apiKey || undefined),
    onSuccess: async () => {
      await refresh();
      setConfirmDelete(null);
      setExpanded(null);
    },
  });

  const canCreate = name.trim() !== "" && !create.isPending;
  const submitCreate = () => {
    if (canCreate) create.mutate(name.trim());
  };

  // listGroups is open to any authenticated caller, so its only failure is a 503
  // (store down); surface it, but the section stays "available" for the TOC.
  const listErr = groups.isError ? (groups.error as Error) : null;

  return (
    <>
      <SectionHeading id="groups">
        <button
          type="button"
          onClick={() => {
            create.reset();
            setCreating((v) => !v);
          }}
          className="rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {creating ? "Cancel" : "＋ New group"}
        </button>
      </SectionHeading>

      {creating ? (
        <div className="mb-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
          <label htmlFor="ops-group-name" className="mb-1 block text-xs font-medium text-gray-500">
            Group name
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <input
              id="ops-group-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitCreate();
              }}
              placeholder="e.g. lab-team"
              className="min-w-[14rem] flex-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <button
              type="button"
              onClick={submitCreate}
              disabled={!canCreate}
              className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white transition-opacity hover:bg-blue-700 disabled:opacity-50"
            >
              {create.isPending ? "Creating…" : "Create group"}
            </button>
          </div>
          <p className="mt-1 text-[11px] leading-snug text-gray-400">
            You own the group; add members below, then share a collection with it from the
            Collection tab’s Share panel (“Share with a group”). “public” is reserved.
          </p>
          {create.isError && create.error ? (
            <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-red-700">
              {groupCreateMessage(
                create.error instanceof ApiError ? create.error.status : null,
                create.error.message,
              )}
            </p>
          ) : null}
        </div>
      ) : null}

      {listErr ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {groupCreateMessage(
            listErr instanceof ApiError ? listErr.status : null,
            listErr.message,
          )}
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 p-4 text-center text-sm text-gray-400">
          You don’t own or belong to any groups yet. Create one to share a collection with a
          set of people at once.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-400">
              <tr>
                <th className="px-3 py-2 font-medium">Group</th>
                <th className="px-3 py-2 font-medium">Owner</th>
                <th className="px-3 py-2 font-medium">Created</th>
                <th className="px-3 py-2 text-right font-medium">Manage</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => {
                const open = expanded === g.id;
                return (
                  <Fragment key={g.id}>
                    <tr className="border-t border-gray-100">
                      <td className="px-3 py-2 font-medium text-gray-800">
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : g.id)}
                          aria-expanded={open}
                          className="text-left hover:underline"
                          title="Show and edit this group's members"
                        >
                          <span className="mr-1 text-gray-400">{open ? "▾" : "▸"}</span>
                          {g.name}
                        </button>
                        <span className="ml-2 font-mono text-[11px] text-gray-400" title={g.id}>
                          {g.id.slice(0, 8)}
                        </span>
                      </td>
                      <td className="max-w-xs truncate px-3 py-2 font-mono text-xs text-gray-600" title={g.owner_subject}>
                        {g.owner_subject || "—"}
                      </td>
                      <td className="px-3 py-2 tabular-nums text-gray-500">{fmtDay(g.created_at)}</td>
                      <td className="px-3 py-2 text-right">
                        <span className="inline-flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setExpanded(open ? null : g.id)}
                            className="text-xs text-gray-400 hover:text-gray-700"
                          >
                            {open ? "Hide members" : "Members"}
                          </button>
                          <span className="text-gray-200">|</span>
                          <button
                            type="button"
                            onClick={() => {
                              del.reset();
                              setConfirmDelete(confirmDelete === g.id ? null : g.id);
                            }}
                            className="text-xs text-red-500 hover:text-red-700"
                            title="Delete this group. Shares granted to it become inert immediately."
                          >
                            Delete
                          </button>
                        </span>
                      </td>
                    </tr>
                    {open ? (
                      <tr className="border-t border-gray-100">
                        <td colSpan={4} className="p-0">
                          <GroupMembers groupId={g.id} apiKey={apiKey} />
                        </td>
                      </tr>
                    ) : null}
                    {confirmDelete === g.id ? (
                      <tr className="border-t border-gray-100">
                        <td colSpan={4} className="p-0">
                          <div className="border-l-4 border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
                            <p className="font-medium">Delete group “{g.name}”?</p>
                            <p className="mt-1 text-xs">
                              Any collection shared with this group stops being readable through
                              it immediately. Members and the group row are kept as an audited
                              soft-delete, not erased. Only the owner (or an admin) can do this.
                            </p>
                            {del.isError && del.error ? (
                              <p role="alert" className="mt-2 rounded bg-white p-2 text-red-700">
                                {groupDeleteMessage(
                                  del.error instanceof ApiError ? del.error.status : null,
                                  del.error.message,
                                )}
                              </p>
                            ) : null}
                            <div className="mt-2 flex gap-2">
                              <button
                                type="button"
                                onClick={() => del.mutate(g.id)}
                                disabled={del.isPending}
                                className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                              >
                                {del.isPending ? "Deleting…" : "Delete group"}
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmDelete(null)}
                                disabled={del.isPending}
                                className="rounded border border-red-300 bg-white px-3 py-1 text-xs text-red-700 hover:bg-red-100 disabled:opacity-50"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-xs text-gray-400">
        Groups you own or belong to. Expand one to add or remove members — a member is always a
        user (groups don’t nest). Share a collection with a group from the Collection tab’s Share
        panel; every active member then reads it, and removing a member revokes their access on
        their next request. Managing a group is owner-or-admin.
      </p>
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

        <GroupsPanel apiKey={apiKey} />

        <TenantsPanel apiKey={apiKey} />

        <ModelsPanel apiKey={apiKey} />

        <JobsPanel apiKey={apiKey} />

        <SectionHeading id="health" unavailable={health.isError} />
        {health.isError ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            {healthErr?.status === 403
              ? "Deep health is admin-only. Start the API with DEFAULT_ROLE=admin (keyless callers default to 'user'), or enter an admin key above."
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
