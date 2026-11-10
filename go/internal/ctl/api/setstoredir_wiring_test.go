package api

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// PR3 of the per-qdrant data dir through the wiring the daemon and --direct
// share (BuildEngineAndDrivers, the registry FILE, registry.Save): a daemon
// engine refuses `set-store-dir` even when the request claims to be direct; a
// --direct engine plans `--dir` and runs `--clear` to the end, and the file on
// disk loses the field and records restart_pending.
func TestSetStoreDirThroughTheWiredEngine(t *testing.T) {
	dir := t.TempDir()
	regPath := filepath.Join(dir, "registry.json")
	fleet := FixtureFleet()
	row := fleet.Tenants[managedFixtureName]
	moved := row.DataDir + "/qdrant-moved" // under the row's own data_dir: no store root, no mount lookup
	row.Stores.Qdrant.DataDir = moved
	row.RestartPending = false
	writeFleet(t, regPath, fleet)
	now := func() time.Time { return time.Date(2026, 11, 10, 10, 0, 0, 0, time.UTC) }
	roots := paths.NewRoots(dir, paths.Overrides{})
	build := func(mode model.WorkerMode) (jobs.Engine, jobs.Drivers) {
		eng, drv, err := BuildEngineAndDrivers(EngineConfig{
			Roots: roots, RegistryPath: regPath, StorePath: filepath.Join(dir, "jobs-"+string(mode)+".db"),
			Mode: mode, Host: "test", FakeDrivers: true, Now: now,
			Doctor: func(ctx context.Context, tenant, op string) (model.DoctorResponse, error) {
				return model.DoctorResponse{Status: model.StatusGreen, Hash: "sha256:" + strings.Repeat("0", 64),
					GeneratedAt: now().Format(time.RFC3339),
					Scope:       model.Scope{Tenant: model.NullString(tenant), Op: model.NullString(op)},
					Findings:    []model.Finding{}}, nil
			},
		})
		if err != nil {
			t.Fatalf("BuildEngineAndDrivers(%s): %v", mode, err)
		}
		return eng, drv
	}
	p := jobs.Principal{Subject: "local:0", Role: "operator", Method: model.AuthLocal}
	clearArgs := map[string]any{"store": "qdrant", "clear": true}

	daemon, _ := build(model.WorkerDaemon)
	_, _, err := daemon.Submit(context.Background(), jobs.Request{
		Op: "set-store-dir", Tenant: managedFixtureName, Args: clearArgs, DryRun: true,
		IdempotencyKey: "store-dir-daemon", Mode: model.WorkerDirect, Principal: p,
	})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "--direct") {
		t.Fatalf("the daemon engine planned set-store-dir: %v", err)
	}

	direct, drv := build(model.WorkerDirect)
	// A --dir dry run plans (the plan reads nothing off the host).
	plan, _, err := direct.Submit(context.Background(), jobs.Request{
		Op: "set-store-dir", Tenant: managedFixtureName, DryRun: true,
		Args:           map[string]any{"store": "qdrant", "dir": row.DataDir + "/qdrant-b"},
		IdempotencyKey: "store-dir-dry", Mode: model.WorkerDirect, Principal: p,
	})
	if err != nil || plan == nil || len(plan.Steps) != 3 {
		t.Fatalf("--dir dry run = %+v, %v; want a three-step plan", plan, err)
	}

	// The qdrant is stopped and the default location holds the store.
	fake := drv.(*drivers.Fake)
	fake.FakeProc().FreePort(row.Ports.QdrantHTTP)
	fake.FakeProc().FreePort(row.Ports.QdrantGRPC)
	fake.FakeInstances().StopAll()
	fake.FakeFiles().Dirs[row.DataDir+"/qdrant/storage"] = 0o2770
	_, job, err := direct.Submit(context.Background(), jobs.Request{
		Op: "set-store-dir", Tenant: managedFixtureName, Args: clearArgs,
		IdempotencyKey: "store-dir-clear", Mode: model.WorkerDirect, Principal: p,
	})
	if err != nil {
		t.Fatalf("--clear: %v", err)
	}
	done := waitForState(t, direct, job.ID, model.JobSucceeded, model.JobFailed)
	if done.State != model.JobSucceeded {
		t.Fatalf("--clear: %s %+v", done.State, done.Error)
	}
	f, err := registry.LoadNoRepair(regPath)
	if err != nil {
		t.Fatalf("the registry the job wrote does not load: %v", err)
	}
	got := f.Tenants[managedFixtureName]
	if got.Stores.Qdrant.DataDir != "" || !got.RestartPending {
		t.Errorf("after --clear: data_dir %q restart_pending %v; want absent and true",
			got.Stores.Qdrant.DataDir, got.RestartPending)
	}
	if _, ok := got.LastOps["set-store-dir"]; ok {
		t.Error("set-store-dir recorded a last_ops entry")
	}
	b, _ := os.ReadFile(regPath)
	if strings.Contains(string(b), moved) {
		t.Error("the registry file still names the moved dir")
	}
}
