package grading

import (
	"context"
	"errors"
	"sort"
	"sync"
)

// ErrNotFound is returned by nothing here on purpose: a missing batch, task,
// verdict or adjudication comes back as a nil record with a nil error, because
// every caller in the handler layer turns "absent" into the contract's 404 and
// must not be able to tell a store outage from an absence by accident. A real
// store FAILURE is a non-nil error, which the handlers turn into the 503.
var ErrNotFound = errors.New("grading: not found")

// Store is the persistence seam for the grading resources.
//
// Four collections — batches, tasks, verdicts, adjudications. Verdict and
// adjudication rows are APPEND-ONLY BY VERSION: a re-save adds a row rather
// than overwriting one, so the pre-adjudication κ (the number the study
// reports) survives a reader changing their mind. Reads return the current
// (highest-version) row.
//
// Every method takes a context and returns an error so a durable backend can be
// added behind it without touching the handlers. The Go side is a Phase-1
// scaffold with no database layer of any kind (no `database/sql`, no driver in
// go.mod, no registry or job store to share one with), so MemoryStore is the
// only implementation here; the Python side is authoritative for durable
// grading state.
type Store interface {
	// ListBatches returns every batch, newest first. Unpaginated — a
	// deployment runs a handful of reads. The caller filters to what the
	// principal may see.
	ListBatches(ctx context.Context) ([]*Batch, error)
	// CreateBatch stores a batch and all of its tasks atomically: a batch is
	// created whole or not at all. A reader handed half a draw would produce a
	// κ over a sample nobody recorded.
	CreateBatch(ctx context.Context, batch *Batch, tasks []*Task) error
	// GetBatch returns the batch, or nil when there is none.
	GetBatch(ctx context.Context, batchID string) (*Batch, error)
	// DeleteBatch hard-deletes a batch, its tasks, every verdict version and
	// every adjudication. Reports whether a batch was removed.
	DeleteBatch(ctx context.Context, batchID string) (bool, error)
	// BeginAdjudication moves `open` → `adjudicating`, stamping at. It is a
	// CONDITIONAL transition and reports whether it took: two concurrent
	// clicks cannot both freeze a read, and the loser gets the contract's 409.
	BeginAdjudication(ctx context.Context, batchID, at string) (bool, error)

	// ListTasks returns the batch's tasks in BATCH order.
	ListTasks(ctx context.Context, batchID string) ([]*Task, error)
	// GetTask returns the task, or nil when there is none.
	GetTask(ctx context.Context, taskID string) (*Task, error)

	// ListVerdicts returns the CURRENT row for every (task, reader) of the
	// batch.
	ListVerdicts(ctx context.Context, batchID string) ([]*Verdict, error)
	// GetVerdict returns the current row for one (task, reader), or nil.
	GetVerdict(ctx context.Context, taskID, reader string) (*Verdict, error)
	// PutVerdict appends a version and returns the stored row with its
	// assigned Version — the store owns that sequence, never the caller.
	PutVerdict(ctx context.Context, row *Verdict) (*Verdict, error)

	// ListAdjudications returns the current joint-read row for every task of
	// the batch that has one.
	ListAdjudications(ctx context.Context, batchID string) ([]*Adjudication, error)
	// GetAdjudication returns the current joint-read row for one task, or nil.
	GetAdjudication(ctx context.Context, taskID string) (*Adjudication, error)
	// PutAdjudication appends a version and returns the stored row.
	PutAdjudication(ctx context.Context, row *Adjudication) (*Adjudication, error)
}

// MemoryStore is the in-process Store: everything lives in maps under one
// mutex and dies with the process.
//
// Records are copied in and out. A handler that mutated a returned *Task would
// otherwise be editing the store's own copy — on this surface that is a
// reader's document changing under them mid-read, which no test would catch
// because the store would agree with the response.
type MemoryStore struct {
	mu       sync.RWMutex
	batches  map[string]*Batch
	tasks    map[string]*Task
	verdicts []*Verdict     // append-only, every version
	adjs     []*Adjudication // append-only, every version
}

// NewMemoryStore returns an empty in-memory grading store.
func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		batches: make(map[string]*Batch),
		tasks:   make(map[string]*Task),
	}
}

var _ Store = (*MemoryStore)(nil)

func cloneBatch(b *Batch) *Batch {
	if b == nil {
		return nil
	}
	out := *b
	out.Readers = append([]string(nil), b.Readers...)
	return &out
}

func cloneTask(t *Task) *Task {
	if t == nil {
		return nil
	}
	out := *t
	out.Readers = append([]string(nil), t.Readers...)
	out.Document = cloneDocument(t.Document)
	out.Claims = cloneClaims(t.Claims)
	out.ExtraQuestions = append([]ExtraQuestion(nil), t.ExtraQuestions...)
	return &out
}

func cloneDocument(d Document) Document {
	out := d
	out.Units = make([]Unit, len(d.Units))
	for i, u := range d.Units {
		cu := u
		cu.Sentences = append([]Sentence(nil), u.Sentences...)
		out.Units[i] = cu
	}
	return out
}

func cloneClaims(claims []EvidenceSet) []EvidenceSet {
	out := make([]EvidenceSet, len(claims))
	for i, c := range claims {
		cc := c
		cc.Spans = append([]Span(nil), c.Spans...)
		cc.Sources = append([]string(nil), c.Sources...)
		out[i] = cc
	}
	return out
}

func cloneVerdict(v *Verdict) *Verdict {
	if v == nil {
		return nil
	}
	out := *v
	out.SpanJudgements = append([]SpanJudgement(nil), v.SpanJudgements...)
	out.ExtraAnswers = append([]ExtraAnswer(nil), v.ExtraAnswers...)
	return &out
}

func cloneAdjudication(a *Adjudication) *Adjudication {
	if a == nil {
		return nil
	}
	out := *a
	out.SpanJudgements = append([]SpanJudgement(nil), a.SpanJudgements...)
	return &out
}

// ListBatches returns every batch newest first, ordered by (created_at, id)
// descending — the same total order the Python memory store uses, so a listing
// is stable across implementations even when two batches share a timestamp
// (created_at has second resolution, so ties are real).
func (s *MemoryStore) ListBatches(_ context.Context) ([]*Batch, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Batch, 0, len(s.batches))
	for _, b := range s.batches {
		out = append(out, cloneBatch(b))
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].CreatedAt != out[j].CreatedAt {
			return out[i].CreatedAt > out[j].CreatedAt
		}
		return out[i].ID > out[j].ID
	})
	return out, nil
}

// CreateBatch stores the batch and its tasks under one lock.
func (s *MemoryStore) CreateBatch(_ context.Context, batch *Batch, tasks []*Task) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.batches[batch.ID] = cloneBatch(batch)
	for _, t := range tasks {
		s.tasks[t.ID] = cloneTask(t)
	}
	return nil
}

// GetBatch returns the batch, or nil when there is none.
func (s *MemoryStore) GetBatch(_ context.Context, batchID string) (*Batch, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneBatch(s.batches[batchID]), nil
}

// DeleteBatch removes the batch and everything under it.
func (s *MemoryStore) DeleteBatch(_ context.Context, batchID string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.batches[batchID]; !ok {
		return false, nil
	}
	delete(s.batches, batchID)
	for id, t := range s.tasks {
		if t.BatchID == batchID {
			delete(s.tasks, id)
		}
	}
	verdicts := s.verdicts[:0]
	for _, v := range s.verdicts {
		if v.BatchID != batchID {
			verdicts = append(verdicts, v)
		}
	}
	s.verdicts = verdicts
	adjs := s.adjs[:0]
	for _, a := range s.adjs {
		if a.BatchID != batchID {
			adjs = append(adjs, a)
		}
	}
	s.adjs = adjs
	return true, nil
}

// BeginAdjudication is the conditional `open` → `adjudicating` transition.
func (s *MemoryStore) BeginAdjudication(_ context.Context, batchID, at string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	b, ok := s.batches[batchID]
	if !ok || b.Status != StatusOpen {
		return false, nil
	}
	b.Status = StatusAdjudicating
	b.AdjudicatingAt = at
	return true, nil
}

// ListTasks returns the batch's tasks in batch order.
func (s *MemoryStore) ListTasks(_ context.Context, batchID string) ([]*Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Task, 0)
	for _, t := range s.tasks {
		if t.BatchID == batchID {
			out = append(out, cloneTask(t))
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Position < out[j].Position })
	return out, nil
}

// GetTask returns the task, or nil when there is none.
func (s *MemoryStore) GetTask(_ context.Context, taskID string) (*Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneTask(s.tasks[taskID]), nil
}

// currentVerdicts collapses the append-only log to the current row per
// (task, reader). Caller holds the lock.
func (s *MemoryStore) currentVerdicts(match func(*Verdict) bool) map[[2]string]*Verdict {
	cur := make(map[[2]string]*Verdict)
	for _, v := range s.verdicts {
		if !match(v) {
			continue
		}
		k := [2]string{v.TaskID, v.Reader}
		if prev, ok := cur[k]; !ok || v.Version > prev.Version {
			cur[k] = v
		}
	}
	return cur
}

// ListVerdicts returns the current row for every (task, reader) of the batch.
func (s *MemoryStore) ListVerdicts(_ context.Context, batchID string) ([]*Verdict, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	cur := s.currentVerdicts(func(v *Verdict) bool { return v.BatchID == batchID })
	out := make([]*Verdict, 0, len(cur))
	for _, v := range cur {
		out = append(out, cloneVerdict(v))
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].TaskID != out[j].TaskID {
			return out[i].TaskID < out[j].TaskID
		}
		return out[i].Reader < out[j].Reader
	})
	return out, nil
}

// GetVerdict returns the current row for one (task, reader), or nil.
func (s *MemoryStore) GetVerdict(_ context.Context, taskID, reader string) (*Verdict, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var best *Verdict
	for _, v := range s.verdicts {
		if v.TaskID == taskID && v.Reader == reader && (best == nil || v.Version > best.Version) {
			best = v
		}
	}
	return cloneVerdict(best), nil
}

// PutVerdict appends a new version and returns the stored row.
func (s *MemoryStore) PutVerdict(_ context.Context, row *Verdict) (*Verdict, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	next := 1
	for _, v := range s.verdicts {
		if v.TaskID == row.TaskID && v.Reader == row.Reader && v.Version >= next {
			next = v.Version + 1
		}
	}
	stored := cloneVerdict(row)
	stored.Version = next
	s.verdicts = append(s.verdicts, stored)
	return cloneVerdict(stored), nil
}

// ListAdjudications returns the current joint-read row per task of the batch.
func (s *MemoryStore) ListAdjudications(_ context.Context, batchID string) ([]*Adjudication, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	cur := make(map[string]*Adjudication)
	for _, a := range s.adjs {
		if a.BatchID != batchID {
			continue
		}
		if prev, ok := cur[a.TaskID]; !ok || a.Version > prev.Version {
			cur[a.TaskID] = a
		}
	}
	out := make([]*Adjudication, 0, len(cur))
	for _, a := range cur {
		out = append(out, cloneAdjudication(a))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].TaskID < out[j].TaskID })
	return out, nil
}

// GetAdjudication returns the current joint-read row for one task, or nil.
func (s *MemoryStore) GetAdjudication(_ context.Context, taskID string) (*Adjudication, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var best *Adjudication
	for _, a := range s.adjs {
		if a.TaskID == taskID && (best == nil || a.Version > best.Version) {
			best = a
		}
	}
	return cloneAdjudication(best), nil
}

// PutAdjudication appends a new version and returns the stored row.
func (s *MemoryStore) PutAdjudication(_ context.Context, row *Adjudication) (*Adjudication, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	next := 1
	for _, a := range s.adjs {
		if a.TaskID == row.TaskID && a.Version >= next {
			next = a.Version + 1
		}
	}
	stored := cloneAdjudication(row)
	stored.Version = next
	s.adjs = append(s.adjs, stored)
	return cloneAdjudication(stored), nil
}
