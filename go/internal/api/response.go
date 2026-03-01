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
