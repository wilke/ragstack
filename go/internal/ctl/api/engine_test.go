package api

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// TestSetHostToolsFromEnvIsTheOneSourceBothEntryPointsRead covers the helper
// `serve` and `--direct` share. Two entry points that read the CTL_* paths
// separately would build two different driver sets on the same host, and the
// operator debugging that would have no reason to suspect which process read
// which variable.
func TestSetHostToolsFromEnvIsTheOneSourceBothEntryPointsRead(t *testing.T) {
	t.Setenv(EnvSystemctlBin, "/bin/systemctl")
	t.Setenv(EnvGitBin, "/opt/git/bin/git")
	t.Setenv(EnvNodeBin, "/home/wilke/.local/bin/node")
	t.Setenv(EnvNpmBin, "  /home/wilke/.local/bin/npm  ") // whitespace is trimmed
	t.Setenv(EnvApptainerBin, "/usr/local/bin/apptainer")
	t.Setenv(EnvMirror, "/rag/repos/ragstack.git")
	t.Setenv(EnvNpmCache, "   ") // blank: says nothing

	var cfg EngineConfig
	cfg.NpmCache = "/already/set"
	SetHostToolsFromEnv(&cfg)

	if cfg.Systemctl != "/bin/systemctl" || cfg.Git != "/opt/git/bin/git" ||
		cfg.Node != "/home/wilke/.local/bin/node" || cfg.Npm != "/home/wilke/.local/bin/npm" ||
		cfg.Apptainer != "/usr/local/bin/apptainer" || cfg.Mirror != "/rag/repos/ragstack.git" {
		t.Errorf("the environment did not reach the config: %+v", cfg)
	}
	// A blank variable says nothing, so it must not erase a value the caller
	// already had.
	if cfg.NpmCache != "/already/set" {
		t.Errorf("a blank variable overwrote the configured value: %q", cfg.NpmCache)
	}
}

// TestAnEmptyPreconditionRowGatesOnNothingThroughTheWiredEngine is the bug
// `ragstack-ctl env pg-password hackathon --yes --wait` hit on coconut:
//
//	refused: doctor_red: yellow; pass force_with_doctor_diff=sha256:…
//
// with the only warn-level findings being `linger_missing`,
// `runtime_dir_missing` and `user_dropin_missing` — the systemd trio PR-D2
// abandoned, which no operation can clear. `env-pg-password`'s precondition row
// is `{}`: it gates on nothing, deliberately. doctor.GateCodes reported that
// empty row as NIL, the engine's gate read nil as "no row, be conservative",
// and the op that gates on nothing was gated on everything.
//
// This is the wiring, not the gate logic (that is in the jobs package): the
// table PreconditionCodes hands the engine, and an engine built the way
// BuildEngineAndDrivers builds it — which is the SAME construction `--direct`
// runs on, since cmd/ragstack-ctl's directEngine calls this function rather
// than assembling an engine of its own.
func TestAnEmptyPreconditionRowGatesOnNothingThroughTheWiredEngine(t *testing.T) {
	// The table itself: an op with a row that raises nothing is KNOWN and
	// gates on nothing. Only an op with no row at all is unknown.
	if codes, known := PreconditionCodes("env-pg-password"); !known || len(codes) != 0 {
		t.Errorf("PreconditionCodes(env-pg-password) = %v, %v; want an empty set on a KNOWN op", codes, known)
	}
	if codes, known := PreconditionCodes("backup"); !known || len(codes) == 0 {
		t.Errorf("PreconditionCodes(backup) = %v, %v; want the op's own findings", codes, known)
	}
	if codes, known := PreconditionCodes("no-such-verb"); known || codes != nil {
		t.Errorf("PreconditionCodes(no-such-verb) = %v, %v; want the unknown-op answer", codes, known)
	}

	// And the engine the daemon and `--direct` share, on the host's permanent
	// yellow.
	trio := []model.Finding{
		{Level: model.LevelWarn, Code: doctor.LingerMissing, Detail: "no linger for svcbvbrc"},
		{Level: model.LevelWarn, Code: doctor.RuntimeDirMissing, Detail: "no XDG_RUNTIME_DIR"},
		{Level: model.LevelWarn, Code: doctor.UserDropInMissing, Detail: "no root drop-in"},
	}
	eng := yellowFixtureEngine(t, trio)
	if _, _, err := eng.Submit(context.Background(), jobs.Request{
		Op: "env-pg-password", Tenant: managedFixtureName, Args: map[string]any{},
		IdempotencyKey: "empty-row-runs", Mode: model.WorkerDirect,
		Principal: jobs.Principal{Subject: "local:0", Role: "operator", Method: model.AuthLocal},
	}); err != nil {
		t.Fatalf("env-pg-password was refused on warnings it does not depend on: %v", err)
	}

	// The other direction, through the same wiring: a warning that IS in the
	// op's row still has to be acknowledged, or the flag would mean nothing
	// anywhere.
	gated := yellowFixtureEngine(t, append(trio,
		model.Finding{Level: model.LevelWarn, Code: doctor.DiskLow, Tenant: managedFixtureName, Detail: "4% free"}))
	_, _, err := gated.Submit(context.Background(), jobs.Request{
		Op: "backup", Tenant: managedFixtureName, Args: map[string]any{},
		IdempotencyKey: "disk-low-is-backups", Mode: model.WorkerDirect,
		Principal: jobs.Principal{Subject: "local:0", Role: "operator", Method: model.AuthLocal},
	})
	if !errors.Is(err, jobs.ErrDoctorRed) {
		t.Fatalf("backup ran over a yellow disk_low unacknowledged: %v", err)
	}
	if !strings.Contains(err.Error(), doctor.DiskLow) {
		t.Errorf("the refusal does not name the finding backup depends on: %v", err)
	}
	if strings.Contains(err.Error(), doctor.LingerMissing) {
		t.Errorf("the refusal blames a finding no op can clear: %v", err)
	}
}

// yellowFixtureEngine builds the real engine over a scratch root and the
// fixture fleet, with an injected doctor whose findings are the caller's. The
// host this test runs on is not consulted: what is under test is the gate's
// reading of a finding set, not this machine's.
func yellowFixtureEngine(t *testing.T, findings []model.Finding) jobs.Engine {
	t.Helper()
	dir := t.TempDir()
	regPath := filepath.Join(dir, "registry.json")
	writeFleet(t, regPath, FixtureFleet())
	now := func() time.Time { return time.Date(2026, 9, 16, 10, 0, 0, 0, time.UTC) }
	eng, err := BuildEngine(EngineConfig{
		Roots:        paths.NewRoots(dir, paths.Overrides{}),
		RegistryPath: regPath,
		StorePath:    filepath.Join(dir, "jobs.db"),
		Mode:         model.WorkerDirect,
		Host:         "test",
		FakeDrivers:  true,
		Now:          now,
		Doctor: func(ctx context.Context, tenant, op string) (model.DoctorResponse, error) {
			return model.DoctorResponse{
				Status: model.StatusFor(findings), Hash: "sha256:" + strings.Repeat("7", 64),
				GeneratedAt: now().Format(time.RFC3339),
				Scope:       model.Scope{Tenant: model.NullString(tenant), Op: model.NullString(op)},
				Findings:    findings,
			}, nil
		},
	})
	if err != nil {
		t.Fatalf("BuildEngine: %v", err)
	}
	return eng
}
