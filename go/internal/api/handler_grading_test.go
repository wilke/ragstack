package api

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/ragstack/ragstack/internal/auth"
	"github.com/ragstack/ragstack/internal/grading"
)

// In-process statements of the rules `conformance/test_grading.py` asserts over
// HTTP. They are not a substitute for that suite — they are what makes a
// failure name the handler instead of the deployment, and what keeps the rules
// covered when nobody has a keyed server running.
//
// Every test drives the real router, so the auth middleware, the chi routing
// and the JSON encoding are all in the path: a test that called the handler
// function directly would prove nothing about whether the route is mounted or
// whether a principal reaches it.

const (
	keyAdmin    = "test-key-admin"
	keyReaderA  = "test-key-reader-a"
	keyReaderB  = "test-key-reader-b"
	keyOutsider = "test-key-outsider"

	subjAdmin    = "t-admin"
	subjReaderA  = "t-reader-a"
	subjReaderB  = "t-reader-b"
	subjOutsider = "t-outsider"

	testRubric = "2e11f3688de916da8bfc8b5b0a788050bf9d077960d616d33490c6ecf747363b"
	testSeed   = 4242
)

func newGradingTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	s := &Server{
		Auth: auth.New(
			[]string{keyAdmin, keyReaderA, keyReaderB, keyOutsider},
			map[string]string{
				keyAdmin:    subjAdmin,
				keyReaderA:  subjReaderA,
				keyReaderB:  subjReaderB,
				keyOutsider: subjOutsider,
			},
			map[string]string{keyAdmin: auth.RoleAdmin},
			auth.RoleUser,
		),
		Grading: grading.NewMemoryStore(),
	}
	srv := httptest.NewServer(NewRouterWithServer(nil, s))
	t.Cleanup(srv.Close)
	return srv
}

func do(t *testing.T, srv *httptest.Server, method, path, key string, body any) (int, []byte) {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal body: %v", err)
		}
		rdr = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, srv.URL+path, rdr)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if key != "" {
		req.Header.Set("X-API-Key", key)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close() //nolint:errcheck
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	return resp.StatusCode, raw
}

func decodeInto(t *testing.T, raw []byte, dst any) {
	t.Helper()
	if err := json.Unmarshal(raw, dst); err != nil {
		t.Fatalf("decode %s: %v", raw, err)
	}
}

// testDocument mirrors the conformance module's material: two units, five
// sentences then two.
func testDocument(n int) map[string]any {
	sentences := make([]map[string]any, 0, 5)
	for i := 0; i < 5; i++ {
		sentences = append(sentences, map[string]any{"i": i, "text": "Sentence."})
	}
	return map[string]any{
		"doc_id": "doc", "title": "A document",
		"units": []map[string]any{
			{"index": 0, "title": "Abstract", "sentences": sentences},
			{"index": 1, "title": "Results", "sentences": []map[string]any{
				{"i": 0, "text": "Result 0."}, {"i": 1, "text": "Result 1."},
			}},
		},
	}
}

func testTask(n int, withClaims bool) map[string]any {
	claims := []map[string]any{}
	if withClaims {
		claims = []map[string]any{{
			"set_index": 1,
			"spans": []map[string]any{
				{"unit": 0, "first_sentence": 1, "last_sentence": 2, "text": "…"},
			},
			"sources": []string{"scout"},
		}}
	}
	return map[string]any{
		"pair_id": pairID(n),
		"stratum": "model_positive",
		"question": map[string]any{
			"type": "diagnosis", "summary": "A case.", "description": "A case, at length.",
		},
		"document":        testDocument(n),
		"claims":          claims,
		"extra_questions": []any{},
	}
}

func pairID(n int) string { return "pair-" + string(rune('a'+n)) }

func createBody(taskCount int) map[string]any {
	tasks := make([]map[string]any, 0, taskCount)
	for i := 0; i < taskCount; i++ {
		tasks = append(tasks, testTask(i, true))
	}
	return map[string]any{
		"name":          "unit-test read",
		"kind":          "evidence-read",
		"rubric_sha256": testRubric,
		"order_seed":    testSeed,
		"readers":       []string{"@service:" + subjReaderA, "@service:" + subjReaderB},
		"tasks":         tasks,
	}
}

// createBatch creates a six-task batch as the admin and returns its id plus the
// task ids in BATCH order.
func createBatch(t *testing.T, srv *httptest.Server) (string, []string) {
	t.Helper()
	status, raw := do(t, srv, http.MethodPost, "/v1/grading/batches", keyAdmin, createBody(6))
	if status != http.StatusCreated {
		t.Fatalf("create batch: %d %s", status, raw)
	}
	var batch gradingBatchJSON
	decodeInto(t, raw, &batch)
	// The admin is not a reader, so the admin's listing is BATCH order.
	status, raw = do(t, srv, http.MethodGet, "/v1/grading/batches/"+batch.ID+"/tasks", keyAdmin, nil)
	if status != http.StatusOK {
		t.Fatalf("list tasks as admin: %d %s", status, raw)
	}
	var tasks gradingTasksResponse
	decodeInto(t, raw, &tasks)
	ids := make([]string, 0, len(tasks.Tasks))
	for _, task := range tasks.Tasks {
		ids = append(ids, task.ID)
	}
	return batch.ID, ids
}

// --------------------------------------------------------------------------- //

func TestCreateEchoesResolvedReadersAndZeroProgress(t *testing.T) {
	srv := newGradingTestServer(t)
	status, raw := do(t, srv, http.MethodPost, "/v1/grading/batches", keyAdmin, createBody(6))
	if status != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", status, raw)
	}
	var b gradingBatchJSON
	decodeInto(t, raw, &b)
	// `@service:<subject>` keeps a keyed principal's subject colon-free. A bare
	// name would be qualified to `bvbrc:<name>` — an identity the key never
	// authenticates as, and the reader would 404 on their own batch.
	if len(b.Readers) != 2 || b.Readers[0] != subjReaderA || b.Readers[1] != subjReaderB {
		t.Fatalf("readers must echo the RESOLVED subjects in label order: %v", b.Readers)
	}
	if b.Status != grading.StatusOpen || b.AdjudicatingAt != "" {
		t.Fatalf("a new batch is open with no adjudicating_at: %+v", b)
	}
	if b.TaskCount != 6 || len(b.Progress) != 2 {
		t.Fatalf("task_count/progress: %+v", b)
	}
	for _, p := range b.Progress {
		if p.Saved != 0 {
			t.Fatalf("a new batch has saved 0 for every reader: %+v", p)
		}
	}
	if b.Progress[0].Label != "A" || b.Progress[1].Label != "B" {
		t.Fatalf("labels are by position in readers: %+v", b.Progress)
	}
	if b.CreatedBy != subjAdmin {
		t.Fatalf("created_by = %q", b.CreatedBy)
	}
}

func TestEachReaderSeesTheirOwnSeededOrder(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, batchOrder := createBatch(t, srv)

	orderOf := func(key string) []string {
		status, raw := do(t, srv, http.MethodGet, "/v1/grading/batches/"+batchID+"/tasks", key, nil)
		if status != http.StatusOK {
			t.Fatalf("list tasks: %d %s", status, raw)
		}
		var resp gradingTasksResponse
		decodeInto(t, raw, &resp)
		ids := make([]string, 0, len(resp.Tasks))
		for _, task := range resp.Tasks {
			ids = append(ids, task.ID)
		}
		return ids
	}
	expect := func(readerIndex int) []string {
		out := make([]string, 0, len(batchOrder))
		for _, i := range grading.ReaderOrder(testSeed, readerIndex, len(batchOrder)) {
			out = append(out, batchOrder[i])
		}
		return out
	}
	a, b := orderOf(keyReaderA), orderOf(keyReaderB)
	if !equalStrings(a, expect(0)) {
		t.Errorf("reader A's order is not the contract's permutation:\n got %v\nwant %v", a, expect(0))
	}
	if !equalStrings(b, expect(1)) {
		t.Errorf("reader B's order is not the contract's permutation:\n got %v\nwant %v", b, expect(1))
	}
	if equalStrings(a, b) {
		t.Errorf("the two readers were shown the SAME order: %v", a)
	}
	// An admin who is not a reader gets batch order and no reader.
	status, raw := do(t, srv, http.MethodGet, "/v1/grading/batches/"+batchID+"/tasks", keyAdmin, nil)
	if status != http.StatusOK {
		t.Fatalf("admin list: %d %s", status, raw)
	}
	var adminResp gradingTasksResponse
	decodeInto(t, raw, &adminResp)
	if adminResp.Reader != nil {
		t.Errorf("an admin who is not a reader has no order of their own: %v", *adminResp.Reader)
	}
}

func TestAReaderNeverReceivesAnotherReadersRow(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, tasks := createBatch(t, srv)
	t0 := tasks[0]

	save := func(key, verdict, notes string) gradingVerdictJSON {
		status, raw := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/verdict", key,
			map[string]any{"verdict": verdict, "notes": notes})
		if status != http.StatusOK {
			t.Fatalf("save verdict: %d %s", status, raw)
		}
		var v gradingVerdictJSON
		decodeInto(t, raw, &v)
		return v
	}
	v1 := save(keyReaderA, "correct", "first pass")
	v2 := save(keyReaderA, "non-minimal", "second pass")
	if v1.Version != 1 || v2.Version != 2 {
		t.Fatalf("a re-save bumps version: %d then %d", v1.Version, v2.Version)
	}
	if v2.Reader != subjReaderA {
		t.Fatalf("the row names the CALLER, never a body field: %q", v2.Reader)
	}
	save(keyReaderB, "correct", "B's own reading")

	// Each reader's own task view carries their own row and nothing else.
	for key, want := range map[string]string{keyReaderA: "second pass", keyReaderB: "B's own reading"} {
		status, raw := do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0, key, nil)
		if status != http.StatusOK {
			t.Fatalf("get task: %d %s", status, raw)
		}
		var task gradingTaskJSON
		decodeInto(t, raw, &task)
		if task.Verdict == nil || task.Verdict.Notes != want {
			t.Fatalf("a reader must see their OWN row: %+v", task.Verdict)
		}
		var asMap map[string]any
		decodeInto(t, raw, &asMap)
		if _, present := asMap["reader_verdicts"]; present {
			t.Fatalf("a reader must never receive reader_verdicts")
		}
		if _, present := asMap["adjudication"]; present {
			t.Fatalf("a reader must never receive adjudication")
		}
	}

	// B naming A's row is the SAME 404 as naming a subject who is not a reader.
	for _, subject := range []string{subjReaderA, subjOutsider} {
		status, _ := do(t, srv, http.MethodGet,
			"/v1/grading/tasks/"+t0+"/verdicts/"+subject, keyReaderB, nil)
		if status != http.StatusNotFound {
			t.Errorf("B reading %q got %d, expected 404", subject, status)
		}
	}
	// A reading their own row is fine.
	status, raw := do(t, srv, http.MethodGet,
		"/v1/grading/tasks/"+t0+"/verdicts/"+subjReaderA, keyReaderA, nil)
	if status != http.StatusOK {
		t.Fatalf("A reading A's own row: %d %s", status, raw)
	}

	// Progress is counts, never verdicts.
	status, raw = do(t, srv, http.MethodGet, "/v1/grading/batches/"+batchID, keyReaderA, nil)
	if status != http.StatusOK {
		t.Fatalf("get batch: %d %s", status, raw)
	}
	if bytes.Contains(raw, []byte("second pass")) {
		t.Fatalf("the batch record leaked a verdict's notes: %s", raw)
	}
	var batch gradingBatchJSON
	decodeInto(t, raw, &batch)
	if batch.Progress[0].Saved != 1 || batch.Progress[1].Saved != 1 {
		t.Fatalf("progress counts: %+v", batch.Progress)
	}
}

func TestAnAdminIsNotExemptWhileTheReadIsOpen(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, tasks := createBatch(t, srv)
	t0 := tasks[0]
	if status, raw := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/verdict",
		keyReaderA, map[string]any{"verdict": "correct"}); status != http.StatusOK {
		t.Fatalf("A saves: %d %s", status, raw)
	}

	if status, _ := do(t, srv, http.MethodGet,
		"/v1/grading/tasks/"+t0+"/verdicts/"+subjReaderA, keyAdmin, nil); status != http.StatusNotFound {
		t.Errorf("an admin reading a reader's row while open must be 404, got %d", status)
	}
	if status, _ := do(t, srv, http.MethodGet,
		"/v1/grading/batches/"+batchID+"/export", keyAdmin, nil); status != http.StatusConflict {
		t.Errorf("exporting an OPEN batch must be 409, got %d", status)
	}
	if status, _ := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/adjudication",
		keyAdmin, map[string]any{"verdict": "correct"}); status != http.StatusConflict {
		t.Errorf("an adjudication on an OPEN batch must be 409, got %d", status)
	}
	// An admin who is not one of the readers has no row to write.
	if status, _ := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/verdict",
		keyAdmin, map[string]any{"verdict": "correct"}); status != http.StatusForbidden {
		t.Errorf("an admin who is not a reader must be 403 on PUT verdict, got %d", status)
	}
	// The admin's own task view carries neither field while the read is open.
	_, raw := do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0, keyAdmin, nil)
	var asMap map[string]any
	decodeInto(t, raw, &asMap)
	if _, present := asMap["reader_verdicts"]; present {
		t.Errorf("reader_verdicts must be ABSENT for an admin while the batch is open")
	}
	if _, present := asMap["adjudication"]; present {
		t.Errorf("adjudication must be ABSENT for an admin while the batch is open")
	}
}

func TestUnseenIsUnknown(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, tasks := createBatch(t, srv)
	t0 := tasks[0]

	// Every route under a batch the caller neither administers nor reads is a
	// 404 — never a 403, which would confirm the batch exists.
	probes := []struct {
		method, path string
		body         any
	}{
		{http.MethodGet, "/v1/grading/batches/" + batchID, nil},
		{http.MethodGet, "/v1/grading/batches/" + batchID + "/tasks", nil},
		{http.MethodGet, "/v1/grading/tasks/" + t0, nil},
		{http.MethodPut, "/v1/grading/tasks/" + t0 + "/verdict", map[string]any{"verdict": "correct"}},
		{http.MethodGet, "/v1/grading/tasks/" + t0 + "/verdicts/" + subjReaderA, nil},
		{http.MethodPost, "/v1/grading/batches/" + batchID + "/adjudicate", nil},
		{http.MethodGet, "/v1/grading/batches/" + batchID + "/export", nil},
		{http.MethodPut, "/v1/grading/tasks/" + t0 + "/adjudication", map[string]any{"verdict": "correct"}},
		{http.MethodDelete, "/v1/grading/batches/" + batchID, nil},
	}
	for _, p := range probes {
		status, raw := do(t, srv, p.method, p.path, keyOutsider, p.body)
		if status != http.StatusNotFound {
			t.Errorf("outsider %s %s got %d, expected 404: %s", p.method, p.path, status, raw)
		}
	}
	// And the batch never appears in an outsider's listing.
	_, raw := do(t, srv, http.MethodGet, "/v1/grading/batches", keyOutsider, nil)
	var listing gradingBatchesResponse
	decodeInto(t, raw, &listing)
	if len(listing.Batches) != 0 {
		t.Errorf("an outsider's listing must be empty, got %+v", listing.Batches)
	}
	// A reader's and an admin's listings do carry it.
	for _, key := range []string{keyReaderA, keyAdmin} {
		_, raw := do(t, srv, http.MethodGet, "/v1/grading/batches", key, nil)
		decodeInto(t, raw, &listing)
		if len(listing.Batches) != 1 || listing.Batches[0].ID != batchID {
			t.Errorf("listing for %s: %+v", key, listing.Batches)
		}
	}
}

func TestARoleRefusalIs403WhereTheCallerCanSeeTheBatch(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, tasks := createBatch(t, srv)
	t0 := tasks[0]

	// A reader can see the batch, so a 404 would be a lie and a 2xx a breach.
	refusals := []struct {
		method, path string
		body         any
	}{
		{http.MethodPost, "/v1/grading/batches/" + batchID + "/adjudicate", nil},
		{http.MethodGet, "/v1/grading/batches/" + batchID + "/export", nil},
		{http.MethodDelete, "/v1/grading/batches/" + batchID, nil},
		{http.MethodPut, "/v1/grading/tasks/" + t0 + "/adjudication", map[string]any{"verdict": "correct"}},
	}
	for _, p := range refusals {
		status, raw := do(t, srv, p.method, p.path, keyReaderA, p.body)
		if status != http.StatusForbidden {
			t.Errorf("reader %s %s got %d, expected 403: %s", p.method, p.path, status, raw)
		}
	}
	// Creating is stateless: no batch to hide, so 403 for any non-admin.
	for _, key := range []string{keyReaderA, keyOutsider} {
		if status, _ := do(t, srv, http.MethodPost, "/v1/grading/batches", key, createBody(1)); status != http.StatusForbidden {
			t.Errorf("a non-admin creating got %d, expected 403", status)
		}
	}
}

func TestAdjudicateFreezesRevealsAndUnlocks(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, tasks := createBatch(t, srv)
	t0 := tasks[0]
	for _, key := range []string{keyReaderA, keyReaderB} {
		if status, raw := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/verdict", key,
			map[string]any{"verdict": "correct", "notes": key}); status != http.StatusOK {
			t.Fatalf("save: %d %s", status, raw)
		}
	}

	status, raw := do(t, srv, http.MethodPost, "/v1/grading/batches/"+batchID+"/adjudicate", keyAdmin, nil)
	if status != http.StatusOK {
		t.Fatalf("adjudicate: %d %s", status, raw)
	}
	var batch gradingBatchJSON
	decodeInto(t, raw, &batch)
	if batch.Status != grading.StatusAdjudicating || batch.AdjudicatingAt == "" {
		t.Fatalf("adjudicate must stamp the batch: %+v", batch)
	}
	// Not idempotent: an accidental second click must not be a silent no-op.
	if status, _ := do(t, srv, http.MethodPost, "/v1/grading/batches/"+batchID+"/adjudicate", keyAdmin, nil); status != http.StatusConflict {
		t.Errorf("replaying adjudicate must be 409, got %d", status)
	}
	// Authorization is decided BEFORE state: a reader is still 403, not 409.
	if status, _ := do(t, srv, http.MethodPost, "/v1/grading/batches/"+batchID+"/adjudicate", keyReaderA, nil); status != http.StatusForbidden {
		t.Errorf("a reader adjudicating an adjudicating batch must still be 403, got %d", status)
	}
	// The readers' rows are frozen.
	if status, _ := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/verdict", keyReaderA,
		map[string]any{"verdict": "ambiguous"}); status != http.StatusConflict {
		t.Errorf("a reader's PUT after adjudication must be 409, got %d", status)
	}
	// …and visible to an admin, and to an admin only.
	if status, _ := do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0+"/verdicts/"+subjReaderA, keyAdmin, nil); status != http.StatusOK {
		t.Errorf("an admin must be able to read a reader's frozen row, got %d", status)
	}
	if status, _ := do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0+"/verdicts/"+subjReaderA, keyReaderB, nil); status != http.StatusNotFound {
		t.Errorf("B reading A's row is 404 even after adjudication, got %d", status)
	}

	// The admin's task view now carries both rows in LABEL order plus the
	// adjudication field, present and null until one is saved.
	_, raw = do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0, keyAdmin, nil)
	var asMap map[string]any
	decodeInto(t, raw, &asMap)
	adjField, present := asMap["adjudication"]
	if !present {
		t.Fatalf("adjudication must be PRESENT for an admin once the batch has left open")
	}
	if adjField != nil {
		t.Fatalf("adjudication must be null before one is saved, got %v", adjField)
	}
	var task gradingTaskJSON
	decodeInto(t, raw, &task)
	if task.ReaderVerdicts == nil || len(*task.ReaderVerdicts) != 2 {
		t.Fatalf("reader_verdicts: %+v", task.ReaderVerdicts)
	}
	if (*task.ReaderVerdicts)[0].Reader != subjReaderA || (*task.ReaderVerdicts)[1].Reader != subjReaderB {
		t.Fatalf("reader_verdicts must be in LABEL order: %+v", *task.ReaderVerdicts)
	}
	// A reader still receives neither field, whatever the status. Decoded into
	// a FRESH map: encoding/json merges into a non-nil one, so reusing asMap
	// here would carry the admin's keys over and assert nothing.
	_, raw = do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0, keyReaderA, nil)
	var readerMap map[string]any
	decodeInto(t, raw, &readerMap)
	if _, present := readerMap["reader_verdicts"]; present {
		t.Errorf("a reader must never receive reader_verdicts, even while adjudicating")
	}

	// The joint-read verdict is now writable, and it does not touch the rows.
	status, raw = do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/adjudication", keyAdmin,
		map[string]any{"verdict": "non-minimal", "notes": "joint read"})
	if status != http.StatusOK {
		t.Fatalf("adjudication: %d %s", status, raw)
	}
	var adj gradingAdjudicationJSON
	decodeInto(t, raw, &adj)
	if adj.Version != 1 || adj.AdjudicatedBy != subjAdmin {
		t.Fatalf("adjudication row: %+v", adj)
	}
	_, raw = do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0+"/verdicts/"+subjReaderA, keyAdmin, nil)
	var row gradingVerdictJSON
	decodeInto(t, raw, &row)
	if row.Verdict != "correct" || row.Version != 1 {
		t.Fatalf("adjudicating must NOT modify a reader's row (the pre-adjudication κ needs it): %+v", row)
	}
}

func TestExportIsTheScorersSheet(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, tasks := createBatch(t, srv)
	if status, _ := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+tasks[0]+"/verdict", keyReaderA,
		map[string]any{"verdict": "non-minimal", "notes": `he said "no"`}); status != http.StatusOK {
		t.Fatal("save failed")
	}
	if status, _ := do(t, srv, http.MethodPost, "/v1/grading/batches/"+batchID+"/adjudicate", keyAdmin, nil); status != http.StatusOK {
		t.Fatal("adjudicate failed")
	}
	status, raw := do(t, srv, http.MethodGet, "/v1/grading/batches/"+batchID+"/export", keyAdmin, nil)
	if status != http.StatusOK {
		t.Fatalf("export: %d %s", status, raw)
	}
	var ex gradingExportResponse
	decodeInto(t, raw, &ex)
	want := []string{"rdev_verdicts_A.csv", "rdev_verdicts_B.csv", "rdev_verdicts_ADJ.csv"}
	got := make([]string, 0, len(ex.CSV))
	for _, sheet := range ex.CSV {
		got = append(got, sheet.Filename)
	}
	if !equalStrings(got, want) {
		t.Fatalf("csv filenames: %v", got)
	}
	if ex.CSV[2].Reader != nil {
		t.Errorf("the adjudicated sheet has no reader")
	}
	// Every field double-quoted, `"` doubled, one row per task in batch order,
	// an empty verdict cell where nobody read — the scorer's "not yet read".
	header := "\"pair_id\",\"verdict\",\"notes\"\n"
	wantA := header +
		"\"pair-a\",\"non-minimal\",\"he said \"\"no\"\"\"\n" +
		"\"pair-b\",\"\",\"\"\n\"pair-c\",\"\",\"\"\n\"pair-d\",\"\",\"\"\n" +
		"\"pair-e\",\"\",\"\"\n\"pair-f\",\"\",\"\"\n"
	if ex.CSV[0].Content != wantA {
		t.Errorf("reader A's sheet:\n got %q\nwant %q", ex.CSV[0].Content, wantA)
	}
	if len(ex.Verdicts) != 1 || ex.Verdicts[0].Label != "A" || ex.Verdicts[0].Stratum != "model_positive" {
		t.Errorf("the JSON side carries what the CSV cannot: %+v", ex.Verdicts)
	}
}

func TestInvalidBodiesAre422AndChangeNothing(t *testing.T) {
	srv := newGradingTestServer(t)
	_, tasks := createBatch(t, srv)
	t0 := tasks[0]
	if status, _ := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/verdict", keyReaderA,
		map[string]any{"verdict": "correct"}); status != http.StatusOK {
		t.Fatal("baseline save failed")
	}

	bad := map[string]map[string]any{
		"verdict outside the vocabulary": {"verdict": "fine"},
		"a span judgement naming a span the task lacks": {
			"verdict":         "correct",
			"span_judgements": []map[string]any{{"set": 9, "span": 1, "judgement": "located"}},
		},
		"a judgement outside the vocabulary": {
			"verdict":         "correct",
			"span_judgements": []map[string]any{{"set": 1, "span": 1, "judgement": "maybe"}},
		},
		"an unknown extra question id": {
			"verdict":       "correct",
			"extra_answers": []map[string]any{{"id": "nope", "answer": "no"}},
		},
		// The body has NO field naming a reader; an unknown one is the
		// schemas' `additionalProperties: false`, i.e. a 422, not a silently
		// ignored field a client believes it set.
		"an unknown field in the body": {"verdict": "correct", "reader": subjReaderB},
	}
	for what, body := range bad {
		status, raw := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+t0+"/verdict", keyReaderA, body)
		if status != http.StatusUnprocessableEntity {
			t.Errorf("%s: expected 422, got %d: %s", what, status, raw)
		}
	}
	_, raw := do(t, srv, http.MethodGet, "/v1/grading/tasks/"+t0+"/verdicts/"+subjReaderA, keyReaderA, nil)
	var row gradingVerdictJSON
	decodeInto(t, raw, &row)
	if row.Version != 1 || row.Verdict != "correct" {
		t.Errorf("a refused body must not have changed the row: %+v", row)
	}
}

func TestCreateValidationRefusesAWholeBatch(t *testing.T) {
	srv := newGradingTestServer(t)
	mutate := func(f func(map[string]any)) map[string]any {
		body := createBody(2)
		f(body)
		return body
	}
	cases := map[string]map[string]any{
		"a duplicate pair_id": mutate(func(b map[string]any) {
			tasks := b["tasks"].([]map[string]any)
			tasks[1]["pair_id"] = tasks[0]["pair_id"]
		}),
		"a span outside its document": mutate(func(b map[string]any) {
			tasks := b["tasks"].([]map[string]any)
			claims := tasks[0]["claims"].([]map[string]any)
			claims[0]["spans"].([]map[string]any)[0]["last_sentence"] = 99
		}),
		"a span naming a unit the document lacks": mutate(func(b map[string]any) {
			tasks := b["tasks"].([]map[string]any)
			claims := tasks[0]["claims"].([]map[string]any)
			claims[0]["spans"].([]map[string]any)[0]["unit"] = 7
		}),
		"a rubric hash that is not 64 lowercase hex": mutate(func(b map[string]any) {
			b["rubric_sha256"] = "NOTAHASH"
		}),
		"a missing order_seed": mutate(func(b map[string]any) { delete(b, "order_seed") }),
		"a group-form reader": mutate(func(b map[string]any) {
			b["readers"] = []string{"@public", "@service:" + subjReaderB}
		}),
		"two readers that resolve to one subject": mutate(func(b map[string]any) {
			b["readers"] = []string{"@service:" + subjReaderA, "@service:" + subjReaderA}
		}),
		"an unknown field":    mutate(func(b map[string]any) { b["owner"] = "me" }),
		"an empty tasks list": mutate(func(b map[string]any) { b["tasks"] = []map[string]any{} }),
	}
	for what, body := range cases {
		status, raw := do(t, srv, http.MethodPost, "/v1/grading/batches", keyAdmin, body)
		if status != http.StatusUnprocessableEntity {
			t.Errorf("%s: expected 422, got %d: %s", what, status, raw)
		}
	}
	// Nothing was created by any of them.
	_, raw := do(t, srv, http.MethodGet, "/v1/grading/batches", keyAdmin, nil)
	var listing gradingBatchesResponse
	decodeInto(t, raw, &listing)
	if len(listing.Batches) != 0 {
		t.Fatalf("a refused create must leave nothing behind: %+v", listing.Batches)
	}
}

func TestDeleteRemovesEverythingUnderTheBatch(t *testing.T) {
	srv := newGradingTestServer(t)
	batchID, tasks := createBatch(t, srv)
	if status, _ := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+tasks[0]+"/verdict", keyReaderA,
		map[string]any{"verdict": "correct"}); status != http.StatusOK {
		t.Fatal("save failed")
	}
	if status, raw := do(t, srv, http.MethodDelete, "/v1/grading/batches/"+batchID, keyAdmin, nil); status != http.StatusNoContent {
		t.Fatalf("delete: %d %s", status, raw)
	}
	// Verified by listing, never by trusting the DELETE's status.
	_, raw := do(t, srv, http.MethodGet, "/v1/grading/batches", keyAdmin, nil)
	var listing gradingBatchesResponse
	decodeInto(t, raw, &listing)
	if len(listing.Batches) != 0 {
		t.Fatalf("the batch survived its delete: %+v", listing.Batches)
	}
	if status, _ := do(t, srv, http.MethodGet, "/v1/grading/tasks/"+tasks[0], keyAdmin, nil); status != http.StatusNotFound {
		t.Errorf("the batch's tasks must go with it, got %d", status)
	}
}

func TestExtraQuestionAnswersAreCheckedAgainstTheTask(t *testing.T) {
	srv := newGradingTestServer(t)
	body := createBody(1)
	tasks := body["tasks"].([]map[string]any)
	tasks[0]["claims"] = []map[string]any{}
	tasks[0]["extra_questions"] = []map[string]any{{
		"id": "other_passage", "text": "Does another passage answer it?", "answer_type": "yes-no",
	}}
	status, raw := do(t, srv, http.MethodPost, "/v1/grading/batches", keyAdmin, body)
	if status != http.StatusCreated {
		t.Fatalf("create: %d %s", status, raw)
	}
	var batch gradingBatchJSON
	decodeInto(t, raw, &batch)
	_, raw = do(t, srv, http.MethodGet, "/v1/grading/batches/"+batch.ID+"/tasks", keyReaderA, nil)
	var resp gradingTasksResponse
	decodeInto(t, raw, &resp)
	taskID := resp.Tasks[0].ID

	if status, _ := do(t, srv, http.MethodPut, "/v1/grading/tasks/"+taskID+"/verdict", keyReaderA,
		map[string]any{
			"verdict":       "correctly-none",
			"extra_answers": []map[string]any{{"id": "other_passage", "answer": "maybe"}},
		}); status != http.StatusUnprocessableEntity {
		t.Errorf("a non-yes/no answer to a yes-no question must be 422, got %d", status)
	}
	status, raw = do(t, srv, http.MethodPut, "/v1/grading/tasks/"+taskID+"/verdict", keyReaderA,
		map[string]any{
			"verdict":       "correctly-none",
			"extra_answers": []map[string]any{{"id": "other_passage", "answer": "no"}},
		})
	if status != http.StatusOK {
		t.Fatalf("save: %d %s", status, raw)
	}
	var v gradingVerdictJSON
	decodeInto(t, raw, &v)
	if len(v.ExtraAnswers) != 1 || v.ExtraAnswers[0].Answer != "no" {
		t.Errorf("extra answers: %+v", v.ExtraAnswers)
	}
	// A whole-row replace: an omitted list CLEARS it, it does not keep the
	// previous value.
	status, raw = do(t, srv, http.MethodPut, "/v1/grading/tasks/"+taskID+"/verdict", keyReaderA,
		map[string]any{"verdict": "correctly-none"})
	if status != http.StatusOK {
		t.Fatalf("re-save: %d %s", status, raw)
	}
	decodeInto(t, raw, &v)
	if len(v.ExtraAnswers) != 0 {
		t.Errorf("an omitted extra_answers must CLEAR the previous value: %+v", v.ExtraAnswers)
	}
}

func TestGradingRequiresACredentialOnAKeyedServer(t *testing.T) {
	srv := newGradingTestServer(t)
	if status, _ := do(t, srv, http.MethodGet, "/v1/grading/batches", "", nil); status != http.StatusUnauthorized {
		t.Errorf("no key on a keyed server must be 401, got %d", status)
	}
	if status, _ := do(t, srv, http.MethodGet, "/v1/grading/batches", "not-a-key", nil); status != http.StatusUnauthorized {
		t.Errorf("an unknown key must be 401, got %d", status)
	}
	// The probe a client uses to learn whether a server implements grading must
	// answer an empty list for a caller with no reads — never 403.
	status, raw := do(t, srv, http.MethodGet, "/v1/grading/batches", keyOutsider, nil)
	if status != http.StatusOK {
		t.Fatalf("expected 200 with an empty list, got %d: %s", status, raw)
	}
}

// A keyless server is the open dev/test path and must behave exactly as it did
// before internal/auth existed: nothing rejected, and the grading surface
// mounted (so the absence probe sees the endpoint rather than a 404).
func TestKeylessServerRejectsNothing(t *testing.T) {
	s := &Server{Auth: auth.New(nil, nil, nil, auth.RoleUser), Grading: grading.NewMemoryStore()}
	srv := httptest.NewServer(NewRouterWithServer(nil, s))
	t.Cleanup(srv.Close)
	for _, path := range []string{"/v1/collections", "/v1/documents", "/v1/grading/batches"} {
		if status, raw := do(t, srv, http.MethodGet, path, "", nil); status != http.StatusOK {
			t.Errorf("keyless GET %s: %d %s", path, status, raw)
		}
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
