package ops

// The one thing a handover MOVES: a tenant's postgres.
//
// Everywhere else in handover.go the claim holds — a handover moves no data,
// the same directories serve the same processes under another account, through
// the ACLs PR-D2 installed. Postgres is the exception, twice over, and the
// second reason is why this file does what it does rather than the obvious
// thing.
//
// FIRST: postgres compares its data directory's `st_uid` with its own
// `geteuid()` at startup and refuses when they differ —
//
//	FATAL:  data directory "/var/lib/postgresql/data/pgdata" has wrong ownership
//	HINT:  The server must be started by the user that owns the data directory.
//
// — whatever the mode is. That is what killed the second live take of
// `hackathon` on 2026-09-17: qdrant and elasticsearch came up, postgres started
// and exited, and the take waited out its three minutes and then reported
// `pg_isready … no response`.
//
// SECOND, and this is the one that decides the design: the taking account
// cannot READ that directory either, and no ACL can be written that would let
// it. A POSIX ACL's named-user entries are filtered by the MASK, and the mask
// IS the file's group mode bits. On the live tenant:
//
//	$ ls -ld  …/postgres/data/pgdata      drwx------+ wilke cels
//	  access: user_obj::rwx, user:svcbvbrc:rwx, group_obj::---, mask::---
//
// `user:svcbvbrc:rwx` filtered by `mask::---` is an effective `---`. Widening
// the mask means giving the directory group bits in `st_mode`, and a PGDATA
// with group or other bits is one postgres refuses on a different line. So
// there is no configuration in which one account copies another's cluster file
// by file: a physical copy is not merely awkward here, it is unreachable.
//
// What IS reachable is the LOGICAL copy, and postgres has shipped the tools for
// it for thirty years. The release — with the tenant's API already stopped and
// its postgres still running — takes a `pg_dump -Fc` into a plain file at 0640.
// A file has no ownership check of any kind, which is the entire reason this
// works where a copy of the cluster cannot, and 0640 in a directory whose ACL
// mask is `rwx` is readable by the group both accounts are in. The take then
// initialises a cluster OF ITS OWN — the image's entrypoint does it, exactly as
// it did when the tenant was provisioned — and `pg_restore`s the dump into it.
// Nothing but the original's own postgres ever reads the original cluster.
//
// The directory swap is still here, because the bind path is fixed by the row:
//
//	<data_dir>/postgres/data                   ← what the instance binds
//	<data_dir>/postgres/data.<account>-<ts>    ← the new, EMPTY cluster directory
//	<data_dir>/postgres/data.pre-handover-<ts> ← the original, untouched
//	<data_dir>/postgres/handover-<ts>.dump     ← the release's dump
//
// Two renames rather than one exchange, because there is no atomic exchange: a
// crash between them leaves `data` missing with both other names present, which
// is a state the step can finish and report rather than guess at. Both names
// are checkpointed BEFORE the first rename.
//
// The original cluster is never opened, never written and never listed by the
// taking account. That is what makes the rollback and the abandon exact: they
// rename the two names back and the releasing account's postgres starts on the
// bytes it always had.
//
// And the PROOF is row counts. A dump and a restore cannot be compared byte for
// byte, so the release records the exact count of every table beside the dump
// (drivers.Postgres.Census) and the take compares after restoring. A migration
// that moved less than everything fails the take rather than surfacing later as
// a tenant that is quietly short.

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// freshClusterBytes is what an EMPTY PostgreSQL 16 cluster costs: initdb lays
// down the template databases, the first WAL segment and the catalogs before a
// single row exists. About 42 MiB from coconut's postgres.sif; 64 MiB here,
// because the number this guards is a refusal and a refusal wants headroom.
const freshClusterBytes = 64 << 20

// preHandoverPrefix names an original cluster directory after the swap, and
// dumpPrefix names the release's archive beside it.
//
// They are DOCTOR's constants rather than second copies of the strings: doctor
// scans the disk for both long after the commit has cleared the row that named
// them, and the compiler is a better guarantee that the two halves agree than
// any test.
const (
	preHandoverPrefix = doctor.PreHandoverDirPrefix
	dumpPrefix        = doctor.HandoverDumpPrefix
)

// pgMigration is the plan-time description: the paths and the account.
// Timestamps live in the RUN halves — a plan that stamped a name would differ
// between the engine's two Plan calls and every job would be `plan_stale`.
type pgMigration struct {
	// Data is the bind source, `<data_dir>/postgres/data`, which
	// render.StoreArgv binds to /var/lib/postgresql/data. PGDATA is `pgdata`
	// INSIDE it, and nothing here ever touches that: it is the directory only
	// the owning account can read.
	Data string
	// Dir is `<data_dir>/postgres`: the parent the new names are created in
	// and the directory the dump is written to. Its ACL mask is `rwx`, which
	// is what makes a 0640 file in it readable by the other account.
	Dir string
	// Run is the socket directory, `<data_dir>/postgres/run`.
	Run string
	// Spec is how the pg tools reach this tenant's server.
	Spec jobs.PostgresSpec
	// Account is whoever is running the job.
	Account string
}

// pgMigrationFor describes this tenant's postgres, or reports that it has none
// of its own.
func (p *planner) pgMigrationFor(legs []component, account string) (pgMigration, bool) {
	local := false
	for _, c := range legs {
		if c.Leg == render.LegPostgres && c.Managed {
			local = true
		}
	}
	if !local || p.t.Stores.Postgres.Kind != registry.PostgresKindLocal {
		return pgMigration{}, false
	}
	return pgMigration{
		Data:    p.tpaths.PostgresData,
		Dir:     filepath.Dir(p.tpaths.PostgresData),
		Run:     p.tpaths.PostgresRun,
		Spec:    p.postgresSpec(),
		Account: account,
	}, true
}

// ---------------------------------------------------------------- the release

// addPGHandoverSpaceCheck is the release's precondition: is there room to hand
// this postgres over at all.
//
// It runs with everything else that can refuse, before a single process is
// stopped, for the reason the password check gives in as many words — a release
// that discovers the take cannot work discovers it with the tenant already
// down, and free space is the one part of this an operator can still act on at
// that moment.
//
// The arithmetic is the dump plus a fresh cluster, not twice the tree: what
// this handover writes is one compressed archive and one newly initialised
// cluster. `pg_database_size` is the input and is an over-estimate of the
// archive, which is the direction a refusal should err in.
func (p *planner) addPGHandoverSpaceCheck(legs []component) {
	m, ok := p.pgMigrationFor(legs, p.op.deps.owner())
	if !ok {
		p.skip("postgres", "check that this tenant's postgres can be handed over",
			"this tenant runs no postgres server of its own (stores.postgres.kind is "+
				p.t.Stores.Postgres.Kind+"), so there is nothing to dump and nothing to restore", p.tenant)
		return
	}
	p.addFor("postgres", step{
		Kind: "probe", Title: "check that there is room to dump and re-create this tenant's postgres",
		Targets: []string{m.Dir},
		Warnings: []string{"a handover cannot COPY a postgres data directory: postgres refuses one it does not own " +
			"(st_uid vs geteuid), and no ACL can make one readable to the other account either — a named-user " +
			"entry is filtered by the mask, and the mask is the group mode bits a PGDATA must not have. So the " +
			"release dumps and the take restores into a cluster of its own",
			"nothing is dumped HERE: this step measures, so that a handover that cannot finish is refused while " +
				"the tenant is still serving"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// The IMAGE first. The release dumps with pg_dump, the take
			// restores with pg_restore, and the image's own entrypoint
			// initialises the new cluster with initdb — and that last one is
			// run by nothing the ctl controls, so an image without it would
			// first be noticed by a take that had already renamed this
			// tenant's cluster aside.
			tools, err := sc.Ops.Drivers.Postgres().ToolVersions(ctx, m.Spec)
			if err != nil {
				return "", err
			}
			sc.Logf("%s carries the three tools a handover needs: %s", m.Spec.SIF, versionsLine(tools))
			census, err := sc.Ops.Drivers.Postgres().Census(ctx, m.Spec)
			if err != nil {
				return "", err
			}
			free, err := sc.Ops.Drivers.Files().DiskFree(ctx, m.Dir)
			if err != nil {
				return "", err
			}
			need := census.SizeBytes + freshClusterBytes
			if free < need {
				return "", fmt.Errorf("%w: handing this postgres over writes a dump (at most %s — what "+
					"pg_database_size reports, and a compressed archive is smaller) and a freshly initialised "+
					"cluster (about %s), so it needs %s; statfs says %s has %s available. Free space before "+
					"releasing: after the release this tenant is DOWN and the take is the only way back up",
					jobs.ErrRefused, megabytes(census.SizeBytes), megabytes(freshClusterBytes),
					megabytes(need), m.Dir, megabytes(free))
			}
			return fmt.Sprintf("%s's postgres holds %s (pg_database_size) in %d table(s); the handover needs %s "+
				"and statfs says %s is available", p.tenant, megabytes(census.SizeBytes), len(census.Tables),
				megabytes(need), megabytes(free)), nil
		},
	})
}

// addPGDump is the release's act: the tenant's database as a file the other
// account can read.
//
// WHERE it sits is the whole of its correctness. AFTER the API stop, so nothing
// is writing and the dump is a consistent picture of a tenant nobody is using;
// BEFORE the postgres stop, because a dump needs a running server. That is a
// window of exactly one step, and it is the only window this operation has.
func (p *planner) addPGDump(legs []component) {
	m, ok := p.pgMigrationFor(legs, p.op.deps.owner())
	if !ok {
		p.skip("postgres", "skip the postgres dump",
			"this tenant runs no postgres server of its own (stores.postgres.kind is "+
				p.t.Stores.Postgres.Kind+")", p.tenant)
		return
	}
	name := p.tenant
	p.addFor("postgres", step{
		Kind: "postgres", Title: "dump this tenant's postgres for the take (the API is stopped, postgres is not)",
		Targets: []string{m.Dir},
		WouldWrite: []model.WouldWrite{
			{Path: m.Dir + "/" + dumpPrefix + "<ts>.dump", Mode: "0640", Preview: model.NullString("")},
		},
		Warnings: []string{"taken with the API already stopped and postgres still up — the only window in which " +
			"it is both consistent and possible at all",
			"0640 and group-readable: the taking account cannot read the CLUSTER (a PGDATA's ACL mask is its " +
				"group mode bits, which postgres requires to be empty), and a plain file has no such check",
			"the exact row count of every table is recorded beside it: a dump and a restore cannot be compared " +
				"byte for byte, so the counts are what proves the take moved everything",
			"ONE DATABASE is moved — this tenant's, which is the only one `new-tenant.sh` creates in a local " +
				"cluster (POSTGRES_DB=<tenant>). A second database somebody made by hand, or a role created " +
				"outside the tenant's own, is in the pre-handover cluster and not in the dump; check for one " +
				"before committing if this tenant's postgres was ever touched by hand"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			dump, resumed := externalIDValue(sc.Step.ExternalIDs, "pgdump:")
			if !resumed {
				dump = filepath.Join(m.Dir, dumpPrefix+p.stampOf(sc)+".dump")
				// The PATH before the file: a crash during pg_dump otherwise
				// leaves an archive whose name nothing knows.
				if err := sc.Checkpoint("pgdump:" + dump); err != nil {
					return "", err
				}
			}
			// The counts come from the SAME quiet moment as the dump, and are
			// read first: a census that cannot be taken is a postgres that
			// cannot be dumped either, and failing here costs nothing.
			census, err := sc.Ops.Drivers.Postgres().Census(ctx, m.Spec)
			if err != nil {
				return "", err
			}
			if there, err := pathExists(ctx, files, dump); err != nil {
				return "", err
			} else if there {
				return "", fmt.Errorf("%w: %s is already there from an earlier attempt; this step never hands the "+
					"take an archive it did not just write. Remove it and run the release again (it is "+
					"re-entrant)", jobs.ErrRefused, dump)
			}
			if err := sc.Ops.Drivers.Postgres().Dump(ctx, m.Spec, dump); err != nil {
				return "", err
			}
			// The bytes, then the NAME. A dump in the page cache with a
			// directory entry that is not on the disk is a handover whose only
			// copy of the database does not survive the machine.
			if err := files.Sync(ctx, dump); err != nil {
				return "", fmt.Errorf("flushing %s to the disk: %w", dump, err)
			}
			if err := files.Sync(ctx, m.Dir); err != nil {
				return "", fmt.Errorf("flushing %s to the disk: %w", m.Dir, err)
			}
			sum, size, err := files.Sha256(ctx, dump)
			if err != nil {
				return "", fmt.Errorf("checksumming %s: %w", dump, err)
			}
			at := p.stampRFC3339(sc)
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				if t.Handover == nil {
					return fmt.Errorf("%w: %s's handover block is gone; something else cleared it while this job "+
						"was running, and the dump at %s would then be recorded nowhere",
						jobs.ErrRefused, name, dump)
				}
				t.Handover.PostgresData = &registry.PostgresDataMigration{
					Dump: dump, DumpSHA256: sum, DumpedAt: at, Tables: censusRows(census),
				}
				return nil
			}); err != nil {
				return "", err
			}
			return fmt.Sprintf("dumped %s to %s (%s on disk, %d table(s))",
				name, dump, megabytes(size), len(census.Tables)), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dump, ok := externalIDValue(sc.Step.ExternalIDs, "pgdump:")
			if !ok {
				return "no dump was taken", nil
			}
			// The dump is THIS step's artefact and nothing else has read it
			// yet, so removing it is the whole undo. The database it came from
			// was only ever read.
			if err := sc.Ops.Drivers.Files().Remove(ctx, dump); err != nil {
				return "", err
			}
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				if t.Handover != nil {
					t.Handover.PostgresData = nil
				}
				return nil
			}); err != nil {
				return "", err
			}
			return "removed " + dump, nil
		},
	})
}

// versionsLine renders the tool versions in a stable order, so two runs of the
// same check produce comparable log lines.
func versionsLine(tools map[string]string) string {
	names := make([]string, 0, len(tools))
	for n := range tools {
		names = append(names, n)
	}
	sort.Strings(names)
	out := make([]string, 0, len(names))
	for _, n := range names {
		out = append(out, tools[n])
	}
	return strings.Join(out, "; ")
}

// censusRows turns the driver's map into the row's ordered list. Sorted,
// because the registry is a document an operator reads and a map has no order.
func censusRows(c jobs.PostgresCensus) []registry.PostgresTableCount {
	names := make([]string, 0, len(c.Tables))
	for n := range c.Tables {
		names = append(names, n)
	}
	sort.Strings(names)
	out := make([]registry.PostgresTableCount, 0, len(names))
	for _, n := range names {
		out = append(out, registry.PostgresTableCount{Name: n, Rows: c.Tables[n]})
	}
	return out
}

// addPGSocketCleanup removes what the releasing account's postgres left in the
// socket directory.
//
// It is here because of the sticky bit. `<data_dir>/postgres/run` is
// `drwxrwsr-t`, and in a sticky directory only a file's OWNER may unlink it —
// so the stale `.s.PGSQL.<port>` and `.s.PGSQL.<port>.lock` this account's
// postgres leaves behind can be removed by this account and by nobody else.
// The take would find them, and a postgres that finds a lock file for its port
// refuses to start over it.
//
// So the RELEASE does it, after stopping the server that owns them, and the
// tenant is handed over with a socket directory the other account can use.
func (p *planner) addPGSocketCleanup(m pgMigration) {
	sock := filepath.Join(m.Run, ".s.PGSQL."+strconv.Itoa(m.Spec.Port))
	lock := sock + ".lock"
	p.addFor("files", step{
		Kind: "fs", Title: "remove the stale postgres socket and lock file this account's server left behind",
		Destructive: true, Targets: []string{m.Run},
		WouldWrite: []model.WouldWrite{
			{Path: lock, Mode: "removed", Preview: model.NullString("")},
			{Path: sock, Mode: "removed", Preview: model.NullString("")},
		},
		Warnings: []string{"the socket directory is STICKY (drwxrwsr-t), so only the owner of these files may " +
			"unlink them — this account. A take that found the lock file would meet a postgres refusing to start " +
			"over a port it is told is already locked",
			"both are removed only after the instance that owned them has been stopped"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			var gone []string
			for _, path := range []string{lock, sock} {
				there, err := pathExists(ctx, sc.Ops.Drivers.Files(), path)
				if err != nil {
					return "", err
				}
				if !there {
					continue
				}
				if err := sc.Ops.Drivers.Files().Remove(ctx, path); err != nil {
					return "", fmt.Errorf("removing the stale %s: %w — it belongs to this account's stopped "+
						"postgres, and the taking account cannot remove it (the directory is sticky)", path, err)
				}
				gone = append(gone, filepath.Base(path))
			}
			if len(gone) == 0 {
				return "the socket directory was already clean", nil
			}
			return "removed " + strings.Join(gone, " and "), nil
		},
	})
}

// ---------------------------------------------------------------- the take

// addPGClusterSwap is the take's first act on the data directory: put an EMPTY
// directory where the cluster was, so that the instance the next steps start
// initialises a cluster of its own there.
//
// WHERE it sits is the fix. After the port-free proofs — a directory moved
// under processes the release did not manage to stop is the worst thing this
// job could do — and before any store starts, because the whole point is that
// the postgres this take starts finds an empty directory rather than another
// account's cluster.
//
// It reads the original exactly once, with `Stat` of `data` ITSELF (which the
// parent's ACL mask does allow), to answer one question: does this account
// already own it? A re-take of a tenant this control plane already runs has
// nothing to migrate, and initialising a fresh cluster over a live one would be
// the only destructive thing in this file.
func (p *planner) addPGClusterSwap(legs []component, account string, h *registry.Handover) {
	m, ok := p.pgMigrationFor(legs, account)
	if !ok {
		p.skip("files", "skip the postgres cluster",
			"this tenant runs no postgres server of its own (stores.postgres.kind is "+
				p.t.Stores.Postgres.Kind+"), so there is nothing to migrate", p.tenant)
		return
	}
	if h.PostgresData == nil || h.PostgresData.Dump == "" {
		p.skip("files", "skip the postgres cluster",
			"the release recorded no postgres dump for this tenant (handover.postgres_data is null): there is "+
				"nothing for a take to restore, and this step will not put an empty directory where a cluster is "+
				"unless it can account for the data in it", p.tenant)
		return
	}
	dump, wantSum := h.PostgresData.Dump, h.PostgresData.DumpSHA256
	name := p.tenant
	p.addFor("files", step{
		Kind: "fs", Title: "put an empty cluster directory in place for " + account + " (two renames)",
		Destructive: true, Targets: []string{m.Data},
		WouldWrite: []model.WouldWrite{
			{Path: m.Dir + "/data.<account>-<ts>", Mode: "2770", Preview: model.NullString("")},
			{Path: m.Dir + "/" + preHandoverPrefix + "<ts>", Mode: "unchanged", Preview: model.NullString("")},
		},
		Warnings: []string{"the original cluster is renamed aside and is never opened, never written and never " +
			"listed by this account — which is just as well, because it could not be: a PGDATA's ACL mask is its " +
			"group mode bits, and postgres requires those to be empty",
			"the EMPTY directory is what makes the postgres instance the next step starts initialise a cluster, " +
				"exactly as it did when this tenant was provisioned: the same image, the same POSTGRES_USER, " +
				"POSTGRES_DB and PGDATA the renderer produces, the same role password out of secrets.env, and " +
				"initdb's own defaults for locale and encoding — because it is the same start path, not a second " +
				"one written to imitate it",
			"`--abandon` renames the two back, and the tenant then restarts on the bytes it always had"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return p.runPGClusterSwap(ctx, sc, m, dump, wantSum)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			fresh, hasFresh := externalIDValue(sc.Step.ExternalIDs, "pgfresh:")
			aside, hasAside := externalIDValue(sc.Step.ExternalIDs, "pgaside:")
			if !hasFresh || !hasAside {
				return "nothing was swapped: no paths were checkpointed", nil
			}
			msg, err := swapPGClusterBack(ctx, sc, m.Data, fresh, aside, true)
			if err != nil {
				return "", err
			}
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				if t.Handover != nil && t.Handover.PostgresData != nil {
					// The DUMP stays recorded: it is the release's artefact
					// rather than this step's, and a re-run of the take needs
					// it.
					t.Handover.PostgresData.PreHandover = ""
					t.Handover.PostgresData.Copy = ""
					t.Handover.PostgresData.MigratedAt = ""
				}
				return nil
			}); err != nil {
				return "", err
			}
			return msg + " (" + name + "'s handover no longer records a cluster swap)", nil
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			fresh, hasFresh := externalIDValue(sc.Step.ExternalIDs, "pgfresh:")
			aside, hasAside := externalIDValue(sc.Step.ExternalIDs, "pgaside:")
			if !hasFresh || !hasAside {
				// The checkpoint is written before anything is touched, so a
				// step with no ids did nothing.
				return jobs.ReconcileRedo, nil
			}
			files := sc.Ops.Drivers.Files()
			dataThere, err := pathExists(ctx, files, m.Data)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			asideThere, err := pathExists(ctx, files, aside)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			freshThere, err := pathExists(ctx, files, fresh)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			switch {
			case dataThere && asideThere && !freshThere:
				return jobs.ReconcileDone, nil
			case !dataThere && asideThere && freshThere:
				// The crash between the two renames. Run finishes it.
				return jobs.ReconcileRedo, nil
			case dataThere && !asideThere:
				return jobs.ReconcileRedo, nil
			default:
				return jobs.ReconcileStuck, fmt.Errorf("%w: %s's postgres directories are in a state this step "+
					"cannot name: %s %s, %s %s, %s %s. Look at all three before anything else touches this tenant",
					jobs.ErrRefused, name,
					m.Data, presence(dataThere), aside, presence(asideThere), fresh, presence(freshThere))
			}
		},
	})
}

// runPGClusterSwap is the step body: a small state machine rather than a
// straight line, because it is re-entered by a resume after the worker died and
// by a retry of the job.
//
// The states it can find, and what each means:
//
//	data present, nothing else          → not started (or nothing to do)
//	data present, fresh present         → an abandoned earlier attempt
//	data ABSENT, aside + fresh present  → the crash between the two renames
//	data present, aside present         → both renames happened; done
func (p *planner) runPGClusterSwap(ctx context.Context, sc *jobs.StepContext, m pgMigration,
	dump, wantSum string) (string, error) {
	files := sc.Ops.Drivers.Files()
	fresh, hadFresh := externalIDValue(sc.Step.ExternalIDs, "pgfresh:")
	aside, hadAside := externalIDValue(sc.Step.ExternalIDs, "pgaside:")

	if !hadFresh || !hadAside {
		// The ONE read of the original, and it is of `data` rather than of the
		// cluster inside it: a re-take of a tenant this account already runs
		// must not put an empty directory where a live cluster is.
		self, err := files.SelfUID(ctx)
		if err != nil {
			return "", err
		}
		st, err := files.Stat(ctx, m.Data)
		switch {
		case errors.Is(err, fs.ErrNotExist):
			return "", fmt.Errorf("%w: %s is not there at all, so this tenant has no postgres cluster to replace "+
				"and no bind for its instance to start from", jobs.ErrRefused, m.Data)
		case err != nil:
			return "", err
		case st.IsSymlink:
			return "", fmt.Errorf("%w: %s is a symlink; the ctl does not rename through one", jobs.ErrRefused, m.Data)
		case st.UID == self:
			sc.Logf("%s is already uid %d's — this account's — so its postgres starts on it as it stands and "+
				"nothing is swapped; the release's dump at %s stays where it is", m.Data, self, dump)
			return fmt.Sprintf("no swap needed: the cluster directory is already uid %d's", self), nil
		}

		// The dump has to be GOOD before the cluster is moved aside. Verifying
		// it afterwards would mean discovering a truncated archive with the
		// tenant's only working cluster already renamed away.
		if err := verifyDump(ctx, sc, dump, wantSum); err != nil {
			return "", err
		}
		free, err := files.DiskFree(ctx, m.Dir)
		if err != nil {
			return "", err
		}
		if free < freshClusterBytes {
			return "", fmt.Errorf("%w: initialising a cluster for %s needs about %s, and statfs says %s has %s "+
				"available. Free space and run the take again; the release warned about this while the tenant "+
				"was still up", jobs.ErrRefused, m.Account, megabytes(freshClusterBytes), m.Dir, megabytes(free))
		}

		ts := p.stampOf(sc)
		fresh = filepath.Join(m.Dir, "data."+m.Account+"-"+ts)
		aside = filepath.Join(m.Dir, preHandoverPrefix+ts)
		if err := sc.Checkpoint("pgfresh:"+fresh, "pgaside:"+aside); err != nil {
			return "", err
		}
	}

	dataThere, err := pathExists(ctx, files, m.Data)
	if err != nil {
		return "", err
	}
	asideThere, err := pathExists(ctx, files, aside)
	if err != nil {
		return "", err
	}
	freshThere, err := pathExists(ctx, files, fresh)
	if err != nil {
		return "", err
	}

	if dataThere && asideThere && !freshThere {
		sc.Logf("%s is already this account's own directory and the original is at %s: this step ran before",
			m.Data, aside)
		return p.recordPGSwap(sc, m, fresh, aside, "already swapped")
	}

	switch {
	case dataThere && !asideThere:
		if freshThere {
			return "", fmt.Errorf("%w: %s is already there from an earlier attempt of this step; this step never "+
				"starts a postgres on a directory it cannot account for. Remove it (`rm -rf %s`) and run the take "+
				"again", jobs.ErrRefused, fresh, fresh)
		}
		// The new cluster's directory, made by THIS account, so the postgres
		// this account starts owns what it initialises. 2770 is the tenant
		// tree's mode; PGDATA is the `pgdata` the image creates INSIDE it at
		// 0700, and that is the directory postgres inspects.
		if err := files.MkdirAll(ctx, fresh, 0o2770); err != nil {
			return "", fmt.Errorf("creating the empty cluster directory %s: %w", fresh, err)
		}
		sc.Logf("%s is an empty directory owned by %s; the postgres instance will initialise a cluster in it",
			fresh, m.Account)
		if err := files.Rename(ctx, m.Data, aside); err != nil {
			return "", fmt.Errorf("renaming the original cluster %s aside to %s: %w", m.Data, aside, err)
		}
		sc.Logf("the original cluster is now %s and nothing opens it again", aside)
		fallthrough
	case !dataThere && asideThere && freshThere:
		// Either the fallthrough above, or a resume that found the crash
		// between the two renames. One rename left.
		if err := files.Rename(ctx, fresh, m.Data); err != nil {
			return "", fmt.Errorf("%w — the original is at %s and this account's empty directory at %s; renaming "+
				"it into place is all that is left, and until it happens this tenant has NO directory at %s",
				err, aside, fresh, m.Data)
		}
		return p.recordPGSwap(sc, m, fresh, aside, "swapped")
	default:
		return "", fmt.Errorf("%w: %s's postgres directories are in a state this step cannot act on: %s %s, %s %s, "+
			"%s %s. Nothing has been changed", jobs.ErrRefused, p.tenant,
			m.Data, presence(dataThere), aside, presence(asideThere), fresh, presence(freshThere))
	}
}

// verifyDump is the take's first question about the release's artefact: is it
// there, and is it the archive the release recorded.
//
// The checksum is not ceremony. The dump crosses a job boundary, an account
// boundary and an unbounded amount of wall-clock time — a soak, a night, an
// operator's second attempt — and what it is about to be weighed against is a
// tenant's entire relational state.
func verifyDump(ctx context.Context, sc *jobs.StepContext, dump, want string) error {
	files := sc.Ops.Drivers.Files()
	there, err := pathExists(ctx, files, dump)
	if err != nil {
		return err
	}
	if !there {
		return fmt.Errorf("%w: the release's postgres dump %s is not there. Without it this take has nothing to "+
			"restore into the cluster it is about to create: re-run the release (it is re-entrant and takes a new "+
			"dump), or abandon the handover", jobs.ErrRefused, dump)
	}
	sum, size, err := files.Sha256(ctx, dump)
	if err != nil {
		return fmt.Errorf("checksumming %s: %w", dump, err)
	}
	if want != "" && sum != want {
		return fmt.Errorf("%w: %s is not the archive the release recorded (sha256 %s, the row says %s). Something "+
			"rewrote or truncated it between the two halves of this handover; re-run the release",
			jobs.ErrRefused, dump, sum, want)
	}
	sc.Logf("the release's dump %s is %s on disk and matches the sha256 the row records", dump, megabytes(size))
	return nil
}

// recordPGSwap writes the two paths into the row, in the SAME step as the
// renames: a row that did not name the pre-handover directory would leave the
// abandon with nothing to swap back, and the window between two steps is
// exactly where the worker died on the run that made all of this necessary.
func (p *planner) recordPGSwap(sc *jobs.StepContext, m pgMigration, fresh, aside, verb string) (string, error) {
	at := p.stampRFC3339(sc)
	err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
		if t.Handover == nil || t.Handover.PostgresData == nil {
			return fmt.Errorf("%w: %s's handover no longer records the release's postgres dump; something else "+
				"rewrote the row while this job was running, and the original cluster at %s would then be "+
				"recorded nowhere", jobs.ErrRefused, p.tenant, aside)
		}
		t.Handover.PostgresData.PreHandover = aside
		t.Handover.PostgresData.Copy = fresh
		t.Handover.PostgresData.MigratedAt = at
		return nil
	})
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s: %s is now %s's own empty cluster directory; the original is %s",
		verb, m.Data, m.Account, aside), nil
}

// addPGRestore loads the release's dump into the cluster the instance has just
// initialised, and PROVES it.
//
// It sits between the store starts and the API start, and both sides of that
// are load-bearing: the cluster does not exist until the instance has created
// it, and the API must not serve a tenant whose relational state is half
// restored.
func (p *planner) addPGRestore(legs []component, account string, h *registry.Handover) {
	m, ok := p.pgMigrationFor(legs, account)
	if !ok || h.PostgresData == nil || h.PostgresData.Dump == "" {
		return // the swap step has already said why, in this same plan
	}
	pd := h.PostgresData
	want := pd.Tables
	p.addFor("postgres", step{
		Kind: "postgres", Title: "restore the release's dump into the new cluster and check every table's row count",
		Targets: []string{pd.Dump},
		Warnings: []string{"the restore runs BEFORE the API starts: a tenant serving out of a half-restored " +
			"database is the one outcome worse than a tenant that is still down",
			"the proof is row counts, table by table, against what the release recorded. A dump and a restore " +
				"cannot be compared byte for byte, and a migration that moved less than everything must fail the " +
				"take rather than show up later as a tenant that is quietly short"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// Did the swap actually swap? It does nothing when this account
			// ALREADY owns the cluster directory — a re-take of a tenant this
			// control plane runs — and in that case the postgres now answering
			// is the tenant's OWN, live, populated cluster. Restoring the
			// release's dump into it would be pouring a copy of a database
			// into itself: pg_restore --exit-on-error would fail on the first
			// relation that already exists, and any restore that did not fail
			// would be worse.
			//
			// The row is the answer, read at RUN time out of the fleet the
			// engine loaded under the locks — the same object the swap step
			// wrote `migrated_at` into two steps ago. The PLAN-time value
			// cannot say: at plan time the swap has not run.
			if live := livePGData(sc, p.tenant); live == nil || live.MigratedAt == "" {
				sc.Logf("the cluster directory was already this account's, so no cluster was created and there is "+
					"nothing to restore: %s is the tenant's own postgres, with its own rows", m.Data)
				return "skipped: this take created no cluster, so the release's dump is not restored", nil
			}
			// The cluster the instance step started has to be ANSWERING before
			// anything is restored into it, and this wait carries the
			// fast-fail: a postgres that exited says why out of its own log
			// rather than after three minutes of silence.
			if err := awaitStores(ctx, sc, pgProbesOnly(p.t, p.tpaths), createReadyTimeout); err != nil {
				return "", err
			}
			if err := verifyDump(ctx, sc, pd.Dump, pd.DumpSHA256); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Postgres().Restore(ctx, m.Spec, pd.Dump); err != nil {
				return "", err
			}
			got, err := sc.Ops.Drivers.Postgres().Census(ctx, m.Spec)
			if err != nil {
				return "", fmt.Errorf("counting the rows the restore put back: %w", err)
			}
			if err := comparePGCensus(want, got, p.tenant); err != nil {
				return "", err
			}
			return fmt.Sprintf("restored %s into %s's new cluster; %d table(s) came back with exactly the rows "+
				"the release recorded", pd.Dump, account, len(want)), nil
		},
	})
}

// livePGData is the tenant's postgres-migration block as it stands RIGHT NOW,
// out of the fleet the engine loaded under this job's locks.
//
// Steps of one job read each other's registry writes through this object — it
// is the same *registry.Fleet saveTenant mutates — which is the only way a
// later step can know what an earlier one decided. nil means there is no
// handover block or no migration in it.
func livePGData(sc *jobs.StepContext, tenant string) *registry.PostgresDataMigration {
	if sc.Ops.Fleet == nil {
		return nil
	}
	t, ok := sc.Ops.Fleet.Tenants[tenant]
	if !ok || t.Handover == nil {
		return nil
	}
	return t.Handover.PostgresData
}

// pgProbesOnly is the postgres leg's readiness probe, with the instance-mode
// fast-fail on it. The restore needs THAT wait and not the others: qdrant and
// elasticsearch have their own gate one step later, in the API start.
func pgProbesOnly(t *registry.Tenant, tp paths.Tenant) []storeProbe {
	var out []storeProbe
	for _, pr := range ownStoreProbes(t, tp, true) {
		if pr.what == "postgres" {
			out = append(out, pr)
		}
	}
	return out
}

// comparePGCensus is the proof. It refuses on ANY difference, which is
// deliberately stricter than the collection census the same handover takes over
// qdrant and elasticsearch: those are read from a tenant that may have been
// written to between the two readings, and this is a cluster that was EMPTY
// four steps ago. A surplus here is not "somebody wrote to the tenant", it is a
// restore into something that was not empty.
func comparePGCensus(want []registry.PostgresTableCount, got jobs.PostgresCensus, tenant string) error {
	var short, surplus, missing []string
	seen := map[string]bool{}
	for _, w := range want {
		seen[w.Name] = true
		now, ok := got.Tables[w.Name]
		switch {
		case !ok:
			missing = append(missing, fmt.Sprintf("%s (%d rows)", w.Name, w.Rows))
		case now < w.Rows:
			short = append(short, fmt.Sprintf("%s %d → %d", w.Name, w.Rows, now))
		case now > w.Rows:
			surplus = append(surplus, fmt.Sprintf("%s %d → %d", w.Name, w.Rows, now))
		}
	}
	var extra []string
	for name := range got.Tables {
		if !seen[name] {
			extra = append(extra, name)
		}
	}
	sort.Strings(extra)
	if len(short) == 0 && len(missing) == 0 && len(surplus) == 0 && len(extra) == 0 {
		return nil
	}
	var parts []string
	for _, pair := range [][2]string{
		{"tables the restore did not create", strings.Join(missing, "; ")},
		{"tables with FEWER rows than the release recorded", strings.Join(short, "; ")},
		{"tables with MORE rows", strings.Join(surplus, "; ")},
		{"tables the release never recorded", strings.Join(extra, ", ")},
	} {
		if pair[1] != "" {
			parts = append(parts, pair[0]+": "+pair[1])
		}
	}
	return fmt.Errorf("%w: %s's postgres did not come back with what it went down with — %s. The original cluster "+
		"is untouched at the pre-handover path in the row: stop this tenant (`ragstack-ctl tenant stop %s`), "+
		"abandon the handover, and start it again with `ops/coconut/restore.sh --tenant %s`",
		jobs.ErrRefused, tenant, strings.Join(parts, "; "), tenant, tenant)
}

// ---------------------------------------------------------------- the abandon

// addPGSwapBack is the ABANDON's half: the tenant goes back to the account that
// released it, and that account's postgres will not start on a cluster this one
// initialised.
func (p *planner) addPGSwapBack(h *registry.Handover) {
	pd := h.PostgresData
	if pd == nil || pd.PreHandover == "" {
		p.skip("files", "skip the postgres cluster",
			"this handover's take swapped no postgres cluster (handover.postgres_data records no pre-handover "+
				"path): either the tenant runs no postgres of its own, the take never got that far, or the "+
				"taking account already owned the directory", p.tenant)
		return
	}
	data := p.tpaths.PostgresData
	name := p.tenant
	p.addFor("files", step{
		Kind: "fs", Title: "put the original postgres cluster back (two renames)", Destructive: true,
		Targets: []string{data},
		WouldWrite: []model.WouldWrite{
			{Path: data, Mode: "unchanged", Preview: model.NullString("")},
			{Path: pd.Copy, Mode: "unchanged", Preview: model.NullString("")},
		},
		Warnings: []string{"the original at " + pd.PreHandover + " has not been opened since the take: putting it " +
			"back is exact, down to the inode",
			"the cluster that served during the soak is renamed to " + pd.Copy + " and LEFT there. It has " +
				"diverged from the original since the take, so it is the operator's to read or delete, never " +
				"this job's"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			msg, err := swapPGClusterBack(ctx, sc, data, pd.Copy, pd.PreHandover, true)
			if err != nil {
				return "", err
			}
			sc.Logf("%s's postgres cluster is the releasing account's again", name)
			return msg, nil
		},
	})
}

// swapPGClusterBack puts the two names back: the live directory returns to the
// name the take made it under, and the original returns to `data`.
//
// In that ORDER, and neither rename may clobber — which is why the order is
// forced rather than chosen: `data` has to be free before the original can go
// back into it.
//
// `moved` says whether the caller KNOWS a swap happened (the take's rollback
// knows from its own checkpoints, the abandon from the row). It decides what an
// absent `aside` means, and the difference is not cosmetic. If nothing was ever
// moved, an absent original is the ordinary case and there is nothing to do. If
// something WAS moved, an absent original means the only copy of the releasing
// account's database is gone — an operator following doctor's own `rm -rf`
// before committing, or a take that died between its rename and its registry
// write — and `data` is then the TAKING account's cluster. Reporting success
// there hands the tenant back on a directory its owner's postgres refuses, with
// the row already cleared and no way back. So it refuses, and says what it
// found.
func swapPGClusterBack(ctx context.Context, sc *jobs.StepContext, data, fresh, aside string, moved bool) (string, error) {
	files := sc.Ops.Drivers.Files()
	asideThere, err := pathExists(ctx, files, aside)
	if err != nil {
		return "", err
	}
	if !asideThere {
		if !moved {
			return fmt.Sprintf("%s is not there and nothing was swapped: %s is left exactly as it is",
				aside, data), nil
		}
		self, serr := files.SelfUID(ctx)
		if serr != nil {
			return "", serr
		}
		owner := "nothing is there at all"
		if st, err := files.Stat(ctx, data); err == nil {
			owner = "owned by uid " + strconv.Itoa(st.UID)
			if st.UID == self {
				owner += ", this account's"
			}
		}
		return "", fmt.Errorf("%w: the original postgres cluster %s is GONE, and %s is %s — the cluster the take "+
			"created. This handover cannot be put back: the account that released this tenant has no cluster to "+
			"start on, and its postgres refuses one it does not own. Do NOT clear the handover block. Either "+
			"keep the handover and commit it, or restore this tenant's postgres from a backup as the account "+
			"that will run it", jobs.ErrRefused, aside, data, owner)
	}
	dataThere, err := pathExists(ctx, files, data)
	if err != nil {
		return "", err
	}
	if dataThere {
		if err := files.Rename(ctx, data, fresh); err != nil {
			return "", fmt.Errorf("moving this account's cluster out of %s (to %s) so the original can go back: %w",
				data, fresh, err)
		}
		sc.Logf("the cluster this account created is now %s", fresh)
	}
	if err := files.Rename(ctx, aside, data); err != nil {
		return "", fmt.Errorf("putting the original cluster %s back at %s: %w", aside, data, err)
	}
	return fmt.Sprintf("the original postgres cluster is back at %s (the take's cluster is %s, and has diverged "+
		"from it: delete it when the tenant is up)", data, fresh), nil
}

// ---------------------------------------------------------------- shared

// megabytes renders a byte count the way an operator reads it.
//
// It does NOT label the number, because the three numbers this file reports are
// three different things and one word cannot be right for all of them: a
// database's size is postgres's own accounting (`pg_database_size`), a dump's
// is the file's apparent size, and free space is the blocks statfs says are
// available. Each call site says which it is holding, so that a figure in a
// refusal can be checked against the command that would produce it.
func megabytes(n int64) string {
	const mb = 1 << 20
	if n < mb && n > 0 {
		return "under 1 MB"
	}
	return fmt.Sprintf("%d MB", n/mb)
}

// pathExists asks the files driver whether a path is there. An absent path is
// (false, nil); anything else is the caller's error, because "I could not tell"
// must never read as "it is not there" in a step that renames directories.
func pathExists(ctx context.Context, files jobs.Files, path string) (bool, error) {
	_, err := files.Stat(ctx, path)
	switch {
	case err == nil:
		return true, nil
	case errors.Is(err, fs.ErrNotExist):
		return false, nil
	default:
		return false, fmt.Errorf("asking whether %s is there: %w", path, err)
	}
}

// presence is how the "cannot act on this" messages say what they found.
func presence(there bool) string {
	if there {
		return "is there"
	}
	return "is NOT there"
}
