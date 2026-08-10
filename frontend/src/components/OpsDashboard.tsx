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
import { lookupTerm } from "../lib/glossary";
import { ChunkStrategyPicker } from "./ChunkStrategyPicker";
import { GlossaryPanel } from "./GlossaryPanel";
import { HelpTip } from "./HelpTip";
import { StatusBand, storeTitle } from "./ops/StatusBand";

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

// One list drives both the TOC rail and every heading: SectionHeading renders
// its text from here, so a section can't exist without a nav entry (or vice
// versa). Order matches the render order below (mockup 6a).
const SECTIONS = [
  { id: "health", label: "Deep health" },
  { id: "stores", label: "Stores" },
  { id: "collections", label: "Collections" },
  { id: "groups", label: "Groups" },
  { id: "models", label: "Models" },
  // "Data ownership", not "Tenants": in this operation a tenant is a whole
  // deployment (dev/lucid/asm); these rows are owner scopes inside ONE of them.
  { id: "tenants", label: "Data ownership" },
  { id: "jobs", label: "Ingest jobs" },
  { id: "config", label: "Config" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

const SECTION_LABEL = Object.fromEntries(SECTIONS.map((s) => [s.id, s.label])) as Record<
  SectionId,
  string
>;

// Headings report their state upward so the TOC can dim the sections that
// 403'd ("admin" — dimmed with a chip) or failed outright ("down"), instead of
// advertising them as live.
type SectionState = "ok" | "admin" | "down";

const ReportSection = createContext<(id: SectionId, state: SectionState) => void>(() => {});

function SectionHeading({
  id,
  gated,
  unavailable,
  meta,
  help,
  children,
}: {
  id: SectionId;
  // 401/403 — the section exists but this key's role can't read it.
  gated?: boolean;
  unavailable?: boolean;
  // Mono side-note next to the label (endpoint, "n of MAX", …).
  meta?: ReactNode;
  // Panel body for the heading's "?" — what the section shows and where it
  // comes from. The accessible name is the section label, so every "?" on this
  // page announces which section it belongs to.
  help?: ReactNode;
  children?: ReactNode;
}) {
  const report = useContext(ReportSection);
  const state: SectionState = gated ? "admin" : unavailable ? "down" : "ok";
  useEffect(() => report(id, state), [report, id, state]);
  return (
    <div className="mb-3.5 mt-9 flex items-baseline gap-2.5 first:mt-0">
      {/* scroll-mt clears the sticky chip strip the TOC becomes on small screens */}
      <h2
        id={id}
        className={`scroll-mt-16 font-mono text-[11px] font-medium uppercase tracking-[.14em] ${
          gated ? "text-faint" : "text-muted"
        }`}
      >
        {SECTION_LABEL[id]}
      </h2>
      {help ? (
        <HelpTip icon side="bottom" term={SECTION_LABEL[id]}>
          {help}
        </HelpTip>
      ) : null}
      {meta ? <span className="font-mono text-[11px] text-[#c4c4be]">{meta}</span> : null}
      {children ? <span className="ml-auto flex items-center gap-2">{children}</span> : null}
    </div>
  );
}

// 196px sticky paper rail on wide screens; a horizontal chip strip below md
// (README responsive rule). The active item is navy/white; role-gated items are
// dimmed with an "admin" chip and the rail's foot explains why in one line.
function SectionToc({
  state,
  active,
  onSelect,
}: {
  state: Partial<Record<SectionId, SectionState>>;
  active: SectionId;
  onSelect: (id: SectionId) => void;
}) {
  const anyDimmed = SECTIONS.some((s) => state[s.id] === "admin");
  return (
    // Wrapper paints the full-height paper column (and is itself the sticky
    // chip strip on small screens); the nav re-sticks inside it at width.
    <div className="sticky top-0 z-10 border-b border-line bg-paper md:static md:z-auto md:border-b-0 md:border-r">
    <nav
      aria-label="Sections"
      className="flex items-center gap-1 overflow-x-auto px-4 py-2 md:sticky md:top-0 md:block md:overflow-visible md:py-6"
    >
      <div className="hidden font-mono text-[10px] font-medium uppercase tracking-[.12em] text-muted md:mb-3.5 md:block">
        Sections
      </div>
      <div className="flex items-center gap-1 md:flex-col md:items-stretch md:gap-[3px]">
        {SECTIONS.map((s) => {
          const st = state[s.id] ?? "ok";
          const current = active === s.id;
          return (
            <a
              key={s.id}
              href={`#${s.id}`}
              onClick={() => onSelect(s.id)}
              title={
                st === "admin"
                  ? "admin-only — this key's role can't read it"
                  : st === "down"
                    ? "failed to load"
                    : undefined
              }
              className={`flex shrink-0 items-center gap-[7px] whitespace-nowrap rounded-row px-[11px] py-[9px] text-[13px] font-medium ${
                current
                  ? "bg-ink-900 text-white"
                  : st === "ok"
                    ? "text-body hover:bg-lineSoft"
                    : "text-faint"
              }`}
            >
              {s.label}
              {st === "admin" ? (
                <span
                  className={`rounded-[3px] px-[5px] py-[3px] font-mono text-[9.5px] leading-none ${
                    current ? "bg-white/15 text-white" : "bg-[#e9e8e4] text-muted"
                  }`}
                >
                  admin
                </span>
              ) : null}
            </a>
          );
        })}
      </div>
      {anyDimmed ? (
        <div className="mt-[22px] hidden border-t border-line pt-[18px] text-[11.5px] leading-relaxed text-dim md:block">
          Dimmed sections need a role your key doesn&apos;t have.
        </div>
      ) : null}
    </nav>
    </div>
  );
}

// Shared table treatment (mockup 6a): paper header strip in mono uppercase,
// hairline row dividers, panel radius on the wrapper.
const THEAD = "bg-paper text-left font-mono text-[10px] uppercase tracking-[.1em] text-muted";
const TH = "px-3.5 py-2.5 font-medium";

// Outline pill for section-heading actions ("New collection →", …).
const PILL =
  "rounded-chip border border-ink-900 px-3.5 py-[7px] text-xs font-medium text-ink-900 hover:bg-ink-900 hover:text-white disabled:opacity-50";

// A 403'd section renders one dim explanatory line — never an error blob.
function GatedNote({ children }: { children: ReactNode }) {
  return <p className="text-[12.5px] leading-relaxed text-faint">{children}</p>;
}

// Non-permission failures also stay a single line, just in the failure color.
function ErrLine({ children }: { children: ReactNode }) {
  return <p className="text-[12.5px] leading-relaxed text-rust">{children}</p>;
}

const gatedErr = (e: unknown): boolean =>
  e instanceof ApiError && (e.status === 403 || e.status === 401);

// `variant` picks the value's type case. "stat" is the mockup's Archivo numeral
// — right for counts and one-word states. "id" is for identifiers such as an
// account subject: at display weight those become a shouted, wrapping headline,
// so they get mono at reading size and break on their own separators.
function KpiCard({
  label,
  value,
  sub,
  variant = "stat",
}: {
  label: string;
  value: string;
  sub?: string;
  variant?: "stat" | "id";
}) {
  return (
    <div className="rounded-card border border-line bg-white p-4">
      <div className="font-mono text-[10px] uppercase tracking-[.1em] text-muted">{label}</div>
      <div
        className={
          variant === "id"
            ? "mt-1.5 break-all font-mono text-[12.5px] font-medium leading-snug text-ink-900"
            : "mt-1.5 font-display text-[22px] font-extrabold leading-none text-ink-900"
        }
      >
        {value}
      </div>
      {sub ? <div className="mt-1.5 truncate font-mono text-[10.5px] text-dim">{sub}</div> : null}
    </div>
  );
}

// Model state leads its table row: reachable ⇒ ready. There is no cold-start
// signal in /v1/stats/models, so the only other honest states are the server's
// own note ("not configured" / "disabled") and down.
function modelState(m: ModelStatus): { label: string; cls: string } {
  if (m.reachable) return { label: "ready", cls: "text-[#1f6b4c]" };
  if (m.note) return { label: m.note, cls: "text-dim" };
  return { label: "down", cls: "text-rust" };
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

  const gated = models.isError && gatedErr(models.error);

  return (
    <>
      <SectionHeading
        id="models"
        gated={gated}
        unavailable={models.isError}
        meta={models.data ? `serving · ${models.data.models.length}` : undefined}
        help={
          <>
            GET /v1/stats/models, re-read every 8s: one row per task role this server serves
            (embedding, llm, reranker…). State is reachability only — “ready” means an endpoint
            answered this probe. There is no warm-up signal in the API, so a model still loading
            reads as down until it responds, and “not configured” / “disabled” is the server’s own
            note. Throughput stays “—” until Measure throughput runs, and that is one batched call
            across the live fleet, not a saturation benchmark. Admin-only.
          </>
        }
      >
        {!models.isError && (
          <button
            type="button"
            onClick={() => bench.mutate()}
            disabled={bench.isPending}
            className={PILL}
          >
            {bench.isPending ? "measuring…" : "Measure throughput"}
          </button>
        )}
      </SectionHeading>

      {models.isError ? (
        gated ? (
          <GatedNote>
            Model status is admin-only — start the API with DEFAULT_ROLE=admin, or enter an admin
            key.
          </GatedNote>
        ) : (
          <ErrLine>Unavailable: {(models.error as Error).message}</ErrLine>
        )
      ) : (
        <div className="overflow-x-auto rounded-panel border border-line">
          <table className="w-full text-sm">
            <thead className={THEAD}>
              <tr>
                <th className={TH}>State</th>
                <th className={TH}>Model</th>
                <th className={TH}>Task</th>
                <th className={TH}>Endpoints</th>
                <th className={TH}>Throughput</th>
              </tr>
            </thead>
            <tbody>
              {(models.data?.models ?? []).map((m) => {
                const st = modelState(m);
                return (
                  <tr key={m.role} className="border-t border-lineSoft">
                    <td className={`px-3.5 py-2.5 font-mono text-[10px] font-medium ${st.cls}`}>
                      {st.label}
                    </td>
                    <td
                      className="max-w-xs truncate px-3.5 py-2.5 font-mono text-[11px] text-strong"
                      title={m.model}
                    >
                      {m.model}
                      {m.dim ? <span className="text-dim"> · {m.dim}d</span> : null}
                    </td>
                    <td className="px-3.5 py-2.5 text-[11px] text-dim">{m.role}</td>
                    <td
                      className="px-3.5 py-2.5 font-mono text-[11px] tabular-nums text-dim"
                      title={m.endpoints.map((e) => `${e.url} — ${e.reachable ? "up" : "down"}${e.latency_ms != null ? ` (${e.latency_ms} ms)` : ""}`).join("\n")}
                    >
                      {endpointSummary(m)}
                    </td>
                    <td className="px-3.5 py-2.5 font-mono text-[11px] tabular-nums text-body">
                      {throughputCell(m.role, bench.data)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {bench.isError ? (
            <p className="px-3.5 py-2 text-xs text-rust">
              Benchmark failed: {(bench.error as Error).message}
            </p>
          ) : bench.data ? (
            <p className="px-3.5 py-2 text-xs text-dim">
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

// URLs in config (store DSNs, embedding endpoints) may embed credentials
// (scheme://user:pass@host). The chip next to the heading promises redaction,
// so every rendered value passes through here.
function redactCreds(v: string): string {
  return v.replace(/\/\/[^/@\s]*@/g, "//***@");
}

function Row({ k, v }: { k: string; v: unknown }) {
  const raw = Array.isArray(v) ? v.join(", ") : v == null || v === "" ? "—" : String(v);
  const val = redactCreds(raw);
  return (
    <div className="flex justify-between gap-3">
      <dt className="shrink-0 text-dim">{k}</dt>
      <dd className="truncate text-right text-body" title={val}>
        {val}
      </dd>
    </div>
  );
}

function ConfigGroup({ title, rows }: { title: string; rows: [string, unknown][] }) {
  return (
    <div>
      <div className="mb-[7px] font-mono text-[10px] font-medium uppercase tracking-[.1em] text-faint">
        {title}
      </div>
      <dl className="font-mono text-[11.5px] leading-[1.7]">
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
  const gated = cfg.isError && gatedErr(cfg.error);
  const c: AppConfig = cfg.data ?? {};

  return (
    <>
      <SectionHeading
        id="config"
        gated={gated}
        unavailable={cfg.isError}
        help={
          <>
            GET /v1/config — the settings this server is actually running with. Read-only: nothing
            on this page writes them, they come from the API&apos;s environment. Any credentials
            embedded in a URL (<span className="font-mono">scheme://user:pass@host</span>) are
            rewritten to <span className="font-mono">***</span> before a value is rendered. This is
            also where MAX_COLLECTIONS lives, which is why a non-admin key sees the Collections
            count with no cap beside it.
          </>
        }
        meta={
          cfg.isError ? undefined : (
            <span className="rounded-[3px] bg-linkSoft px-[7px] py-1 font-mono text-[10px] normal-case tracking-normal text-link">
              admin only · URL credentials redacted
            </span>
          )
        }
      />
      {cfg.isError ? (
        gated ? (
          <GatedNote>
            Config is admin-only — start the API with DEFAULT_ROLE=admin, or enter an admin key.
          </GatedNote>
        ) : (
          <ErrLine>Unavailable: {(cfg.error as Error).message}</ErrLine>
        )
      ) : (
        // Three mono columns (mockup 6a): Retrieval / Stores / Limits. Every key
        // the old six-card layout showed still renders, regrouped.
        <div className="grid grid-cols-1 gap-x-[26px] gap-y-5 rounded-panel border border-line px-5 py-[18px] sm:grid-cols-3">
          <div className="space-y-5">
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
          </div>
          <div className="space-y-5">
            <ConfigGroup
              title="Stores"
              rows={[
                ["vector", c.vector_backend],
                ["text", c.text_backend],
                ["graph", c.graph_backend],
                ["jobs", c.job_store_backend],
                ["collection", c.qdrant_collection_explicit || c.qdrant_collection],
                ["es index", c.elasticsearch_index],
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
          </div>
          <ConfigGroup
            title="Limits"
            rows={[
              ["ingest conc.", c.ingest_concurrency],
              ["tenant conc.", c.tenant_max_concurrency || "unbounded"],
              ["embed conc.", c.embedding_max_concurrency],
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
    // The dash stands where every other row carries a verdict, so what it means
    // is reachable — not a hover-only title on a non-focusable span.
    return (
      <span className="inline-flex items-center gap-1 font-mono text-[10px] text-faint">
        —
        <HelpTip icon side="left">
          One of the two counts is unavailable, so the legs can&apos;t be compared. A store
          that answers no count is usually disabled or unreachable, not empty.
        </HelpTip>
      </span>
    );
  }
  const delta = Math.abs(vec - text);
  const rel = delta / Math.max(vec, text, 1);
  if (delta === 0) {
    return (
      <span className="rounded-[3px] bg-mossSoft px-1.5 py-0.5 font-mono text-[10px] font-medium text-[#1f6b4c]">
        ✓ match
      </span>
    );
  }
  if (rel <= 0.02) {
    return (
      <span
        className="rounded-[3px] bg-accent-soft px-1.5 py-0.5 font-mono text-[10px] font-medium text-accent-text"
        title={`vector and text counts differ by ${delta.toLocaleString()} (~${(rel * 100).toFixed(1)}%) — likely an approximate count on a large collection`}
      >
        ≈ close
      </span>
    );
  }
  return (
    <span
      className="rounded-[3px] bg-rustSoft px-1.5 py-0.5 font-mono text-[10px] font-medium text-rust"
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

// Both branches carry their explanation in a tip, not a native title: the fix
// for a missing manifest and the manifest's own contents are consequential, and
// a title on a <span> reaches neither keyboard nor touch.
function ProvenanceBadge({ p }: { p?: Provenance | null }) {
  if (!p) {
    return (
      <span className="inline-flex items-center gap-1 font-mono text-[10px] text-faint">
        none
        {/* side="right": this badge sits in the table's FIRST column, and the
            wrapper's overflow-x clips anything reaching past its left edge. */}
        <HelpTip icon side="right">
          No build manifest for this collection — set COLLECTION_MANIFEST_DIR and restart to
          materialize one from the registry spec (an ingest through this API then upgrades it
          to a verified record).
        </HelpTip>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className={`rounded-[3px] px-1 py-0.5 font-mono text-[10px] font-medium ${
          p.source === "ingest" ? "bg-mossSoft text-[#1f6b4c]" : "bg-[#f2f1ed] text-[#6a6a64]"
        }`}
      >
        {p.source === "ingest" ? "verified" : "declared"}
      </span>
      {p.ingested_at ? (
        <span className="font-mono text-[10px] text-faint">{p.ingested_at.slice(0, 10)}</span>
      ) : null}
      <HelpTip icon side="right" term="verified vs declared">
        <span className="mb-1.5 block">{lookupTerm("verified vs declared")}</span>
        <span className="block whitespace-pre-line font-mono text-[11px]">
          {provenanceDetail(p)}
        </span>
      </HelpTip>
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
    <div className="grid gap-4 bg-paper px-4 py-3 text-sm sm:grid-cols-2">
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
    <div className="mb-3 rounded-panel border border-line bg-paper p-4">
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
          This is the only delete for a store no other collection shares.
        </span>
      </div>
    </div>
  );
}

// Access chip per collection row. GET /v1/collections carries no share data and
// this page doesn't fetch per-collection shares (N admin-gated requests), so the
// only honest chips are "default" and a dash. The column header's HelpTip says
// so once, rather than every cell repeating it in a native title.
function AccessChip({ c }: { c: CollectionInfo }) {
  if (c.default) {
    return (
      <span className="rounded-[3px] bg-[#f2f1ed] px-2 py-[5px] font-mono text-[10px] font-medium text-[#6a6a64]">
        default
      </span>
    );
  }
  return <span className="font-mono text-[10px] text-faint">—</span>;
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

  // "n of MAX_COLLECTIONS" in the heading — the cap only exists in the
  // admin-only config, so non-admins just see the count. Same query key as
  // ConfigPanel: one request serves both.
  const cfg = useQuery({
    queryKey: ["config", apiKey],
    queryFn: () => getConfig(apiKey || undefined),
    refetchInterval: 30000,
    retry: false,
  });
  const maxCollections =
    typeof cfg.data?.max_collections === "number" ? cfg.data.max_collections : null;

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
      <SectionHeading
        id="collections"
        unavailable={cols.isError}
        help={
          <>
            Every entry in the collection registry (GET /v1/collections). Several entries can
            point at the same physical store, which is exactly when Unregister is offered and
            Delete permanently is refused. The badge beside a name is the build manifest,{" "}
            <em>verified</em> or <em>declared</em>. The count in the heading is entries against
            MAX_COLLECTIONS, which lives in the admin-only config — without an admin key you see
            the count alone.
          </>
        }
        meta={
          cols.data
            ? maxCollections != null
              ? `${rows.length} of ${maxCollections} · MAX_COLLECTIONS`
              : String(rows.length)
            : undefined
        }
      >
        <button
          type="button"
          onClick={() => {
            setNotice(null);
            setCreating((v) => !v);
          }}
          className={PILL}
        >
          {creating ? "Cancel" : "New collection →"}
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
        <ErrLine>Unavailable: {(cols.error as Error).message}</ErrLine>
      ) : rows.length === 0 ? (
        <div className="rounded-panel border border-dashed border-line p-4 text-center text-sm text-faint">
          No collections registered.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-panel border border-line">
          <table className="w-full text-sm">
            <thead className={THEAD}>
              <tr>
                <th className={TH}>Collection</th>
                <th className={TH}>Embedding model</th>
                <th className={TH}>Chunking</th>
                <th className={`${TH} text-right`}>
                  {/* Was a native title="": the column beside it already carries
                      its explanation as a HelpTip, and one header row cannot
                      have two kinds of help. side="bottom-end" because the table
                      scrolls in an overflow box that clips a centred panel. */}
                  <span className="inline-flex items-center gap-1.5">
                    Chunks
                    <HelpTip icon side="bottom-end" term="drift">
                      Both legs&apos; counts for this collection, vector store then text index
                      (BM25), filtered to this credential&apos;s scope. The badge flags drift
                      between them. {lookupTerm("drift")}
                    </HelpTip>
                  </span>
                </th>
                <th className={TH}>
                  <span className="inline-flex items-center gap-1.5">
                    Access
                    <HelpTip icon side="bottom-end" term="access">
                      {lookupTerm("access")} The <em>default</em> chip marks the fallback
                      collection, which can&apos;t be unregistered or deleted.
                    </HelpTip>
                  </span>
                </th>
                <th className={`${TH} text-right`}>Registry</th>
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
                    <tr className="border-t border-lineSoft">
                      <td className="px-3.5 py-3 text-[12.5px] font-semibold text-ink-900">
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : c.id)}
                          aria-expanded={open}
                          className="text-left hover:underline"
                          title="Show what this collection is made of"
                        >
                          <span className="mr-1 text-faint">{open ? "▾" : "▸"}</span>
                          {c.label || c.id}
                        </button>
                        <span className="ml-1.5 font-normal">
                          <ProvenanceBadge p={p} />
                        </span>
                      </td>
                      <td className="max-w-xs truncate px-3.5 py-3 font-mono text-[11px] text-[#6a6a64]" title={c.model}>
                        {c.model}
                        <span className="text-faint"> · {c.dim}d</span>
                      </td>
                      <td className="px-3.5 py-3 font-mono text-[11px] text-body">{chunking}</td>
                      {/* The BM25 count is visible text, not a title on the cell:
                          it is half of what the parity badge is judging, and a
                          <td> tooltip reaches neither keyboard nor touch. */}
                      <td className="whitespace-nowrap px-3.5 py-3 text-right font-mono text-[11px] tabular-nums text-body">
                        {c.count != null ? c.count.toLocaleString() : "—"}
                        <span className="text-[#6a6a64]">
                          {" / "}
                          {c.text_count != null ? c.text_count.toLocaleString() : "—"}
                        </span>
                        <span className="ml-1.5">
                          <ParityBadge vec={c.count} text={c.text_count} />
                        </span>
                      </td>
                      <td className="px-3.5 py-3">
                        <AccessChip c={c} />
                      </td>
                      <td className="px-3.5 py-3 text-right">
                        {c.default ? (
                          // This dash stands where every other row has buttons,
                          // so why it has none has to be reachable.
                          <span className="inline-flex items-center gap-1 text-xs text-faint">
                            —
                            <HelpTip icon side="left">
                              The default collection can&apos;t be unregistered or deleted — it is
                              the one a query falls back to when it names none.
                            </HelpTip>
                          </span>
                        ) : (
                          // Unregister is only offered when ANOTHER listed collection
                          // serves the same physical store. Otherwise the server
                          // refuses it (409): dropping the last binding would leave
                          // the data with no collection claiming it and no permissions
                          // governing it. Offering a button that always errors is worse
                          // than not offering it.
                          <span className="inline-flex items-center gap-2">
                            {rows.some(
                              (o) =>
                                o.id !== c.id &&
                                !!o.provenance?.collection &&
                                o.provenance?.collection === c.provenance?.collection,
                            ) ? (
                              <>
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
                                  className="text-xs text-dim hover:text-strong"
                                  title="Another collection shares this store, so the data survives the unregister."
                                >
                                  Unregister
                                </button>
                                <span className="text-lineSoft">|</span>
                              </>
                            ) : null}
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
                      <tr className="border-t border-lineSoft">
                        <td colSpan={6} className="p-0">
                          <CollectionDetail c={c} />
                        </td>
                      </tr>
                    ) : null}
                    {confirming?.id === c.id ? (
                      <tr className="border-t border-lineSoft">
                        <td colSpan={6} className="p-0">
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
      {/* Only what no tip on this table already carries: drift is on the Chunks
          header, the readable-scope filter on the section heading, and
          unregister-vs-purge in the glossary and both confirm panels. */}
      {rows.length > 0 ? (
        <p className="mt-2.5 text-[11.5px] leading-relaxed text-dim">
          Click a collection to see the model, dimension, chunk strategy and build manifest it was
          made with. <span className="font-medium text-body">Unregister</span> appears only when
          another collection shares the same physical store.
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
    <div className="bg-paper px-4 py-3 text-sm">
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
      <SectionHeading id="groups" meta={groups.data ? String(rows.length) : undefined}>
        <button
          type="button"
          onClick={() => {
            create.reset();
            setCreating((v) => !v);
          }}
          className={PILL}
        >
          {creating ? "Cancel" : "New group →"}
        </button>
      </SectionHeading>

      {creating ? (
        <div className="mb-3 rounded-panel border border-line bg-paper p-4">
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
        <ErrLine>
          {groupCreateMessage(
            listErr instanceof ApiError ? listErr.status : null,
            listErr.message,
          )}
        </ErrLine>
      ) : rows.length === 0 ? (
        <div className="rounded-panel border border-dashed border-line p-4 text-center text-sm text-faint">
          You don’t own or belong to any groups yet. Create one to share a collection with a
          set of people at once.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-panel border border-line">
          <table className="w-full text-sm">
            <thead className={THEAD}>
              <tr>
                <th className={TH}>Group</th>
                <th className={TH}>Owner</th>
                <th className={TH}>Created</th>
                <th className={`${TH} text-right`}>Manage</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => {
                const open = expanded === g.id;
                return (
                  <Fragment key={g.id}>
                    <tr className="border-t border-lineSoft">
                      <td className="px-3.5 py-3 text-[12.5px] font-semibold text-ink-900">
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : g.id)}
                          aria-expanded={open}
                          className="text-left hover:underline"
                          title="Show and edit this group's members"
                        >
                          <span className="mr-1 text-faint">{open ? "▾" : "▸"}</span>
                          {g.name}
                        </button>
                        <span className="ml-2 font-mono text-[11px] font-normal text-faint" title={g.id}>
                          {g.id.slice(0, 8)}
                        </span>
                      </td>
                      <td className="max-w-xs truncate px-3.5 py-3 font-mono text-[11px] text-[#6a6a64]" title={g.owner_subject}>
                        {g.owner_subject || "—"}
                      </td>
                      <td className="px-3.5 py-3 font-mono text-[11px] tabular-nums text-dim">{fmtDay(g.created_at)}</td>
                      <td className="px-3.5 py-3 text-right">
                        <span className="inline-flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setExpanded(open ? null : g.id)}
                            className="text-xs text-dim hover:text-strong"
                          >
                            {open ? "Hide members" : "Members"}
                          </button>
                          <span className="text-lineSoft">|</span>
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
                      <tr className="border-t border-lineSoft">
                        <td colSpan={4} className="p-0">
                          <GroupMembers groupId={g.id} apiKey={apiKey} />
                        </td>
                      </tr>
                    ) : null}
                    {confirmDelete === g.id ? (
                      <tr className="border-t border-lineSoft">
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
      <p className="mt-2.5 text-[11.5px] leading-relaxed text-dim">
        Groups you own or belong to. Expand one to add or remove members — a member is always a
        user (groups don’t nest). Share a collection with a group from the Collection tab’s Share
        panel; every active member then reads it, and removing a member revokes their access on
        their next request. Managing a group is owner-or-admin.
      </p>
    </>
  );
}

// --- Tenancy --------------------------------------------------------------

// /v1/stats/stores reports one number per store for the UNION of readable owner
// scopes (you + public). This panel splits that union apart per collection, which
// is how you spot a corpus sitting entirely in `public` when you expected it under
// your own account. Fetched on demand (no refetchInterval): it costs a count per
// owner x collection x store.
//
// TERMINOLOGY: the API calls these rows "tenants" (/v1/stats/tenants, tenant_id
// payload filtering), but in this operation "tenant" means a whole DEPLOYMENT —
// the dev/lucid/asm stacks with their own ports and stores. What the rows here
// actually are is data-OWNERSHIP scopes: your account's subject, plus the shared
// `public` bucket everyone may read. The UI copy says "owner"/"account"; only
// code-level identifiers (query keys, env var names, API fields) keep "tenant".
function TenantsPanel({ apiKey }: { apiKey?: string }) {
  const t = useQuery({
    queryKey: ["tenants", apiKey],
    queryFn: () => getTenants(apiKey || undefined),
    retry: false,
  });
  const data = t.data;
  const cols = data?.tenants[0]?.collections ?? [];

  // Tenants the admin policy names but the listing doesn't return — visible
  // only when the policy itself is readable (admin), so the filtering is shown
  // rather than silently hidden.
  const hiddenTenants = data?.policy
    ? Object.keys(data.policy).filter((p) => !data.tenants.some((r) => r.tenant === p)).length
    : 0;

  return (
    <>
      <SectionHeading
        id="tenants"
        unavailable={t.isError}
        meta="readable only"
        help={
          <>
            GET /v1/stats/tenants. Each row is a data-ownership scope inside this one deployment:
            your account&apos;s subject, plus every other scope this credential may read — normally
            the shared <span className="font-mono">public</span> bucket. (Only the API field kept
            the name &ldquo;tenant&rdquo;; in this operation a tenant is a whole deployment.) Cells
            are vector-store chunks per collection for that owner, so a 0 under your own account
            beside a large <span className="font-mono">public</span> number is the normal shape of
            a shared corpus, not missing data.
          </>
        }
      />
      {t.isError ? (
        <ErrLine>Unavailable: {(t.error as Error).message}</ErrLine>
      ) : !data ? (
        <div className="rounded-panel border border-dashed border-line p-4 text-center text-sm text-faint">
          Loading tenancy…
        </div>
      ) : (
        <div className="space-y-3.5">
          <div className="grid grid-cols-2 gap-3">
            <KpiCard
              label="Account"
              value={data.tenant}
              sub={`role ${data.role}`}
              variant="id"
            />
            <KpiCard
              label="Readable scopes"
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
              sub={data.auth_enabled ? "per-key owner" : "everyone is `default`"}
            />
          </div>

          {!data.auth_enabled ? (
            <div className="rounded-panel bg-accent-soft px-3.5 py-2.5 text-xs leading-relaxed text-accent-text">
              Keyless mode — every caller shares the <code>default</code> owner scope
              with the server's default role. Fine for dev; production startup forbids it.
            </div>
          ) : null}

          {cols.length === 0 ? (
            <div className="rounded-panel border border-dashed border-line p-4 text-center text-sm text-faint">
              No collections reachable by this account.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-panel border border-line">
              <table className="w-full text-sm">
                <thead className={THEAD}>
                  <tr>
                    <th className={TH}>Owner</th>
                    <th className={TH}>Role</th>
                    {cols.map((c) => (
                      <th key={c.collection} className={`${TH} text-right`}>
                        {c.label}
                      </th>
                    ))}
                    <th className={`${TH} text-right`}>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.tenants.map((row) => {
                    const total = row.collections.reduce((n, c) => n + (c.vector_count ?? 0), 0);
                    return (
                      <tr key={row.tenant} className="border-t border-lineSoft">
                        <td className="px-3.5 py-2.5 text-[12.5px] font-semibold text-ink-900">
                          {row.tenant}
                        </td>
                        <td className="px-3.5 py-2.5">
                          {/* The server only reports OUR role — other readable
                              tenants get the honest "shared", not a guessed role. */}
                          <span
                            className={`rounded-[3px] px-1.5 py-1 font-mono text-[10px] font-medium ${
                              row.own
                                ? "bg-linkSoft text-link"
                                : "bg-[#f2f1ed] text-[#6a6a64]"
                            }`}
                          >
                            {row.own ? data.role : "shared"}
                          </span>
                        </td>
                        {row.collections.map((c) => (
                          <td
                            key={c.collection}
                            className="px-3.5 py-2.5 text-right font-mono text-[11px] tabular-nums text-body"
                            title={`text index: ${fmt(c.text_count)}`}
                          >
                            {fmt(c.vector_count)}
                          </td>
                        ))}
                        <td className="px-3.5 py-2.5 text-right font-mono text-[11px] font-medium tabular-nums text-strong">
                          {total.toLocaleString()}
                        </td>
                      </tr>
                    );
                  })}
                  {hiddenTenants > 0 ? (
                    <tr className="border-t border-lineSoft text-faint">
                      <td className="px-3.5 py-2.5 text-[12px]">
                        {hiddenTenants} owner{hiddenTenants === 1 ? "" : "s"} hidden
                      </td>
                      <td className="px-3.5 py-2.5 text-[11px]">—</td>
                      {cols.map((c) => (
                        <td key={c.collection} className="px-3.5 py-2.5 text-right font-mono text-[11px]">
                          —
                        </td>
                      ))}
                      <td className="px-3.5 py-2.5 text-right font-mono text-[11px]">—</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-[11.5px] leading-relaxed text-dim">
            Vector-store chunks owned by each scope (hover a cell for its text-index
            count). Rows cover only the scopes you may read — another account's corpus
            size is never shown. Reads are filtered to{" "}
            <code>{data.readable.join(" + ")}</code>, so a collection that looks empty
            for your own account may still be fully served from <code>public</code>.
            (&ldquo;Tenant&rdquo; in this operation means a whole deployment — this
            table is about who owns the data inside this one.)
          </p>

          {data.policy ? (
            <div className="rounded-panel border border-line px-3.5 py-3">
              <div className="mb-1.5 font-mono text-[10px] font-medium uppercase tracking-[.1em] text-muted">
                Access policy (admin)
              </div>
              {Object.keys(data.policy).length === 0 ? (
                <p className="text-xs text-dim">
                  <code>TENANT_COLLECTIONS</code> unset — every owner may reach every
                  collection.
                </p>
              ) : (
                <ul className="space-y-1 text-xs text-body">
                  {Object.entries(data.policy).map(([tenant, ids]) => (
                    <li key={tenant}>
                      <span className="font-medium text-strong">{tenant}</span>
                      <span className="text-faint"> → </span>
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

// Status chip palette (mockup 6a): running amber, completed green, failed rust,
// anything queued/other blue.
function jobStatusChip(status: string): string {
  if (status === "completed") return "bg-mossSoft text-[#1f6b4c]";
  if (status === "failed") return "bg-rustSoft text-rust";
  if (status === "running") return "bg-[#fff4c9] text-accent-text";
  return "bg-linkSoft text-link";
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

// Percent of tracked items settled; null when the job tracks no items.
function jobPct(j: JobSummary): number | null {
  const { pending, completed, failed } = j.items;
  const tracked = pending + completed + failed;
  return tracked > 0 ? Math.round(((completed + failed) / tracked) * 100) : null;
}

function JobsPanel({ apiKey }: { apiKey?: string }) {
  const jobs = useQuery({
    queryKey: ["jobs", apiKey],
    queryFn: () => getJobs(25, apiKey || undefined),
    refetchInterval: 5000,
    retry: false,
  });
  const gated = jobs.isError && gatedErr(jobs.error);
  const rows = jobs.data?.jobs ?? [];

  return (
    <>
      <SectionHeading
        id="jobs"
        gated={gated}
        unavailable={jobs.isError}
        meta="last 25"
        help={
          <>
            The 25 most recent ingest runs (GET /v1/jobs?limit=25), re-read every 5s — one row per
            upload/ingest request, not per document. Progress comes from per-item counters, which
            only exist once a job has enumerated its documents; a single-document run has none and
            shows its chunk count instead. A failed row puts the server&apos;s error where the
            progress bar would be. Admin-only.
          </>
        }
      >
        {jobs.isFetching && !jobs.isError ? (
          <span className="font-mono text-[10px] text-faint">refreshing…</span>
        ) : null}
      </SectionHeading>
      {jobs.isError ? (
        gated ? (
          <GatedNote>
            Ingest jobs are admin-only — start the API with DEFAULT_ROLE=admin, or enter an admin
            key.
          </GatedNote>
        ) : (
          <ErrLine>Unavailable: {(jobs.error as Error).message}</ErrLine>
        )
      ) : rows.length === 0 ? (
        <div className="rounded-panel border border-dashed border-line p-4 text-center text-sm text-faint">
          No ingest jobs yet. Run one via <code className="font-mono">POST /v1/ingest</code>.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-panel border border-line">
          <table className="w-full text-sm">
            <thead className={THEAD}>
              <tr>
                <th className={TH}>Status</th>
                <th className={TH}>Job</th>
                <th className={TH}>Source</th>
                <th className={`${TH} w-[190px]`}>Progress</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((j) => {
                const running = j.status === "running" || j.status === "accepted";
                const pct = jobPct(j);
                return (
                  <tr
                    key={j.job_id}
                    className={`border-t border-lineSoft ${running ? "bg-accent-soft" : ""}`}
                  >
                    <td className="px-3.5 py-3">
                      <span
                        className={`rounded-[3px] px-2 py-[5px] font-mono text-[10.5px] font-medium ${jobStatusChip(j.status)}`}
                      >
                        {j.status}
                      </span>
                    </td>
                    <td className="px-3.5 py-3 font-mono text-[11.5px] text-strong" title={j.job_id}>
                      {j.job_id.slice(0, 8)}
                    </td>
                    <td className="max-w-xs truncate px-3.5 py-3 text-[12px] text-body" title={j.source}>
                      {j.source || "—"}
                    </td>
                    <td className="px-3.5 py-3">
                      {j.status === "failed" && j.error ? (
                        // A failed row puts the reason where the progress was going.
                        <span className="font-mono text-[10.5px] text-rust" title={j.error}>
                          {j.error}
                        </span>
                      ) : running && pct != null ? (
                        <span className="flex items-center gap-2">
                          <span className="h-1 flex-1 rounded-sm bg-[#e9e8e4]">
                            <span
                              className="block h-1 rounded-sm bg-accent"
                              style={{ width: `${pct}%` }}
                            />
                          </span>
                          <span className="font-mono text-[10.5px] text-accent-text">{pct}%</span>
                        </span>
                      ) : (
                        <span
                          className={`font-mono text-[10.5px] tabular-nums ${
                            running ? "text-accent-text" : "text-[#6a6a64]"
                          }`}
                          title={j.error || undefined}
                        >
                          {jobProgress(j)}
                          {/* An error on a non-failed status (partial success) still surfaces. */}
                          {j.error ? <span className="text-rust"> · {j.error}</span> : null}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --- Deep health / Stores sections ----------------------------------------

// Dependency rows from the mockup's [dot, dependency, version, latency, detail]
// grid — minus the version column, which the API doesn't report (never fake).
// Degraded rows take the yellow-tint treatment instead of a red blob.
function HealthPanel({ apiKey }: { apiKey?: string }) {
  const health = useQuery({
    queryKey: ["health-deep", apiKey],
    queryFn: () => getDeepHealth(apiKey || undefined),
    refetchInterval: 15000,
    retry: false,
  });
  const gated = health.isError && gatedErr(health.error);

  return (
    <>
      <SectionHeading
        id="health"
        gated={gated}
        unavailable={health.isError}
        meta="GET /v1/health/deep"
        help={
          <>
            One probe per dependency, re-run every 15s — not a single liveness bit. A filled dot is
            a check that passed; a “!” and a tinted row is one that didn&apos;t, with whatever
            detail the server returned. The number is that one probe&apos;s round trip, so a single
            slow reading is one slow call rather than a trend. Admin-only: a non-admin key gets the
            gated note here, not a failure.
          </>
        }
      />
      {health.isError ? (
        gated ? (
          <GatedNote>
            Deep health is admin-only — start the API with DEFAULT_ROLE=admin (keyless callers
            default to &lsquo;user&rsquo;), or enter an admin key.
          </GatedNote>
        ) : (
          <ErrLine>Unavailable: {(health.error as Error).message}</ErrLine>
        )
      ) : (
        <div className="overflow-hidden rounded-panel border border-line">
          {(health.data?.checks ?? []).map((c) => (
            <div
              key={c.name}
              className={`grid grid-cols-[20px_minmax(0,1fr)_90px_minmax(0,1.2fr)] items-center gap-3 border-b border-lineSoft px-4 py-3 last:border-b-0 ${
                c.ok ? "" : "bg-accent-soft"
              }`}
            >
              {/* State is never color-alone: ok = filled dot, not-ok = a "!"
                  glyph — distinguishable without color vision, plus AT text. */}
              {c.ok ? (
                <span className="h-2 w-2 rounded-full bg-moss" role="img" aria-label="ok" />
              ) : (
                <span
                  className="font-mono text-[11px] font-bold leading-none text-amber"
                  role="img"
                  aria-label="degraded"
                >
                  !
                </span>
              )}
              <span className="truncate text-[13px] font-medium text-strong">{c.name}</span>
              <span
                className={`font-mono text-[11px] tabular-nums ${
                  c.ok ? "text-[#6a6a64]" : "font-medium text-accent-text"
                }`}
              >
                {c.latency_ms != null ? `${c.latency_ms}ms` : "—"}
              </span>
              <span
                className={`truncate font-mono text-[11px] ${
                  c.ok ? "text-dim" : "font-medium text-accent-text"
                }`}
                title={c.detail ?? undefined}
              >
                {c.detail ?? ""}
              </span>
            </div>
          ))}
          {health.isLoading ? (
            <p className="px-4 py-3 text-[12.5px] text-dim">Checking dependencies…</p>
          ) : null}
        </div>
      )}
    </>
  );
}

// The section-level view of the same store stats the band summarises: one card
// per store plus the tenant union. Shares the band's query key, so no extra
// polling.
function StoreKpi({
  label,
  swatch,
  unit,
  s,
}: {
  label: string; // role name ("Vector store") — title fallback + sub-line
  swatch: string;
  unit: string;
  s?: StoreStat;
}) {
  const disabled = s != null && !s.available && s.backend === "disabled";
  return (
    <div className={`rounded-card border border-line bg-white p-4 ${disabled ? "opacity-60" : ""}`}>
      <div className="mb-2.5 flex items-center gap-[7px]">
        <span className={`h-2 w-2 rounded-[2px] ${swatch}`} />
        {/* Titled by the concrete backend (Qdrant/Elasticsearch/Neo4j), like
            the status band's cards; the role stays in the mono sub-line. */}
        <span className="text-[11px] font-medium text-body">{storeTitle(s?.backend, label)}</span>
        <span
          className={`ml-auto font-mono text-[10px] ${
            s == null
              ? "text-faint"
              : s.available
                ? "text-[#1f6b4c]"
                : disabled
                  ? "text-faint"
                  : "text-rust"
          }`}
        >
          {s == null ? "…" : s.available ? "up" : disabled ? "disabled" : "down"}
        </span>
      </div>
      <div className="font-display text-[26px] font-extrabold leading-none text-ink-900">
        {disabled ? "—" : fmt(s?.count)}
      </div>
      <div className="mt-1.5 truncate font-mono text-[10.5px] text-dim">
        {disabled ? "backend disabled" : s ? `${unit} · ${label.toLowerCase()}` : "loading"}
      </div>
    </div>
  );
}

function StoresPanel({ apiKey }: { apiKey?: string }) {
  const stats = useQuery({
    queryKey: ["stats-stores", apiKey],
    queryFn: () => getStoreStats(apiKey || undefined),
    // 15s, not 5s: the endpoint counts every readable collection (one probe per
    // physical store), so a fast poll multiplies store load by the collection
    // count. Corpus size does not move that quickly.
    refetchInterval: 15000,
    retry: false, // fail fast on 401/403 like the sibling queries — no retry storm
  });

  return (
    <>
      <SectionHeading
        id="stores"
        unavailable={stats.isError}
        meta="GET /v1/stats/stores"
        help={
          <>
            Live counts per backing store, re-read every 5s and summed across every owner scope
            this credential may read (yours plus anything shared or public) — so a number here can
            exceed any single collection&apos;s. The text store holds one Elasticsearch document
            per chunk, so it and the vector store should track each other; a lasting gap means one
            leg is missing rows. The graph store reads “disabled” when no graph backend is
            configured, which is a choice, not an outage.
          </>
        }
      >
        {stats.isFetching && !stats.isError ? (
          <span className="font-mono text-[10px] text-faint">refreshing…</span>
        ) : null}
      </SectionHeading>
      {stats.isError ? (
        <ErrLine>Failed to load store stats: {(stats.error as Error).message}</ErrLine>
      ) : (
        <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-4">
          <StoreKpi label="Vector store" swatch="bg-accent" unit="chunks" s={stats.data?.vector} />
          <StoreKpi label="Text store" swatch="bg-sky" unit="docs" s={stats.data?.text} />
          <StoreKpi label="Graph store" swatch="bg-moss" unit="relations" s={stats.data?.graph} />
          <KpiCard
            label="Readable scopes"
            value={fmt(stats.data?.tenants.length)}
            sub={stats.data?.tenants.join(", ")}
          />
        </div>
      )}
    </>
  );
}

export function OpsDashboard({ apiKey }: { apiKey?: string }) {
  const [sectionState, setSectionState] = useState<Partial<Record<SectionId, SectionState>>>({});
  const report = useCallback((id: SectionId, st: SectionState) => {
    setSectionState((prev) => (prev[id] === st ? prev : { ...prev, [id]: st }));
  }, []);

  // Scroll-spy for the rail: the topmost heading in the upper band of the
  // viewport is the active section. Clicking a TOC item sets it directly so the
  // rail responds before the smooth scroll settles.
  const [active, setActive] = useState<SectionId>(SECTIONS[0].id);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const obs = new IntersectionObserver(
      (entries) => {
        const tops = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (tops[0]) setActive(tops[0].target.id as SectionId);
      },
      { rootMargin: "-10% 0px -70% 0px" },
    );
    for (const s of SECTIONS) {
      const el = document.getElementById(s.id);
      if (el) obs.observe(el);
    }
    return () => obs.disconnect();
  }, []);

  // App renders Ops full-bleed (no <main> padding), so the navy band runs
  // edge-to-edge flush under the header; the 1180px two-column page (mockup
  // 6a) centers itself below it.
  return (
    <section className="bg-white">
      <StatusBand apiKey={apiKey} />
      <ReportSection.Provider value={report}>
        <div className="mx-auto grid max-w-[1180px] grid-cols-1 md:grid-cols-[196px_minmax(0,1fr)]">
          <SectionToc state={sectionState} active={active} onSelect={setActive} />
          <div className="min-w-0 px-5 pb-10 pt-[26px] md:px-[30px]">
            <HealthPanel apiKey={apiKey} />
            <StoresPanel apiKey={apiKey} />
            <CollectionsPanel apiKey={apiKey} />
            <GroupsPanel apiKey={apiKey} />
            {/* Models and Data ownership sit side by side at width (mockup 6a). */}
            {/* Each column's heading is its wrapper's first child, so the
                headings' own first:mt-0 applies — the grid carries the gap. */}
            <div className="mt-9 grid grid-cols-1 gap-x-[26px] gap-y-9 xl:grid-cols-2">
              <div className="min-w-0">
                <ModelsPanel apiKey={apiKey} />
              </div>
              <div className="min-w-0">
                <TenantsPanel apiKey={apiKey} />
              </div>
            </div>
            <JobsPanel apiKey={apiKey} />
            <ConfigPanel apiKey={apiKey} />
            {/* The operator vocabulary this page uses in its own labels — drift,
                readable scopes, unregister vs purge — defined once in
                lib/glossary, as on Collection/Compare/Evidence. */}
            <GlossaryPanel
              groups={["Corpus & indexing", "Stores", "Access & sharing", "Operations"]}
              summary="drift · readable scopes · unregister vs purge · deep health"
            />
          </div>
        </div>
      </ReportSection.Provider>
    </section>
  );
}
