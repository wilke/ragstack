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

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
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
// `<dir>`, `<dir>/storage` and `<dir>/snapshots` must each exist, be a
// directory and not be a symlink, and — for a dir outside the rag root — the
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
	mountOf func(string) (string, string, error)) (string, error) {
	if !tp.QdrantOffLayout() {
		return "", nil
	}
	dir := tp.QdrantDir
	for _, p := range []string{dir, tp.QdrantStorage, tp.QdrantSnapshots} {
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
