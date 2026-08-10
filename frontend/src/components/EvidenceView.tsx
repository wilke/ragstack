// Evidence tab (mockup 5b) — one answer taken apart on the app's dark screen:
// run bar (with a selector over saved runs), pipeline strip, the answer claim
// by claim + KG entities (left), the selected source in situ + the retrieved
// set (right). App paints the dark chrome (bg-ink-700 page + dark header)
// whenever this view is active.
//
// Backend gaps honoured (handoff README): claims render UNGRADED (no per-claim
// grounding) and the whole passage is framed as the match (no chunk-relative
// offsets). The KG section uses the real /v1/graph endpoints: entities the
// answer text actually mentions, with their depth-1 triples — hidden entirely
// when the graph is disabled or nothing matches. Nothing here invents a number
// the API did not return.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { getGraphEntities, getGraphNeighbors } from "../api/client";
import { KEY_SCOPE } from "../api/config";
import { splitClaims } from "../lib/claims";
import { formatCitation } from "../lib/citation";
import type { RunRecord } from "../lib/run";
import { ClaimBlock } from "./evidence/ClaimBlock";
import { PipelineStrip } from "./evidence/PipelineStrip";
import { SourceViewer } from "./evidence/SourceViewer";
import { GlossaryPanel } from "./GlossaryPanel";
import { HelpTip } from "./HelpTip";

export interface EvidenceViewProps {
  run: RunRecord | null; // most recent Explore run; null until one completes
  apiKey: string; // forwarded per request for chunk fetches + graph lookups
  // 0-based source to preselect in the viewer (a per-source "Evidence →" link);
  // null/omitted → the first cited source.
  initialSourceIndex?: number | null;
  // App seeds Compare and navigates; the query names WHICH run to seed from,
  // because the run on display here may be a saved one, not App's live run.
  onSendToCompare: (query?: string) => void;
}

// Saved runs live in localStorage (they are meant to survive a reload, unlike
// the session-only feedback log), newest first, capped so an enthusiastic
// session can't grow the key unboundedly. Best-effort: storage disabled/full
// returns false and the button says so instead of throwing.
// Scoped to the served path like every other stored key (api/config.ts
// KEY_SCOPE): the gateway serves every deployment from ONE origin at
// /ragstack/<name>/ui/, so an unscoped key would list asm's saved runs in
// lucid's run selector — rendering another deployment's answer and firing
// its chunk ids at this one's API.
const SAVED_KEY = `ragstack.${KEY_SCOPE}evidence.savedRuns`;
const SAVED_CAP = 20;

function readSavedRuns(): RunRecord[] {
  try {
    const raw = localStorage.getItem(SAVED_KEY);
    const arr = raw ? (JSON.parse(raw) as RunRecord[]) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function saveRun(run: RunRecord): boolean {
  try {
    const next = [run, ...readSavedRuns().filter((r) => r.id !== run.id)].slice(0, SAVED_CAP);
    localStorage.setItem(SAVED_KEY, JSON.stringify(next));
    return true;
  } catch {
    return false;
  }
}

// KG section limits: how many mentioned entities to expand and how many triples
// to show per entity — a readout, not a graph browser.
const MAX_ENTITIES = 3;
const MAX_TRIPLES = 6;

// Markdown report of the run — query, config as sent, answer, sources with
// scores. Only fields the API actually returned; absent metadata is omitted.
function runReport(run: RunRecord): string {
  const o = run.options;
  const lines = [
    `# RAGStack run ${run.id}`,
    "",
    run.startedAt != null ? `- date: ${new Date(run.startedAt).toISOString()}` : null,
    `- collection: ${run.collection || "default"}`,
    `- mode: ${o.mode}`,
    `- rewrite: ${o.rewrite}`,
    `- rerank: ${o.rerank ?? "server default"}`,
    `- top_k: ${o.topK}`,
    run.ms != null ? `- elapsed: ${(run.ms / 1000).toFixed(2)}s` : null,
    "",
    "## Query",
    "",
    run.query,
    "",
    "## Answer",
    "",
    run.response.answer,
    "",
    `## Sources (${run.response.sources.length})`,
  ].filter((l): l is string => l != null);
  run.response.sources.forEach((s, i) => {
    const title = (s.metadata.title && String(s.metadata.title)) || s.doc_id;
    lines.push(
      "",
      `### ${i + 1}. ${title} — score ${s.score.toFixed(2)}`,
      "",
      `- citation: ${formatCitation(s.metadata, title)}`,
      `- doc_id: ${s.doc_id} · chunk_id: ${s.chunk_id}`,
      "",
      ...s.content.split("\n").map((l) => `> ${l}`),
    );
  });
  return lines.join("\n") + "\n";
}

function downloadReport(run: RunRecord) {
  const blob = new Blob([runReport(run)], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `ragstack-run-${run.id}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

// Dark-ground text floor: dimmest tier #8fb3d4 (≥6.2:1 on ink-500/700/800),
// secondary #9dbdda, body #a9c1d6 — dimmed stays dimmer, all readable.
const eyebrow = "font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[#8fb3d4]";

export function EvidenceView(props: EvidenceViewProps) {
  const { run, apiKey, initialSourceIndex, onSendToCompare } = props;

  // The run selector's choices: the live Explore run first, then the saved
  // runs — so Evidence is also enterable "from a saved run" with no live one.
  const [savedRuns, setSavedRuns] = useState<RunRecord[]>(readSavedRuns);
  const [pickedRunId, setPickedRunId] = useState<string | null>(null); // null = the live run
  useEffect(() => setPickedRunId(null), [run?.id]);
  const runChoices = useMemo(
    () => (run ? [run, ...savedRuns.filter((r) => r.id !== run.id)] : savedRuns),
    [run, savedRuns],
  );
  const active =
    (pickedRunId != null ? runChoices.find((r) => r.id === pickedRunId) : undefined) ??
    run ??
    runChoices[0] ??
    null;

  const claims = useMemo(
    () => (active ? splitClaims(active.response.answer, active.response.sources.length) : []),
    [active],
  );
  const cited = useMemo(() => new Set(claims.flatMap((c) => c.cited)), [claims]);
  const firstCited = useMemo(() => (cited.size ? Math.min(...cited) : 0), [cited]);

  // null = "no explicit choice" → the seeded index (a per-source Evidence link)
  // or the first cited source. Reset when the displayed run changes so a stale
  // index can't outlive the source list it pointed into.
  const [picked, setPicked] = useState<number | null>(initialSourceIndex ?? null);
  useEffect(() => setPicked(initialSourceIndex ?? null), [active?.id, initialSourceIndex]);

  // KG entities — real /v1/graph endpoints (they are tenant-scoped and take no
  // collection). "In this answer" is a CLIENT-side text match: only entities
  // whose name the answer actually mentions are expanded. A failing query
  // (graph disabled, 4xx/503) just hides the section.
  const answerText = active?.response.answer ?? "";
  const entities = useQuery({
    queryKey: ["graph-entities", apiKey],
    queryFn: () => getGraphEntities(200, apiKey || undefined),
    retry: false,
    enabled: answerText !== "",
  });
  const mentioned = useMemo(() => {
    const hay = answerText.toLowerCase();
    return (entities.data ?? [])
      .filter((e) => e.name.length >= 3 && hay.includes(e.name.toLowerCase()))
      .slice(0, MAX_ENTITIES)
      .map((e) => e.name);
  }, [entities.data, answerText]);
  const neighbors = useQuery({
    queryKey: ["graph-neighbors", apiKey, mentioned],
    queryFn: () =>
      Promise.all(
        mentioned.map(async (name) => ({
          name,
          triples: (await getGraphNeighbors(name, 1, apiKey || undefined)).slice(0, MAX_TRIPLES),
        })),
      ),
    retry: false,
    enabled: mentioned.length > 0,
  });
  const entityGroups = (neighbors.data ?? []).filter((g) => g.triples.length > 0);

  // "Saved ✓" feedback; timer cancelled on unmount.
  const [saved, setSaved] = useState("");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (!active) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-2 text-center">
        <p className="font-display text-lg font-semibold text-white">No run to verify yet</p>
        <p className="text-sm text-[#9dbdda]">Run a query in Explore, then open it here.</p>
      </div>
    );
  }

  const sources = active.response.sources;
  const selected = Math.min(picked ?? firstCited, Math.max(sources.length - 1, 0));

  const onSave = () => {
    const ok = saveRun(active);
    if (ok) setSavedRuns(readSavedRuns());
    setSaved(ok ? "Saved ✓" : "Storage unavailable");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSaved(""), 2000);
  };

  return (
    <div className="mx-auto max-w-[1180px]">
      {/* Run bar — the run's identity, the saved-run selector, one export action. */}
      <div className="flex items-center gap-3.5">
        <div className="flex min-w-0 flex-1 items-center gap-2.5 rounded-panel border border-white/[0.12] bg-white/[0.06] px-4 py-[13px]">
          <span className="font-mono text-[10px] font-medium tracking-[0.1em] text-accent">
            RUN
          </span>
          <span className="min-w-0 flex-1 truncate text-[14.5px] font-medium leading-[1.4] text-[#eaf1f8]">
            {active.query}
          </span>
          <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
            <span className="relative flex items-center">
              <select
                value={active.id}
                onChange={(e) => setPickedRunId(e.target.value)}
                aria-label="Run"
                className="max-w-[300px] cursor-pointer appearance-none truncate rounded-[4px] border border-white/20 bg-white/5 py-1 pl-2.5 pr-6 font-mono text-[11px] text-[#c7d8e8] [color-scheme:dark] hover:bg-white/10"
              >
                {runChoices.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.id} · {r.collection || "default"} · {r.options.mode} · k{r.options.topK}
                  </option>
                ))}
              </select>
              <span
                aria-hidden="true"
                className="pointer-events-none absolute right-2 font-mono text-[9px] text-[#8fb3d4]"
              >
                ▾
              </span>
            </span>
            <HelpTip icon dark side="bottom" term="run selector" />
          </span>
        </div>
        <button
          type="button"
          onClick={() => downloadReport(active)}
          className="flex shrink-0 items-center gap-2 rounded-[20px] bg-accent px-5 py-3 text-[13px] font-semibold text-ink-600 hover:brightness-95"
        >
          Export report <span aria-hidden="true" className="text-sm">→</span>
        </button>
        <HelpTip icon dark side="left" term="Export report">
          Builds a Markdown file in your browser and downloads it: the question, the collection and
          levers as sent, the elapsed time, the answer, and every source with its score, citation
          and text. Nothing is uploaded.
        </HelpTip>
      </div>

      <div className="mt-4">
        <PipelineStrip options={active.options} kept={sources.length} ms={active.ms} />
      </div>

      <div className="mt-5 grid grid-cols-1 border-t border-white/[0.09] min-[900px]:grid-cols-2">
        {/* Left — the answer, claim by claim, then the KG entities it mentions. */}
        <section className="px-[26px] py-6 min-[900px]:border-r min-[900px]:border-white/[0.09]">
          <div className={`${eyebrow} mb-1.5 flex items-center gap-1.5`}>
            Answer · claim by claim
            <HelpTip icon dark side="bottom" term="claim">
              One block per sentence of the answer. A src chip is a source that sentence&rsquo;s
              [n] marker points at — click it to open that source; the number beside it is that
              source&rsquo;s retrieval score, not a measure of how well it supports the sentence.
            </HelpTip>
          </div>
          {/* Say WHY there are no grades, so their absence reads as a gap, not a bug. */}
          <p className="mb-4 font-mono text-[10.5px] text-[#8fb3d4]">
            claims ungraded — the API returns no per-claim grounding yet
          </p>
          {claims.length === 0 ? (
            <p className="text-sm text-[#a9c1d6]">This run returned no answer text.</p>
          ) : (
            <div className="space-y-3">
              {claims.map((c, i) => (
                <ClaimBlock
                  key={i}
                  claim={c}
                  scores={sources.map((s) => s.score)}
                  onSelectSource={setPicked}
                />
              ))}
            </div>
          )}

          {entityGroups.length > 0 ? (
            <>
              <div className={`${eyebrow} mb-[11px] mt-5 flex items-center gap-1.5`}>
                Entities in this answer
                <HelpTip icon dark side="bottom" term="KG entity / relation">
                  Graph-store entities whose name literally occurs in the answer text — matched in
                  your browser, not attributed by the model — with their depth-1 relations. Up to
                  three entities, six relations each. Absent when the deployment has no graph or no
                  name matched.
                </HelpTip>
              </div>
              <div className="space-y-3.5 rounded-panel bg-white/5 px-[15px] py-3.5">
                {entityGroups.map((g) => (
                  <div key={g.name}>
                    <div className="mb-[9px] flex items-center gap-[7px] text-xs font-medium leading-[1.3] text-white">
                      <span aria-hidden="true" className="h-2 w-2 rounded-full bg-moss" />
                      {g.name}
                    </div>
                    <div className="flex flex-col gap-[7px] border-l border-white/15 pl-[15px] text-[11.5px] leading-[1.3] text-[#a9c1d6]">
                      {g.triples.map((t, i) => (
                        <div key={i}>
                          — {t.predicate} →{" "}
                          <strong className="font-semibold text-white">
                            {t.subject === g.name ? t.object : t.subject}
                          </strong>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </section>

        {/* Right — the selected source in situ, then the rest of the set. */}
        <section className="bg-ink-800 px-[26px] py-6">
          {sources.length === 0 ? (
            <p className="text-sm text-[#a9c1d6]">This run retrieved no sources.</p>
          ) : (
            <>
              <div className="mb-3.5 flex items-center gap-2.5">
                <div className={eyebrow}>
                  Source {selected + 1} of {sources.length}
                </div>
                <div className="ml-auto flex gap-[5px]">
                  <button
                    type="button"
                    onClick={() => setPicked(Math.max(selected - 1, 0))}
                    disabled={selected === 0}
                    aria-label="Previous source"
                    className="h-[22px] w-[22px] rounded bg-white/[0.14] text-[11px] leading-[22px] text-[#dce8f3] hover:bg-white/25 disabled:opacity-40"
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    onClick={() => setPicked(Math.min(selected + 1, sources.length - 1))}
                    disabled={selected === sources.length - 1}
                    aria-label="Next source"
                    className="h-[22px] w-[22px] rounded bg-white/[0.14] text-[11px] leading-[22px] text-[#dce8f3] hover:bg-white/25 disabled:opacity-40"
                  >
                    ›
                  </button>
                </div>
              </div>

              <SourceViewer
                source={sources[selected]}
                answer={active.response.answer}
                collection={active.collection}
                apiKey={apiKey}
              />

              <div className={`${eyebrow} mb-[11px] mt-3.5 flex items-center gap-1.5`}>
                Retrieved set
                <HelpTip icon dark side="left" term="Retrieved set">
                  Everything this run retrieved, minus the source shown above. A row in accent is
                  cited by some claim; a dimmed row was retrieved but no claim cites it. The number
                  on the right is the retrieval score. Click a row to open it above.
                </HelpTip>
              </div>
              <div className="flex flex-col gap-2">
                {sources.map((s, i) => {
                  if (i === selected) return null;
                  const isCited = cited.has(i);
                  const title = (s.metadata.title && String(s.metadata.title)) || s.doc_id;
                  return (
                    <button
                      key={s.chunk_id || i}
                      type="button"
                      onClick={() => setPicked(i)}
                      className="flex w-full items-center gap-2.5 rounded-row bg-white/5 px-3 py-[11px] text-left hover:bg-white/10"
                    >
                      <span
                        className={`font-mono text-[10px] font-medium ${
                          isCited ? "text-accent" : "text-[#8fb3d4]"
                        }`}
                      >
                        {i + 1}
                      </span>
                      <span
                        className={`min-w-0 flex-1 truncate text-[12.5px] leading-[1.35] ${
                          isCited ? "text-[#c7d8e8]" : "text-[#a9c1d6]"
                        }`}
                      >
                        {title}
                      </span>
                      <span className="font-mono text-[10px] text-[#8fb3d4]">
                        {s.score.toFixed(2)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          )}

          <div className="mt-[18px] flex items-center gap-2">
            <button
              type="button"
              onClick={onSave}
              className="flex-1 rounded-chip bg-accent py-[11px] text-center text-[11.5px] font-medium text-ink-600 hover:brightness-95"
            >
              Save run
            </button>
            <HelpTip icon dark term="saved run">
              Keeps this run — question, collection, retrieval levers, answer and its sources — in
              this browser, newest first and capped at {SAVED_CAP}, so it stays in the run selector
              after a reload. Explore&rsquo;s live run is in memory only and dies with the tab.
            </HelpTip>
            <button
              type="button"
              onClick={() => onSendToCompare(active.query)}
              className="flex-1 rounded-chip border border-white/35 py-[11px] text-center text-[11.5px] font-medium text-[#dce8f3] hover:bg-white/5"
            >
              Send to Compare →
            </button>
            <HelpTip icon dark side="left" term="Send to Compare">
              Opens Compare with this question filled in, ready to run across several lanes. Only
              the question travels — the answer, the sources and this run&rsquo;s levers stay here.
            </HelpTip>
          </div>
          <span aria-live="polite" className="mt-2 block text-center font-mono text-[10px] text-[#8fb3d4]">
            {saved}
          </span>
        </section>
      </div>

      {/* The terms this screen shows but cannot define inline. */}
      <GlossaryPanel
        dark
        groups={[
          "Retrieval mode",
          "Reranking",
          "Fusion & scoring",
          "Corpus & indexing",
          "Stores",
          "Runs & evidence",
        ]}
        summary="run · claim · citation · retrieval score · passage highlighting"
      />
    </div>
  );
}
