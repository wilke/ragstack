package api

// HealthResponse is the response for GET /health.
type HealthResponse struct {
	Status string `json:"status"`
}

// Source represents a retrieved chunk with its relevance score.
type Source struct {
	DocID    string         `json:"doc_id"`
	ChunkID  string         `json:"chunk_id"`
	Content  string         `json:"content"`
	Score    float64        `json:"score"`
	Metadata map[string]any `json:"metadata,omitempty"`
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

// TripleResponse represents a subject-predicate-object triple.
type TripleResponse struct {
	Subject   string `json:"subject"`
	Predicate string `json:"predicate"`
	Object    string `json:"object"`
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
