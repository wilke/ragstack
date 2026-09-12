package main

// `ragstack-ctl gateway …` — generation-based publishing of the two generated
// nginx includes. Every verb here reads the registry; only `apply`,
// `rollback` and `repair` write, and what they write lives under
// <state>/gateway/ plus two symlinks in the proxy tree. No verb ever edits a
// file the coconut-proxy repo tracks.

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// stdin is indirected for the same reason stdout/stderr are: the confirmation
// prompt has to be testable.
var stdin io.Reader = os.Stdin

func gatewayUsage() {
	fmt.Fprint(stderr, `usage: ragstack-ctl gateway <verb> [flags]

  render   [--out DIR]                     render the next generation (writes only to --out)
  diff                                     unified diff of the render vs what is published
  apply    [--dry-run] [--yes] [--expect-bodies DIR]
                                           publish: stage + nginx -t, switch, HUP, probe, revert on failure
                                           --dry-run stops after the staged nginx -t (no switch, no HUP)
  status   [--json]                        published generation, txn state, nginx master, routes
  rollback [--to N]                        switch back + HUP + probe (refuses without a previous generation)
  repair                                   put `+"`current`"+` back on the last verified generation and acknowledge it

common flags: --registry PATH --rag-root DIR --state-dir DIR --proxy-dir DIR
              --base-url URL --pidfile PATH --nginx-sif PATH

exit: 0 ok · 1 error · 2 usage · 3 refused
`)
}

// gatewayFlags adds the flags every gateway verb accepts and returns a
// resolver that turns them into roots + registry + publish options.
type gatewayFlags struct {
	registry  *string
	ragRoot   *string
	stateDir  *string
	proxyDir  *string
	baseURL   *string
	pidfile   *string
	nginxSIF  *string
	apptainer *string
}

func addGatewayFlags(fs *flag.FlagSet, registryPath, ragRoot string) *gatewayFlags {
	return &gatewayFlags{
		registry:  fs.String("registry", registryPath, "registry.json path"),
		ragRoot:   fs.String("rag-root", ragRoot, "deployment root"),
		stateDir:  fs.String("state-dir", "", "ctl state dir (default <rag-root>/data/ctl)"),
		proxyDir:  fs.String("proxy-dir", "", "nginx tree (default <rag-root>/config/proxy)"),
		baseURL:   fs.String("base-url", gateway.DefaultBaseURL, "gateway base URL for the probes"),
		pidfile:   fs.String("pidfile", "", "nginx master pidfile (default <proxy-dir>/run/nginx.pid)"),
		nginxSIF:  fs.String("nginx-sif", "", "nginx image (default <rag-root>/apptainer/images/nginx.sif)"),
		apptainer: fs.String("apptainer", "/usr/bin/apptainer", "apptainer binary"),
	}
}

func (g *gatewayFlags) resolve() (paths.Roots, *registry.Fleet, gateway.Options, error) {
	roots := paths.NewRoots(*g.ragRoot, paths.Overrides{CtlStateDir: *g.stateDir, ProxyDir: *g.proxyDir})
	f, err := registry.LoadNoRepair(resolveRegistry(*g.registry, *g.ragRoot))
	if err != nil {
		return roots, nil, gateway.Options{}, err
	}
	opts := gateway.Options{
		Roots:     roots,
		Exec:      gateway.NewRealExec(),
		Sig:       gateway.NewRealSignaller(),
		Prober:    gateway.NewRealProber(),
		By:        auditPrincipal(),
		BaseURL:   *g.baseURL,
		PIDFile:   *g.pidfile,
		NginxSIF:  *g.nginxSIF,
		Apptainer: *g.apptainer,
	}
	return roots, f, opts, nil
}

// auditPrincipal names WHO published: under ops/coconut/ctl-as-svc.sh the
// process runs as svcbvbrc but SUDO_USER still says which human asked.
func auditPrincipal() string {
	user := os.Getenv("USER")
	if user == "" {
		user = fmt.Sprintf("uid:%d", os.Getuid())
	}
	if sudo := os.Getenv("SUDO_USER"); sudo != "" && sudo != user {
		return sudo + " as " + user
	}
	return user
}

// gatewayExit maps an error to the CLI's exit codes: a refusal is 3, anything
// else is 1.
func gatewayExit(err error) int {
	if err == nil {
		return exitOK
	}
	fmt.Fprintf(stderr, "ragstack-ctl gateway: %v\n", err)
	if errors.Is(err, gateway.ErrRefused) {
		return exitRefused
	}
	return exitError
}

func cmdGateway(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		gatewayUsage()
		return exitUsage
	}
	verb, rest := args[0], args[1:]
	switch verb {
	case "render":
		return cmdGatewayRender(rest, registryPath, ragRoot, jsonOut)
	case "diff":
		return cmdGatewayDiff(rest, registryPath, ragRoot, jsonOut)
	case "apply":
		return cmdGatewayApply(rest, registryPath, ragRoot, jsonOut)
	case "status":
		return cmdGatewayStatus(rest, registryPath, ragRoot, jsonOut)
	case "rollback":
		return cmdGatewayRollback(rest, registryPath, ragRoot, jsonOut)
	case "repair":
		return cmdGatewayRepair(rest, registryPath, ragRoot, jsonOut)
	case "help", "-h", "--help":
		gatewayUsage()
		return exitOK
	default:
		fmt.Fprintf(stderr, "ragstack-ctl gateway: unknown verb %q\n", verb)
		gatewayUsage()
		return exitUsage
	}
}

func cmdGatewayRender(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("gateway render", flag.ContinueOnError)
	fs.SetOutput(stderr)
	g := addGatewayFlags(fs, registryPath, ragRoot)
	out := fs.String("out", "", "write the generation into this directory instead of stdout")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	roots, f, _, err := g.resolve()
	if err != nil {
		return gatewayExit(err)
	}
	gen, err := gateway.Render(f, roots)
	if err != nil {
		return gatewayExit(err)
	}
	if *out != "" {
		for _, rel := range gateway.RelPaths() {
			p := filepath.Join(*out, filepath.FromSlash(rel))
			if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
				return gatewayExit(err)
			}
			if err := os.WriteFile(p, gen.Files[rel], 0o640); err != nil {
				return gatewayExit(err)
			}
		}
		b, err := json.MarshalIndent(gen.Manifest(), "", "  ")
		if err != nil {
			return gatewayExit(err)
		}
		if err := os.WriteFile(filepath.Join(*out, gateway.ManifestName), append(b, '\n'), 0o640); err != nil {
			return gatewayExit(err)
		}
	}
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(map[string]any{
			"generation": gen.N, "registry_generation": gen.RegistryGeneration,
			"sha256": gen.SHA256(), "out": *out, "manifest": gen.Manifest(),
		}); err != nil {
			return gatewayExit(err)
		}
		return exitOK
	}
	fmt.Fprintf(stdout, "generation %d from registry generation %d (%s)\n", gen.N, gen.RegistryGeneration, gen.SHA256())
	for _, rel := range gateway.RelPaths() {
		if *out != "" {
			fmt.Fprintf(stdout, "  wrote %s (%d bytes)\n", filepath.Join(*out, rel), len(gen.Files[rel]))
			continue
		}
		fmt.Fprintf(stdout, "\n===== %s =====\n%s", rel, gen.Files[rel])
	}
	return exitOK
}

func cmdGatewayDiff(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("gateway diff", flag.ContinueOnError)
	fs.SetOutput(stderr)
	g := addGatewayFlags(fs, registryPath, ragRoot)
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	roots, f, _, err := g.resolve()
	if err != nil {
		return gatewayExit(err)
	}
	d, err := gateway.DiffDetail(roots, f)
	if err != nil {
		return gatewayExit(err)
	}
	if *asJSON {
		// The contract's response has no room for the semantic verdict
		// (additionalProperties:false), so the CLI reports it beside it.
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(map[string]any{
			"render": d.Response, "compared_to": d.ComparedTo,
			"semantic_noop": d.SemanticNoop, "notes": d.Notes,
		}); err != nil {
			return gatewayExit(err)
		}
		return exitOK
	}
	printDiff(d)
	return exitOK
}

func printDiff(d *gateway.DiffResult) {
	fmt.Fprintf(stdout, "generation %d (would publish) vs %s — registry generation %d\n",
		d.Response.Generation, d.ComparedTo, d.Response.RegistryGeneration)
	for _, file := range d.Response.Files {
		if file.Diff == "" {
			fmt.Fprintf(stdout, "\n%s: unchanged\n", file.Path)
			continue
		}
		fmt.Fprintf(stdout, "\n%s:\n%s", file.Path, file.Diff)
	}
	fmt.Fprintln(stdout)
	if d.SemanticNoop {
		fmt.Fprintln(stdout, "semantic_noop: true — the generated maps route exactly what is routed today")
	} else {
		fmt.Fprintln(stdout, "semantic_noop: false")
		for _, n := range d.Notes {
			fmt.Fprintf(stdout, "  %s\n", n)
		}
	}
}

func cmdGatewayApply(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("gateway apply", flag.ContinueOnError)
	fs.SetOutput(stderr)
	g := addGatewayFlags(fs, registryPath, ragRoot)
	dryRun := fs.Bool("dry-run", false, "render + stage + nginx -t only; no switch, no HUP")
	yes := fs.Bool("yes", false, "do not ask for confirmation")
	expect := fs.String("expect-bodies", "", "directory of golden gateway bodies to compare byte-for-byte")
	keepStage := fs.Bool("keep-stage", false, "leave the staged copy of the proxy tree behind")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	roots, f, opts, err := g.resolve()
	if err != nil {
		return gatewayExit(err)
	}
	opts.DryRun, opts.KeepStage = *dryRun, *keepStage
	if *expect != "" {
		bodies, err := gateway.LoadExpectBodies(*expect)
		if err != nil {
			return gatewayExit(fmt.Errorf("reading the golden bodies: %w", err))
		}
		opts.ExpectBodies = bodies
	}
	if !*asJSON {
		opts.Log = func(step, msg string) { fmt.Fprintf(stdout, "  [%s] %s\n", step, msg) }
	}

	// ---- the plan ---------------------------------------------------------
	d, err := gateway.DiffDetail(roots, f)
	if err != nil {
		return gatewayExit(err)
	}
	if !*asJSON {
		fmt.Fprintln(stdout, "plan:")
		printDiff(d)
		fmt.Fprintf(stdout, "\nfiles:      %s\n", strings.Join(gateway.RelPaths(), ", "))
		fmt.Fprintf(stdout, "state dir:  %s\n", filepath.Join(roots.CtlStateDir, "gateway"))
		fmt.Fprintf(stdout, "proxy tree: %s\n", roots.ProxyDir)
		fmt.Fprintf(stdout, "nginx:      %s\n", describeMaster(opts))
		if len(opts.ExpectBodies) > 0 {
			fmt.Fprintf(stdout, "goldens:    %d bodies compared byte-for-byte\n", len(opts.ExpectBodies))
		} else {
			fmt.Fprint(stdout, goldenBodiesWarning)
		}
	}
	// Under --json nothing but JSON may reach stdout: the caller is a script,
	// and a banner or a prompt in front of the document breaks the parse. The
	// dry-run banner is suppressed, and a confirmation that CANNOT be given —
	// JSON mode has no terminal to answer it — is a refusal stated as JSON
	// rather than a read on a stdin nobody is typing into.
	if *dryRun {
		if !*asJSON {
			fmt.Fprintln(stdout, "\n--dry-run: staging a copy of the proxy tree and running nginx -t; nothing is switched or reloaded.")
		}
	} else if !*yes {
		if *asJSON {
			writeJSONError(exitRefused, "refused",
				"`gateway apply --json` cannot ask for confirmation; pass --yes to state it on the command line")
			return exitRefused
		}
		fmt.Fprint(stdout, "\npublish this generation? [y/N] ")
		if !confirmed() {
			fmt.Fprintln(stderr, "ragstack-ctl gateway: refused by the operator")
			return exitRefused
		}
	}

	// ---- execute ----------------------------------------------------------
	res, err := gateway.Publish(context.Background(), f, opts)
	if len(opts.ExpectBodies) == 0 && res != nil {
		// In the RESULT too, not only in the plan: --json prints no banner, and
		// the record of the publish has to say that its go/no-go was skipped.
		res.Warnings = append([]string{
			"--expect-bodies was not passed: the four golden gateway bodies were NOT compared, so this publish did " +
				"not check that nothing a client can see changed",
		}, res.Warnings...)
	}
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(res)
	} else {
		printResult(res)
	}
	return gatewayExit(err)
}

// goldenBodiesWarning is printed when a publish runs without --expect-bodies.
//
// The four golden bodies are PR-B's whole go/no-go: "moving the tenant maps
// into a generated include changes nothing a client can see". Without them the
// probe still checks the tenant list and per-tenant health, which a gateway
// serving the OLD configuration also passes whenever the change is a no-op —
// so the publish can report `verified` having verified the one thing it exists
// to prove. That the check is skipped has to be as visible as the result.
const goldenBodiesWarning = "\n" +
	"!! --expect-bodies was NOT passed: the four golden gateway bodies (/, /ragstack/tenants,\n" +
	"!! an unknown tenant's API, the catch-all 404) are NOT compared, so this publish does not\n" +
	"!! check the one thing it exists to prove — that nothing a client can see changed.\n" +
	"!! Pass --expect-bodies /rag/data/ctl/goldens (or <checkout>/go/internal/ctl/testdata/live-2026-09-10/gateway).\n"

// writeJSONError emits a contract-shaped error document for a CLI refusal that
// happens before anything a Result could describe.
func writeJSONError(code int, kind, detail string) {
	enc := json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	_ = enc.Encode(map[string]any{"error": map[string]any{"code": kind, "detail": detail}, "exit": code})
}

// describeMaster reports the master the reload would signal, without signalling
// anything. A pidfile that names nothing is a fact worth printing in the plan.
func describeMaster(opts gateway.Options) string {
	pidfile := opts.PIDFile
	if pidfile == "" {
		pidfile = filepath.Join(opts.Roots.ProxyDir, "run", "nginx.pid")
	}
	b, err := os.ReadFile(pidfile)
	if err != nil {
		return fmt.Sprintf("no pidfile at %s (%v)", pidfile, err)
	}
	var pid int
	if _, err := fmt.Sscanf(strings.TrimSpace(string(b)), "%d", &pid); err != nil || pid <= 0 {
		return fmt.Sprintf("%s does not contain a pid", pidfile)
	}
	info, err := opts.Sig.Proc(pid)
	if err != nil || !info.Exists {
		return fmt.Sprintf("pid %d (from %s) is not running", pid, pidfile)
	}
	owner := "this account"
	if info.UID != opts.Sig.Self() {
		owner = fmt.Sprintf("uid %d — NOT this account (uid %d); the reload will be refused", info.UID, opts.Sig.Self())
	}
	return fmt.Sprintf("master pid %d (%s), owned by %s", pid, info.Comm, owner)
}

func printResult(res *gateway.Result) {
	fmt.Fprintf(stdout, "\nresult: generation %d (previous %d) state %s\n", res.Generation, res.PreviousGeneration, res.State)
	for _, s := range res.Steps {
		mark := "ok  "
		if !s.OK {
			mark = "FAIL"
		}
		fmt.Fprintf(stdout, "  %s %-10s %s\n", mark, s.Name, s.Detail)
	}
	for _, w := range res.Warnings {
		fmt.Fprintf(stdout, "  warning: %s\n", w)
	}
	if res.ConfigTest != "" {
		fmt.Fprintf(stdout, "nginx -t:\n%s\n", res.ConfigTest)
	}
}

func confirmed() bool {
	line, err := bufio.NewReader(stdin).ReadString('\n')
	if err != nil && line == "" {
		return false
	}
	switch strings.ToLower(strings.TrimSpace(line)) {
	case "y", "yes":
		return true
	}
	return false
}

func cmdGatewayStatus(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("gateway status", flag.ContinueOnError)
	fs.SetOutput(stderr)
	g := addGatewayFlags(fs, registryPath, ragRoot)
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	roots, f, opts, err := g.resolve()
	if err != nil {
		return gatewayExit(err)
	}
	s, err := gateway.StatusWith(roots, f, opts)
	if err != nil {
		return gatewayExit(err)
	}
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(s); err != nil {
			return gatewayExit(err)
		}
		return exitOK
	}
	fmt.Fprintf(stdout, "generation:          %d\n", s.Generation)
	fmt.Fprintf(stdout, "registry generation: %d (published) / %d (current)\n", s.RegistryGeneration, f.Generation)
	fmt.Fprintf(stdout, "txn state:           %s\n", s.TxnState)
	fmt.Fprintf(stdout, "pending diff:        %v\n", s.PendingDiff)
	fmt.Fprintf(stdout, "published:           %s by %s\n", deref(s.PublishedAt, "never"), deref(s.PublishedBy, "-"))
	fmt.Fprintf(stdout, "nginx:               pid %s uid %s config_ok %s last reload %s\n",
		derefInt(s.Nginx.MasterPID), derefInt(s.Nginx.MasterUID), derefBool(s.Nginx.ConfigOK), deref(s.Nginx.LastReloadAt, "-"))
	fmt.Fprintln(stdout, "\nroutes:")
	for _, r := range append(append([]model.GatewayRoute{}, s.Routes...), s.LegacyRoutes...) {
		fmt.Fprintf(stdout, "  %-12s api %-6d ui %-6s %-8s %s\n", r.Name, r.API, derefInt(r.UI), r.UIMode, r.Status)
	}
	return exitOK
}

func deref(p *string, dflt string) string {
	if p == nil || *p == "" {
		return dflt
	}
	return *p
}

func derefInt(p *int) string {
	if p == nil {
		return "-"
	}
	return fmt.Sprintf("%d", *p)
}

func derefBool(p *bool) string {
	if p == nil {
		return "-"
	}
	return fmt.Sprintf("%v", *p)
}

func cmdGatewayRollback(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("gateway rollback", flag.ContinueOnError)
	fs.SetOutput(stderr)
	g := addGatewayFlags(fs, registryPath, ragRoot)
	to := fs.Int("to", 0, "generation to roll back to (default: the previous one)")
	yes := fs.Bool("yes", false, "do not ask for confirmation")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	_, f, opts, err := g.resolve()
	if err != nil {
		return gatewayExit(err)
	}
	if !*asJSON {
		opts.Log = func(step, msg string) { fmt.Fprintf(stdout, "  [%s] %s\n", step, msg) }
	}
	if !*yes {
		// Same rule as apply: --json has no terminal to answer a prompt, so the
		// refusal is stated as JSON rather than read off a stdin nobody is
		// typing into.
		if *asJSON {
			writeJSONError(exitRefused, "refused",
				"`gateway rollback --json` cannot ask for confirmation; pass --yes to state it on the command line")
			return exitRefused
		}
		fmt.Fprintf(stdout, "roll the gateway back to generation %s and reload? [y/N] ", rollbackTarget(*to))
		if !confirmed() {
			fmt.Fprintln(stderr, "ragstack-ctl gateway: refused by the operator")
			return exitRefused
		}
	}
	res, err := gateway.Rollback(context.Background(), f, *to, opts)
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(res)
	} else {
		printResult(res)
	}
	return gatewayExit(err)
}

func rollbackTarget(to int) string {
	if to == 0 {
		return "the previous one"
	}
	return fmt.Sprintf("%d", to)
}

func cmdGatewayRepair(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("gateway repair", flag.ContinueOnError)
	fs.SetOutput(stderr)
	g := addGatewayFlags(fs, registryPath, ragRoot)
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	roots, _, opts, err := g.resolve()
	if err != nil {
		return gatewayExit(err)
	}
	st := gateway.NewState(roots)
	rep, err := st.Repair()
	if err != nil {
		return gatewayExit(err)
	}
	if rep.Incomplete {
		if err := st.Acknowledge(opts.By); err != nil {
			return gatewayExit(err)
		}
	}
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(rep); err != nil {
			return gatewayExit(err)
		}
		return exitOK
	}
	if !rep.Incomplete {
		fmt.Fprintf(stdout, "nothing to repair: current is gen-%d and the last publication finished\n", rep.CurrentGeneration)
		return exitOK
	}
	fmt.Fprintf(stdout, "the last publication (gen-%d, state %s) never finished\n", rep.Generation, rep.State)
	fmt.Fprintf(stdout, "action: %s\n", rep.Action)
	// repair does NOT signal the master — see gateway.State.Repair. The running
	// workers are still on whatever they last loaded, so the pointer and the
	// process disagree until this command is run, and saying which command is
	// the whole point of printing anything here.
	fmt.Fprintf(stdout, "current is now gen-%d. `repair` does not reload: run %s to make the running workers agree\n",
		rep.CurrentGeneration, filepath.Join(roots.ProxyDir, "proxy.sh")+" reload")
	return exitOK
}
