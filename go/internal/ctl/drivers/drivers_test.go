package drivers

import (
	"context"
	"errors"
	"os"
	"path/filepath"
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

func TestRealDriversRefuseWhatLandsInPRD(t *testing.T) {
	ctx := context.Background()
	d := NewReal(RealOptions{Roots: paths.NewRoots(t.TempDir(), paths.Overrides{})})
	sysd, proc, q, es, api := d.Systemd(), d.Proc(), d.Qdrant(), d.Elasticsearch(), d.TenantAPI()
	_, errIsActive := sysd.IsActive(ctx, "u")
	_, errListening := proc.Listening(ctx, 1)
	_, errCols := q.Collections(ctx, "u")
	_, errSnap := q.Snapshot(ctx, "u", "c")
	_, errIdx := es.Indices(ctx, "u")
	for name, err := range map[string]error{
		"systemd.DaemonReload":   sysd.DaemonReload(ctx),
		"systemd.Start":          sysd.Start(ctx, "u"),
		"systemd.Stop":           sysd.Stop(ctx, "u"),
		"systemd.Enable":         sysd.Enable(ctx, "u"),
		"systemd.Disable":        sysd.Disable(ctx, "u"),
		"systemd.IsActive":       errIsActive,
		"proc.Listening":         errListening,
		"proc.Signal":            proc.Signal(ctx, 1, "c", "m", "TERM"),
		"qdrant.Collections":     errCols,
		"qdrant.Snapshot":        errSnap,
		"elasticsearch.Indices":  errIdx,
		"elasticsearch.Snapshot": es.Snapshot(ctx, "u", "r", "n"),
		"tenantapi.Health":       api.Health(ctx, "o"),
		"tenantapi.Account":      api.ServiceAccount(ctx, "o", "s", "create"),
	} {
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("%s = %v, want a jobs.ErrRefused", name, err)
		}
		if !strings.Contains(err.Error(), "PR-D") {
			t.Errorf("%s = %q, want it to say where the driver lands", name, err)
		}
	}
	// The two real ones are real.
	if _, ok := d.Files().(*RealFiles); !ok {
		t.Error("Files() is not the real driver")
	}
	if _, ok := d.Gateway().(*RealGateway); !ok {
		t.Error("Gateway() is not the real driver")
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
	if got != "systemd,proc,qdrant,elasticsearch,tenantapi" {
		t.Errorf("Real.Pending() = %q", got)
	}
	// Every name on the list is a driver that actually refuses, and no driver
	// that WORKS is on it.
	if err := d.Systemd().DaemonReload(context.Background()); !strings.Contains(err.Error(), PendingPR) {
		t.Errorf("systemd refusal = %v, want it to name %s", err, PendingPR)
	}
	for _, name := range d.Pending() {
		if name == "gateway" || name == "files" {
			t.Errorf("%s is wired; it must not be reported as pending", name)
		}
	}
	if p := NewFake(FakeOptions{}).Pending(); len(p) != 0 {
		t.Errorf("Fake.Pending() = %v, want none: the fakes run every driver", p)
	}
}
