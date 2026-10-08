package drivers

// Files.RemoveTree — the ONLY recursive delete in the control plane (`tenant
// purge`, PR-G1.4).
//
// Every other mutation a driver makes is a write, a rename, or the removal of
// one file or one empty directory; this one takes a whole tree. So its
// containment is narrower than the approved roots every other write is held to,
// and it is decided in two halves that both drivers share:
//
//   - the LEXICAL half (treeShapeOf, below, used by the real and the fake
//     driver alike): the path names exactly one of four deletion roots, is not
//     a root itself, and has the one shape that root's deletable things have;
//   - the FILESYSTEM half, per driver: the leaf is a directory (or, for the one
//     file shape, a regular file) and NOT a symlink, and no component between
//     the root and the leaf is a symlink either.
//
// An absent path is success: a purge interrupted half way is resumed, and the
// step it was in runs again over a tree that is partly or wholly gone.

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// TreeRoots are the four roots Files.RemoveTree may delete under, and the only
// four. A zero field is a root that does not exist: nothing is deletable under
// it, and a driver with all four empty refuses every RemoveTree (fail closed,
// exactly as an empty approved-roots list does for every other write).
type TreeRoots struct {
	// DataDir is the tenant data root (/rag/data/tenants). Only a QUARANTINED
	// tree directly under it is deletable: `<manifest>.quarantined-<stamp>`.
	// A live tenant's data directory never is, whatever is asked.
	DataDir string
	// BackupsDir is the backup root (/rag/backups/tenants): `<tenant>`, or
	// `<tenant>/<bundle-id>` (a bundle directory), or `<tenant>/<bundle-id>.tar`
	// (the bundle's tar — the one FILE shape RemoveTree accepts).
	BackupsDir string
	// ReposDir is the tenant worktree root (/rag/repos/tenants): `<tenant>`.
	ReposDir string
	// UnitsDir is the ctl's rendered-units directory (<CtlConfigDir>/units):
	// `<tenant>`.
	UnitsDir string
}

// TreeRootsOf is the deployment's four deletion roots.
func TreeRootsOf(r paths.Roots) TreeRoots {
	t := TreeRoots{DataDir: r.DataDir, BackupsDir: r.BackupsDir, ReposDir: r.ReposDir}
	if r.CtlConfigDir != "" {
		t.UnitsDir = r.UnitsDir()
	}
	return t
}

// BundleIDPattern is a bundle id: `<YYYYMMDDTHHMMSSZ>-<kind>`. It is the args
// grammar's `patBundleID` (ops/args.go, the contract's restore `from`), and a
// test in the ops package asserts the two are the same string.
const BundleIDPattern = `^[0-9]{8}T[0-9]{6}Z-(backup|pre-update|recovery)$`

var bundleIDRe = regexp.MustCompile(BundleIDPattern)

// quarantineStampRe is the part of a quarantined tree's name after the marker:
// the stamp decommission writes (20260914T093000Z), in the sweep's charset.
var quarantineStampRe = regexp.MustCompile(`^[0-9A-Za-z-]+$`)

// treeShape is what the lexical half decided the leaf must be.
type treeShape struct {
	// Root is the deletion root the path is under, as configured.
	Root string
	// Rel is the path relative to Root.
	Rel string
	// File is true for the one regular-file shape (`<tenant>/<bundle-id>.tar`
	// under BackupsDir); every other shape must be a directory.
	File bool
}

// treeShapeOf is the lexical containment of RemoveTree, shared by both
// drivers so that they cannot disagree about which strings are deletable.
// Every failure is jobs.ErrRefused.
func treeShapeOf(path string, roots TreeRoots) (treeShape, error) {
	refuse := func(format string, a ...any) (treeShape, error) {
		return treeShape{}, fmt.Errorf("%w: RemoveTree %q: %s", jobs.ErrRefused, path, fmt.Sprintf(format, a...))
	}
	if path == "" || !filepath.IsAbs(path) {
		return refuse("not an absolute path")
	}
	if filepath.Clean(path) != path {
		return refuse("not a clean path (it would be %q)", filepath.Clean(path))
	}
	type named struct{ name, root string }
	all := []named{
		{"the tenant data root", roots.DataDir}, {"the backup root", roots.BackupsDir},
		{"the tenant worktree root", roots.ReposDir}, {"the units directory", roots.UnitsDir},
	}
	var hits []named
	var rels []string
	for _, r := range all {
		if r.root == "" {
			continue
		}
		if !filepath.IsAbs(r.root) || filepath.Clean(r.root) != r.root {
			return refuse("%s %q is not an absolute, clean path; RemoveTree deletes under nothing it cannot "+
				"compare exactly", r.name, r.root)
		}
		if path == r.root {
			return refuse("it is %s itself; a deletion root is never deleted", r.name)
		}
		rel, err := filepath.Rel(r.root, path)
		if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, "../") {
			continue
		}
		hits = append(hits, r)
		rels = append(rels, rel)
	}
	switch len(hits) {
	case 0:
		return refuse("outside every deletion root (%s, %s, %s, %s)",
			orNone(roots.DataDir), orNone(roots.BackupsDir), orNone(roots.ReposDir), orNone(roots.UnitsDir))
	case 1:
	default:
		return refuse("it is under %s AND %s; a path two roots claim is one nobody can reason about",
			hits[0].name, hits[1].name)
	}
	root, rel := hits[0].root, rels[0]
	parts := strings.Split(rel, "/")
	shape := treeShape{Root: root, Rel: rel}
	switch root {
	case roots.DataDir:
		// One level, and QUARANTINED: `<manifest>.quarantined-<stamp>`. The
		// live data directory `<manifest>` has no marker, so no request can
		// reach it through this method.
		if len(parts) != 1 {
			return refuse("under the tenant data root only a quarantined tree directly beneath it is deletable")
		}
		i := strings.Index(rel, registry.QuarantineMarker)
		if i <= 0 {
			return refuse("%q carries no %q marker: a live tenant's data is never deleted", rel,
				registry.QuarantineMarker)
		}
		if err := paths.ValidateName(rel[:i]); err != nil {
			return refuse("%q does not begin with a tenant name: %v", rel, err)
		}
		if !quarantineStampRe.MatchString(rel[i+len(registry.QuarantineMarker):]) {
			return refuse("%q has no stamp after the %q marker", rel, registry.QuarantineMarker)
		}
	case roots.BackupsDir:
		if err := paths.ValidateName(parts[0]); err != nil {
			return refuse("%q is not a tenant's backup directory: %v", parts[0], err)
		}
		switch len(parts) {
		case 1:
		case 2:
			id := parts[1]
			if strings.HasSuffix(id, ".tar") {
				id, shape.File = strings.TrimSuffix(id, ".tar"), true
			}
			if !bundleIDRe.MatchString(id) {
				return refuse("%q is not a bundle id (%s) or its .tar", parts[1], BundleIDPattern)
			}
		default:
			return refuse("under the backup root only <tenant>, <tenant>/<bundle-id> and <tenant>/<bundle-id>.tar " +
				"are deletable")
		}
	case roots.ReposDir, roots.UnitsDir:
		if len(parts) != 1 {
			return refuse("under %s only a tenant's own directory directly beneath it is deletable", hits[0].name)
		}
		if err := paths.ValidateName(rel); err != nil {
			return refuse("%q is not a tenant name: %v", rel, err)
		}
	}
	return shape, nil
}

func orNone(s string) string {
	if s == "" {
		return "(none)"
	}
	return s
}
