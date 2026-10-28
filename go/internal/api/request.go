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
	// Collections (issue #253): 1–5 unique registry collection ids to query
	// together — one retrieval leg per id, RRF-fused, reranked once. Mutually
	// exclusive with Collection. Schema mirror only (handler is a stub).
	Collections []string `json:"collections,omitempty"`
	// RetrievalMode selects the legs: hybrid | vector | bm25; nil = hybrid.
	RetrievalMode *string `json:"retrieval_mode,omitempty"`
	// ContextWindow (issue #322): prev/next neighbour hops to attach to each
	// Source as `context` after rerank; 0 (default) = none, max 3. Schema
	// mirror only — the handler is a stub per ADR-0006.
	ContextWindow int `json:"context_window,omitempty"`
	// ExcludeBoilerplate (issue #597): drop chunks stamped
	// `metadata.is_boilerplate` (reference lists, licence footers,
	// acknowledgements) from retrieval; false (default) = unchanged. Python
	// applies it as a native store negation (Qdrant must_not / Elasticsearch
	// bool.must_not) built server-side from this boolean — the `filters`
	// grammar stays equality-only. Schema mirror only — the handler is a stub
	// per ADR-0006.
	ExcludeBoilerplate bool `json:"exclude_boilerplate,omitempty"`
	// LLM / Reranker are per-request model overrides (registered model ids); nil = default.
	LLM      *string `json:"llm,omitempty"`
	Reranker *string `json:"reranker,omitempty"`
}

// RetrieveRequest is the request body for POST /v1/retrieve.
//
// TODO(parity, issue #27): mirror QueryRequest's `rerank` / `rerank_candidates`
// per-request rerank controls (see Python ragstack.api.routers.query).
type RetrieveRequest struct {
	Query *string `json:"query"`
	TopK  int     `json:"top_k,omitempty"`
	// RewriteStrategies mirrors QueryRequest.RewriteStrategies (issue #750).
	// Go has no rewriters; accepted and ignored exactly as HandleQuery ignores
	// it; schema mirror only per ADR-0006.
	RewriteStrategies []string       `json:"rewrite_strategies,omitempty"`
	Filters           map[string]any `json:"filters,omitempty"`
	UseGraph          *bool          `json:"use_graph,omitempty"`
	// Collection selects which registry collection to retrieve from; nil = default.
	Collection *string `json:"collection,omitempty"`
	// Collections mirrors QueryRequest.Collections (issue #253); mutually exclusive with Collection.
	Collections []string `json:"collections,omitempty"`
	// RetrievalMode selects the legs: hybrid | vector | bm25; nil = hybrid.
	RetrievalMode *string `json:"retrieval_mode,omitempty"`
	// ContextWindow mirrors QueryRequest.ContextWindow (issue #322); 0 = none, max 3.
	ContextWindow int `json:"context_window,omitempty"`
	// ExcludeBoilerplate mirrors QueryRequest.ExcludeBoilerplate (issue #597).
	ExcludeBoilerplate bool `json:"exclude_boilerplate,omitempty"`
	// Reranker is a per-request reranker model override (registered id); nil = default.
	Reranker *string `json:"reranker,omitempty"`
	// IncludeSymbolGlossary (#754): true asks for each returned Source to
	// carry `symbol_glossary` (the chunk's ingest-time symbol definitions, ""
	// when it has none), filled after rerank and the top_k cut from each
	// source's own collection's text index. /v1/retrieve only. Schema mirror
	// only — the handler is a stub per ADR-0006 and returns no sources.
	//
	// TODO(parity, #754): when Go retrieves for real, hydrate the glossary of
	// the FINAL sources from the text index (Python
	// ragstack.retrieval.glossary.hydrate_symbol_glossary: one batched,
	// tenant-scoped lookup per collection, bounded by RERANK_GLOSSARY_TIMEOUT,
	// failure → "" and HTTP 200) and set Source.SymbolGlossary on every source
	// iff this is true. Never invent a separate glossary store.
	IncludeSymbolGlossary bool `json:"include_symbol_glossary,omitempty"`
}

// IngestRequest is the request body for POST /v1/ingest.
type IngestRequest struct {
	Source     *string        `json:"source"`
	Metadata   map[string]any `json:"metadata,omitempty"`
	Collection *string        `json:"collection,omitempty"`
	// DuplicatePolicy is the duplicate-paper guard for this request (#747):
	// "refuse" | "allow" | null. The Go server runs no ingest (HandleIngest is a
	// stub), so parity is accepting the field and refusing a value outside the
	// contract's enum with 422 — nothing else reads it.
	DuplicatePolicy *string `json:"duplicate_policy,omitempty"`
}

// duplicatePolicies is contracts/schemas/ingest_request.json's enum for
// duplicate_policy (null is the absent pointer).
var duplicatePolicies = map[string]bool{"refuse": true, "allow": true}

// ChunkConfig is the chunk strategy for a new collection (build-time identity).
type ChunkConfig struct {
	Method  string         `json:"method"`
	Size    *int           `json:"size,omitempty"`
	Overlap *int           `json:"overlap,omitempty"`
	Params  map[string]any `json:"params,omitempty"`
}

// CollectionCreateRequest is the request body for POST /v1/collections.
// `embedding` and `chunk` are optional (ADR-0003): omitted → the server's
// default build spec fills them in. Chunk is a pointer so "omitted" is
// distinguishable from an empty object.
type CollectionCreateRequest struct {
	Embedding string       `json:"embedding,omitempty"`
	Chunk     *ChunkConfig `json:"chunk,omitempty"`
	ID        *string      `json:"id,omitempty"`
	Label     string       `json:"label,omitempty"`
}
