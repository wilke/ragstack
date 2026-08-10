// Shown before the first search. No skeletons — nothing is loading yet, so this
// is the screen's only chance to say what Explore does and what the controls
// above it are for.

export function EmptyState() {
  return (
    <div className="mt-8 rounded-card border border-dashed border-line p-6 text-sm leading-[1.7] text-dim">
      <p className="mb-2.5 text-strong">Ask the corpus a question in plain language.</p>
      <p className="mb-2.5">
        Explore retrieves the passages that best match it and shows one synthesized answer
        above the sources it was built from — each <span className="font-mono">[n]</span>{" "}
        in the answer points at a source below, and every source keeps its own link into
        Evidence for verification.
      </p>
      <p>
        The chips above are the settings the next question runs with. The first names the
        collection being searched — a picker, when more than one is available to your key
        — and <span className="text-strong">Options</span> tunes retrieval itself: which
        legs run, whether the query is rewritten, whether a reranker re-scores, and how
        many passages come back.
      </p>
    </div>
  );
}
