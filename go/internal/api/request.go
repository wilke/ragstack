package api

// QueryRequest is the request body for POST /v1/query.
//
// TODO(parity, issue #27): add nullable per-request rerank controls
//
//	`rerank *bool` (json:"rerank,omitempty") and
//	`rerank_candidates *int` (json:"rerank_candidates,omitempty")
//
// to match the Python implementation: rerank=false skips reranking even when a
// reranker is wired (shallow pool = top_k); rerank_candidates overrides the
// pool depth; both nil preserve the server-wide default.
type QueryRequest struct {
	Query             *string        `json:"query"`
	TopK              int            `json:"top_k,omitempty"`
	RewriteStrategies []string       `json:"rewrite_strategies,omitempty"`
	Filters           map[string]any `json:"filters,omitempty"`
	UseGraph          *bool          `json:"use_graph,omitempty"`
	Stream            *bool          `json:"stream,omitempty"`
	// Collection selects which registry collection to query; nil = default.
	Collection *string `json:"collection,omitempty"`
	// RetrievalMode selects the legs: hybrid | vector | bm25; nil = hybrid.
	RetrievalMode *string `json:"retrieval_mode,omitempty"`
	// LLM / Reranker are per-request model overrides (registered model ids); nil = default.
	LLM      *string `json:"llm,omitempty"`
	Reranker *string `json:"reranker,omitempty"`
}

// RetrieveRequest is the request body for POST /v1/retrieve.
//
// TODO(parity, issue #27): mirror QueryRequest's `rerank` / `rerank_candidates`
// per-request rerank controls (see Python ragstack.api.routers.query).
type RetrieveRequest struct {
	Query    *string        `json:"query"`
	TopK     int            `json:"top_k,omitempty"`
	Filters  map[string]any `json:"filters,omitempty"`
	UseGraph *bool          `json:"use_graph,omitempty"`
	// Collection selects which registry collection to retrieve from; nil = default.
	Collection *string `json:"collection,omitempty"`
	// RetrievalMode selects the legs: hybrid | vector | bm25; nil = hybrid.
	RetrievalMode *string `json:"retrieval_mode,omitempty"`
	// Reranker is a per-request reranker model override (registered id); nil = default.
	Reranker *string `json:"reranker,omitempty"`
}

// IngestRequest is the request body for POST /v1/ingest.
type IngestRequest struct {
	Source   *string        `json:"source"`
	Metadata map[string]any `json:"metadata,omitempty"`
}
