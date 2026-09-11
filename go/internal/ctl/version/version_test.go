package version

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestInfoDefaults(t *testing.T) {
	i := Info()
	if i.Version != "dev" {
		t.Fatalf("Version = %q, want dev (ldflags unset)", i.Version)
	}
	if i.Go == "" {
		t.Fatal("Go runtime version empty")
	}
	if i.SchemaVersion != SchemaVersion || SchemaVersion < 1 {
		t.Fatalf("SchemaVersion = %d", i.SchemaVersion)
	}
	b, err := json.Marshal(i)
	if err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{`"version"`, `"commit"`, `"built_at"`, `"go"`, `"schema_version"`} {
		if !strings.Contains(string(b), k) {
			t.Errorf("json missing %s: %s", k, b)
		}
	}
}
