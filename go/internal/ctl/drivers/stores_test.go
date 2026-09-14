package drivers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// The store drivers are tested against httptest servers rather than mocks,
// because what is being tested IS the wire: the exact path, the exact method,
// the exact body, and — the half a mock cannot check — what the driver does
// with a response that is a 200 and still says the operation failed.

// recording is one captured request.
type recording struct {
	method string
	path   string
	query  string
	body   string
	header http.Header
}

// stubStore is an httptest server that records every request and answers from
// a route table keyed "METHOD /path".
type stubStore struct {
	t      *testing.T
	srv    *httptest.Server
	routes map[string]func(w http.ResponseWriter, r *http.Request)
	got    []recording
}

func newStubStore(t *testing.T) *stubStore {
	t.Helper()
	s := &stubStore{t: t, routes: map[string]func(http.ResponseWriter, *http.Request){}}
	s.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		s.got = append(s.got, recording{
			method: r.Method, path: r.URL.Path, query: r.URL.RawQuery,
			body: string(body), header: r.Header.Clone(),
		})
		h, ok := s.routes[r.Method+" "+r.URL.Path]
		if !ok {
			// A path the driver must never reach is a test failure HERE, at
			// the server, rather than a confusing assertion later.
			s.t.Errorf("the driver called %s %s, which is not an allowlisted route", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusTeapot)
			return
		}
		h(w, r)
	}))
	t.Cleanup(s.srv.Close)
	return s
}

// on registers a JSON answer for one route.
func (s *stubStore) on(route string, status int, body string) {
	s.routes[route] = func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}
}

func (s *stubStore) url() string { return s.srv.URL }

// last is the most recent request, or a fatal error.
func (s *stubStore) last() recording {
	s.t.Helper()
	if len(s.got) == 0 {
		s.t.Fatal("the driver made no request")
	}
	return s.got[len(s.got)-1]
}

// realStores builds the real driver set over a temp root, which is all the
// HTTP drivers need.
func realStores(t *testing.T) *Real {
	t.Helper()
	return NewReal(RealOptions{ApprovedRoots: []string{t.TempDir()}})
}

// ---------------------------------------------------------------- qdrant

func TestQdrantCollectionsCountAndSnapshotSpeakTheDocumentedWire(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	s.on("GET /collections", 200, `{"result":{"collections":[{"name":"zeta"},{"name":"alpha"}]}}`)
	s.on("POST /collections/alpha/points/count", 200, `{"result":{"count":4242}}`)
	s.on("POST /collections/alpha/snapshots", 200, `{"result":{"name":"alpha-2026.snapshot"}}`)
	q := realStores(t).Qdrant()

	got, err := q.Collections(ctx, s.url())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(got, ",") != "alpha,zeta" {
		t.Errorf("Collections = %v, want them sorted", got)
	}

	n, err := q.Count(ctx, s.url(), "alpha")
	if err != nil {
		t.Fatal(err)
	}
	if n != 4242 {
		t.Errorf("Count = %d, want 4242", n)
	}
	// exact:true is the whole reason the ctl counts at all.
	if body := s.last().body; !strings.Contains(body, `"exact":true`) {
		t.Errorf("count body = %s, want exact:true", body)
	}

	name, err := q.Snapshot(ctx, s.url(), "alpha")
	if err != nil {
		t.Fatal(err)
	}
	if name != "alpha-2026.snapshot" {
		t.Errorf("Snapshot = %q", name)
	}
	if got := s.last().query; got != "wait=true" {
		t.Errorf("snapshot query = %q, want wait=true", got)
	}
}

func TestQdrantReadyFallsBackToCollectionsWhenReadyzIsAbsent(t *testing.T) {
	s := newStubStore(t)
	// The 404 an image older than qdrant 1.9 answers: not a store that is
	// unready, a store without the endpoint.
	s.on("GET /readyz", 404, `{"status":{"error":"Not found"}}`)
	s.on("GET /collections", 200, `{"result":{"collections":[]}}`)
	if err := realStores(t).Qdrant().Ready(context.Background(), s.url()); err != nil {
		t.Fatalf("Ready with a 404 /readyz and a good /collections = %v, want it to pass", err)
	}
}

func TestQdrantReadyFailsWhenBothProbesFail(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /readyz", 503, `not ready`)
	s.on("GET /collections", 503, `still not ready`)
	err := realStores(t).Qdrant().Ready(context.Background(), s.url())
	if err == nil || !strings.Contains(err.Error(), "fallback") {
		t.Fatalf("Ready = %v, want an error naming both probes", err)
	}
}

func TestQdrantRecoverRefusesALocationOutsideTheSnapshotDirectory(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	s.on("PUT /collections/alpha/snapshots/recover", 200, `{"result":true}`)
	q := realStores(t).Qdrant()

	for _, loc := range []string{
		"http://evil.example/snap",                  // an arbitrary URL
		"file:///etc/passwd",                        // a local file that is not a snapshot
		"file:///qdrant/snapshots/../../etc/passwd", // the prefix plus a traversal
	} {
		if err := q.Recover(ctx, s.url(), "alpha", loc); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Recover from %q = %v, want a refusal", loc, err)
		}
	}
	if len(s.got) != 0 {
		t.Fatalf("a refused location still reached the store: %v", s.got)
	}
	if err := q.Recover(ctx, s.url(), "alpha", "file:///qdrant/snapshots/alpha/a.snapshot"); err != nil {
		t.Fatalf("Recover from an approved location = %v", err)
	}
	if got := s.last().body; !strings.Contains(got, `"location":"file:///qdrant/snapshots/alpha/a.snapshot"`) {
		t.Errorf("recover body = %s", got)
	}
	if got := s.last().query; got != "wait=true" {
		t.Errorf("recover query = %q, want wait=true", got)
	}
}

func TestQdrantRefusesNamesItWillNotPutInAPath(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	q := realStores(t).Qdrant()
	for _, name := range []string{"", "../etc", "a/b", ".hidden", strings.Repeat("x", 129)} {
		if _, err := q.Count(ctx, s.url(), name); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Count of collection %q = %v, want a refusal", name, err)
		}
	}
	if err := q.DeleteSnapshot(ctx, s.url(), "alpha", "../../escape"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("DeleteSnapshot of %q = %v, want a refusal", "../../escape", err)
	}
	if len(s.got) != 0 {
		t.Fatalf("a refused name still reached the store: %v", s.got)
	}
}

// A snapshot name is the one value in this package that comes back FROM the
// store and then becomes a filesystem path, so it is validated on the way out
// exactly as one on the way in is.
func TestQdrantRefusesASnapshotNameTheStoreMadeUp(t *testing.T) {
	s := newStubStore(t)
	s.on("POST /collections/alpha/snapshots", 200, `{"result":{"name":"../../../etc/cron.d/x"}}`)
	_, err := realStores(t).Qdrant().Snapshot(context.Background(), s.url(), "alpha")
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Snapshot named %q = %v, want a refusal", "../../../etc/cron.d/x", err)
	}
}

// DeleteSnapshot is Snapshot's rollback, so a snapshot that is already gone is
// not a failure — a retried rollback must not turn one failed step into two.
func TestQdrantDeleteSnapshotTreatsAbsenceAsSuccess(t *testing.T) {
	s := newStubStore(t)
	s.on("DELETE /collections/alpha/snapshots/a.snapshot", 404, `{"status":{"error":"Not found"}}`)
	if err := realStores(t).Qdrant().DeleteSnapshot(context.Background(), s.url(), "alpha", "a.snapshot"); err != nil {
		t.Fatalf("DeleteSnapshot of an absent snapshot = %v, want success", err)
	}
}

// ---------------------------------------------------------------- elasticsearch

func TestESIndicesLeavesOutTheClustersOwnSystemIndices(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /_cat/indices", 200,
		`[{"index":"zeta-chunks"},{"index":".security-7"},{"index":"alpha-chunks"},{"index":".geoip_databases"}]`)
	got, err := realStores(t).Elasticsearch().Indices(context.Background(), s.url())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(got, ",") != "alpha-chunks,zeta-chunks" {
		t.Errorf("Indices = %v, want the tenant's two, sorted", got)
	}
	if q := s.last().query; !strings.Contains(q, "format=json") || !strings.Contains(q, "h=index") {
		t.Errorf("indices query = %q", q)
	}
}

func TestESSnapshotRefusesAPartialSnapshot(t *testing.T) {
	// The failure this driver exists to catch: HTTP 200, and a snapshot that
	// did not capture the data. A client that checked the status code alone
	// would record a bundle that cannot restore what its manifest claims.
	s := newStubStore(t)
	s.on("PUT /_snapshot/ctl-b1/snap", 200,
		`{"snapshot":{"snapshot":"snap","state":"PARTIAL","indices":["alpha-chunks"],"shards":{"total":4,"failed":1,"successful":3}}}`)
	err := realStores(t).Elasticsearch().Snapshot(context.Background(), s.url(), "ctl-b1", "snap")
	if err == nil {
		t.Fatal("a PARTIAL snapshot was accepted")
	}
	for _, want := range []string{"PARTIAL", "1 of 4"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error = %q, want it to name %q", err, want)
		}
	}
}

func TestESSnapshotAcceptsSuccessAndSendsTheDocumentedBody(t *testing.T) {
	s := newStubStore(t)
	s.on("PUT /_snapshot/ctl-b1/snap", 200,
		`{"snapshot":{"snapshot":"snap","state":"SUCCESS","indices":["alpha-chunks"],"shards":{"total":4,"failed":0,"successful":4}}}`)
	if err := realStores(t).Elasticsearch().Snapshot(context.Background(), s.url(), "ctl-b1", "snap"); err != nil {
		t.Fatal(err)
	}
	last := s.last()
	if last.query != "wait_for_completion=true" {
		t.Errorf("snapshot query = %q", last.query)
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(last.body), &body); err != nil {
		t.Fatal(err)
	}
	if body["indices"] != "*" || body["ignore_unavailable"] != false || body["include_global_state"] != false {
		t.Errorf("snapshot body = %s", last.body)
	}
}

func TestESRestoreRefusesWhenAShardDidNotRecover(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	s.on("POST /_snapshot/ctl-b1/snap/_restore", 200,
		`{"snapshot":{"snapshot":"snap","indices":["alpha-chunks"],"shards":{"total":4,"failed":2,"successful":2}}}`)
	err := realStores(t).Elasticsearch().Restore(ctx, s.url(), "ctl-b1", "snap", []string{"alpha-chunks"})
	if err == nil || !strings.Contains(err.Error(), "2 of 4") {
		t.Fatalf("Restore with failed shards = %v, want an error naming them", err)
	}
	if body := s.last().body; !strings.Contains(body, `"indices":"alpha-chunks"`) {
		t.Errorf("restore body = %s", body)
	}
}

// A repository the target cluster does not have is a 404, and it IS a refusal:
// no retry changes it.
func TestESRestoreOfAnUnknownRepoIsARefusal(t *testing.T) {
	s := newStubStore(t)
	s.on("POST /_snapshot/ctl-b1/snap/_restore", 404, `{"error":{"type":"repository_missing_exception"}}`)
	err := realStores(t).Elasticsearch().Restore(context.Background(), s.url(), "ctl-b1", "snap", nil)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Restore from an unregistered repo = %v, want a refusal", err)
	}
}

func TestESRegisterRepoRefusesALocationOutsideTheBoundSnapshotRoot(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	s.on("PUT /_snapshot/ctl-b1", 200, `{"acknowledged":true}`)
	es := realStores(t).Elasticsearch()
	for _, loc := range []string{
		"relative/path",
		"/var/lib/elsewhere/b1",
		"/usr/share/elasticsearch/snapshots/../../../etc",
	} {
		if err := es.RegisterRepo(ctx, s.url(), "ctl-b1", loc, false); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("RegisterRepo at %q = %v, want a refusal", loc, err)
		}
	}
	if len(s.got) != 0 {
		t.Fatalf("a refused location still reached the cluster: %v", s.got)
	}
	if err := es.RegisterRepo(ctx, s.url(), "ctl-b1", "/usr/share/elasticsearch/snapshots/b1", true); err != nil {
		t.Fatal(err)
	}
	if body := s.last().body; !strings.Contains(body, `"type":"fs"`) || !strings.Contains(body, `"readonly":true`) {
		t.Errorf("register body = %s", body)
	}
}

// UnregisterRepo is RegisterRepo's rollback, so an unknown repository is
// success.
func TestESUnregisterRepoTreatsAbsenceAsSuccess(t *testing.T) {
	s := newStubStore(t)
	s.on("DELETE /_snapshot/ctl-b1", 404, `{"error":{"type":"repository_missing_exception"}}`)
	if err := realStores(t).Elasticsearch().UnregisterRepo(context.Background(), s.url(), "ctl-b1"); err != nil {
		t.Fatalf("UnregisterRepo of an absent repo = %v, want success", err)
	}
}

func TestESSnapshotsListsARepositorySorted(t *testing.T) {
	// The verify leg of a backup re-registers the COPIED repo directory
	// read-only and lists it: a listing that names the snapshot is the proof
	// that the copy is a repository ES can read.
	s := newStubStore(t)
	s.on("GET /_snapshot/verify-b1/_all", 200,
		`{"snapshots":[{"snapshot":"20260914T100000Z-backup","state":"SUCCESS"},{"snapshot":"20260913T100000Z-backup","state":"SUCCESS"}]}`)
	got, err := realStores(t).Elasticsearch().Snapshots(context.Background(), s.url(), "verify-b1")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(got, ",") != "20260913T100000Z-backup,20260914T100000Z-backup" {
		t.Errorf("Snapshots = %v, want both, sorted", got)
	}
	if _, err := realStores(t).Elasticsearch().Snapshots(context.Background(), s.url(), "../etc"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Snapshots of a repo name outside the pattern = %v, want a refusal", err)
	}
}

func TestESReadyWaitsForYellow(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /_cluster/health", 200, `{"status":"yellow"}`)
	if err := realStores(t).Elasticsearch().Ready(context.Background(), s.url()); err != nil {
		t.Fatal(err)
	}
	q := s.last().query
	// Yellow, not green: a single-node tenant cluster never reaches green.
	if !strings.Contains(q, "wait_for_status=yellow") || !strings.Contains(q, "timeout=30s") {
		t.Errorf("health query = %q", q)
	}
}

func TestESCountReadsTheDocumentCount(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /alpha-chunks/_count", 200, `{"count":17,"_shards":{"total":1,"failed":0}}`)
	n, err := realStores(t).Elasticsearch().Count(context.Background(), s.url(), "alpha-chunks")
	if err != nil {
		t.Fatal(err)
	}
	if n != 17 {
		t.Errorf("Count = %d, want 17", n)
	}
}

// ---------------------------------------------------------------- tenant API

const testKey = "ctl-test-key-0123456789abcdef"

func TestTenantAPICarriesTheKeyInTheHeaderAndNowhereElse(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	s.on("GET /health", 200, `{"status":"ok"}`)
	s.on("GET /v1/version", 200, `{"version":"1.5.3","git_sha":"abc"}`)
	s.on("GET /v1/collections", 200, `{"collections":[{"id":"zeta"},{"id":"alpha"}],"default":"alpha"}`)
	api := realStores(t).TenantAPI()

	if err := api.Health(ctx, s.url()); err != nil {
		t.Fatal(err)
	}
	// /health is the UNAUTHENTICATED probe: sending a credential to it would
	// put one on a route that does not need it.
	if got := s.last().header.Get("X-API-Key"); got != "" {
		t.Errorf("/health carried a key: %q", got)
	}

	v, err := api.Version(ctx, s.url(), testKey)
	if err != nil {
		t.Fatal(err)
	}
	if v["version"] != "1.5.3" {
		t.Errorf("Version = %v", v)
	}
	if got := s.last().header.Get("X-API-Key"); got != testKey {
		t.Errorf("/v1/version key header = %q", got)
	}
	if strings.Contains(s.last().query, testKey) || strings.Contains(s.last().path, testKey) {
		t.Error("the key reached the URL, where it would land in the tenant's access log")
	}

	cols, err := api.Collections(ctx, s.url(), testKey)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(cols, ",") != "alpha,zeta" {
		t.Errorf("Collections = %v, want the ids sorted", cols)
	}
	if got := s.last().query; got != "counts=false" {
		t.Errorf("collections query = %q, want counts=false", got)
	}
}

// A tenant that echoes the credential it rejected must not put it in a step
// log. The driver scrubs its own errors rather than relying on the engine's
// redactor, which is seeded from files a just-minted key is not in yet.
func TestTenantAPIKeepsTheKeyOutOfItsErrors(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /v1/version", 401, fmt.Sprintf(`{"detail":"invalid api key %s"}`, testKey))
	_, err := realStores(t).TenantAPI().Version(context.Background(), s.url(), testKey)
	if err == nil {
		t.Fatal("a 401 was accepted")
	}
	if strings.Contains(err.Error(), testKey) {
		t.Fatalf("the error carries the credential: %q", err)
	}
	if !strings.Contains(err.Error(), "redacted") {
		t.Errorf("error = %q, want it to say the key was redacted", err)
	}
}

func TestTenantAPIDeepHealthNamesTheFailingDependency(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /v1/health/deep", 503,
		`{"status":"degraded","checks":[{"name":"vector","ok":false,"detail":"connection refused"},{"name":"text","ok":true}]}`)
	err := realStores(t).TenantAPI().DeepHealth(context.Background(), s.url(), testKey)
	if err == nil {
		t.Fatal("a degraded deep health was accepted")
	}
	// "the API is up and its vector store is not" is a different operator
	// action from "the API is down", and the status code alone does not say.
	_, summary, ok := strings.Cut(err.Error(), "failing checks: ")
	if !ok {
		t.Fatalf("error = %q, want it to summarise the failing checks", err)
	}
	if summary != "vector (connection refused)" {
		t.Errorf("summary = %q, want only the check that failed", summary)
	}
}

func TestTenantAPIServiceAccountUsesTheContractBody(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	s.on("POST /v1/admin/service-accounts", 201, `{"subject":"ingestor","active":true}`)
	s.on("POST /v1/admin/service-accounts/ingestor/disable", 204, ``)
	s.on("POST /v1/admin/service-accounts/ingestor/enable", 204, ``)
	api := realStores(t).TenantAPI()

	if err := api.ServiceAccount(ctx, s.url(), testKey, "ingestor", "user", "nightly ingest", "create"); err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(s.last().body), &body); err != nil {
		t.Fatal(err)
	}
	// The tenant's model is extra="forbid" and knows nothing of a role, so a
	// role field here would be a 422 on every real tenant.
	if _, ok := body["role"]; ok {
		t.Errorf("the create body carries a role the tenant API forbids: %s", s.last().body)
	}
	if body["subject"] != "ingestor" || body["purpose"] != "nightly ingest" {
		t.Errorf("create body = %s", s.last().body)
	}
	for _, action := range []string{"disable", "enable"} {
		if err := api.ServiceAccount(ctx, s.url(), testKey, "ingestor", "user", "", action); err != nil {
			t.Fatalf("%s = %v", action, err)
		}
	}
}

func TestTenantAPIServiceAccountRefusesUnknownActionsAndSubjects(t *testing.T) {
	ctx := context.Background()
	s := newStubStore(t)
	api := realStores(t).TenantAPI()
	if err := api.ServiceAccount(ctx, s.url(), testKey, "ingestor", "user", "", "delete"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("action delete = %v, want a refusal", err)
	}
	// A subject is the only caller string this driver ever puts in a path.
	for _, subject := range []string{"../admin", "a/b", "", "has:colon", strings.Repeat("s", 65)} {
		if err := api.ServiceAccount(ctx, s.url(), testKey, subject, "user", "", "disable"); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("subject %q = %v, want a refusal", subject, err)
		}
	}
	if len(s.got) != 0 {
		t.Fatalf("a refused call still reached the tenant: %v", s.got)
	}
}

// The 409 the API answers is a collision with a HUMAN account, which is a
// privilege event it refuses on purpose. Reading it as "already there" would
// report success for an account the ctl does not own.
func TestTenantAPIServiceAccountDoesNotSwallowAHumanAccountCollision(t *testing.T) {
	s := newStubStore(t)
	s.on("POST /v1/admin/service-accounts", 409,
		`{"detail":"'ingestor' already exists as a 'user' account; converting a real user row into a service account is a privilege event and is refused"}`)
	err := realStores(t).TenantAPI().ServiceAccount(context.Background(), s.url(), testKey, "ingestor", "user", "", "create")
	if err == nil {
		t.Fatal("a collision with a human account was reported as success")
	}
}

func TestTenantAPIServiceAccountIsIdempotentOnAnExistingServiceAccount(t *testing.T) {
	s := newStubStore(t)
	s.on("POST /v1/admin/service-accounts", 409,
		`{"detail":"'ingestor' already exists as a 'service' account"}`)
	if err := realStores(t).TenantAPI().ServiceAccount(context.Background(), s.url(), testKey, "ingestor", "user", "", "create"); err != nil {
		t.Fatalf("re-registering an existing service account = %v, want success", err)
	}
}

// ---------------------------------------------------------------- client

func TestStoreClientRefusesEveryNonLoopbackOrCredentialBearingBase(t *testing.T) {
	ctx := context.Background()
	q := realStores(t).Qdrant()
	for _, base := range []string{
		"",
		"http://10.0.0.5:6333",           // routable
		"https://127.0.0.1:6333",         // not http
		"http://user:pw@127.0.0.1:6333",  // userinfo
		"http://127.0.0.1",               // no port
		"http://127.0.0.1:6333/prefix",   // a path the ctl did not build
		"http://127.0.0.1:6333?x=1",      // a query the ctl did not build
		"http://qdrant.internal:6333",    // a name that is not loopback
		"http://127.0.0.1.evil.com:6333", // a name that only looks loopback
	} {
		if _, err := q.Collections(ctx, base); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Collections against %q = %v, want a refusal", base, err)
		}
	}
}

// The tenant API is the one surface the ctl presents a credential to, so its
// origin rule is stricter than a store's: ::1 is a loopback address and is
// still not one of the two spellings a tenant origin may have.
func TestTenantOriginIsStricterThanAStoreOrigin(t *testing.T) {
	if _, err := checkOrigin("http://[::1]:24040", storeOrigin); err != nil {
		t.Errorf("::1 as a STORE origin = %v, want it accepted", err)
	}
	if _, err := checkOrigin("http://[::1]:24040", tenantOrigin); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("::1 as a TENANT origin = %v, want a refusal", err)
	}
}

func TestStoreClientRefusesToFollowARedirect(t *testing.T) {
	// Every check this package makes is on the URL the ctl BUILT. Following a
	// Location header would hand the next request's destination to the
	// service, and with it any credential the first one carried.
	elsewhere := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("the driver followed a redirect")
		_, _ = io.WriteString(w, `{"result":{"collections":[]}}`)
	}))
	defer elsewhere.Close()
	s := newStubStore(t)
	s.routes["GET /collections"] = func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, elsewhere.URL+"/collections", http.StatusFound)
	}
	_, err := realStores(t).Qdrant().Collections(context.Background(), s.url())
	if err == nil || !strings.Contains(err.Error(), "redirect") {
		t.Fatalf("Collections against a redirecting store = %v, want a refusal naming the redirect", err)
	}
	// A refusal, not a failure: the API layer maps the two to different
	// statuses, so the sentinel has to survive the client's error wrapping.
	if !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("the redirect refusal lost its jobs.ErrRefused: %v", err)
	}
}

func TestStoreClientCapsTheResponseBody(t *testing.T) {
	s := newStubStore(t)
	s.routes["GET /collections"] = func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		// A store answering with more than the ctl ever asked for is how one
		// bad response becomes a daemon-wide outage.
		for written := 0; written <= maxBodyBytes; written += 1 << 16 {
			if _, err := w.Write(make([]byte, 1<<16)); err != nil {
				return
			}
		}
	}
	_, err := realStores(t).Qdrant().Collections(context.Background(), s.url())
	if err == nil || !strings.Contains(err.Error(), "cap") {
		t.Fatalf("Collections against an oversized response = %v, want the cap to refuse it", err)
	}
}

func TestStoreErrorsQuoteTheStoreButNotAllOfIt(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /alpha/_count", 500, strings.Repeat("A", 4096))
	_, err := realStores(t).Elasticsearch().Count(context.Background(), s.url(), "alpha")
	if err == nil {
		t.Fatal("a 500 was accepted")
	}
	if !strings.Contains(err.Error(), "HTTP 500") {
		t.Errorf("error = %q, want it to name the status", err)
	}
	if len(err.Error()) > 2*errBodyBytes {
		t.Errorf("error is %d bytes; a step log is not a transcript", len(err.Error()))
	}
}

// The redactor the engine supplies is applied to whatever a store says back,
// because a store that echoes a value out of a tenant's environment would
// otherwise put it in a step log.
func TestStoreErrorsGoThroughTheConfiguredRedactor(t *testing.T) {
	s := newStubStore(t)
	s.on("GET /alpha/_count", 500, `connection to postgresql://u:hunter2@localhost/x failed`)
	d := NewReal(RealOptions{
		ApprovedRoots: []string{t.TempDir()},
		Redact:        func(in string) string { return strings.ReplaceAll(in, "hunter2", "<redacted>") },
	})
	_, err := d.Elasticsearch().Count(context.Background(), s.url(), "alpha")
	if err == nil || strings.Contains(err.Error(), "hunter2") {
		t.Fatalf("error = %v, want the redactor applied", err)
	}
}
