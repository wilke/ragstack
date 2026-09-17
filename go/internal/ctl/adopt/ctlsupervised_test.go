package adopt

import (
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The shape of the live hackathon row minutes after the first successful
// handover on 2026-09-17: the service account owns it, the ctl's own instance
// supervisor runs it, it is up, and the handover block is still parked at
// `taken` waiting for the commit.
const (
	handedOverOwner      = "svcbvbrc"
	handedOverToken      = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
	handedOverPidFile    = "/rag/data/ctl/run/api-dev.pid"
	handedOverAPIPid     = 31337
	handedOverStartedAt  = "2026-09-17T11:40:00Z"
	handedOverTakenAt    = "2026-09-17T12:05:00Z"
	handedOverReleasedAt = "2026-09-17T11:52:00Z"
)

// handedOver turns a freshly previewed `dev` row into the row the handover
// left behind, and is the fixture the tests in this file are built from. Every
// field it sets is one the 2026-09-17 `--readopt` overwrote or dropped.
func handedOver(t *testing.T, row *registry.Tenant) *registry.Tenant {
	t.Helper()
	out := *row
	out.Owner = handedOverOwner
	out.Supervisor = string(model.SupervisorInstance)
	out.State = string(model.StateActive)
	out.DesiredBoot = "enabled"
	out.API.PidFile = handedOverPidFile
	out.Handover = &registry.Handover{
		Phase: registry.HandoverTaken, Token: handedOverToken,
		StartedAt: handedOverStartedAt, ReleasedBy: "wilke",
		ReleasedAt: handedOverReleasedAt,
		TakenAt:    handedOverTakenAt, TakenBy: handedOverOwner,
		Census: []registry.CensusEntry{{Store: "qdrant", Name: "docs", Count: 12}},
	}
	// A5 confirmed the tenant's own qdrant before the handover; the capability
	// is what lets the ctl stop the store it now supervises.
	out.Stores.Qdrant.Capabilities = registry.Capabilities{Stop: true, Snapshot: true, Restore: true}
	return &out
}

// unattributable is the live cross-account shape of the API port: something
// holds it, and this account's /proc/<pid>/fd scan cannot say what. Every
// `--readopt` run by anyone but the tenant's own owner sees exactly this.
func unattributable(h *hostfacts.Fake, port int) {
	for i := range h.Ports {
		if h.Ports[i].Port == port {
			h.Ports[i].Pid, h.Ports[i].UID, h.Ports[i].User = 0, -1, ""
			h.Ports[i].Cmdline, h.Ports[i].Cwd = nil, ""
		}
	}
}

// seedHandedOver materializes the live fleet, commits all four rows (the
// manifest.tsv beside them names four tenants, and adopt reconciles against
// it), and returns the `dev` row as the handover left it.
func seedHandedOver(t *testing.T) (paths.Roots, *hostfacts.Fake, *registry.Tenant) {
	t.Helper()
	roots := materialize(t, t.TempDir())
	h := liveHost(t, roots)
	rows := previewLive(t, roots, h)
	dev := handedOver(t, rows["dev"])
	rows["dev"] = dev
	batch := make([]*registry.Tenant, 0, len(rows))
	for _, l := range live {
		batch = append(batch, rows[l.name])
	}
	if err := CommitAll(roots.Registry(), batch, CommitOptions{Roots: roots, UpdatedBy: "test"}); err != nil {
		t.Fatalf("seeding the fleet: %v", err)
	}
	return roots, h, dev
}

// devReadopt previews `dev` the way `adopt dev … --readopt` does.
func devReadopt(t *testing.T, roots paths.Roots, h hostfacts.Host, prev *registry.Tenant) (*registry.Tenant, []model.Finding) {
	t.Helper()
	at := time.Date(2026, 9, 17, 12, 20, 0, 0, time.UTC)
	row, findings, err := Preview(roots, "dev", Options{
		DataDir:  filepath.Join(roots.DataDir, "dev"),
		Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort:   8090, Host: h, Now: func() time.Time { return at },
		Existing: prev, Readopt: true,
	})
	if err != nil {
		t.Fatalf("readopt preview: %v", err)
	}
	return row, findings
}

// appendKey adds a third API key to the tenant's own tenant.env, the way an
// operator editing secrets does — the edit the 2026-09-17 `--readopt` was run
// to pick up in the first place.
func appendKey(t *testing.T, roots paths.Roots) {
	t.Helper()
	path := filepath.Join(roots.DataDir, "dev", "config", "tenant.env")
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	old := `API_KEYS='["<REDACTED:k1>","<REDACTED:k2>"]'`
	if !strings.Contains(string(b), old) {
		t.Fatalf("fixture tenant.env no longer carries %s", old)
	}
	next := `API_KEYS='["<REDACTED:k1>","<REDACTED:k2>","<REDACTED:k3>"]'`
	if err := os.WriteFile(path, []byte(strings.Replace(string(b), old, next, 1)), 0o600); err != nil {
		t.Fatal(err)
	}
}

// TestReadoptKeepsACtlSupervisedRowAndRefreshesTheKeys is the regression for
// what happened on coconut on 2026-09-17 at 12:20 UTC.
//
// Minutes after the first successful handover — hackathon at owner svcbvbrc,
// supervisor instance, state active, handover parked at `taken` — the operator
// edited secrets.env and ran, as wilke:
//
//	ragstack-ctl adopt hackathon --data-dir … --worktree … --ui-mode static \
//	    --readopt --commit
//
// to refresh the key ledger. The commit wrote owner `wilke`, supervisor
// `manual`, and dropped the handover block: adopt hard-coded `manual`,
// defaulted the owner to `wilke`, and read the owner from a listen table that
// cannot attribute another account's socket at all. The row then described
// wilke/manual over svcbvbrc's processes — `tenant restart` refused it as
// hand-started, `fleet start --all` skipped it, and restore.sh would have
// started a second copy at the next reboot.
//
// The test drives that exact sequence: the handed-over row, an edited key
// list, the port unattributable (the shape wilke's account sees), and asserts
// the commit keeps every decision AND picks up the new key.
func TestReadoptKeepsACtlSupervisedRowAndRefreshesTheKeys(t *testing.T) {
	roots, h, prev := seedHandedOver(t)
	reg := roots.Registry()
	if n := len(prev.Keys); n != 2 {
		t.Fatalf("the fixture tenant has %d keys, want the 2 in tenant.env", n)
	}

	appendKey(t, roots)
	unattributable(h, prev.Ports.API)

	next, findings := devReadopt(t, roots, h, prev)
	if countCode(findings, doctor.CtlSupervisedRow) != 1 {
		t.Errorf("no plan-time note that the row is ctl-supervised: %v", codes(findings))
	}
	if err := Commit(reg, next, CommitOptions{Roots: roots, UpdatedBy: "test", Readopt: true}); err != nil {
		t.Fatalf("--readopt refused: %v", err)
	}

	f, err := registry.LoadNoRepair(reg)
	if err != nil {
		t.Fatal(err)
	}
	row := f.Tenants["dev"]

	// 1. Everything the handover decided is still there.
	if row.Owner != handedOverOwner {
		t.Errorf("owner = %q, want the recorded %q: a re-adoption must not re-derive ownership", row.Owner, handedOverOwner)
	}
	if row.Supervisor != string(model.SupervisorInstance) {
		t.Errorf("supervisor = %q, want instance: `manual` here is what made `tenant restart` refuse the tenant", row.Supervisor)
	}
	if row.State != string(model.StateActive) {
		t.Errorf("state = %q, want active", row.State)
	}
	if row.DesiredBoot != "enabled" {
		t.Errorf("desired_boot = %q, want enabled: the boot hook reads it", row.DesiredBoot)
	}
	if row.API.PidFile != handedOverPidFile {
		t.Errorf("api.pidfile = %q, want the supervisor's own %q", row.API.PidFile, handedOverPidFile)
	}
	if row.Handover == nil {
		t.Fatal("the handover block was dropped — the exact loss of 2026-09-17")
	}
	if row.Handover.Phase != registry.HandoverTaken || row.Handover.Token != handedOverToken ||
		row.Handover.TakenBy != handedOverOwner || row.Handover.TakenAt != handedOverTakenAt {
		t.Errorf("handover = %+v, want the parked take intact", *row.Handover)
	}
	if !row.Stores.Qdrant.Capabilities.Stop {
		t.Error("the confirmed qdrant capability was reset: the ctl could no longer stop the store it supervises")
	}

	// 2. What the run exists to refresh, it refreshed.
	if len(row.Keys) != 3 {
		t.Errorf("keys = %d, want the 3 now in tenant.env: refreshing the ledger is what the command was for", len(row.Keys))
	}
	if row.EnvFileSHA256 == prev.EnvFileSHA256 {
		t.Error("env_file_sha256 was not refreshed after the edit")
	}
}

// TestReadoptRecordsDriftWhenTheProbesContradictTheRow: the probes are never
// acted on for a ctl-supervised row, but they are not swallowed either. The
// two shapes are different facts and get different rows.
func TestReadoptRecordsDriftWhenTheProbesContradictTheRow(t *testing.T) {
	driftFor := func(t *testing.T, code string, rows []registry.Drift) *registry.Drift {
		t.Helper()
		for i := range rows {
			if rows[i].Code == code && rows[i].Field == "owner" {
				return &rows[i]
			}
		}
		t.Fatalf("no %s drift row on owner: %+v", code, rows)
		return nil
	}

	t.Run("an unattributable port is info and the row still stands", func(t *testing.T) {
		roots, h, prev := seedHandedOver(t)
		unattributable(h, prev.Ports.API)

		next, _ := devReadopt(t, roots, h, prev)
		d := driftFor(t, doctor.PortOwnerUnverifiable, next.Drift)
		if d.Level != string(model.LevelInfo) || d.Expected != handedOverOwner || d.Actual != "unattributable" {
			t.Errorf("drift = %+v", *d)
		}
		if next.Owner != handedOverOwner {
			t.Errorf("owner = %q: an unreadable probe rewrote the row anyway", next.Owner)
		}
	})

	t.Run("a readable other account is warn and the row still stands", func(t *testing.T) {
		roots, h, prev := seedHandedOver(t)
		// The fixture's API port is held by a readable wilke process, which
		// is what the row says it is NOT.
		next, _ := devReadopt(t, roots, h, prev)
		d := driftFor(t, doctor.PortOwnerMismatch, next.Drift)
		if d.Level != string(model.LevelWarn) || d.Expected != handedOverOwner || d.Actual != "wilke" {
			t.Errorf("drift = %+v", *d)
		}
		if next.Owner != handedOverOwner {
			t.Errorf("owner = %q, want the recorded %q", next.Owner, handedOverOwner)
		}
	})

	t.Run("a manual row is re-derived as it always was", func(t *testing.T) {
		roots := materialize(t, t.TempDir())
		h := liveHost(t, roots)
		prev := previewLive(t, roots, h)["dev"] // supervisor: manual
		next, findings := devReadopt(t, roots, h, prev)
		if next.Supervisor != string(model.SupervisorManual) || next.Owner != "wilke" {
			t.Errorf("a hand-started row must still be re-derived: %s/%s", next.Owner, next.Supervisor)
		}
		if countCode(findings, doctor.CtlSupervisedRow) != 0 {
			t.Errorf("the ctl-supervised note was raised for a manual row: %v", codes(findings))
		}
	})
}

// TestOwnerComesFromTheProcUidNotADefault: the listen table cannot attribute
// another account's socket (Pid 0, User ""), which the old code read as
// "wilke". The pidfile names the pid outright and /proc/<pid> tells anyone on
// the host who owns it, so that is where the owner comes from now.
func TestOwnerComesFromTheProcUidNotADefault(t *testing.T) {
	roots := materialize(t, t.TempDir())
	h := liveHost(t, roots)
	port := paths.Block(2).API

	// The pidfile the layout puts beside the tenant's data dir, naming a live
	// process this account did not start.
	pidFile := paths.TenantPaths(roots, "dev", "dev").PidFile
	if err := os.WriteFile(pidFile, []byte(strconv.Itoa(handedOverAPIPid)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	h.Procs = map[int]hostfacts.ProcOwnership{handedOverAPIPid: {UID: 10078, User: handedOverOwner}}
	unattributable(h, port)

	row, findings := devReadopt(t, roots, h, nil)
	if row.Owner != handedOverOwner {
		t.Errorf("owner = %q, want %q from /proc/%d — not a default", row.Owner, handedOverOwner, handedOverAPIPid)
	}
	if countCode(findings, doctor.PortOwnerUnverifiable) != 0 {
		t.Errorf("the owner WAS attributed; nothing is unverifiable: %v", codes(findings))
	}

	// A pidfile naming nothing is no evidence about anybody: the row records
	// the default and says out loud that it did.
	if err := os.WriteFile(pidFile, []byte("999999999\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	row, findings = devReadopt(t, roots, h, nil)
	if row.Owner != DefaultOwner {
		t.Errorf("owner = %q, want the default %q", row.Owner, DefaultOwner)
	}
	if countCode(findings, doctor.PortOwnerUnverifiable) != 1 {
		t.Errorf("a defaulted owner must be reported: %v", codes(findings))
	}
}

// TestFirstAdoptionOfACtlRunTenantIsRefused: `adopt` writes `supervisor:
// manual` — "somebody else started this" — and may only say that about a
// hand-started tenant. A pidfile naming a live process owned by the control
// plane's own account is the supervisor's own record that it did not.
func TestFirstAdoptionOfACtlRunTenantIsRefused(t *testing.T) {
	roots := materialize(t, t.TempDir())
	h := liveHost(t, roots)
	pidFile := paths.TenantPaths(roots, "dev", "dev").PidFile
	if err := os.WriteFile(pidFile, []byte(strconv.Itoa(handedOverAPIPid)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	h.Procs = map[int]hostfacts.ProcOwnership{handedOverAPIPid: {UID: 10078, User: handedOverOwner}}
	unattributable(h, paths.Block(2).API)

	opts := Options{
		DataDir:  filepath.Join(roots.DataDir, "dev"),
		Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort:   8090, Host: h,
	}
	_, _, err := Preview(roots, "dev", opts)
	if !errors.Is(err, ErrCtlRunsTenant) {
		t.Fatalf("a first adoption of a ctl-run tenant must be refused, got %v", err)
	}
	if !strings.Contains(err.Error(), "--readopt") {
		t.Errorf("the refusal must name the repair: %v", err)
	}

	// --readopt is the verb for it, and it is not refused.
	opts.Readopt = true
	if _, _, err := Preview(roots, "dev", opts); err != nil {
		t.Fatalf("--readopt must be allowed: %v", err)
	}

	// A pidfile owned by somebody else is a hand-started tenant, which is what
	// adoption is FOR.
	h.Procs[handedOverAPIPid] = hostfacts.ProcOwnership{UID: 1000, User: "wilke"}
	opts.Readopt = false
	if _, _, err := Preview(roots, "dev", opts); err != nil {
		t.Fatalf("a hand-started tenant must still be adoptable: %v", err)
	}
}

// TestCommitKeepsCtlSupervisionWithoutAPreview is the belt to the preview's
// braces: CommitAll is reachable with a row built by a caller that never
// passed Options.Existing, and the last gate before the write must still
// refuse to move ownership.
func TestCommitKeepsCtlSupervisionWithoutAPreview(t *testing.T) {
	prev := registry.NewTenant("hack", "hack")
	prev.Owner, prev.Supervisor, prev.State = handedOverOwner, string(model.SupervisorInstance), string(model.StateActive)
	prev.API.PidFile = handedOverPidFile
	prev.Handover = &registry.Handover{Phase: registry.HandoverTaken, Token: handedOverToken,
		StartedAt: handedOverStartedAt, ReleasedBy: "wilke"}

	next := registry.NewTenant("hack", "hack")
	next.Owner, next.Supervisor, next.State = "wilke", string(model.SupervisorManual), string(model.StateStopped)
	next.API.PidFile = "/rag/data/tenants/hack/api-hack.pid"

	carryOver(next, prev, Overrides{})

	if next.Owner != handedOverOwner || next.Supervisor != string(model.SupervisorInstance) ||
		next.State != string(model.StateActive) || next.API.PidFile != handedOverPidFile {
		t.Errorf("ownership was re-derived at commit time: %s/%s/%s %s",
			next.Owner, next.Supervisor, next.State, next.API.PidFile)
	}
	if next.Handover == nil || next.Handover.Token != handedOverToken {
		t.Errorf("handover = %+v, want the parked take", next.Handover)
	}
}
