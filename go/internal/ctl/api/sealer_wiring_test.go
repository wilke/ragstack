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
	"github.com/ragstack/ragstack/internal/ctl/seal"
)

// sealerEngine builds the engine the daemon and --direct share, over the
// fixture fleet with fake drivers and a green doctor, and with `recipients`
// (if non-empty) as <CtlConfigDir>/backup-recipients.txt.
func sealerEngine(t *testing.T, recipients string) (jobs.Engine, jobs.Drivers, paths.Roots) {
	t.Helper()
	dir := t.TempDir()
	roots := paths.NewRoots(dir, paths.Overrides{})
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
		Mode: model.WorkerDirect, Host: "test", FakeDrivers: true, Now: now,
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
	return eng, drv, roots
}

func backupRequest(args map[string]any, dry bool) jobs.Request {
	return jobs.Request{
		Op: "backup", Tenant: managedFixtureName, Args: args, DryRun: dry,
		IdempotencyKey: "sealer-wiring", Mode: model.WorkerDirect,
		Principal: jobs.Principal{Subject: "local:0", Role: "operator", Method: model.AuthLocal},
	}
}

// TestTheEngineSealsBackupsToTheConfiguredRecipients is the wiring, end to
// end: BuildEngineAndDrivers loads backup-recipients.txt, `secrets=require`
// plans, the job runs, and the secrets.age it wrote opens with the identity.
func TestTheEngineSealsBackupsToTheConfiguredRecipients(t *testing.T) {
	id, err := age.GenerateX25519Identity()
	if err != nil {
		t.Fatal(err)
	}
	eng, drv, roots := sealerEngine(t, "# fixture recipient\n"+id.Recipient().String()+"\n")
	ctx := context.Background()

	plan, _, err := eng.Submit(ctx, backupRequest(map[string]any{"secrets": "require"}, true))
	if err != nil {
		t.Fatalf("secrets=require with a recipient was refused: %v", err)
	}
	var seals bool
	for _, s := range plan.Steps {
		if strings.Contains(s.Title, "seal the secret files into secrets.age") {
			seals = true
			if !strings.Contains(strings.Join(s.Warnings, " "), "sha256:") {
				t.Errorf("the seal step does not name the recipient fingerprint: %v", s.Warnings)
			}
		}
	}
	if !seals {
		t.Fatal("the plan has no seal step")
	}

	_, job, err := eng.Submit(ctx, backupRequest(map[string]any{"secrets": "require"}, false))
	if err != nil {
		t.Fatalf("submit: %v", err)
	}
	done := waitForState(t, eng, job.ID, model.JobSucceeded, model.JobFailed, model.JobRolledBack)
	if done.State != model.JobSucceeded {
		t.Fatalf("backup job ended %s", done.State)
	}
	matches, err := drv.Files().ReadDir(ctx, filepath.Join(roots.BackupsDir, managedFixtureName))
	if err != nil || len(matches) == 0 {
		t.Fatalf("no bundle directory: %v", err)
	}
	var sealed []byte
	for _, m := range matches {
		if b, err := drv.Files().ReadFile(ctx, filepath.Join(roots.BackupsDir, managedFixtureName, m.Name, "secrets.age")); err == nil {
			sealed = b
		}
	}
	if sealed == nil {
		t.Fatal("the bundle has no secrets.age")
	}
	if _, err := seal.Unseal([]age.Identity{id}, sealed); err != nil {
		t.Fatalf("the identity cannot open the bundle's secrets.age: %v", err)
	}
}

// TestWithoutUsableRecipientsTheEngineRefusesSecretsRequire: absent and
// malformed files both leave the engine with no Sealer.
func TestWithoutUsableRecipientsTheEngineRefusesSecretsRequire(t *testing.T) {
	for name, body := range map[string]string{"absent": "", "malformed": "not-a-public-key\n", "empty": "# nobody\n"} {
		eng, _, _ := sealerEngine(t, body)
		_, _, err := eng.Submit(context.Background(), backupRequest(map[string]any{"secrets": "require"}, true))
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "fleet backup-identity init") {
			t.Errorf("%s: secrets=require = %v, want a refusal naming the repair", name, err)
		}
		if strings.Contains(fmtErr(err), "not-a-public-key") {
			t.Errorf("%s: the refusal quotes the recipients file", name)
		}
		// The default still plans (and excludes the secrets).
		if _, _, err := eng.Submit(context.Background(), backupRequest(map[string]any{}, true)); err != nil {
			t.Errorf("%s: a default backup was refused: %v", name, err)
		}
	}
}

func fmtErr(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
