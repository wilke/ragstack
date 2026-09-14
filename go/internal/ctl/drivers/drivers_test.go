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
