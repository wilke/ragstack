// Package settings classifies tenant.env keys. Every key the ctl ever sees
// falls into exactly one class, and the class decides where the key may live
// (tenant.env vs secrets.env), who may edit it (HTTP API vs CLI-only), what
// the registry records (value vs fingerprint vs nothing) and what the
// redactors strip from every response, log, unit and bundle.
package settings

import (
	"regexp"
	"sort"
	"strings"
)

// Class of a tenant.env key.
type Class int

const (
	// Unsupported: not a known ragstack setting. Recorded as drift by adopt;
	// never edited by the ctl.
	Unsupported Class = iota
	// Public: a plain application setting; value lives in the registry and
	// may be edited through the typed env API.
	Public
	// Secret: credentials. Only ever in secrets.env; the registry keeps a
	// fingerprint; every redactor strips the value.
	Secret
	// ExecutableSurface: a path, URL or interpreter knob that decides WHAT
	// code runs or WHERE the process reaches. CLI-only, trusted operators.
	ExecutableSurface
)

func (c Class) String() string {
	switch c {
	case Public:
		return "public"
	case Secret:
		return "secret"
	case ExecutableSurface:
		return "executable-surface"
	default:
		return "unsupported"
	}
}

// secretExplicit are secret by name regardless of pattern.
var secretExplicit = set(
	"API_KEYS", "API_KEY_TENANTS", "API_KEY_ROLES",
)

// secretPattern is the key-shaped secret detector shared with the redactors
// (and with the sed in apptainer/new-tenant.sh's keep-mode diff).
var secretPattern = regexp.MustCompile(`(API_KEY[A-Z_]*|_KEY$|SECRET|PASSWORD|TOKEN|DSN|AUTH)`)

// publicDespitePattern are ragstack settings whose NAME trips secretPattern
// but whose value is a number or an identifier, never a credential. Listed
// explicitly so the exception is reviewable; anything not here that matches
// the pattern is treated as a secret (fail closed).
var publicDespitePattern = set(
	"CHUNK_MAX_TOKENS",
	"CHUNK_TOKEN_COUNTER",
	"EMBEDDING_MAX_BATCH_TOKENS",
	"EMBEDDING_CHARS_PER_TOKEN",
	"GOWE_RECEIPTS_OUTPUT_KEY",
	"GOWE_SHARDS_INPUT_KEY",
)

// executableSurface keys decide what runs or where the process connects.
var executableSurface = set(
	"PYTHONPATH", "PATH", "HF_HOME",
	"INGEST_ROOT", "COLLECTION_MANIFEST_DIR",
	"USER_STORE_PATH", "JOB_STORE_PATH", "COLLECTION_STORE_PATH", "GRADING_STORE_PATH",
	"COLLECTIONS_FILE", "MODELS_REGISTRY_FILE", "DOI_ENRICHMENT_CACHE_DIR",
	"GOWE_WORKFLOW_CWL", "COLLECTION_RESTORE_CWL", "GRAPH_EXTRACT_CWL",
	"QDRANT_URL", "ELASTICSEARCH_URL", "NEO4J_URI", "REDIS_URL",
	"EMBEDDING_ENDPOINTS", "EMBEDDING_SIDECAR_URL", "CROSSENCODER_SIDECAR_URL",
	"LLM_ENDPOINT", "GOWE_URL", "WORKSPACE_URL", "MODEL_URL_ALLOWLIST",
	"OTEL_EXPORTER_OTLP_ENDPOINT",
	"PORT", "ROOT_PATH",
)

// public is every field of python/ragstack/config.py's Settings (upper-cased)
// that is neither a secret nor an executable-surface key. Regenerate with:
//
//	grep -E '^    [a-z_]+:' python/ragstack/config.py | sed -E 's/^ +([a-z_]+):.*/\1/' | tr a-z A-Z | sort -u
//
// and drop the keys the two tables above claim.
var public = set(
	"ACCESS_LOG_REPLACED",
	"ACL_BACKFILL_OWNER",
	"ADMIN_ROLE_CACHE_TTL_SECONDS",
	"ADMIN_SUBJECTS",
	"ALLOWED_ORIGINS",
	"ALLOW_USER_COLLECTION_CREATE",
	"BOILERPLATE_CONFIG_JSON",
	"BOILERPLATE_DETECTION_ENABLED",
	"BOILERPLATE_DROP",
	"CHUNK_BREAKPOINT_PERCENTILE",
	"CHUNK_BUFFER_SIZE",
	"CHUNK_MAX_TOKENS",
	"CHUNK_METHOD",
	"CHUNK_MIN_LENGTH",
	"CHUNK_OVERLAP",
	"CHUNK_SIZE",
	"CHUNK_TOKEN_COUNTER",
	"COLLECTION_ACCESS_FLUSH_SECONDS",
	"COLLECTION_NAME_INCLUDE_CHUNK",
	"COLLECTION_RESTORE_INPUTS_JSON",
	"COLLECTION_RESTORE_POLL_INTERVAL",
	"COLLECTION_RESTORE_RETRY_AFTER",
	"COLLECTION_RESTORE_TIMEOUT",
	"COLLECTION_RESTORE_WORKFLOW_NAME",
	"COLLECTIONS_JSON",
	"COLLECTION_SPEC_GUARD",
	"COLLECTION_STATE_CACHE_SECONDS",
	"COLLECTION_STORE_BACKEND",
	"DEFAULT_COLLECTION_ID",
	"DEFAULT_ROLE",
	"DOI_ENRICHMENT_CONCURRENCY",
	"DOI_ENRICHMENT_DATACITE_FALLBACK",
	"DOI_ENRICHMENT_ENABLED",
	"DOI_ENRICHMENT_MAILTO",
	"DOI_ENRICHMENT_TIMEOUT",
	"DOI_ENRICHMENT_USER_AGENT",
	"ELASTICSEARCH_INDEX",
	"ELASTICSEARCH_TIMEOUT",
	"EMBEDDING_API",
	"EMBEDDING_CHARS_PER_TOKEN",
	"EMBEDDING_HEALTH_PATH",
	"EMBEDDING_MAX_BATCH_ITEMS",
	"EMBEDDING_MAX_BATCH_TOKENS",
	"EMBEDDING_MAX_CONCURRENCY",
	"EMBEDDING_MODEL",
	"EMBEDDING_MODEL_DIM",
	"GOWE_OUTPUT_WAIT_TIMEOUT",
	"GOWE_POLL_INTERVAL",
	"GOWE_RECEIPTS_OUTPUT_KEY",
	"GOWE_SHARDS_INPUT_KEY",
	"GOWE_TIMEOUT",
	"GOWE_WORKER_GROUP",
	"GOWE_WORKFLOW_INPUTS_JSON",
	"GOWE_WORKFLOW_NAME",
	"GRADING_STORE_BACKEND",
	"GRAPH_BACKEND",
	"GRAPH_CONTEXT_DEPTH",
	"GRAPH_CONTEXT_SCORE",
	"GRAPH_EXTRACT_CONCURRENCY",
	"GRAPH_EXTRACT_INPUTS_JSON",
	"GRAPH_EXTRACTION_JOBS_PER_OWNER",
	"GRAPH_EXTRACTION_MAX_FAILED_FRACTION",
	"GRAPH_EXTRACT_WORKFLOW_NAME",
	"GRAPH_MAX_TRIPLES_PER_COLLECTION",
	"GRAPH_MIN_CONFIDENCE",
	"GRAPH_QUERY_ENTITY_MAX",
	"GRAPH_QUERY_NGRAM_MAX",
	"IDENTITY_CACHE_TTL_SECONDS",
	"IDENTITY_CLOCK_SKEW_SECONDS",
	"IDENTITY_HTTP_TIMEOUT_SECONDS",
	"IDENTITY_ISSUER_ALLOWLIST",
	"IDENTITY_KEY_CACHE_TTL_SECONDS",
	"IDENTITY_OIDC_ALLOWED_ISSUERS",
	"IDENTITY_OIDC_CLIENT_IDS",
	"IDENTITY_OIDC_ISSUER",
	"IDENTITY_OIDC_ISSUER_LABEL",
	"IDENTITY_PROVIDER",
	"INGEST_BACKEND",
	"INGEST_CONCURRENCY",
	"INGEST_SHARD_SIZE",
	"JOB_STORE_BACKEND",
	"KG_EXTRACTION_ENABLED",
	"KG_EXTRACTION_MAX_CHUNKS",
	"KG_EXTRACTION_MAX_TRIPLES_PER_CHUNK",
	"LATENCY_ROLLUP_SECONDS",
	"LLM_MAX_CONTEXT_CHARS",
	"LLM_MODEL",
	"LOG_DAMPEN_LOGGERS",
	"LOG_FORMAT",
	"LOG_LEVEL",
	"MAX_CHUNK_IDS",
	"MAX_CHUNKS_PER_COLLECTION",
	"MAX_COLLECTIONS",
	"MAX_COLLECTIONS_PER_OWNER",
	"MAX_DOCUMENT_BYTES",
	"MAX_JSON_BODY_BYTES",
	"MAX_LIST_LIMIT",
	"MAX_TOP_K",
	"MAX_UPLOAD_BYTES_PER_REQUEST",
	"MAX_UPLOAD_FILES",
	"MULTIQUERY_N",
	"NEO4J_DATABASE",
	"NEO4J_USER",
	"PUBLISHER_PROFILE",
	"QDRANT_COLLECTION",
	"QDRANT_COLLECTION_EXPLICIT",
	"QDRANT_COLLECTION_ROUTES",
	"QDRANT_POSTMORTEM_PROBE",
	"QDRANT_TIMEOUT",
	"QDRANT_UPSERT_BATCH_SIZE",
	"QDRANT_UPSERT_CONCURRENCY",
	"RATE_LIMIT_COLLECTIONS_CREATE_PER_HOUR",
	"RATE_LIMIT_INGEST_PER_HOUR",
	"RATE_LIMIT_SHARES_PER_HOUR",
	"REQUIRE_DURABLE_BACKENDS",
	"RERANK_CANDIDATES",
	"RERANK_ENABLED",
	"RERANKER_MODEL",
	"RETRIEVAL_CANDIDATE_MULTIPLIER",
	"RETRIEVAL_DEMOTE_BOILERPLATE",
	"RETRIEVAL_MAX_PER_DOC",
	"RRF_K",
	"SERVICE_ACCOUNT_DISABLED_CACHE_TTL_SECONDS",
	"TENANT_COLLECTIONS",
	"TENANT_MAX_CONCURRENCY",
	"TEXT_BACKEND",
	"TOP_K",
	"UPLOAD_CONTENT_TYPES",
	"USER_STORE_BACKEND",
	"VECTOR_BACKEND",
	"WORKSPACE_TIMEOUT",
)

// Classify classifies key. Precedence: explicit secrets, then the secret
// pattern (minus the reviewed exceptions), then executable surface, then the
// public table; anything else is Unsupported.
func Classify(key string) Class {
	key = strings.TrimSpace(key)
	if key == "" {
		return Unsupported
	}
	if secretExplicit[key] {
		return Secret
	}
	if secretPattern.MatchString(key) && !publicDespitePattern[key] {
		return Secret
	}
	if executableSurface[key] {
		return ExecutableSurface
	}
	if public[key] {
		return Public
	}
	return Unsupported
}

// IsSecret is Classify(key) == Secret.
func IsSecret(key string) bool { return Classify(key) == Secret }

// Redact returns value unchanged for non-secret keys and "<REDACTED>" for
// secret keys (an empty secret stays empty so "unset" remains observable).
func Redact(key, value string) string {
	if IsSecret(key) && value != "" {
		return Redacted
	}
	return value
}

// Redacted is the placeholder every redactor substitutes.
const Redacted = "<REDACTED>"

// PublicKeys returns the sorted public table (for docs and the env API).
func PublicKeys() []string { return keys(public) }

// ExecutableSurfaceKeys returns the sorted executable-surface table.
func ExecutableSurfaceKeys() []string { return keys(executableSurface) }

func set(ks ...string) map[string]bool {
	m := make(map[string]bool, len(ks))
	for _, k := range ks {
		m[k] = true
	}
	return m
}

func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
