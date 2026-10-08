package api

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"filippo.io/age"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/seal"
)

// archiveEngine is the engine the daemon and --direct share, over the fixture
// fleet with fake drivers, a green doctor, the given recipients file, and the
// service account as the ctl's own account.
//
// Unlike sealerEngine its roots are the deployment's (/rag), the way `serve
// --fake-drivers` runs: the fixture rows name /rag paths, and a decommission
// RENAMES a row's data_dir, which the fake host refuses outside its roots.
// Nothing touches the real /rag: the drivers are in memory, and everything
// the engine itself writes to disk (the job store, the locks, the gateway
// state, the registry, the recipients file it reads) is redirected into the
// test's temporary directory.
func archiveEngine(t *testing.T, recipients string) (jobs.Engine, jobs.Drivers, string) {
	t.Helper()
	dir := t.TempDir()
	roots := paths.NewRoots("/rag", paths.Overrides{
		CtlConfigDir: filepath.Join(dir, "config"), CtlStateDir: filepath.Join(dir, "state"),
		SharedLockDir: filepath.Join(dir, "locks"), ProxyDir: filepath.Join(dir, "proxy"),
		ImagesDir: filepath.Join(dir, "images"),
	})
	if recipients != "" {
		if err := os.MkdirAll(roots.CtlConfigDir, 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(roots.BackupRecipients(), []byte(recipients), 0o640); err != nil {
			t.Fatal(err)
		}
	}
	regPath := filepath.Join(dir, "registry.json")
	writeFleet(t, regPath, FixtureFleet())
	now := func() time.Time { return time.Date(2026, 10, 8, 10, 0, 0, 0, time.UTC) }
	eng, drv, err := BuildEngineAndDrivers(EngineConfig{
		Roots: roots, RegistryPath: regPath, StorePath: filepath.Join(dir, "jobs.db"),
		Mode: model.WorkerDirect, Host: "test", FakeDrivers: true, Now: now, Owner: "svcbvbrc",
		Doctor: func(ctx context.Context, tenant, op string) (model.DoctorResponse, error) {
			return model.DoctorResponse{
				Status: model.StatusGreen, Hash: "sha256:" + strings.Repeat("7", 64),
				GeneratedAt: now().Format(time.RFC3339),
				Scope:       model.Scope{Tenant: model.NullString(tenant), Op: model.NullString(op)},
				Findings:    []model.Finding{},
			}, nil
		},
	})
	if err != nil {
		t.Fatalf("BuildEngineAndDrivers: %v", err)
	}
	return eng, drv, regPath
}

// decommissionRequest is a decommission of the instance-supervised fixture
// tenant, confirmed.
func decommissionRequest(args map[string]any, dry bool) jobs.Request {
	return jobs.Request{
		Op: "decommission", Tenant: instanceFixtureName, Args: args, DryRun: dry, Confirm: instanceFixtureName,
		IdempotencyKey: "decommission-archive", Mode: model.WorkerDirect,
		Principal: jobs.Principal{Subject: "local:0", Role: "operator", Method: model.AuthLocal},
	}
}

// TestTheEngineArchivesThenQuarantinesAnInstanceTenant is PR-G1.3's archive
// path through the engine the daemon runs, on the fixture fleet with a real
// age recipient — the half the conformance suite cannot reach, because its
// fixture daemon deliberately carries no recipient (its secrets tests depend
// on that). The default decommission takes a fenced, checked bundle whose
// secrets.age opens with the identity, keeps the API down, quarantines the
// tenant, and the row records the block.
func TestTheEngineArchivesThenQuarantinesAnInstanceTenant(t *testing.T) {
	id, err := age.GenerateX25519Identity()
	if err != nil {
		t.Fatal(err)
	}
	eng, drv, regPath := archiveEngine(t, "# fixture recipient\n"+id.Recipient().String()+"\n")
	ctx := context.Background()

	plan, _, err := eng.Submit(ctx, decommissionRequest(map[string]any{}, true))
	if err != nil {
		t.Fatalf("the archive decommission dry run was refused: %v", err)
	}
	var sawCheck, sawQuarantine bool
	for _, s := range plan.Steps {
		if strings.Contains(s.Title, "check the bundle") {
			sawCheck = true
		}
		if strings.Contains(s.Title, "quarantine the data directory") {
			if !sawCheck {
				t.Errorf("the quarantine is planned before the bundle check")
			}
			sawQuarantine = true
		}
		if sawCheck && strings.Contains(s.Title, "start the API") {
			t.Errorf("the archive decommission starts the API after the bundle: %q", s.Title)
		}
		for _, w := range s.WouldWrite {
			if strings.ContainsAny(w.Path, "<>") {
				t.Errorf("step %q plans a would_write path plan.json refuses: %s", s.Title, w.Path)
			}
		}
	}
	if !sawQuarantine {
		t.Fatal("no quarantine step planned")
	}

	_, job, err := eng.Submit(ctx, decommissionRequest(map[string]any{}, false))
	if err != nil {
		t.Fatalf("submit: %v", err)
	}
	done := waitForState(t, eng, job.ID, model.JobSucceeded, model.JobFailed, model.JobRolledBack)
	if done.State != model.JobSucceeded {
		for _, s := range done.Steps {
			t.Logf("%d %s %s %s", s.N, s.State, s.Title, s.Error)
		}
		t.Fatalf("decommission job ended %s", done.State)
	}

	f, err := registry.Load(regPath)
	if err != nil {
		t.Fatalf("loading the registry the job wrote: %v", err)
	}
	row := f.Tenants[instanceFixtureName]
	if row.State != registry.StateQuarantined {
		t.Fatalf("state = %s", row.State)
	}
	if row.LastBackup == nil || !row.LastBackup.Fenced || !row.LastBackup.Checked {
		t.Fatalf("last_backup = %+v, want the archive, fenced and checked", row.LastBackup)
	}
	q := row.Quarantine
	if q == nil {
		t.Fatal("no quarantine block")
	}
	if q.JobID != job.ID || string(q.Bundle) != row.LastBackup.Bundle ||
		!strings.HasPrefix(q.Dir, row.DataDir+registry.QuarantineMarker) {
		t.Errorf("quarantine = %+v (job %s, last_backup %s)", *q, job.ID, row.LastBackup.Bundle)
	}
	sealed, err := drv.Files().ReadFile(ctx, filepath.Join(string(q.Bundle), "secrets.age"))
	if err != nil {
		t.Fatalf("the archive has no secrets.age: %v", err)
	}
	if _, err := seal.Unseal([]age.Identity{id}, sealed); err != nil {
		t.Fatalf("the identity cannot open the archive's secrets.age: %v", err)
	}
}

// Without a recipient the default is refused, naming the repair, and
// `archive: false` is the decommission that existed before.
func TestTheEngineRefusesAnArchiveWithoutARecipient(t *testing.T) {
	eng, _, _ := archiveEngine(t, "")
	_, _, err := eng.Submit(context.Background(), decommissionRequest(map[string]any{}, true))
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(fmtErr(err), "fleet backup-identity init") {
		t.Fatalf("archive without a recipient = %v", err)
	}
}
