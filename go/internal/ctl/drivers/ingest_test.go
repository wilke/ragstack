package drivers

// The ingest pair is the only WRITE into a tenant's corpus this driver can
// make, and the thing worth testing about it is not the wire — it is the
// refusal. `ragstack-ctl selftest` is its one caller; every other tenant on the
// host must be unreachable through it, and that has to hold for a caller who
// does not know the rule exists.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// newSandboxStore is newStubStore on a port inside the SELFTEST range, which
// is what the ingest routes require. The port is found by trying the range
// rather than hard-coded: 26000 may well be taken on a host that is running a
// selftest, and a test that skipped for that reason would be a test that
// stopped covering the rule exactly when the rule was in use.
func newSandboxStore(t *testing.T) *stubStore {
	t.Helper()
	s := &stubStore{t: t, routes: map[string]func(http.ResponseWriter, *http.Request){}}
	s.srv = httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		s.got = append(s.got, recording{
			method: r.Method, path: r.URL.Path, query: r.URL.RawQuery,
			body: string(body), header: r.Header.Clone(),
		})
		h, ok := s.routes[r.Method+" "+r.URL.Path]
		if !ok {
			s.t.Errorf("the driver called %s %s, which is not an allowlisted route", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusTeapot)
			return
		}
		h(w, r)
	}))
	for port := paths.SelftestBase; port <= paths.SelftestEnd; port++ {
		ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
		if err != nil {
			continue
		}
		_ = s.srv.Listener.Close()
		s.srv.Listener = ln
		s.srv.Start()
		t.Cleanup(s.srv.Close)
		return s
	}
	t.Fatalf("no free port in the sandbox range %d–%d to listen on", paths.SelftestBase, paths.SelftestEnd)
	return nil
}

func TestTenantAPIIngestSpeaksTheDocumentedWireOnASandboxPort(t *testing.T) {
	ctx := context.Background()
	s := newSandboxStore(t)
	s.on("POST /v1/ingest", 200, `{"job_id":"ing-7","status":"accepted"}`)
	s.on("GET /v1/ingest/ing-7", 200, `{"job_id":"ing-7","status":"completed"}`)
	api := realStores(t).TenantAPI()

	id, err := api.Ingest(ctx, s.url(), testKey, "/rag/documents/test_api.md")
	if err != nil {
		t.Fatal(err)
	}
	if id != "ing-7" {
		t.Errorf("Ingest = %q, want the tenant's own job id", id)
	}
	last := s.last()
	if last.path != "/v1/ingest" || last.method != http.MethodPost {
		t.Errorf("%s %s, want POST /v1/ingest", last.method, last.path)
	}
	// The contract's IngestRequest is `{source}` — a SERVER-SIDE path.
	if last.body != `{"source":"/rag/documents/test_api.md"}` {
		t.Errorf("ingest body = %s", last.body)
	}
	if got := last.header.Get("X-API-Key"); got != testKey {
		t.Errorf("ingest key header = %q", got)
	}
	if strings.Contains(last.query, testKey) || strings.Contains(last.path, testKey) {
		t.Error("the key reached the URL, where it would land in the tenant's access log")
	}

	state, err := api.IngestStatus(ctx, s.url(), testKey, "ing-7")
	if err != nil {
		t.Fatal(err)
	}
	if state != "completed" {
		t.Errorf("IngestStatus = %q", state)
	}
}

// An unknown job id is answered 200 with status "unknown". That is a FACT the
// poller acts on, not a failure of the call: a driver that turned it into an
// error would make a selftest that polled a moment too early report the tenant
// as broken.
func TestTenantAPIIngestStatusPassesUnknownThrough(t *testing.T) {
	s := newSandboxStore(t)
	s.on("GET /v1/ingest/ing-9", 200, `{"job_id":"ing-9","status":"unknown"}`)
	state, err := realStores(t).TenantAPI().IngestStatus(context.Background(), s.url(), testKey, "ing-9")
	if err != nil {
		t.Fatalf("IngestStatus of an unknown id = %v, want the state and no error", err)
	}
	if state != "unknown" {
		t.Errorf("state = %q, want unknown", state)
	}
}

// The rule: a tenant outside the sandbox range is unreachable through these
// two methods, and the refusal happens BEFORE the request — the server must
// see nothing, or the key has already travelled.
func TestTenantAPIIngestRefusesEveryNonSandboxOrigin(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t) // an ordinary ephemeral port: not a sandbox block
	s.routes["POST /v1/ingest"] = func(w http.ResponseWriter, _ *http.Request) {
		t.Error("the driver ingested into a tenant outside the sandbox range")
		w.WriteHeader(http.StatusOK)
	}
	api := realStores(t).TenantAPI()

	if _, err := api.Ingest(ctx, s.url(), testKey, "/rag/documents/test_api.md"); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Ingest against %s = %v, want a jobs.ErrRefused", s.url(), err)
	} else if !strings.Contains(err.Error(), "SANDBOX") {
		t.Errorf("the refusal does not say why: %v", err)
	}
	if _, err := api.IngestStatus(ctx, s.url(), testKey, "ing-1"); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("IngestStatus against %s = %v, want a jobs.ErrRefused", s.url(), err)
	}
	if len(s.got) != 0 {
		t.Fatalf("the driver sent %d request(s) to a non-sandbox tenant before refusing", len(s.got))
	}
}

// The values the ctl interpolates are checked before they are sent, on the
// same principle as the service-account subject: what the ctl will SEND is
// narrower than what the tenant would accept.
func TestTenantAPIIngestRefusesValuesItWillNotPutOnTheWire(t *testing.T) {
	ctx := context.Background()
	s := newSandboxStore(t)
	api := realStores(t).TenantAPI()

	for _, path := range []string{"documents/test_api.md", "/rag/doc uments/x.md", "/rag/../etc/shadow"} {
		if _, err := api.Ingest(ctx, s.url(), testKey, path); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Ingest(%q) = %v, want a refusal", path, err)
		}
	}
	if _, err := api.Ingest(ctx, s.url(), "", "/rag/documents/test_api.md"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Ingest with no key = %v, want a refusal", err)
	}
	if _, err := api.IngestStatus(ctx, s.url(), testKey, "../../v1/collections"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("IngestStatus with a traversing id = %v, want a refusal", err)
	}
	if len(s.got) != 0 {
		t.Fatalf("the driver sent %d request(s) built from a value it had not checked", len(s.got))
	}
}

// A tenant that accepts an ingest and names no job is an ingest nothing can
// poll. Reporting success for it would make the selftest wait out its whole
// timeout on a job that never existed.
func TestTenantAPIIngestRefusesAnAcceptanceWithNoJobID(t *testing.T) {
	s := newSandboxStore(t)
	s.on("POST /v1/ingest", 200, `{"status":"accepted"}`)
	_, err := realStores(t).TenantAPI().Ingest(context.Background(), s.url(), testKey, "/rag/documents/test_api.md")
	if err == nil || !strings.Contains(err.Error(), "job_id") {
		t.Fatalf("Ingest with no job_id = %v, want an error naming job_id", err)
	}
}
