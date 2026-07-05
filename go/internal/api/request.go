package api

// QueryRequest is the request body for POST /v1/query.
//
// TODO(parity, issue #27): add nullable per-request rerank controls
//   `rerank *bool` (json:"rerank,omitempty") and
//   `rerank_candidates *int` (json:"rerank_candidates,omitempty")
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
	// RetrievalMode selects the legs: hybrid | vector | bm25; nil = hybrid.
	RetrievalMode     *string        `json:"retrieval_mode,omitempty"`
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
	// RetrievalMode selects the legs: hybrid | vector | bm25; nil = hybrid.
	RetrievalMode *string   `json:"retrieval_mode,omitempty"`
}

// IngestRequest is the request body for POST /v1/ingest.
type IngestRequest struct {
	Source   *string        `json:"source"`
	Metadata map[string]any `json:"metadata,omitempty"`
}
