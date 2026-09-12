package settings

import (
	"strings"
	"testing"
)

func TestClassify(t *testing.T) {
	cases := map[string]Class{
		// secrets: explicit
		"API_KEYS": Secret, "API_KEY_TENANTS": Secret, "API_KEY_ROLES": Secret,
		// secrets: pattern
		"QDRANT_API_KEY": Secret, "ELASTICSEARCH_API_KEY": Secret, "OPENAI_API_KEY": Secret,
		"POSTGRES_DSN": Secret, "USER_STORE_DSN": Secret, "COLLECTION_STORE_DSN": Secret,
		"NEO4J_PASSWORD": Secret, "NEO4J_AUTH": Secret, "GOWE_TOKEN": Secret,
		"TENANT_PG_PASSWORD": Secret, "TENANT_API_KEY_USER": Secret, "TENANT_API_KEY_ADMIN": Secret,
		"SOME_SECRET": Secret, "X_TOKEN_Y": Secret, "FOO_KEY": Secret,
		// executable surface
		"PYTHONPATH": ExecutableSurface, "PATH": ExecutableSurface, "HF_HOME": ExecutableSurface,
		"INGEST_ROOT": ExecutableSurface, "COLLECTION_MANIFEST_DIR": ExecutableSurface,
		"USER_STORE_PATH": ExecutableSurface, "JOB_STORE_PATH": ExecutableSurface, "COLLECTION_STORE_PATH": ExecutableSurface,
		"COLLECTIONS_FILE": ExecutableSurface, "MODELS_REGISTRY_FILE": ExecutableSurface, "GOWE_WORKFLOW_CWL": ExecutableSurface,
		"QDRANT_URL": ExecutableSurface, "ELASTICSEARCH_URL": ExecutableSurface, "NEO4J_URI": ExecutableSurface,
		"EMBEDDING_ENDPOINTS": ExecutableSurface, "EMBEDDING_SIDECAR_URL": ExecutableSurface, "CROSSENCODER_SIDECAR_URL": ExecutableSurface,
		"LLM_ENDPOINT": ExecutableSurface, "GOWE_URL": ExecutableSurface, "WORKSPACE_URL": ExecutableSurface,
		"MODEL_URL_ALLOWLIST": ExecutableSurface, "PORT": ExecutableSurface, "ROOT_PATH": ExecutableSurface,
		// public (config.py Settings fields)
		"DEFAULT_ROLE": Public, "IDENTITY_PROVIDER": Public, "ADMIN_SUBJECTS": Public, "MAX_COLLECTIONS": Public,
		"USER_STORE_BACKEND": Public, "ACL_BACKFILL_OWNER": Public, "VECTOR_BACKEND": Public, "TEXT_BACKEND": Public,
		"GRAPH_BACKEND": Public, "JOB_STORE_BACKEND": Public, "COLLECTION_STORE_BACKEND": Public, "EMBEDDING_API": Public,
		"EMBEDDING_MODEL": Public, "EMBEDDING_MODEL_DIM": Public, "RERANK_ENABLED": Public, "LLM_MODEL": Public,
		"REQUIRE_DURABLE_BACKENDS": Public, "MAX_DOCUMENT_BYTES": Public, "LOG_LEVEL": Public, "INGEST_BACKEND": Public,
		"GOWE_WORKER_GROUP": Public, "NEO4J_USER": Public, "MAX_COLLECTIONS_PER_OWNER": Public, "MAX_CHUNKS_PER_COLLECTION": Public,
		"ALLOW_USER_COLLECTION_CREATE": Public, "RATE_LIMIT_COLLECTIONS_CREATE_PER_HOUR": Public, "RATE_LIMIT_INGEST_PER_HOUR": Public,
		"QDRANT_COLLECTION_EXPLICIT": Public, "ELASTICSEARCH_INDEX": Public, "QDRANT_TIMEOUT": Public, "ELASTICSEARCH_TIMEOUT": Public,
		"DEFAULT_COLLECTION_ID": Public, "CHUNK_METHOD": Public, "CHUNK_SIZE": Public, "CHUNK_OVERLAP": Public,
		"DOI_ENRICHMENT_ENABLED": Public, "DOI_ENRICHMENT_MAILTO": Public, "RETRIEVAL_DEMOTE_BOILERPLATE": Public,
		"RETRIEVAL_MAX_PER_DOC": Public, "IDENTITY_KEY_CACHE_TTL_SECONDS": Public,
		// public despite the pattern (reviewed exceptions)
		"CHUNK_MAX_TOKENS": Public, "CHUNK_TOKEN_COUNTER": Public, "EMBEDDING_MAX_BATCH_TOKENS": Public,
		"EMBEDDING_CHARS_PER_TOKEN": Public, "GOWE_RECEIPTS_OUTPUT_KEY": Public, "GOWE_SHARDS_INPUT_KEY": Public,
		// unknown
		"TENANT_ES_HEAP": Unsupported, "TENANT_STORE_KIND": Unsupported, "FOO": Unsupported, "": Unsupported,
		"lowercase": Unsupported,
	}
	for k, want := range cases {
		if got := Classify(k); got != want {
			t.Errorf("Classify(%q) = %s, want %s", k, got, want)
		}
	}
}

func TestTablesAreDisjointAndUpper(t *testing.T) {
	for _, k := range PublicKeys() {
		if k != strings.ToUpper(k) {
			t.Errorf("public key %q not upper-case", k)
		}
		if executableSurface[k] {
			t.Errorf("%q in both public and executable-surface tables", k)
		}
		if Classify(k) != Public {
			t.Errorf("public table entry %q classifies as %s", k, Classify(k))
		}
	}
	for _, k := range ExecutableSurfaceKeys() {
		if Classify(k) != ExecutableSurface {
			t.Errorf("executable-surface entry %q classifies as %s", k, Classify(k))
		}
	}
	for k := range publicDespitePattern {
		if !secretPattern.MatchString(k) {
			t.Errorf("%q is listed as an exception but does not match the secret pattern — drop it", k)
		}
		if !public[k] {
			t.Errorf("%q is an exception but not in the public table", k)
		}
	}
}

func TestRedact(t *testing.T) {
	if got := Redact("API_KEYS", `["abc"]`); got != Redacted {
		t.Errorf("Redact secret = %q", got)
	}
	if got := Redact("API_KEYS", ""); got != "" {
		t.Errorf("empty secret should stay empty, got %q", got)
	}
	if got := Redact("LOG_LEVEL", "INFO"); got != "INFO" {
		t.Errorf("Redact public = %q", got)
	}
}

const (
	canaryA = "9f1c4e8d2b7a6c5e4d3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d" // 64 hex
	canaryB = "legacy-key-value-01"
	canaryC = "revoked0123456789"
	short   = "abc"
)

func TestRedactorSeeds(t *testing.T) {
	r := NewRedactor(canaryA, canaryB, short)
	r.Add(canaryC, canaryB)
	if r.Seeds() != 3 {
		t.Fatalf("Seeds = %d, want 3 (short dropped, duplicate collapsed)", r.Seeds())
	}
	in := "cur=" + canaryA + " legacy=" + canaryB + " revoked=" + canaryC + " short=" + short +
		" twice=" + canaryA + canaryA + " json={\"" + canaryA + "\":\"dev\"}"
	out := r.Redact(in)
	for _, c := range []string{canaryA, canaryB, canaryC} {
		if strings.Contains(out, c) {
			t.Errorf("canary %q survived: %s", c, out)
		}
	}
	if !strings.Contains(out, "short="+short) {
		t.Errorf("short value must not be redacted: %s", out)
	}
	if !strings.Contains(out, "json={\""+Redacted+"\":\"dev\"}") {
		t.Errorf("json context lost: %s", out)
	}
}

func TestRedactorPatterns(t *testing.T) {
	r := NewRedactor()
	sig := strings.Repeat("ab", 64)
	in := strings.Join([]string{
		"un=user@patricbrc.org|tokenid=123|expiry=1|sig=" + sig,
		"API_KEYS='[\"" + canaryA + "\"]'",
		"  API_KEY_TENANTS='{\"" + canaryA + "\":\"dev\"}'",
		"+POSTGRES_DSN=postgresql://u:p@h:5/db",
		"# NEO4J_PASSWORD=hunter2hunter2",
		"-TENANT_API_KEY_USER=" + canaryA,
		"LOG_LEVEL=INFO",
		"QDRANT_URL=http://localhost:24041",
		"CHUNK_MAX_TOKENS=512",
		"GOWE_TOKEN=",
		"text with " + canaryA + " unseeded stays (pattern pass only)",
	}, "\n")
	out := r.Redact(in)
	lines := strings.Split(out, "\n")
	want := []string{
		"un=user@patricbrc.org|tokenid=123|expiry=1|sig=" + Redacted,
		"API_KEYS=" + Redacted,
		"  API_KEY_TENANTS=" + Redacted,
		"+POSTGRES_DSN=" + Redacted,
		"# NEO4J_PASSWORD=" + Redacted,
		"-TENANT_API_KEY_USER=" + Redacted,
		"LOG_LEVEL=INFO",
		"QDRANT_URL=http://localhost:24041",
		"CHUNK_MAX_TOKENS=512",
		"GOWE_TOKEN=",
		"text with " + canaryA + " unseeded stays (pattern pass only)",
	}
	for i := range want {
		if i >= len(lines) || lines[i] != want[i] {
			t.Errorf("line %d:\n got %q\nwant %q", i, lines[i], want[i])
		}
	}
	if strings.Contains(out, sig) || strings.Contains(out, "hunter2") || strings.Contains(out, "u:p@h") {
		t.Errorf("secret survived: %s", out)
	}
	// Idempotent.
	if r.Redact(out) != out {
		t.Errorf("not idempotent")
	}
	if RedactText("QDRANT_API_KEY=x") != "QDRANT_API_KEY="+Redacted {
		t.Errorf("RedactText")
	}
}
