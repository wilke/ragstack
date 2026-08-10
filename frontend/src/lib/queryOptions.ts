// The query-pipeline levers shared by Explore's Options menu and Compare's
// lever panel — each maps to a /v1/query field. One module so the two views can
// never disagree about what a value means on the wire (Compare's lane requests
// and Explore's single request must stay comparable).

export type Mode = "hybrid" | "vector" | "bm25"; // retrieval_mode
export type Rerank = "default" | "on" | "off"; // → server default | force on | force off
export type Rewrite = "none" | "multiquery" | "hyde"; // → rewrite_strategies

// The server's rewriter registry (api/deps.py _build_rewriters) always runs
// passthrough; an LLM-backed strategy is ADDED to it, never substituted, so the
// original query still retrieves alongside the rewrites.
export const rewriteStrategies = (r: Rewrite): string[] =>
  r === "none" ? ["passthrough"] : ["passthrough", r];

export const rerankValue = (r: Rerank): boolean | null =>
  r === "default" ? null : r === "on";

// No tip strings live here any more. Both views label these levers with
// <HelpTip term="retrieval mode" | "query rewriting" | "rerank" | "top_k"/>,
// which reads lib/glossary — a second copy of the wording is what drift is
// made of, and the old one was written for native title="" tooltips.
