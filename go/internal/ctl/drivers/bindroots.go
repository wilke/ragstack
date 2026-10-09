package drivers

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// BindRoot is one directory the instance driver may bind from beyond the
// approved roots (RealOptions.ExtraBindRoots, CTL_API_BIND_ROOTS; PR-F).
//
// The roots the drivers are built with are where the ctl WRITES; an API
// instance also has to SEE directories no op writes under — the HF cache it
// fills itself, GoWe's image directories it hashes, the /rag/config file a
// tenant's COLLECTIONS_FILE names. Those are widened here, by daemon
// configuration only, and each says whether a bind under it may be writable.
type BindRoot struct {
	// Path is absolute and clean, and never "/".
	Path string
	// ReadOnly refuses every bind under Path that is not `:ro`.
	ReadOnly bool
}

// String is the CTL_API_BIND_ROOTS spelling: `<path>:ro` or `<path>:rw`.
func (r BindRoot) String() string {
	if r.ReadOnly {
		return r.Path + ":ro"
	}
	return r.Path + ":rw"
}

// CheckBindRoot holds an extra bind root to the rules every other root
// follows: absolute, filepath.Clean-stable (so no `..`, no trailing slash, no
// `//`), within the safe path charset — and not "/", which would approve every
// path on the host.
func CheckBindRoot(r BindRoot) error {
	if r.Path == "" {
		return fmt.Errorf("%w: a bind root has no path", jobs.ErrRefused)
	}
	if r.Path == "/" {
		return fmt.Errorf("%w: / is not a bind root; it would approve every path on this host", jobs.ErrRefused)
	}
	if _, err := paths.SafePath("/", r.Path); err != nil {
		return fmt.Errorf("%w: bind root: %v", jobs.ErrRefused, err)
	}
	return nil
}

// containBind is the containment check for the HOST side of one bind, and the
// path it returns is the one that goes on the argv.
//
// A bind is accepted when its host path is strictly under one of i.Roots (the
// pre-PR-F rule, unchanged: resolved parent, no symlink at the leaf), or
// under — or exactly — one of i.ExtraRoots. Then the MOST SPECIFIC root that
// contains it decides the mode: a read-only extra root refuses a bind that is
// not `:ro`, and an approved root (rw by construction) more specific than a
// read-only extra root keeps the bind it always allowed. On a tie the stricter
// root wins.
func (i *RealInstances) containBind(host string, readOnly bool) (string, error) {
	for _, r := range i.ExtraRoots {
		if err := CheckBindRoot(r); err != nil {
			return "", fmt.Errorf("%w: this instance driver was configured with an unusable extra bind root %q: %v",
				jobs.ErrRefused, r.Path, err)
		}
	}
	all := append([]string(nil), i.Roots...)
	for _, r := range i.ExtraRoots {
		all = append(all, r.Path)
	}
	resolved, err := resolvedContainedNoLeafLink(host, all)
	if err != nil {
		// The one shape the strict check cannot accept: an extra root bound
		// WHOLE. It is matched textually against the configured root (which
		// CheckBindRoot has already held to the clean-path rules), and the
		// argv names its resolved spelling, as for every other bind.
		itself, ok := i.extraRootItself(host)
		if !ok {
			return "", err
		}
		resolved = itself
	}
	if ro, root := i.governingRootIsReadOnly(resolved); ro && !readOnly {
		return "", fmt.Errorf("%w: %s is under the read-only bind root %s; bind it with :ro",
			jobs.ErrRefused, host, root)
	}
	return resolved, nil
}

// extraRootItself answers the resolved spelling of host when host IS one of
// the extra roots, written exactly as configured.
func (i *RealInstances) extraRootItself(host string) (string, bool) {
	for _, r := range i.ExtraRoots {
		if host != r.Path {
			continue
		}
		real, err := resolveDir(host)
		if err != nil {
			return "", false
		}
		// The root must be a DIRECTORY that exists: a bind of something that
		// is not there fails in apptainer anyway, and a root that is a file is
		// not a root.
		st, err := os.Stat(real)
		if err != nil || !st.IsDir() {
			return "", false
		}
		return real, true
	}
	return "", false
}

// governingRootIsReadOnly finds the most specific root (approved or extra, in
// both its written and its resolved spelling) that contains or equals path,
// and reports whether it is a read-only extra root.
func (i *RealInstances) governingRootIsReadOnly(path string) (bool, string) {
	best, bestRO, bestName := -1, false, ""
	consider := func(root string, ro bool) {
		for _, spelling := range resolveRoots([]string{root}) {
			if !underOrAt(path, spelling) {
				continue
			}
			n := len(spelling)
			if n > best || (n == best && ro && !bestRO) {
				best, bestRO, bestName = n, ro, root
			}
		}
	}
	for _, r := range i.Roots {
		consider(r, false)
	}
	for _, r := range i.ExtraRoots {
		consider(r.Path, r.ReadOnly)
	}
	return bestRO, bestName
}

// underOrAt is paths.SafePath's "strictly under", or equal.
func underOrAt(path, root string) bool {
	if filepath.Clean(root) == path {
		return true
	}
	_, err := paths.SafePath(root, path)
	return err == nil
}
