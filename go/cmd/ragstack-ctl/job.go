package main

// `ragstack-ctl job …` — the job surface: three reads (list, show, log) and
// the three continuations (resume, continue, cancel).
//
// The continuations go through the SAME envelope as an operation, because the
// contract gives them the same body: an idempotency key on a resume is what
// stops a double-pressed retry from re-running the step the first one is
// still in the middle of.

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"net/url"
	"strconv"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

func jobUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl job <verb> … [--server URL] [--api-key-file F] [--json]

  list [--tenant T] [--state S] [--limit N]   jobs, newest first
  show <id>                                   one job, its steps and its outcome
  log  <id> <n> [--lines N]                   the redacted tail of step n's log
  resume   <id>                               re-run an interrupted job from its last checkpoint
  continue <id>                               release a job waiting in awaiting_cutover
  cancel   <id>                               stop a job, rolling back what can be rolled back

exit: 0 ok · 1 error · 2 usage · 3 refused · 4 job failed · 5 job interrupted
`)
	return exitUsage
}

// readFlags is the flag set the three job READS share. They are reads, so
// they carry no envelope: no --dry-run, no --yes, no idempotency key.
type readFlags struct {
	server     *string
	apiKeyFile *string
	asJSON     *bool
}

func addReadFlags(fs *flag.FlagSet, jsonOut bool) *readFlags {
	return &readFlags{
		server:     fs.String("server", envOrDefault(envServer, defaultServer), "control-plane base URL"),
		apiKeyFile: fs.String("api-key-file", "", "file holding the ctl API key (else $"+envAPIKey+")"),
		asJSON:     fs.Bool("json", jsonOut, "machine-readable output"),
	}
}

func (r *readFlags) client() (*ctlClient, error) { return newCtlClient(*r.server, *r.apiKeyFile) }

func cmdJob(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return jobUsage()
	}
	verb, rest := args[0], args[1:]
	switch verb {
	case "list":
		return cmdJobList(rest, jsonOut)
	case "show":
		return cmdJobShow(rest, jsonOut)
	case "log":
		return cmdJobLog(rest, jsonOut)
	case "resume", "continue", "cancel":
		return cmdJobContinuation(verb, rest, registryPath, ragRoot, jsonOut)
	case "help", "-h", "--help":
		jobUsage()
		return exitOK
	default:
		return usageErr("job: unknown verb %q (list|show|log|resume|continue|cancel)", verb)
	}
}

func cmdJobList(args []string, jsonOut bool) int {
	fs := flag.NewFlagSet("job list", flag.ContinueOnError)
	fs.SetOutput(stderr)
	r := addReadFlags(fs, jsonOut)
	tenant := fs.String("tenant", "", "restrict to one tenant")
	state := fs.String("state", "", "restrict to one job state")
	limit := fs.Int("limit", 0, "how many jobs (1..500, default 50)")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	q := url.Values{}
	if *tenant != "" {
		q.Set("tenant", *tenant)
	}
	if *state != "" {
		q.Set("state", *state)
	}
	if *limit > 0 {
		q.Set("limit", strconv.Itoa(*limit))
	}
	c, err := r.client()
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	resp, err := c.get(context.Background(), "/v1/jobs", q)
	if err != nil {
		return failClient(err)
	}
	if resp.Status != http.StatusOK {
		return reportHTTPError(resp)
	}
	if *r.asJSON {
		return writeRaw(resp.Body)
	}
	var list model.JobsResponse
	if err := json.Unmarshal(resp.Body, &list); err != nil {
		return failClient(fmt.Errorf("the job list is not jobs_response.json: %w", err))
	}
	fmt.Fprintf(stdout, "%-28s %-16s %-12s %-16s %s\n", "JOB", "OP", "TENANT", "STATE", "CREATED")
	for _, j := range list.Jobs {
		fmt.Fprintf(stdout, "%-28s %-16s %-12s %-16s %s\n",
			j.ID, j.Op, orNone(string(j.Tenant)), j.State, j.CreatedAt)
	}
	if list.Truncated {
		fmt.Fprintf(stderr, "(truncated at the %d-job limit)\n", list.Limit)
	}
	return exitOK
}

func cmdJobShow(args []string, jsonOut bool) int {
	fs := flag.NewFlagSet("job show", flag.ContinueOnError)
	fs.SetOutput(stderr)
	r := addReadFlags(fs, jsonOut)
	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return usageErr("usage: ragstack-ctl job show <id>")
	}
	c, err := r.client()
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	resp, err := c.get(context.Background(), "/v1/jobs/"+pos[0], nil)
	if err != nil {
		return failClient(err)
	}
	if resp.Status != http.StatusOK {
		return reportHTTPError(resp)
	}
	if *r.asJSON {
		return writeRaw(resp.Body)
	}
	var j model.Job
	if err := json.Unmarshal(resp.Body, &j); err != nil {
		return failClient(fmt.Errorf("job %s is not job.json: %w", pos[0], err))
	}
	printJobDetail(&j)
	return exitOK
}

func printJobDetail(j *model.Job) {
	fmt.Fprintf(stdout, "job %s · %s %s · %s\n", j.ID, j.Op, orNone(string(j.Tenant)), j.State)
	fmt.Fprintf(stdout, "principal %s (%s) · request %s · idempotency %s\n",
		j.Principal, j.AuthMethod, j.RequestID, j.IdempotencyKey)
	fmt.Fprintf(stdout, "created %s · started %s · finished %s\n",
		j.CreatedAt, orNone(string(j.StartedAt)), orNone(string(j.FinishedAt)))
	if j.Worker != nil {
		fmt.Fprintf(stdout, "worker pid %d on %s (%s)\n", j.Worker.PID, j.Worker.Host, j.Worker.Mode)
	}
	if j.Lock != nil && len(j.Lock.Order) > 0 {
		fmt.Fprintf(stdout, "locks %v since %s\n", j.Lock.Order, j.Lock.Since)
	}
	for _, s := range j.Steps {
		fmt.Fprintf(stdout, "  %2d %-12s %-28s %-12s attempts %d\n", s.N, s.Kind, s.Title, s.State, s.Attempts)
		if d := string(s.Error); d != "" {
			fmt.Fprintf(stdout, "       error %s\n", d)
		}
	}
	for _, r := range j.Reservations {
		fmt.Fprintf(stdout, "  reserved %s until %s\n", r.Resource, orNone(string(r.Until)))
	}
	if j.Error != nil {
		fmt.Fprintf(stdout, "error %s: %s\n", j.Error.Code, j.Error.Detail)
	}
	if j.Rollback != nil && j.Rollback.Attempted {
		fmt.Fprintf(stdout, "rollback %s %s\n", j.Rollback.State, orNone(string(j.Rollback.Detail)))
	}
}

func cmdJobLog(args []string, jsonOut bool) int {
	fs := flag.NewFlagSet("job log", flag.ContinueOnError)
	fs.SetOutput(stderr)
	r := addReadFlags(fs, jsonOut)
	lines := fs.Int("lines", 0, "how many lines from the end (1..5000, default 200)")
	pos, rest := takePositionals(args, 2)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 2 {
		return usageErr("usage: ragstack-ctl job log <id> <step> [--lines N]")
	}
	n, err := strconv.Atoi(pos[1])
	if err != nil || n < 1 {
		return usageErr("job log: the step number must be a positive integer, not %q\nusage: ragstack-ctl job log <id> <step> [--lines N]", pos[1])
	}
	q := url.Values{}
	if *lines > 0 {
		q.Set("lines", strconv.Itoa(*lines))
	}
	c, cerr := r.client()
	if cerr != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", cerr)
		return exitUsage
	}
	resp, err := c.get(context.Background(), fmt.Sprintf("/v1/jobs/%s/steps/%d/log", pos[0], n), q)
	if err != nil {
		return failClient(err)
	}
	if resp.Status != http.StatusOK {
		return reportHTTPError(resp)
	}
	if *r.asJSON {
		return writeRaw(resp.Body)
	}
	var log model.StepLogResponse
	if err := json.Unmarshal(resp.Body, &log); err != nil {
		return failClient(fmt.Errorf("the step log is not step_log_response: %w", err))
	}
	for _, l := range log.Lines {
		fmt.Fprintln(stdout, l)
	}
	if log.Truncated {
		fmt.Fprintf(stderr, "(truncated: %d of the last %d lines, redacted)\n", log.Returned, log.Requested)
	}
	return exitOK
}

// cmdJobContinuation is resume, continue and cancel: an OpRequest body with
// `args: {}` posted to /v1/jobs/{id}/{verb}.
func cmdJobContinuation(verb string, args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("job "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return usageErr("usage: ragstack-ctl job %s <id> %s", verb, opFlagSummary)
	}
	id := pos[0]
	if *o.direct {
		return continueDirect(o, verb, id)
	}
	req, err := o.envelope(map[string]any{})
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	c, err := o.client()
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	ctx := context.Background()
	resp, err := c.do(ctx, http.MethodPost, "/v1/jobs/"+id+"/"+verb, req)
	if err != nil {
		return failClient(err)
	}
	switch resp.Status {
	case http.StatusOK:
		// A continuation has no dry run in the contract, but a daemon that
		// answers a Plan is answering the documented 200 for this route, so
		// the client renders it rather than calling it a fault.
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

// continueDirect runs a continuation through the in-process engine.
//
// TODO(integration): like submitDirect, this works the moment
// api.BuildEngine stops returning api.ErrEngineNotWired. Nothing is stubbed
// here on purpose.
func continueDirect(o *opFlags, verb, id string) int {
	eng, err := buildDirectEngine(o)
	if err != nil {
		return failClient(err)
	}
	p, err := directPrincipal()
	if err != nil {
		return failClient(err)
	}
	ctx := context.Background()
	var job *model.Job
	switch verb {
	case "resume":
		job, err = eng.Resume(ctx, id, p)
	case "continue":
		job, err = eng.Continue(ctx, id, p)
	case "cancel":
		job, err = eng.Cancel(ctx, id, p)
	}
	if err != nil {
		return directExit(err)
	}
	if job == nil {
		return failClient(fmt.Errorf("the engine returned no job for %s %s", verb, id))
	}
	if *o.wait {
		if code, done := waitExit(job.State); done {
			finishWait(job, o)
			return code
		}
	}
	return printJob(job, *o.asJSON)
}

// writeRaw prints a response body verbatim under --json. Re-encoding the
// decoded struct instead would silently drop any member this build's model
// does not know, which is exactly the member a script would be asking about.
func writeRaw(b []byte) int {
	if _, err := stdout.Write(b); err != nil {
		return fail(err)
	}
	if len(b) > 0 && b[len(b)-1] != '\n' {
		fmt.Fprintln(stdout)
	}
	return exitOK
}
