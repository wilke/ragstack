package registry

import "github.com/ragstack/ragstack/internal/ctl/paths"

// Paths is THE derivation of a registry row's host paths: paths.TenantPaths
// for the row's name/manifest_name, with stores.qdrant.data_dir applied.
// Every caller that lays out a registered tenant goes through here, so a row
// that moves its qdrant dir moves it for the renderer, the ops, adopt, doctor
// and the API alike (it lives in this package because paths cannot import
// registry). A row without data_dir gets exactly paths.TenantPaths.
func (t *Tenant) Paths(roots paths.Roots) paths.Tenant {
	return paths.TenantPaths(roots, t.Name, t.ManifestName).WithQdrantDataDir(t.Stores.Qdrant.DataDir)
}
