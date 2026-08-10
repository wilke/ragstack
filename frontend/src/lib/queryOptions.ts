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

// Hover copy for the shared levers (native title tooltips). Compare extends
// this with its lane-only levers (graph/llm/reranker model).
export const OPTION_TIP: Record<string, string> = {
  mode: "Which retrieval legs run. hybrid = dense vectors + BM25 keyword (fused); vector = dense only; bm25 = keyword (Elasticsearch) only.",
  rewrite:
    "Expand the query before retrieving. none = as-is; multiquery = LLM paraphrases; hyde = retrieve on a hypothetical answer.",
  rerank: "Cross-encoder re-scoring of the results. default = server setting; on / off = force it.",
  top_k: "How many results to return.",
};
