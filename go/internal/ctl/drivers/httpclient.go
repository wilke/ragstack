package drivers

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// The three HTTP drivers — qdrant, elasticsearch and the tenant's own API —
// share ONE client, because the rules that make an HTTP call safe here are
// properties of the ctl and not of the service at the other end: every one of
// them is a loopback process on this host, none of them is ever reached
// through a proxy, and none of them may redirect the ctl anywhere.
//
// Sharing it also means the transport (and so the connection pool) is shared:
// a backup that counts forty collections opens one connection, not forty.

const (
	// connectTimeout bounds the TCP connect only. A store that is starting up
	// refuses the connection immediately and a store that is wedged accepts it
	// and then says nothing, so this is short: the long waits below are for
	// work, not for dialling.
	connectTimeout = 2 * time.Second
	// listTimeout is the ceiling on the calls that read a small answer — a
	// listing, a count, a readiness probe.
	listTimeout = 10 * time.Second
	// readyTimeout is listTimeout's exception: ES's own readiness call carries
	// a server-side `timeout=30s`, so a 10 s client deadline would cut the
	// wait the ctl explicitly asked the cluster to perform.
	readyTimeout = 45 * time.Second
	// LongTimeout is the default ceiling on a call that can legitimately take
	// a long time: a snapshot of a large index, a restore, a qdrant recover.
	// Thirty minutes is not a guess at how long those take — it is the point
	// past which a job that is still waiting is a job an operator needs to
	// look at. RealOptions.StoreLongTimeout overrides it.
	LongTimeout = 30 * time.Minute
	// maxBodyBytes caps every response the ctl reads. The bodies here are
	// listings and status documents; anything at this size is a store
	// answering with something the ctl was not asking for, and reading it into
	// the daemon's heap is how one bad response becomes an outage.
	maxBodyBytes = 8 << 20
	// errBodyBytes is how much of a failing response's body an error carries.
	// Enough to show the store's own message, short enough that it cannot turn
	// a step log into a transcript.
	errBodyBytes = 512
)

// originKind selects which containment rule an origin is checked against.
type originKind int

const (
	// storeOrigin is hostfacts.AllowedStoreURL's rule minus the port table:
	// http, no userinfo, a loopback host, a port. The PORT half of that
	// function needs the tenant's own block, which a driver does not have and
	// must not guess — the caller that read the URL out of the registry is the
	// one that knows the block, and hostfacts checks it there.
	storeOrigin originKind = iota
	// tenantOrigin is stricter still: the tenant API is the one surface the
	// ctl presents a CREDENTIAL to, so its origin must be exactly
	// http://127.0.0.1:<port> or http://localhost:<port> — no ::1 spelling, no
	// path, nothing that could make the key travel somewhere else.
	tenantOrigin
)

// httpStores is the shared client. It is built once per driver set.
type httpStores struct {
	hc   *http.Client
	long time.Duration
	// redact is RealOptions.Redact: the seeded redactor the engine uses for
	// step logs. A store's error body is quoted back to the operator, and a
	// store that echoes a value out of a tenant's environment would otherwise
	// put it there.
	redact func(string) string
}

// newHTTPStores builds the shared loopback client from the driver options.
func newHTTPStores(o RealOptions) *httpStores {
	long := o.StoreLongTimeout
	if long <= 0 {
		long = LongTimeout
	}
	return &httpStores{
		hc: &http.Client{
			// Proxy is nil ON PURPOSE, rather than left at
			// http.ProxyFromEnvironment: every URL here is loopback, and an
			// HTTP_PROXY in the daemon's environment would send a tenant's
			// admin key to whatever that variable names.
			Transport: &http.Transport{
				Proxy:                 nil,
				DialContext:           (&net.Dialer{Timeout: connectTimeout}).DialContext,
				TLSHandshakeTimeout:   connectTimeout,
				ResponseHeaderTimeout: 0, // the per-call context is the deadline
				MaxIdleConnsPerHost:   4,
				IdleConnTimeout:       30 * time.Second,
				ForceAttemptHTTP2:     false,
			},
			// A redirect is refused rather than followed. Every check this
			// package makes — the scheme, the host, the port, the exact path —
			// is made on the URL the ctl built; following a Location header
			// would hand the next request's destination to the service, and
			// with it the credential the first one carried.
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				return fmt.Errorf("%w: %s redirected to %s; the ctl does not follow redirects",
					jobs.ErrRefused, via[len(via)-1].URL.Host, req.URL.Redacted())
			},
		},
		long:   long,
		redact: o.Redact,
	}
}

// request is one call, built entirely from values this package controls.
type request struct {
	method string
	// path is already escaped and always begins with "/". Callers build it
	// from validated components only — never from a raw caller string.
	path    string
	query   url.Values
	body    any               // JSON-encoded when non-nil
	headers map[string]string // X-API-Key and nothing else, today
	timeout time.Duration
	// okStatus are the statuses that are NOT an error. Empty means {200}.
	okStatus []int
}

// httpError is a store's refusal with enough of its own words to act on.
//
// It is deliberately NOT a jobs.ErrRefused: a 500 from qdrant is a failure of
// the store, not a refusal by the control plane, and collapsing the two would
// make `tenant backup` report "refused" for an outage. The driver wraps the
// few statuses that ARE a refusal (an unknown repository, a location the ctl
// will not accept) itself.
type httpError struct {
	Method string
	URL    string // path and query only — never the credential-bearing headers
	Status int
	Body   string
}

func (e *httpError) Error() string {
	if e.Body == "" {
		return fmt.Sprintf("%s %s: HTTP %d", e.Method, e.URL, e.Status)
	}
	return fmt.Sprintf("%s %s: HTTP %d: %s", e.Method, e.URL, e.Status, e.Body)
}

// statusOf returns the HTTP status behind err, or 0.
func statusOf(err error) int {
	var he *httpError
	if errors.As(err, &he) {
		return he.Status
	}
	return 0
}

// do performs one request against origin and returns the body.
func (h *httpStores) do(ctx context.Context, origin string, kind originKind, r request) ([]byte, error) {
	base, err := checkOrigin(origin, kind)
	if err != nil {
		return nil, err
	}
	u := base + r.path
	if len(r.query) > 0 {
		u += "?" + r.query.Encode()
	}
	var body io.Reader
	if r.body != nil {
		encoded, err := json.Marshal(r.body)
		if err != nil {
			return nil, fmt.Errorf("encoding the request body for %s %s: %w", r.method, r.path, err)
		}
		body = bytes.NewReader(encoded)
	}
	timeout := r.timeout
	if timeout <= 0 {
		timeout = listTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, r.method, u, body)
	if err != nil {
		return nil, err
	}
	if r.body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Accept", "application/json")
	for k, v := range r.headers {
		req.Header.Set(k, v)
	}
	resp, err := h.hc.Do(req)
	if err != nil {
		// The error is passed through UNCHANGED unless the redactor actually
		// removes something: flattening it to a string would drop the wrapped
		// sentinel (a refused redirect is a jobs.ErrRefused), and that chain
		// is what the API layer maps to 409 rather than 500. When redaction
		// does fire, not leaking is worth more than the chain.
		if h.redact == nil {
			return nil, err
		}
		if cleaned := h.redact(err.Error()); cleaned != err.Error() {
			return nil, errors.New(cleaned)
		}
		return nil, err
	}
	defer resp.Body.Close()

	data, err := readCapped(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("%s %s: %v", r.method, r.path, err)
	}
	if !statusOK(resp.StatusCode, r.okStatus) {
		return data, &httpError{
			Method: r.method,
			URL:    r.path,
			Status: resp.StatusCode,
			// Redacted BEFORE it is truncated: a secret that straddled the
			// 512-byte boundary would otherwise be cut in half and the half
			// that survived would not match the redactor's seed.
			Body: snippet(h.clean(string(data))),
		}
	}
	return data, nil
}

// doJSON performs the request and decodes the body into out.
func (h *httpStores) doJSON(ctx context.Context, origin string, kind originKind, r request, out any) error {
	data, err := h.do(ctx, origin, kind, r)
	if err != nil {
		return err
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("%s %s answered a body that is not the expected JSON: %v", r.method, r.path, err)
	}
	return nil
}

// clean runs text through the configured redactor. A nil redactor is the
// identity — the driver set the tests build has none — so nothing here may
// DEPEND on redaction for a secret the ctl itself holds; those are kept out of
// the string in the first place (see tenantapi.go).
func (h *httpStores) clean(s string) string {
	if h.redact == nil {
		return s
	}
	return h.redact(s)
}

// readCapped reads at most maxBodyBytes and reports a body that exceeds it.
func readCapped(r io.Reader) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(r, maxBodyBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxBodyBytes {
		return nil, fmt.Errorf("the response body exceeds the %d-byte cap", maxBodyBytes)
	}
	return data, nil
}

// snippet is the first errBodyBytes of body, whitespace-collapsed so a
// multi-line stack trace does not become a multi-line step log.
func snippet(body string) string {
	s := strings.Join(strings.Fields(body), " ")
	if len(s) > errBodyBytes {
		return s[:errBodyBytes] + "…"
	}
	return s
}

func statusOK(got int, want []int) bool {
	if len(want) == 0 {
		return got == http.StatusOK
	}
	for _, w := range want {
		if got == w {
			return true
		}
	}
	return false
}

// checkOrigin validates a base URL and returns it as a bare origin with no
// trailing slash, so every path this package appends is the path it built.
func checkOrigin(raw string, kind originKind) (string, error) {
	if raw == "" {
		return "", fmt.Errorf("%w: an empty URL is not a store the ctl may reach", jobs.ErrRefused)
	}
	u, err := url.Parse(raw)
	if err != nil {
		return "", fmt.Errorf("%w: unparsable URL %q", jobs.ErrRefused, raw)
	}
	if u.Scheme != "http" {
		return "", fmt.Errorf("%w: scheme %q is not http; these are loopback services", jobs.ErrRefused, u.Scheme)
	}
	// `http://u:p@localhost:9200` passes every host and port test below while
	// carrying a credential the ctl would put on the wire and into its error
	// strings. Same rule, same reason, as hostfacts.AllowedStoreURL.
	if u.User != nil {
		return "", fmt.Errorf("%w: the URL carries userinfo (a credential); a store URL must not", jobs.ErrRefused)
	}
	if u.RawQuery != "" || u.Fragment != "" {
		return "", fmt.Errorf("%w: %q carries a query or fragment; a base URL is an origin", jobs.ErrRefused, raw)
	}
	if p := strings.TrimSuffix(u.Path, "/"); p != "" {
		return "", fmt.Errorf("%w: %q carries a path; the ctl builds every path itself", jobs.ErrRefused, raw)
	}
	host := u.Hostname()
	switch kind {
	case tenantOrigin:
		if host != "127.0.0.1" && host != "localhost" {
			return "", fmt.Errorf("%w: tenant origin host %q must be 127.0.0.1 or localhost", jobs.ErrRefused, host)
		}
	default:
		if host != "127.0.0.1" && host != "localhost" && host != "::1" {
			return "", fmt.Errorf("%w: host %q is not loopback", jobs.ErrRefused, host)
		}
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil || port <= 0 || port > 65535 {
		return "", fmt.Errorf("%w: no usable port in %q", jobs.ErrRefused, raw)
	}
	return u.Scheme + "://" + u.Host, nil
}
