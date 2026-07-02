// Ephemeral, best-effort answer feedback.
//
// There is no feedback endpoint yet, so this records to an in-session store only
// (sessionStorage — NOT localStorage, matching the API-key no-persist
// convention) and is lost on reload. recordFeedback NEVER throws: the answer
// view must not break if storage is unavailable. The event shape is pre-aligned
// to a future POST /v1/stats/usage feedback event, so wiring the network call
// later is a one-line swap of the sink.

export type Verdict = "up" | "down";

export interface FeedbackEvent {
  query: string;
  answerHash: string;
  verdict: Verdict;
  ts: number;
}

const KEY = "ragstack.feedback";

export function recordFeedback(ev: Omit<FeedbackEvent, "ts">): void {
  try {
    const full: FeedbackEvent = { ...ev, ts: Date.now() };
    const raw = sessionStorage.getItem(KEY);
    const arr = raw ? (JSON.parse(raw) as FeedbackEvent[]) : [];
    arr.push(full);
    sessionStorage.setItem(KEY, JSON.stringify(arr));
  } catch {
    // Ephemeral by contract — storage disabled/full is non-fatal; swallow.
  }
}

/** Small stable non-crypto hash, used only to key a feedback event to an answer. */
export function hashAnswer(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}
