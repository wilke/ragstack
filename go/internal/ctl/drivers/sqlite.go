package drivers

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"

	_ "modernc.org/sqlite" // pure-Go SQLite: CGO_ENABLED=0 stays true
)

// RealSQLite backs up a live SQLite database with VACUUM INTO.
//
// Not a file copy. A ragstack state database runs in WAL mode, which means the
// committed data is split between the `.db` file and a `.db-wal` beside it; a
// copy of the `.db` alone opens cleanly and is missing every write since the
// last checkpoint, which is the kind of backup that is only discovered to be
// wrong on the day it is restored. So: checkpoint the WAL back into the main
// file, ask SQLite whether what is there is coherent, and then have SQLite
// itself write the copy.
type RealSQLite struct{ opts RealOptions }

var _ jobs.SQLite = (*RealSQLite)(nil)

// sqliteDstRe is the destination rule.
//
// VACUUM INTO takes a string LITERAL and not a bound parameter — it is parsed
// as part of the statement, so there is nothing to bind to — which means the
// path is the one place in this package where a value is concatenated into
// SQL. Two guards, not one: the path must match this pattern (the same
// character set paths.SafePath enforces, which contains no quote), and it is
// then quoted by doubling any single quote anyway. The second is redundant
// while the first holds, and it is there because the day somebody widens the
// first is the day the second is the only thing left.
var sqliteDstRe = regexp.MustCompile(`^[A-Za-z0-9._/-]+$`)

// Backup checkpoints src, verifies it, and writes a compacted copy to dst.
func (s *RealSQLite) Backup(ctx context.Context, src, dst string) (string, error) {
	if _, err := paths.SafePath("/", src); err != nil {
		return "", fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	// The RESOLVED destination is what SQLite is told to write and what is
	// chmodded afterwards, so the path the check approved and the path VACUUM
	// INTO creates are one path whatever the parent directory is a link to.
	dst, err := s.checkDst(dst)
	if err != nil {
		return "", err
	}
	// The source has to exist BEFORE the open: sqlite creates an empty
	// database for a path that is not there, and a backup of a state file the
	// tenant never wrote would then succeed with zero rows. fs.ErrNotExist, so
	// that "this tenant has no jobs database" and "the backup failed" are
	// distinguishable with errors.Is — the fake answers the same way.
	if st, err := os.Stat(src); err != nil {
		return "", fmt.Errorf("opening the database to back up: %w", err)
	} else if st.IsDir() {
		return "", fmt.Errorf("opening the database to back up: %s is a directory: %w", src, fs.ErrNotExist)
	}

	// Read-WRITE, because the checkpoint is a write: a read-only handle can
	// neither truncate the WAL nor, on a database whose last writer died, run
	// the recovery that makes integrity_check meaningful.
	db, err := sql.Open("sqlite", "file:"+src+"?_pragma=busy_timeout(10000)")
	if err != nil {
		return "", err
	}
	defer db.Close()
	// One connection: the pragmas below are per-connection, and a pool that
	// handed the checkpoint and the VACUUM to different connections would be
	// checkpointing one handle and copying through another.
	db.SetMaxOpenConns(1)

	if err := checkpoint(ctx, db, src); err != nil {
		return "", err
	}
	integrity, err := integrityCheck(ctx, db, src)
	if err != nil {
		return "", err
	}
	if _, err := db.ExecContext(ctx, "VACUUM INTO '"+quoteSQLite(dst)+"'"); err != nil {
		return "", fmt.Errorf("VACUUM INTO %s from %s: %w", dst, src, err)
	}
	// 0640 after the fact: SQLite creates the file itself, with the process
	// umask, so this is the first moment the mode can be set. The file is a
	// copy of a tenant's state database, inside a bundle directory the ctl
	// made 2770.
	if err := os.Chmod(dst, 0o640); err != nil {
		return "", fmt.Errorf("setting the mode of the backup %s: %w", dst, err)
	}
	return integrity, nil
}

// checkpoint folds the WAL back into the main database file.
//
// TRUNCATE rather than PASSIVE: PASSIVE gives up the moment a reader holds the
// WAL, and reports that it did so in a result column most callers throw away —
// so a backup taken while the tenant is serving would silently be a backup of
// whatever had been checkpointed already.
func checkpoint(ctx context.Context, db *sql.DB, src string) error {
	var busy, logFrames, checkpointed sql.NullInt64
	row := db.QueryRowContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)")
	if err := row.Scan(&busy, &logFrames, &checkpointed); err != nil {
		// A database in journal mode (not WAL) answers the pragma with no
		// rows, which is not a failure: there is no WAL to fold in.
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		return fmt.Errorf("PRAGMA wal_checkpoint(TRUNCATE) on %s: %w", src, err)
	}
	if busy.Int64 != 0 {
		return fmt.Errorf("PRAGMA wal_checkpoint(TRUNCATE) on %s was blocked by another connection; the backup would be missing its most recent writes", src)
	}
	return nil
}

// integrityCheck returns SQLite's own verdict on the database.
//
// The pragma answers one row, "ok", when the database is sound, and one row
// PER PROBLEM when it is not. So both halves are checked: the first row must
// say ok AND it must be the only row — a check that read the first row alone
// would pass a database whose first reported problem happened to be spelled
// that way, and one that counted rows alone would pass an unreadable file that
// produced none.
func integrityCheck(ctx context.Context, db *sql.DB, src string) (string, error) {
	rows, err := db.QueryContext(ctx, "PRAGMA integrity_check")
	if err != nil {
		return "", fmt.Errorf("PRAGMA integrity_check on %s: %w", src, err)
	}
	defer rows.Close()
	var lines []string
	for rows.Next() {
		var line string
		if err := rows.Scan(&line); err != nil {
			return "", err
		}
		lines = append(lines, line)
	}
	if err := rows.Err(); err != nil {
		return "", fmt.Errorf("PRAGMA integrity_check on %s: %w", src, err)
	}
	if len(lines) == 0 {
		return "", fmt.Errorf("PRAGMA integrity_check on %s answered nothing; the file is not a database this build can read", src)
	}
	verdict := lines[0]
	if verdict != "ok" || len(lines) != 1 {
		return verdict, fmt.Errorf("PRAGMA integrity_check on %s reported %s; a corrupt database is not backed up",
			src, strings.Join(lines, "; "))
	}
	return verdict, nil
}

// checkDst enforces every rule on the destination — shape, containment, an
// existing parent, and, the one that matters, that nothing is there yet — and
// returns the RESOLVED path the backup must be written to.
//
// Containment is checked AFTER the symlinks in the parent are resolved: a
// `dst` under a symlinked component is lexically inside an approved root and
// writes wherever the link points, and VACUUM INTO would follow it.
func (s *RealSQLite) checkDst(dst string) (string, error) {
	resolved, err := resolvedContained(dst, s.opts.ApprovedRoots)
	if err != nil {
		return "", err
	}
	// The pattern is checked on the RESOLVED path: that is the string this
	// driver interpolates into the statement.
	if !sqliteDstRe.MatchString(resolved) {
		return "", fmt.Errorf("%w: %q is not a path this driver will interpolate into a VACUUM INTO statement (want %s)",
			jobs.ErrRefused, resolved, sqliteDstRe)
	}
	if st, err := os.Stat(filepath.Dir(resolved)); err != nil || !st.IsDir() {
		return "", fmt.Errorf("%w: %s is not an existing directory to write the backup into", jobs.ErrRefused, filepath.Dir(resolved))
	}
	// VACUUM INTO refuses an existing file itself, but with SQLite's message
	// rather than the ctl's — and refusing here means the ctl never even
	// reaches for a path a previous bundle is using.
	if _, err := os.Lstat(resolved); err == nil {
		return "", fmt.Errorf("%w: %s already exists; a backup never overwrites", jobs.ErrRefused, resolved)
	} else if !errors.Is(err, fs.ErrNotExist) {
		return "", err
	}
	return resolved, nil
}

// quoteSQLite escapes a string literal the only way SQLite defines: a single
// quote inside one is written twice.
func quoteSQLite(s string) string { return strings.ReplaceAll(s, "'", "''") }
