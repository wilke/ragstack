package storeroots

import "testing"

// The shared grammar and overlap rule (review F4): the api and doctor
// packages both call these, so their own tests exercise the same code; this
// pins the package's own contract.
func TestParseCheckGoverning(t *testing.T) {
	if got, err := Parse(""); err != nil || got != nil {
		t.Errorf("Parse(\"\") = %v, %v; want nil", got, err)
	}
	if got, err := Parse("/qdrant,/vol2"); err != nil || len(got) != 2 {
		t.Errorf("Parse = %v, %v", got, err)
	}
	for _, bad := range []string{"/", "rel", "/a/../b", "/a/", "/a,/a", "/a:rw", "/a,,/b", "/a b"} {
		if _, err := Parse(bad); err == nil {
			t.Errorf("Parse(%q) accepted", bad)
		}
	}
	approved := []string{"/rag/data/tenants"}
	bind := []string{"/rag/cache"}
	if err := Check([]string{"/qdrant"}, approved, bind); err != nil {
		t.Errorf("Check(/qdrant) = %v", err)
	}
	for _, bad := range [][]string{{"/rag/data"}, {"/rag/data/tenants/x"}, {"/rag/cache"}, {"/rag"}, {"/q", "/q/x"}} {
		if err := Check(bad, approved, bind); err == nil {
			t.Errorf("Check(%v) accepted", bad)
		}
	}
	if g := Governing("/qdrant/tenants/dev", []string{"/vol2", "/qdrant"}); g != "/qdrant" {
		t.Errorf("Governing = %q", g)
	}
	if g := Governing("/qdrant", []string{"/qdrant"}); g != "" {
		t.Errorf("Governing of the root itself = %q, want \"\"", g)
	}
}
