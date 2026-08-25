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
	// Context holds the source's neighbours (context_window > 0), ordered by
	// Position; omitted when none were attached.
	Context []ContextChunk `json:"context,omitempty"`
}

// QueryResponse is the response for POST /v1/query.
type QueryResponse struct {
	Answer           string   `json:"answer"`
	Sources          []Source `json:"sources"`
	RewrittenQueries []string `json:"rewritten_queries"`
}

// RetrieveResponse is the response for POST /v1/retrieve.
type RetrieveResponse struct {
	Sources []Source `json:"sources"`
}

// IngestResponse is the response for POST /v1/ingest and GET /v1/ingest/{job_id}.
type IngestResponse struct {
	JobID    string   `json:"job_id"`
	Status   string   `json:"status"`
	ChunkIDs []string `json:"chunk_ids"`
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
type CollectionInfo struct {
	ID          string      `json:"id"`
	Label       string      `json:"label"`
	Model       string      `json:"model"`
	Dim         int         `json:"dim"`
	ChunkMethod *string     `json:"chunk_method,omitempty"`
	ChunkSize   *int        `json:"chunk_size,omitempty"`
	Default     bool        `json:"default"`
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
