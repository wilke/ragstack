package jobs

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

func newTestStore(t *testing.T) (Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "ctl", "jobs.db")
	st, err := NewStore(path)
	if err != nil {
		t.Fatalf("NewStore: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	return st, path
}

func sampleJob(id string) *model.Job {
	return &model.Job{
		ID: id, Op: "start", Tenant: "dev", Principal: "key:ops",
		AuthMethod: model.AuthAPIKey, State: model.JobQueued,
		PlanHash:  sha256Of([]byte("plan")),
		RequestID: "0123456789abcdef", IdempotencyKey: "key-" + id,
		CreatedAt:    time.Now().UTC().Format(time.RFC3339),
		Reservations: []model.Reservation{},
		Steps: []model.Step{
			{N: 1, Kind: "systemd", Title: "start the api unit", State: model.StepPending, ExternalIDs: []string{}},
		},
	}
}

func TestStoreCreateGetUpdateList(t *testing.T) {
	st, _ := newTestStore(t)
	ctx := context.Background()

	var ids []string
	var gen ulidGen
	for i := 0; i < 3; i++ {
		j := sampleJob(gen.new(time.Now()))
		j.Tenant = model.NullString([]string{"dev", "demo", "dev"}[i])
		if _, err := st.Create(ctx, j, "fp-"+j.ID); err != nil {
			t.Fatalf("Create: %v", err)
		}
		ids = append(ids, j.ID)
	}

	got, err := st.Get(ctx, ids[0])
	if err != nil || got.Op != "start" || got.State != model.JobQueued {
		t.Fatalf("Get: %+v %v", got, err)
	}
	if _, err := st.Get(ctx, "01ZZZZZZZZZZZZZZZZZZZZZZZZ"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Get of an unknown job = %v, want ErrNotFound", err)
	}

	got.State = model.JobRunning
	if err := st.Update(ctx, got); err != nil {
		t.Fatalf("Update: %v", err)
	}
	again, _ := st.Get(ctx, ids[0])
	if again.State != model.JobRunning {
		t.Fatalf("state after Update = %q", again.State)
	}

	all, truncated, err := st.List(ctx, ListFilter{})
	if err != nil || len(all) != 3 || truncated {
		t.Fatalf("List all = %d jobs, truncated=%v, err=%v", len(all), truncated, err)
	}
	if all[0].ID != ids[2] {
		t.Fatalf("List is not newest-first: %s then %s", all[0].ID, all[1].ID)
	}
	dev, _, _ := st.List(ctx, ListFilter{Tenant: "dev"})
	if len(dev) != 2 {
		t.Fatalf("List tenant=dev = %d jobs, want 2", len(dev))
	}
	running, _, _ := st.List(ctx, ListFilter{State: model.JobRunning})
	if len(running) != 1 {
		t.Fatalf("List state=running = %d jobs, want 1", len(running))
	}
	page, truncated, _ := st.List(ctx, ListFilter{Limit: 2})
	if len(page) != 2 || !truncated {
		t.Fatalf("List limit=2 = %d jobs, truncated=%v", len(page), truncated)
	}

	live, err := st.Running(ctx)
	if err != nil || len(live) != 1 || live[0].ID != ids[0] {
		t.Fatalf("Running = %+v %v", live, err)
	}
}

// TestStoreIdempotency is the contract's duplicate rule: the SAME request
// under a key returns the original job, a DIFFERENT one is 409 duplicate.
func TestStoreIdempotency(t *testing.T) {
	st, _ := newTestStore(t)
	ctx := context.Background()
	var gen ulidGen

	first := sampleJob(gen.new(time.Now()))
	first.IdempotencyKey = "deploy-2026-09-14"
	if _, err := st.Create(ctx, first, "fingerprint-A"); err != nil {
		t.Fatalf("Create: %v", err)
	}
	first.State = model.JobRunning
	if err := st.Update(ctx, first); err != nil {
		t.Fatal(err)
	}

	retry := sampleJob(gen.new(time.Now()))
	retry.IdempotencyKey = "deploy-2026-09-14"
	got, err := st.Create(ctx, retry, "fingerprint-A")
	if err != nil {
		t.Fatalf("a retry of the same request: %v", err)
	}
	if got.ID != first.ID {
		t.Fatalf("the retry made a new job %s, want the original %s", got.ID, first.ID)
	}
	if got.State != model.JobRunning {
		t.Fatalf("the original came back as %q, want the state it had reached", got.State)
	}

	other := sampleJob(gen.new(time.Now()))
	other.IdempotencyKey = "deploy-2026-09-14"
	if _, err := st.Create(ctx, other, "fingerprint-B"); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("a different request under the same key = %v, want ErrDuplicate", err)
	}
	if _, err := st.Get(ctx, other.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("the refused job was persisted anyway: %v", err)
	}
}

func TestStoreStepLogsReservationsAndAudit(t *testing.T) {
	st, _ := newTestStore(t)
	ctx := context.Background()
	var gen ulidGen
	j := sampleJob(gen.new(time.Now()))
	if _, err := st.Create(ctx, j, "fp"); err != nil {
		t.Fatal(err)
	}

	for _, line := range []string{"starting", "started"} {
		if err := st.AppendStepLog(ctx, j.ID, 1, line); err != nil {
			t.Fatal(err)
		}
	}
	log, err := st.StepLog(ctx, j.ID, 1)
	if err != nil || log != "starting\nstarted\n" {
		t.Fatalf("StepLog = %q, %v", log, err)
	}

	rs, ok := st.(ReservationStore)
	if !ok {
		t.Fatal("the SQLite store does not implement ReservationStore")
	}
	if err := rs.PutReservation(ctx, j.ID, model.Reservation{Resource: "port:24040"}, time.Now()); err != nil {
		t.Fatal(err)
	}
	held, err := rs.Reservations(ctx, j.ID)
	if err != nil || len(held) != 1 || held[0].Resource != "port:24040" {
		t.Fatalf("Reservations = %+v %v", held, err)
	}

	dur := int64(1200)
	rows := []model.AuditRow{
		{At: "2026-09-14T10:00:00Z", Phase: model.AuditIntent, Principal: "key:ops",
			AuthMethod: model.AuthAPIKey, RequestID: "0123456789abcdef", Op: "start",
			Tenant: "dev", JobID: model.NullString(j.ID), ArgsRedacted: map[string]any{"force": true},
			PlanHash: model.NullString(j.PlanHash), Outcome: "accepted"},
		{At: "2026-09-14T10:00:02Z", Phase: model.AuditResult, Principal: "key:ops",
			AuthMethod: model.AuthAPIKey, RequestID: "0123456789abcdef", Op: "start",
			Tenant: "dev", JobID: model.NullString(j.ID), ArgsRedacted: map[string]any{"force": true},
			PlanHash: model.NullString(j.PlanHash), Outcome: "succeeded", DurationMS: &dur},
	}
	for _, r := range rows {
		if err := st.Audit(ctx, r); err != nil {
			t.Fatal(err)
		}
	}
	got, truncated, err := st.AuditList(ctx, 10)
	if err != nil || len(got) != 2 || truncated {
		t.Fatalf("AuditList = %d rows, truncated=%v, err=%v", len(got), truncated, err)
	}
	if got[0].Phase != model.AuditResult || got[0].DurationMS == nil || *got[0].DurationMS != 1200 {
		t.Fatalf("newest audit row = %+v", got[0])
	}
	if got[0].ID < 1 {
		t.Fatalf("audit row id = %d, want a positive integer (the contract's minimum)", got[0].ID)
	}
	if _, truncated, _ := st.AuditList(ctx, 1); !truncated {
		t.Fatal("AuditList(limit=1) over 2 rows did not report truncation")
	}
}

func TestStoreEnvelopeIsDeliveredOnce(t *testing.T) {
	st, _ := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC()
	env := Envelope{
		JobID: "01JQ8ZKE7R9X4M2V6T5S3N1P0B", Principal: "key:ops", Op: "key-mint",
		CreatedAt: now, ExpiresAt: now.Add(15 * time.Minute), Sealed: true,
		Payload: []byte("sealed-bytes"),
	}
	if err := st.PutEnvelope(ctx, env); err != nil {
		t.Fatal(err)
	}
	if _, err := st.TakeEnvelope(ctx, env.JobID, "key:other", now); !errors.Is(err, ErrForbidden) {
		t.Fatalf("another principal = %v, want ErrForbidden", err)
	}
	got, err := st.TakeEnvelope(ctx, env.JobID, "key:ops", now)
	if err != nil || string(got.Payload) != "sealed-bytes" || !got.Sealed {
		t.Fatalf("TakeEnvelope = %+v %v", got, err)
	}
	if _, err := st.TakeEnvelope(ctx, env.JobID, "key:ops", now); !errors.Is(err, ErrGone) {
		t.Fatalf("second take = %v, want ErrGone", err)
	}
	if _, err := st.TakeEnvelope(ctx, "01JQ8ZKE7R9X4M2V6T5S3N1P0C", "key:ops", now); !errors.Is(err, ErrNotFound) {
		t.Fatalf("a job that minted nothing = %v, want ErrNotFound", err)
	}

	expired := env
	expired.JobID = "01JQ8ZKE7R9X4M2V6T5S3N1P0D"
	expired.ExpiresAt = now.Add(-time.Minute)
	if err := st.PutEnvelope(ctx, expired); err != nil {
		t.Fatal(err)
	}
	if _, err := st.TakeEnvelope(ctx, expired.JobID, "key:ops", now); !errors.Is(err, ErrGone) {
		t.Fatalf("an expired envelope = %v, want ErrGone", err)
	}
}

// TestStoreBackupIsReadable proves VACUUM INTO produced a database, not a
// file: the copy is opened and queried.
func TestStoreBackupIsReadable(t *testing.T) {
	st, _ := newTestStore(t)
	ctx := context.Background()
	var gen ulidGen
	j := sampleJob(gen.new(time.Now()))
	if _, err := st.Create(ctx, j, "fp"); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(t.TempDir(), "backups", "jobs.db")
	if err := st.Backup(ctx, dest); err != nil {
		t.Fatalf("Backup: %v", err)
	}
	if fi, err := os.Stat(dest); err != nil || fi.Size() == 0 {
		t.Fatalf("backup file: %v", err)
	}
	copyStore, err := NewStore(dest)
	if err != nil {
		t.Fatalf("opening the backup: %v", err)
	}
	defer func() { _ = copyStore.Close() }()
	if _, err := copyStore.Get(ctx, j.ID); err != nil {
		t.Fatalf("the backup does not contain the job: %v", err)
	}
	if err := st.Backup(ctx, dest); err == nil {
		t.Fatal("Backup over an existing file should refuse")
	}
}

// TestStoreTwoProcessesOneFile is the daemon-plus---direct-CLI case: two
// independent Store handles on one file, writing concurrently. It is the
// reason for WAL and the busy timeout.
func TestStoreTwoProcessesOneFile(t *testing.T) {
	st, path := newTestStore(t)
	other, err := NewStore(path)
	if err != nil {
		t.Fatalf("second handle: %v", err)
	}
	defer func() { _ = other.Close() }()
	ctx := context.Background()

	var gen ulidGen
	var wg sync.WaitGroup
	errs := make(chan error, 20)
	for i := 0; i < 10; i++ {
		for _, s := range []Store{st, other} {
			wg.Add(1)
			go func(s Store, j *model.Job) {
				defer wg.Done()
				if _, err := s.Create(ctx, j, "fp-"+j.ID); err != nil {
					errs <- err
				}
			}(s, sampleJob(gen.new(time.Now())))
		}
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatalf("concurrent Create: %v", err)
	}
	all, _, _ := st.List(ctx, ListFilter{Limit: 100})
	if len(all) != 20 {
		t.Fatalf("%d jobs landed, want 20", len(all))
	}
	// The second handle sees every write the first made.
	seen, _, _ := other.List(ctx, ListFilter{Limit: 100})
	if len(seen) != 20 {
		t.Fatalf("the second handle sees %d jobs, want 20", len(seen))
	}
}

// TestULIDMatchesTheContractPattern checks the job-id grammar and that ids
// minted inside one millisecond still sort in mint order.
func TestULIDMatchesTheContractPattern(t *testing.T) {
	re := regexp.MustCompile(`^[0-7][0-9A-HJKMNP-TV-Z]{25}$`)
	var gen ulidGen
	at := time.Date(2026, 9, 14, 10, 0, 0, 0, time.UTC)
	prev := ""
	for i := 0; i < 2000; i++ {
		id := gen.new(at)
		if !re.MatchString(id) {
			t.Fatalf("id %q does not match the contract pattern", id)
		}
		if prev != "" && id <= prev {
			t.Fatalf("ids do not increase within a millisecond: %s then %s", prev, id)
		}
		prev = id
	}
	later := gen.new(at.Add(time.Second))
	if later <= prev {
		t.Fatalf("a later timestamp gave a smaller id: %s then %s", prev, later)
	}
}
