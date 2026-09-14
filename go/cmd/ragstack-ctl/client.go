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
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
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
	registry       *string
	ragRoot        *string
	asJSON         *bool
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
		idempotency:    fs.String("idempotency-key", "", "retry key (generated and printed on stderr when absent)"),
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

// envelope builds op_request.json. The idempotency key is GENERATED when the
// operator gave none and announced on stderr, because the retry of a request
// whose answer was lost has to carry the same key — a fresh key on the retry
// is how one `tenant restore` becomes two.
func (o *opFlags) envelope(args map[string]any) (model.OpRequest, error) {
	key := strings.TrimSpace(*o.idempotency)
	if key == "" {
		var err error
		if key, err = newIdempotencyKey(); err != nil {
			return model.OpRequest{}, err
		}
		fmt.Fprintf(stderr, "ragstack-ctl: idempotency key %s (re-run with --idempotency-key %s to retry this exact request)\n", key, key)
	} else if !idempotencyKeyPattern.MatchString(key) {
		return model.OpRequest{}, fmt.Errorf("--idempotency-key %q is outside op_request.json's ^[A-Za-z0-9._:-]{8,128}$", key)
	}
	if args == nil {
		args = map[string]any{}
	}
	return model.OpRequest{
		DryRun:              *o.dryRun,
		IdempotencyKey:      key,
		Confirm:             o.confirm(),
		ForceWithDoctorDiff: strings.TrimSpace(*o.forceDoctor),
		Args:                args,
	}, nil
}

// newIdempotencyKey is a random key inside op_request.json's pattern. It is
// not a ULID: the contract only constrains the grammar, and "ctl-" plus 128
// bits of randomness is unguessable, sortable enough for a human to compare,
// and obviously machine-generated in an audit row.
func newIdempotencyKey() (string, error) {
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
	return &ctlClient{base: base, key: key, http: &http.Client{Timeout: httpTimeout}}, nil
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
	req, err := o.envelope(args)
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
	for {
		if code, done := waitExit(job.State); done {
			finishWait(job, o)
			return code
		}
		if time.Now().After(deadline) {
			fmt.Fprintf(stderr, "ragstack-ctl: job %s is still %s after %s — `ragstack-ctl job show %s`\n",
				job.ID, job.State, jobWaitTimeout, job.ID)
			return exitError
		}
		select {
		case <-ctx.Done():
			return failClient(ctx.Err())
		case <-time.After(jobPollInterval):
		}
		resp, err := c.get(ctx, "/v1/jobs/"+job.ID, nil)
		if err != nil {
			return failClient(err)
		}
		if resp.Status != http.StatusOK {
			return reportHTTPError(resp)
		}
		var next model.Job
		if err := json.Unmarshal(resp.Body, &next); err != nil {
			return failClient(fmt.Errorf("polling job %s: %w", job.ID, err))
		}
		job = &next
		report(job)
	}
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
		// Not a failure: the job did exactly what handover/migrate-local
		// plan, and the next move is the operator's.
		return exitOK, true
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
	host, err := os.Hostname()
	if err != nil {
		return nil, fmt.Errorf("--direct records the worker host and this host has none: %w", err)
	}
	roots := paths.NewRoots(*o.ragRoot, paths.Overrides{})
	return api.BuildEngine(api.EngineConfig{
		Roots:        roots,
		RegistryPath: resolveRegistry(*o.registry, *o.ragRoot),
		StorePath:    filepath.Join(roots.CtlStateDir, "jobs.db"),
		Mode:         model.WorkerDirect,
		Host:         host,
		SecretsTTL:   api.DefaultSecretsTTL,
		Now:          time.Now,
	})
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
	// --direct runs the job in this process, so there is nothing to poll: the
	// engine returns once the job has moved as far as it will.
	if *o.wait {
		if code, done := waitExit(job.State); done {
			finishWait(job, o)
			return code
		}
	}
	return printJob(job, *o.asJSON)
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
