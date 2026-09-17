package adopt

import (
	"bytes"
	"os"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// commitTestTenant builds a minimal, contract-valid tenant named name/idx by
// cloning the fully-populated "dev" fixture tenant and repointing the fields
// that must be unique per tenant. It exists so this file does not need the
// heavy live-corpus fixtures TestCommitAllReproducesTheLiveManifest uses.
func commitTestTenant(roots paths.Roots, name string, idx int) *registry.Tenant {
	tn := *registry.LiveFixture().Tenants["dev"]
	tn.Name, tn.ManifestName = name, name
	tp := paths.TenantPaths(roots, name, name)
	tn.DataDir, tn.Worktree = tp.DataDir, tp.Worktree
	tn.Ports = paths.Block(idx)
	return &tn
}

// TestCommitAllChecksRefusalsBeforeRepairingProjection is the regression for
// issue #536: with an existing registry (tenants a, b) and a manifest.tsv
// hand-appended with a row for a new tenant c (simulating an operator's
// out-of-band edit ahead of adopting it), running CommitAll for a,b,c with
// RepairProjection must evaluate the "already adopted" refusal BEFORE
// touching the projection. Repairing first — as the old loadOrCreate did —
// rewrote manifest.tsv FROM the registry (dropping row c) and only then
// noticed "a" was already adopted, discarding the appended row on the way
// out to a refusal.
func TestCommitAllChecksRefusalsBeforeRepairingProjection(t *testing.T) {
	dir := t.TempDir()
	roots := paths.NewRoots(dir, paths.Overrides{})
	reg := roots.Registry()

	a := commitTestTenant(roots, "a", 0)
	b := commitTestTenant(roots, "b", 1)
	c := commitTestTenant(roots, "c", 2)

	f := registry.NewFleet(dir)
	f.Tenants["a"], f.Tenants["b"] = a, b
	f.DisplayOrder = []string{"a", "b"}
	if err := registry.Save(reg, f, "t"); err != nil {
		t.Fatal(err)
	}

	// Hand-append row c to manifest.tsv, out of band — the registry does not
	// know c yet, so this is exactly what makes the projection stale.
	manPath := registry.PathsFor(reg).Manifest
	before, err := os.ReadFile(manPath)
	if err != nil {
		t.Fatal(err)
	}
	appended := append(append([]byte{}, before...), []byte("c\t2\t24040\n")...)
	if err := os.WriteFile(manPath, appended, 0o664); err != nil {
		t.Fatal(err)
	}
	regBefore, err := os.ReadFile(reg)
	if err != nil {
		t.Fatal(err)
	}

	err = CommitAll(reg, []*registry.Tenant{a, b, c}, CommitOptions{Roots: roots, RepairProjection: true})
	if err == nil {
		t.Fatal("adopting a and b again alongside c must be refused")
	}
	if !strings.Contains(err.Error(), `tenant "a" is already in`) {
		t.Errorf("refusal should name the already-adopted tenant, got: %v", err)
	}

	after, err := os.ReadFile(manPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(after, appended) {
		t.Fatalf("manifest.tsv was rewritten despite the refusal:\n before %q\n after  %q", appended, after)
	}
	regAfter, err := os.ReadFile(reg)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(regAfter, regBefore) {
		t.Fatalf("registry.json changed despite the refusal:\n before %s\n after  %s", regBefore, regAfter)
	}
}

// TestCommitAllRepairsTheProjectionOnceRefusalsAreCleared is the positive
// case alongside the regression above: adopting only the new tenant (c) with
// --repair-projection succeeds — the stale manifest.tsv (which already
// carried the hand-appended row c) is repaired and the commit then leaves
// registry and projection in sync.
func TestCommitAllRepairsTheProjectionOnceRefusalsAreCleared(t *testing.T) {
	dir := t.TempDir()
	roots := paths.NewRoots(dir, paths.Overrides{})
	reg := roots.Registry()

	a := commitTestTenant(roots, "a", 0)
	b := commitTestTenant(roots, "b", 1)
	c := commitTestTenant(roots, "c", 2)

	f := registry.NewFleet(dir)
	f.Tenants["a"], f.Tenants["b"] = a, b
	f.DisplayOrder = []string{"a", "b"}
	if err := registry.Save(reg, f, "t"); err != nil {
		t.Fatal(err)
	}
	manPath := registry.PathsFor(reg).Manifest
	before, err := os.ReadFile(manPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(manPath, append(append([]byte{}, before...), []byte("c\t2\t24040\n")...), 0o664); err != nil {
		t.Fatal(err)
	}
	if _, diag, err := registry.LoadWithDiagnostics(reg); err != nil || !diag.Stale() {
		t.Fatalf("test setup: projection should be stale before commit, diag=%+v err=%v", diag, err)
	}

	if err := Commit(reg, c, CommitOptions{Roots: roots, RepairProjection: true}); err != nil {
		t.Fatalf("adopting c with --repair-projection should succeed: %v", err)
	}

	got, diag, err := registry.LoadWithDiagnostics(reg)
	if err != nil {
		t.Fatal(err)
	}
	if diag.Stale() {
		t.Fatalf("projection still stale after commit: %v", diag.ProjectionStale)
	}
	if _, ok := got.Tenants["c"]; !ok {
		t.Fatal("tenant c was not committed")
	}
	if len(got.Tenants) != 3 {
		t.Fatalf("registry has %d tenants, want 3 (a, b, c)", len(got.Tenants))
	}
}

// TestReadoptReplacesTheRowAndKeepsWhatAdoptionCannotSee: a tenant whose
// store kind the contract only just learned (#535) is re-adopted in place —
// the row is rebuilt from the host, the generation moves, and the state
// adoption does not observe (adopted_at, desired_boot) survives.
func TestReadoptReplacesTheRowAndKeepsWhatAdoptionCannotSee(t *testing.T) {
	dir := t.TempDir()
	roots := paths.NewRoots(dir, paths.Overrides{})
	reg := roots.Registry()
	first := commitTestTenant(roots, "dev", 2)
	first.AdoptedAt = "2026-09-14T00:00:00Z"
	first.DesiredBoot = "disabled"
	if err := Commit(reg, first, CommitOptions{Roots: roots, UpdatedBy: "test"}); err != nil {
		t.Fatal(err)
	}
	before, err := registry.LoadNoRepair(reg)
	if err != nil {
		t.Fatal(err)
	}
	again := commitTestTenant(roots, "dev", 2)
	again.AdoptedAt = "2026-09-15T00:00:00Z" // a fresh preview stamps now; the original must win
	again.Stores.Postgres = registry.Postgres{
		Kind: "local", Ownership: "exclusive", URL: "postgresql://localhost:24045",
		Port: 24045, Instance: "postgres-dev", SIF: "/rag/apptainer/images/postgres.sif",
		DataDir: registry.NullString(paths.TenantPaths(roots, "dev", "dev").DataDir + "/postgres"),
	}
	if err := Commit(reg, again, CommitOptions{Roots: roots, UpdatedBy: "test"}); err == nil {
		t.Fatal("re-adoption without --readopt was accepted")
	}
	if err := Commit(reg, again, CommitOptions{Roots: roots, UpdatedBy: "test", Readopt: true}); err != nil {
		t.Fatalf("--readopt refused: %v", err)
	}
	after, err := registry.LoadNoRepair(reg)
	if err != nil {
		t.Fatal(err)
	}
	row := after.Tenants["dev"]
	if row.Stores.Postgres.Kind != "local" || row.Stores.Postgres.Instance != "postgres-dev" {
		t.Errorf("the row was not replaced: %+v", row.Stores.Postgres)
	}
	if after.Generation != before.Generation+1 {
		t.Errorf("generation %d -> %d, want +1", before.Generation, after.Generation)
	}
	if row.AdoptedAt != "2026-09-14T00:00:00Z" || row.DesiredBoot != "disabled" {
		t.Errorf("adopted_at/desired_boot were not carried over: %q %q", row.AdoptedAt, row.DesiredBoot)
	}
}

// A re-adoption must not revert the preparation ops. It is the runbook's own
// A5 command that made this urgent: `adopt <t> --readopt --confirm-stores …`
// re-derives ui.mode and api.bind from the host, and on hackathon — whose UI is
// a `static` build with no port — a fresh preview infers `external` with port
// 0, which is a gateway row pointing at nothing.
//
// The row shapes here are the live ones: hackathon static/no-port,
// dev dev-mode on 8090, both with a live API still bound 0.0.0.0 after
// `tenant set-bind` recorded 127.0.0.1.
func TestReadoptKeepsWhatThePreparationOpsDecided(t *testing.T) {
	prepared := func(uiMode string, uiPort int) *registry.Tenant {
		row := registry.NewTenant("hack", "hack")
		row.UI = registry.UI{Mode: uiMode, Port: registry.NullPort(uiPort), Base: "/ragstack/hack/ui/"}
		row.API = registry.API{Bind: "127.0.0.1", PidFile: "/rag/data/tenants/hack/api-hack.pid",
			Log: "/rag/data/tenants/hack/logs/api-hack.log"}
		row.Stores.Qdrant.Capabilities = registry.Capabilities{Stop: true, Snapshot: true, Restore: true}
		row.AdoptedAt = "2026-09-10T09:00:00Z"
		return row
	}
	// What a fresh preview of the same tenant produces: the UI inferred from
	// the absent --ui-port, the bind observed off the still-running process,
	// every capability false.
	fresh := func() *registry.Tenant {
		row := registry.NewTenant("hack", "hack")
		row.UI = registry.UI{Mode: registry.UIModeExternal, Port: 0, Base: "/ragstack/hack/ui/"}
		row.API = registry.API{Bind: "0.0.0.0"}
		row.AdoptedAt = "2026-09-16T12:00:00Z"
		return row
	}

	t.Run("no flags: every decision is kept", func(t *testing.T) {
		next, prev := fresh(), prepared(registry.UIModeStatic, 0)
		carryOver(next, prev, Overrides{})
		if next.UI.Mode != registry.UIModeStatic || next.UI.Port != 0 {
			t.Errorf("ui = %+v, want the recorded static/no-port", next.UI)
		}
		if next.API.Bind != "127.0.0.1" {
			t.Errorf("api.bind = %q, want the recorded 127.0.0.1", next.API.Bind)
		}
		if !next.Stores.Qdrant.Capabilities.Stop {
			t.Error("the confirmed qdrant capability was reset by a re-adoption")
		}
		// The disagreement with the live process is written down, not swallowed.
		var drift *registry.Drift
		for i := range next.Drift {
			if next.Drift[i].Code == doctor.APIBindDrift {
				drift = &next.Drift[i]
			}
		}
		if drift == nil {
			t.Fatalf("no api_bind_drift row: the live 0.0.0.0 disappeared silently (%+v)", next.Drift)
		}
		if drift.Expected != "127.0.0.1" || drift.Actual != "0.0.0.0" || drift.ObservedAt == "" {
			t.Errorf("drift row = %+v", *drift)
		}
	})

	t.Run("a dev-mode UI is kept too", func(t *testing.T) {
		next, prev := fresh(), prepared(registry.UIModeDev, 8090)
		carryOver(next, prev, Overrides{})
		if next.UI.Mode != registry.UIModeDev || next.UI.Port != 8090 {
			t.Errorf("ui = %+v, want dev/8090", next.UI)
		}
	})

	t.Run("an explicit flag wins", func(t *testing.T) {
		next, prev := fresh(), prepared(registry.UIModeStatic, 0)
		next.API.Bind = "0.0.0.0" // as --api-bind 0.0.0.0 would have set it
		carryOver(next, prev, Overrides{UI: true, Bind: true, ConfirmedLegs: []string{"qdrant"}})
		if next.UI.Mode != registry.UIModeExternal {
			t.Errorf("ui.mode = %q, want the explicitly given external", next.UI.Mode)
		}
		if next.API.Bind != "0.0.0.0" {
			t.Errorf("api.bind = %q, want the explicitly given 0.0.0.0", next.API.Bind)
		}
		if next.Stores.Qdrant.Capabilities.Stop {
			t.Error("--confirm-stores decided the capabilities; the old row must not overwrite them")
		}
		if len(next.Drift) != 0 {
			t.Errorf("an explicit bind is a decision, not a drift: %+v", next.Drift)
		}
	})

	t.Run("a partial --confirm-stores says nothing about the other legs", func(t *testing.T) {
		next, prev := fresh(), prepared(registry.UIModeStatic, 0)
		// Last week both legs were confirmed; today only qdrant is re-verified.
		prev.Stores.Elasticsearch.Capabilities = registry.Capabilities{Stop: true, Snapshot: true, Restore: true}
		next.Stores.Qdrant.Capabilities = registry.Capabilities{Stop: true, Snapshot: true, Restore: true}
		carryOver(next, prev, Overrides{ConfirmedLegs: []string{"qdrant"}})
		if !next.Stores.Qdrant.Capabilities.Stop {
			t.Error("the leg this call confirmed was overwritten by the old row")
		}
		if !next.Stores.Elasticsearch.Capabilities.Stop {
			t.Error("a leg the call never named was un-confirmed by it")
		}
	})

	t.Run("the bookkeeping fields are carried as they always were", func(t *testing.T) {
		next, prev := fresh(), prepared(registry.UIModeStatic, 0)
		prev.DesiredBoot, prev.RestartPending = "enabled", true
		prev.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/hack/b", At: "2026-09-15T00:00:00Z",
			Kind: "backup", Scope: []string{"config", "state"}}
		carryOver(next, prev, Overrides{})
		if next.AdoptedAt != prev.AdoptedAt || next.DesiredBoot != "enabled" || !next.RestartPending ||
			next.LastBackup == nil {
			t.Errorf("a bookkeeping field was dropped: %+v", next)
		}
	})
}
