// Thumbs up/down on an answer. Ephemeral: records to the in-session store (never
// a network call today), so it can't fail. aria-pressed reflects selection; the
// confirmation is a transient "Noted" (not "Saved" — nothing is persisted server
// side).

import { useState } from "react";
import { hashAnswer, recordFeedback, type Verdict } from "../lib/feedback";

export function FeedbackControl({ query, answer }: { query: string; answer: string }) {
  const [choice, setChoice] = useState<Verdict | null>(null);

  const vote = (verdict: Verdict) => {
    setChoice(verdict);
    recordFeedback({ query, answerHash: hashAnswer(answer), verdict });
  };

  const btn = (verdict: Verdict, label: string, glyph: string) => (
    <button
      type="button"
      aria-label={label}
      aria-pressed={choice === verdict}
      onClick={() => vote(verdict)}
      className={`min-h-6 min-w-6 rounded border px-2 py-1 text-sm transition-colors ${
        choice === verdict
          ? "border-blue-500 bg-blue-50 text-blue-700"
          : "border-gray-300 text-gray-500 hover:bg-gray-50"
      }`}
    >
      {glyph}
    </button>
  );

  return (
    <div className="flex items-center gap-2">
      {btn("up", "Helpful", "👍")}
      {btn("down", "Not helpful", "👎")}
      <span aria-live="polite" className="text-xs text-gray-400">
        {choice ? "Noted" : ""}
      </span>
    </div>
  );
}
