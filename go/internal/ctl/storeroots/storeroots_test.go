package storeroots

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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

// Second review item 3: a store root that RESOLVES into an approved or bind
// root is refused although the written paths are disjoint; one that resolves
// to a separate volume stays legal.
func TestCheckComparesResolvedSpellings(t *testing.T) {
	base := t.TempDir()
	approved := filepath.Join(base, "rag", "data", "tenants")
	bind := filepath.Join(base, "rag", "cache")
	vol := filepath.Join(base, "mnt", "vol")
	for _, d := range []string{filepath.Join(approved, "x"), bind, vol} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	link := func(name, target string) string {
		p := filepath.Join(base, name)
		if err := os.Symlink(target, p); err != nil {
			t.Fatal(err)
		}
		return p
	}
	intoTenant := link("qdrant", filepath.Join(approved, "x"))   // /qdrant -> /rag/data/tenants/x
	dataLink := link("data", filepath.Join(base, "rag", "data")) // /data -> /rag/data
	intoBind := link("cachelink", bind)                          // a root resolving to a bind root
	separate := link("qdrant2", vol)                             // /qdrant2 -> /mnt/vol
	for _, bad := range []string{intoTenant, filepath.Join(dataLink, "tenants", "qdrant"), intoBind} {
		if err := Check([]string{bad}, []string{approved}, []string{bind}); err == nil {
			t.Errorf("Check(%s) accepted a store root that resolves into a ctl root", bad)
		} else if !strings.Contains(err.Error(), "resolves to") {
			t.Errorf("Check(%s) = %v, want the resolved spelling named", bad, err)
		}
	}
	if err := Check([]string{separate}, []string{approved}, []string{bind}); err != nil {
		t.Errorf("Check(%s -> separate volume) = %v, want legal", separate, err)
	}
	// /data/qdrant with /data -> /rag/data resolves to /rag/data/qdrant, which
	// is beside the approved /rag/data/tenants, not in it: legal, as the
	// written /rag/data/qdrant would be.
	if err := Check([]string{filepath.Join(dataLink, "qdrant")}, []string{approved}, []string{bind}); err != nil {
		t.Errorf("a store root resolving beside the approved roots = %v, want legal", err)
	}
	// Two store roots that are one directory on disk overlap.
	if err := Check([]string{separate, vol}, []string{approved}, nil); err == nil {
		t.Error("two store roots resolving to the same directory were accepted")
	}
}
