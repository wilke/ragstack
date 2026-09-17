package drivers

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// The postgres driver is tested through a STUB apptainer: a shell script that
// records its argv and does whatever the test needs the container to have
// done. That is the only way to assert on the thing that actually matters
// here — the exact command line, including which host directories are bound
// where — without a postgres image, and it means the test runs on a host that
// has no apptainer at all.

// Deadlines the runner tests use: long enough that a stub script finishes,
// short enough that the timeout case does not slow the suite down.
const (
	testTimeout      = 30 * time.Second
	testShortTimeout = 300 * time.Millisecond
)

// stubApptainer writes a recording script and returns (binary, argv log path).
func stubApptainer(t *testing.T, body string) (string, string) {
	t.Helper()
	dir := t.TempDir()
	bin := filepath.Join(dir, "apptainer")
	log := filepath.Join(dir, "argv.log")
	script := "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + log + "\n" + body
	if err := os.WriteFile(bin, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return bin, log
}

// argvOf reads the recorded arguments.
func argvOf(t *testing.T, log string) []string {
	t.Helper()
	data, err := os.ReadFile(log)
	if err != nil {
		t.Fatalf("the stub apptainer was never run: %v", err)
	}
	return strings.Split(strings.TrimRight(string(data), "\n"), "\n")
}

// pgFixture builds a driver whose approved root is a temp directory, with a
// spec that points at plausible paths inside it.
func pgFixture(t *testing.T, stub string) (*Real, jobs.PostgresSpec, string) {
	t.Helper()
	root := t.TempDir()
	runDir := filepath.Join(root, "postgres", "run")
	if err := os.MkdirAll(runDir, 0o750); err != nil {
		t.Fatal(err)
	}
	sif := filepath.Join(root, "postgres.sif")
	if err := os.WriteFile(sif, []byte("sif"), 0o644); err != nil {
		t.Fatal(err)
	}
	d := NewReal(RealOptions{ApprovedRoots: []string{root}, Apptainer: stub})
	return d, jobs.PostgresSpec{SIF: sif, RunDir: runDir, DB: "ctltest", User: "ctltest", Port: 26005}, root
}

func TestPostgresReadyExecsPgIsreadyOverTheSocketBind(t *testing.T) {
	bin, log := stubApptainer(t, "exit 0\n")
	d, spec, _ := pgFixture(t, bin)
	if err := d.Postgres().Ready(context.Background(), spec); err != nil {
		t.Fatal(err)
	}
	argv := strings.Join(argvOf(t, log), " ")
	for _, want := range []string{
		"exec", "--no-home",
		spec.RunDir + ":/var/run/postgresql",
		spec.SIF, "pg_isready", "-h /var/run/postgresql", "-p 26005",
		"-U ctltest", "-d ctltest",
	} {
		// The socket bind is the whole authentication story: `local all all
		// trust` inside the image is why no password appears anywhere here.
		if !strings.Contains(argv, strings.ReplaceAll(want, " ", " ")) {
			t.Errorf("argv %q is missing %q", argv, want)
		}
	}
	if strings.Contains(argv, "password") || strings.Contains(argv, "PGPASSWORD") {
		t.Errorf("argv carries a credential: %q", argv)
	}
}

func TestPostgresReadyReportsTheToolsOwnOutputOnFailure(t *testing.T) {
	bin, _ := stubApptainer(t, "echo 'no response'; exit 2\n")
	d, spec, _ := pgFixture(t, bin)
	err := d.Postgres().Ready(context.Background(), spec)
	if err == nil || !strings.Contains(err.Error(), "no response") {
		t.Fatalf("Ready = %v, want the tool's own words", err)
	}
}

// pgDumpStub is a stub that behaves like pg_dump: it reads the bind and the
// -f target out of its OWN argv and writes a file there, which is the only
// way to check that the two agree without an actual image.
const pgDumpStub = `
bind=""; out=""
for a in "$@"; do
  case "$a" in
    *:/mnt/ctl) bind=${a%:/mnt/ctl} ;;
    /mnt/ctl/*) out=${a#/mnt/ctl/} ;;
  esac
done
if [ -n "$bind" ] && [ -n "$out" ]; then printf 'PGDMP fake\n' > "$bind/$out"; fi
exit 0
`

func TestPostgresDumpBindsTheOutputDirectoryAndSetsTheMode(t *testing.T) {
	bin, log := stubApptainer(t, pgDumpStub)
	d, spec, root := pgFixture(t, bin)
	bundle := filepath.Join(root, "bundle", "postgres")
	if err := os.MkdirAll(bundle, 0o750); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(bundle, "ctltest.dump")

	if err := d.Postgres().Dump(context.Background(), spec, out); err != nil {
		t.Fatal(err)
	}
	argv := strings.Join(argvOf(t, log), " ")
	// The DIRECTORY is bound, not the file: a bind of a path that does not
	// exist yet is an error, and pg_dump creates the file itself.
	for _, want := range []string{bundle + ":/mnt/ctl", "pg_dump", "-Fc", "-f", "/mnt/ctl/ctltest.dump"} {
		if !strings.Contains(argv, want) {
			t.Errorf("argv %q is missing %q", argv, want)
		}
	}
	st, err := os.Stat(out)
	if err != nil {
		t.Fatal(err)
	}
	// 0640 after the fact, because apptainer creates the file with the host
	// umask and there is no earlier moment to set it.
	if st.Mode().Perm() != 0o640 {
		t.Errorf("dump mode = %o, want 0640", st.Mode().Perm())
	}
}

func TestPostgresDumpRemovesAPartialFileWhenPgDumpFails(t *testing.T) {
	d, spec, root := pgFixture(t, "")
	bundle := filepath.Join(root, "bundle")
	if err := os.MkdirAll(bundle, 0o750); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(bundle, "ctltest.dump")
	bin, _ := stubApptainer(t, "printf 'half a dump' > "+out+"\nexit 1\n")
	d = NewReal(RealOptions{ApprovedRoots: []string{root}, Apptainer: bin})

	if err := d.Postgres().Dump(context.Background(), spec, out); err == nil {
		t.Fatal("a failed pg_dump was reported as success")
	}
	// A short dump opens, restores, and is missing data — worse than none.
	if _, err := os.Stat(out); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("the partial dump survived: %v", err)
	}
}

func TestPostgresRestoreRefusesAnAbsentDumpWithErrNotExist(t *testing.T) {
	bin, _ := stubApptainer(t, "exit 0\n")
	d, spec, root := pgFixture(t, bin)
	err := d.Postgres().Restore(context.Background(), spec, filepath.Join(root, "nope.dump"))
	// fs.ErrNotExist, matching SQLite.Backup, so "the bundle has no postgres
	// dump" is distinguishable from "the restore failed" with errors.Is.
	if !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("Restore of an absent dump = %v, want an fs.ErrNotExist", err)
	}
}

func TestPostgresRestorePassesExitOnErrorAndTheRole(t *testing.T) {
	bin, log := stubApptainer(t, "exit 0\n")
	d, spec, root := pgFixture(t, bin)
	in := filepath.Join(root, "ctltest.dump")
	if err := os.WriteFile(in, []byte("PGDMP"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := d.Postgres().Restore(context.Background(), spec, in); err != nil {
		t.Fatal(err)
	}
	argv := strings.Join(argvOf(t, log), " ")
	// Without --exit-on-error pg_restore logs each failure, carries on, and
	// exits 0 — reporting a half-restored database as a success.
	for _, want := range []string{"pg_restore", "--no-owner", "--role=ctltest", "--exit-on-error", "/mnt/ctl/ctltest.dump"} {
		if !strings.Contains(argv, want) {
			t.Errorf("argv %q is missing %q", argv, want)
		}
	}
}

func TestPostgresRefusesASpecItWillNotBuildACommandLineFrom(t *testing.T) {
	bin, log := stubApptainer(t, "exit 0\n")
	_, good, root := pgFixture(t, bin)
	d := NewReal(RealOptions{ApprovedRoots: []string{root}, Apptainer: bin})

	bad := []struct {
		name string
		spec jobs.PostgresSpec
	}{
		{"relative sif", jobs.PostgresSpec{SIF: "postgres.sif", RunDir: good.RunDir, DB: "a", User: "a"}},
		{"traversing run dir", jobs.PostgresSpec{SIF: good.SIF, RunDir: "/rag/../etc", DB: "a", User: "a"}},
		{"db with a shell character", jobs.PostgresSpec{SIF: good.SIF, RunDir: good.RunDir, DB: "a;drop", User: "a"}},
		{"user with a dot", jobs.PostgresSpec{SIF: good.SIF, RunDir: good.RunDir, DB: "a", User: "a.b"}},
		{"empty db", jobs.PostgresSpec{SIF: good.SIF, RunDir: good.RunDir, DB: "", User: "a"}},
	}
	for _, c := range bad {
		if err := d.Postgres().Ready(context.Background(), c.spec); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Ready with a %s = %v, want a refusal", c.name, err)
		}
	}
	if _, err := os.Stat(log); err == nil {
		t.Fatal("a refused spec still reached apptainer")
	}
}

func TestPostgresDumpRefusesAnOutsideOrUnmadeDestination(t *testing.T) {
	bin, _ := stubApptainer(t, "exit 0\n")
	d, spec, root := pgFixture(t, bin)
	cases := map[string]string{
		"outside the approved roots": filepath.Join(t.TempDir(), "x.dump"),
		"in a directory nobody made": filepath.Join(root, "not", "made", "x.dump"),
	}
	for name, out := range cases {
		if err := d.Postgres().Dump(context.Background(), spec, out); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Dump %s = %v, want a refusal", name, err)
		}
	}
}

func TestPostgresRefusesAnApptainerThatIsNotInstalled(t *testing.T) {
	root := t.TempDir()
	d := NewReal(RealOptions{ApprovedRoots: []string{root}, Apptainer: filepath.Join(root, "no-such-apptainer")})
	spec := jobs.PostgresSpec{SIF: filepath.Join(root, "p.sif"), RunDir: root, DB: "a", User: "a"}
	err := d.Postgres().Ready(context.Background(), spec)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "no such file") {
		t.Fatalf("Ready without apptainer = %v, want a refusal that says so", err)
	}
}

// ---------------------------------------------------------------- census

// The census is the handover's PROOF. A dump and a restore cannot be compared
// byte for byte — the take restores into a cluster it initialised itself — so
// what the release records and the take checks is the exact row count of every
// table, plus the database's size for the free-space arithmetic.
func TestPostgresCensusReadsSizeAndExactRowCountsInOneQuery(t *testing.T) {
	bin, log := stubApptainer(t, "printf '=size=|48234496\\npublic.chunks|88000\\npublic.jobs|12\\n'\nexit 0\n")
	d, spec, _ := pgFixture(t, bin)
	got, err := d.Postgres().Census(context.Background(), spec)
	if err != nil {
		t.Fatal(err)
	}
	if got.SizeBytes != 48234496 {
		t.Errorf("size = %d, want the pg_database_size row", got.SizeBytes)
	}
	if got.Tables["public.chunks"] != 88000 || got.Tables["public.jobs"] != 12 || len(got.Tables) != 2 {
		t.Errorf("tables = %v", got.Tables)
	}
	argv := strings.Join(argvOf(t, log), " ")
	for _, want := range []string{"psql", "-XAt", "ON_ERROR_STOP=1", "count(*)", "pg_database_size"} {
		if !strings.Contains(argv, want) {
			t.Errorf("argv %q is missing %q", argv, want)
		}
	}
	// n_live_tup is an ESTIMATE, it is reset by a restore, and a handover
	// proved with one would have proved nothing.
	if strings.Contains(argv, "n_live_tup") {
		t.Errorf("the census counts with an estimate rather than exactly: %q", argv)
	}
	// Nothing from a registry row reaches the SQL: the query is a literal and
	// the identifiers inside it come from the catalog via format('%I').
	if strings.Contains(argv, "ctltest'") {
		t.Errorf("the tenant name was interpolated into the query: %q", argv)
	}
}

// An EMPTY database is an empty census and no error: a tenant that has never
// been written to is a normal tenant, and a handover must not refuse over one.
func TestPostgresCensusOfAnEmptyDatabaseIsNotAnError(t *testing.T) {
	bin, _ := stubApptainer(t, "printf '=size=|7000000\\n'\nexit 0\n")
	d, spec, _ := pgFixture(t, bin)
	got, err := d.Postgres().Census(context.Background(), spec)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Tables) != 0 || got.SizeBytes != 7000000 {
		t.Errorf("census of an empty database = %+v", got)
	}
}

// A line the parser does not understand is a REFUSAL, not a skip. This answer
// is weighed against a tenant's entire relational state, and a parser that
// dropped what it did not recognise would prove less than it claims while
// looking exactly the same.
func TestPostgresCensusRefusesOutputItCannotParse(t *testing.T) {
	for _, out := range []string{
		"public.chunks|not-a-number\n",
		"WARNING: something\npublic.chunks|1\n",
		"public.chunks\n",
	} {
		b, _ := stubApptainer(t, "printf '"+strings.ReplaceAll(out, "\n", "\\n")+"'\nexit 0\n")
		d, spec, _ := pgFixture(t, b)
		if _, err := d.Postgres().Census(context.Background(), spec); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("census over %q = %v, want a refusal", out, err)
		}
	}
}

// psql's own failure is the census's failure, with its output attached: a
// census that could not be taken must never read as an empty database.
func TestPostgresCensusReportsPsqlsOwnOutputOnFailure(t *testing.T) {
	bin, _ := stubApptainer(t, "echo 'FATAL:  database \"ctltest\" does not exist' >&2; exit 2\n")
	d, spec, _ := pgFixture(t, bin)
	_, err := d.Postgres().Census(context.Background(), spec)
	if err == nil || !strings.Contains(err.Error(), "does not exist") {
		t.Errorf("census over a missing database = %v, want the tool's own words", err)
	}
}
