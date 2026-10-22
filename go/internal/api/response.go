package api

// HealthResponse is the response for GET /health.
type HealthResponse struct {
	Status string `json:"status"`
}

// ContextChunk is one neighbouring chunk attached to a Source when the request
// set context_window > 0 (issue #322). Position is the offset from the source
// in chunks: -1 is the immediately preceding chunk, 1 the following one.
type ContextChunk struct {
	ChunkID  string `json:"chunk_id"`
	Position int    `json:"position"`
	Content  string `json:"content"`
}

// Source represents a retrieved chunk with its relevance score.
type Source struct {
	DocID    string         `json:"doc_id"`
	ChunkID  string         `json:"chunk_id"`
	Content  string         `json:"content"`
	Score    float64        `json:"score"`
	Metadata map[string]any `json:"metadata,omitempty"`
	// Collection is the registry collection id the source came from; set only
	// on a multi-collection request (`collections`, issue #253).
	Collection string `json:"collection,omitempty"`
	// Context holds the source's neighbours (context_window > 0), ordered by
	// Position; omitted when none were attached.
	Context []ContextChunk `json:"context,omitempty"`
}

// DegradedEntry is one stage of a query/retrieve request that fell back to a
// lower-quality path instead of failing the request
// (contracts/schemas/degraded_entry.json, #748, #751). Component is one of
// rerank, generation, rewrite, graph, context; Reason is bounded to 200
// characters by the contract.
type DegradedEntry struct {
	Component string `json:"component"`
	Reason    string `json:"reason"`
}

// QueryResponse is the response for POST /v1/query.
//
// Degraded is omitted when empty: the contract gives it minItems 1, so an
// undegraded response carries no `degraded` key at all. The Go stub handlers
// never degrade and never set it.
type QueryResponse struct {
	Answer           string          `json:"answer"`
	Sources          []Source        `json:"sources"`
	RewrittenQueries []string        `json:"rewritten_queries"`
	Degraded         []DegradedEntry `json:"degraded,omitempty"`
}

// RetrieveResponse is the response for POST /v1/retrieve. Degraded follows
// the same rule as on QueryResponse.
type RetrieveResponse struct {
	Sources  []Source        `json:"sources"`
	Degraded []DegradedEntry `json:"degraded,omitempty"`
}

// DeepHealthCheck is one dependency verdict in a DeepHealthResponse
// (contracts/schemas/deep_health_response.json). OK stays for compatibility
// and is true for state ok and disabled; State is the finer verdict
// (ok, degraded, down, disabled).
type DeepHealthCheck struct {
	Name      string   `json:"name"`
	OK        bool     `json:"ok"`
	State     string   `json:"state,omitempty"`
	Detail    *string  `json:"detail,omitempty"`
	LatencyMS *float64 `json:"latency_ms,omitempty"`
}

// DeepHealthResponse is the body for GET /v1/health/deep (admin only). Checks
// must encode as [] and never null: build it with a non-nil slice.
type DeepHealthResponse struct {
	Status string            `json:"status"`
	Checks []DeepHealthCheck `json:"checks"`
}

// IngestResponse is the response for POST /v1/ingest and GET /v1/ingest/{job_id}
// (contracts/schemas/ingest_response.json). Items and Collection are nullable in
// the contract and are always emitted, as null when unset, like the Python side.
type IngestResponse struct {
	JobID      string            `json:"job_id"`
	Status     string            `json:"status"`
	ChunkIDs   []string          `json:"chunk_ids"`
	Items      *IngestItemCounts `json:"items"`
	Collection *string           `json:"collection"`
}

// IngestItemCounts is per-document progress for a batch/directory ingest.
type IngestItemCounts struct {
	Total     int `json:"total"`
	Completed int `json:"completed"`
	Failed    int `json:"failed"`
	Pending   int `json:"pending"`
}

// DocumentInfo represents metadata about an indexed document.
type DocumentInfo struct {
	DocID    string         `json:"doc_id"`
	Source   string         `json:"source"`
	Metadata map[string]any `json:"metadata,omitempty"`
}

// EntityInfo represents an entity in the knowledge graph.
type EntityInfo struct {
	Name        string `json:"name"`
	TripleCount int    `json:"triple_count"`
}

// TripleResponse represents a subject-predicate-object triple, with optional
// epistemic provenance (#347). The six provenance fields are optional in the
// contract; like the Python implementation they are always emitted.
type TripleResponse struct {
	Subject    string `json:"subject"`
	Predicate  string `json:"predicate"`
	Object     string `json:"object"`
	Evidence   string `json:"evidence"`
	ChunkID    string `json:"chunk_id"`
	DerivedBy  string `json:"derived_by"`
	Confidence int    `json:"confidence"`
	SubjectID  string `json:"subject_id"`
	ObjectID   string `json:"object_id"`
}

// CollectionInfo describes one registry collection the query API can serve.
//
// IsDefault is the canonical spelling of Default and both carry the SAME answer:
// this entry is the one the global registry pointer (DEFAULT_COLLECTION_ID)
// names. It is deliberately not `omitempty` — a false must be emitted, not
// dropped, or an entry would go out as `"default": true` with `"is_default"`
// missing (or false) and the two spellings would contradict each other. Neither
// flag is the caller's effective target; that is CollectionsResponse.Default.
type CollectionInfo struct {
	ID          string      `json:"id"`
	Label       string      `json:"label"`
	Model       string      `json:"model"`
	Dim         int         `json:"dim"`
	ChunkMethod *string     `json:"chunk_method,omitempty"`
	ChunkSize   *int        `json:"chunk_size,omitempty"`
	Default     bool        `json:"default"`
	IsDefault   bool        `json:"is_default"`
	Count       *int        `json:"count,omitempty"`
	Provenance  interface{} `json:"provenance,omitempty"`
}

// CollectionsResponse is the body for GET /v1/collections.
type CollectionsResponse struct {
	Collections []CollectionInfo `json:"collections"`
	Default     string           `json:"default"`
}

// ChunkOut is a chunk fetched by id (no retrieval score), for context expansion.
type ChunkOut struct {
	DocID    string         `json:"doc_id"`
	ChunkID  string         `json:"chunk_id"`
	Content  string         `json:"content"`
	Metadata map[string]any `json:"metadata,omitempty"`
}

// ChunksResponse is the body for GET /v1/chunks.
type ChunksResponse struct {
	Chunks []ChunkOut `json:"chunks"`
}

// ModelEntry is a registered model (which task it serves + how to reach it).
type ModelEntry struct {
	ID       string         `json:"id"`
	Task     string         `json:"task"`
	Provider string         `json:"provider,omitempty"`
	BaseURLs []string       `json:"base_urls,omitempty"`
	Model    string         `json:"model,omitempty"`
	Dim      *int           `json:"dim,omitempty"`
	Params   map[string]any `json:"params,omitempty"`
}

// ModelsRegistryResponse is the body for GET /v1/admin/models/registry.
type ModelsRegistryResponse struct {
	Models      []ModelEntry      `json:"models"`
	Assignments map[string]string `json:"assignments"`
}

// AvailableModel is a per-request-assignable model (llm / reranker); URLs omitted.
type AvailableModel struct {
	ID       string `json:"id"`
	Task     string `json:"task"`
	Label    string `json:"label"`
	Model    string `json:"model"`
	Provider string `json:"provider"`
}

// AvailableModelsResponse is the body for GET /v1/models/available.
type AvailableModelsResponse struct {
	Models []AvailableModel `json:"models"`
}
