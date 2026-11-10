package ops

// A row's moved qdrant data dir (stores.qdrant.data_dir), PR2: the
// containment rule the ops hold it to, and the host facts the instance
// supervisor checks before it starts a qdrant there.

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/storeroots"
)

// refuseQdrantDataDir is the PLAN-time half: a row whose stores.qdrant.data_dir
// is outside every root that may hold it (registry.Tenant.QdrantDataDirAllowed:
// strictly under a CTL_STORE_DATA_ROOTS entry or under the row's own data_dir)
// is refused before anything is touched. It is lexical — the plan reads
// nothing off the host — and it is the same rule doctor's
// qdrant_data_dir_outside_roots reports.
func (p *planner) refuseQdrantDataDir(verb string) error {
	if err := p.t.QdrantDataDirAllowed(p.op.deps.StoreDataRoots); err != nil {
		return p.refuse("%s of %s's qdrant is refused: %v", verb, p.t.Name, err)
	}
	return nil
}

// qdrantDataDirPrecheck is the RUN-time half, made immediately before the
// instance is started: for a row whose qdrant dir is off the default layout,
// every component from the admitting root down to `<dir>`, and
// `<dir>/storage` and `<dir>/snapshots`, must each exist, be a directory and
// not be a symlink, and — for a dir outside the rag root — the
// deepest mount holding it must not be `/`. A failure is a refusal and nothing
// is started.
//
// Why each one: apptainer refuses a bind whose source is missing, but only
// after the step has recorded an instance it then cannot start; a symlink is a
// store whose real location the registry does not record (and the driver
// refuses a symlinked leaf anyway, with a less useful sentence); and a store
// volume that is not mounted leaves the dir ON THE ROOT FILESYSTEM, where a
// qdrant would quietly start empty and fill `/`. A dir under the rag root is
// on /rag's own mount, which everything else the ctl runs is already on — the
// same exemption doctor's qdrant_data_dir_root_not_mounted makes.
func qdrantDataDirPrecheck(ctx context.Context, sc *jobs.StepContext, tp paths.Tenant, ragRoot string,
	storeRoots []string, mountOf func(string) (string, string, error)) (string, error) {
	if !tp.QdrantOffLayout() {
		return "", nil
	}
	dir := tp.QdrantDir
	// EVERY component from the root that admits the dir (a store root, or
	// the row's own data_dir) down to the dir is checked, not just the leaf:
	// `ln -s /rag/data/tenants/victim /qdrant/tenants` leaves dir, storage/
	// and snapshots/ all real directories while the store runs inside
	// another tenant's tree (review F1). The root itself may be a link — that
	// is the operator's layout — but nothing below it may.
	root := storeroots.Governing(dir, storeRoots)
	if root == "" {
		if _, err := paths.SafePath(filepath.Clean(tp.DataDir), dir); err == nil {
			root = filepath.Clean(tp.DataDir)
		}
	}
	if root == "" {
		return "", fmt.Errorf("%w: the moved qdrant dir %s is under no store data root and not under %s",
			jobs.ErrRefused, dir, tp.DataDir)
	}
	rel, _ := filepath.Rel(root, dir)
	var chain []string
	cur := root
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		cur = filepath.Join(cur, part)
		chain = append(chain, cur)
	}
	for _, p := range append(chain, tp.QdrantStorage, tp.QdrantSnapshots) {
		st, err := sc.Ops.Drivers.Files().Stat(ctx, p)
		switch {
		case errors.Is(err, fs.ErrNotExist):
			return "", fmt.Errorf("%w: the moved qdrant dir needs %s, and it does not exist; create it (or "+
				"finish moving the store there) before starting — apptainer refuses a bind whose source is missing",
				jobs.ErrRefused, p)
		case err != nil:
			return "", fmt.Errorf("%w: checking %s before starting qdrant: %v", jobs.ErrRefused, p, err)
		case st.IsSymlink:
			return "", fmt.Errorf("%w: %s is a symlink; a moved qdrant dir must be the directory itself, or the "+
				"store's real location is not the one the registry records", jobs.ErrRefused, p)
		case !st.IsDir:
			return "", fmt.Errorf("%w: %s is not a directory", jobs.ErrRefused, p)
		}
	}
	if ragRoot != "" {
		if _, err := paths.SafePath(filepath.Clean(ragRoot), dir); err == nil {
			return "moved qdrant dir " + dir + " is in place (storage/ and snapshots/ are directories, under " +
				ragRoot + ")", nil
		}
	}
	if mountOf == nil {
		return "", fmt.Errorf("%w: the moved qdrant dir %s is outside %s and this engine has no mount table to "+
			"check it against; it is not started on a filesystem nobody looked at", jobs.ErrRefused, dir, ragRoot)
	}
	// MountOf resolves the dir through a symlinked store root first
	// (hostfacts), so /qdrant -> /mnt/vol answers the volume's mount (F3). A
	// store root that really is on the root filesystem is still refused.
	mp, fsType, err := mountOf(dir)
	if err != nil {
		return "", fmt.Errorf("%w: finding the mount that holds %s: %v", jobs.ErrRefused, dir, err)
	}
	if mp == "/" {
		return "", fmt.Errorf("%w: the deepest mount holding the moved qdrant dir %s is the root filesystem (/, "+
			"%s): the store volume it was moved to is not mounted, and a qdrant started there would write onto "+
			"the root fs. Mount the volume, then start again", jobs.ErrRefused, dir, fsType)
	}
	return fmt.Sprintf("moved qdrant dir %s is in place (storage/ and snapshots/ are directories, on %s %s)",
		dir, fsType, mp), nil
}
