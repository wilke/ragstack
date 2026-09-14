package drivers

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"

	_ "modernc.org/sqlite"
)

// The SQLite driver is tested against REAL databases. There is nothing to fake
// here: what is being tested is that a copy taken while a WAL is live carries
// the rows, and only an actual sqlite can answer that.

// seedDB makes a WAL-mode database with n rows and leaves it closed.
func seedDB(t *testing.T, path string, n int) {
	t.Helper()
	db, err := sql.Open("sqlite", "file:"+path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("CREATE TABLE rows_ (id INTEGER PRIMARY KEY, payload TEXT)"); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < n; i++ {
		if _, err := db.Exec("INSERT INTO rows_ (payload) VALUES (?)", fmt.Sprintf("row-%d", i)); err != nil {
			t.Fatal(err)
		}
	}
}

// countRows opens path read-only-ish and counts.
func countRows(t *testing.T, path string) int {
	t.Helper()
	db, err := sql.Open("sqlite", "file:"+path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var n int
	if err := db.QueryRow("SELECT count(*) FROM rows_").Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestSQLiteBackupCarriesEveryRowAndReportsOk(t *testing.T) {
	root := t.TempDir()
	src := filepath.Join(root, "jobs.db")
	seedDB(t, src, 250)
	// A live WAL beside the database is the case this driver exists for: a
	// file copy of jobs.db alone would open and be missing these rows.
	if _, err := os.Stat(src + "-wal"); err != nil && !errors.Is(err, fs.ErrNotExist) {
		t.Fatal(err)
	}
	dst := filepath.Join(root, "jobs.db.bak")

	d := NewReal(RealOptions{ApprovedRoots: []string{root}})
	integrity, err := d.SQLite().Backup(context.Background(), src, dst)
	if err != nil {
		t.Fatal(err)
	}
	if integrity != "ok" {
		t.Errorf("integrity = %q, want ok", integrity)
	}
	if n := countRows(t, dst); n != 250 {
		t.Errorf("the copy holds %d rows, want 250", n)
	}
	st, err := os.Stat(dst)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o640 {
		t.Errorf("backup mode = %o, want 0640", st.Mode().Perm())
	}
	// VACUUM INTO writes a checkpointed, single file: a copy with a WAL of its
	// own would be a backup that still needs recovery to be complete.
	if _, err := os.Stat(dst + "-wal"); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("the backup left a WAL beside it: %v", err)
	}
}

func TestSQLiteBackupRefusesACorruptDatabase(t *testing.T) {
	root := t.TempDir()
	src := filepath.Join(root, "jobs.db")
	seedDB(t, src, 40)
	// Corrupt a PAGE rather than the header: a mangled header fails at open,
	// which is a different (and easier) error. This is the case integrity_check
	// exists for — a file that opens and is not sound.
	f, err := os.OpenFile(src, os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteAt(bytes4096(), 4096); err != nil {
		t.Fatal(err)
	}
	f.Close()

	d := NewReal(RealOptions{ApprovedRoots: []string{root}})
	verdict, err := d.SQLite().Backup(context.Background(), src, filepath.Join(root, "out.db"))
	if err == nil {
		t.Fatal("a corrupt database was backed up")
	}
	if verdict == "ok" {
		t.Errorf("verdict = %q, want the problem sqlite reported", verdict)
	}
	// Named, so the test says WHICH guard caught it: the integrity check, not
	// the VACUUM failing afterwards on a file it had already begun writing.
	if !strings.Contains(err.Error(), "integrity_check") {
		t.Errorf("error = %q, want the integrity check to be what refused", err)
	}
	// Nothing is left behind: a bundle must not hold a copy of a database the
	// ctl refused to back up.
	if _, statErr := os.Stat(filepath.Join(root, "out.db")); !errors.Is(statErr, fs.ErrNotExist) {
		t.Errorf("a backup of the corrupt database survived: %v", statErr)
	}
}

// bytes4096 is a page of garbage.
func bytes4096() []byte {
	b := make([]byte, 4096)
	for i := range b {
		b[i] = 0xA5
	}
	return b
}

func TestSQLiteBackupReportsAnAbsentSourceAsErrNotExist(t *testing.T) {
	root := t.TempDir()
	d := NewReal(RealOptions{ApprovedRoots: []string{root}})
	// sqlite CREATES a database for a path that is not there, so without this
	// check a backup of a state file the tenant never wrote would succeed with
	// zero rows. The fake answers the same way, so callers can use errors.Is.
	_, err := d.SQLite().Backup(context.Background(), filepath.Join(root, "nope.db"), filepath.Join(root, "out.db"))
	if !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("Backup of an absent database = %v, want an fs.ErrNotExist", err)
	}
	if _, err := os.Stat(filepath.Join(root, "nope.db")); !errors.Is(err, fs.ErrNotExist) {
		t.Error("the driver created the database it was asked to back up")
	}
}

func TestSQLiteBackupRefusesToOverwrite(t *testing.T) {
	root := t.TempDir()
	src := filepath.Join(root, "jobs.db")
	seedDB(t, src, 3)
	dst := filepath.Join(root, "out.db")
	if err := os.WriteFile(dst, []byte("an earlier bundle's copy"), 0o640); err != nil {
		t.Fatal(err)
	}
	d := NewReal(RealOptions{ApprovedRoots: []string{root}})
	if _, err := d.SQLite().Backup(context.Background(), src, dst); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Backup over an existing file = %v, want a refusal", err)
	}
	if data, _ := os.ReadFile(dst); string(data) != "an earlier bundle's copy" {
		t.Error("the existing file was overwritten anyway")
	}
}

func TestSQLiteBackupRefusesADestinationItWillNotInterpolate(t *testing.T) {
	root := t.TempDir()
	src := filepath.Join(root, "jobs.db")
	seedDB(t, src, 3)
	d := NewReal(RealOptions{ApprovedRoots: []string{root}})

	cases := map[string]string{
		// VACUUM INTO takes a string LITERAL, so this is the one place a value
		// is concatenated into SQL. A quote must never get that far.
		"a single quote":              filepath.Join(root, "a'b.db"),
		"a statement terminator":      filepath.Join(root, "a;b.db"),
		"outside the approved roots":  filepath.Join(t.TempDir(), "out.db"),
		"a directory nobody made":     filepath.Join(root, "not", "made", "out.db"),
		"a relative path":             "out.db",
		"a path with a parent escape": root + "/../out.db",
	}
	for name, dst := range cases {
		if _, err := d.SQLite().Backup(context.Background(), src, dst); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Backup to %s = %v, want a refusal", name, err)
		}
	}
}

func TestQuoteSQLiteDoublesSingleQuotes(t *testing.T) {
	// Redundant while sqliteDstRe holds, and here because the day somebody
	// widens that pattern this is the only thing left.
	if got := quoteSQLite("a'b"); got != "a''b" {
		t.Errorf("quoteSQLite = %q, want a''b", got)
	}
	if got := quoteSQLite("/rag/data/x.db"); !strings.Contains(got, "/rag/data/x.db") {
		t.Errorf("quoteSQLite mangled an ordinary path: %q", got)
	}
}
