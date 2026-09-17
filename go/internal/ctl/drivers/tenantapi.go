package drivers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// RealTenantAPI is the ctl's client to a tenant's OWN API, and it is an exact
// allowlist rather than a general-purpose HTTP client.
//
// The reason is the credential. Every other driver here talks to a store on a
// loopback port; this one presents a tenant's bootstrap ADMIN key, minted
// minutes earlier by `tenant create` and held in memory for the length of the
// job. A client that could be pointed at an arbitrary path is a client that
// can be made to send that key somewhere the ctl did not intend — so every
// method takes a fixed route out of the table below, and the only
// caller-supplied text that ever reaches a URL is a service-account subject or
// an ingest job id that matched a path-safe pattern first.
//
// Two of the routes — the ingest pair — are refused outright unless the origin
// is a SANDBOX port: see the ingest section at the bottom of this file.
//
// The key travels in the X-API-Key HEADER and nowhere else: never a query
// parameter (they land in the tenant's access log), never a path, and never an
// error string — errors from this driver are scrubbed of it before they are
// returned, on top of whatever redactor the engine applies afterwards.
type RealTenantAPI struct{ h *httpStores }

var _ jobs.TenantAPI = (*RealTenantAPI)(nil)

// tenantSubjectRe is the service-account subject rule. It is narrower than the
// API's own (which allows up to 128 characters and only forbids the ones that
// break routing) because this value is interpolated into a path by the ctl:
// the API's rule is what it will ACCEPT, this is what the ctl will SEND.
var tenantSubjectRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

// route is one allowlisted call. The table is the surface: a path that is not
// here cannot be reached from this driver, and neither can a method against a
// path that is.
type route struct {
	method string
	path   string
	// key is whether the call carries X-API-Key.
	key bool
	// ok are the acceptable statuses; empty means {200}.
	ok []int
}

// routes are every call the ctl makes to a tenant. The service-account routes
// carry a %s that ONLY a validated subject is ever substituted into.
var routes = map[string]route{
	"health":     {method: http.MethodGet, path: "/health", key: false},
	"version":    {method: http.MethodGet, path: "/v1/version", key: true},
	"deephealth": {method: http.MethodGet, path: "/v1/health/deep", key: true},
	"collections": {
		method: http.MethodGet, path: "/v1/collections", key: true,
	},
	"sa-create": {
		method: http.MethodPost, path: "/v1/admin/service-accounts", key: true,
		// 201 is the documented answer (and is also what a RE-registration
		// returns, unchanged — the API makes the call idempotent itself); 200
		// is accepted because an older tenant build answered it.
		ok: []int{http.StatusCreated, http.StatusOK},
	},
	"sa-disable": {
		method: http.MethodPost, path: "/v1/admin/service-accounts/%s/disable", key: true,
		ok: []int{http.StatusNoContent, http.StatusOK},
	},
	"sa-enable": {
		method: http.MethodPost, path: "/v1/admin/service-accounts/%s/enable", key: true,
		ok: []int{http.StatusNoContent, http.StatusOK},
	},
	"ingest": {
		method: http.MethodPost, path: "/v1/ingest", key: true,
		// The contract documents 200; 202 is accepted as well because the
		// upload sibling of this route answers that one.
		ok: []int{http.StatusOK, http.StatusAccepted},
	},
	"ingest-status": {method: http.MethodGet, path: "/v1/ingest/%s", key: true},
	// The admin ingest-job listing. A handover's release reads it to refuse
	// over an ingest that is still writing; nothing else calls it.
	"jobs": {method: http.MethodGet, path: "/v1/jobs", key: true},
}

// tenantJobIDRe is the ingest job id rule. Same reason as tenantSubjectRe: the
// value is interpolated into a path by the ctl, so what the ctl will SEND is
// narrower than what a tenant might answer with.
var tenantJobIDRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)

// call runs one allowlisted route. subject is empty unless the route has a
// placeholder, and reaches this function already validated.
func (a *RealTenantAPI) call(ctx context.Context, origin, apiKey, name, subject string, body any, out any) error {
	r, ok := routes[name]
	if !ok {
		// Unreachable by construction: every caller passes a literal. It is
		// here so that a future one that does not fails loudly rather than
		// sending a request to "".
		return fmt.Errorf("%w: %q is not an allowlisted tenant API call", jobs.ErrRefused, name)
	}
	path := r.path
	if strings.Contains(path, "%s") {
		path = fmt.Sprintf(path, url.PathEscape(subject))
	}
	req := request{method: r.method, path: path, body: body, okStatus: r.ok, timeout: listTimeout}
	if r.key {
		if apiKey == "" {
			return fmt.Errorf("%w: %s needs an API key and none was supplied", jobs.ErrRefused, path)
		}
		req.headers = map[string]string{"X-API-Key": apiKey}
	}
	err := a.h.doJSON(ctx, origin, tenantOrigin, req, out)
	if err != nil {
		return scrub(err, apiKey)
	}
	return nil
}

// scrub removes the key from an error before it leaves this package.
//
// The ctl never PUTS the key in a message, so this only fires when the tenant
// echoes it back — a 401 body quoting the credential it rejected, say. It does
// not rely on the engine's redactor: that one is seeded from the tenant's
// secrets.env, and a key minted thirty seconds ago by `tenant create` is not in
// any file yet.
func scrub(err error, apiKey string) error {
	if err == nil || apiKey == "" {
		return err
	}
	msg := err.Error()
	if !strings.Contains(msg, apiKey) {
		return err
	}
	// `%w`, wrapping the ORIGINAL: flattening it to a string dropped whatever
	// sentinel it carried, and those sentinels are what the API layer maps to
	// statuses (a jobs.ErrRefused became a 500). The message is the redacted
	// one; the chain is the real one, and errors.Is keeps working.
	return fmt.Errorf("%s (%w)", strings.ReplaceAll(msg, apiKey, "<redacted api key>"), redactedCause{err, apiKey})
}

// redactedCause carries the original error's CHAIN without its text.
//
// It exists because `scrub` has two jobs that pull in opposite directions: the
// message must not contain the credential, and `errors.Is` must still find
// whatever sentinel the original wrapped. Wrapping the original directly would
// reprint the un-redacted message; this wrapper prints the redaction and
// unwraps to the original.
type redactedCause struct {
	err    error
	apiKey string
}

func (r redactedCause) Error() string {
	return strings.ReplaceAll(r.err.Error(), r.apiKey, "<redacted api key>")
}

func (r redactedCause) Unwrap() error { return r.err }

// Health is the unauthenticated liveness probe.
func (a *RealTenantAPI) Health(ctx context.Context, origin string) error {
	return a.call(ctx, origin, "", "health", "", nil, nil)
}

// Version is the post-create proof that the API which answered is the artifact
// the ctl checked out.
func (a *RealTenantAPI) Version(ctx context.Context, origin, apiKey string) (map[string]any, error) {
	var out map[string]any
	if err := a.call(ctx, origin, apiKey, "version", "", nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// DeepHealth is the tenant's own verdict on every store it was configured
// with.
//
// A non-200 is an error that carries the tenant's per-dependency verdicts
// rather than only the status code, because "the API is up and its vector
// store is not" is a different operator action from "the API is down", and the
// status code alone does not tell them apart.
func (a *RealTenantAPI) DeepHealth(ctx context.Context, origin, apiKey string) error {
	if apiKey == "" {
		return fmt.Errorf("%w: /v1/health/deep needs an API key and none was supplied", jobs.ErrRefused)
	}
	// h.do rather than call(): the BODY of a failing deep-health response is
	// the useful half, and doJSON does not decode one.
	data, err := a.h.do(ctx, origin, tenantOrigin, request{
		method:  http.MethodGet,
		path:    routes["deephealth"].path,
		headers: map[string]string{"X-API-Key": apiKey},
		timeout: listTimeout,
	})
	if err == nil {
		return nil
	}
	err = scrub(err, apiKey)
	if detail := unhealthy(data); detail != "" {
		return fmt.Errorf("%v: %s", err, detail)
	}
	return err
}

// unhealthy renders the failing checks of a DeepHealthResponse as
// "overall; name (detail)" — the cluster's own words about which dependency is
// down. An unrecognisable body yields "" and the caller keeps the plain status
// error: inventing structure that is not there would be worse than the status
// code on its own.
func unhealthy(data []byte) string {
	var body struct {
		Status string `json:"status"`
		Checks []struct {
			Name   string `json:"name"`
			OK     bool   `json:"ok"`
			Detail string `json:"detail"`
		} `json:"checks"`
	}
	if json.Unmarshal(data, &body) != nil {
		return ""
	}
	var bad []string
	for _, c := range body.Checks {
		if c.OK {
			continue
		}
		if c.Detail != "" {
			bad = append(bad, c.Name+" ("+c.Detail+")")
			continue
		}
		bad = append(bad, c.Name)
	}
	sort.Strings(bad)
	switch {
	case body.Status == "" && len(bad) == 0:
		return ""
	case len(bad) == 0:
		return "the tenant reports status " + body.Status
	default:
		return "failing checks: " + strings.Join(bad, ", ")
	}
}

// Collections is the inventory a restore compares against the bundle manifest.
//
// It returns the collection IDS, not the labels: the id is what /v1/query
// selects with, what the manifest records, and what a restore has to find
// again on the new tenant. Labels are human text and two collections may share
// one.
func (a *RealTenantAPI) Collections(ctx context.Context, origin, apiKey string) ([]string, error) {
	var body struct {
		Collections []struct {
			ID string `json:"id"`
		} `json:"collections"`
	}
	req := request{
		method: http.MethodGet, path: routes["collections"].path,
		// counts=false: the inventory is what is being compared, and the
		// per-collection chunk counts cost a query against every store.
		query:   url.Values{"counts": []string{"false"}},
		headers: map[string]string{"X-API-Key": apiKey},
		timeout: listTimeout,
	}
	if apiKey == "" {
		return nil, fmt.Errorf("%w: /v1/collections needs an API key and none was supplied", jobs.ErrRefused)
	}
	if err := a.h.doJSON(ctx, origin, tenantOrigin, req, &body); err != nil {
		return nil, scrub(err, apiKey)
	}
	out := make([]string, 0, len(body.Collections))
	for _, c := range body.Collections {
		if c.ID != "" {
			out = append(out, c.ID)
		}
	}
	sort.Strings(out)
	return out, nil
}

// ServiceAccount creates, disables or enables one service account.
//
// role is NOT on the wire. The tenant's POST body is `extra="forbid"` and
// carries `subject` and `purpose` only — a role field would be a 422 — because
// a service account's role comes from API_KEY_ROLES in the tenant's
// environment, which this API deliberately cannot write. The parameter stays in
// the seam because the REGISTRY row records which role the operator minted the
// key with, and the two have to agree; the ctl writes that half.
func (a *RealTenantAPI) ServiceAccount(ctx context.Context, origin, apiKey, subject, _ /* role */, purpose, action string) error {
	if !tenantSubjectRe.MatchString(subject) {
		return fmt.Errorf("%w: %q is not a service-account subject the ctl will put in a path (want %s)",
			jobs.ErrRefused, subject, tenantSubjectRe)
	}
	switch action {
	case "create":
		err := a.call(ctx, origin, apiKey, "sa-create", "", map[string]any{
			"subject": subject,
			"purpose": purpose,
		}, nil)
		if err != nil && statusOf(err) == http.StatusConflict && alreadyAService(err) {
			// Idempotency, narrowly. This tenant build answers 201 to a
			// re-registration, so this branch only fires on a build that
			// answers 409 — and only when the body says the EXISTING row is
			// itself a service account. The other 409 on this route is a
			// collision with a HUMAN account, which is a privilege event the
			// API refuses on purpose, and swallowing it as "already there"
			// would report success for an account the ctl does not own.
			return nil
		}
		return err
	case "disable", "enable":
		return a.call(ctx, origin, apiKey, "sa-"+action, subject, nil, nil)
	default:
		return fmt.Errorf("%w: %q is not a service-account action (create, disable, enable)", jobs.ErrRefused, action)
	}
}

// alreadyAService reports whether a 409 says the subject is already a SERVICE
// account, as opposed to a human one.
func alreadyAService(err error) bool {
	msg := strings.ToLower(err.Error())
	if !strings.Contains(msg, "already exists") {
		return false
	}
	return strings.Contains(msg, "'service'") || strings.Contains(msg, "already exists as a service")
}

// ---------------------------------------------------------------- ingest
//
// The two ingest calls are the only WRITES into a tenant's corpus this driver
// can make, and they are refused unless the origin is a SANDBOX port.
//
// Everything else here is a read or a service-account change: operations the
// control plane performs on tenants it manages, for tenants it manages. Putting
// a document into a tenant's index is a different kind of act — it changes what
// that tenant's users retrieve — and the control plane has exactly one reason
// to do it: `ragstack-ctl selftest` needs a corpus in the tenant it just made
// so that the backup it takes has something in it and the restore it verifies
// proves something. That reason applies to sandbox tenants and to no others,
// so the refusal is in the DRIVER rather than in the selftest: a caller added
// later cannot reach a production tenant with these, whatever it intends.
//
// The check is on the port because that is what `paths.IsSelftestBlock` is
// everywhere else in the control plane — the sandbox is where its ports are,
// not what its row says or what it is called.

// sandboxOrigin reports whether origin's port belongs to a selftest block, and
// returns the port for the refusal message.
func sandboxOrigin(origin string) (port int, ok bool) {
	u, err := url.Parse(origin)
	if err != nil {
		return 0, false
	}
	port, err = strconv.Atoi(u.Port())
	if err != nil {
		return 0, false
	}
	if port < paths.SelftestBase || port > paths.SelftestEnd {
		return port, false
	}
	// The block CONTAINING the port, not the port: an ingest reaches a tenant
	// on its API port (block base + 0), and asking the question of the base is
	// the same question every other sandbox rule asks.
	base := paths.SelftestBase + ((port-paths.SelftestBase)/paths.PortStride)*paths.PortStride
	return port, paths.IsSelftestBlock(base)
}

// refuseNonSandbox is the shared refusal of the two ingest calls.
func refuseNonSandbox(origin, method string) error {
	port, ok := sandboxOrigin(origin)
	if ok {
		return nil
	}
	return fmt.Errorf("%w: TenantAPI.%s writes into a tenant's corpus and is allowed against SANDBOX tenants only "+
		"(ports %d–%d); %s listens on %d. The control plane does not ingest into tenants it did not create for a test",
		jobs.ErrRefused, method, paths.SelftestBase, paths.SelftestEnd, origin, port)
}

// Ingest is POST /v1/ingest with a server-side path.
//
// `source` is an absolute path on the host the tenant runs on; the tenant
// confines it under its own INGEST_ROOT and answers 503 when it has none. The
// path is checked here as well, for the same reason every other value this
// driver sends is: what the ctl will SEND is narrower than what the tenant will
// accept, and a `source` carrying a newline or a quote would be a value the ctl
// built out of something it did not check.
func (a *RealTenantAPI) Ingest(ctx context.Context, origin, apiKey, path string) (string, error) {
	if err := refuseNonSandbox(origin, "Ingest"); err != nil {
		return "", err
	}
	if apiKey == "" {
		return "", fmt.Errorf("%w: /v1/ingest needs an API key and none was supplied", jobs.ErrRefused)
	}
	if _, err := paths.SafePath("/", path); err != nil {
		return "", fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	var body struct {
		JobID  string `json:"job_id"`
		Status string `json:"status"`
	}
	if err := a.call(ctx, origin, apiKey, "ingest", "", map[string]any{"source": path}, &body); err != nil {
		return "", err
	}
	if body.JobID == "" {
		return "", fmt.Errorf("POST /v1/ingest accepted %s and named no job_id, so there is nothing to poll", path)
	}
	if !tenantJobIDRe.MatchString(body.JobID) {
		return "", fmt.Errorf("%w: the tenant answered an ingest job id the ctl will not put in a path (want %s)",
			jobs.ErrRefused, tenantJobIDRe)
	}
	return body.JobID, nil
}

// IngestStatus is GET /v1/ingest/{job_id}.
//
// An unknown id is NOT an error: the tenant answers 200 with status "unknown",
// and that is a fact the caller acts on (it polls, and gives up on its own
// clock) rather than a failure of the call.
func (a *RealTenantAPI) IngestStatus(ctx context.Context, origin, apiKey, jobID string) (string, error) {
	if err := refuseNonSandbox(origin, "IngestStatus"); err != nil {
		return "", err
	}
	if apiKey == "" {
		return "", fmt.Errorf("%w: /v1/ingest/{job_id} needs an API key and none was supplied", jobs.ErrRefused)
	}
	if !tenantJobIDRe.MatchString(jobID) {
		return "", fmt.Errorf("%w: %q is not an ingest job id the ctl will put in a path (want %s)",
			jobs.ErrRefused, jobID, tenantJobIDRe)
	}
	var body struct {
		Status string `json:"status"`
	}
	if err := a.call(ctx, origin, apiKey, "ingest-status", jobID, nil, &body); err != nil {
		return "", err
	}
	return body.Status, nil
}

// ---------------------------------------------------------------- census and proof

// CollectionCounts is the census: every collection the tenant lists, with the
// chunk count it reports for it.
//
// `counts=true`, which is the one place the ctl asks a tenant to pay for the
// per-store queries — a handover's release has to record what the tenant held
// before it is stopped, and "the same collections came back" is a weaker claim
// than "the same collections with the same number of chunks came back".
//
// A collection the tenant lists WITHOUT a count is recorded as -1 rather than
// as 0: an older tenant build that does not answer the field, or a store that
// could not be queried, must not read back as an empty collection.
func (a *RealTenantAPI) CollectionCounts(ctx context.Context, origin, apiKey string) (map[string]int64, error) {
	if apiKey == "" {
		return nil, fmt.Errorf("%w: /v1/collections needs an API key and none was supplied", jobs.ErrRefused)
	}
	var body struct {
		Collections []struct {
			ID    string `json:"id"`
			Count *int64 `json:"count"`
		} `json:"collections"`
	}
	req := request{
		method: http.MethodGet, path: routes["collections"].path,
		query:   url.Values{"counts": []string{"true"}},
		headers: map[string]string{"X-API-Key": apiKey},
		timeout: listTimeout,
	}
	if err := a.h.doJSON(ctx, origin, tenantOrigin, req, &body); err != nil {
		return nil, scrub(err, apiKey)
	}
	out := make(map[string]int64, len(body.Collections))
	for _, c := range body.Collections {
		if c.ID == "" {
			continue
		}
		if c.Count == nil {
			out[c.ID] = -1
			continue
		}
		out[c.ID] = *c.Count
	}
	return out, nil
}

// RunningIngestJobs is the ids of the tenant's ingest jobs that have not
// finished.
//
// The tenant's own vocabulary decides: anything that is not one of the
// terminal words is taken to be in flight. That direction is deliberate — an
// unknown status on the "still running" side delays a handover, and on the
// other side it stops an API in the middle of writing a collection.
func (a *RealTenantAPI) RunningIngestJobs(ctx context.Context, origin, apiKey string) ([]string, error) {
	if apiKey == "" {
		return nil, fmt.Errorf("%w: /v1/jobs needs an admin API key and none was supplied", jobs.ErrRefused)
	}
	var body struct {
		Jobs []struct {
			JobID  string `json:"job_id"`
			Status string `json:"status"`
		} `json:"jobs"`
	}
	req := request{
		method: http.MethodGet, path: routes["jobs"].path,
		headers: map[string]string{"X-API-Key": apiKey},
		timeout: listTimeout,
	}
	if err := a.h.doJSON(ctx, origin, tenantOrigin, req, &body); err != nil {
		return nil, scrub(err, apiKey)
	}
	var out []string
	for _, j := range body.Jobs {
		if terminalIngestStatus(strings.ToLower(strings.TrimSpace(j.Status))) || j.JobID == "" {
			continue
		}
		out = append(out, j.JobID)
	}
	sort.Strings(out)
	return out, nil
}

// terminalIngestStatus is the tenant's finished vocabulary. Everything else
// counts as in flight.
func terminalIngestStatus(s string) bool {
	switch s {
	case "completed", "succeeded", "success", "failed", "error", "cancelled", "canceled", "unknown":
		return true
	}
	return false
}

// KeyStatus dials the tenant with one credential and answers the STATUS.
//
// /v1/collections is the route: it needs a key, it is cheap, and both roles
// may call it — so a 200 means "this credential authenticates", which is
// exactly and only what a credential proof asks. A transport failure is still
// an error; an HTTP answer, whatever it is, is the result.
func (a *RealTenantAPI) KeyStatus(ctx context.Context, origin, apiKey string) (int, error) {
	if apiKey == "" {
		return 0, fmt.Errorf("%w: a key proof needs a credential to present", jobs.ErrRefused)
	}
	_, err := a.h.do(ctx, origin, tenantOrigin, request{
		method: http.MethodGet, path: routes["collections"].path,
		query:   url.Values{"counts": []string{"false"}},
		headers: map[string]string{"X-API-Key": apiKey},
		timeout: listTimeout,
	})
	if err == nil {
		return http.StatusOK, nil
	}
	if status := statusOf(err); status != 0 {
		return status, nil
	}
	return 0, scrub(err, apiKey)
}
