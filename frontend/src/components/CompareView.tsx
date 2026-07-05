import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  getCollections,
  queryRag,
  type CollectionInfo,
  type QueryResponse,
  type Source,
} from "../api/client";

// Compare module: run ONE query across several lanes — each a (collection,
// optional API key) pair — and lay the answers out side by side so retrieval
// strategies (chunkers, embedding models) or tenants can be A/B'd and ranked.
// The collection axis is server-supported via the `collection` param; the tenant
// axis is a per-lane API-key override (tenant is server-derived from the key).
// Backend needs nothing new — it's N independent /v1/query calls.

type LaneResult = {
  status: "pending" | "success" | "error";
  data?: QueryResponse;
  error?: string;
  ms?: number;
};

type Mode = "hybrid" | "vector" | "bm25";
type Rerank = "default" | "on" | "off"; // → server default | force on | force off
type Rewrite = "none" | "multiquery" | "hyde";

interface Lane {
  key: string;
  collection: string; // "" → default collection
  apiKey: string; // "" → inherit the shared key (same tenant)
  // Per-lane pipeline levers — each maps to a /v1/query field so a single
  // question can be compared across retrieval strategies, not just corpora.
  mode: Mode; // retrieval_mode
  rerank: Rerank; // rerank: null | true | false
  useGraph: boolean; // use_graph
  rewrite: Rewrite; // rewrite_strategies
  topK: number | null; // per-lane top_k override; null → inherit the global
}

let _seq = 0;
const newLane = (collection = "", apiKey = ""): Lane => ({
  key: `lane-${_seq++}`,
  collection,
  apiKey,
  mode: "hybrid",
  rerank: "default",
  useGraph: true,
  rewrite: "none",
  topK: null,
});

const MAX_LANES = 6;

// The non-default levers of a lane, as short chips — so two lanes on the same
// collection but different pipelines are distinguishable in the header/leaderboard.
const laneTags = (l: Lane): string[] => {
  const t: string[] = [];
  if (l.mode !== "hybrid") t.push(l.mode);
  if (l.rewrite !== "none") t.push(l.rewrite);
  if (l.rerank !== "default") t.push(`rerank:${l.rerank}`);
  if (!l.useGraph) t.push("no-graph");
  if (l.topK != null) t.push(`k=${l.topK}`);
  return t;
};

const rewriteStrategies = (r: Rewrite): string[] =>
  r === "none" ? ["passthrough"] : ["passthrough", r];

const rerankValue = (r: Rerank): boolean | null =>
  r === "default" ? null : r === "on";

function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="flex items-center gap-0.5" role="radiogroup" aria-label="rating">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          aria-label={`${n} star${n > 1 ? "s" : ""}`}
          aria-checked={value === n}
          onClick={() => onChange(value === n ? 0 : n)}
          className={`text-lg leading-none ${n <= value ? "text-amber-500" : "text-gray-300 hover:text-amber-300"}`}
        >
          ★
        </button>
      ))}
    </div>
  );
}

function CompareSource({ rank, source }: { rank: number; source: Source }) {
  const title = (source.metadata.title && String(source.metadata.title)) || source.doc_id;
  return (
    <li className="border-t border-gray-100 py-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-xs font-medium text-gray-700" title={title}>
          <span className="text-gray-400">{rank}.</span> {title}
        </span>
        <span className="shrink-0 tabular-nums text-[11px] text-gray-400">
          {source.score.toFixed(4)}
        </span>
      </div>
      <p className="mt-0.5 line-clamp-2 text-xs text-gray-500">{source.content}</p>
    </li>
  );
}

export function CompareView({
  apiKey,
  setApiKey,
}: {
  apiKey: string;
  setApiKey: (v: string) => void;
}) {
  const collections = useQuery({
    queryKey: ["collections", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    retry: false,
  });
  const opts: CollectionInfo[] = collections.data?.collections ?? [];

  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [results, setResults] = useState<Record<string, LaneResult>>({});
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [ran, setRan] = useState(false);

  // Seed one lane per collection once the registry loads.
  useEffect(() => {
    if (lanes.length === 0 && opts.length > 0) {
      setLanes(opts.slice(0, MAX_LANES).map((c) => newLane(c.default ? "" : c.id)));
    }
  }, [opts, lanes.length]);

  // Reconcile lanes when the registry changes (apiKey/tenant switch): a lane
  // pointing at a collection no longer offered would submit a phantom id (backend
  // 404) and render a <select> with no matching <option> — reset it to default.
  useEffect(() => {
    if (opts.length === 0) return;
    const valid = new Set(opts.map((c) => (c.default ? "" : c.id)));
    setLanes((ls) =>
      ls.every((l) => valid.has(l.collection))
        ? ls
        : ls.map((l) => (valid.has(l.collection) ? l : { ...l, collection: "" })),
    );
  }, [opts]);

  const collOf = (collection: string): CollectionInfo | undefined =>
    opts.find((o) => (o.default ? "" : o.id) === collection);
  const collLabel = (collection: string): string =>
    collOf(collection)?.label ?? (collection || "default");

  const run = () => {
    const q = query.trim();
    if (!q || lanes.length === 0) return;
    setRan(true);
    setResults(Object.fromEntries(lanes.map((l) => [l.key, { status: "pending" as const }])));
    for (const lane of lanes) {
      const t0 = performance.now();
      queryRag(
        {
          query: q,
          top_k: lane.topK ?? topK,
          collection: lane.collection || undefined,
          retrieval_mode: lane.mode,
          rerank: rerankValue(lane.rerank),
          use_graph: lane.useGraph,
          rewrite_strategies: rewriteStrategies(lane.rewrite),
        },
        lane.apiKey || apiKey || undefined,
      )
        .then((data) =>
          setResults((r) => ({
            ...r,
            [lane.key]: { status: "success", data, ms: performance.now() - t0 },
          })),
        )
        .catch((e: Error) =>
          setResults((r) => ({
            ...r,
            [lane.key]: { status: "error", error: e.message, ms: performance.now() - t0 },
          })),
        );
    }
  };

  const setLane = (key: string, patch: Partial<Lane>) =>
    setLanes((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  // Clear a lane's prior answer + rating — a stored result is attributed to the
  // exact pipeline that produced it, so any lever change must invalidate it.
  const resetLane = (key: string) => {
    setResults((r) => {
      const n = { ...r };
      delete n[key];
      return n;
    });
    setRatings((r) => {
      const n = { ...r };
      delete n[key];
      return n;
    });
  };
  const tuneLane = (key: string, patch: Partial<Lane>) => {
    setLane(key, patch);
    resetLane(key);
  };
  const removeLane = (key: string) => setLanes((ls) => ls.filter((l) => l.key !== key));
  const addLane = () => setLanes((ls) => (ls.length < MAX_LANES ? [...ls, newLane()] : ls));

  // Leaderboard: lanes with a rating, best first.
  const ranked = lanes
    .filter((l) => (ratings[l.key] ?? 0) > 0)
    .sort((a, b) => (ratings[b.key] ?? 0) - (ratings[a.key] ?? 0));
  const topKey = ranked[0]?.key;

  return (
    <div>
      {/* Toolbar */}
      <div className="mb-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="Ask one question, compare across collections…"
            className="min-w-64 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
          <label className="flex items-center gap-1 text-xs text-gray-500">
            top_k
            <input
              type="number"
              min={1}
              max={20}
              value={topK}
              onChange={(e) => setTopK(Math.max(1, Math.min(20, Number(e.target.value) || 5)))}
              className="w-14 rounded-md border border-gray-300 px-2 py-1 text-sm tabular-nums"
            />
          </label>
          <button
            type="button"
            onClick={run}
            disabled={!query.trim() || lanes.length === 0}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-40"
          >
            Run {lanes.length}
          </button>
          <button
            type="button"
            onClick={addLane}
            disabled={lanes.length >= MAX_LANES}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40"
          >
            + Lane
          </button>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="API key (optional)"
            className="w-40 rounded-md border border-gray-300 px-2 py-1 text-xs"
          />
        </div>

        {/* Leaderboard */}
        {ranked.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-gray-500">Ranking:</span>
            {ranked.map((l, i) => (
              <span
                key={l.key}
                className={`rounded-full px-2 py-0.5 ${i === 0 ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-600"}`}
              >
                {i === 0 ? "🥇 " : `${i + 1}. `}
                {collLabel(l.collection)}
                {laneTags(l).length ? ` · ${laneTags(l).join(" ")}` : ""} · {ratings[l.key]}★
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {/* Lanes */}
      <div className="flex gap-4 overflow-x-auto pb-4">
        {lanes.map((lane) => {
          const res = results[lane.key];
          const isTop = lane.key === topKey;
          return (
            <div
              key={lane.key}
              className={`flex w-80 shrink-0 flex-col rounded-lg border bg-white ${isTop ? "border-amber-300 ring-1 ring-amber-200" : "border-gray-200"}`}
            >
              {/* Lane header */}
              <div className="space-y-2 border-b border-gray-100 p-3">
                <div className="flex items-center gap-2">
                  <select
                    value={lane.collection}
                    onChange={(e) => tuneLane(lane.key, { collection: e.target.value })}
                    className="min-w-0 flex-1 rounded-md border border-gray-300 px-2 py-1 text-sm"
                  >
                    {opts.map((c) => (
                      <option key={c.id} value={c.default ? "" : c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    aria-label="remove lane"
                    onClick={() => removeLane(lane.key)}
                    className="shrink-0 text-gray-400 hover:text-red-500"
                  >
                    ✕
                  </button>
                </div>
                {(() => {
                  const c = collOf(lane.collection);
                  const p = c?.provenance;
                  const method = p?.chunk_method ?? c?.chunk_method;
                  return c ? (
                    <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
                      <span>{c.model.split("/").pop()} · {c.dim}d</span>
                      {method ? (
                        <span>· {method}{p?.chunk_size ? "/" + p.chunk_size : ""}</span>
                      ) : null}
                      {p ? (
                        <span className={p.source === "ingest" ? "text-green-600" : "text-gray-400"}>
                          · {p.source === "ingest" ? "verified" : "config"}
                        </span>
                      ) : null}
                    </div>
                  ) : null;
                })()}

                {/* Per-lane pipeline levers — each maps to a /v1/query field. */}
                <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px] text-gray-500">
                  <label className="flex items-center gap-1">
                    <span className="w-9 shrink-0 text-gray-400">mode</span>
                    <select
                      value={lane.mode}
                      onChange={(e) => tuneLane(lane.key, { mode: e.target.value as Mode })}
                      className="min-w-0 flex-1 rounded border border-gray-200 px-1 py-0.5"
                    >
                      <option value="hybrid">hybrid</option>
                      <option value="vector">vector</option>
                      <option value="bm25">bm25</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-1">
                    <span className="w-12 shrink-0 text-gray-400">rewrite</span>
                    <select
                      value={lane.rewrite}
                      onChange={(e) => tuneLane(lane.key, { rewrite: e.target.value as Rewrite })}
                      className="min-w-0 flex-1 rounded border border-gray-200 px-1 py-0.5"
                    >
                      <option value="none">none</option>
                      <option value="multiquery">multiquery</option>
                      <option value="hyde">hyde</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-1">
                    <span className="w-9 shrink-0 text-gray-400">rerank</span>
                    <select
                      value={lane.rerank}
                      onChange={(e) => tuneLane(lane.key, { rerank: e.target.value as Rerank })}
                      className="min-w-0 flex-1 rounded border border-gray-200 px-1 py-0.5"
                    >
                      <option value="default">default</option>
                      <option value="on">on</option>
                      <option value="off">off</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-1">
                    <span className="w-12 shrink-0 text-gray-400">top_k</span>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={lane.topK ?? ""}
                      placeholder={String(topK)}
                      onChange={(e) => {
                        const v = e.target.value.trim();
                        tuneLane(lane.key, {
                          topK: v === "" ? null : Math.max(1, Math.min(20, Number(v) || topK)),
                        });
                      }}
                      className="min-w-0 flex-1 rounded border border-gray-200 px-1 py-0.5 tabular-nums"
                    />
                  </label>
                  <label className="col-span-2 flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      checked={lane.useGraph}
                      onChange={(e) => tuneLane(lane.key, { useGraph: e.target.checked })}
                    />
                    <span className="text-gray-400">use knowledge graph</span>
                  </label>
                </div>

                <input
                  type="password"
                  value={lane.apiKey}
                  onChange={(e) => setLane(lane.key, { apiKey: e.target.value })}
                  placeholder="lane API key → compare a tenant (optional)"
                  className="w-full rounded-md border border-gray-200 px-2 py-1 text-xs"
                />
                <div className="flex items-center justify-between">
                  <Stars
                    value={ratings[lane.key] ?? 0}
                    onChange={(v) => setRatings((r) => ({ ...r, [lane.key]: v }))}
                  />
                  {res?.ms != null ? (
                    <span className="tabular-nums text-[11px] text-gray-400">
                      {res.ms.toFixed(0)} ms
                      {res.data?.sources?.length
                        ? ` · top ${res.data.sources[0].score.toFixed(4)}`
                        : ""}
                    </span>
                  ) : null}
                </div>
              </div>

              {/* Lane body */}
              <div className="flex-1 space-y-3 p-3">
                {!ran ? (
                  <p className="text-xs text-gray-400">Run a query to compare.</p>
                ) : res?.status === "pending" ? (
                  <p className="animate-pulse text-xs text-gray-400">querying…</p>
                ) : res?.status === "error" ? (
                  <p className="text-xs text-red-600">Error: {res.error}</p>
                ) : res?.data ? (
                  <>
                    <p className="whitespace-pre-wrap rounded bg-gray-50 p-2 text-sm text-gray-800">
                      {res.data.answer}
                    </p>
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                        Sources ({res.data.sources.length})
                      </div>
                      <ul>
                        {res.data.sources.map((s, i) => (
                          <CompareSource key={s.chunk_id} rank={i + 1} source={s} />
                        ))}
                      </ul>
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          );
        })}

        {lanes.length === 0 ? (
          <p className="text-sm text-gray-400">
            No collections available. Configure the registry to compare.
          </p>
        ) : null}
      </div>
    </div>
  );
}
