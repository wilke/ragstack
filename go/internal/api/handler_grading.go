package api

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"regexp"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/ragstack/ragstack/internal/auth"
	"github.com/ragstack/ragstack/internal/grading"
)

// `/v1/grading` — the study's evidence read, with independence enforced here.
//
// `contracts/openapi.yaml` (tag Grading) and `contracts/schemas/grading_*.json`
// are authoritative for every shape; `python/ragstack/api/routers/grading.py` is
// matched wherever the contract is silent, so the two servers cannot disagree
// about a read that is half-done on one of them.
//
// THE FOUR RULES THIS FILE EXISTS FOR
//
//  1. A reader reads and writes only their own verdict row. `PUT …/verdict`
//     has no field naming a reader — the row is keyed by the authenticated
//     subject — and `GET …/verdicts/{reader}` answers 404 for anyone else's,
//     the same 404 as naming a subject who is not a reader at all.
//  2. An admin is not exempt while the read is open. `reader_verdicts` and
//     `adjudication` appear only for an admin AND only once the batch has left
//     `open`; before that an admin reading a reader's row gets the same 404 and
//     the export is a 409. `POST …/adjudicate` is the moment independence ends,
//     recorded with a timestamp.
//  3. The order is the server's, not the client's — grading.ReaderOrder, the
//     CPython rule, ported in internal/grading/cpyrand.go.
//  4. Unseen is unknown. A batch the caller neither administers nor reads is a
//     404 from EVERY endpoint under it, including the write and admin ones, so
//     the surface is never an existence oracle. 403 is used only where the
//     caller can already see the batch and lacks the role.
//
// Authorization is decided BEFORE state: a reader's `POST …/adjudicate` on an
// already-adjudicating batch is 403, not 409.
//
// REQUEST VALIDATION. The Go side has no JSON-schema validation mechanism, and
// its module root is `go/`, so `go:embed` cannot reach `contracts/schemas/`
// (they are not part of this module) — a runtime schema validator would have to
// be handed files that may not be deployed alongside the binary. Bodies are
// instead decoded with `DisallowUnknownFields`, which is the exact mirror of
// the schemas' `additionalProperties: false` and of the Python models'
// `extra="forbid"`, followed by explicit checks for every enum, bound and
// pattern the schemas state. No new dependency was added.

const maxGradingBodyBytes = 1 << 20 // max_json_body_bytes' 1 MB default

var rubricSHA256Re = regexp.MustCompile(`^[0-9a-f]{64}$`)
var extraQuestionIDRe = regexp.MustCompile(`^[A-Za-z0-9_.\-]{1,64}$`)

// --------------------------------------------------------------------------- //
// Wire shapes
// --------------------------------------------------------------------------- //
// Built as explicit types rather than reused records because two of the
// contract's rules are about a field's PRESENCE, not its value: `stratum` is
// absent when none was authored, and `reader_verdicts`/`adjudication` are
// absent unless the caller is an admin on a batch that has left `open`.
// `adjudication` in particular must be able to be *present and null*, which no
// `omitempty` pointer can express — hence the json.RawMessage.

type gradingProgressJSON struct {
	Reader string `json:"reader"`
	Label  string `json:"label"`
	Saved  int    `json:"saved"`
}

type gradingBatchJSON struct {
	ID             string                `json:"id"`
	Name           string                `json:"name"`
	Kind           string                `json:"kind"`
	Status         string                `json:"status"`
	RubricSHA256   string                `json:"rubric_sha256"`
	OrderSeed      int64                 `json:"order_seed"`
	Readers        []string              `json:"readers"`
	TaskCount      int                   `json:"task_count"`
	Progress       []gradingProgressJSON `json:"progress"`
	CreatedAt      string                `json:"created_at"`
	CreatedBy      string                `json:"created_by"`
	AdjudicatingAt string                `json:"adjudicating_at"`
}

type gradingVerdictJSON struct {
	TaskID         string                  `json:"task_id"`
	Reader         string                  `json:"reader"`
	Verdict        string                  `json:"verdict"`
	SpanJudgements []grading.SpanJudgement `json:"span_judgements"`
	ExtraAnswers   []grading.ExtraAnswer   `json:"extra_answers"`
	Notes          string                  `json:"notes"`
	Version        int                     `json:"version"`
	SavedAt        string                  `json:"saved_at"`
}

type gradingAdjudicationJSON struct {
	TaskID         string                  `json:"task_id"`
	Verdict        string                  `json:"verdict"`
	SpanJudgements []grading.SpanJudgement `json:"span_judgements"`
	Notes          string                  `json:"notes"`
	AdjudicatedBy  string                  `json:"adjudicated_by"`
	Version        int                     `json:"version"`
	SavedAt        string                  `json:"saved_at"`
}

type gradingTaskJSON struct {
	ID             string                  `json:"id"`
	BatchID        string                  `json:"batch_id"`
	Kind           string                  `json:"kind"`
	PairID         string                  `json:"pair_id"`
	Stratum        string                  `json:"stratum,omitempty"`
	Question       grading.Question        `json:"question"`
	Document       grading.Document        `json:"document"`
	Claims         []grading.EvidenceSet   `json:"claims"`
	ExtraQuestions []grading.ExtraQuestion `json:"extra_questions"`
	Readers        []string                `json:"readers"`
	CreatedAt      string                  `json:"created_at"`
	CreatedBy      string                  `json:"created_by"`
	Verdict        *gradingVerdictJSON     `json:"verdict"`
	// Present ONLY for an admin caller on a batch that has left `open`.
	ReaderVerdicts *[]gradingVerdictJSON `json:"reader_verdicts,omitempty"`
	Adjudication   json.RawMessage       `json:"adjudication,omitempty"`
}

type gradingBatchesResponse struct {
	Batches []gradingBatchJSON `json:"batches"`
}

type gradingTasksResponse struct {
	BatchID string            `json:"batch_id"`
	Reader  *string           `json:"reader"`
	Tasks   []gradingTaskJSON `json:"tasks"`
}

type gradingExportCSV struct {
	Filename string  `json:"filename"`
	Reader   *string `json:"reader"`
	Label    string  `json:"label"`
	Content  string  `json:"content"`
}

type gradingExportVerdictRow struct {
	PairID         string                  `json:"pair_id"`
	Stratum        string                  `json:"stratum,omitempty"`
	TaskID         string                  `json:"task_id"`
	Reader         string                  `json:"reader"`
	Label          string                  `json:"label"`
	Verdict        string                  `json:"verdict"`
	SpanJudgements []grading.SpanJudgement `json:"span_judgements"`
	ExtraAnswers   []grading.ExtraAnswer   `json:"extra_answers"`
	Notes          string                  `json:"notes"`
	Version        int                     `json:"version"`
	SavedAt        string                  `json:"saved_at"`
}

type gradingExportAdjudicationRow struct {
	PairID         string                  `json:"pair_id"`
	Stratum        string                  `json:"stratum,omitempty"`
	TaskID         string                  `json:"task_id"`
	Verdict        string                  `json:"verdict"`
	SpanJudgements []grading.SpanJudgement `json:"span_judgements"`
	Notes          string                  `json:"notes"`
	AdjudicatedBy  string                  `json:"adjudicated_by"`
	Version        int                     `json:"version"`
	SavedAt        string                  `json:"saved_at"`
}

type gradingReaderLabel struct {
	Subject string `json:"subject"`
	Label   string `json:"label"`
}

type gradingExportResponse struct {
	BatchID       string                         `json:"batch_id"`
	Name          string                         `json:"name"`
	Kind          string                         `json:"kind"`
	Status        string                         `json:"status"`
	RubricSHA256  string                         `json:"rubric_sha256"`
	OrderSeed     int64                          `json:"order_seed"`
	ExportedAt    string                         `json:"exported_at"`
	Readers       []gradingReaderLabel           `json:"readers"`
	CSV           []gradingExportCSV             `json:"csv"`
	Verdicts      []gradingExportVerdictRow      `json:"verdicts"`
	Adjudications []gradingExportAdjudicationRow `json:"adjudications"`
}

// --------------------------------------------------------------------------- //
// Request bodies
// --------------------------------------------------------------------------- //

type gradingSentenceIn struct {
	I    int    `json:"i"`
	Text string `json:"text"`
}

type gradingUnitIn struct {
	Index     int                 `json:"index"`
	Title     string              `json:"title"`
	Sentences []gradingSentenceIn `json:"sentences"`
}

type gradingDocumentIn struct {
	DocID string          `json:"doc_id"`
	Title string          `json:"title"`
	Units []gradingUnitIn `json:"units"`
}

type gradingSpanIn struct {
	Unit          int    `json:"unit"`
	FirstSentence int    `json:"first_sentence"`
	LastSentence  int    `json:"last_sentence"`
	Text          string `json:"text"`
}

type gradingEvidenceSetIn struct {
	SetIndex int             `json:"set_index"`
	Spans    []gradingSpanIn `json:"spans"`
	Sources  []string        `json:"sources"`
}

type gradingQuestionIn struct {
	ID          string `json:"id"`
	Type        string `json:"type"`
	Summary     string `json:"summary"`
	Description string `json:"description"`
}

type gradingExtraQuestionIn struct {
	ID         string `json:"id"`
	Text       string `json:"text"`
	AnswerType string `json:"answer_type"`
}

type gradingTaskCreateIn struct {
	PairID         string                   `json:"pair_id"`
	Stratum        string                   `json:"stratum"`
	Question       gradingQuestionIn        `json:"question"`
	Document       gradingDocumentIn        `json:"document"`
	Claims         []gradingEvidenceSetIn   `json:"claims"`
	ExtraQuestions []gradingExtraQuestionIn `json:"extra_questions"`
}

type gradingBatchCreateRequest struct {
	Name         string                `json:"name"`
	Kind         string                `json:"kind"`
	RubricSHA256 string                `json:"rubric_sha256"`
	OrderSeed    *int64                `json:"order_seed"`
	Readers      []string              `json:"readers"`
	Tasks        []gradingTaskCreateIn `json:"tasks"`
}

type gradingSpanJudgementIn struct {
	Set       int    `json:"set"`
	Span      int    `json:"span"`
	Judgement string `json:"judgement"`
}

type gradingExtraAnswerIn struct {
	ID     string `json:"id"`
	Answer string `json:"answer"`
}

type gradingVerdictPutRequest struct {
	Verdict        string                   `json:"verdict"`
	SpanJudgements []gradingSpanJudgementIn `json:"span_judgements"`
	ExtraAnswers   []gradingExtraAnswerIn   `json:"extra_answers"`
	Notes          string                   `json:"notes"`
}

type gradingAdjudicationPutRequest struct {
	Verdict        string                   `json:"verdict"`
	SpanJudgements []gradingSpanJudgementIn `json:"span_judgements"`
	Notes          string                   `json:"notes"`
}

// --------------------------------------------------------------------------- //
// Serializers
// --------------------------------------------------------------------------- //

func gradingBatchOut(b *grading.Batch, saved map[string]int) gradingBatchJSON {
	progress := make([]gradingProgressJSON, 0, len(b.Readers))
	for i, r := range b.Readers {
		// Counts, never verdicts — this is what one reader learns of the
		// other, and all of it.
		progress = append(progress, gradingProgressJSON{
			Reader: r, Label: grading.LabelFor(i), Saved: saved[r],
		})
	}
	return gradingBatchJSON{
		ID:             b.ID,
		Name:           b.Name,
		Kind:           b.Kind,
		Status:         b.Status,
		RubricSHA256:   b.RubricSHA256,
		OrderSeed:      b.OrderSeed,
		Readers:        nonNilStrings(b.Readers),
		TaskCount:      b.TaskCount,
		Progress:       progress,
		CreatedAt:      b.CreatedAt,
		CreatedBy:      b.CreatedBy,
		AdjudicatingAt: b.AdjudicatingAt,
	}
}

func gradingVerdictOut(v *grading.Verdict) *gradingVerdictJSON {
	if v == nil {
		return nil
	}
	return &gradingVerdictJSON{
		TaskID:         v.TaskID,
		Reader:         v.Reader,
		Verdict:        v.Verdict,
		SpanJudgements: nonNilJudgements(v.SpanJudgements),
		ExtraAnswers:   nonNilAnswers(v.ExtraAnswers),
		Notes:          v.Notes,
		Version:        v.Version,
		SavedAt:        v.SavedAt,
	}
}

func gradingAdjudicationOut(a *grading.Adjudication) *gradingAdjudicationJSON {
	if a == nil {
		return nil
	}
	return &gradingAdjudicationJSON{
		TaskID:         a.TaskID,
		Verdict:        a.Verdict,
		SpanJudgements: nonNilJudgements(a.SpanJudgements),
		Notes:          a.Notes,
		AdjudicatedBy:  a.AdjudicatedBy,
		Version:        a.Version,
		SavedAt:        a.SavedAt,
	}
}

func gradingTaskOut(
	t *grading.Task,
	own *grading.Verdict,
	adminView bool,
	readerVerdicts []*grading.Verdict,
	adj *grading.Adjudication,
) gradingTaskJSON {
	out := gradingTaskJSON{
		ID:             t.ID,
		BatchID:        t.BatchID,
		Kind:           t.Kind,
		PairID:         t.PairID,
		Stratum:        t.Stratum,
		Question:       t.Question,
		Document:       normalizedDocument(t.Document),
		Claims:         nonNilClaims(t.Claims),
		ExtraQuestions: nonNilExtraQuestions(t.ExtraQuestions),
		Readers:        nonNilStrings(t.Readers),
		CreatedAt:      t.CreatedAt,
		CreatedBy:      t.CreatedBy,
		Verdict:        gradingVerdictOut(own),
	}
	if adminView {
		rows := make([]gradingVerdictJSON, 0, len(readerVerdicts))
		for _, v := range readerVerdicts {
			rows = append(rows, *gradingVerdictOut(v))
		}
		out.ReaderVerdicts = &rows
		// Present AND null when there is no joint-read row yet — "the admin
		// view is on, nobody has adjudicated this task" is a different fact
		// from "you are not entitled to this field".
		raw, err := json.Marshal(gradingAdjudicationOut(adj))
		if err != nil { // unreachable: the value is a plain struct
			raw = []byte("null")
		}
		out.Adjudication = raw
	}
	return out
}

// --------------------------------------------------------------------------- //
// Authorization: who the caller is, relative to one batch
// --------------------------------------------------------------------------- //

type gradingView struct {
	batch       *grading.Batch
	subject     string
	isAdmin     bool
	readerIndex int // -1 when the caller is not one of the batch's readers
}

func (v gradingView) isReader() bool { return v.readerIndex >= 0 }

// adminView reports whether the caller may see the OTHER readers' rows: an
// admin, and only once the read has ended.
func (v gradingView) adminView() bool {
	return v.isAdmin && v.batch.Status != grading.StatusOpen
}

// gradingNotFound is the one 404. A batch, task or verdict the caller may not
// see is indistinguishable from one that does not exist — otherwise this
// surface would confirm a read's existence, and its reader list, to anyone who
// guessed an id (ADR-0003 §2).
func gradingNotFound(w http.ResponseWriter, r *http.Request, what string) {
	writeError(w, r, http.StatusNotFound, "unknown grading "+what)
}

// gradingViewOrNotFound resolves the caller against a batch, or writes the 404.
func gradingViewOrNotFound(
	w http.ResponseWriter, r *http.Request, batch *grading.Batch, what string,
) (gradingView, bool) {
	if batch == nil {
		gradingNotFound(w, r, what)
		return gradingView{}, false
	}
	p := auth.FromContext(r.Context())
	idx := -1
	for i, reader := range batch.Readers {
		if reader == p.Tenant {
			idx = i
			break
		}
	}
	if !p.IsAdmin() && idx < 0 {
		gradingNotFound(w, r, what)
		return gradingView{}, false
	}
	return gradingView{batch: batch, subject: p.Tenant, isAdmin: p.IsAdmin(), readerIndex: idx}, true
}

// requireGradingAdmin writes 403 — not 404 — when the caller lacks the role:
// they can already see this batch, so refusing by role tells them nothing new.
func requireGradingAdmin(w http.ResponseWriter, r *http.Request, v gradingView, action string) bool {
	if v.isAdmin {
		return true
	}
	writeError(w, r, http.StatusForbidden, action+" requires the admin role")
	return false
}

// gradingUnavailable is the contract's 503. Every `/v1/grading` operation
// documents it: a store failure must never surface as a 500, and never as a
// PARTIAL 200 — on this surface a silently missing verdict row reads as "not
// yet read", which is a wrong κ rather than a visible outage.
func gradingUnavailable(w http.ResponseWriter, r *http.Request, op string, err error) {
	slog.Warn("grading store failure", "op", op, "error", err)
	writeError(w, r, http.StatusServiceUnavailable,
		"grading store unavailable; refusing to serve (fail closed)")
}

// --------------------------------------------------------------------------- //
// Handlers
// --------------------------------------------------------------------------- //

// HandleListGradingBatches lists the batches the caller may see: every batch
// for an admin, and for anyone else exactly the batches that name the caller in
// `readers`. Newest first, unpaginated.
//
// This is also the endpoint a client probes to learn whether a server
// implements grading at all — an implementation without it answers 404 — so it
// must not 403 a caller who simply has no reads. An empty list is the answer.
func (s *Server) HandleListGradingBatches(w http.ResponseWriter, r *http.Request) {
	p := auth.FromContext(r.Context())
	batches, err := s.Grading.ListBatches(r.Context())
	if err != nil {
		gradingUnavailable(w, r, "list_batches", err)
		return
	}
	out := gradingBatchesResponse{Batches: []gradingBatchJSON{}}
	for _, b := range batches {
		if !p.IsAdmin() && !containsString(b.Readers, p.Tenant) {
			continue
		}
		saved, err := s.savedCounts(r, b.ID)
		if err != nil {
			gradingUnavailable(w, r, "list_batches", err)
			return
		}
		out.Batches = append(out.Batches, gradingBatchOut(b, saved))
	}
	writeJSON(w, http.StatusOK, out)
}

// HandleCreateGradingBatch creates a read with its tasks (admin only).
//
// Validated whole and stored whole: a duplicate `pair_id`, a span outside its
// document, a duplicate `set_index`, a group-form reader or two readers that
// resolve to one subject is a 422 and nothing is created. A reader handed half
// a draw would produce a κ over a sample nobody recorded.
func (s *Server) HandleCreateGradingBatch(w http.ResponseWriter, r *http.Request) {
	p := auth.FromContext(r.Context())
	if !p.IsAdmin() {
		// Stateless: no batch exists to hide, so a 403 leaks nothing.
		writeError(w, r, http.StatusForbidden, "creating a grading batch requires the admin role")
		return
	}
	var req gradingBatchCreateRequest
	if !decodeGradingBody(w, r, &req) {
		return
	}
	if err := validateBatchCreate(&req); err != nil {
		writeError(w, r, http.StatusUnprocessableEntity, err.Error())
		return
	}
	readers, err := grading.ResolveReaders(req.Readers)
	if err != nil {
		writeError(w, r, http.StatusUnprocessableEntity, err.Error())
		return
	}

	now := grading.NowISO()
	batch := &grading.Batch{
		ID:             uuid.New().String(),
		Name:           req.Name,
		Kind:           req.Kind,
		Status:         grading.StatusOpen,
		RubricSHA256:   req.RubricSHA256,
		OrderSeed:      *req.OrderSeed,
		Readers:        readers,
		TaskCount:      len(req.Tasks),
		CreatedAt:      now,
		CreatedBy:      p.Tenant,
		AdjudicatingAt: "",
	}
	tasks := make([]*grading.Task, 0, len(req.Tasks))
	for i := range req.Tasks {
		tasks = append(tasks, toTaskRecord(&req.Tasks[i], batch, i))
	}
	if err := s.Grading.CreateBatch(r.Context(), batch, tasks); err != nil {
		gradingUnavailable(w, r, "create_batch", err)
		return
	}
	slog.Info("grading batch created",
		"id", batch.ID, "name", batch.Name, "tasks", batch.TaskCount,
		"readers", readers, "by", p.Tenant)
	writeJSON(w, http.StatusCreated, gradingBatchOut(batch, map[string]int{}))
}

// HandleGetGradingBatch returns one batch — the same record for an admin and
// for a reader. Counts are the only thing one reader learns about the other,
// and no verdict ever travels here.
func (s *Server) HandleGetGradingBatch(w http.ResponseWriter, r *http.Request) {
	batchID := chi.URLParam(r, "batch_id")
	batch, err := s.Grading.GetBatch(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "get_batch", err)
		return
	}
	view, ok := gradingViewOrNotFound(w, r, batch, "batch")
	if !ok {
		return
	}
	saved, err := s.savedCounts(r, batchID)
	if err != nil {
		gradingUnavailable(w, r, "get_batch", err)
		return
	}
	writeJSON(w, http.StatusOK, gradingBatchOut(view.batch, saved))
}

// HandleDeleteGradingBatch hard-deletes a batch and everything under it (admin
// only). It exists so a harness that created a batch on a real server can
// remove it and verify by listing; it is not part of the read's protocol.
func (s *Server) HandleDeleteGradingBatch(w http.ResponseWriter, r *http.Request) {
	batchID := chi.URLParam(r, "batch_id")
	batch, err := s.Grading.GetBatch(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "delete_batch", err)
		return
	}
	view, ok := gradingViewOrNotFound(w, r, batch, "batch")
	if !ok {
		return
	}
	if !requireGradingAdmin(w, r, view, "deleting a grading batch") {
		return
	}
	if _, err := s.Grading.DeleteBatch(r.Context(), batchID); err != nil {
		gradingUnavailable(w, r, "delete_batch", err)
		return
	}
	slog.Warn("grading batch deleted", "id", batchID, "name", view.batch.Name,
		"by", auth.FromContext(r.Context()).Tenant)
	w.WriteHeader(http.StatusNoContent)
}

// HandleListGradingTasks returns the batch's tasks in the CALLER'S order, each
// carrying the caller's own verdict or null — never another reader's, whatever
// the batch status.
//
// An admin who is not a reader gets batch order, `reader: null` and a null
// verdict on every task; once the batch has left `open`, every task also
// carries `reader_verdicts` and `adjudication`.
func (s *Server) HandleListGradingTasks(w http.ResponseWriter, r *http.Request) {
	batchID := chi.URLParam(r, "batch_id")
	batch, err := s.Grading.GetBatch(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "list_tasks", err)
		return
	}
	view, ok := gradingViewOrNotFound(w, r, batch, "batch")
	if !ok {
		return
	}
	tasks, err := s.Grading.ListTasks(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "list_tasks", err)
		return
	}
	ordered := tasks
	if view.isReader() {
		order := grading.ReaderOrder(view.batch.OrderSeed, view.readerIndex, len(tasks))
		ordered = make([]*grading.Task, 0, len(tasks))
		for _, i := range order {
			ordered = append(ordered, tasks[i])
		}
	}

	own := map[string]*grading.Verdict{}
	byTask := map[string][]*grading.Verdict{}
	if view.isReader() || view.adminView() {
		rows, err := s.Grading.ListVerdicts(r.Context(), batchID)
		if err != nil {
			gradingUnavailable(w, r, "list_tasks", err)
			return
		}
		for _, v := range rows {
			if view.isReader() && v.Reader == view.subject {
				own[v.TaskID] = v
			}
			if view.adminView() {
				byTask[v.TaskID] = append(byTask[v.TaskID], v)
			}
		}
	}
	adjs := map[string]*grading.Adjudication{}
	if view.adminView() {
		rows, err := s.Grading.ListAdjudications(r.Context(), batchID)
		if err != nil {
			gradingUnavailable(w, r, "list_tasks", err)
			return
		}
		for _, a := range rows {
			adjs[a.TaskID] = a
		}
	}

	resp := gradingTasksResponse{BatchID: batchID, Tasks: []gradingTaskJSON{}}
	if view.isReader() {
		subject := view.subject
		resp.Reader = &subject
	}
	for _, t := range ordered {
		resp.Tasks = append(resp.Tasks, gradingTaskOut(
			t, own[t.ID], view.adminView(),
			inLabelOrder(byTask[t.ID], view.batch.Readers), adjs[t.ID],
		))
	}
	writeJSON(w, http.StatusOK, resp)
}

// HandleAdjudicateGradingBatch freezes the readers' verdicts and opens
// adjudication (admin only) — the moment independence ends, recorded with a
// timestamp.
//
// Not idempotent: replaying it on a batch that is no longer `open` is a 409, so
// an accidental second click is not a silent no-op hiding a state the UI did
// not expect. Authorization is decided first: a reader gets 403 here even on a
// batch that is already adjudicating.
func (s *Server) HandleAdjudicateGradingBatch(w http.ResponseWriter, r *http.Request) {
	batchID := chi.URLParam(r, "batch_id")
	batch, err := s.Grading.GetBatch(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "adjudicate", err)
		return
	}
	view, ok := gradingViewOrNotFound(w, r, batch, "batch")
	if !ok {
		return
	}
	if !requireGradingAdmin(w, r, view, "adjudicating a grading batch") {
		return
	}
	moved, err := s.Grading.BeginAdjudication(r.Context(), batchID, grading.NowISO())
	if err != nil {
		gradingUnavailable(w, r, "adjudicate", err)
		return
	}
	if !moved {
		writeError(w, r, http.StatusConflict, fmt.Sprintf(
			"batch %q is %q, not 'open': the readers' rows are already frozen",
			batchID, view.batch.Status))
		return
	}
	updated, err := s.Grading.GetBatch(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "adjudicate", err)
		return
	}
	if updated == nil { // deleted between the two calls
		gradingNotFound(w, r, "batch")
		return
	}
	saved, err := s.savedCounts(r, batchID)
	if err != nil {
		gradingUnavailable(w, r, "adjudicate", err)
		return
	}
	slog.Warn("grading batch adjudication opened — reader rows are now frozen and visible to admins",
		"id", batchID, "by", auth.FromContext(r.Context()).Tenant)
	writeJSON(w, http.StatusOK, gradingBatchOut(updated, saved))
}

// HandleGetGradingTask returns one task with the caller's own verdict or null.
// A reader never receives `reader_verdicts` or `adjudication`, whatever the
// batch status.
func (s *Server) HandleGetGradingTask(w http.ResponseWriter, r *http.Request) {
	task, view, ok := s.gradingTaskView(w, r)
	if !ok {
		return
	}
	var own *grading.Verdict
	if view.isReader() {
		var err error
		own, err = s.Grading.GetVerdict(r.Context(), task.ID, view.subject)
		if err != nil {
			gradingUnavailable(w, r, "get_task", err)
			return
		}
	}
	var rows []*grading.Verdict
	var adj *grading.Adjudication
	if view.adminView() {
		all, err := s.Grading.ListVerdicts(r.Context(), task.BatchID)
		if err != nil {
			gradingUnavailable(w, r, "get_task", err)
			return
		}
		mine := make([]*grading.Verdict, 0, len(all))
		for _, v := range all {
			if v.TaskID == task.ID {
				mine = append(mine, v)
			}
		}
		rows = inLabelOrder(mine, view.batch.Readers)
		adj, err = s.Grading.GetAdjudication(r.Context(), task.ID)
		if err != nil {
			gradingUnavailable(w, r, "get_task", err)
			return
		}
	}
	writeJSON(w, http.StatusOK, gradingTaskOut(task, own, view.adminView(), rows, adj))
}

// HandlePutGradingVerdict saves or overwrites the CALLER'S own verdict — the
// one write a reader has.
//
// The row is keyed by (task, authenticated subject) and the body has no field
// naming a reader, so a caller can only ever write their own row. Always 200: a
// client does not care whether this was the first save, and `version` says so.
func (s *Server) HandlePutGradingVerdict(w http.ResponseWriter, r *http.Request) {
	task, view, ok := s.gradingTaskView(w, r)
	if !ok {
		return
	}
	if !view.isReader() {
		// Reachable only by an admin: a non-reader non-admin got the 404 above.
		writeError(w, r, http.StatusForbidden, fmt.Sprintf(
			"only a reader of this batch has a verdict row; an admin adjudicates "+
				"instead (PUT /v1/grading/tasks/%s/adjudication)", task.ID))
		return
	}
	if view.batch.Status != grading.StatusOpen {
		writeError(w, r, http.StatusConflict, fmt.Sprintf(
			"batch %q is %q: the readers' rows are frozen for adjudication and cannot be changed",
			view.batch.ID, view.batch.Status))
		return
	}
	var req gradingVerdictPutRequest
	if !decodeGradingBody(w, r, &req) {
		return
	}
	if !grading.InVocabulary(req.Verdict, grading.Verdicts) {
		writeError(w, r, http.StatusUnprocessableEntity, fmt.Sprintf(
			"verdict %q is not one of %v", req.Verdict, grading.Verdicts))
		return
	}
	if len(req.Notes) > 10000 {
		writeError(w, r, http.StatusUnprocessableEntity, "notes must be at most 10000 characters")
		return
	}
	judgements, err := checkJudgements(task, req.SpanJudgements)
	if err != nil {
		writeError(w, r, http.StatusUnprocessableEntity, err.Error())
		return
	}
	answers, err := checkAnswers(task, req.ExtraAnswers)
	if err != nil {
		writeError(w, r, http.StatusUnprocessableEntity, err.Error())
		return
	}
	stored, err := s.Grading.PutVerdict(r.Context(), &grading.Verdict{
		TaskID:         task.ID,
		BatchID:        task.BatchID,
		Reader:         view.subject,
		Verdict:        req.Verdict,
		SpanJudgements: judgements,
		ExtraAnswers:   answers,
		Notes:          req.Notes,
		SavedAt:        grading.NowISO(),
	})
	if err != nil {
		gradingUnavailable(w, r, "put_verdict", err)
		return
	}
	writeJSON(w, http.StatusOK, gradingVerdictOut(stored))
}

// HandleGetGradingVerdict returns the caller's own row — or any reader's, for
// an admin once the batch has left `open`.
//
// Every other case is a 404, deliberately indistinguishable: a reader naming
// another reader, an admin naming a reader while the read is still open, a
// subject who is not a reader at all, and a reader who has simply not saved yet
// all answer the same way. This is the endpoint the independence test probes.
func (s *Server) HandleGetGradingVerdict(w http.ResponseWriter, r *http.Request) {
	task, view, ok := s.gradingTaskView(w, r)
	if !ok {
		return
	}
	reader := urlParam(r, "reader")
	own := view.isReader() && reader == view.subject
	if !own && !view.adminView() {
		gradingNotFound(w, r, "verdict")
		return
	}
	row, err := s.Grading.GetVerdict(r.Context(), task.ID, reader)
	if err != nil {
		gradingUnavailable(w, r, "get_verdict", err)
		return
	}
	if row == nil {
		gradingNotFound(w, r, "verdict")
		return
	}
	writeJSON(w, http.StatusOK, gradingVerdictOut(row))
}

// HandlePutGradingAdjudication saves the joint-read verdict (admin only, batch
// `adjudicating`) — the verdict the study USES (SPEC §6.6.3). The readers' own
// rows are never modified by it: the pre-adjudication κ is the one reported,
// and it needs the originals.
func (s *Server) HandlePutGradingAdjudication(w http.ResponseWriter, r *http.Request) {
	task, view, ok := s.gradingTaskView(w, r)
	if !ok {
		return
	}
	if !requireGradingAdmin(w, r, view, "saving a joint-read verdict") {
		return
	}
	if view.batch.Status == grading.StatusOpen {
		writeError(w, r, http.StatusConflict, fmt.Sprintf(
			"batch %q is still 'open': adjudicate it first (POST /v1/grading/batches/%s/adjudicate)",
			view.batch.ID, view.batch.ID))
		return
	}
	var req gradingAdjudicationPutRequest
	if !decodeGradingBody(w, r, &req) {
		return
	}
	if !grading.InVocabulary(req.Verdict, grading.Verdicts) {
		writeError(w, r, http.StatusUnprocessableEntity, fmt.Sprintf(
			"verdict %q is not one of %v", req.Verdict, grading.Verdicts))
		return
	}
	if len(req.Notes) > 10000 {
		writeError(w, r, http.StatusUnprocessableEntity, "notes must be at most 10000 characters")
		return
	}
	judgements, err := checkJudgements(task, req.SpanJudgements)
	if err != nil {
		writeError(w, r, http.StatusUnprocessableEntity, err.Error())
		return
	}
	stored, err := s.Grading.PutAdjudication(r.Context(), &grading.Adjudication{
		TaskID:         task.ID,
		BatchID:        task.BatchID,
		Verdict:        req.Verdict,
		SpanJudgements: judgements,
		Notes:          req.Notes,
		AdjudicatedBy:  view.subject,
		SavedAt:        grading.NowISO(),
	})
	if err != nil {
		gradingUnavailable(w, r, "put_adjudication", err)
		return
	}
	writeJSON(w, http.StatusOK, gradingAdjudicationOut(stored))
}

// HandleExportGradingBatch returns the read's results in the scorer's shape
// (admin only, batch not `open`).
//
// Refused with 409 while the batch is `open`: exporting mid-read would show an
// admin the readers' rows before independence ends, which is what
// `POST …/adjudicate` is for.
func (s *Server) HandleExportGradingBatch(w http.ResponseWriter, r *http.Request) {
	batchID := chi.URLParam(r, "batch_id")
	batch, err := s.Grading.GetBatch(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "export", err)
		return
	}
	view, ok := gradingViewOrNotFound(w, r, batch, "batch")
	if !ok {
		return
	}
	if !requireGradingAdmin(w, r, view, "exporting a grading batch") {
		return
	}
	if view.batch.Status == grading.StatusOpen {
		writeError(w, r, http.StatusConflict, fmt.Sprintf(
			"batch %q is still 'open': adjudicate it first (POST /v1/grading/batches/%s/adjudicate). "+
				"Exporting mid-read would end reader independence without recording that it ended",
			batchID, batchID))
		return
	}
	tasks, err := s.Grading.ListTasks(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "export", err)
		return
	}
	verdicts, err := s.Grading.ListVerdicts(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "export", err)
		return
	}
	adjRows, err := s.Grading.ListAdjudications(r.Context(), batchID)
	if err != nil {
		gradingUnavailable(w, r, "export", err)
		return
	}
	byKey := map[[2]string]*grading.Verdict{}
	for _, v := range verdicts {
		byKey[[2]string{v.TaskID, v.Reader}] = v
	}
	adjs := map[string]*grading.Adjudication{}
	for _, a := range adjRows {
		adjs[a.TaskID] = a
	}

	b := view.batch
	sheets := make([]gradingExportCSV, 0, len(b.Readers)+1)
	for i, subject := range b.Readers {
		label := grading.LabelFor(i)
		rows := make([][3]string, 0, len(tasks))
		for _, t := range tasks {
			// One row per task IN BATCH ORDER whether or not a verdict exists:
			// a task without one has an EMPTY verdict cell — the scorer's
			// "not yet read", never a verdict.
			if v, ok := byKey[[2]string{t.ID, subject}]; ok {
				rows = append(rows, [3]string{t.PairID, v.Verdict, v.Notes})
			} else {
				rows = append(rows, [3]string{t.PairID, "", ""})
			}
		}
		reader := subject
		sheets = append(sheets, gradingExportCSV{
			Filename: "rdev_verdicts_" + label + ".csv",
			Reader:   &reader,
			Label:    label,
			Content:  gradingCSV(rows),
		})
	}
	adjSheet := make([][3]string, 0, len(tasks))
	for _, t := range tasks {
		if a, ok := adjs[t.ID]; ok {
			adjSheet = append(adjSheet, [3]string{t.PairID, a.Verdict, a.Notes})
		} else {
			adjSheet = append(adjSheet, [3]string{t.PairID, "", ""})
		}
	}
	sheets = append(sheets, gradingExportCSV{
		Filename: "rdev_verdicts_ADJ.csv",
		Reader:   nil,
		Label:    "ADJ",
		Content:  gradingCSV(adjSheet),
	})

	verdictRows := []gradingExportVerdictRow{}
	adjudicationRows := []gradingExportAdjudicationRow{}
	for _, t := range tasks {
		for i, subject := range b.Readers {
			v, ok := byKey[[2]string{t.ID, subject}]
			if !ok {
				continue
			}
			verdictRows = append(verdictRows, gradingExportVerdictRow{
				PairID:         t.PairID,
				Stratum:        t.Stratum,
				TaskID:         t.ID,
				Reader:         v.Reader,
				Label:          grading.LabelFor(i),
				Verdict:        v.Verdict,
				SpanJudgements: nonNilJudgements(v.SpanJudgements),
				ExtraAnswers:   nonNilAnswers(v.ExtraAnswers),
				Notes:          v.Notes,
				Version:        v.Version,
				SavedAt:        v.SavedAt,
			})
		}
		if a, ok := adjs[t.ID]; ok {
			adjudicationRows = append(adjudicationRows, gradingExportAdjudicationRow{
				PairID:         t.PairID,
				Stratum:        t.Stratum,
				TaskID:         t.ID,
				Verdict:        a.Verdict,
				SpanJudgements: nonNilJudgements(a.SpanJudgements),
				Notes:          a.Notes,
				AdjudicatedBy:  a.AdjudicatedBy,
				Version:        a.Version,
				SavedAt:        a.SavedAt,
			})
		}
	}

	labelsOut := make([]gradingReaderLabel, 0, len(b.Readers))
	for i, subject := range b.Readers {
		labelsOut = append(labelsOut, gradingReaderLabel{Subject: subject, Label: grading.LabelFor(i)})
	}
	writeJSON(w, http.StatusOK, gradingExportResponse{
		BatchID:       b.ID,
		Name:          b.Name,
		Kind:          b.Kind,
		Status:        b.Status,
		RubricSHA256:  b.RubricSHA256,
		OrderSeed:     b.OrderSeed,
		ExportedAt:    grading.NowISO(),
		Readers:       labelsOut,
		CSV:           sheets,
		Verdicts:      verdictRows,
		Adjudications: adjudicationRows,
	})
}

// --------------------------------------------------------------------------- //
// Shared helpers
// --------------------------------------------------------------------------- //

// gradingTaskView resolves a task and the caller's view of its batch, writing
// the 404 for a task that does not exist OR sits on a batch the caller may not
// see — the same answer, on purpose.
func (s *Server) gradingTaskView(
	w http.ResponseWriter, r *http.Request,
) (*grading.Task, gradingView, bool) {
	taskID := chi.URLParam(r, "task_id")
	task, err := s.Grading.GetTask(r.Context(), taskID)
	if err != nil {
		gradingUnavailable(w, r, "get_task", err)
		return nil, gradingView{}, false
	}
	if task == nil {
		gradingNotFound(w, r, "task")
		return nil, gradingView{}, false
	}
	batch, err := s.Grading.GetBatch(r.Context(), task.BatchID)
	if err != nil {
		gradingUnavailable(w, r, "get_batch", err)
		return nil, gradingView{}, false
	}
	view, ok := gradingViewOrNotFound(w, r, batch, "task")
	if !ok {
		return nil, gradingView{}, false
	}
	return task, view, true
}

func (s *Server) savedCounts(r *http.Request, batchID string) (map[string]int, error) {
	rows, err := s.Grading.ListVerdicts(r.Context(), batchID)
	if err != nil {
		return nil, err
	}
	counts := map[string]int{}
	for _, v := range rows {
		counts[v.Reader]++
	}
	return counts, nil
}

// inLabelOrder returns the readers' rows in label order, omitting readers who
// have not saved one.
func inLabelOrder(rows []*grading.Verdict, readers []string) []*grading.Verdict {
	byReader := map[string]*grading.Verdict{}
	for _, v := range rows {
		byReader[v.Reader] = v
	}
	out := make([]*grading.Verdict, 0, len(rows))
	for _, reader := range readers {
		if v, ok := byReader[reader]; ok {
			out = append(out, v)
		}
	}
	return out
}

// gradingCSV renders `s0_rdev_score.read_verdicts`'s sheet: RFC 4180 with EVERY
// field double-quoted and `"` doubled, UTF-8, `\n` line endings, a trailing
// newline, header exactly `pair_id,verdict,notes`.
//
// The form is pinned by `GradingExportResponse` so two implementations produce
// the SAME BYTES and the scorer reads either unchanged — which is why this is
// hand-rolled rather than handed to encoding/csv, whose writer quotes only
// where it must.
func gradingCSV(rows [][3]string) string {
	var b strings.Builder
	writeRow := func(fields [3]string) {
		for i, f := range fields {
			if i > 0 {
				b.WriteByte(',')
			}
			b.WriteByte('"')
			b.WriteString(strings.ReplaceAll(f, `"`, `""`))
			b.WriteByte('"')
		}
		b.WriteByte('\n')
	}
	writeRow([3]string{"pair_id", "verdict", "notes"})
	for _, row := range rows {
		writeRow(row)
	}
	return b.String()
}

// decodeGradingBody decodes a request body strictly.
//
// `DisallowUnknownFields` is the mirror of the schemas' `additionalProperties:
// false` (and of the Python models' `extra="forbid"`), so an unknown field is
// the contract's 422 rather than a silently ignored one — which on this surface
// would mean a client believing it named a reader when it did not.
func decodeGradingBody(w http.ResponseWriter, r *http.Request, dst any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxGradingBodyBytes))
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			writeError(w, r, http.StatusRequestEntityTooLarge, fmt.Sprintf(
				"request body exceeds max_json_body_bytes (%d)", maxGradingBodyBytes))
			return false
		}
		writeValidationError(w, r, err.Error())
		return false
	}
	if dec.More() {
		writeValidationError(w, r, "unexpected trailing content after the JSON body")
		return false
	}
	return true
}

// urlParam reads a chi path parameter, percent-decoding it. A resolved reader
// subject is `issuer:subject`, and the contract tells clients to URL-encode it
// because some escape the colon.
func urlParam(r *http.Request, name string) string {
	raw := chi.URLParam(r, name)
	if decoded, err := url.PathUnescape(raw); err == nil {
		return decoded
	}
	return raw
}

func containsString(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}

func nonNilStrings(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}

func nonNilJudgements(in []grading.SpanJudgement) []grading.SpanJudgement {
	if in == nil {
		return []grading.SpanJudgement{}
	}
	return in
}

func nonNilAnswers(in []grading.ExtraAnswer) []grading.ExtraAnswer {
	if in == nil {
		return []grading.ExtraAnswer{}
	}
	return in
}

func nonNilClaims(in []grading.EvidenceSet) []grading.EvidenceSet {
	out := make([]grading.EvidenceSet, 0, len(in))
	for _, c := range in {
		c.Spans = append([]grading.Span{}, c.Spans...)
		c.Sources = nonNilStrings(c.Sources)
		out = append(out, c)
	}
	return out
}

func nonNilExtraQuestions(in []grading.ExtraQuestion) []grading.ExtraQuestion {
	if in == nil {
		return []grading.ExtraQuestion{}
	}
	return in
}

// normalizedDocument guarantees every array in the document is an array and
// never JSON null — the schema requires `units` and `sentences` to be arrays,
// and a `nil` slice marshals to `null`.
func normalizedDocument(d grading.Document) grading.Document {
	out := d
	units := make([]grading.Unit, 0, len(d.Units))
	for _, u := range d.Units {
		cu := u
		if cu.Sentences == nil {
			cu.Sentences = []grading.Sentence{}
		}
		units = append(units, cu)
	}
	out.Units = units
	return out
}
