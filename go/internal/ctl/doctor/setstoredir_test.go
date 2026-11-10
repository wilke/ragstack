package doctor

import (
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// `set-store-dir` (PR3) is the repair for a row whose moved qdrant dir doctor
// reports, and it runs on a STOPPED tenant: none of those findings, and not
// port_not_listening, may refuse it or demand an acknowledgement. The
// registry-projection findings still gate it, as they gate set-bind.
func TestSetStoreDirPreconditions(t *testing.T) {
	if !KnownOp("set-store-dir") {
		t.Fatal("set-store-dir has no precondition row")
	}
	repaired := []string{PortNotListening, QdrantDataDirMissing, QdrantDataDirSymlink, QdrantDataDirOutsideRoots,
		QdrantDataDirRootNotMounted}
	gate := map[string]bool{}
	for _, c := range GateCodes("set-store-dir", "") {
		gate[c] = true
	}
	for _, c := range repaired {
		if gate[c] {
			t.Errorf("GateCodes(set-store-dir) contains %s, a finding the op repairs or runs under", c)
		}
	}
	for _, c := range []string{RegistryManifestMismatch, ManifestUnknownRow} {
		if !gate[c] {
			t.Errorf("GateCodes(set-store-dir) lacks %s", c)
		}
	}
	var in []model.Finding
	for _, c := range repaired {
		in = append(in, model.Finding{Level: model.LevelError, Code: c, Tenant: "dev"})
	}
	for _, f := range applyPreconditions(in, "set-store-dir", "", nil) {
		if f.Level != model.LevelWarn {
			t.Errorf("%s stays %s for set-store-dir, want warn", f.Code, f.Level)
		}
	}
}
