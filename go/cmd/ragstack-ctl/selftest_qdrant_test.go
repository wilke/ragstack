package main

import (
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// A sandbox row never carries stores.qdrant.data_dir: the selftest says so
// as a check, and fails it when one does.
func TestSandboxQdrantDirCheck(t *testing.T) {
	row := &registry.Tenant{Name: "ctltest-x", DataDir: "/rag/data/tenants/ctltest-x"}
	if c := sandboxQdrantDirCheck(row); c.Verdict != checkPass {
		t.Errorf("a sandbox on the standard layout = %+v, want pass", c)
	}
	row.Stores.Qdrant.DataDir = "/qdrant/tenants/ctltest-x"
	if c := sandboxQdrantDirCheck(row); c.Verdict != checkFail {
		t.Errorf("a sandbox with a moved qdrant dir = %+v, want fail", c)
	}
}
