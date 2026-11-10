package registry

import (
	"strings"
	"testing"
)

// PR2's containment rule for stores.qdrant.data_dir: strictly under a store
// data root, or strictly under the row's own data_dir; nothing else.
func TestQdrantDataDirAllowed(t *testing.T) {
	row := func(q string) *Tenant {
		tn := &Tenant{Name: "dev", DataDir: "/rag/data/tenants/dev"}
		tn.Stores.Qdrant.DataDir = q
		return tn
	}
	stores := []string{"/qdrant", "/vol2/stores"}
	for _, c := range []struct {
		q     string
		roots []string
		ok    bool
	}{
		{"", nil, true}, // no field: always allowed
		{"/rag/data/tenants/dev/qdrant-b", nil, true},      // under its own data_dir
		{"/qdrant/tenants/dev", stores, true},              // under a store root
		{"/vol2/stores/dev", stores, true},                 // under the second
		{"/qdrant/tenants/dev", nil, false},                // no store root configured
		{"/qdrant", stores, false},                         // the store root itself
		{"/qdrantx/dev", stores, false},                    // a prefix is not containment
		{"/rag/backups/tenants/dev/qdrant", stores, false}, // another ctl root is not enough any more
		{"/rag/data/tenants/other/qdrant", stores, false},  // another tenant's tree
		{"/home/wilke/qdrant", stores, false},              // a home directory
	} {
		err := row(c.q).QdrantDataDirAllowed(c.roots)
		if c.ok && err != nil {
			t.Errorf("data_dir %q with roots %v = %v, want allowed", c.q, c.roots, err)
		}
		if !c.ok {
			if err == nil {
				t.Errorf("data_dir %q with roots %v allowed, want refused", c.q, c.roots)
			} else if !strings.Contains(err.Error(), c.q) || !strings.Contains(err.Error(), "CTL_STORE_DATA_ROOTS") {
				t.Errorf("the refusal must name the dir and the setting: %v", err)
			}
		}
	}
}
