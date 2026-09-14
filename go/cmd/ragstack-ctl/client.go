package main

// The transport half of the mutation surface.
//
// Every op-submitting command in this binary is a THIN CLIENT: it maps its
// positionals and flags onto the `args` object x-ctl-op-args describes, wraps
// them in op_request.json's envelope, and posts that to the daemon. It does
// not plan, it does not authorize, and it does not decide what a verb means —
// a CLI that reimplemented any of those would be a second reader of the
// contract, and the first divergence between the two readers is a plan an
// operator previewed on the command line and a job the daemon then ran
// differently.
//
// The one exception is `--direct`, which builds the SAME engine in-process
// (api.BuildEngine) rather than reimplementing one. That is why --direct is a
// flag on the client and not a separate code path through the ops.

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

const (
	// defaultServer matches api.DefaultListen, as a URL. The daemon's own
	// CTL_LISTEN is a BIND ADDRESS, not a URL, so the client reads its own
	// variable instead of guessing a scheme for a host:port — and no new
	// CTL_* name is added to internal/, which is the daemon's namespace.
	defaultServer = "http://127.0.0.1:23990"
	// envServer overrides --server's default.
	envServer = "CTL_URL"
	// envAPIKey is the fallback for --api-key-file. The VALUE is a
	// credential: it is sent as the X-API-Key header and is never printed,
	// never echoed into an error, and never put in the body (a body key that
	// differs from the header is 400 `both_credentials`, and the header alone
	// is the documented exemption).
	envAPIKey = "RAGSTACK_CTL_API_KEY"
)

// jobPollInterval and jobWaitTimeout bound `--wait`. They are package vars so
// a test can poll a httptest server without sleeping, and so a wait that the
// daemon never finishes ends as a diagnosable timeout rather than a CLI that
// hangs in somebody's terminal forever.
var (
	jobPollInterval = 2 * time.Second
	jobWaitTimeout  = 2 * time.Hour
	// directPollInterval is the same loop against the IN-PROCESS engine,
	// where a poll is a map lookup rather than a request: a --direct run of a
	// three-step op should not take six seconds because the daemon's polling
	// interval was the only one there was.
	directPollInterval = 25 * time.Millisecond
)

// httpTimeout bounds one request. A plan can be slow (the op-scoped doctor
// probes stores), so it is generous; it is not unlimited, because a client
// blocked on a half-open socket reports nothing at all.
var httpTimeout = 120 * time.Second

// idempotencyKeyPattern is op_request.json's own. Checking it here means a
// mistyped --idempotency-key is a usage error the operator can fix, rather
// than a 422 from the daemon after the request has crossed the network.
var idempotencyKeyPattern = regexp.MustCompile(`^[A-Za-z0-9._:-]{8,128}$`)

// ---------------------------------------------------------------- op flags

// opFlags is the flag set every op-submitting command shares. One struct
// rather than a copy per verb: the envelope is the same for all of them, and
// a verb that grew its own --yes spelling would be a verb an operator cannot
// script from muscle memory.
type opFlags struct {
	fs             *flag.FlagSet
	server         *string
	apiKeyFile     *string
	direct         *bool
	dryRun         *bool
	yes            *bool
	yesDestructive *string
	wait           *bool
	forceDoctor    *string
	idempotency    *string
	newKey         *bool
	registry       *string
	ragRoot        *string
	asJSON         *bool

	// wantSecrets is set by the commands that MINT credentials (`tenant
	// create`): after a successful wait, the envelope is collected and printed
	// once. fetchSecrets is filled in by whichever transport submitted the job,
	// so the collection goes back the same way the submission went.
	wantSecrets  bool
	fetchSecrets func(jobID string) (*model.SecretsResponse, error)

	// mountPoint overrides what a rendered unit's ConditionPathIsMountPoint
	// names. It is deliberately not a flag: `selftest --rag-root <scratch>`
	// sets it, because that is the one run whose paths move into a sandbox tree
	// while the mount those units wait for is still /rag. Empty means
	// Roots.RagRoot, which is right everywhere else.
	mountPoint string
}

func addOpFlags(fs *flag.FlagSet, registryPath, ragRoot string, jsonOut bool) *opFlags {
	return &opFlags{
		fs:             fs,
		server:         fs.String("server", envOrDefault(envServer, defaultServer), "control-plane base URL"),
		apiKeyFile:     fs.String("api-key-file", "", "file holding the ctl API key (else $"+envAPIKey+")"),
		direct:         fs.Bool("direct", false, "run the engine in this process instead of calling the daemon"),
		dryRun:         fs.Bool("dry-run", false, "plan only: print the Plan and change nothing"),
		yes:            fs.Bool("yes", false, `answer a plan's confirm with "yes"`),
		yesDestructive: fs.String("yes-destructive", "", "answer a destructive plan's confirm with this exact value (the tenant name)"),
		wait:           fs.Bool("wait", false, "poll the job to a terminal state and exit with its outcome"),
		forceDoctor:    fs.String("force-with-doctor-diff", "", "the doctor hash you accepted (lets a YELLOW doctor through; never a red one)"),
		idempotency:    fs.String("idempotency-key", "", "retry key (derived from the request and printed on stderr when absent)"),
		newKey:         fs.Bool("new-key", false, "mint a RANDOM idempotency key: run this operation again on purpose"),
		registry:       fs.String("registry", registryPath, "registry.json path (--direct)"),
		ragRoot:        fs.String("rag-root", ragRoot, "deployment root (--direct)"),
		asJSON:         fs.Bool("json", jsonOut, "print the raw Plan/Job JSON"),
	}
}

func envOrDefault(name, dflt string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return dflt
}

// readAPIKey resolves the ctl key: --api-key-file wins, $RAGSTACK_CTL_API_KEY
// is the fallback, and neither is ever written to stdout or stderr. The error
// names the FILE, never its contents.
func readAPIKey(file string) (string, error) {
	if file == "" {
		return strings.TrimSpace(os.Getenv(envAPIKey)), nil
	}
	// The MODE is checked before the bytes are read.
	//
	// A ctl key is an operator credential: the file holding it is 0600 or it
	// is readable by somebody who is not the operator, and on a shared host
	// that somebody is every account on it. Reading it anyway and saying
	// nothing is how a world-readable key survives for a year — the CLI is the
	// only thing that ever looks at this file, so it is the only thing that
	// can say so.
	fi, err := os.Lstat(file)
	if err != nil {
		return "", fmt.Errorf("--api-key-file: %w", err)
	}
	if !fi.Mode().IsRegular() {
		return "", fmt.Errorf("--api-key-file %s is a %s, not a regular file; a credential read through a link or a "+
			"fifo is a credential somebody else chose", file, fi.Mode().Type())
	}
	if perm := fi.Mode().Perm(); perm&0o077 != 0 {
		return "", fmt.Errorf("--api-key-file %s is mode %04o: it is readable (or writable) by group or other. "+
			"Run `chmod 600 %s`", file, perm, file)
	}
	b, err := os.ReadFile(file)
	if err != nil {
		return "", fmt.Errorf("--api-key-file: %w", err)
	}
	key := strings.TrimSpace(string(b))
	if key == "" {
		return "", fmt.Errorf("--api-key-file %s holds no key", file)
	}
	return key, nil
}

// confirm is the envelope's `confirm`. --yes-destructive wins over --yes: it
// is the more specific statement, and a plan that demands the tenant name is
// never satisfied by "yes".
func (o *opFlags) confirm() string {
	if *o.yesDestructive != "" {
		return *o.yesDestructive
	}
	if *o.yes {
		return "yes"
	}
	return ""
}

// envelope builds op_request.json.
//
// The idempotency key, when the operator gave none, is DERIVED from the
// request: the operation, the tenant, the canonical arguments and the local
// user. It used to be 128 random bits, which made the key different on every
// invocation — so the one thing an idempotency key exists to prevent was
// exactly what a retry did. A script that re-runs `tenant restore` because the
// first answer was lost in a dropped connection sent a NEW key with the same
// body, and the control plane, having no way to know the two were the same
// request, restored twice.
//
// Deriving it makes the default the safe one: the same command, run again,
// carries the same key and gets the ORIGINAL job back. The dangerous thing —
// "do that again" — is the one that has to be asked for, with `--new-key`
// (a fresh random key) or `--idempotency-key` (your own). Either way the key
// is printed on stderr, so an operator who wants to retry THIS request has the
// value to quote.
func (o *opFlags) envelope(target opTarget, args map[string]any) (model.OpRequest, error) {
	if args == nil {
		args = map[string]any{}
	}
	key := strings.TrimSpace(*o.idempotency)
	switch {
	case key != "":
		if !idempotencyKeyPattern.MatchString(key) {
			return model.OpRequest{}, fmt.Errorf("--idempotency-key %q is outside op_request.json's ^[A-Za-z0-9._:-]{8,128}$", key)
		}
		if *o.newKey {
			return model.OpRequest{}, errors.New("--new-key and --idempotency-key say different things: " +
				"pass one or the other")
		}
	case *o.newKey:
		var err error
		if key, err = randomIdempotencyKey(); err != nil {
			return model.OpRequest{}, err
		}
		fmt.Fprintf(stderr, "ragstack-ctl: idempotency key %s (--new-key: this is a NEW run of the same request; "+
			"re-run with --idempotency-key %s to retry it)\n", key, key)
	default:
		key = derivedIdempotencyKey(target, args)
		fmt.Fprintf(stderr, "ragstack-ctl: idempotency key %s (derived from this request: re-running the same "+
			"command returns the same job — pass --new-key to run it again)\n", key)
	}
	return model.OpRequest{
		DryRun:              *o.dryRun,
		IdempotencyKey:      key,
		Confirm:             o.confirm(),
		ForceWithDoctorDiff: strings.TrimSpace(*o.forceDoctor),
		Args:                args,
	}, nil
}

// derivedIdempotencyKey is the default: sha256 over the request as this
// invocation means it.
//
// The LOCAL USER is in the digest so that two operators typing the same
// command are two requests — one of them retrying the other's `key mint` by
// accident is a surprise nobody asked for — and $SUDO_USER is included because
// `ops/coconut/ctl-as-svc.sh` runs everything as the same uid.
//
// json.Marshal of a map sorts its keys, so the same arguments written in a
// different order produce the same key.
func derivedIdempotencyKey(target opTarget, args map[string]any) string {
	body, err := json.Marshal(struct {
		Op        string         `json:"op"`
		Tenant    string         `json:"tenant"`
		Path      string         `json:"path"`
		Args      map[string]any `json:"args"`
		Principal string         `json:"principal"`
	}{target.op, target.tenant, target.path, args, localPrincipal()})
	if err != nil {
		// An args map this CLI built cannot fail to marshal; if it somehow
		// does, a random key is still a VALID key — it only loses the retry
		// protection, and saying so is better than refusing the operation.
		if k, rerr := randomIdempotencyKey(); rerr == nil {
			return k
		}
	}
	sum := sha256.Sum256(body)
	return "ctl-" + hex.EncodeToString(sum[:])[:40]
}

// localPrincipal identifies who is typing, for the derived key only. It is
// never sent as an identity claim: the daemon authenticates the credential.
func localPrincipal() string {
	if u := strings.TrimSpace(os.Getenv("SUDO_USER")); u != "" {
		return fmt.Sprintf("local:%d:sudo:%s", os.Getuid(), u)
	}
	return fmt.Sprintf("local:%d", os.Getuid())
}

// randomIdempotencyKey is a random key inside op_request.json's pattern. It is
// not a ULID: the contract only constrains the grammar, and "ctl-" plus 128
// bits of randomness is unguessable, sortable enough for a human to compare,
// and obviously machine-generated in an audit row.
func randomIdempotencyKey() (string, error) {
	h, err := randomHex(16)
	if err != nil {
		return "", err
	}
	return "ctl-" + h, nil
}

func randomHex(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("the system random source failed: %w", err)
	}
	return hex.EncodeToString(b), nil
}

// ---------------------------------------------------------------- client

type ctlClient struct {
	base string
	key  string
	http *http.Client
}

// newCtlClient is the one place a base URL and a credential become a client,
// so `job list` and `tenant restore` present the key the same way.
func newCtlClient(server, apiKeyFile string) (*ctlClient, error) {
	key, err := readAPIKey(apiKeyFile)
	if err != nil {
		return nil, err
	}
	base := strings.TrimSuffix(strings.TrimSpace(server), "/")
	u, err := url.Parse(base)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, fmt.Errorf("--server %q is not an http(s) URL", server)
	}
	// The ctl key travels in a header on EVERY request, so the transport has
	// to be one that cannot hand it to somebody else.
	//
	// Plain http is allowed only to a loopback host, where the bytes never
	// leave the machine — which is the conventional deployment
	// (http://127.0.0.1:23990) and the default. Any other host over http would
	// put an operator credential on a wire in cleartext, and $CTL_URL is an
	// environment variable: the one place a mistake is easiest to make and
	// hardest to see.
	if u.Scheme == "http" && !isLoopbackHost(u.Host) {
		return nil, fmt.Errorf("--server %q sends the ctl key over plain http to %s. Only a loopback host "+
			"(127.0.0.1, ::1, localhost) may be http; use https:// for anything else", server, u.Hostname())
	}
	return &ctlClient{base: base, key: key, http: &http.Client{
		Timeout: httpTimeout,
		// And a redirect must not be able to take the key somewhere else. Go
		// follows redirects by default and re-sends the headers it was given,
		// so a control plane (or anything answering on its port) could move
		// the credential to another host, or off https, with one 302.
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			prev := via[len(via)-1].URL
			if req.URL.Scheme != prev.Scheme || req.URL.Host != prev.Host {
				return fmt.Errorf("refusing a redirect from %s://%s to %s://%s: the ctl key is a header on this "+
					"request and a redirect that changes host or scheme would hand it to somebody else",
					prev.Scheme, prev.Host, req.URL.Scheme, req.URL.Host)
			}
			if len(via) >= 5 {
				return errors.New("too many redirects")
			}
			return nil
		},
	}}, nil
}

// isLoopbackHost reports whether a URL host (with or without a port) names
// this machine. A NAME other than "localhost" is not accepted: resolving it
// here would make the decision depend on DNS, which is the thing an attacker
// who can change the answer would change.
func isLoopbackHost(host string) bool {
	h := host
	if hn, _, err := net.SplitHostPort(host); err == nil {
		h = hn
	}
	h = strings.Trim(h, "[]")
	if strings.EqualFold(h, "localhost") {
		return true
	}
	ip := net.ParseIP(h)
	return ip != nil && ip.IsLoopback()
}

func (o *opFlags) client() (*ctlClient, error) { return newCtlClient(*o.server, *o.apiKeyFile) }

// ctlResponse is one answer, read whole. Bodies here are plans, jobs and
// error documents — all small — so reading them into memory under a cap is
// simpler than streaming and cannot be made to exhaust the CLI.
type ctlResponse struct {
	Status   int
	Body     []byte
	Location string
}

const maxBody = 8 << 20

func (c *ctlClient) do(ctx context.Context, method, path string, body any) (*ctlResponse, error) {
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, rdr)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Accept", "application/json")
	if c.key != "" {
		req.Header.Set("X-API-Key", c.key)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return nil, err
	}
	return &ctlResponse{Status: resp.StatusCode, Body: b, Location: resp.Header.Get("Location")}, nil
}

func (c *ctlClient) get(ctx context.Context, path string, q url.Values) (*ctlResponse, error) {
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	return c.do(ctx, http.MethodGet, path, nil)
}

// ---------------------------------------------------------------- errors

// apiError is error.json. `extra` is the only untyped member, and the three
// values an operator can ACT on live there: the confirm value a 428 demands,
// the doctor hash a yellow refusal will accept, the job id a duplicate key
// already produced.
type apiError struct {
	Detail    string         `json:"detail"`
	Code      string         `json:"code"`
	RequestID string         `json:"request_id"`
	Extra     map[string]any `json:"extra"`
}

// reportHTTPError prints a non-2xx answer and returns the exit code for it.
//
// The mapping is the plan's: a REFUSAL — the daemon understood the request
// and declined it — is 3, and a fault is 1. The distinction matters to a
// script: 3 means "change something and ask again", 1 means "the control
// plane could not answer".
func reportHTTPError(resp *ctlResponse) int {
	var e apiError
	if err := json.Unmarshal(resp.Body, &e); err != nil || e.Detail == "" {
		// An unparseable body is a fault, not a policy answer: nothing in it
		// tells the operator what to change.
		fmt.Fprintf(stderr, "ragstack-ctl: the control plane answered HTTP %d with a body this client cannot read\n", resp.Status)
		if n := len(resp.Body); n > 0 && n < 512 {
			fmt.Fprintf(stderr, "ragstack-ctl: %s\n", strings.TrimSpace(string(resp.Body)))
		}
		return exitError
	}
	code := e.Code
	if code == "" {
		code = "error"
	}
	switch resp.Status {
	case http.StatusUnauthorized:
		fmt.Fprintf(stderr, "ragstack-ctl: the ctl key was not accepted: %s\n", e.Detail)
	default:
		fmt.Fprintf(stderr, "ragstack-ctl: %s: %s\n", code, e.Detail)
	}
	printErrorExtra(e)
	if e.RequestID != "" {
		fmt.Fprintf(stderr, "ragstack-ctl: request_id %s\n", e.RequestID)
	}
	switch {
	case resp.Status == http.StatusUnauthorized,
		resp.Status == http.StatusForbidden,
		resp.Status == http.StatusConflict,
		resp.Status == http.StatusUnprocessableEntity,
		resp.Status == http.StatusPreconditionRequired:
		return exitRefused
	default:
		// 404, 400, 429, 5xx and anything unexpected: a fault from the
		// operator's point of view, because no retry of the same request with
		// a different flag makes it succeed.
		return exitError
	}
}

// printErrorExtra turns the typed `extra` members into the retry the operator
// actually has to type. A 428 whose body carries `confirm_value` and whose
// message does not name it leaves a caller with a refusal it cannot satisfy.
func printErrorExtra(e apiError) {
	if v, ok := e.Extra["confirm_value"].(string); ok && v != "" {
		if v == "yes" {
			fmt.Fprintln(stderr, "ragstack-ctl: re-run with --yes")
		} else {
			fmt.Fprintf(stderr, "ragstack-ctl: this plan is destructive — re-run with --yes-destructive %s\n", v)
		}
	}
	if v, ok := e.Extra["doctor_hash"].(string); ok && v != "" {
		fmt.Fprintf(stderr, "ragstack-ctl: doctor hash %s — if the doctor is YELLOW and you accept it, re-run with --force-with-doctor-diff %s\n", v, v)
	}
	if v, ok := e.Extra["plan_hash"].(string); ok && v != "" {
		fmt.Fprintf(stderr, "ragstack-ctl: the plan moved under the locks (plan_hash %s) — re-run --dry-run and look again\n", v)
	}
	if v, ok := e.Extra["job_id"].(string); ok && v != "" {
		fmt.Fprintf(stderr, "ragstack-ctl: job %s — `ragstack-ctl job show %s`\n", v, v)
	}
	if h, ok := e.Extra["holder"].(map[string]any); ok {
		if v, ok := h["job_id"].(string); ok && v != "" {
			fmt.Fprintf(stderr, "ragstack-ctl: the lock is held by job %s\n", v)
		}
	}
}

// failClient reports a transport or IO failure. It is exit 1 in every case:
// the control plane said nothing, so nothing about the request is known to be
// wrong.
func failClient(err error) int {
	fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
	return exitError
}

// ---------------------------------------------------------------- submit

// opTarget names one submission: the daemon route, and the engine op/tenant
// the same submission means under --direct. Holding both in one value is what
// keeps the two transports describing the same operation.
type opTarget struct {
	path   string
	op     string
	tenant string
}

// submitOp is the single path every op-submitting command takes.
func submitOp(o *opFlags, target opTarget, args map[string]any) int {
	req, err := o.envelope(target, args)
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	if *o.direct {
		return submitDirect(o, target, req)
	}
	c, err := o.client()
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	ctx := context.Background()
	o.fetchSecrets = func(jobID string) (*model.SecretsResponse, error) {
		r, err := c.get(ctx, "/v1/jobs/"+jobID+"/secrets", nil)
		if err != nil {
			return nil, err
		}
		if r.Status != http.StatusOK {
			return nil, fmt.Errorf("GET /v1/jobs/%s/secrets answered %d", jobID, r.Status)
		}
		var out model.SecretsResponse
		if err := json.Unmarshal(r.Body, &out); err != nil {
			return nil, err
		}
		return &out, nil
	}
	resp, err := c.do(ctx, http.MethodPost, target.path, req)
	if err != nil {
		return failClient(err)
	}
	switch resp.Status {
	case http.StatusOK:
		var plan model.Plan
		if err := json.Unmarshal(resp.Body, &plan); err != nil {
			return failClient(fmt.Errorf("the control plane answered 200 with something that is not a plan: %w", err))
		}
		return printPlan(&plan, *o.asJSON)
	case http.StatusAccepted:
		var job model.Job
		if err := json.Unmarshal(resp.Body, &job); err != nil {
			return failClient(fmt.Errorf("the control plane answered 202 with something that is not a job: %w", err))
		}
		return acceptJob(ctx, c, o, &job)
	default:
		return reportHTTPError(resp)
	}
}

// acceptJob prints the accepted job and, with --wait, follows it.
func acceptJob(ctx context.Context, c *ctlClient, o *opFlags, job *model.Job) int {
	if !*o.wait {
		return printJob(job, *o.asJSON)
	}
	return waitForJob(ctx, c, o, job)
}

// waitForJob polls GET /v1/jobs/{id}, printing every step as its state moves,
// and exits with the job's outcome.
//
// It stops on `interrupted` and `awaiting_cutover` as well as on the four
// terminal states. Neither of those two advances on its own — one needs
// `job resume`, the other `job continue` — so a wait that only stopped at
// JobState.Terminal() would sit there until the timeout on exactly the runs
// where the operator is most needed.
func waitForJob(ctx context.Context, c *ctlClient, o *opFlags, job *model.Job) int {
	// A poll that fails is not the same as an operation that failed: the
	// control plane may be restarting, or a proxy may have dropped one
	// connection. So a transport error is RETRIED with backoff, and only a
	// run of them gives up — a --wait that aborted on the first hiccup was a
	// client reporting its own network as the job's outcome.
	//
	// A non-2xx ANSWER is not retried: the daemon said something, and saying
	// it again will not change it.
	fetch := func() (*model.Job, int, error) {
		resp, err := c.get(ctx, "/v1/jobs/"+job.ID, nil)
		if err != nil {
			return nil, 0, err
		}
		if resp.Status != http.StatusOK {
			return nil, reportHTTPError(resp), errStopPolling
		}
		var next model.Job
		if err := json.Unmarshal(resp.Body, &next); err != nil {
			return nil, 0, fmt.Errorf("polling job %s: %w", job.ID, err)
		}
		return &next, 0, nil
	}
	return followJob(ctx, o, job, jobPollInterval, fetch)
}

// errStopPolling marks a fetch failure the poller must NOT retry, because the
// exit code it carries is already the answer.
var errStopPolling = errors.New("stop polling")

// maxPollFailures is how many consecutive transport failures --wait tolerates
// before it reports one. Bounded: a control plane that is gone stays gone, and
// a client that retried forever would hang in somebody's terminal.
const maxPollFailures = 5

// followJob is the poll loop both transports share: print each step as it
// settles, stop at a state that does not advance on its own, and return this
// CLI's exit code for it.
func followJob(ctx context.Context, o *opFlags, job *model.Job, interval time.Duration,
	fetch func() (*model.Job, int, error)) int {
	deadline := time.Now().Add(jobWaitTimeout)
	seen := map[int]model.StepState{}
	report := func(j *model.Job) {
		if *o.asJSON {
			return
		}
		for _, s := range j.Steps {
			if seen[s.N] == s.State {
				continue
			}
			seen[s.N] = s.State
			fmt.Fprintf(stdout, "  step %2d %-12s %-24s %s\n", s.N, s.Kind, s.Title, s.State)
		}
	}
	if !*o.asJSON {
		fmt.Fprintf(stdout, "job %s %s state %s\n", job.ID, job.Op, job.State)
	}
	report(job)
	fails, backoff := 0, interval
	for {
		if code, done := waitExit(job.State); done {
			finishWait(job, o)
			if code == exitOK && o.wantSecrets {
				if c := deliverSecrets(o, job.ID); c != exitOK {
					return c
				}
			}
			return code
		}
		if time.Now().After(deadline) {
			// Its own exit code: the job is still going, so this is not a
			// failure of the operation and not a failure of the transport.
			fmt.Fprintf(stderr, "ragstack-ctl: job %s is still %s after %s — it is still running; "+
				"`ragstack-ctl job show %s`\n", job.ID, job.State, jobWaitTimeout, job.ID)
			return exitWaitTimeout
		}
		select {
		case <-ctx.Done():
			return failClient(ctx.Err())
		case <-time.After(backoff):
		}
		next, code, err := fetch()
		switch {
		case errors.Is(err, errStopPolling):
			return code
		case err != nil:
			fails++
			if fails >= maxPollFailures {
				return failClient(fmt.Errorf("polling job %s failed %d times in a row (the job may still be "+
					"running — `ragstack-ctl job show %s`): %w", job.ID, fails, job.ID, err))
			}
			fmt.Fprintf(stderr, "ragstack-ctl: polling job %s: %v (retry %d of %d)\n", job.ID, err, fails, maxPollFailures)
			if backoff < 30*time.Second {
				backoff *= 2
			}
			continue
		}
		fails, backoff = 0, interval
		job = next
		report(job)
	}
}

// deliverSecrets collects the one-time envelope and prints it.
//
// It runs ONCE, right after the job succeeded, because that is the only moment
// the values exist anywhere an operator can reach: the envelope is destroyed by
// the first successful read and expires 15 minutes after it was created. A
// failure to collect is reported as its own thing rather than as the job's —
// the tenant was created either way, and telling an operator the create failed
// would send them to roll back a tenant that is running.
func deliverSecrets(o *opFlags, jobID string) int {
	if o.fetchSecrets == nil {
		fmt.Fprintf(stderr, "ragstack-ctl: this transport cannot collect the credentials; "+
			"`ragstack-ctl job show %s` names them and the envelope expires in 15 minutes\n", jobID)
		return exitOK
	}
	resp, err := o.fetchSecrets(jobID)
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: the job succeeded but its credentials could not be collected: %v\n"+
			"They are NOT recoverable once the envelope expires; mint replacements with `ragstack-ctl key mint`.\n", err)
		return exitError
	}
	if *o.asJSON {
		return encode(resp)
	}
	fmt.Fprintf(stdout, "\ncredentials for job %s — SHOWN ONCE, they are not stored anywhere and cannot be shown again:\n",
		resp.JobID)
	for _, s := range resp.Secrets {
		fmt.Fprintf(stdout, "  %-20s %-6s %s\n", s.Label, s.Role, s.Value)
	}
	fmt.Fprintf(stdout, "Save them now. The registry keeps fingerprints only.\n")
	return exitOK
}

// finishWait prints the job's ending. Under --json the whole job document is
// the ending, so a script sees the same record the daemon holds.
func finishWait(job *model.Job, o *opFlags) {
	if *o.asJSON {
		_ = encode(job)
		return
	}
	fmt.Fprintf(stdout, "job %s %s\n", job.ID, job.State)
	if job.Error != nil && job.Error.Detail != "" {
		fmt.Fprintf(stderr, "ragstack-ctl: %s: %s\n", job.Error.Code, job.Error.Detail)
	}
	switch job.State {
	case model.JobInterrupted:
		fmt.Fprintf(stderr, "ragstack-ctl: its reservations are held — `ragstack-ctl job resume %s` once the cause is fixed\n", job.ID)
	case model.JobAwaitingCutover:
		fmt.Fprintf(stderr, "ragstack-ctl: waiting for the cutover — `ragstack-ctl job continue %s` to proceed, `job cancel %s` to roll back\n", job.ID, job.ID)
	}
}

// waitExit maps a job state onto this CLI's exit codes, and says whether the
// wait is over.
func waitExit(s model.JobState) (int, bool) {
	switch s {
	case model.JobSucceeded:
		return exitOK, true
	case model.JobFailed, model.JobRolledBack:
		return exitJobFailed, true
	case model.JobInterrupted:
		return exitJobInterrupted, true
	case model.JobCancelled:
		return exitRefused, true
	case model.JobAwaitingCutover:
		// Not a failure — the job did exactly what handover/migrate-local plan
		// — but not success either: the next move is the operator's, and a
		// script that saw 0 here would call a half-done migration finished.
		return exitJobAwaitingCutover, true
	}
	return 0, false
}

// ---------------------------------------------------------------- printing

func printPlan(p *model.Plan, asJSON bool) int {
	if asJSON {
		return encode(p)
	}
	fmt.Fprintf(stdout, "plan %s %s · registry generation %d · %s\n",
		p.Op, orNone(string(p.Tenant)), p.RegistryGeneration, p.PlanHash)
	fmt.Fprintf(stdout, "doctor %s (%s)\n", p.Doctor.Status, p.Doctor.Hash)
	if p.RequiresConfirm {
		v := string(p.ConfirmValue)
		if v == "yes" {
			fmt.Fprintln(stdout, "requires confirm: pass --yes to run it")
		} else {
			fmt.Fprintf(stdout, "requires confirm: this plan is DESTRUCTIVE — pass --yes-destructive %s to run it\n", v)
		}
	}
	for _, s := range p.Steps {
		mark := ""
		if s.Destructive {
			mark = "   DESTRUCTIVE"
		}
		fmt.Fprintf(stdout, "  %2d %-14s %s%s\n", s.N, s.Kind, s.Title, mark)
		if len(s.Targets) > 0 {
			fmt.Fprintf(stdout, "       targets %s\n", strings.Join(s.Targets, ", "))
		}
		for _, w := range s.WouldWrite {
			fmt.Fprintf(stdout, "       write   %s (%s)\n", w.Path, w.Mode)
		}
		for _, r := range s.WouldRun {
			fmt.Fprintf(stdout, "       run     %s\n", strings.Join(r.Argv, " "))
		}
		for _, w := range s.Warnings {
			fmt.Fprintf(stdout, "       warning %s\n", w)
		}
	}
	for _, w := range p.Warnings {
		fmt.Fprintf(stdout, "warning: %s\n", w)
	}
	return exitOK
}

func printJob(j *model.Job, asJSON bool) int {
	if asJSON {
		return encode(j)
	}
	fmt.Fprintf(stdout, "job %s %s %s state %s\n", j.ID, j.Op, orNone(string(j.Tenant)), j.State)
	fmt.Fprintf(stdout, "follow it with: ragstack-ctl job show %s\n", j.ID)
	return exitOK
}

// ---------------------------------------------------------------- --direct

// buildDirectEngine assembles the engine this process would run ops through.
//
// TODO(integration): api.BuildEngine returns api.ErrEngineNotWired until the
// store, the op registry and the drivers are connected to it. That is the
// whole of --direct's implementation on purpose: the CLI must never stub an
// engine of its own, because two engines that agree by coincidence are how a
// `--direct` run takes locks the daemon does not honour. When BuildEngine
// starts returning a real engine, --direct starts working here with no change
// to this file.
func buildDirectEngine(o *opFlags) (jobs.Engine, error) {
	eng, _, err := buildDirectEngineAndDrivers(o)
	return eng, err
}

// buildDirectEngineAndDrivers is buildDirectEngine, and it also hands back the
// driver set the engine was built with. Only `selftest` needs both: it submits
// jobs through the engine and then asks the same host whether the ports are
// free, the units are gone and the quarantine is there.
func buildDirectEngineAndDrivers(o *opFlags) (jobs.Engine, jobs.Drivers, error) {
	host, err := os.Hostname()
	if err != nil {
		return nil, nil, fmt.Errorf("--direct records the worker host and this host has none: %w", err)
	}
	roots := paths.NewRoots(*o.ragRoot, paths.Overrides{})
	cfg := api.EngineConfig{
		Roots:        roots,
		RegistryPath: resolveRegistry(*o.registry, *o.ragRoot),
		StorePath:    filepath.Join(roots.CtlStateDir, "jobs.db"),
		Mode:         model.WorkerDirect,
		Host:         host,
		Mirror:       mirrorPath(*o.ragRoot),
		MountPoint:   o.mountPoint,
		SecretsTTL:   api.DefaultSecretsTTL,
		Now:          time.Now,
	}
	// The same CTL_* variables the daemon reads, through the same helper: a
	// --direct run and the daemon must resolve `systemctl`, `git`, node, the
	// mirror and the npm cache identically, or an operator would be debugging
	// two different driver sets.
	api.SetHostToolsFromEnv(&cfg)
	return api.BuildEngineAndDrivers(cfg)
}

// directPrincipal is who a --direct run is. Nothing about it comes from a
// flag: a CLI-supplied subject or role would be an unauthenticated identity
// claim, and the engine re-authorizes every continuation against exactly this
// value. SUDO_USER is the human behind an `ops/coconut/ctl-as-svc.sh` run.
func directPrincipal() (jobs.Principal, error) {
	rid, err := randomHex(8)
	if err != nil {
		return jobs.Principal{}, err
	}
	return jobs.Principal{
		Subject:   fmt.Sprintf("local:%d", os.Getuid()),
		Role:      "operator",
		Method:    model.AuthLocal,
		SudoUser:  os.Getenv("SUDO_USER"),
		RequestID: rid,
	}, nil
}

func submitDirect(o *opFlags, target opTarget, req model.OpRequest) int {
	eng, err := buildDirectEngine(o)
	if err != nil {
		return failClient(err)
	}
	p, err := directPrincipal()
	if err != nil {
		return failClient(err)
	}
	o.fetchSecrets = func(jobID string) (*model.SecretsResponse, error) {
		return eng.Secrets(context.Background(), jobID, p)
	}
	plan, job, err := eng.Submit(context.Background(), jobs.Request{
		Op:                  target.op,
		Tenant:              target.tenant,
		Args:                req.Args,
		DryRun:              req.DryRun,
		IdempotencyKey:      req.IdempotencyKey,
		Confirm:             req.Confirm,
		ForceWithDoctorDiff: req.ForceWithDoctorDiff,
		Principal:           p,
		Mode:                model.WorkerDirect,
	})
	if err != nil {
		return directExit(err)
	}
	if req.DryRun {
		if plan == nil {
			return failClient(errors.New("the engine produced no plan for a dry run"))
		}
		return printPlan(plan, *o.asJSON)
	}
	if job == nil {
		return failClient(errors.New("the engine accepted no job"))
	}
	// --direct IMPLIES --wait, whether or not the operator typed it.
	//
	// The engine runs a job on a goroutine and returns the accepted snapshot —
	// state `queued` — immediately. This used to print that snapshot and
	// return, so the process exited while its own worker was in the middle of
	// a step: os.Exit does not wait for goroutines, so a `tenant backup
	// --direct` reported "queued", exited 0, and left a half-written bundle
	// and a job row nothing would ever finish. There is no daemon behind a
	// --direct run to pick it up.
	//
	// So the local engine is polled to a state that does not advance on its
	// own, exactly as the HTTP client polls the daemon, and the exit code is
	// the job's.
	return followDirect(eng, o, job)
}

// followDirect polls the in-process engine to a settled state.
func followDirect(eng jobs.Engine, o *opFlags, job *model.Job) int {
	ctx := context.Background()
	return followJob(ctx, o, job, directPollInterval, func() (*model.Job, int, error) {
		next, err := eng.Get(ctx, job.ID)
		if err != nil {
			// A local engine that cannot read back the job it just accepted is
			// not a transient network fault; there is nothing to retry.
			return nil, directExit(err), errStopPolling
		}
		return next, 0, nil
	})
}

// directExit maps an engine error onto the same exit codes the HTTP statuses
// map to, through the jobs package's sentinel errors — the seam the API layer
// maps to statuses, so the two transports refuse identically.
func directExit(err error) int {
	fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
	switch {
	case errors.Is(err, jobs.ErrNotFound):
		return exitError
	case errors.Is(err, jobs.ErrRefused), errors.Is(err, jobs.ErrLocked),
		errors.Is(err, jobs.ErrPlanStale), errors.Is(err, jobs.ErrDoctorRed),
		errors.Is(err, jobs.ErrDuplicate), errors.Is(err, jobs.ErrConfirmRequired),
		errors.Is(err, jobs.ErrValidation), errors.Is(err, jobs.ErrForbidden):
		return exitRefused
	default:
		return exitError
	}
}

// ---------------------------------------------------------------- helpers

// takePositionals pulls up to n leading non-flag arguments off args. The
// binary's house style accepts the positional BEFORE the flags; Go's flag
// package stops at the first non-flag, so the caller appends fs.Args()
// afterwards and an operator may write them in either order.
func takePositionals(args []string, n int) (pos, rest []string) {
	for len(pos) < n && len(args) > 0 && args[0] != "" && args[0][0] != '-' {
		pos = append(pos, args[0])
		args = args[1:]
	}
	return pos, args
}

// multiFlag is a repeatable flag that also accepts a comma list, deduplicated
// because the contract marks these arrays uniqueItems.
type multiFlag []string

func (m *multiFlag) String() string { return strings.Join(*m, ",") }

func (m *multiFlag) Set(v string) error {
	for _, part := range strings.Split(v, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		found := false
		for _, have := range *m {
			if have == part {
				found = true
				break
			}
		}
		if !found {
			*m = append(*m, part)
		}
	}
	return nil
}

// setFlags is the set of flags the operator actually wrote. Args are built
// from it rather than from the flag VALUES so that an unstated boolean is
// ABSENT from `args` — x-ctl-op-args gives these properties `default: false`,
// and a CLI that always sent `force: false` would put a value in the audit
// row and the plan hash that nobody chose.
func setFlags(fs *flag.FlagSet) map[string]bool {
	set := map[string]bool{}
	fs.Visit(func(f *flag.Flag) { set[f.Name] = true })
	return set
}

func usageErr(format string, args ...any) int {
	fmt.Fprintf(stderr, "ragstack-ctl: "+format+"\n", args...)
	return exitUsage
}
