// Cross-lane agreement chip on a ranked source row. Membership is computed on
// `doc_id` (the join key that survives different chunkers/models) — never on
// cross-lane scores, which aren't commensurable across models/fusions.
// No off-topic state: flagging a result off-topic needs a backend signal that
// doesn't exist yet (handoff README backend gap #4) — inferring it from scores
// here would fabricate a judgment the API never made.

export function agreementLabel(letters: string[], laneCount: number): string {
  if (letters.length === 1) return `only ${letters[0]}`;
  if (letters.length >= laneCount) return "all lanes";
  return letters.join(" · ");
}

export function AgreementBadge({ letters, laneCount }: { letters: string[]; laneCount: number }) {
  // With fewer than two answered lanes there is no cross-lane fact to state.
  if (laneCount < 2 || letters.length === 0) return null;
  const unique = letters.length === 1;
  return (
    <span
      className={`rounded-[3px] px-1.5 py-1 font-mono text-[9.5px] font-medium ${
        unique ? "bg-mossSoft text-[#1f6b4c]" : "bg-linkSoft text-link"
      }`}
    >
      {agreementLabel(letters, laneCount)}
    </span>
  );
}
