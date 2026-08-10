// Shimmer placeholder while the answer is generating. aria-busy + sr-only label
// so assistive tech announces the pending state. Surfaces use the handoff
// palette (paper/line) so the skeleton sits on the same warm scale as the
// cards it stands in for.

export function AnswerSkeleton() {
  return (
    <div aria-busy="true" className="space-y-2 rounded-row bg-paper p-3">
      <span className="sr-only">Generating answer…</span>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-4 animate-pulse rounded-row bg-line"
          style={{ width: `${100 - i * 12}%` }}
        />
      ))}
    </div>
  );
}

// A muted source-card placeholder row shown while the (single) request is in
// flight — sources arrive in the same response, so this is a brief loading cue.
export function SourceSkeleton() {
  return (
    <ol className="space-y-3" aria-busy="true">
      {[0, 1, 2].map((i) => (
        <li key={i} className="rounded-row border border-line p-3">
          <div className="h-4 w-1/2 animate-pulse rounded-row bg-line" />
          <div className="mt-2 h-3 w-full animate-pulse rounded-row bg-lineSoft" />
        </li>
      ))}
    </ol>
  );
}
