import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { getCollections, getConfig, queryRag, type QueryResponse } from "../api/client";
import { collectionTarget, requestCollection } from "../lib/collectionTarget";
import { newRunId, type RunRecord } from "../lib/run";
import { ConfigChips } from "./explore/ConfigChips";
import { RunRail } from "./explore/RunRail";
import { GlossaryPanel } from "./GlossaryPanel";
import {
  DEFAULT_QUERY_OPTIONS,
  queryOptionsRequest,
  type QueryOptions,
} from "./QueryOptionsMenu";
import { ResultsPanel } from "./ResultsPanel";
import { SearchForm } from "./SearchForm";
import { EmptyState } from "./states/EmptyState";

// Explore: ask the corpus, read the answer, scan sources — verification lives
// one click away in Evidence. Single /v1/query request → answer + sources
// return atomically. Layout: 660px editorial column + 300px run rail; the rail
// drops under the answer below 900px.

export interface ExploreViewProps {
  apiKey: string;
  setApiKey: (v: string) => void;
  // Shared run plumbing, owned by App: `run` is the most recent completed
  // query (this view writes it via onRun on every success; the run rail reads
  // it); the two callbacks navigate to Evidence / seed Compare with the run.
  run: RunRecord | null;
  onRun: (r: RunRecord) => void;
  // Optional 0-based source index → Evidence preselects that source (per-source
  // "Evidence →" links); omitted for whole-run entry points.
  onOpenEvidence: (sourceIndex?: number) => void;
  onSendToCompare: () => void;
}

// How many past questions the rail keeps. In-session only, like feedback.
const RECENT_MAX = 5;

export function ExploreView(props: ExploreViewProps) {
  const { apiKey, setApiKey, onRun, onOpenEvidence, onSendToCompare } = props;
  const [query, setQuery] = useState("");
  // The user's explicit pick, or null for "I didn't choose — use the default the
  // listing reports for ME". There is no "" sentinel any more: it used to mean
  // "let the server pick", and since no option ever mapped back to it for a
  // caller who couldn't read the registry default, the state was pinned there
  // with no way out (#420).
  const [selected, setSelected] = useState<string | null>(null);
  // Pipeline levers from the Options menu. Applied on the NEXT search — an
  // in-flight request keeps the options it was sent with.
  const [options, setOptions] = useState<QueryOptions>(DEFAULT_QUERY_OPTIONS);
  // Asked questions, newest first — [0] is the rail's "current" (yellow rule).
  const [recent, setRecent] = useState<string[]>([]);

  // Populate the collection chip's picker. Any authenticated caller can read
  // this; failure (e.g. 401 before a key is set) just leaves the plain chip.
  const collections = useQuery({
    queryKey: ["collections", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    retry: false,
  });
  const opts = collections.data?.collections ?? [];

  // The server's effective config, for presenting its rerank default in the
  // Options menu. /v1/config is admin-only — a 403 just leaves the menu on the
  // code default, it is not an error worth surfacing here.
  const config = useQuery({
    queryKey: ["config", apiKey],
    queryFn: () => getConfig(apiKey || undefined),
    retry: false,
  });
  const serverRerank = config.data ? config.data.rerank_enabled === true : null;

  // WHAT THE NEXT REQUEST WILL HIT — resolved ONCE, here, and handed to both the
  // mutation (as the request body's `collection`) and the chip (as its label).
  // One value, two consumers: the chip can no longer name a collection the query
  // will not target, because it no longer computes one (#420).
  const target = collectionTarget(collections.data, selected);

  // Clear a stale selection when the registry changes (apiKey/tenant switch), so
  // the <select> isn't left showing a value with no matching <option>. This is
  // display hygiene only: `collectionTarget` already ignores a selection that
  // isn't in the listing, so a stale id can never reach a request. The old
  // version mapped options through `c.default ? "" : c.id`, which for a caller
  // who couldn't read the registry default matched nothing — so it re-set "" on
  // every pass and the selection was un-clearable.
  useEffect(() => {
    if (opts.length === 0) return;
    if (selected !== null && !opts.some((c) => c.id === selected)) setSelected(null);
  }, [opts, selected]);

  // Snapshot at submit: collection/options may change while the request is in
  // flight, and the RunRecord must describe the request actually sent.
  const inFlight = useRef<{
    collection: string | null;
    options: QueryOptions;
    startedAt: number;
    t0: number;
  } | null>(null);

  const run = useMutation<QueryResponse, Error, string>({
    mutationFn: (q) =>
      queryRag(
        { query: q, collection: requestCollection(target), ...queryOptionsRequest(options) },
        apiKey || undefined,
      ),
    onMutate: () => {
      inFlight.current = {
        // The id actually SENT, not the user's pick — an unpicked request still
        // carries a concrete collection, and the run rail / Compare hand-off
        // must describe the real target.
        collection: target.id,
        options,
        startedAt: Date.now(),
        t0: performance.now(),
      };
    },
    onSuccess: (data, q) => {
      const sent = inFlight.current;
      onRun({
        id: newRunId(),
        query: q,
        collection: sent ? sent.collection : target.id,
        options: sent?.options ?? options,
        response: data,
        startedAt: sent?.startedAt,
        ms: sent ? performance.now() - sent.t0 : undefined,
      });
    },
  });

  const submit = () => {
    const q = query.trim();
    if (!q) return;
    setRecent((r) => [q, ...r.filter((x) => x !== q)].slice(0, RECENT_MAX));
    run.mutate(q);
  };

  const status = run.isPending ? "pending" : run.isError ? "error" : "success";

  return (
    <div className="mx-auto max-w-[1004px]">
      <div className="grid items-start gap-[44px] min-[900px]:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 max-w-[660px]">
          <SearchForm
            apiKey={apiKey}
            setApiKey={setApiKey}
            query={query}
            setQuery={setQuery}
            onSubmit={submit}
            pending={run.isPending}
          />

          {/* The chip row is the closed-state pipeline readout; the collection
              picker lives in its first chip, the Options popover at its end. The
              levers apply to the default collection too, so it always renders. */}
          <ConfigChips
            opts={opts}
            target={target}
            setCollection={setSelected}
            options={options}
            onOptionsChange={(patch) => setOptions((o) => ({ ...o, ...patch }))}
            serverRerank={serverRerank}
          />

          {run.isIdle ? (
            <EmptyState />
          ) : (
            <ResultsPanel
              status={status}
              query={run.variables ?? query}
              data={run.data}
              error={run.error}
              onRetry={() => run.variables && run.mutate(run.variables)}
              onOpenEvidence={onOpenEvidence}
            />
          )}
        </div>

        <RunRail
          run={props.run}
          serverRerank={serverRerank}
          recent={recent}
          onPick={setQuery}
          onOpenEvidence={onOpenEvidence}
          onSendToCompare={onSendToCompare}
        />
      </div>

      {/* The landing tab shows vocabulary it cannot define inline — the chips'
          "hybrid"/"rerank off"/"k 5" and the picker's "fixed_token/512 · ov 64" —
          so it closes with the same glossary as the other four screens. */}
      <GlossaryPanel
        groups={["Retrieval mode", "Query rewriting", "Reranking", "Chunking", "Runs & evidence"]}
        summary="hybrid · rewrite · rerank · fixed_token · run"
      />
    </div>
  );
}
