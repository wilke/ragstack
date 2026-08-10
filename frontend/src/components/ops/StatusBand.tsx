import { useQuery } from "@tanstack/react-query";
import {
  ApiError,
  getApiVersion,
  getDeepHealth,
  getJobs,
  getStoreStats,
  type JobSummary,
  type StoreStat,
} from "../../api/client";
import { BACKEND_PRESETS, getApiBase } from "../../api/config";
import {
  apiDocsUrl,
  apiUrlAbsolute,
  deploymentName,
  uiUrl,
  uiVersion,
} from "../../lib/deployment";
import { HelpTip } from "../HelpTip";

// Full-width navy status band across the top of Ops (mockup 6a): health pill,
// tenancy/freshness line in mono, a re-check control, and a 4-up grid of
// translucent cards — the three stores plus ingest jobs. All three queries use
// the same keys as the section panels below, so the band shares their cache and
// their poll instead of adding traffic.
//
// The jobs card is the one admin-gated element up here: a 403 degrades it to a
// dash with an "admin" note rather than hiding the card or showing an error.

const fmt = (n: number | null | undefined): string => (n == null ? "—" : n.toLocaleString());

// Cards are titled by the CONCRETE store (mockup: "Qdrant", not "Vector store")
// — /v1/stats/stores already names the backend, so no guessing. The role name
// is the fallback while stats load (or the backend is disabled) and stays in
// the card's mono sub-line either way.
const STORE_NAMES: Record<string, string> = {
  qdrant: "Qdrant",
  elasticsearch: "Elasticsearch",
  neo4j: "Neo4j",
};

export function storeTitle(backend: string | undefined, role: string): string {
  if (!backend || backend === "disabled") return role;
  return STORE_NAMES[backend.toLowerCase()] ?? backend.charAt(0).toUpperCase() + backend.slice(1);
}

// The selected deployment — the "tenant" in this operation's vocabulary — for
// the meta line ("tenant dev · reads …").
// Client-side truth: the preset id (or the raw base URL for a custom target) —
// the API has no name-yourself endpoint to ask.
export function deploymentLabel(): string {
  const base = getApiBase();
  return BACKEND_PRESETS.find((p) => p.url === base)?.id ?? (base || "same-origin");
}

const gatedErr = (e: unknown): boolean =>
  e instanceof ApiError && (e.status === 403 || e.status === 401);

// `graph_backend=disabled` is a configuration choice, not an outage — the card
// must say "disabled", never a lying "down".
type StoreMode = "up" | "down" | "disabled" | "unknown";
function storeMode(s: StoreStat | undefined): StoreMode {
  if (!s) return "unknown";
  if (s.available) return "up";
  return s.backend === "disabled" ? "disabled" : "down";
}

function StoreCard({
  swatch,
  role,
  unit,
  term,
  s,
}: {
  swatch: string;
  role: string; // role name ("Vector store") — title fallback + sub-line
  unit: string;
  term: string; // glossary key: what this store holds and what its count counts
  s?: StoreStat;
}) {
  const mode = storeMode(s);
  return (
    <div className={`rounded-card bg-white/[.06] px-[18px] py-4 ${mode === "disabled" ? "opacity-70" : ""}`}>
      <div className="mb-3 flex items-center gap-[7px]">
        <span className={`h-2 w-2 rounded-[2px] ${swatch}`} />
        <span className="text-[11px] font-medium text-[#c7d8e8]">{storeTitle(s?.backend, role)}</span>
        <HelpTip icon dark side="bottom" term={term} />
        <span
          className={`ml-auto font-mono text-[10px] ${
            mode === "up" ? "text-[#7bd6a2]" : mode === "down" ? "text-[#ff9b76]" : "text-[#5d84ad]"
          }`}
        >
          {mode === "unknown" ? "…" : mode}
        </span>
      </div>
      <div className="mb-[5px] font-display text-[26px] font-extrabold leading-none text-white">
        {mode === "disabled" ? "—" : fmt(s?.count)}
      </div>
      <div className="truncate font-mono text-[10.5px] leading-none text-[#5d84ad]">
        {mode === "disabled" ? "backend disabled" : s ? `${unit} · ${role.toLowerCase()}` : "waiting for stats"}
      </div>
    </div>
  );
}

// Percent complete from the per-item counters; null when the job tracks no
// items (single-doc runs), so the card falls back to a count.
function jobPct(j: JobSummary): number | null {
  const { pending, completed, failed } = j.items;
  const tracked = pending + completed + failed;
  return tracked > 0 ? Math.round(((completed + failed) / tracked) * 100) : null;
}

export function StatusBand({ apiKey }: { apiKey?: string }) {
  const stats = useQuery({
    queryKey: ["stats-stores", apiKey],
    queryFn: () => getStoreStats(apiKey || undefined),
    // Matches StoresPanel (shared key): the count now spans every readable
    // collection, so it is a heavier probe than the old default-store-only one.
    refetchInterval: 15000,
    retry: false,
  });
  const health = useQuery({
    queryKey: ["health-deep", apiKey],
    queryFn: () => getDeepHealth(apiKey || undefined),
    refetchInterval: 15000,
    retry: false,
  });
  const jobs = useQuery({
    queryKey: ["jobs", apiKey],
    queryFn: () => getJobs(25, apiKey || undefined),
    refetchInterval: 5000,
    retry: false,
  });

  // Health pill: deep health when readable; otherwise (admin-gated) the honest
  // fallback is the store probes — never a green light nothing confirmed.
  const checks = health.data?.checks ?? [];
  const failing = checks.filter((c) => !c.ok);
  let pill: { ok: boolean; label: string; title?: string } | null = null;
  if (health.data) {
    pill =
      failing.length === 0
        ? { ok: true, label: "All dependencies healthy" }
        : {
            ok: false,
            label: `${failing.length} of ${checks.length} dependencies degraded`,
            title: failing.map((c) => c.name).join(", "),
          };
  } else if (stats.data) {
    const down = [stats.data.vector, stats.data.text].filter((s) => !s.available).length;
    pill =
      down === 0
        ? {
            ok: true,
            label: gatedErr(health.error) ? "Stores up · deep health needs admin" : "Stores up",
          }
        : { ok: false, label: `${down} store${down === 1 ? "" : "s"} down` };
  }

  const updated = Math.max(stats.dataUpdatedAt || 0, health.dataUpdatedAt || 0);
  const ago = updated ? Math.max(0, Math.round((Date.now() - updated) / 1000)) : null;
  // The deployment's own identity line lives below the pill (tenant name, its
  // URLs, versions), so this one carries only what is being reported and when.
  const meta = [
    stats.data ? `reads ${stats.data.tenants.join(" + ")}` : "",
    ago != null ? `checked ${ago}s ago` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  // "tenant" names the DEPLOYMENT (dev/lucid/asm). The gateway path is the only
  // place it is written down — no endpoint names itself — so a UI served at "/"
  // honestly has no name and falls back to naming the backend it addresses.
  const name = deploymentName();
  const apiPath = getApiBase() || "same-origin";

  // The API's version, from the OpenAPI doc (the only place it exists). Fetched
  // once per backend and never refetched — it changes on redeploy, not on a
  // poll — and keyed on the base so switching deployments re-reads it.
  const version = useQuery({
    queryKey: ["api-version", getApiBase()],
    queryFn: getApiVersion,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  });

  const checking = stats.isFetching || health.isFetching || jobs.isFetching;
  const recheck = () => {
    void stats.refetch();
    void health.refetch();
    void jobs.refetch();
  };

  // Jobs card state. "accepted" counts as running — work is queued either way.
  const jobsGated = jobs.isError && gatedErr(jobs.error);
  const jobRows = jobs.data?.jobs ?? [];
  const running = jobRows.filter((j) => j.status === "running" || j.status === "accepted");
  const lead = running[0];
  const pct = lead ? jobPct(lead) : null;

  return (
    <div className="bg-ink-900 px-5 py-6 md:px-[34px]">
      <div className="mx-auto max-w-[1112px]">
        <div className="mb-5 flex flex-wrap items-center gap-3.5">
          {pill ? (
            <span
              title={pill.title}
              className={`flex items-center gap-2 rounded-chip border px-3.5 py-[7px] text-xs font-semibold ${
                pill.ok
                  ? "border-[rgba(126,214,162,.4)] bg-[rgba(126,214,162,.16)] text-[#7bd6a2]"
                  : "border-accent/40 bg-accent/10 text-accent"
              }`}
            >
              <span
                className={`h-[7px] w-[7px] rounded-full ${pill.ok ? "bg-[#7bd6a2]" : "bg-accent"}`}
              />
              {pill.label}
            </span>
          ) : (
            <span className="flex items-center gap-2 rounded-chip border border-white/15 bg-white/5 px-3.5 py-[7px] text-xs font-semibold text-[#7fa4c6]">
              <span className="h-[7px] w-[7px] rounded-full bg-[#7fa4c6]" />
              {stats.isError ? "status unavailable" : "checking…"}
            </span>
          )}
          {meta ? <span className="font-mono text-[11.5px] text-[#7fa4c6]">{meta}</span> : null}
          {/* The tip sits BESIDE the control, never inside it — a button in a
              button is invalid markup and steals the click. */}
          <span className="ml-auto flex items-center gap-2">
            <HelpTip icon dark side="bottom" term="re-check" />
            <button
              type="button"
              onClick={recheck}
              disabled={checking}
              className="rounded-chip border border-white/25 px-[15px] py-2 text-xs font-medium text-[#c7d8e8] hover:bg-white/10 disabled:opacity-60"
            >
              {checking ? "checking…" : "Re-check ↻"}
            </button>
          </span>
        </div>

        {/* Which deployment this is, where it lives, and what is running here —
            the facts you need when several stacks look alike in a screenshot. */}
        <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11px] text-[#8fb3d4]">
          <span className="flex items-center gap-2">
            <span className="text-[#5d84ad]">tenant</span>
            <span className="font-sans text-[13px] font-semibold text-white">
              {name ?? deploymentLabel()}
            </span>
            <HelpTip icon dark side="bottom" term="deployment" />
          </span>
          <a
            href={uiUrl()}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-[3px] text-[#c7d8e8] underline decoration-white/30 underline-offset-2 hover:decoration-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            UI ↗ <span className="text-[#8fb3d4]">{import.meta.env.BASE_URL}</span>
          </a>
          <a
            href={apiDocsUrl()}
            target="_blank"
            rel="noopener noreferrer"
            title={apiUrlAbsolute()}
            className="rounded-[3px] text-[#c7d8e8] underline decoration-white/30 underline-offset-2 hover:decoration-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            API docs ↗ <span className="text-[#8fb3d4]">{apiPath}</span>
          </a>
          {/* Two independently deployed halves: a mismatched pair is worth
              seeing, so neither number stands in for the other. */}
          <span>
            ui {uiVersion} · api{" "}
            {version.isPending ? "…" : (version.data ?? "unknown")}
          </span>
        </div>

        {stats.isError && !gatedErr(stats.error) ? (
          <p className="mb-3.5 font-mono text-[11.5px] text-[#ff9b76]">
            store stats unavailable: {(stats.error as Error).message}
          </p>
        ) : null}

        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
          <StoreCard
            swatch="bg-accent"
            role="Vector store"
            unit="chunks"
            term="vector store"
            s={stats.data?.vector}
          />
          <StoreCard
            swatch="bg-sky"
            role="Text store"
            unit="docs"
            term="text index (BM25)"
            s={stats.data?.text}
          />
          <StoreCard
            swatch="bg-moss"
            role="Graph store"
            unit="relations"
            term="graph store"
            s={stats.data?.graph}
          />

          <div
            className={`rounded-card px-[18px] py-4 ${
              running.length
                ? "border border-accent/40 bg-accent/10"
                : "bg-white/[.06]"
            } ${jobsGated ? "opacity-70" : ""}`}
          >
            <div className="mb-3 flex items-center gap-[7px]">
              <span className="h-2 w-2 rounded-[2px] bg-accent" />
              <span
                className={`text-[11px] font-medium ${running.length ? "text-accent" : "text-[#c7d8e8]"}`}
              >
                Ingest jobs
              </span>
              <HelpTip icon dark side="bottom" term="ingest job">
                The last 25 ingest runs on this server. While one is running the big number is that
                job&apos;s percent complete — settled items over tracked items — and the line below
                names its source; with nothing running it is how many recent jobs came back. A job
                that tracks no items (a single-document run) shows the running count instead.
                Admin-only, so without an admin key the card dims to “admin” and a dash.
              </HelpTip>
              <span
                className={`ml-auto font-mono text-[10px] ${
                  running.length ? "text-accent" : "text-[#5d84ad]"
                }`}
              >
                {jobsGated
                  ? "admin"
                  : jobs.isError
                    ? "unavailable"
                    : running.length
                      ? `${running.length} running`
                      : jobs.data
                        ? "idle"
                        : "…"}
              </span>
            </div>
            <div className="mb-[5px] font-display text-[26px] font-extrabold leading-none text-white">
              {jobs.isError || !jobs.data
                ? "—"
                : running.length
                  ? pct != null
                    ? `${pct}%`
                    : String(running.length)
                  : String(jobRows.length)}
            </div>
            <div
              className={`truncate font-mono text-[10.5px] leading-none ${
                running.length ? "text-[#e3d07a]" : "text-[#5d84ad]"
              }`}
            >
              {jobsGated
                ? "needs an admin key"
                : jobs.isError
                  ? "jobs unavailable"
                  : running.length
                    ? lead.source || lead.job_id.slice(0, 8)
                    : jobRows.length
                      ? "recent jobs · none running"
                      : "no jobs yet"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
