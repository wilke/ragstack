package drivers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// RealTenantAPI is the ctl's client to a tenant's OWN API, and it is an exact
// allowlist rather than a general-purpose HTTP client.
//
// The reason is the credential. Every other driver here talks to a store on a
// loopback port; this one presents a tenant's bootstrap ADMIN key, minted
// minutes earlier by `tenant create` and held in memory for the length of the
// job. A client that could be pointed at an arbitrary path is a client that
// can be made to send that key somewhere the ctl did not intend — so there are
// five methods, each with a fixed route out of the table below, and the only
// caller-supplied text that ever reaches a URL is a service-account subject
// that matched a 64-character path-safe pattern first.
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
}

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
	return fmt.Errorf("%s", strings.ReplaceAll(msg, apiKey, "<redacted api key>"))
}

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
