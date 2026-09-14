package jobs

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"

	_ "modernc.org/sqlite" // pure-Go SQLite: CGO_ENABLED=0 stays true
)

// The durable record of every mutation: jobs.db, one SQLite file under
// <CtlStateDir> (default /rag/data/ctl/jobs.db).
//
// Two processes write it — the daemon and any number of `--direct` CLI runs —
// so the file, not a process's memory, is the source of truth. That is
// handled by WAL (a reader never blocks the writer), a busy timeout (a
// contending writer waits rather than failing), and IMMEDIATE transactions (a
// write transaction takes its write lock at BEGIN, so two writers cannot both
// read, both decide, and then have one of them fail to upgrade).
//
// Shape: the whole model.Job travels as a JSON document, with the columns a
// query actually filters on (id, op, tenant, state, created_at,
// idempotency_key) lifted out and indexed. The document is the contract type,
// so adding a field to job.json never needs a migration; the lifted columns
// are the only thing a migration would touch.

// migrations are applied in order, once, inside a transaction each.
var migrations = []string{
	`
CREATE TABLE IF NOT EXISTS jobs (
  id              TEXT PRIMARY KEY,
  op              TEXT NOT NULL,
  tenant          TEXT,
  state           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  idempotency_key TEXT NOT NULL DEFAULT '',
  doc             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state_idx   ON jobs(state);
CREATE INDEX IF NOT EXISTS jobs_tenant_idx  ON jobs(tenant);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_key_idx     ON jobs(idempotency_key);

CREATE TABLE IF NOT EXISTS idempotency (
  key         TEXT PRIMARY KEY,
  job_id      TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_args (
  job_id TEXT PRIMARY KEY,
  args   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS step_logs (
  seq    INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  n      INTEGER NOT NULL,
  at     TEXT NOT NULL,
  text   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS step_logs_idx ON step_logs(job_id, n, seq);

CREATE TABLE IF NOT EXISTS reservations (
  job_id     TEXT NOT NULL,
  resource   TEXT NOT NULL,
  until      TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (job_id, resource)
);

CREATE TABLE IF NOT EXISTS audit (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  at            TEXT NOT NULL,
  phase         TEXT NOT NULL,
  principal     TEXT NOT NULL,
  auth_method   TEXT NOT NULL,
  sudo_user     TEXT,
  request_id    TEXT NOT NULL,
  op            TEXT NOT NULL,
  tenant        TEXT,
  job_id        TEXT,
  args_redacted TEXT NOT NULL,
  plan_hash     TEXT,
  outcome       TEXT,
  error         TEXT,
  duration_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS audit_at_idx     ON audit(id DESC);
CREATE INDEX IF NOT EXISTS audit_tenant_idx ON audit(tenant);

CREATE TABLE IF NOT EXISTS envelopes (
  job_id       TEXT PRIMARY KEY,
  principal    TEXT NOT NULL,
  op           TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  sealed       INTEGER NOT NULL,
  payload      BLOB,
  delivered_at TEXT
);
`,
	// The worker's IDENTITY, kept beside the job rather than inside it.
	// job.json (the contract) records {pid, host, mode}; a pid alone cannot
	// survive pid reuse, and the process start time that makes the pair
	// unique is a host fact no client of the API should have to parse. So it
	// lives here, where only reconcile reads it.
	`
CREATE TABLE IF NOT EXISTS job_workers (
  job_id      TEXT PRIMARY KEY,
  pid         INTEGER NOT NULL,
  host        TEXT NOT NULL,
  start_time  INTEGER NOT NULL,
  mode        TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL
);
`,
}

// DefaultStorePath is where the daemon keeps jobs.db when the operator says
// nothing: <CtlStateDir>/jobs.db, i.e. /rag/data/ctl/jobs.db.
func DefaultStorePath(ctlStateDir string) string {
	return filepath.Join(ctlStateDir, "jobs.db")
}

// ReservationStore is the optional Store extension the engine uses to keep
// reservations queryable on their own — a reservation must survive an
// interruption, and "which port blocks are spoken for right now?" should be
// one SELECT, not a scan of every job document. A Store that does not
// implement it still works; its reservations live only in the job document.
type ReservationStore interface {
	PutReservation(ctx context.Context, jobID string, r model.Reservation, now time.Time) error
	Reservations(ctx context.Context, jobID string) ([]model.Reservation, error)
}

// ArgsStore is the optional Store extension that remembers a job's REDACTED
// args. A continuation (resume, continue after a daemon restart) has to
// re-plan the job to get its executable steps back — closures cannot be
// persisted — and a plan is a function of its args, so the args have to
// outlive the process.
//
// REDACTED, not the originals: the plan hash is computed over
// `args_redacted`, so the redacted document is exactly what a re-plan needs
// to reproduce the digest, and the unredacted values never reach the disk in
// any form but the idempotency fingerprint. The cost is that an op whose
// PLAN SHAPE depends on a secret-class argument cannot be resumed across a
// restart — it will read as plan_stale, which is a refusal, not a silent
// divergence.
type ArgsStore interface {
	PutArgs(ctx context.Context, jobID string, argsRedacted map[string]any) error
	Args(ctx context.Context, jobID string) (map[string]any, error)
}

// WorkerIdentity is who is running a job, precisely enough to survive pid
// reuse: the pair (pid, start time) is unique for the life of the kernel, and
// the host says which kernel.
type WorkerIdentity struct {
	PID       int
	Host      string
	StartTime uint64
	Mode      model.WorkerMode
}

// WorkerStore is the optional Store extension reconcile uses to tell a live
// worker from a recycled pid. It is deliberately NOT part of model.JobWorker:
// job.json is the published contract, and the start time is a /proc detail no
// API client should have to know about. A Store that does not implement it
// still works — reconcile then falls back to host + kill(pid, 0) + the flock
// probe, which is what the code did before.
type WorkerStore interface {
	PutWorker(ctx context.Context, jobID string, w WorkerIdentity) error
	// Worker returns (nil, nil) when nothing was recorded for jobID.
	Worker(ctx context.Context, jobID string) (*WorkerIdentity, error)
}

type store struct {
	db   *sql.DB
	path string
}

// NewStore opens (creating it if needed) the jobs database at path and brings
// its schema up to date.
func NewStore(path string) (Store, error) {
	if path == "" {
		return nil, errors.New("jobs: store path is empty")
	}
	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o2770); err != nil {
			return nil, fmt.Errorf("jobs: creating %s: %w", dir, err)
		}
	}
	// _txlock=immediate: every sql.Tx begins with BEGIN IMMEDIATE.
	dsn := "file:" + path +
		"?_pragma=journal_mode(WAL)" +
		"&_pragma=busy_timeout(10000)" +
		"&_pragma=synchronous(NORMAL)" +
		"&_pragma=foreign_keys(1)" +
		"&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("jobs: opening %s: %w", path, err)
	}
	// Each connection carries its own pragmas; a small pool keeps the number
	// of writers contending inside this process down without serializing
	// reads behind a slow write.
	db.SetMaxOpenConns(8)
	db.SetMaxIdleConns(4)
	db.SetConnMaxLifetime(time.Hour)
	s := &store{db: db, path: path}
	if err := s.migrate(context.Background()); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

// migrate brings the schema up to date. It is safe for two PROCESSES to run
// it at once on the same file — which happens whenever a `--direct` CLI opens
// jobs.db at the same moment as the daemon: each step re-reads the version
// INSIDE its own IMMEDIATE transaction, so the loser of the race sees the
// winner's committed version and skips the work rather than failing on the
// migrations table's primary key.
func (s *store) migrate(ctx context.Context) error {
	if _, err := s.db.ExecContext(ctx,
		`CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`); err != nil {
		return fmt.Errorf("jobs: creating the migrations table: %w", err)
	}
	for {
		applied, err := s.migrateOne(ctx)
		if err != nil {
			return err
		}
		if !applied {
			return nil
		}
	}
}

// migrateOne applies the next pending migration, reporting whether it did.
func (s *store) migrateOne(ctx context.Context) (bool, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return false, fmt.Errorf("jobs: migrating: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var at sql.NullInt64
	if err := tx.QueryRowContext(ctx, `SELECT MAX(version) FROM migrations`).Scan(&at); err != nil {
		return false, fmt.Errorf("jobs: reading the schema version: %w", err)
	}
	i := int(at.Int64)
	if i >= len(migrations) {
		return false, nil
	}
	if _, err := tx.ExecContext(ctx, migrations[i]); err != nil {
		return false, fmt.Errorf("jobs: migration %d: %w", i+1, err)
	}
	if _, err := tx.ExecContext(ctx, `INSERT INTO migrations (version, applied_at) VALUES (?, ?)`,
		i+1, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		return false, fmt.Errorf("jobs: recording migration %d: %w", i+1, err)
	}
	if err := tx.Commit(); err != nil {
		return false, fmt.Errorf("jobs: committing migration %d: %w", i+1, err)
	}
	return true, nil
}

func (s *store) Close() error { return s.db.Close() }

// Path is the database file, for diagnostics and for Backup's caller.
func (s *store) Path() string { return s.path }

// ---------------------------------------------------------------- jobs

// Create persists the job and claims the idempotency key in ONE transaction.
// Either the key and the job land together or neither does: a key that named
// a job which was never written would make the retry of a crashed submission
// answer with a job id that does not exist.
func (s *store) Create(ctx context.Context, job *model.Job, fingerprint string) (*model.Job, error) {
	if job == nil {
		return nil, errors.New("jobs: Create with a nil job")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback() }()

	if job.IdempotencyKey != "" {
		var existingID, existingFP string
		err := tx.QueryRowContext(ctx,
			`SELECT job_id, fingerprint FROM idempotency WHERE key = ?`, job.IdempotencyKey).
			Scan(&existingID, &existingFP)
		switch {
		case err == nil && existingFP == fingerprint:
			// The same request again: hand back the job it made, in whatever
			// state it has reached. This is the retry of a client that lost
			// the 202, not a new mutation.
			existing, err := getJobTx(ctx, tx, existingID)
			if err != nil {
				return nil, err
			}
			if err := tx.Commit(); err != nil {
				return nil, err
			}
			return existing, nil
		case err == nil:
			return nil, refuse(fmt.Errorf("%w: idempotency key %q was used for a different request (job %s)",
				ErrDuplicate, job.IdempotencyKey, existingID),
				map[string]any{"job_id": existingID})
		case !errors.Is(err, sql.ErrNoRows):
			return nil, err
		}
	}

	doc, err := json.Marshal(job)
	if err != nil {
		return nil, err
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO jobs (id, op, tenant, state, created_at, idempotency_key, doc) VALUES (?,?,?,?,?,?,?)`,
		job.ID, job.Op, nullStr(string(job.Tenant)), string(job.State), job.CreatedAt, job.IdempotencyKey, string(doc)); err != nil {
		return nil, fmt.Errorf("jobs: inserting %s: %w", job.ID, err)
	}
	if job.IdempotencyKey != "" {
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO idempotency (key, job_id, fingerprint, created_at) VALUES (?,?,?,?)`,
			job.IdempotencyKey, job.ID, fingerprint, job.CreatedAt); err != nil {
			return nil, fmt.Errorf("jobs: claiming idempotency key: %w", err)
		}
	}
	for _, r := range job.Reservations {
		if _, err := tx.ExecContext(ctx,
			`INSERT OR REPLACE INTO reservations (job_id, resource, until, created_at) VALUES (?,?,?,?)`,
			job.ID, r.Resource, nullStr(string(r.Until)), job.CreatedAt); err != nil {
			return nil, err
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return cloneJob(job), nil
}

func getJobTx(ctx context.Context, tx *sql.Tx, id string) (*model.Job, error) {
	var doc string
	if err := tx.QueryRowContext(ctx, `SELECT doc FROM jobs WHERE id = ?`, id).Scan(&doc); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, fmt.Errorf("%w: job %s", ErrNotFound, id)
		}
		return nil, err
	}
	return decodeJob(doc)
}

func (s *store) Get(ctx context.Context, id string) (*model.Job, error) {
	var doc string
	if err := s.db.QueryRowContext(ctx, `SELECT doc FROM jobs WHERE id = ?`, id).Scan(&doc); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, fmt.Errorf("%w: job %s", ErrNotFound, id)
		}
		return nil, err
	}
	return decodeJob(doc)
}

func (s *store) List(ctx context.Context, f ListFilter) ([]model.Job, bool, error) {
	limit := f.Limit
	if limit <= 0 {
		limit = 50
	}
	if limit > 1000 {
		limit = 1000
	}
	q := `SELECT doc FROM jobs WHERE 1=1`
	var args []any
	if f.Tenant != "" {
		q += ` AND tenant = ?`
		args = append(args, f.Tenant)
	}
	if f.State != "" {
		q += ` AND state = ?`
		args = append(args, string(f.State))
	}
	// id is a ULID, so it orders by creation time; ties are impossible.
	q += ` ORDER BY id DESC LIMIT ?`
	args = append(args, limit+1)

	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, false, err
	}
	defer func() { _ = rows.Close() }()
	out := []model.Job{}
	for rows.Next() {
		var doc string
		if err := rows.Scan(&doc); err != nil {
			return nil, false, err
		}
		j, err := decodeJob(doc)
		if err != nil {
			return nil, false, err
		}
		out = append(out, *j)
	}
	if err := rows.Err(); err != nil {
		return nil, false, err
	}
	truncated := len(out) > limit
	if truncated {
		out = out[:limit]
	}
	return out, truncated, nil
}

// Update replaces the whole document. The engine calls it at every
// transition, and every transition is a fact the next process to open this
// file has to see — a crash between two of them must leave a state reconcile
// can act on, never a half-written one.
func (s *store) Update(ctx context.Context, job *model.Job) error {
	if job == nil {
		return errors.New("jobs: Update with a nil job")
	}
	doc, err := json.Marshal(job)
	if err != nil {
		return err
	}
	res, err := s.db.ExecContext(ctx,
		`UPDATE jobs SET op=?, tenant=?, state=?, created_at=?, idempotency_key=?, doc=? WHERE id=?`,
		job.Op, nullStr(string(job.Tenant)), string(job.State), job.CreatedAt, job.IdempotencyKey, string(doc), job.ID)
	if err != nil {
		return fmt.Errorf("jobs: updating %s: %w", job.ID, err)
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("%w: job %s", ErrNotFound, job.ID)
	}
	return nil
}

func (s *store) Running(ctx context.Context) ([]model.Job, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT doc FROM jobs WHERE state IN (?, ?) ORDER BY id`,
		string(model.JobRunning), string(model.JobAwaitingCutover))
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []model.Job
	for rows.Next() {
		var doc string
		if err := rows.Scan(&doc); err != nil {
			return nil, err
		}
		j, err := decodeJob(doc)
		if err != nil {
			return nil, err
		}
		out = append(out, *j)
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------- step logs

// AppendStepLog is append-only by construction: there is no update or delete
// on this table. The text is already redacted — the engine passes every line
// through the Redactor before it gets here, and this layer does not check,
// because a layer that "also redacts" is a layer the caller stops trusting.
func (s *store) AppendStepLog(ctx context.Context, id string, n int, text string) error {
	if text == "" {
		return nil
	}
	if !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO step_logs (job_id, n, at, text) VALUES (?,?,?,?)`,
		id, n, time.Now().UTC().Format(time.RFC3339Nano), text)
	return err
}

func (s *store) StepLog(ctx context.Context, id string, n int) (string, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT text FROM step_logs WHERE job_id = ? AND n = ? ORDER BY seq`, id, n)
	if err != nil {
		return "", err
	}
	defer func() { _ = rows.Close() }()
	var b strings.Builder
	for rows.Next() {
		var t string
		if err := rows.Scan(&t); err != nil {
			return "", err
		}
		b.WriteString(t)
	}
	return b.String(), rows.Err()
}

// ---------------------------------------------------------------- args

func (s *store) PutArgs(ctx context.Context, jobID string, argsRedacted map[string]any) error {
	b, err := json.Marshal(nonNilArgs(argsRedacted))
	if err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx,
		`INSERT OR REPLACE INTO job_args (job_id, args) VALUES (?, ?)`, jobID, string(b))
	return err
}

func (s *store) Args(ctx context.Context, jobID string) (map[string]any, error) {
	var raw string
	if err := s.db.QueryRowContext(ctx, `SELECT args FROM job_args WHERE job_id = ?`, jobID).Scan(&raw); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return map[string]any{}, nil
		}
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		return nil, err
	}
	return nonNilArgs(out), nil
}

// ---------------------------------------------------------------- workers

func (s *store) PutWorker(ctx context.Context, jobID string, w WorkerIdentity) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT OR REPLACE INTO job_workers (job_id, pid, host, start_time, mode, recorded_at) VALUES (?,?,?,?,?,?)`,
		jobID, w.PID, w.Host, int64(w.StartTime), string(w.Mode), time.Now().UTC().Format(time.RFC3339))
	if err != nil {
		return fmt.Errorf("jobs: recording the worker of %s: %w", jobID, err)
	}
	return nil
}

func (s *store) Worker(ctx context.Context, jobID string) (*WorkerIdentity, error) {
	var (
		pid     int
		host    string
		started int64
		mode    string
	)
	err := s.db.QueryRowContext(ctx,
		`SELECT pid, host, start_time, mode FROM job_workers WHERE job_id = ?`, jobID).
		Scan(&pid, &host, &started, &mode)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &WorkerIdentity{PID: pid, Host: host, StartTime: uint64(started), Mode: model.WorkerMode(mode)}, nil
}

// ---------------------------------------------------------------- reservations

func (s *store) PutReservation(ctx context.Context, jobID string, r model.Reservation, now time.Time) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT OR REPLACE INTO reservations (job_id, resource, until, created_at) VALUES (?,?,?,?)`,
		jobID, r.Resource, nullStr(string(r.Until)), now.UTC().Format(time.RFC3339))
	return err
}

func (s *store) Reservations(ctx context.Context, jobID string) ([]model.Reservation, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT resource, until FROM reservations WHERE job_id = ? ORDER BY resource`, jobID)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	out := []model.Reservation{}
	for rows.Next() {
		var res string
		var until sql.NullString
		if err := rows.Scan(&res, &until); err != nil {
			return nil, err
		}
		out = append(out, model.Reservation{Resource: res, Until: model.NullString(until.String)})
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------- audit

func (s *store) Audit(ctx context.Context, row model.AuditRow) error {
	args, err := json.Marshal(nonNilArgs(row.ArgsRedacted))
	if err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx,
		`INSERT INTO audit (at, phase, principal, auth_method, sudo_user, request_id, op, tenant,
		                    job_id, args_redacted, plan_hash, outcome, error, duration_ms)
		 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		row.At, string(row.Phase), row.Principal, string(row.AuthMethod), nullStr(string(row.SudoUser)),
		row.RequestID, row.Op, nullStr(string(row.Tenant)), nullStr(string(row.JobID)), string(args),
		nullStr(string(row.PlanHash)), row.Outcome, nullStr(string(row.Error)), row.DurationMS)
	if err != nil {
		return fmt.Errorf("jobs: writing the audit row: %w", err)
	}
	return nil
}

func (s *store) AuditList(ctx context.Context, limit int) ([]model.AuditRow, bool, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > 1000 {
		limit = 1000
	}
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, at, phase, principal, auth_method, sudo_user, request_id, op, tenant,
		        job_id, args_redacted, plan_hash, outcome, error, duration_ms
		 FROM audit ORDER BY id DESC LIMIT ?`, limit+1)
	if err != nil {
		return nil, false, err
	}
	defer func() { _ = rows.Close() }()
	out := []model.AuditRow{}
	for rows.Next() {
		var r model.AuditRow
		var sudo, tenant, jobID, planHash, errText sql.NullString
		var args string
		var dur sql.NullInt64
		var phase, method string
		if err := rows.Scan(&r.ID, &r.At, &phase, &r.Principal, &method, &sudo, &r.RequestID, &r.Op,
			&tenant, &jobID, &args, &planHash, &r.Outcome, &errText, &dur); err != nil {
			return nil, false, err
		}
		r.Phase = model.AuditPhase(phase)
		r.AuthMethod = model.AuthMethod(method)
		r.SudoUser = model.NullString(sudo.String)
		r.Tenant = model.NullString(tenant.String)
		r.JobID = model.NullString(jobID.String)
		r.PlanHash = model.NullString(planHash.String)
		r.Error = model.NullString(errText.String)
		if dur.Valid {
			v := dur.Int64
			r.DurationMS = &v
		}
		if err := json.Unmarshal([]byte(args), &r.ArgsRedacted); err != nil {
			return nil, false, err
		}
		r.ArgsRedacted = nonNilArgs(r.ArgsRedacted)
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, false, err
	}
	truncated := len(out) > limit
	if truncated {
		out = out[:limit]
	}
	return out, truncated, nil
}

// ---------------------------------------------------------------- envelopes

func (s *store) PutEnvelope(ctx context.Context, e Envelope) error {
	sealed := 0
	if e.Sealed {
		sealed = 1
	}
	_, err := s.db.ExecContext(ctx,
		`INSERT OR REPLACE INTO envelopes (job_id, principal, op, created_at, expires_at, sealed, payload, delivered_at)
		 VALUES (?,?,?,?,?,?,?,NULL)`,
		e.JobID, e.Principal, e.Op,
		e.CreatedAt.UTC().Format(time.RFC3339), e.ExpiresAt.UTC().Format(time.RFC3339),
		sealed, e.Payload)
	return err
}

// TakeEnvelope is the once-only delivery. The row is kept as a TOMBSTONE
// after delivery (payload cleared, delivered_at set) rather than deleted, so
// a second read can be answered "delivered, at <time>" — 410 with a reason —
// instead of 404, which would read like "you asked for the wrong job".
func (s *store) TakeEnvelope(ctx context.Context, jobID, principal string, now time.Time) (*Envelope, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback() }()

	var e Envelope
	var createdAt, expiresAt string
	var delivered sql.NullString
	var sealed int
	err = tx.QueryRowContext(ctx,
		`SELECT job_id, principal, op, created_at, expires_at, sealed, payload, delivered_at
		 FROM envelopes WHERE job_id = ?`, jobID).
		Scan(&e.JobID, &e.Principal, &e.Op, &createdAt, &expiresAt, &sealed, &e.Payload, &delivered)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, fmt.Errorf("%w: job %s minted no secrets", ErrNotFound, jobID)
	}
	if err != nil {
		return nil, err
	}
	if e.Principal != principal {
		return nil, fmt.Errorf("%w: the envelope of job %s belongs to another operator", ErrForbidden, jobID)
	}
	if delivered.Valid && delivered.String != "" {
		return nil, fmt.Errorf("%w: the secrets of job %s were delivered at %s; the remedy is a new, audited mint",
			ErrGone, jobID, delivered.String)
	}
	e.Sealed = sealed == 1
	e.CreatedAt, _ = time.Parse(time.RFC3339, createdAt)
	e.ExpiresAt, _ = time.Parse(time.RFC3339, expiresAt)
	if now.After(e.ExpiresAt) {
		if _, err := tx.ExecContext(ctx,
			`UPDATE envelopes SET payload = NULL, delivered_at = ? WHERE job_id = ?`,
			e.ExpiresAt.UTC().Format(time.RFC3339), jobID); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return nil, fmt.Errorf("%w: the secrets of job %s expired at %s; the remedy is a new, audited mint",
			ErrGone, jobID, e.ExpiresAt.UTC().Format(time.RFC3339))
	}
	if _, err := tx.ExecContext(ctx,
		`UPDATE envelopes SET payload = NULL, delivered_at = ? WHERE job_id = ?`,
		now.UTC().Format(time.RFC3339), jobID); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return &e, nil
}

// ---------------------------------------------------------------- backup

// Backup writes a consistent copy with VACUUM INTO — SQLite's own
// point-in-time copy, which is safe while other connections are writing (a
// plain `cp` of a WAL database is not).
func (s *store) Backup(ctx context.Context, dest string) error {
	if dest == "" {
		return errors.New("jobs: Backup with an empty destination")
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0o2770); err != nil {
		return err
	}
	if _, err := os.Stat(dest); err == nil {
		return fmt.Errorf("jobs: %s already exists (VACUUM INTO refuses to overwrite)", dest)
	}
	if _, err := s.db.ExecContext(ctx, `VACUUM INTO ?`, dest); err != nil {
		return fmt.Errorf("jobs: backing up to %s: %w", dest, err)
	}
	return nil
}

// ---------------------------------------------------------------- helpers

func decodeJob(doc string) (*model.Job, error) {
	var j model.Job
	if err := json.Unmarshal([]byte(doc), &j); err != nil {
		return nil, fmt.Errorf("jobs: decoding the job document: %w", err)
	}
	normalizeJob(&j)
	return &j, nil
}

func cloneJob(j *model.Job) *model.Job {
	b, err := json.Marshal(j)
	if err != nil {
		return j
	}
	var out model.Job
	if err := json.Unmarshal(b, &out); err != nil {
		return j
	}
	normalizeJob(&out)
	return &out
}

// normalizeJob makes the nullable-vs-empty distinction the schemas draw: the
// array fields are `type: array`, never null, so a decoded nil becomes [].
func normalizeJob(j *model.Job) {
	if j.Reservations == nil {
		j.Reservations = []model.Reservation{}
	}
	if j.Steps == nil {
		j.Steps = []model.Step{}
	}
	for i := range j.Steps {
		if j.Steps[i].ExternalIDs == nil {
			j.Steps[i].ExternalIDs = []string{}
		}
	}
}

func nonNilArgs(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	return m
}

// nullStr maps "" to SQL NULL, which is what every nullable column in the
// contract means by the empty NullString.
func nullStr(s string) any {
	if s == "" {
		return nil
	}
	return s
}
