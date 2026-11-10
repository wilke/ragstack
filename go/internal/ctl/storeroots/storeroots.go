// Package storeroots is CTL_STORE_DATA_ROOTS' grammar and its overlap rule,
// in one place below every package that reads the setting.
//
// The api package (daemon, --direct), the drivers (NewReal), `fleet grant`
// and doctor all need the same answer to "is this list usable". doctor cannot
// import api or drivers (both import doctor), so the rule lives here, with no
// dependency but paths, and every reader calls it: a standalone doctor that
// accepted a list the daemon refuses would be green over a daemon that will
// not start.
package storeroots

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// Env is the ctl.env key.
const Env = "CTL_STORE_DATA_ROOTS"

// Parse parses a CTL_STORE_DATA_ROOTS value. Blank is nil: no store roots.
//
// Every entry must be an absolute directory that is already clean (no `..`,
// no trailing or doubled slash — nothing is normalised, because a store root
// is compared exactly against registry rows), in the safe path charset, not
// `/`, and listed once. A `:ro`/`:rw` suffix is refused as such: a store root
// is read/write by definition, and a suffix copied from CTL_API_BIND_ROOTS is
// an operator's mistake that must be loud. Overlap is Check's.
func Parse(v string) ([]string, error) {
	if strings.TrimSpace(v) == "" {
		return nil, nil
	}
	var out []string
	seen := map[string]bool{}
	for _, raw := range strings.Split(v, ",") {
		entry := strings.TrimSpace(raw)
		if entry == "" {
			return nil, fmt.Errorf("%s=%q has an empty entry", Env, v)
		}
		if strings.Contains(entry, ":") {
			return nil, fmt.Errorf("%s entry %q: a store root takes no :ro/:rw suffix (it is read/write by "+
				"definition) and no other colon", Env, entry)
		}
		if !filepath.IsAbs(entry) {
			return nil, fmt.Errorf("%s entry %q: the path must be absolute", Env, entry)
		}
		for _, part := range strings.Split(entry, "/") {
			if part == ".." {
				return nil, fmt.Errorf("%s entry %q: the path may not contain ..", Env, entry)
			}
		}
		if filepath.Clean(entry) != entry {
			return nil, fmt.Errorf("%s entry %q: the path is not clean (write %q)", Env, entry, filepath.Clean(entry))
		}
		if err := checkRoot(entry); err != nil {
			return nil, fmt.Errorf("%s entry %q: %v", Env, entry, err)
		}
		if seen[entry] {
			return nil, fmt.Errorf("%s lists %s twice", Env, entry)
		}
		seen[entry] = true
		out = append(out, entry)
	}
	return out, nil
}

// checkRoot is a bind root's shape: absolute, clean, safe charset, not "/".
func checkRoot(p string) error {
	if p == "" {
		return fmt.Errorf("a store root has no path")
	}
	if p == "/" {
		return fmt.Errorf("/ is not a store root; it would approve every path on this host")
	}
	if _, err := paths.SafePath("/", p); err != nil {
		return err
	}
	return nil
}

// UnderOrAt is paths.SafePath's "strictly under", or equal.
func UnderOrAt(path, root string) bool {
	if filepath.Clean(root) == path {
		return true
	}
	_, err := paths.SafePath(root, path)
	return err == nil
}

// Check holds a store root list to the rules a store root needs before it
// may join the approved roots of the files and instance drivers: each is a
// root's shape; none is listed twice or nested in another; and none equals,
// contains or lies under an approved root (approved) or an API bind root
// (bind, CTL_API_BIND_ROOTS' paths). See drivers.CheckStoreDataRoots for why.
//
// Overlap is judged on BOTH spellings of every path — as written and as it
// resolves on disk (paths.ResolveExisting) — so `/qdrant -> /rag/data/tenants/x`,
// or `/data/qdrant` with `/data -> /rag/data`, is refused although the
// written paths are disjoint. A store root that is a symlink to a separate
// volume (`/qdrant -> /mnt/vol`) is legal: neither spelling meets a ctl root.
func Check(store, approved, bind []string) error {
	type named struct{ written, resolved string }
	spell := func(p string) named { return named{p, paths.ResolveExisting(filepath.Clean(p))} }
	overlap := func(a, b named) bool {
		for _, x := range []string{a.written, a.resolved} {
			for _, y := range []string{b.written, b.resolved} {
				if UnderOrAt(x, y) || UnderOrAt(y, x) {
					return true
				}
			}
		}
		return false
	}
	describe := func(n named) string {
		if n.resolved != n.written {
			return n.written + " (resolves to " + n.resolved + ")"
		}
		return n.written
	}
	var seen []named
	for _, raw := range store {
		if err := checkRoot(raw); err != nil {
			return fmt.Errorf("store data root %q: %v", raw, err)
		}
		r := spell(raw)
		for _, o := range seen {
			if overlap(r, o) {
				return fmt.Errorf("store data roots %s and %s overlap (one is, or is under, the other)",
					describe(o), describe(r))
			}
		}
		seen = append(seen, r)
		for _, a := range approved {
			if a == "" {
				continue
			}
			if an := spell(a); overlap(r, an) {
				return fmt.Errorf("store data root %s overlaps the approved root %s; a store root is a "+
					"directory of its own (a separate volume), never one of, inside or around the ctl's roots",
					describe(r), describe(an))
			}
		}
		for _, b := range bind {
			if b == "" {
				continue
			}
			if bn := spell(b); overlap(r, bn) {
				return fmt.Errorf("store data root %s overlaps the API bind root %s (CTL_API_BIND_ROOTS); "+
					"the most specific root decides a bind's mode, so the two lists may not share a directory",
					describe(r), describe(bn))
			}
		}
	}
	return nil
}

// Governing is the store root (as written) that path is strictly under, or ""
// when it is under none.
func Governing(path string, store []string) string {
	for _, r := range store {
		if _, err := paths.SafePath(r, path); err == nil {
			return r
		}
	}
	return ""
}
