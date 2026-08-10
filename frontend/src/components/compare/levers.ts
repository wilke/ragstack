import type { Mode, Rerank, Rewrite } from "../../lib/queryOptions";

// The pipeline levers — each maps to a /v1/query field so a single question can
// be compared across retrieval *strategies*, not just corpora. Held once as the
// shared defaults and per lane as a sparse override set: a lane's effective
// config is `{...defaults, ...overrides}`, so editing a default never clobbers
// an explicit lane choice.

export interface Levers {
  mode: Mode; // retrieval_mode
  rerank: Rerank; // rerank: null | true | false
  useGraph: boolean; // use_graph
  rewrite: Rewrite; // rewrite_strategies
  topK: number | null; // top_k; null → inherit the global default
  llm: string; // registered model id for generation; "" → server default
  reranker: string; // registered model id for reranking; "" → server default
}

export type LeverOverrides = Partial<Levers>;

export const DEFAULT_LEVERS: Levers = {
  mode: "hybrid",
  rerank: "default",
  useGraph: true,
  rewrite: "none",
  topK: null,
  llm: "",
  reranker: "",
};

export const effectiveLevers = (defaults: Levers, overrides: LeverOverrides): Levers => ({
  ...defaults,
  ...overrides,
});

// Drop overrides that equal the current default — a lever set back to the
// shared value is inheritance again, not a deviation worth a yellow chip.
export function normalizeOverrides(defaults: Levers, overrides: LeverOverrides): LeverOverrides {
  const out: LeverOverrides = {};
  for (const k of Object.keys(overrides) as (keyof Levers)[]) {
    if (overrides[k] !== defaults[k]) (out as Record<string, unknown>)[k] = overrides[k];
  }
  return out;
}

// One chip label per lever, in a fixed order so chip rows read the same across
// lanes. Model ids are shown by their basename to keep chips short.
const shortModel = (id: string) => id.split("/").pop() || id;

function leverChip(k: keyof Levers, v: Levers): string {
  switch (k) {
    case "mode":
      return `mode ${v.mode}`;
    case "rewrite":
      return `rewrite ${v.rewrite}`;
    case "rerank":
      return `rerank ${v.rerank}`;
    case "topK":
      return `k ${v.topK ?? "default"}`;
    case "useGraph":
      return `KG ${v.useGraph ? "on" : "off"}`;
    case "llm":
      return `llm ${shortModel(v.llm)}`;
    case "reranker":
      return `rr ${shortModel(v.reranker)}`;
  }
}

const CHIP_ORDER: (keyof Levers)[] = ["mode", "rewrite", "rerank", "topK", "useGraph", "llm", "reranker"];

// The shared-defaults chip row: every lever, current value.
export function defaultsChips(defaults: Levers): string[] {
  return CHIP_ORDER.filter((k) => k !== "llm" || defaults.llm)
    .filter((k) => k !== "reranker" || defaults.reranker)
    .map((k) => leverChip(k, defaults));
}

// A lane's chip row: ONLY the levers it overrides (empty ⇒ show "defaults").
export function overrideChips(defaults: Levers, overrides: LeverOverrides): string[] {
  const eff = effectiveLevers(defaults, overrides);
  return CHIP_ORDER.filter((k) => k in overrides && overrides[k] !== defaults[k]).map((k) =>
    leverChip(k, eff),
  );
}
