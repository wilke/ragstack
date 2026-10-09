package api

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// #714 through the wiring the daemon and --direct share (BuildEngineAndDrivers
// over the fixture fleet): a --direct engine runs `env set-surface` and
// `env set` to the end and the registry FILE records restart_pending and the
// written file's checksum; a daemon engine refuses `env set-surface` even when
// the request claims to be a direct one.
func TestEnvSurfaceThroughTheWiredEngine(t *testing.T) {
	dir := t.TempDir()
	regPath := filepath.Join(dir, "registry.json")
	writeFleet(t, regPath, FixtureFleet())
	now := func() time.Time { return time.Date(2026, 10, 9, 10, 0, 0, 0, time.UTC) }
	roots := paths.NewRoots(dir, paths.Overrides{})
	build := func(mode model.WorkerMode) (jobs.Engine, jobs.Drivers) {
		eng, drv, err := BuildEngineAndDrivers(EngineConfig{
			Roots: roots, RegistryPath: regPath, StorePath: filepath.Join(dir, "jobs-"+string(mode)+".db"),
			Mode: mode, Host: "test", FakeDrivers: true, Now: now,
			AllowedEndpointHosts: []string{"127.0.0.1", "localhost", "mango.cels.anl.gov"},
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
	surface := map[string]any{"values": map[string]any{"LLM_ENDPOINT": "http://mango.cels.anl.gov:8003"}}

	daemon, _ := build(model.WorkerDaemon)
	_, _, err := daemon.Submit(context.Background(), jobs.Request{
		Op: "env-set-surface", Tenant: managedFixtureName, Args: surface, DryRun: true,
		IdempotencyKey: "surface-daemon", Mode: model.WorkerDirect, Principal: p,
	})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "--direct") {
		t.Fatalf("the daemon engine planned env-set-surface: %v", err)
	}

	direct, drv := build(model.WorkerDirect)
	envPath := paths.TenantPaths(roots, managedFixtureName, managedFixtureName).TenantEnv
	for i, req := range []jobs.Request{
		{Op: "env-set-surface", Args: surface},
		{Op: "env-set", Args: map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"}},
	} {
		req.Tenant, req.Mode, req.Principal = managedFixtureName, model.WorkerDirect, p
		req.IdempotencyKey = "surface-direct-" + req.Op
		_, job, err := direct.Submit(context.Background(), req)
		if err != nil {
			t.Fatalf("%s: %v", req.Op, err)
		}
		done := waitForState(t, direct, job.ID, model.JobSucceeded, model.JobFailed)
		if done.State != model.JobSucceeded {
			t.Fatalf("%s: %s %+v", req.Op, done.State, done.Error)
		}
		body, err := drv.Files().ReadFile(context.Background(), envPath)
		if err != nil {
			t.Fatal(err)
		}
		if i == 0 && !strings.Contains(string(body), "LLM_ENDPOINT=http://mango.cels.anl.gov:8003") {
			t.Fatalf("tenant.env:\n%s", body)
		}
		f, err := registry.LoadNoRepair(regPath)
		if err != nil {
			t.Fatal(err)
		}
		row := f.Tenants[managedFixtureName]
		if !row.RestartPending || row.EnvFileSHA256 != sha256Hex(body) {
			t.Errorf("%s: restart_pending %v, env_file_sha256 %s; want true and %s", req.Op, row.RestartPending,
				row.EnvFileSHA256, sha256Hex(body))
		}
		if _, ok := row.Settings["LLM_ENDPOINT"]; ok {
			t.Errorf("%s: settings{} carries the surface key", req.Op)
		}
	}
}

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
