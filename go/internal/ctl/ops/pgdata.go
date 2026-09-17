package ops

// The one thing a handover MOVES.
//
// Everywhere else in handover.go the claim holds: a handover moves no data,
// the same directories serve the same processes under another account, through
// the ACLs PR-D2 installed. Postgres is the exception, and it is not a
// permissions problem that a better ACL would fix.
//
// postgres compares its data directory's `st_uid` with its own `geteuid()` at
// startup and refuses when they differ:
//
//	FATAL:  data directory "/var/lib/postgresql/data/pgdata" has wrong ownership
//	HINT:  The server must be started by the user that owns the data directory.
//
// It never looks at the mode and it never looks at the ACL. On coconut the
// hackathon tenant's `pgdata` is `drwx------ wilke` with 660 wilke files and a
// default ACL granting svcbvbrc rwx — every byte of it readable and writable by
// the service account, and postgres refuses anyway. Qdrant and Elasticsearch do
// not ask, which is why the second live take got two stores up, started the
// postgres instance, and then sat for three minutes waiting for a server that
// had already written its one line and exited:
//
//	postgres did not become ready within 3m0s: pg_isready for hackathon:
//	apptainer exited 2: /var/run/postgresql:24085 - no response
//
// Nobody on this host can chown to another account (no root, and `--fakeroot`
// has no subuid mapping — MEMORY's first host fact). What an account CAN do is
// make a copy, because a file it creates is a file it owns. So the take copies
// the directory as itself and swaps the names:
//
//	<data_dir>/postgres/data                 ← what the bind and PGDATA name
//	<data_dir>/postgres/data.<account>-<ts>  ← the copy, owned by the taker
//	<data_dir>/postgres/data.pre-handover-<ts> ← the original, untouched
//
// copy, then rename the original aside, then rename the copy into place. Two
// renames rather than one swap because there is no atomic exchange here: a
// crash between them leaves `data` MISSING with both other names present, which
// is a state this step can finish (and says so) rather than a state it has to
// guess about. Both names are checkpointed BEFORE the copy for exactly that
// reason — the rule every step in this package follows about external ids.
//
// The original is never written to. That is what makes the rollback and the
// abandon exact: they rename the two names back and the tenant is on the same
// bytes it was on before the release, down to the inode.

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// pgDataFreeSpaceFactor is how much room the copy is required to have: twice
// the tree.
//
// Twice, not once, and the extra one is deliberate. The copy needs the tree's
// size and the original is not going anywhere — that is 2× on its own — and
// the first thing the tenant does on the new directory is write: WAL segments,
// a checkpoint, the autovacuum that follows a start. A filesystem with exactly
// enough room for the copy is a filesystem with no room for the database the
// copy becomes, and /rag is shared with every other tenant on the host.
const pgDataFreeSpaceFactor = 2

// preHandoverPrefix is the name an original directory is renamed to.
//
// It is DOCTOR's constant rather than a second copy of the string: doctor
// scans the disk for these directories (pre_handover_copy_present) long after
// the commit has cleared the row that named them, so the two halves have to
// agree on the spelling, and the compiler is a better guarantee of that than a
// test.
const preHandoverPrefix = doctor.PreHandoverDirPrefix

// pgDataMigration is the plan-time description of the swap: the three paths
// and the account. The timestamps in the two new names are RUN-time, so they
// are not here — a plan that stamped a name would differ between the engine's
// two Plan calls and every job would be `plan_stale`.
type pgDataMigration struct {
	// Data is the bind source — `<data_dir>/postgres/data`, what
	// render.StoreArgv binds to /var/lib/postgresql/data.
	Data string
	// PGData is `<Data>/pgdata`, the directory postgres actually checks
	// (PGDATA in the instance's environment). It is the one whose ownership
	// decides whether the server starts, and it is checked as well as the bind
	// source because the two can differ: on coconut `data` is 0770 wilke and
	// `pgdata` is 0700 wilke.
	PGData string
	// Dir is the parent both new names are created in.
	Dir string
	// Account is the account doing the taking: the copy's owner.
	Account string
}

// pgDataMigrationFor describes the swap for this tenant, or reports that there
// is nothing to swap (a tenant with no postgres of its own).
func (p *planner) pgDataMigrationFor(legs []component, account string) (pgDataMigration, bool) {
	local := false
	for _, c := range legs {
		if c.Leg == render.LegPostgres && c.Managed {
			local = true
		}
	}
	if !local || p.t.Stores.Postgres.Kind != registry.PostgresKindLocal {
		return pgDataMigration{}, false
	}
	data := p.tpaths.PostgresData
	return pgDataMigration{
		Data:    data,
		PGData:  filepath.Join(data, "pgdata"),
		Dir:     filepath.Dir(data),
		Account: account,
	}, true
}

// pgDataOwner answers who owns this postgres data directory and whether that
// is somebody other than the account asking.
//
// BOTH paths are asked about, and the answer is "another account owns it" when
// EITHER is another account's: the bind source is what the copy moves and
// `pgdata` is what postgres inspects, and a tenant whose `data` is already the
// service account's while `pgdata` under it is still the owner's is a tenant
// whose postgres refuses for a reason a check of the parent alone would have
// missed.
//
// An ABSENT directory is not a migration: a tenant whose postgres has never
// been initialised has nothing to copy, and postgres initdb's a fresh PGDATA
// as itself. Absence is therefore (0, false, nil).
func pgDataOwner(ctx context.Context, sc *jobs.StepContext, m pgDataMigration) (uid int, foreign bool, err error) {
	files := sc.Ops.Drivers.Files()
	self, err := files.SelfUID(ctx)
	if err != nil {
		return 0, false, err
	}
	for _, path := range []string{m.Data, m.PGData} {
		st, serr := files.Stat(ctx, path)
		switch {
		case errors.Is(serr, fs.ErrNotExist):
			continue
		case serr != nil:
			return 0, false, fmt.Errorf("asking who owns %s: %w", path, serr)
		}
		if st.IsSymlink {
			return 0, false, fmt.Errorf("%w: %s is a symlink; the ctl neither copies nor renames through one, and a "+
				"postgres data directory reached through a link is not one this control plane can move between "+
				"accounts", jobs.ErrRefused, path)
		}
		if st.UID != self {
			return st.UID, true, nil
		}
	}
	return self, false, nil
}

// ---------------------------------------------------------------- the release's warning

// addPGDataOwnershipCheck is the RELEASE's half: say now, while the tenant is
// still serving, what the take is going to have to do.
//
// It runs with everything else that can refuse, before a single process is
// stopped, for the reason the password check gives in as many words: a take
// that discovers it cannot start postgres discovers it with the tenant already
// down. Free space is the part that can actually make the take impossible, and
// it is the part an operator can do something about — five minutes before the
// release, and not at all after it.
func (p *planner) addPGDataOwnershipCheck(legs []component) {
	m, ok := p.pgDataMigrationFor(legs, "the taking account")
	if !ok {
		p.skip("files", "check what the take will have to do with the postgres data directory",
			"this tenant runs no postgres server of its own (stores.postgres.kind is "+
				p.t.Stores.Postgres.Kind+"), so there is no data directory to hand over", p.tenant)
		return
	}
	p.addFor("files", step{
		Kind: "probe", Title: "check whether the take can own the postgres data directory", Targets: []string{m.Data},
		Warnings: []string{"postgres compares its data directory's owner with its own uid and refuses to start when " +
			"they differ — the mode and the ACL make no difference — and no account on this host may chown to " +
			"another, so a take whose account does not own this directory has to COPY it",
			"nothing is copied HERE: this step measures, so that a take that cannot work is refused while the " +
				"tenant is still up"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			uid, foreign, err := pgDataOwner(ctx, sc, m)
			if err != nil {
				return "", err
			}
			if !foreign {
				return fmt.Sprintf("%s is already owned by uid %d, the account running this release; a take by "+
					"another account will still have to copy it", m.Data, uid), nil
			}
			files := sc.Ops.Drivers.Files()
			bytes, entries, err := files.TreeSize(ctx, m.Data)
			if err != nil {
				return "", fmt.Errorf("measuring %s: %w", m.Data, err)
			}
			free, err := files.DiskFree(ctx, m.Dir)
			if err != nil {
				return "", err
			}
			need := int64(pgDataFreeSpaceFactor) * bytes
			if free < need {
				return "", fmt.Errorf("%w: postgres data owned by uid %d: the take will copy %s (%d entries) and "+
					"needs 2× that — %s — free on the filesystem holding %s, which has %s. Free space before "+
					"releasing: after the release this tenant is DOWN and the take is the only way back up",
					jobs.ErrRefused, uid, megabytes(bytes), entries, megabytes(need), m.Dir, megabytes(free))
			}
			sc.Logf("postgres data owned by uid %d: the take will copy %s (%d entries) into %s and rename the "+
				"original to %s/%s<ts>; %s is free and %s is needed",
				uid, megabytes(bytes), entries, m.Dir, m.Dir, preHandoverPrefix, megabytes(free), megabytes(need))
			return fmt.Sprintf("postgres data owned by uid %d: the take will copy %s, and needs %s free (%s available)",
				uid, megabytes(bytes), megabytes(need), megabytes(free)), nil
		},
	})
}

// megabytes renders a byte count the way an operator reads it in a runbook.
// Integer MB: the number decides whether there is room, and a decimal place on
// a figure that is about to be doubled is false precision.
func megabytes(n int64) string {
	const mb = 1 << 20
	if n < mb && n > 0 {
		return "under 1 MB"
	}
	return fmt.Sprintf("%d MB", n/mb)
}

// ---------------------------------------------------------------- the take's swap

// addPGDataMigration plans the copy and the two renames.
//
// WHERE it sits in the take matters and is the whole shape of the fix: after
// the port-free proofs (a tenant whose old processes are still up must not have
// its data directory moved under them) and BEFORE any store is started (a
// postgres started on the old directory is the failure this exists to prevent,
// and a qdrant started first would only have to be rolled back).
func (p *planner) addPGDataMigration(legs []component, account string) {
	m, ok := p.pgDataMigrationFor(legs, account)
	if !ok {
		p.skip("files", "skip the postgres data directory",
			"this tenant runs no postgres server of its own (stores.postgres.kind is "+
				p.t.Stores.Postgres.Kind+"), so there is nothing to migrate", p.tenant)
		return
	}
	name := p.tenant
	p.addFor("files", step{
		Kind: "fs", Title: "migrate the postgres data directory to " + account + " (copy, then two renames)",
		Destructive: true,
		Targets:     []string{m.Data},
		WouldWrite: []model.WouldWrite{
			{Path: m.Dir + "/data.<account>-<ts>", Mode: "same as the source", Preview: model.NullString("")},
			{Path: m.Dir + "/" + preHandoverPrefix + "<ts>", Mode: "unchanged", Preview: model.NullString("")},
		},
		Warnings: []string{"postgres refuses a data directory it does not own (st_uid vs geteuid), whatever the mode " +
			"and whatever the ACL say, and no account here may chown: a copy the taking account makes is the only " +
			"directory its postgres will start on",
			"the ORIGINAL is renamed aside and never written to again. `--abandon` renames the two back, and the " +
				"tenant is then on the same bytes it was on before the release",
			"`--commit` LEAVES the pre-handover directory: it is a second copy of this tenant's postgres, and " +
				"deleting it is an operator's decision after the soak (doctor says `pre_handover_copy_present` " +
				"until it is gone)"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return p.runPGDataMigration(ctx, sc, m)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			copyPath, hasCopy := externalIDValue(sc.Step.ExternalIDs, "pgcopy:")
			aside, hasAside := externalIDValue(sc.Step.ExternalIDs, "pgaside:")
			if !hasCopy || !hasAside {
				return "nothing was migrated: no paths were checkpointed", nil
			}
			msg, err := swapPGDataBack(ctx, sc, m.Data, copyPath, aside)
			if err != nil {
				return "", err
			}
			// The row goes back with the directories. A row still naming a
			// pre-handover path that is no longer there would send the next
			// abandon at a directory that does not exist.
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				if t.Handover != nil {
					t.Handover.PostgresData = nil
				}
				return nil
			}); err != nil {
				return "", err
			}
			return msg + " (" + name + "'s handover no longer records a postgres migration)", nil
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			copyPath, hasCopy := externalIDValue(sc.Step.ExternalIDs, "pgcopy:")
			aside, hasAside := externalIDValue(sc.Step.ExternalIDs, "pgaside:")
			if !hasCopy || !hasAside {
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
			copyThere, err := pathExists(ctx, files, copyPath)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			switch {
			case dataThere && asideThere && !copyThere:
				// Both renames happened.
				return jobs.ReconcileDone, nil
			case !dataThere && asideThere && copyThere:
				// The crash between the two renames. Run finishes it.
				return jobs.ReconcileRedo, nil
			case dataThere && !asideThere:
				// Nothing was renamed; a partial copy may be there and Run
				// removes nothing, so this is redo with a name to look at.
				return jobs.ReconcileRedo, nil
			default:
				return jobs.ReconcileStuck, fmt.Errorf("%w: %s's postgres data directory is in a state this step "+
					"cannot name: %s %s, %s %s, %s %s. Look at all three before anything else touches this tenant",
					jobs.ErrRefused, name,
					m.Data, presence(dataThere), aside, presence(asideThere), copyPath, presence(copyThere))
			}
		},
	})
}

// runPGDataMigration is the step body, and it is a small state machine rather
// than a straight line because it is re-entered: by a resume after the worker
// died, and by a retry of the job.
//
// The states it can find, and what each means:
//
//	data present, nothing else          → not started (or nothing to do)
//	data present, copy present          → the copy finished, no rename happened
//	data ABSENT, aside + copy present   → the crash between the two renames
//	data present, aside present         → both renames happened; done
func (p *planner) runPGDataMigration(ctx context.Context, sc *jobs.StepContext, m pgDataMigration) (string, error) {
	files := sc.Ops.Drivers.Files()
	uid, foreign, err := pgDataOwner(ctx, sc, m)
	if err != nil {
		return "", err
	}

	// The names. Checkpointed BEFORE the copy, because they are the only
	// record of what this step touched: a crash after the first rename with no
	// checkpoint would leave a data directory renamed to a name nothing knows.
	copyPath, hadCopy := externalIDValue(sc.Step.ExternalIDs, "pgcopy:")
	aside, hadAside := externalIDValue(sc.Step.ExternalIDs, "pgaside:")
	resumed := hadCopy && hadAside

	if !resumed {
		if !foreign {
			sc.Logf("%s and %s are already owned by uid %d, the account this take runs as: postgres will start on "+
				"the directory as it stands and nothing is copied", m.Data, m.PGData, uid)
			return fmt.Sprintf("no migration needed: the postgres data directory is already uid %d's", uid), nil
		}
		ts := p.stampOf(sc)
		copyPath = filepath.Join(m.Dir, "data."+m.Account+"-"+ts)
		aside = filepath.Join(m.Dir, preHandoverPrefix+ts)
		if err := sc.Checkpoint("pgcopy:"+copyPath, "pgaside:"+aside); err != nil {
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
	copyThere, err := pathExists(ctx, files, copyPath)
	if err != nil {
		return "", err
	}

	// Already done: both renames happened on an earlier attempt.
	if dataThere && asideThere && !copyThere {
		sc.Logf("%s is already this account's copy and the original is at %s: this step ran before", m.Data, aside)
		return p.recordPGDataMigration(sc, m, copyPath, aside, "already migrated")
	}

	switch {
	case dataThere && !asideThere:
		// Nothing renamed yet. A copy from a previous attempt may be sitting
		// there; it is NOT reused — a copy that stopped half way is a postgres
		// data directory missing files, and the only safe thing to say about
		// one is its name.
		if copyThere {
			return "", fmt.Errorf("%w: %s is already there from an earlier attempt of this step and may be "+
				"incomplete; this step never starts a postgres on a copy it did not finish. Remove it "+
				"(`rm -rf %s`) and run the take again", jobs.ErrRefused, copyPath, copyPath)
		}
		bytes, entries, err := files.TreeSize(ctx, m.Data)
		if err != nil {
			return "", fmt.Errorf("measuring %s: %w", m.Data, err)
		}
		free, err := files.DiskFree(ctx, m.Dir)
		if err != nil {
			return "", err
		}
		need := int64(pgDataFreeSpaceFactor) * bytes
		if free < need {
			return "", fmt.Errorf("%w: %s is owned by uid %d and this take runs as %s, so it has to be COPIED — "+
				"%s, which needs 2× that (%s) free, and %s has %s. Free space and run the take again; the "+
				"release warned about this while the tenant was still up",
				jobs.ErrRefused, m.Data, uid, m.Account, megabytes(bytes), megabytes(need), m.Dir, megabytes(free))
		}
		sc.Logf("copying %s (%s, %d entries) to %s as %s: postgres refuses a data directory it does not own",
			m.Data, megabytes(bytes), entries, copyPath, m.Account)
		wrote, written, err := files.CopyTree(ctx, m.Data, copyPath)
		if err != nil {
			return "", fmt.Errorf("copying %s to %s: %w", m.Data, copyPath, err)
		}
		sc.Logf("copied %s in %d entries; every file in %s is %s's", megabytes(wrote), written, copyPath, m.Account)
		if err := files.Rename(ctx, m.Data, aside); err != nil {
			return "", fmt.Errorf("renaming the original %s aside to %s: %w", m.Data, aside, err)
		}
		sc.Logf("the original is now %s and nothing writes to it again", aside)
		fallthrough
	case !dataThere && asideThere && copyThere:
		// Either the fallthrough above, or a resume that found the crash
		// between the two renames. One rename left.
		if err := files.Rename(ctx, copyPath, m.Data); err != nil {
			return "", fmt.Errorf("%w — the original is at %s and this account's copy at %s; renaming the copy into "+
				"place is all that is left, and until it happens this tenant has NO data directory at %s",
				err, aside, copyPath, m.Data)
		}
		return p.recordPGDataMigration(sc, m, copyPath, aside, "migrated")
	default:
		return "", fmt.Errorf("%w: %s's postgres data directory is in a state this step cannot act on: %s %s, %s %s, "+
			"%s %s. Nothing has been changed", jobs.ErrRefused, p.tenant,
			m.Data, presence(dataThere), aside, presence(asideThere), copyPath, presence(copyThere))
	}
}

// recordPGDataMigration writes the two paths into the row. The registry write
// is part of the SAME step as the renames rather than a step of its own,
// because a row that did not name the pre-handover directory would leave the
// abandon with nothing to swap back — and the window between two steps is
// exactly where the worker died on the run that made this necessary.
func (p *planner) recordPGDataMigration(sc *jobs.StepContext, m pgDataMigration, copyPath, aside, verb string) (string, error) {
	at := p.stampRFC3339(sc)
	err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
		if t.Handover == nil {
			return fmt.Errorf("%w: %s's handover block is gone; something else cleared it while this job was "+
				"running, and the pre-handover postgres directory %s would then be recorded nowhere",
				jobs.ErrRefused, p.tenant, aside)
		}
		t.Handover.PostgresData = &registry.PostgresDataMigration{
			PreHandover: aside, Copy: copyPath, MigratedAt: at,
		}
		return nil
	})
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s: %s is now %s's own copy; the original is %s", verb, m.Data, m.Account, aside), nil
}

// swapPGDataBack puts the two names back: the live copy returns to the name it
// was made under, and the original returns to `data`.
//
// In that ORDER, and neither rename may clobber — which is why the order is
// forced rather than chosen: `data` has to be free before the original can go
// back into it.
//
// It is idempotent in the way its two callers need. A rollback and an abandon
// both run over states somebody else may already have put back by hand, so
// "the original is already at `data`" is success with a sentence, not a
// failure.
func swapPGDataBack(ctx context.Context, sc *jobs.StepContext, data, copyPath, aside string) (string, error) {
	files := sc.Ops.Drivers.Files()
	asideThere, err := pathExists(ctx, files, aside)
	if err != nil {
		return "", err
	}
	if !asideThere {
		// Nothing was moved, or somebody already moved it back.
		return fmt.Sprintf("%s is not there: the original directory was never renamed aside, or has already been "+
			"put back; %s is left exactly as it is", aside, data), nil
	}
	dataThere, err := pathExists(ctx, files, data)
	if err != nil {
		return "", err
	}
	if dataThere {
		if err := files.Rename(ctx, data, copyPath); err != nil {
			return "", fmt.Errorf("moving this account's copy out of %s (to %s) so the original can go back: %w",
				data, copyPath, err)
		}
		sc.Logf("this account's copy is now %s", copyPath)
	}
	if err := files.Rename(ctx, aside, data); err != nil {
		return "", fmt.Errorf("putting the original %s back at %s: %w", aside, data, err)
	}
	return fmt.Sprintf("the original postgres data directory is back at %s (this account's copy is %s, and is "+
		"nobody's data: delete it when the tenant is up)", data, copyPath), nil
}

// ---------------------------------------------------------------- the abandon's half

// addPGDataSwapBack is the ABANDON's half: the tenant goes back to the account
// that released it, and it has to go back to the directory that account owns.
//
// It is planned only when the row RECORDS a migration, which is the only way
// this control plane knows a swap happened at all. An abandon over a handover
// whose take never ran plans nothing here and says so.
func (p *planner) addPGDataSwapBack(h *registry.Handover) {
	pd := h.PostgresData
	if pd == nil {
		p.skip("files", "skip the postgres data directory",
			"this handover's take moved no postgres data directory (handover.postgres_data is null): either the "+
				"tenant runs no postgres of its own, the take never ran, or the taking account already owned it",
			p.tenant)
		return
	}
	data := p.tpaths.PostgresData
	name := p.tenant
	p.addFor("files", step{
		Kind: "fs", Title: "put the postgres data directory back (two renames)", Destructive: true,
		Targets: []string{data},
		WouldWrite: []model.WouldWrite{
			{Path: data, Mode: "unchanged", Preview: model.NullString("")},
			{Path: pd.Copy, Mode: "unchanged", Preview: model.NullString("")},
		},
		Warnings: []string{"the original at " + pd.PreHandover + " has not been written to since the take: putting " +
			"it back is exact, down to the inode",
			"what was serving during the soak — the take's copy — is renamed to " + pd.Copy + " and left there. " +
				"It is a divergent copy of this tenant's postgres from the moment of the take onwards, so it is " +
				"the OPERATOR's to read or delete, never this job's"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			msg, err := swapPGDataBack(ctx, sc, data, pd.Copy, pd.PreHandover)
			if err != nil {
				return "", err
			}
			sc.Logf("%s's postgres data directory is the releasing account's again", name)
			return msg, nil
		},
	})
}

// ---------------------------------------------------------------- shared

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

// presence is how the two "cannot act on this" messages say what they found.
func presence(there bool) string {
	if there {
		return "is there"
	}
	return "is NOT there"
}
