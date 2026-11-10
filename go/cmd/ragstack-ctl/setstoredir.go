package main

// `ragstack-ctl tenant set-store-dir` (PR3 of the per-qdrant host data dir):
// repoint a row's stores.qdrant.data_dir. The plan and every refusal are in
// internal/ctl/ops/setstoredir.go; this file translates flags into the op's
// args and refuses, before anything is submitted, what only the CLI can get
// wrong.

import (
	"flag"
	"fmt"
	"path"

	"github.com/ragstack/ragstack/internal/ctl/ops"
)

func setStoreDirUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant set-store-dir <name> --store qdrant (--dir ABS_PATH | --clear) [--direct] [--dry-run] [--json]

Repoints the registry's stores.qdrant.data_dir for one tenant and NOTHING else:
no data is copied, moved or removed, and no process is started. Move the store
first (docs/runbooks/qdrant-data-dir.md: fenced backup, census, rsync, verify),
then run this, then `+"`ragstack-ctl tenant start <name>`"+`.

  --dir ABS_PATH  point the row at a moved qdrant dir holding storage/ and
                  snapshots/. Refused unless the tenant's qdrant is exclusive
                  and STOPPED (its instance not running, nothing on its ports),
                  the path is absolute and clean, strictly under a
                  CTL_STORE_DATA_ROOTS entry or under the row's own data_dir,
                  not the default <data_dir>/qdrant (that is --clear), not
                  another row's qdrant dir and not inside another row's trees,
                  and <dir>, <dir>/storage and <dir>/snapshots are real
                  directories (not symlinks) on a mounted volume.
  --clear         remove the field: back to <data_dir>/qdrant. Refused unless
                  the qdrant is stopped and <data_dir>/qdrant/storage is a real
                  directory (the default location must hold the store).

Both set restart_pending. Setting the value already recorded is a no-op
success. CLI-only and --direct only (implied; --server is refused): run it on
the host as the account that owns the row, e.g.
  ops/coconut/ctl-as-svc.sh tenant set-store-dir dev --store qdrant \
    --dir /qdrant/tenants/dev --dry-run

A registry row carrying stores.qdrant.data_dir is refused by every ctl binary
older than the field: every ragstack-ctl on the host (daemon, CLI, cron) must
be this build or newer. `+"`ragstack-ctl registry validate`"+` checks the registry.
Common op flags: %s
`, opFlagSummary)
	return exitUsage
}

// cmdTenantSetStoreDir is `ragstack-ctl tenant set-store-dir <name> …`.
func cmdTenantSetStoreDir(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant set-store-dir", flag.ContinueOnError)
	fs.SetOutput(stderr)
	store := fs.String("store", "", "the store whose host directory is repointed (qdrant)")
	dir := fs.String("dir", "", "the moved qdrant data dir (absolute, clean; holds storage/ and snapshots/)")
	clear := fs.Bool("clear", false, "remove the field: back to <data_dir>/qdrant")
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)

	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return setStoreDirUsage()
	}
	opArgs, err := setStoreDirArgs(*store, *dir, *clear, setFlags(fs)["dir"])
	if err != nil {
		return usageErr("set-store-dir: %v", err)
	}
	if code := refuseServerFlag(fs, "tenant set-store-dir", "it names the host directory a tenant's qdrant is "+
		"bound from, which ADR-0007 keeps off every HTTP surface; the daemon refuses the verb (422) and its "+
		"planner refuses any engine but a --direct one"); code != exitOK {
		return code
	}
	*o.direct = true
	rc := submitOp(o, tenantOpTarget(pos[0], "set-store-dir"), opArgs)
	printSetStoreDirWarning(rc, *o.dryRun)
	return rc
}

// setStoreDirArgs builds the op's args, refusing what the op would refuse
// anyway but the CLI can say more plainly. dirGiven is whether --dir was on
// the command line at all (an empty --dir is an error, not "absent").
func setStoreDirArgs(store, dir string, clear, dirGiven bool) (map[string]any, error) {
	if store == "" {
		return nil, fmt.Errorf("--store is required (qdrant)")
	}
	if store != "qdrant" {
		return nil, fmt.Errorf("--store %q: only qdrant has a configurable host directory", store)
	}
	switch {
	case dirGiven && clear:
		return nil, fmt.Errorf("--dir and --clear are exclusive")
	case !dirGiven && !clear:
		return nil, fmt.Errorf("one of --dir ABS_PATH or --clear is required")
	case clear:
		return map[string]any{"store": store, "clear": true}, nil
	}
	if !path.IsAbs(dir) || path.Clean(dir) != dir {
		return nil, fmt.Errorf("--dir %q is not an absolute, clean path", dir)
	}
	return map[string]any{"store": store, "dir": dir}, nil
}

// printSetStoreDirWarning is the one line after a successful run: every other
// ctl binary on the host must be able to read the row this wrote.
func printSetStoreDirWarning(rc int, dryRun bool) {
	if rc == exitOK && !dryRun {
		fmt.Fprintln(stderr, ops.OtherBinariesWarning)
	}
}
