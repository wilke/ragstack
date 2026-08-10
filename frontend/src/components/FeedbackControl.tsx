// "Was this useful?" Yes/No on an answer. Ephemeral: records to the in-session
// store (never a network call today), so it can't fail. aria-pressed reflects
// selection; the confirmation is a transient "Noted" (not "Saved" — nothing is
// persisted server side).

import { useState } from "react";
import { hashAnswer, recordFeedback, type Verdict } from "../lib/feedback";
import { HelpTip } from "./HelpTip";

export function FeedbackControl({ query, answer }: { query: string; answer: string }) {
  const [choice, setChoice] = useState<Verdict | null>(null);

  const vote = (verdict: Verdict) => {
    setChoice(verdict);
    recordFeedback({ query, answerHash: hashAnswer(answer), verdict });
  };

  const btn = (verdict: Verdict, label: string, text: string) => (
    <button
      type="button"
      aria-label={label}
      aria-pressed={choice === verdict}
      onClick={() => vote(verdict)}
      className={`rounded-chip border px-[15px] py-2 text-xs font-medium transition-colors ${
        choice === verdict
          ? "border-ink-900 bg-accent-soft text-ink-900"
          : "border-[#d8d7d2] text-[#5b5b55] hover:bg-paper"
      }`}
    >
      {text}
    </button>
  );

  return (
    <div className="flex items-center gap-[9px]">
      <span className="text-[11.5px] text-dim">Was this useful?</span>
      <HelpTip icon side="top" term="feedback" />
      {btn("up", "Helpful", "Yes")}
      {btn("down", "Not helpful", "No")}
      <span aria-live="polite" className="text-xs text-faint">
        {choice ? "Noted" : ""}
      </span>
    </div>
  );
}
