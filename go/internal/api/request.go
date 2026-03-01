package api

// QueryRequest is the request body for POST /v1/query.
type QueryRequest struct {
	Query             *string        `json:"query"`
	TopK              int            `json:"top_k,omitempty"`
	RewriteStrategies []string       `json:"rewrite_strategies,omitempty"`
	Filters           map[string]any `json:"filters,omitempty"`
	UseGraph          *bool          `json:"use_graph,omitempty"`
	Stream            *bool          `json:"stream,omitempty"`
}

// RetrieveRequest is the request body for POST /v1/retrieve.
type RetrieveRequest struct {
	Query    *string        `json:"query"`
	TopK     int            `json:"top_k,omitempty"`
	Filters  map[string]any `json:"filters,omitempty"`
	UseGraph *bool          `json:"use_graph,omitempty"`
}

// IngestRequest is the request body for POST /v1/ingest.
type IngestRequest struct {
	Source   *string        `json:"source"`
	Metadata map[string]any `json:"metadata,omitempty"`
}
