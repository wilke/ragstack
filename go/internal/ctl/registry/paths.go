package registry

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// Paths is THE derivation of a registry row's host paths: paths.TenantPaths
// for the row's name/manifest_name, with stores.qdrant.data_dir applied.
// Every caller that lays out a registered tenant goes through here, so a row
// that moves its qdrant dir moves it for the renderer, the ops, adopt, doctor
// and the API alike (it lives in this package because paths cannot import
// registry). A row without data_dir gets exactly paths.TenantPaths.
func (t *Tenant) Paths(roots paths.Roots) paths.Tenant {
	return paths.TenantPaths(roots, t.Name, t.ManifestName).WithQdrantDataDir(t.Stores.Qdrant.DataDir)
}

// QdrantDataDirAllowed is the containment rule for stores.qdrant.data_dir
// (PR2): a row's moved qdrant dir is honoured only when it is STRICTLY under
// one of storeRoots (the daemon's CTL_STORE_DATA_ROOTS) or strictly under the
// row's own data_dir. Anything else — a dir under another ctl root, a home
// directory, a store root itself — is refused, and the error says which roots
// would have admitted it. A row without the field is always allowed.
//
// It is the ONE statement of the rule: the ops that start or back up the store
// refuse with it and doctor's qdrant_data_dir_outside_roots reports it, so the
// two cannot disagree about which rows are usable. The roots are passed in
// rather than read here because they are configuration, and this package knows
// none.
func (t *Tenant) QdrantDataDirAllowed(storeRoots []string) error {
	q := t.Stores.Qdrant.DataDir
	if q == "" {
		return nil
	}
	allowed := append([]string{t.DataDir}, storeRoots...)
	for _, r := range allowed {
		if r == "" {
			continue
		}
		if _, err := paths.SafePath(filepath.Clean(r), q); err == nil {
			return nil
		}
	}
	configured := "none are configured"
	if len(storeRoots) > 0 {
		configured = strings.Join(storeRoots, ", ")
	}
	return fmt.Errorf("stores.qdrant.data_dir %s of %s is neither strictly under the row's data_dir %s nor "+
		"strictly under a store data root (CTL_STORE_DATA_ROOTS: %s); add its volume to CTL_STORE_DATA_ROOTS "+
		"in ctl.env and restart the daemon, or move the store back", q, t.Name, t.DataDir, configured)
}
