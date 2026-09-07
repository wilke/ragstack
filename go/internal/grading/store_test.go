package grading

import (
	"context"
	"testing"
)

func seed(t *testing.T) (*MemoryStore, *Batch, []*Task) {
	t.Helper()
	s := NewMemoryStore()
	b := &Batch{
		ID: "b1", Name: "read", Kind: "evidence-read", Status: StatusOpen,
		RubricSHA256: "00", OrderSeed: 1, Readers: []string{"a", "b"},
		TaskCount: 2, CreatedAt: "2026-01-01T00:00:00+00:00", CreatedBy: "admin",
	}
	tasks := []*Task{
		{ID: "t1", BatchID: "b1", Position: 0, PairID: "p1"},
		{ID: "t2", BatchID: "b1", Position: 1, PairID: "p2"},
	}
	if err := s.CreateBatch(context.Background(), b, tasks); err != nil {
		t.Fatalf("create: %v", err)
	}
	return s, b, tasks
}

// A re-save must APPEND a version rather than overwrite one: the
// pre-adjudication κ is the number the study reports, and it needs the row a
// reader saved before they changed their mind.
func TestPutVerdictAppendsVersions(t *testing.T) {
	ctx := context.Background()
	s, _, _ := seed(t)
	for want := 1; want <= 3; want++ {
		row, err := s.PutVerdict(ctx, &Verdict{TaskID: "t1", BatchID: "b1", Reader: "a", Verdict: "correct"})
		if err != nil {
			t.Fatalf("put: %v", err)
		}
		if row.Version != want {
			t.Fatalf("version %d, want %d", row.Version, want)
		}
	}
	// The store owns the sequence: a caller-supplied Version is ignored.
	row, _ := s.PutVerdict(ctx, &Verdict{TaskID: "t1", BatchID: "b1", Reader: "a", Version: 99})
	if row.Version != 4 {
		t.Fatalf("the store owns the version sequence, got %d", row.Version)
	}
	// A read returns the current row, and one reader's versions do not
	// influence another's.
	cur, _ := s.GetVerdict(ctx, "t1", "a")
	if cur.Version != 4 {
		t.Fatalf("current row is version %d", cur.Version)
	}
	other, _ := s.PutVerdict(ctx, &Verdict{TaskID: "t1", BatchID: "b1", Reader: "b"})
	if other.Version != 1 {
		t.Fatalf("reader b's first save is version %d", other.Version)
	}
	rows, _ := s.ListVerdicts(ctx, "b1")
	if len(rows) != 2 {
		t.Fatalf("ListVerdicts must collapse the log to one CURRENT row per (task, reader): %d", len(rows))
	}
}

// Two clicks must not both freeze a read: the transition is conditional, and
// the loser is told so (the handler's 409).
func TestBeginAdjudicationIsConditional(t *testing.T) {
	ctx := context.Background()
	s, _, _ := seed(t)
	ok, err := s.BeginAdjudication(ctx, "b1", "2026-01-02T00:00:00+00:00")
	if err != nil || !ok {
		t.Fatalf("first transition: ok=%v err=%v", ok, err)
	}
	ok, _ = s.BeginAdjudication(ctx, "b1", "2026-01-03T00:00:00+00:00")
	if ok {
		t.Fatalf("a second adjudicate must not take")
	}
	b, _ := s.GetBatch(ctx, "b1")
	if b.Status != StatusAdjudicating || b.AdjudicatingAt != "2026-01-02T00:00:00+00:00" {
		t.Fatalf("the losing call must not restamp the batch: %+v", b)
	}
	ok, _ = s.BeginAdjudication(ctx, "nope", "x")
	if ok {
		t.Fatalf("an unknown batch cannot transition")
	}
}

func TestDeleteBatchRemovesEverythingUnderIt(t *testing.T) {
	ctx := context.Background()
	s, _, _ := seed(t)
	if _, err := s.PutVerdict(ctx, &Verdict{TaskID: "t1", BatchID: "b1", Reader: "a"}); err != nil {
		t.Fatalf("put verdict: %v", err)
	}
	if _, err := s.PutAdjudication(ctx, &Adjudication{TaskID: "t1", BatchID: "b1"}); err != nil {
		t.Fatalf("put adjudication: %v", err)
	}
	removed, err := s.DeleteBatch(ctx, "b1")
	if err != nil || !removed {
		t.Fatalf("delete: removed=%v err=%v", removed, err)
	}
	if b, _ := s.GetBatch(ctx, "b1"); b != nil {
		t.Errorf("batch survived")
	}
	if task, _ := s.GetTask(ctx, "t1"); task != nil {
		t.Errorf("task survived")
	}
	if rows, _ := s.ListVerdicts(ctx, "b1"); len(rows) != 0 {
		t.Errorf("verdict rows survived: %d", len(rows))
	}
	if rows, _ := s.ListAdjudications(ctx, "b1"); len(rows) != 0 {
		t.Errorf("adjudication rows survived: %d", len(rows))
	}
	if removed, _ := s.DeleteBatch(ctx, "b1"); removed {
		t.Errorf("deleting twice must report false the second time")
	}
}

// An absent record is (nil, nil) — never an error. The handler turns absence
// into the contract's 404 and a store FAILURE into its 503; conflating them
// would make an outage look like "no such batch", which on this surface reads
// as a read that does not exist.
func TestAbsenceIsNotAnError(t *testing.T) {
	ctx := context.Background()
	s := NewMemoryStore()
	if b, err := s.GetBatch(ctx, "nope"); b != nil || err != nil {
		t.Errorf("GetBatch: %v %v", b, err)
	}
	if task, err := s.GetTask(ctx, "nope"); task != nil || err != nil {
		t.Errorf("GetTask: %v %v", task, err)
	}
	if v, err := s.GetVerdict(ctx, "nope", "a"); v != nil || err != nil {
		t.Errorf("GetVerdict: %v %v", v, err)
	}
	if a, err := s.GetAdjudication(ctx, "nope"); a != nil || err != nil {
		t.Errorf("GetAdjudication: %v %v", a, err)
	}
}

// The store hands out copies. A handler that mutated a returned record would
// otherwise be editing the store's own copy — a reader's document changing
// under them mid-read, which no test would catch because the store would agree
// with the response.
func TestRecordsAreCopiedOut(t *testing.T) {
	ctx := context.Background()
	s, _, _ := seed(t)
	b, _ := s.GetBatch(ctx, "b1")
	b.Readers[0] = "tampered"
	b.Status = "closed"
	again, _ := s.GetBatch(ctx, "b1")
	if again.Readers[0] != "a" || again.Status != StatusOpen {
		t.Fatalf("the store handed out its own record: %+v", again)
	}
}

func TestListTasksIsBatchOrderAndListBatchesIsNewestFirst(t *testing.T) {
	ctx := context.Background()
	s, _, _ := seed(t)
	tasks, _ := s.ListTasks(ctx, "b1")
	if len(tasks) != 2 || tasks[0].ID != "t1" || tasks[1].ID != "t2" {
		t.Fatalf("tasks must come back in batch order: %+v", tasks)
	}
	if err := s.CreateBatch(ctx, &Batch{ID: "b2", CreatedAt: "2026-02-01T00:00:00+00:00"}, nil); err != nil {
		t.Fatalf("create: %v", err)
	}
	batches, _ := s.ListBatches(ctx)
	if len(batches) != 2 || batches[0].ID != "b2" {
		t.Fatalf("batches must come back newest first: %+v", batches)
	}
}
