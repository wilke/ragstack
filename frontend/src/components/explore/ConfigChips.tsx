// The closed-state pipeline readout under the query row: what the NEXT query
// will run with, as mono chips — collection, mode, rerank, k — ending in the
// Options popover trigger. The collection chip IS the picker: a native
// <select> stretched invisibly over the chip face, so the chip shows only the
// label while keyboard/AT get the real control (the option text still carries
// count + chunking, as the old standalone select did).
//
// #420: this component COMPUTES NOTHING about the target any more. It is handed
// the `CollectionTarget` the request is built from and renders its label, so the
// chip cannot name a collection the query will not hit. It used to resolve its
// own label with `opts.find(...) ?? opts[0]`, which for a caller who could not
// read the registry default matched nothing and fell through to the first
// option — a plausible, wrong answer.

import type { CollectionInfo } from "../../api/client";
import { describeChunking } from "../../lib/chunkers";
import type { CollectionTarget } from "../../lib/collectionTarget";
import { HelpTip } from "../HelpTip";
import {
  effectiveRerank,
  QueryOptionsMenu,
  type QueryOptions,
} from "../QueryOptionsMenu";

const CHIP = "rounded-[10px] bg-[#f2f1ed] px-[11px] py-1.5 text-[#6a6a64]";

export function ConfigChips({
  opts,
  target,
  setCollection,
  options,
  onOptionsChange,
  serverRerank,
}: {
  opts: CollectionInfo[];
  // What the next request will target, resolved once by the view and shared
  // with the mutation. The chip renders `target.label` verbatim.
  target: CollectionTarget;
  setCollection: (id: string) => void;
  options: QueryOptions;
  onOptionsChange: (patch: Partial<QueryOptions>) => void;
  serverRerank: boolean | null;
}) {
  const selected = opts.find((c) => c.id === target.id);

  return (
    <div className="mb-8 mt-3 flex flex-wrap items-center gap-[7px] font-mono text-[10.5px]">
      {/* The picker renders whenever there is ANYTHING to pick — one option
          included. Hiding the only control that can correct a wrong target is
          what turned #420 from a bug into a dead end; a one-option <select> is
          a harmless no-op, and "when is it shown?" is one fewer branch that can
          be wrong. */}
      {opts.length >= 1 ? (
        <span className="relative inline-flex">
          <span aria-hidden="true" className={CHIP}>
            {target.label} ▾
          </span>
          <select
            aria-label="Collection"
            title={selected?.model}
            value={target.id ?? ""}
            onChange={(e) => setCollection(e.target.value)}
            className="absolute inset-0 w-full cursor-pointer opacity-0"
          >
            {opts.map((c) => {
              // Shared with the Collection picker (lib/chunkers.ts) so both name a
              // collection's build config the same way, and semantic collections
              // don't get an invented size appended.
              const built = describeChunking(c);
              return (
                <option key={c.id} value={c.id}>
                  {c.label}
                  {c.count != null ? ` (${c.count.toLocaleString()})` : ""}
                  {built ? ` · ${built}` : ""}
                </option>
              );
            })}
          </select>
        </span>
      ) : (
        <span className={CHIP}>{target.label}</span>
      )}
      <span className={CHIP}>{options.mode === "bm25" ? "es (bm25)" : options.mode}</span>
      <span className={CHIP}>rerank {effectiveRerank(options, serverRerank)}</span>
      <span className={CHIP}>k {options.topK}</span>
      <HelpTip icon side="bottom" term="query settings">
        The pipeline settings the next question runs with: which collection is searched,
        which retrieval legs run, whether a reranker re-scores them, and how many
        passages come back. Changing one does not re-run the last question. Until the
        reranker is set by hand the rerank chip reads the server&rsquo;s configured
        default — and that is only legible when the admin-only /v1/config is readable,
        so otherwise it shows the built-in default (off) rather than what the server
        will actually do.
      </HelpTip>
      <QueryOptionsMenu value={options} onChange={onOptionsChange} serverRerank={serverRerank} />
    </div>
  );
}
