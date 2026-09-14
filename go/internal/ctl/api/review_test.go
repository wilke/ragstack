package api

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// --------------------------------------------------------------------------
// The arg redactor
// --------------------------------------------------------------------------

// TestIsSecretArgClassifiesByNameNotBySubstring.
//
// args_redacted is not just what an audit row SHOWS. It is what a re-plan is
// computed from: the plan hash is over args_redacted, and a continuation
// (resume, rebuild, cancel of an interrupted job) has nothing but the stored
// copy to work from. So a name blanked here is a name every later re-plan
// sees as the literal string "<redacted>".
//
// The substring test blanked `key` — env-set's SETTING NAME, "LOG_LEVEL",
// which contains "KEY". The stored args then read {key: "<redacted>"}, the
// re-plan asked env-set to edit a setting called "<redacted>", and the op
// refused it as not a known ragstack setting. Every rebuild of that job
// refused, forever, with nothing an operator could do about it.
func TestIsSecretArgClassifiesByNameNotBySubstring(t *testing.T) {
	for _, name := range []string{"key", "keys", "label", "subject", "id", "role", "value", "name", "from", "as"} {
		if isSecretArg(name) {
			t.Errorf("isSecretArg(%q) = true; blanking it makes the job unreplannable", name)
		}
	}
	for _, name := range []string{"ctl_api_key", "token", "secret", "password", "dsn", "api_key", "API_KEYS"} {
		if !isSecretArg(name) {
			t.Errorf("isSecretArg(%q) = false; the value is a credential", name)
		}
	}
	// Whatever the settings classifier calls a secret is one here too.
	for _, name := range []string{"NEO4J_PASSWORD", "OPENAI_API_KEY", "SOME_TOKEN"} {
		if settings.Classify(strings.ToUpper(name)) == settings.Secret && !isSecretArg(name) {
			t.Errorf("isSecretArg(%q) disagrees with settings.Classify", name)
		}
	}
}

// TestRedactArgsBlanksTheValueOnlyWhenItsKeyIsSecret: env-set's `{key,
// value}` pair. The VALUE is a credential exactly when the KEY names one; the
// key itself never is, because it identifies what to edit.
func TestRedactArgsBlanksTheValueOnlyWhenItsKeyIsSecret(t *testing.T) {
	r := &engineRedactor{text: settings.NewRedactor()}

	public := r.RedactArgs(map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"})
	if public["key"] != "LOG_LEVEL" {
		t.Fatalf("the setting NAME was redacted: %+v", public)
	}
	if public["value"] != "DEBUG" {
		t.Fatalf("a public setting's value was redacted: %+v", public)
	}

	secret := r.RedactArgs(map[string]any{"key": "API_KEYS", "value": "the-real-thing"})
	if secret["key"] != "API_KEYS" {
		t.Fatalf("the setting NAME was redacted: %+v", secret)
	}
	if secret["value"] != argRedacted {
		t.Fatalf("a secret setting's value was NOT redacted: %+v", secret)
	}

	// The op vocabulary: a minted key's label and role identify it; the key
	// material itself never appears in the clear.
	mint := r.RedactArgs(map[string]any{"label": "ops", "role": "admin", "ctl_api_key": "sk-live-123"})
	if mint["label"] != "ops" || mint["role"] != "admin" {
		t.Fatalf("the mint's identifying args were redacted: %+v", mint)
	}
	if mint["ctl_api_key"] != argRedacted {
		t.Fatalf("a credential arg survived in the clear: %+v", mint)
	}
}

// TestAnInterruptedEnvSetResumesThroughItsRedactedArgs is the round trip the
// bug broke, end to end over the real ops registry and the real engine: plan,
// store the redacted args, interrupt, re-plan from those args, and get the
// SAME plan hash back so the resume is allowed to proceed.
func TestAnInterruptedEnvSetResumesThroughItsRedactedArgs(t *testing.T) {
	dir := t.TempDir()
	roots := paths.NewRoots(dir, paths.Overrides{})
	fleet := registry.LiveFixture()
	tenant := fleet.Tenants["dev"]
	if tenant == nil {
		t.Fatal("the live fixture has no tenant dev")
	}
	tenant.Supervisor, tenant.Owner, tenant.State = "systemd", "svcbvbrc", "active"
	tenant.EnvLayout = "managed"

	storePath := filepath.Join(dir, "jobs.db")
	now := func() time.Time { return time.Date(2026, 9, 14, 10, 0, 0, 0, time.UTC) }
	green := func(ctx context.Context, tenantName, op string) (model.DoctorResponse, error) {
		return model.DoctorResponse{
			Status: model.StatusGreen, Hash: "sha256:" + strings.Repeat("0", 64),
			GeneratedAt: now().Format(time.RFC3339),
			Scope:       model.Scope{Tenant: model.NullString(tenantName), Op: model.NullString(op)},
			Findings:    []model.Finding{},
		}, nil
	}
	eng, err := BuildEngine(EngineConfig{
		Roots:        roots,
		RegistryPath: filepath.Join(dir, "registry.json"),
		StorePath:    storePath,
		Mode:         model.WorkerDaemon,
		Host:         "testhost",
		FakeDrivers:  true,
		Now:          now,
		LoadFleet:    func() (*registry.Fleet, error) { return fleet, nil },
		SaveFleet:    func(*registry.Fleet) error { return nil },
		Doctor:       green,
		Logger:       slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError})),
	})
	if err != nil {
		t.Fatalf("BuildEngine: %v", err)
	}
	ctx := context.Background()
	p := jobs.Principal{Subject: "key:ops", Role: "operator", Method: model.AuthAPIKey, RequestID: "0123456789abcdef"}
	_, job, err := eng.Submit(ctx, jobs.Request{
		Op: "env-set", Tenant: "dev", Args: map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"},
		IdempotencyKey: "env-set-1", Principal: p, Mode: model.WorkerDaemon,
	})
	if err != nil {
		t.Fatalf("Submit env-set: %v", err)
	}
	done := waitForState(t, eng, job.ID, model.JobSucceeded, model.JobFailed, model.JobRolledBack)
	if done.State != model.JobSucceeded {
		t.Fatalf("env-set = %s %+v", done.State, done.Error)
	}

	// The audit row shows what was edited. A row that said key=<redacted>
	// would be useless to an operator AND unreplannable by the engine.
	rows, _, err := eng.Audit(ctx, 50)
	if err != nil {
		t.Fatal(err)
	}
	var intent *model.AuditRow
	for i := range rows {
		if rows[i].Phase == model.AuditIntent && string(rows[i].JobID) == job.ID {
			intent = &rows[i]
			break
		}
	}
	if intent == nil {
		t.Fatalf("no intent audit row for job %s", job.ID)
	}
	if intent.ArgsRedacted["key"] != "LOG_LEVEL" {
		t.Fatalf("the audit row hides which setting was edited: %+v", intent.ArgsRedacted)
	}
	if intent.ArgsRedacted["value"] != "DEBUG" {
		t.Fatalf("a public setting's value was redacted: %+v", intent.ArgsRedacted)
	}

	// Now interrupt it, the way reconcile-on-restart would, and resume. The
	// resume re-plans from the STORED redacted args and compares hashes: with
	// key blanked it re-planned env-set for a setting called "<redacted>" and
	// refused every time.
	st, err := jobs.NewStore(storePath)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = st.Close() }()
	stored, err := st.Get(ctx, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	stored.State = model.JobInterrupted
	stored.FinishedAt = ""
	stored.Worker, stored.Lock, stored.Error, stored.Rollback = nil, nil, nil, nil
	for i := range stored.Steps {
		stored.Steps[i].State = model.StepInterrupted
	}
	if err := st.Update(ctx, stored); err != nil {
		t.Fatal(err)
	}

	if _, err := eng.Resume(ctx, job.ID, p); err != nil {
		t.Fatalf("Resume of an interrupted env-set = %v; the redacted args must round-trip to the same plan", err)
	}
	after := waitForState(t, eng, job.ID, model.JobSucceeded, model.JobFailed, model.JobRolledBack)
	if after.State != model.JobSucceeded {
		t.Fatalf("after Resume: %s %+v", after.State, after.Error)
	}
}

// waitForState polls the engine until the job reaches one of want.
func waitForState(t *testing.T, e jobs.Engine, id string, want ...model.JobState) *model.Job {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	var last model.JobState
	for time.Now().Before(deadline) {
		j, err := e.Get(context.Background(), id)
		if err == nil {
			last = j.State
			for _, w := range want {
				if j.State == w {
					return j
				}
			}
		}
		time.Sleep(3 * time.Millisecond)
	}
	t.Fatalf("job %s never reached %v (last %q)", id, want, last)
	return nil
}

// --------------------------------------------------------------------------
// cancel's confirm
// --------------------------------------------------------------------------

// TestCancelThreadsTheEnvelopesConfirmToTheEngine: openapi.yaml gives
// /v1/jobs/{id}/cancel the same OpRequest every mutation has, and requires
// `confirm` when the cancel would roll a job's succeeded steps back. The
// handler decoded the envelope and then dropped that field on the floor, so
// the engine could never see the confirmation and a cancel that needed one
// could not be performed at all.
func TestCancelThreadsTheEnvelopesConfirmToTheEngine(t *testing.T) {
	eng := &fakeEngine{job: sampleJob()}
	h := newEngineServer(t, eng)
	w := do(t, h, http.MethodPost, "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/cancel",
		opHeaders(), opBody(`"confirm":"dev"`))
	if w.Code != http.StatusAccepted {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	if eng.continuation != "cancel" {
		t.Fatalf("called %q, want cancel", eng.continuation)
	}
	if eng.confirm != "dev" {
		t.Fatalf("the engine was handed confirm %q, want %q", eng.confirm, "dev")
	}
}

// TestCancelWithoutConfirmStillReachesTheEngine: the HTTP layer does not
// decide whether a confirm is needed — only the engine knows whether the job
// has succeeded steps with a rollback — so an empty confirm is forwarded and
// the engine's 428 is what the client sees.
func TestCancelWithoutConfirmStillReachesTheEngine(t *testing.T) {
	eng := &fakeEngine{job: sampleJob()}
	h := newEngineServer(t, eng)
	if w := do(t, h, http.MethodPost, "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/cancel",
		opHeaders(), opBody("")); w.Code != http.StatusAccepted {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	if eng.confirm != "" {
		t.Fatalf("confirm = %q, want it forwarded empty", eng.confirm)
	}
}

// TestCancelAnswers428WhenTheEngineDemandsAConfirm pins the mapping the
// client depends on to print `--yes-destructive <name>`.
func TestCancelAnswers428WhenTheEngineDemandsAConfirm(t *testing.T) {
	eng := &fakeEngine{err: extraErr{
		base:  jobs.ErrConfirmRequired,
		extra: map[string]any{"confirm_value": "dev"},
	}}
	h := newEngineServer(t, eng)
	w := do(t, h, http.MethodPost, "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/cancel", opHeaders(), opBody(""))
	body := assertError(t, w, http.StatusPreconditionRequired, "confirm_required")
	extra, _ := body["extra"].(map[string]any)
	if extra["confirm_value"] != "dev" {
		t.Fatalf("extra = %+v, want the confirm value the operator must type", extra)
	}
}
