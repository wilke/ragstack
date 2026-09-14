package drivers

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

func TestFakeRecordsEveryCallInOrderAcrossDrivers(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{
		Active:      []string{"ragstack-dev-api.service"},
		Listening:   []int{24040},
		Collections: map[string][]string{"http://q": {"b", "a"}},
		Indices:     map[string][]string{"http://e": {"i1"}},
	})
	if err := f.Systemd().Stop(ctx, "ragstack-dev-api.service"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Proc().Listening(ctx, 24040); err != nil {
		t.Fatal(err)
	}
	cols, err := f.Qdrant().Collections(ctx, "http://q")
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(cols, ","); got != "a,b" {
		t.Errorf("collections = %q, want them sorted", got)
	}
	name, err := f.Qdrant().Snapshot(ctx, "http://q", "a")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(name, "a-") || !strings.HasSuffix(name, ".snapshot") {
		t.Errorf("snapshot name = %q", name)
	}
	if _, _, err := f.Gateway().Apply(ctx, false); err != nil {
		t.Fatal(err)
	}

	want := []string{
		"systemd.Stop(ragstack-dev-api.service)",
		"proc.Listening(24040)",
		"qdrant.Collections(http://q)",
		"qdrant.Snapshot(a,http://q)",
		"gateway.Apply(false)",
	}
	got := f.CallKeys()
	if len(got) != len(want) {
		t.Fatalf("calls =\n%v\nwant\n%v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("call %d = %q, want %q", i, got[i], want[i])
		}
	}
	// The fake keeps STATE, not just a log: the unit is stopped, the snapshot
	// is in the ledger and the gateway advanced a generation.
	if f.FakeSystemd().Active["ragstack-dev-api.service"] {
		t.Error("the unit is still active after Stop")
	}
	if len(f.FakeQdrant().Snapshots["a"]) != 1 {
		t.Errorf("snapshot ledger = %v", f.FakeQdrant().Snapshots)
	}
	if f.FakeGateway().Generation != 1 {
		t.Errorf("generation = %d, want 1", f.FakeGateway().Generation)
	}
}

func TestFakeFailsExactlyTheNamedCall(t *testing.T) {
	ctx := context.Background()
	boom := errors.New("boom")
	f := NewFake(FakeOptions{})
	f.Fail("systemd.Start:ragstack-dev-es.service", boom)
	if err := f.Systemd().Start(ctx, "ragstack-dev-qdrant.service"); err != nil {
		t.Fatalf("another unit must not fail: %v", err)
	}
	if err := f.Systemd().Start(ctx, "ragstack-dev-es.service"); !errors.Is(err, boom) {
		t.Fatalf("the named unit did not fail: %v", err)
	}
	// A failed call is still recorded — that is how a test sees WHERE a job
	// stopped — and it did not change the state.
	if n := f.Count("systemd.Start"); n != 2 {
		t.Errorf("Start calls = %d, want 2", n)
	}
	if f.FakeSystemd().Active["ragstack-dev-es.service"] {
		t.Error("a failed Start must not mark the unit active")
	}
}

func TestFakeFilesHonoursTheApprovedRoots(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag/data/tenants"}})
	if err := f.Files().WriteAtomic(ctx, "/rag/data/tenants/dev/config/tenant.env", []byte("A=1\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	err := f.Files().WriteAtomic(ctx, "/etc/passwd", []byte("x"), 0o644)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a write outside the approved roots must be refused, got %v", err)
	}
	if got := string(f.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env")); got != "A=1\n" {
		t.Errorf("content = %q", got)
	}
}

// ---------------------------------------------------------------- real files

func realFiles(t *testing.T) (*RealFiles, string) {
	t.Helper()
	root := t.TempDir()
	return &RealFiles{Roots: []string{root}}, root
}

func TestRealFilesWritesAtomicallyWithTheModeSetBeforeTheRename(t *testing.T) {
	f, root := realFiles(t)
	path := filepath.Join(root, "secrets.env")
	if err := f.WriteAtomic(context.Background(), path, []byte("API_KEYS='[]'\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o640 {
		t.Errorf("mode = %v, want 0640 — a secrets file must never be readable at 0600→0644 for even a moment", st.Mode().Perm())
	}
	// No temporary file is left behind.
	ents, _ := os.ReadDir(root)
	if len(ents) != 1 {
		t.Errorf("directory holds %d entries, want only the final file", len(ents))
	}
}

func TestRealFilesRefusesOutsideTheApprovedRoots(t *testing.T) {
	f, _ := realFiles(t)
	other := filepath.Join(t.TempDir(), "x")
	for name, err := range map[string]error{
		"write":  f.WriteAtomic(context.Background(), other, []byte("x"), 0o600),
		"remove": f.Remove(context.Background(), other),
		"rename": f.Rename(context.Background(), other, other+"2"),
	} {
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("%s outside the roots = %v, want a refusal", name, err)
		}
	}
}

func TestRealFilesRemoveNeverFollowsASymlink(t *testing.T) {
	f, root := realFiles(t)
	target := filepath.Join(root, "target")
	if err := os.WriteFile(target, []byte("precious"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	err := f.Remove(context.Background(), link)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("removing through a symlink = %v, want a refusal", err)
	}
	if _, err := os.Stat(target); err != nil {
		t.Fatalf("the symlink's TARGET was touched: %v", err)
	}
	// An absent path is not an error: Remove is idempotent, which is what a
	// rollback re-run needs.
	if err := f.Remove(context.Background(), filepath.Join(root, "gone")); err != nil {
		t.Errorf("removing an absent path = %v, want nil", err)
	}
}

// ---------------------------------------------------------------- PR-D

// pendingSurfaces pairs each driver name with the interface it satisfies and
// the value the REAL set hands out for it.
//
// The interface TYPE is what makes this table worth having: the test walks
// every method the interface declares, so a method added to jobs.Systemd
// tomorrow is covered the moment it exists. Listing the calls by hand — which
// is what this test used to do — covers only the methods somebody remembered,
// and the one that is forgotten is the one that panics on a host.
func pendingSurfaces(d *Real) map[string]struct {
	typ reflect.Type
	val reflect.Value
} {
	type surface = struct {
		typ reflect.Type
		val reflect.Value
	}
	return map[string]surface{
		"systemd": {reflect.TypeOf((*jobs.Systemd)(nil)).Elem(), reflect.ValueOf(d.Systemd())},
		"proc":    {reflect.TypeOf((*jobs.Proc)(nil)).Elem(), reflect.ValueOf(d.Proc())},
		"git":     {reflect.TypeOf((*jobs.Git)(nil)).Elem(), reflect.ValueOf(d.Git())},
		"build":   {reflect.TypeOf((*jobs.Build)(nil)).Elem(), reflect.ValueOf(d.Build())},
	}
}

func TestRealDriversRefuseEveryMethodThatLandsInPRD(t *testing.T) {
	d := NewReal(RealOptions{Roots: paths.NewRoots(t.TempDir(), paths.Overrides{})})
	surfaces := pendingSurfaces(d)
	// The table and Pending() describe the same set, or one of them is lying
	// to an operator reading a plan.
	if len(surfaces) != len(d.Pending()) {
		t.Fatalf("the table covers %d drivers, Pending() names %d (%v)", len(surfaces), len(d.Pending()), d.Pending())
	}
	for _, name := range d.Pending() {
		if _, ok := surfaces[name]; !ok {
			t.Fatalf("%s is reported pending but this test does not cover it", name)
		}
	}
	for name, s := range surfaces {
		for i := 0; i < s.typ.NumMethod(); i++ {
			m := s.typ.Method(i)
			// Zero arguments throughout: a pending method must refuse before
			// it looks at anything, so a nil context and empty strings are
			// exactly the call that proves it.
			args := make([]reflect.Value, m.Type.NumIn())
			for j := range args {
				args[j] = reflect.Zero(m.Type.In(j))
			}
			out := s.val.MethodByName(m.Name).Call(args)
			if len(out) == 0 {
				t.Fatalf("%s.%s returns nothing; every driver method must be able to refuse", name, m.Name)
			}
			last := out[len(out)-1]
			err, ok := last.Interface().(error)
			if !ok {
				t.Fatalf("%s.%s does not return an error last", name, m.Name)
			}
			if !errors.Is(err, jobs.ErrRefused) {
				t.Errorf("%s.%s = %v, want a jobs.ErrRefused", name, m.Name, err)
				continue
			}
			want := name + "." + m.Name + " lands in PR-D"
			if !strings.Contains(err.Error(), want) {
				t.Errorf("%s.%s = %q, want it to contain %q", name, m.Name, err, want)
			}
		}
	}
	// The wired ones are real.
	if _, ok := d.Files().(*RealFiles); !ok {
		t.Error("Files() is not the real driver")
	}
	if _, ok := d.Gateway().(*RealGateway); !ok {
		t.Error("Gateway() is not the real driver")
	}
	if _, ok := d.Qdrant().(*RealQdrant); !ok {
		t.Error("Qdrant() is not the real driver")
	}
	if _, ok := d.Elasticsearch().(*RealElasticsearch); !ok {
		t.Error("Elasticsearch() is not the real driver")
	}
	if _, ok := d.TenantAPI().(*RealTenantAPI); !ok {
		t.Error("TenantAPI() is not the real driver")
	}
	if _, ok := d.Postgres().(*RealPostgres); !ok {
		t.Error("Postgres() is not the real driver")
	}
	if _, ok := d.SQLite().(*RealSQLite); !ok {
		t.Error("SQLite() is not the real driver")
	}
	if _, ok := d.Archive().(*RealArchive); !ok {
		t.Error("Archive() is not the real driver")
	}
}

func TestRealGatewayRefusesWithoutARegistryLoader(t *testing.T) {
	d := NewReal(RealOptions{Roots: paths.NewRoots(t.TempDir(), paths.Overrides{})})
	if _, _, err := d.Gateway().Apply(context.Background(), true); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Apply without a Fleet loader = %v, want a refusal", err)
	}
}

// ---------------------------------------------------------------- containment

func TestRealFilesRefusesASymlinkedDirectoryComponent(t *testing.T) {
	f, root := realFiles(t)
	outside := t.TempDir()
	// `<root>/escape` is a link to a directory nobody approved. Every
	// character of `<root>/escape/loot` is under the root, so a LEXICAL check
	// says yes; the file it names is not.
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	loot := filepath.Join(root, "escape", "loot")
	err := f.WriteAtomic(context.Background(), loot, []byte("x"), 0o600)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("writing through a symlinked directory = %v, want a refusal", err)
	}
	if _, statErr := os.Stat(filepath.Join(outside, "loot")); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatalf("the write landed outside the approved roots: %v", statErr)
	}
	if err := f.Remove(context.Background(), loot); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("removing through a symlinked directory = %v, want a refusal", err)
	}
	if err := f.Rename(context.Background(), loot, loot+"2"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("renaming through a symlinked directory = %v, want a refusal", err)
	}
}

func TestRealFilesAcceptsASymlinkedAPPROVEDRoot(t *testing.T) {
	// The deployment root itself may be a link (/rag/data -> /mnt/…). That is
	// a normal host, not an escape: the resolved path is still inside it.
	real := t.TempDir()
	link := filepath.Join(t.TempDir(), "data")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	f := &RealFiles{Roots: []string{link}}
	p := filepath.Join(link, "f")
	if err := f.WriteAtomic(context.Background(), p, []byte("x"), 0o600); err != nil {
		t.Fatalf("writing under a symlinked approved root = %v, want it accepted", err)
	}
	if _, err := os.Stat(filepath.Join(real, "f")); err != nil {
		t.Fatalf("the file is not where the link points: %v", err)
	}
}

func TestFilesDriversWithNoApprovedRootsRefuseEverything(t *testing.T) {
	ctx := context.Background()
	p := filepath.Join(t.TempDir(), "f")
	real := &RealFiles{}
	if err := real.WriteAtomic(ctx, p, []byte("x"), 0o600); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("RealFiles with no roots wrote %s: %v", p, err)
	}
	fake := NewFake(FakeOptions{})
	if err := fake.Files().WriteAtomic(ctx, p, []byte("x"), 0o600); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("FakeFiles with no roots wrote %s: %v", p, err)
	}
}

func TestFakeReadFileReportsAbsenceAsErrNotExist(t *testing.T) {
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	_, err := f.Files().ReadFile(context.Background(), "/rag/nope")
	if !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("ReadFile of an absent path = %v, want an os.ErrNotExist a caller can match", err)
	}
}

func TestPendingIsTheSameListTheRealDriversRefuseWith(t *testing.T) {
	d := NewReal(RealOptions{Roots: paths.NewRoots(t.TempDir(), paths.Overrides{})})
	got := strings.Join(d.Pending(), ",")
	if got != "systemd,proc,git,build" {
		t.Errorf("Real.Pending() = %q", got)
	}
	// Every name on the list is a driver that actually refuses, and no driver
	// that WORKS is on it.
	if err := d.Systemd().DaemonReload(context.Background()); !strings.Contains(err.Error(), PendingPR) {
		t.Errorf("systemd refusal = %v, want it to name %s", err, PendingPR)
	}
	for _, name := range d.Pending() {
		switch name {
		case "gateway", "files", "qdrant", "elasticsearch", "tenantapi", "postgres", "sqlite", "archive":
			t.Errorf("%s is wired; it must not be reported as pending", name)
		}
	}
	if p := NewFake(FakeOptions{}).Pending(); len(p) != 0 {
		t.Errorf("Fake.Pending() = %v, want none: the fakes run every driver", p)
	}
}

// ---------------------------------------------------------------- PR-D fakes

const (
	qURL   = "http://127.0.0.1:26333"
	esURL  = "http://127.0.0.1:26334"
	origin = "http://127.0.0.1:26340"
	fakeWT = "/rag/state/artifacts/main-abc/worktree"
	sha40  = "0123456789abcdef0123456789abcdef01234567"
)

// seededFake is a host on which every PR-D driver call can SUCCEED: a ref to
// resolve, a repository with a snapshot in it, node_modules installed, a state
// file to back up. The prep calls are cleared from the log, so what a test
// then asserts about the log is what the test itself did.
func seededFake(t *testing.T) *Fake {
	t.Helper()
	ctx := context.Background()
	f := NewFake(FakeOptions{
		Roots:               []string{"/rag"},
		Refs:                map[string]string{"main": sha40},
		Installed:           []string{fakeWT},
		Files:               map[string][]byte{"/rag/data/tenants/dev/state/jobs.db": []byte("sqlite-ish")},
		Collections:         map[string][]string{qURL: {"docs"}},
		Indices:             map[string][]string{esURL: {"dev-chunks"}},
		QdrantCounts:        map[string]int64{qURL + "/docs": 41},
		ESCounts:            map[string]int64{esURL + "/dev-chunks": 7},
		Owners:              map[int]PortOwner{26340: {PID: 4242, UID: 3581}},
		Versions:            map[string]map[string]any{origin: {"version": "0.9.1"}},
		CollectionsByOrigin: map[string][]string{origin: {"docs"}},
	})
	if err := f.Elasticsearch().RegisterRepo(ctx, esURL, "ctl-b1", "/rag/data/tenants/dev/elasticsearch/snapshots/b1", false); err != nil {
		t.Fatal(err)
	}
	if err := f.Elasticsearch().Snapshot(ctx, esURL, "ctl-b1", "snap-1"); err != nil {
		t.Fatal(err)
	}
	f.Clear()
	return f
}

// prdCalls is every method the PR-D seam added to the fakes, as a call that
// SUCCEEDS on seededFake. The key is the one the recorder and the failure
// table use.
var prdCalls = []struct {
	key  string
	call func(context.Context, *Fake) error
}{
	{"systemd.Link", func(ctx context.Context, f *Fake) error {
		return f.Systemd().Link(ctx, "/rag/config/ctl/units/ragstack-dev-api.service")
	}},
	{"systemd.IsEnabled", func(ctx context.Context, f *Fake) error {
		_, err := f.Systemd().IsEnabled(ctx, "ragstack-dev.target")
		return err
	}},
	{"systemd.Show", func(ctx context.Context, f *Fake) error {
		_, err := f.Systemd().Show(ctx, "ragstack-dev.target")
		return err
	}},
	{"systemd.ResetFailed", func(ctx context.Context, f *Fake) error {
		return f.Systemd().ResetFailed(ctx, "ragstack-dev-api.service")
	}},
	{"proc.Owner", func(ctx context.Context, f *Fake) error {
		_, _, err := f.Proc().Owner(ctx, 26340)
		return err
	}},
	{"files.MkdirAll", func(ctx context.Context, f *Fake) error {
		return f.Files().MkdirAll(ctx, "/rag/data/tenants/dev/logs", 0o2770)
	}},
	{"qdrant.Ready", func(ctx context.Context, f *Fake) error { return f.Qdrant().Ready(ctx, qURL) }},
	{"qdrant.Count", func(ctx context.Context, f *Fake) error {
		_, err := f.Qdrant().Count(ctx, qURL, "docs")
		return err
	}},
	{"qdrant.Recover", func(ctx context.Context, f *Fake) error {
		return f.Qdrant().Recover(ctx, qURL, "docs", "file:///qdrant/snapshots/docs/s.snapshot")
	}},
	{"qdrant.DeleteSnapshot", func(ctx context.Context, f *Fake) error {
		return f.Qdrant().DeleteSnapshot(ctx, qURL, "docs", "s.snapshot")
	}},
	{"es.Ready", func(ctx context.Context, f *Fake) error { return f.Elasticsearch().Ready(ctx, esURL) }},
	{"es.RegisterRepo", func(ctx context.Context, f *Fake) error {
		return f.Elasticsearch().RegisterRepo(ctx, esURL, "ctl-b1", "/rag/data/tenants/dev/elasticsearch/snapshots/b1", false)
	}},
	{"es.Restore", func(ctx context.Context, f *Fake) error {
		return f.Elasticsearch().Restore(ctx, esURL, "ctl-b1", "snap-1", []string{"dev-chunks"})
	}},
	{"es.Count", func(ctx context.Context, f *Fake) error {
		_, err := f.Elasticsearch().Count(ctx, esURL, "dev-chunks")
		return err
	}},
	{"es.UnregisterRepo", func(ctx context.Context, f *Fake) error {
		return f.Elasticsearch().UnregisterRepo(ctx, esURL, "ctl-b1")
	}},
	{"tenantapi.ServiceAccount", func(ctx context.Context, f *Fake) error {
		return f.TenantAPI().ServiceAccount(ctx, origin, "secret", "gowe", "user", "workflow engine", "create")
	}},
	{"tenantapi.Version", func(ctx context.Context, f *Fake) error {
		_, err := f.TenantAPI().Version(ctx, origin, "secret")
		return err
	}},
	{"tenantapi.DeepHealth", func(ctx context.Context, f *Fake) error {
		return f.TenantAPI().DeepHealth(ctx, origin, "secret")
	}},
	{"tenantapi.Collections", func(ctx context.Context, f *Fake) error {
		_, err := f.TenantAPI().Collections(ctx, origin, "secret")
		return err
	}},
	{"git.ResolveRef", func(ctx context.Context, f *Fake) error {
		_, err := f.Git().ResolveRef(ctx, "/rag/repos/ragstack.git", "main")
		return err
	}},
	{"git.AddWorktree", func(ctx context.Context, f *Fake) error {
		return f.Git().AddWorktree(ctx, "/rag/repos/ragstack.git", sha40, "/rag/repos/tenants/dev")
	}},
	{"git.RemoveWorktree", func(ctx context.Context, f *Fake) error {
		return f.Git().RemoveWorktree(ctx, "/rag/repos/ragstack.git", "/rag/repos/tenants/dev")
	}},
	{"git.Describe", func(ctx context.Context, f *Fake) error {
		_, err := f.Git().Describe(ctx, fakeWT)
		return err
	}},
	{"build.NpmCI", func(ctx context.Context, f *Fake) error {
		return f.Build().NpmCI(ctx, fakeWT, "/rag/cache/npm")
	}},
	{"build.UI", func(ctx context.Context, f *Fake) error {
		return f.Build().UI(ctx, fakeWT, "/ragstack/dev/ui/", "/rag/data/tenants/dev/ui/dist")
	}},
	{"postgres.Ready", func(ctx context.Context, f *Fake) error { return f.Postgres().Ready(ctx, pgSpec()) }},
	{"postgres.Dump", func(ctx context.Context, f *Fake) error {
		return f.Postgres().Dump(ctx, pgSpec(), "/rag/backups/dev/b1/postgres/dev.dump")
	}},
	{"postgres.Restore", func(ctx context.Context, f *Fake) error {
		return f.Postgres().Restore(ctx, pgSpec(), "/rag/backups/dev/b1/postgres/dev.dump")
	}},
	{"sqlite.Backup", func(ctx context.Context, f *Fake) error {
		_, err := f.SQLite().Backup(ctx, "/rag/data/tenants/dev/state/jobs.db", "/rag/backups/dev/b1/state/jobs.db")
		return err
	}},
	{"archive.Create", func(ctx context.Context, f *Fake) error {
		return f.Archive().Create(ctx, "/rag/backups/dev/b1", "/rag/backups/dev/b1.tar")
	}},
	{"archive.Extract", func(ctx context.Context, f *Fake) error {
		return f.Archive().Extract(ctx, "/rag/backups/dev/b1.tar", "/rag/backups/dev/restore",
			jobs.ArchiveLimits{MaxEntries: 10000, MaxBytes: 1 << 30})
	}},
}

func pgSpec() jobs.PostgresSpec {
	return jobs.PostgresSpec{
		SIF: "/rag/apptainer/images/postgres.sif", RunDir: "/rag/data/tenants/dev/postgres/run",
		DB: "dev", User: "dev",
	}
}

func TestEveryPRDFakeRecordsItsCall(t *testing.T) {
	ctx := context.Background()
	for _, c := range prdCalls {
		t.Run(c.key, func(t *testing.T) {
			f := seededFake(t)
			if err := c.call(ctx, f); err != nil {
				t.Fatalf("%s on a seeded host = %v, want it to succeed", c.key, err)
			}
			if n := f.Count(c.key); n != 1 {
				t.Errorf("%s was recorded %d times, want 1 (log: %v)", c.key, n, f.CallKeys())
			}
		})
	}
}

func TestEveryPRDFakeHonoursTheFailureTable(t *testing.T) {
	ctx := context.Background()
	boom := errors.New("boom")
	for _, c := range prdCalls {
		t.Run(c.key, func(t *testing.T) {
			f := seededFake(t)
			f.Fail(c.key, boom)
			if err := c.call(ctx, f); !errors.Is(err, boom) {
				t.Fatalf("%s with the failure table armed = %v, want boom", c.key, err)
			}
		})
	}
}

func TestFakeSystemdShowDerivesTheStateItWasPutIn(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Failed: []string{"ragstack-dev-es.service"}})
	sysd := f.Systemd()

	// A unit this manager has never heard of is the ZERO UnitInfo — that is
	// how `systemctl show` answers for a unit that is not loaded, and how
	// decommission proves the units are gone.
	unknown, err := sysd.Show(ctx, "ragstack-gone-api.service")
	if err != nil {
		t.Fatal(err)
	}
	if unknown.FragmentPath != "" || unknown.ActiveState != "" {
		t.Errorf("an unknown unit showed %+v, want the zero UnitInfo", unknown)
	}

	unit := "ragstack-dev-api.service"
	if err := sysd.Link(ctx, "/rag/config/ctl/units/"+unit); err != nil {
		t.Fatal(err)
	}
	linked, err := sysd.Show(ctx, unit)
	if err != nil {
		t.Fatal(err)
	}
	if linked.FragmentPath != "/rag/config/ctl/units/"+unit || linked.UnitFileState != "linked" {
		t.Errorf("after Link, Show = %+v", linked)
	}
	if linked.ActiveState != "inactive" || linked.MainPID != 0 {
		t.Errorf("a linked but unstarted unit = %+v, want inactive with no main pid", linked)
	}
	if err := sysd.Start(ctx, unit); err != nil {
		t.Fatal(err)
	}
	started, err := sysd.Show(ctx, unit)
	if err != nil {
		t.Fatal(err)
	}
	if started.ActiveState != "active" || started.SubState != "running" || started.MainPID == 0 {
		t.Errorf("after Start, Show = %+v, want an active unit with a main pid", started)
	}
	if err := sysd.Enable(ctx, unit); err != nil {
		t.Fatal(err)
	}
	if enabled, _ := sysd.Show(ctx, unit); enabled.UnitFileState != "enabled" {
		t.Errorf("after Enable, UnitFileState = %q", enabled.UnitFileState)
	}
	if ok, err := sysd.IsEnabled(ctx, unit); err != nil || !ok {
		t.Errorf("IsEnabled = %v, %v", ok, err)
	}

	// A failed unit says so until it is reset — which is why ResetFailed
	// exists: systemd's start limit refuses the retry otherwise.
	failed, _ := sysd.Show(ctx, "ragstack-dev-es.service")
	if failed.ActiveState != "failed" || failed.Result != "exit-code" {
		t.Errorf("a failed unit = %+v", failed)
	}
	if err := sysd.ResetFailed(ctx, "ragstack-dev-es.service"); err != nil {
		t.Fatal(err)
	}
	if after, _ := sysd.Show(ctx, "ragstack-dev-es.service"); after.ActiveState == "failed" {
		t.Errorf("after ResetFailed the unit is still failed: %+v", after)
	}
}

func TestFakeProcOwnerReportsPIDZeroForAForeignSocket(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Listening: []int{24040, 24041}, Owners: map[int]PortOwner{24040: {PID: 4242, UID: 3581}}})
	pid, uid, err := f.Proc().Owner(ctx, 24040)
	if err != nil || pid != 4242 || uid != 3581 {
		t.Errorf("Owner(24040) = %d, %d, %v", pid, uid, err)
	}
	// Bound, but by somebody this reader cannot see: pid 0 and NO error, the
	// same answer `ss -ltnp` gives an unprivileged caller.
	pid, _, err = f.Proc().Owner(ctx, 24041)
	if err != nil || pid != 0 {
		t.Errorf("a foreign socket = pid %d, %v, want pid 0 and no error", pid, err)
	}
	if pid, _, err := f.Proc().Owner(ctx, 24099); err != nil || pid != 0 {
		t.Errorf("an unbound port = pid %d, %v, want pid 0 and no error", pid, err)
	}
}

func TestFakeQdrantCountsRecoversAndDeletesSnapshots(t *testing.T) {
	ctx := context.Background()
	f := seededFake(t)
	q := f.Qdrant()
	if n, _ := q.Count(ctx, qURL, "docs"); n != 41 {
		t.Errorf("Count = %d, want the seeded 41", n)
	}
	if n, _ := q.Count(ctx, qURL, "never-existed"); n != 0 {
		t.Errorf("an unknown collection counted %d, want 0", n)
	}
	name, err := q.Snapshot(ctx, qURL, "docs")
	if err != nil {
		t.Fatal(err)
	}
	if err := q.DeleteSnapshot(ctx, qURL, "docs", name); err != nil {
		t.Fatal(err)
	}
	if got := f.FakeQdrant().Snapshots["docs"]; len(got) != 0 {
		t.Errorf("the snapshot ledger still holds %v after DeleteSnapshot", got)
	}
	// Recovering into a URL that did not hold the collection makes it exist,
	// so the verification step that follows reads the effect of the recover.
	if err := q.Recover(ctx, qURL, "restored", "file:///qdrant/snapshots/restored/s.snapshot"); err != nil {
		t.Fatal(err)
	}
	if got := f.FakeQdrant().Recovered; len(got) != 1 ||
		got[0] != qURL+" restored file:///qdrant/snapshots/restored/s.snapshot" {
		t.Errorf("Recovered = %v", got)
	}
	cols, _ := q.Collections(ctx, qURL)
	if !containsString(cols, "restored") {
		t.Errorf("collections after a recover = %v, want the recovered one present", cols)
	}
}

func TestFakeElasticsearchRepoAndRestoreRules(t *testing.T) {
	ctx := context.Background()
	f := seededFake(t)
	es := f.Elasticsearch()
	// The same repo name at a second location is refused: two bundles sharing
	// a repo name would each believe the other's directory was theirs.
	err := es.RegisterRepo(ctx, esURL, "ctl-b1", "/rag/data/tenants/dev/elasticsearch/snapshots/OTHER", false)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("re-registering at another location = %v, want a refusal", err)
	}
	// The same location again is idempotent, and may flip readonly — that is
	// the verify leg re-opening a copied directory.
	if err := es.RegisterRepo(ctx, esURL, "ctl-b1", "/rag/data/tenants/dev/elasticsearch/snapshots/b1", true); err != nil {
		t.Errorf("re-registering the same location = %v, want it accepted", err)
	}
	if got := f.FakeElasticsearch().Repos["ctl-b1"]; !got.ReadOnly {
		t.Errorf("the repo is %+v, want readonly after the second registration", got)
	}
	if err := es.Restore(ctx, esURL, "ctl-nope", "snap-1", nil); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("restoring from an unregistered repo = %v, want a refusal", err)
	}
	if err := es.Restore(ctx, esURL, "ctl-b1", "snap-404", nil); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("restoring an unknown snapshot = %v, want a refusal", err)
	}
	if err := es.Restore(ctx, esURL, "ctl-b1", "snap-1", []string{"dev-chunks"}); err != nil {
		t.Fatal(err)
	}
	if got := f.FakeElasticsearch().Restored; len(got) != 1 || got[0] != "ctl-b1 snap-1 dev-chunks" {
		t.Errorf("Restored = %v", got)
	}
	if n, _ := es.Count(ctx, esURL, "dev-chunks"); n != 7 {
		t.Errorf("Count = %d, want the seeded 7", n)
	}
	if err := es.UnregisterRepo(ctx, esURL, "ctl-b1"); err != nil {
		t.Fatal(err)
	}
	if _, ok := f.FakeElasticsearch().Repos["ctl-b1"]; ok {
		t.Error("the repo survived UnregisterRepo")
	}
	if err := es.UnregisterRepo(ctx, esURL, "ctl-b1"); err != nil {
		t.Errorf("unregistering twice = %v, want it to be a no-op: it is a rollback", err)
	}
}

func TestFakeTenantAPIRecordsTheRoleAndNeverTheKey(t *testing.T) {
	ctx := context.Background()
	f := seededFake(t)
	api := f.TenantAPI()
	if err := api.ServiceAccount(ctx, origin, "sk-live-do-not-log", "gowe", "user", "workflow engine", "create"); err != nil {
		t.Fatal(err)
	}
	want := origin + " create gowe user"
	if got := f.FakeTenantAPI().Accounts; len(got) != 1 || got[0] != want {
		t.Errorf("Accounts = %v, want [%q]", got, want)
	}
	for _, c := range f.CallKeys() {
		if strings.Contains(c, "sk-live-do-not-log") {
			t.Fatalf("the API key reached the call log: %s", c)
		}
	}
	v, err := api.Version(ctx, origin, "k")
	if err != nil || v["version"] != "0.9.1" {
		t.Errorf("Version = %v, %v", v, err)
	}
	unknown, err := api.Version(ctx, "http://127.0.0.1:1", "k")
	if err != nil || unknown["version"] != "fake" {
		t.Errorf("Version of an unseeded origin = %v, %v, want the fake default", unknown, err)
	}
	if err := api.DeepHealth(ctx, origin, "k"); err != nil {
		t.Fatal(err)
	}
	cols, err := api.Collections(ctx, origin, "k")
	if err != nil || strings.Join(cols, ",") != "docs" {
		t.Errorf("Collections = %v, %v", cols, err)
	}
}

func TestFakeGitResolvesRefsAndTracksWorktrees(t *testing.T) {
	ctx := context.Background()
	f := seededFake(t)
	g := f.Git()
	if sha, err := g.ResolveRef(ctx, "/mirror", "main"); err != nil || sha != sha40 {
		t.Errorf("ResolveRef(main) = %q, %v", sha, err)
	}
	// A sha resolves to itself without a table entry — `--tag <sha>` is a
	// documented way to pin an artifact.
	if sha, err := g.ResolveRef(ctx, "/mirror", sha40); err != nil || sha != sha40 {
		t.Errorf("ResolveRef(<sha>) = %q, %v", sha, err)
	}
	if _, err := g.ResolveRef(ctx, "/mirror", "v9.9.9"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("ResolveRef of an unknown ref = %v, want a refusal", err)
	}
	dest := "/rag/repos/tenants/dev"
	if err := g.AddWorktree(ctx, "/mirror", sha40, dest); err != nil {
		t.Fatal(err)
	}
	if got := f.FakeGit().Worktrees[dest]; got != sha40 {
		t.Errorf("Worktrees[%s] = %q", dest, got)
	}
	if d, err := g.Describe(ctx, dest); err != nil || d != sha40[:12] {
		t.Errorf("Describe = %q, %v", d, err)
	}
	if err := g.RemoveWorktree(ctx, "/mirror", dest); err != nil {
		t.Fatal(err)
	}
	if _, ok := f.FakeGit().Worktrees[dest]; ok {
		t.Error("the worktree survived RemoveWorktree")
	}
}

func TestFakeBuildRefusesWithoutNodeModulesAndWritesTheDist(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	b, wt, out := f.Build(), "/rag/state/artifacts/x/worktree", "/rag/data/tenants/dev/ui/dist"
	if err := b.UI(ctx, wt, "/ragstack/dev/ui/", out); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("UI without node_modules = %v, want a refusal: a create must never install from the network", err)
	}
	if err := b.NpmCI(ctx, wt, "/rag/cache/npm"); err != nil {
		t.Fatal(err)
	}
	if err := b.UI(ctx, wt, "/ragstack/dev/ui/", out); err != nil {
		t.Fatal(err)
	}
	if got := f.FakeBuild().Builds; len(got) != 1 || got[0] != wt+" /ragstack/dev/ui/ "+out {
		t.Errorf("Builds = %v", got)
	}
	if f.FakeFiles().Content(out+"/index.html") == nil {
		t.Error("the build produced no index.html for the steps that read the dist tree")
	}
}

func TestFakePostgresReadinessAndDump(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	// An unseeded run directory is READY: the ordinary fixture is a store
	// that works.
	if err := f.Postgres().Ready(ctx, pgSpec()); err != nil {
		t.Errorf("Ready on an unseeded host = %v, want nil", err)
	}
	down := NewFake(FakeOptions{PostgresReady: map[string]bool{pgSpec().RunDir: false}})
	if err := down.Postgres().Ready(ctx, pgSpec()); err == nil {
		t.Error("a run directory seeded not-ready answered ready")
	}
	out := "/rag/backups/dev/b1/postgres/dev.dump"
	if err := f.Postgres().Dump(ctx, pgSpec(), out); err != nil {
		t.Fatal(err)
	}
	if f.FakeFiles().Content(out) == nil {
		t.Error("Dump wrote no file; the checksum and manifest steps read one")
	}
	if got := f.FakePostgres().Dumps; len(got) != 1 || !strings.HasSuffix(got[0], out) {
		t.Errorf("Dumps = %v", got)
	}
}

func TestFakeSQLiteBackupCopiesAndReportsAbsence(t *testing.T) {
	ctx := context.Background()
	f := seededFake(t)
	src, dst := "/rag/data/tenants/dev/state/jobs.db", "/rag/backups/dev/b1/state/jobs.db"
	integrity, err := f.SQLite().Backup(ctx, src, dst)
	if err != nil || integrity != "ok" {
		t.Fatalf("Backup = %q, %v", integrity, err)
	}
	if string(f.FakeFiles().Content(dst)) != "sqlite-ish" {
		t.Errorf("the backup is %q, want a copy of the source", f.FakeFiles().Content(dst))
	}
	if mode := f.FakeFiles().Files[dst].Mode; mode != 0o640 {
		t.Errorf("the backup is mode %04o, want 0640", mode)
	}
	if _, err := f.SQLite().Backup(ctx, "/rag/data/tenants/dev/state/gone.db", dst); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("backing up an absent file = %v, want an fs.ErrNotExist a caller can match", err)
	}
}

func TestFakeArchiveCreatesAFileAndExtractsNothing(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	if err := f.Archive().Create(ctx, "/rag/backups/dev/b1", "/rag/backups/dev/b1.tar"); err != nil {
		t.Fatal(err)
	}
	if f.FakeFiles().Content("/rag/backups/dev/b1.tar") == nil {
		t.Error("Create wrote no tar; the bundle's checksum step reads one")
	}
	before := len(f.FakeFiles().Paths())
	if err := f.Archive().Extract(ctx, "/rag/backups/dev/b1.tar", "/rag/backups/dev/restore",
		jobs.ArchiveLimits{MaxEntries: 10, MaxBytes: 1024}); err != nil {
		t.Fatal(err)
	}
	// Extract writes nothing: deciding which entries are refused is the real
	// driver's whole job, and a fake that invented files would let a test
	// assert on a policy nothing here implements.
	if after := len(f.FakeFiles().Paths()); after != before {
		t.Errorf("Extract created %d files, want none", after-before)
	}
	if got := f.FakeArchive().Extracted; len(got) != 1 {
		t.Errorf("Extracted = %v", got)
	}
}

func TestFakeFilesMkdirAllRecordsTheModeAndHonoursTheRoots(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag/data/tenants"}})
	dir := "/rag/data/tenants/dev"
	if err := f.Files().MkdirAll(ctx, dir, 0o2770); err != nil {
		t.Fatal(err)
	}
	if got := f.FakeFiles().Dirs[dir]; got != 0o2770 {
		t.Errorf("Dirs[%s] = %04o, want 2770", dir, got)
	}
	// A second call does not re-mode a directory it did not create.
	if err := f.Files().MkdirAll(ctx, dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if got := f.FakeFiles().Dirs[dir]; got != 0o2770 {
		t.Errorf("Dirs[%s] = %04o after a second call, want the first mode kept", dir, got)
	}
	if err := f.Files().MkdirAll(ctx, "/etc/ragstack", 0o755); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("MkdirAll outside the approved roots = %v, want a refusal", err)
	}
}

// ---------------------------------------------------------------- real MkdirAll

func TestRealFilesMkdirAllCreatesNestedDirectoriesWithTheMode(t *testing.T) {
	f, root := realFiles(t)
	dir := filepath.Join(root, "tenants", "dev", "logs")
	if err := f.MkdirAll(context.Background(), dir, 0o2770); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(dir)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o770 {
		t.Errorf("mode = %v, want 0770 — mkdir's mode is masked by the umask, so the leaf is chmodded", st.Mode().Perm())
	}
	if st.Mode()&os.ModeSetgid == 0 {
		t.Error("the setgid bit is gone; it is what makes the tenant tree inherit its group")
	}
	// Idempotent: a retried create must not fail on the directory it made.
	if err := f.MkdirAll(context.Background(), dir, 0o2770); err != nil {
		t.Errorf("MkdirAll of an existing directory = %v, want nil", err)
	}
}

func TestRealFilesMkdirAllNeverChmodsADirectoryItDidNotCreate(t *testing.T) {
	f, root := realFiles(t)
	// /rag/data/tenants is wilke 755 and its group has 1869 members: a driver
	// that "corrected" the mode of a parent it found would be re-permissioning
	// a directory shared with the whole host.
	existing := filepath.Join(root, "shared")
	if err := os.Mkdir(existing, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := f.MkdirAll(context.Background(), existing, 0o770); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(existing)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o700 {
		t.Errorf("mode = %v, want the 0700 it already had", st.Mode().Perm())
	}
}

func TestRealFilesMkdirAllRefusesOutsideTheRootsAndThroughASymlink(t *testing.T) {
	f, root := realFiles(t)
	outside := t.TempDir()
	if err := f.MkdirAll(context.Background(), filepath.Join(outside, "x"), 0o770); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("MkdirAll outside the roots = %v, want a refusal", err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	err := f.MkdirAll(context.Background(), filepath.Join(root, "escape", "loot"), 0o770)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("MkdirAll through a symlinked parent = %v, want a refusal", err)
	}
	if _, statErr := os.Stat(filepath.Join(outside, "loot")); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatal("the directory was created outside the approved roots")
	}
	// A file sitting where the directory should go is a refusal, not a
	// silently skipped step.
	file := filepath.Join(root, "afile")
	if err := os.WriteFile(file, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := f.MkdirAll(context.Background(), file, 0o770); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("MkdirAll over a file = %v, want a refusal", err)
	}
}
