// ragstack-ctl — tenant control plane CLI (plan v3, PR-A skeleton).
//
// Exit codes: 0 ok · 1 error · 2 usage · 3 refused · 4 job failed ·
// 5 job interrupted. Only 0/1/2 are produced by this PR.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/adopt"
	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/logs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/version"
)

const (
	exitOK      = 0
	exitError   = 1
	exitUsage   = 2
	exitRefused = 3
)

var stdout io.Writer = os.Stdout
var stderr io.Writer = os.Stderr

func usage() {
	fmt.Fprint(stderr, `usage: ragstack-ctl [--json] [--registry PATH] <group> <verb> [args] [flags]

  version                                   build identity (JSON)
  paths validate <name>                     tenant-name grammar + reserved list
  render tenant-env <name> [--index N] [--rag-root R] [--store sqlite|postgres --pg-host H --pg-port P]
  render up-sh <name> [--index N] [--rag-root R] [--images DIR] [--es-heap SIZE]
  render down-sh <name>
  render units <name> --registry PATH [--allow-non-loopback-bind]
                                            per-tenant systemd --user units
  render nginx [--registry PATH] [--kind tenants|static]

  adopt <name> --data-dir D --worktree W [--manifest-name M] [--ui-port P] [--force] --preview|--commit
                                            read a hand-started tenant into a registry row
  adopt-all --preview|--commit [--spec FILE] [--force] [--repair-projection]
                                            the four live coconut tenants in one batch (see below)
  registry repair [--registry PATH]         rewrite manifest.tsv FROM the registry (explicit, destructive)
  doctor [<name>] [--op VERB] [--json]      diagnose the host/a tenant (exit 3 when red)
  wait-ready <name> [--timeout S]           block until the tenant's own stores answer (unit ExecStartPre)
  fleet status [--json]                     the dashboard view: host band + one row per tenant
  tenant list [--json]                      every tenant in display order
  tenant show <name> [--json]               one tenant: summary, live status, units, drift
  tenant logs <name> --file api|qdrant|es|ui [--lines N]
                                            bounded, redacted tail (never the on-disk path)

  serve [--listen 127.0.0.1:23990] [--fake-drivers] [--registry PATH] [--rag-root DIR]
                                            the read-only control-plane HTTP API (PR-A)
  key | admin | sa | env | units | gateway | job | backup | selftest
                                            not implemented in this PR

adopt-all on coconut adopts, in this order (data dirs and worktrees derived
from --rag-root, i.e. /rag by default):

  lucid-next  --data-dir /rag/data/tenants/lucid --manifest-name lucid \
              --worktree /rag/repos/tenants/lucid-next --ui-port 5211
  asm-next    --data-dir /rag/data/tenants/asm   --manifest-name asm \
              --worktree /rag/repos/tenants/asm-next  --ui-port 5212
  dev         --data-dir /rag/data/tenants/dev   --worktree /rag/repos/tenants/dev  --ui-port 8090
  demo        --data-dir /rag/data/tenants/demo  --worktree /rag/repos/tenants/demo --ui-port 5210

  ragstack-ctl adopt-all --preview --registry /tmp/preview.json   # nothing written
  ragstack-ctl adopt-all --commit  --registry /tmp/preview.json   # writes that file + its manifest.tsv

--preview writes nothing at all; --commit writes ONLY the registry file named
by --registry and the manifest.tsv beside it. Point --registry at a scratch
path to rehearse. --commit REFUSES (exit 3) when the preview raised any
error-level finding — a disallowed store URL, an unattributable API port —
unless --force says to record the row anyway.

A read NEVER rewrites manifest.tsv: a reader that repaired the projection
would truncate a live four-row manifest down to a one-tenant registry, and the
next apptainer/new-tenant.sh would reissue index 0 onto a live tenant's ports.
A stale projection is reported by every read and repaired only by
"registry repair" (or "adopt-all --commit --repair-projection").

exit: 0 ok · 1 error · 2 usage · 3 refused (doctor red, a manifest that does
not reconcile, a stale projection, a tenant already adopted)

Renderers print dry-run placeholders (<GENERATED:*>) — never real secrets.
`)
}

func main() { os.Exit(run(os.Args[1:])) }

func run(args []string) int {
	global := flag.NewFlagSet("ragstack-ctl", flag.ContinueOnError)
	global.SetOutput(stderr)
	jsonOut := global.Bool("json", false, "machine-readable output")
	registryPath := global.String("registry", paths.NewRoots("/rag", paths.Overrides{}).Registry(), "registry.json path")
	globalRagRoot := global.String("rag-root", "/rag", "deployment root")
	global.Usage = usage
	if err := global.Parse(args); err != nil {
		return exitUsage
	}
	rest := global.Args()
	if len(rest) == 0 {
		usage()
		return exitUsage
	}
	switch rest[0] {
	case "version":
		return cmdVersion(*jsonOut)
	case "paths":
		return cmdPaths(rest[1:])
	case "render":
		return cmdRender(rest[1:], *registryPath)
	case "adopt":
		return cmdAdopt(rest[1:], *registryPath, *globalRagRoot, *jsonOut)
	case "adopt-all":
		return cmdAdoptAll(rest[1:], *registryPath, *globalRagRoot, *jsonOut)
	case "registry":
		return cmdRegistry(rest[1:], *registryPath, *globalRagRoot)
	case "doctor":
		return cmdDoctor(rest[1:], *registryPath, *globalRagRoot, *jsonOut)
	case "fleet":
		return cmdFleet(rest[1:], *registryPath, *globalRagRoot, *jsonOut)
	case "tenant":
		return cmdTenant(rest[1:], *registryPath, *globalRagRoot, *jsonOut)
	case "wait-ready":
		return cmdWaitReady(rest[1:], *registryPath, *globalRagRoot)
	case "serve":
		return api.RunServe(rest[1:])
	case "key", "admin", "sa", "env", "units", "gateway", "job", "backup", "selftest":
		fmt.Fprintf(stderr, "ragstack-ctl %s: not implemented in this PR (PR-A ships the read surface)\n", rest[0])
		return exitUsage
	case "help", "-h", "--help":
		usage()
		return exitOK
	default:
		fmt.Fprintf(stderr, "ragstack-ctl: unknown command %q\n", rest[0])
		usage()
		return exitUsage
	}
}

func cmdVersion(_ bool) int {
	// Always JSON: the plan defines `version` as {version, commit, built_at, go, schema_version}.
	enc := json.NewEncoder(stdout)
	if err := enc.Encode(version.Info()); err != nil {
		return fail(err)
	}
	return exitOK
}

func cmdPaths(args []string) int {
	if len(args) != 2 || args[0] != "validate" {
		fmt.Fprintln(stderr, "usage: ragstack-ctl paths validate <name>")
		return exitUsage
	}
	if err := paths.ValidateName(args[1]); err != nil {
		fmt.Fprintln(stderr, err)
		return exitError
	}
	fmt.Fprintf(stdout, "ok: %s\n", args[1])
	return exitOK
}

func cmdRender(args []string, registryPath string) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "usage: ragstack-ctl render tenant-env|up-sh|down-sh|units|nginx …")
		return exitUsage
	}
	kind, args := args[0], args[1:]
	fs := flag.NewFlagSet("render "+kind, flag.ContinueOnError)
	fs.SetOutput(stderr)
	index := fs.Int("index", 0, "port-block index (base 24000 + 20*index)")
	ragRoot := fs.String("rag-root", "/rag", "deployment root")
	store := fs.String("store", render.StoreSQLite, "sqlite|postgres")
	pgHost := fs.String("pg-host", "", "postgres host (store=postgres)")
	pgPort := fs.String("pg-port", "5432", "postgres port (store=postgres)")
	images := fs.String("images", "", "RAG_IMAGES (default <rag-root>/apptainer/images)")
	esHeap := fs.String("es-heap", "512m", "Elasticsearch heap")
	reg := fs.String("registry", registryPath, "registry.json path")
	nginxKind := fs.String("kind", "tenants", "tenants|static")
	// Off by default on purpose: see render.UnitConfig.AllowNonLoopbackBind.
	// The adopted tenants really do bind 0.0.0.0 today, so rendering their
	// units is a deliberate act an operator has to name.
	allowBind := fs.Bool("allow-non-loopback-bind", false, "render an api unit whose --host is not loopback (this host is internet-reachable)")

	// Accept the positional name before or after the flags.
	var name string
	if len(args) > 0 && args[0] != "" && args[0][0] != '-' {
		name, args = args[0], args[1:]
	}
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if name == "" && fs.NArg() > 0 {
		name = fs.Arg(0)
	}
	roots := paths.NewRoots(*ragRoot, paths.Overrides{})
	if *images == "" {
		*images = roots.ImagesDir
	}

	switch kind {
	case "tenant-env", "up-sh", "down-sh":
		if name == "" {
			fmt.Fprintf(stderr, "usage: ragstack-ctl render %s <name> [flags]\n", kind)
			return exitUsage
		}
		if err := paths.ValidateName(name); err != nil {
			return fail(err)
		}
		tp := paths.TenantPaths(roots, name, name)
		t := &registry.Tenant{Name: name, ManifestName: name, DataDir: tp.DataDir, Ports: paths.Block(*index)}
		var out []byte
		var err error
		switch kind {
		case "tenant-env":
			out, err = render.TenantEnv(t, render.EnvOptions{DryRun: true, StoreKind: *store, PGHost: *pgHost, PGPort: *pgPort})
		case "up-sh":
			out, err = render.UpSh(t, render.StoreOptions{Images: *images, ESHeap: *esHeap})
		case "down-sh":
			out, err = render.DownSh(t)
		}
		if err != nil {
			return fail(err)
		}
		_, _ = stdout.Write(out)
		return exitOK
	case "units":
		if name == "" {
			fmt.Fprintln(stderr, "usage: ragstack-ctl render units <name> --registry PATH")
			return exitUsage
		}
		f, err := registry.LoadNoRepair(*reg)
		if err != nil {
			return fail(err)
		}
		t, ok := f.Tenants[name]
		if !ok {
			return fail(fmt.Errorf("tenant %q not in %s", name, *reg))
		}
		units, err := render.Units(t, render.UnitConfig{RagRoot: f.RagRoot, AllowNonLoopbackBind: *allowBind})
		if err != nil {
			return fail(err)
		}
		names := make([]string, 0, len(units))
		for n := range units {
			names = append(names, n)
		}
		sort.Strings(names)
		for _, n := range names {
			fmt.Fprintf(stdout, "-- unit: %s --\n", n)
			_, _ = stdout.Write(units[n])
			fmt.Fprintln(stdout)
		}
		return exitOK
	case "nginx":
		f, err := registry.LoadNoRepair(*reg)
		if err != nil {
			return fail(err)
		}
		var out []byte
		switch *nginxKind {
		case "tenants":
			out, err = render.NginxTenants(f, render.NginxConfig{})
		case "static":
			out, err = render.NginxStatic(f, render.NginxConfig{})
		default:
			return fail(fmt.Errorf("unknown --kind %q (tenants|static)", *nginxKind))
		}
		if err != nil {
			return fail(err)
		}
		_, _ = stdout.Write(out)
		return exitOK
	default:
		fmt.Fprintf(stderr, "ragstack-ctl render: unknown renderer %q\n", kind)
		return exitUsage
	}
}

// loadForRead is the read path's registry load. It never writes, and it
// PRINTS the projection diagnostic on stderr instead of repairing it: a
// reader that rewrote manifest.tsv would truncate a live four-row manifest to
// match a one-tenant registry, and the next apptainer/new-tenant.sh would
// then hand out index 0 — a live tenant's port block — to a new tenant. The
// diagnostic goes to stderr so `--json` stdout stays machine-readable.
func loadForRead(path string) (*registry.Fleet, error) {
	f, diag, err := registry.LoadWithDiagnostics(path)
	if err != nil {
		return nil, err
	}
	if diag.Stale() {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", diag.ProjectionStale)
		fmt.Fprintln(stderr, "ragstack-ctl: run `ragstack-ctl registry repair` once you are sure the registry is the side that is right")
	}
	return f, nil
}

func fail(err error) int {
	if err == nil {
		return exitOK
	}
	var ne *strconv.NumError
	if errors.As(err, &ne) {
		fmt.Fprintf(stderr, "ragstack-ctl: bad number: %v\n", err)
		return exitUsage
	}
	fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
	return exitError
}

// --------------------------------------------------------------- PR-A reads
//
// adopt / adopt-all / doctor / fleet status / tenant list|show|logs. Every
// one of them reads; the single write in this PR is the registry file named
// by --registry, and only under `--commit`.

// resolveRegistry lets --rag-root move the registry with it: the flag
// default is computed from /rag, so a caller who overrides the root but not
// the registry path means the registry under THAT root.
func resolveRegistry(registryPath, ragRoot string) string {
	if registryPath == paths.NewRoots("/rag", paths.Overrides{}).Registry() && ragRoot != "/rag" {
		return paths.NewRoots(ragRoot, paths.Overrides{}).Registry()
	}
	return registryPath
}

// adoptSpec is one tenant's adoption arguments (also the --spec JSON shape).
type adoptSpec struct {
	Name         string `json:"name"`
	DataDir      string `json:"data_dir"`
	Worktree     string `json:"worktree"`
	ManifestName string `json:"manifest_name,omitempty"`
	UIPort       int    `json:"ui_port,omitempty"`
}

// liveSpecs is the coconut fleet as `adopt-all` adopts it: display order
// aside, the batch order is the port-block order the manifest records.
func liveSpecs(ragRoot string) []adoptSpec {
	r := paths.NewRoots(ragRoot, paths.Overrides{})
	d := func(name string) string { return filepath.Join(r.DataDir, name) }
	w := func(name string) string { return filepath.Join(r.ReposDir, name) }
	return []adoptSpec{
		{Name: "lucid-next", DataDir: d("lucid"), Worktree: w("lucid-next"), ManifestName: "lucid", UIPort: 5211},
		{Name: "asm-next", DataDir: d("asm"), Worktree: w("asm-next"), ManifestName: "asm", UIPort: 5212},
		{Name: "dev", DataDir: d("dev"), Worktree: w("dev"), UIPort: 8090},
		{Name: "demo", DataDir: d("demo"), Worktree: w("demo"), UIPort: 5210},
	}
}

// previewResult is what --preview prints: the row that would be written and
// the findings the read raised.
type previewResult struct {
	Tenant   *registry.Tenant `json:"tenant"`
	Findings []model.Finding  `json:"findings"`
}

func cmdAdopt(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("adopt", flag.ContinueOnError)
	fs.SetOutput(stderr)
	dataDir := fs.String("data-dir", "", "the tenant's data tree (required)")
	worktree := fs.String("worktree", "", "the checkout its API runs from (required)")
	manifestName := fs.String("manifest-name", "", "manifest.tsv row / data-dir basename (default: the name)")
	uiPort := fs.Int("ui-port", 0, "the Vite dev server's port")
	preview := fs.Bool("preview", false, "print the row that would be written")
	commit := fs.Bool("commit", false, "write the row to --registry")
	force := fs.Bool("force", false, "commit even when the preview raised error-level findings")
	repair := fs.Bool("repair-projection", false, "rewrite a stale manifest.tsv FROM the registry before committing")
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")

	var name string
	if len(args) > 0 && args[0] != "" && args[0][0] != '-' {
		name, args = args[0], args[1:]
	}
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if name == "" {
		fmt.Fprintln(stderr, "usage: ragstack-ctl adopt <name> --data-dir D --worktree W [--manifest-name M] [--ui-port P] --preview|--commit")
		return exitUsage
	}
	if *preview == *commit {
		fmt.Fprintln(stderr, "adopt: choose exactly one of --preview and --commit")
		return exitUsage
	}
	spec := adoptSpec{Name: name, DataDir: *dataDir, Worktree: *worktree, ManifestName: *manifestName, UIPort: *uiPort}
	return runAdopt([]adoptSpec{spec}, resolveRegistry(*reg, *root), *root, *commit, *force, *repair, jsonOut)
}

func cmdAdoptAll(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("adopt-all", flag.ContinueOnError)
	fs.SetOutput(stderr)
	preview := fs.Bool("preview", false, "print the rows that would be written")
	commit := fs.Bool("commit", false, "write the rows to --registry as one generation")
	force := fs.Bool("force", false, "commit even when a preview raised error-level findings")
	repair := fs.Bool("repair-projection", false, "rewrite a stale manifest.tsv FROM the registry before committing")
	specFile := fs.String("spec", "", "JSON array of {name,data_dir,worktree,manifest_name,ui_port} (default: the four live tenants)")
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if *preview == *commit {
		fmt.Fprintln(stderr, "adopt-all: choose exactly one of --preview and --commit")
		return exitUsage
	}
	specs := liveSpecs(*root)
	if *specFile != "" {
		b, err := os.ReadFile(*specFile)
		if err != nil {
			return fail(err)
		}
		specs = nil
		if err := json.Unmarshal(b, &specs); err != nil {
			return fail(fmt.Errorf("%s: %w", *specFile, err))
		}
	}
	return runAdopt(specs, resolveRegistry(*reg, *root), *root, *commit, *force, *repair, jsonOut)
}

// cmdRegistry is the explicit projection-repair verb. It is a verb and not a
// side effect of a read for the reason registry.ErrProjectionStale spells
// out: the repair rewrites manifest.tsv FROM the registry, so a reader that
// did it would truncate a live manifest and hand the next new-tenant.sh a
// port block that is already in use.
func cmdRegistry(args []string, registryPath, ragRoot string) int {
	if len(args) == 0 || args[0] != "repair" {
		fmt.Fprintln(stderr, "usage: ragstack-ctl registry repair [--registry PATH]")
		return exitUsage
	}
	fs := flag.NewFlagSet("registry repair", flag.ContinueOnError)
	fs.SetOutput(stderr)
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	if err := fs.Parse(args[1:]); err != nil {
		return exitUsage
	}
	path := resolveRegistry(*reg, *root)
	f, err := registry.Repair(path)
	if err != nil {
		return fail(err)
	}
	fmt.Fprintf(stdout, "repaired %s from %s at generation %d (the generation is unchanged)\n",
		registry.PathsFor(path).Manifest, path, f.Generation)
	return exitOK
}

// runAdopt previews every spec and, with commit, writes them as one registry
// generation. A preview that fails on any tenant does not write anything —
// and neither does one whose findings include an error, unless --force.
func runAdopt(specs []adoptSpec, registryPath, ragRoot string, commit, force, repairProjection, jsonOut bool) int {
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	results := make([]previewResult, 0, len(specs))
	rows := make([]*registry.Tenant, 0, len(specs))
	for _, s := range specs {
		t, findings, err := adopt.Preview(roots, s.Name, adopt.Options{
			DataDir: s.DataDir, Worktree: s.Worktree,
			ManifestName: s.ManifestName, UIPort: s.UIPort,
		})
		if err != nil {
			return fail(fmt.Errorf("adopt %s: %w", s.Name, err))
		}
		results = append(results, previewResult{Tenant: t, Findings: findings})
		rows = append(rows, t)
	}
	printResults := func() int {
		if !jsonOut {
			for _, r := range results {
				printPreview(r)
			}
			return exitOK
		}
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(results); err != nil {
			return fail(err)
		}
		return exitOK
	}
	if code := printResults(); code != exitOK {
		return code
	}

	if !commit {
		return exitOK
	}
	// An error-level finding is the preview saying this row does not
	// describe something the ctl can safely operate — a store URL it refuses
	// to dial, an API port it cannot attribute. Writing it anyway put a row
	// in the registry that every later op would then refuse to act on, and
	// did it silently: the findings scrolled past above the "committed" line.
	if blockers := errorFindings(results); len(blockers) > 0 && !force {
		fmt.Fprintf(stderr, "\nragstack-ctl: refusing to commit — %d error-level finding(s):\n", len(blockers))
		for _, f := range blockers {
			fmt.Fprintf(stderr, "  error %-32s %-11s %s\n", f.Code, string(f.Tenant), f.Detail)
		}
		fmt.Fprintln(stderr, "fix them, or pass --force to record the row as it stands")
		return exitRefused
	}
	if err := adopt.CommitAll(registryPath, rows, adopt.CommitOptions{
		Roots: roots, UpdatedBy: fmt.Sprintf("local:%d", os.Getuid()),
		RepairProjection: repairProjection,
	}); err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitRefused
	}
	fmt.Fprintf(stdout, "\ncommitted %d tenant(s) to %s (+ manifest.tsv beside it)\n", len(rows), registryPath)
	return exitOK
}

// errorFindings collects every error-level finding across a batch preview.
func errorFindings(results []previewResult) []model.Finding {
	var out []model.Finding
	for _, r := range results {
		for _, f := range r.Findings {
			if f.Level == model.LevelError {
				out = append(out, f)
			}
		}
	}
	return out
}

func printPreview(r previewResult) {
	t := r.Tenant
	fmt.Fprintf(stdout, "== %s (manifest %s, index %d, base %d)\n", t.Name, t.ManifestName, t.Ports.Index, t.Ports.Base)
	fmt.Fprintf(stdout, "   data_dir   %s\n", t.DataDir)
	fmt.Fprintf(stdout, "   worktree   %s  code %s\n", t.Worktree, t.Code.Tag)
	fmt.Fprintf(stdout, "   state      %s / owner %s / supervisor %s / env_layout %s\n", t.State, t.Owner, t.Supervisor, t.EnvLayout)
	fmt.Fprintf(stdout, "   qdrant     %-10s %s\n", t.Stores.Qdrant.Ownership, t.Stores.Qdrant.URL)
	fmt.Fprintf(stdout, "   es         %-10s %s heap %s (provision %s)\n",
		t.Stores.Elasticsearch.Ownership, t.Stores.Elasticsearch.URL,
		orNone(string(t.Stores.Elasticsearch.Heap)), orNone(string(t.Stores.Elasticsearch.ProvisionHeap)))
	if t.Stores.Neo4j.URL != "" {
		fmt.Fprintf(stdout, "   neo4j      external   %s\n", t.Stores.Neo4j.URL)
	}
	fmt.Fprintf(stdout, "   settings %d · secret_refs %d · keys %d · admins %d · external_refs %d · unmanaged %d · drift %d\n",
		len(t.Settings), len(t.SecretRefs), len(t.Keys), t.Identity.AdminSubjectsCount,
		len(t.ExternalRefs), len(t.UnmanagedFiles), len(t.Drift))
	counts := map[string]int{}
	for _, f := range r.Findings {
		counts[string(f.Level)+" "+f.Code]++
	}
	for _, k := range sortedKeys(counts) {
		fmt.Fprintf(stdout, "   finding    %-6s %s\n", fmt.Sprintf("×%d", counts[k]), k)
	}
	fmt.Fprintln(stdout)
}

func cmdDoctor(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	fs.SetOutput(stderr)
	op := fs.String("op", "", "scope the run to one operation's preconditions")
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")

	var tenant string
	if len(args) > 0 && args[0] != "" && args[0][0] != '-' {
		tenant, args = args[0], args[1:]
	}
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	// An op nobody wrote a precondition row for disables the gate entirely
	// (RedCodes returns nil for an unknown key), so `--op strat` bought a
	// green run with no preconditions at all instead of a typo report.
	if *op != "" && !doctor.KnownOp(*op) {
		fmt.Fprintf(stderr, "ragstack-ctl doctor: unknown --op %q\nknown ops: %s\n", *op, strings.Join(doctor.Ops(), " "))
		return exitUsage
	}
	roots := paths.NewRoots(*root, paths.Overrides{})
	registryPath = resolveRegistry(*reg, *root)
	f, err := loadForRead(registryPath)
	if err != nil {
		return fail(err)
	}
	if tenant != "" {
		if _, ok := f.Tenants[tenant]; !ok {
			return fail(fmt.Errorf("tenant %q is not in %s", tenant, registryPath))
		}
	}
	resp := doctor.Run(context.Background(), roots, f, doctor.Options{
		Tenant: tenant, Op: *op, RegistryPath: registryPath, CtlUID: ctlUID(doctor.DefaultCtlUser),
	})
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(resp); err != nil {
			return fail(err)
		}
	} else {
		for _, fnd := range resp.Findings {
			scope := string(fnd.Tenant)
			if scope == "" {
				scope = "host"
			}
			fmt.Fprintf(stdout, "%-5s %-32s %-11s %s\n", fnd.Level, fnd.Code, scope, fnd.Detail)
		}
		fmt.Fprintf(stdout, "\nstatus %s · %d finding(s) · %s\n", resp.Status, len(resp.Findings), resp.Hash)
	}
	if resp.Status == model.StatusRed {
		return exitRefused
	}
	return exitOK
}

func cmdFleet(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 || args[0] != "status" {
		fmt.Fprintln(stderr, "usage: ragstack-ctl fleet status [--json]")
		return exitUsage
	}
	fs := flag.NewFlagSet("fleet status", flag.ContinueOnError)
	fs.SetOutput(stderr)
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args[1:]); err != nil {
		return exitUsage
	}
	roots := paths.NewRoots(*root, paths.Overrides{})
	f, err := loadForRead(resolveRegistry(*reg, *root))
	if err != nil {
		return fail(err)
	}
	resp := fleet.Build(context.Background(), roots, f, fleet.Probes{})
	if *asJSON {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(resp); err != nil {
			return fail(err)
		}
		return exitOK
	}
	h := resp.Host
	fmt.Fprintf(stdout, "host: %.1f GiB free · linger %v · ctl unit active %v · vm.max_map_count %d · %s [%s]\n\n",
		float64(h.DiskFreeBytes)/(1<<30), h.Linger, h.CtlUnitActive, h.VMMaxMapCount,
		doctor.DefaultSudoersGroup, strings.Join(h.SudoersGroup, " "))
	fmt.Fprintf(stdout, "%-12s %-8s %-9s %-10s %-9s %-22s %-6s %s\n",
		"TENANT", "STATE", "OWNER", "STORES", "API", "HEALTH api/qdrant/es", "DRIFT", "DISK")
	for _, r := range resp.Tenants {
		fmt.Fprintf(stdout, "%-12s %-8s %-9s %-10s %-9d %-22s %-6d %.1f GiB\n",
			r.Name, r.State, r.Owner, r.StoresMode, r.Ports.API,
			fmt.Sprintf("%s/%s/%s", r.Health.API, r.Health.Qdrant, r.Health.ES),
			r.DriftCount, float64(r.DiskBytes)/(1<<30))
	}
	return exitOK
}

func cmdTenant(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "usage: ragstack-ctl tenant list|show <name>|logs <name> --file api|qdrant|es|ui [--lines N]")
		return exitUsage
	}
	verb, args := args[0], args[1:]
	var name string
	if len(args) > 0 && args[0] != "" && args[0][0] != '-' {
		name, args = args[0], args[1:]
	}
	fs := flag.NewFlagSet("tenant "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	file := fs.String("file", "api", "api|qdrant|es|ui")
	lines := fs.Int("lines", 200, "how many lines to tail (max 2000)")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	roots := paths.NewRoots(*root, paths.Overrides{})
	f, err := loadForRead(resolveRegistry(*reg, *root))
	if err != nil {
		return fail(err)
	}
	lookup := func() (*registry.Tenant, int) {
		if name == "" {
			fmt.Fprintf(stderr, "usage: ragstack-ctl tenant %s <name>\n", verb)
			return nil, exitUsage
		}
		t, ok := f.Tenants[name]
		if !ok {
			fmt.Fprintf(stderr, "ragstack-ctl: tenant %q is not in the registry\n", name)
			return nil, exitError
		}
		return t, exitOK
	}

	switch verb {
	case "list":
		view := fleet.TenantsView(context.Background(), roots, f, fleet.Probes{}, true)
		if *asJSON {
			return encode(view)
		}
		fmt.Fprintf(stdout, "%-12s %-8s %-10s %-9s %-7s %s\n", "TENANT", "STATE", "STORES", "API", "UI", "CODE")
		for _, row := range view.Tenants {
			s := row.Summary
			fmt.Fprintf(stdout, "%-12s %-8s %-10s %-9d %-7v %s\n", s.Name, s.State, s.StoresMode, s.Ports.API, row.Status.Listening.UI, s.CodeTag)
		}
		return exitOK
	case "show":
		t, code := lookup()
		if t == nil {
			return code
		}
		view := fleet.TenantView(context.Background(), roots, t, fleet.Probes{}, true)
		if *asJSON {
			return encode(view)
		}
		s := view.Summary
		fmt.Fprintf(stdout, "%s (manifest %s) %s · owner %s · supervisor %s · code %s\n", s.Name, s.ManifestName, s.State, s.Owner, s.Supervisor, s.CodeTag)
		fmt.Fprintf(stdout, "ports api %d · qdrant %d · es %d   stores %s\n", s.Ports.API, s.Ports.QdrantHTTP, s.Ports.ESHTTP, s.StoresMode)
		fmt.Fprintf(stdout, "health api %s · qdrant %s · es %s · deep %s\n", s.Health.API, s.Health.Qdrant, s.Health.ES, s.Health.Deep)
		fmt.Fprintf(stdout, "api pid %v owner %v · listening api=%v qdrant=%v es=%v ui=%v\n",
			view.Status.APIPid, view.Status.APIPidOwner, view.Status.Listening.API,
			view.Status.Listening.QdrantHTTP, view.Status.Listening.ESHTTP, view.Status.Listening.UI)
		for _, svc := range view.Units.Services {
			fmt.Fprintf(stdout, "  %-7s %-28s %s\n", svc.Kind, svc.Name, svc.ActiveState)
		}
		for _, d := range view.Drift {
			fmt.Fprintf(stdout, "  drift %-10s %s: %s != %s\n", d.Code, d.Field, d.Expected, d.Actual)
		}
		return exitOK
	case "logs":
		t, code := lookup()
		if t == nil {
			return code
		}
		resp, err := logs.Read(roots, t, model.LogFile(*file), *lines)
		if err != nil {
			return fail(err)
		}
		if *asJSON {
			return encode(resp)
		}
		for _, l := range resp.Lines {
			fmt.Fprintln(stdout, l)
		}
		if resp.Truncated {
			fmt.Fprintf(stderr, "(truncated: %d of the last %d lines, redacted)\n", resp.Returned, resp.Requested)
		}
		return exitOK
	default:
		fmt.Fprintf(stderr, "ragstack-ctl tenant: unknown verb %q\n", verb)
		return exitUsage
	}
}

func encode(v any) int {
	enc := json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return fail(err)
	}
	return exitOK
}

// ctlUID resolves the service account's uid for the drop-in and runtime-dir
// checks; 0 (skip those checks) when the account does not exist here. The
// lookup goes through hostfacts, which falls back to getent — the static
// binary cannot see an LDAP account through Go's own /etc/passwd reader.
func ctlUID(username string) int {
	if uid := hostfacts.LookupUID(username); uid > 0 {
		return uid
	}
	return 0
}

func orNone(v string) string {
	if v == "" {
		return "-"
	}
	return v
}

func sortedKeys(m map[string]int) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// --------------------------------------------------------------- wait-ready
//
// `ExecStartPre=%[12]s wait-ready %[1]s --timeout %[13]d` is baked into every
// rendered api unit (render/units.go), so this verb is not optional: without
// it every api unit on the host fails its start with "exit 2 usage" and a
// tenant whose stores are still opening its segments starts anyway and 500s.

// waitPoll is how long to wait between attempts. The stores it waits for take
// tens of seconds to open their data; polling faster only adds load.
const waitPoll = 2 * time.Second

// cmdWaitReady blocks until every store this tenant OWNS answers a readiness
// probe, or the timeout expires.
//
// Only exclusive stores are waited for: a shared qdrant or elasticsearch
// belongs to the host, not to this tenant, and a tenant unit must never sit
// in ExecStartPre because somebody else's store is down.
//
// exit 0 ready · 1 not ready (timeout, or a URL the ctl refuses to dial) ·
// 2 usage.
func cmdWaitReady(args []string, registryPath, ragRoot string) int {
	fs := flag.NewFlagSet("wait-ready", flag.ContinueOnError)
	fs.SetOutput(stderr)
	timeout := fs.Int("timeout", 180, "seconds to wait for the tenant's own stores")
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")

	var name string
	if len(args) > 0 && args[0] != "" && args[0][0] != '-' {
		name, args = args[0], args[1:]
	}
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if name == "" {
		fmt.Fprintln(stderr, "usage: ragstack-ctl wait-ready <name> [--timeout S]")
		return exitUsage
	}
	if *timeout <= 0 {
		fmt.Fprintln(stderr, "wait-ready: --timeout must be positive")
		return exitUsage
	}
	f, err := registry.LoadNoRepair(resolveRegistry(*reg, *root))
	if err != nil {
		return fail(err)
	}
	t, ok := f.Tenants[name]
	if !ok {
		fmt.Fprintf(stderr, "ragstack-ctl: tenant %q is not in the registry\n", name)
		return exitError
	}

	type target struct{ kind, url string }
	var targets []target
	if q := t.Stores.Qdrant; q.Ownership == registry.OwnershipExclusive && q.URL != "" {
		targets = append(targets, target{"qdrant", strings.TrimSuffix(q.URL, "/") + "/collections"})
	}
	if e := t.Stores.Elasticsearch; e.Ownership == registry.OwnershipExclusive && e.URL != "" {
		// wait_for_status does the yellow|green test server-side: a 200 IS
		// "yellow or better", anything else (408 included) is "not yet".
		targets = append(targets, target{"elasticsearch",
			strings.TrimSuffix(e.URL, "/") + "/_cluster/health?wait_for_status=yellow&timeout=1s"})
	}
	if len(targets) == 0 {
		fmt.Fprintf(stdout, "%s: no exclusively-owned store to wait for\n", name)
		return exitOK
	}
	// The same gate every other probe goes through: a URL hostfacts refuses
	// is never dialled, here or anywhere else.
	for _, tg := range targets {
		if ok, reason := hostfacts.AllowedStoreURL(tg.url, t.Ports, hostfacts.DefaultExternalStorePorts); !ok {
			fmt.Fprintf(stderr, "ragstack-ctl wait-ready: %s url is not one the ctl may probe: %s\n", tg.kind, reason)
			return exitError
		}
	}

	deadline := time.Now().Add(time.Duration(*timeout) * time.Second)
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	prober := hostfacts.NewProber()
	pending := targets
	for {
		var next []target
		for _, tg := range pending {
			code, err := prober.Probe(ctx, tg.url)
			if err == nil && code >= 200 && code < 300 {
				fmt.Fprintf(stdout, "%s: %s ready\n", name, tg.kind)
				continue
			}
			next = append(next, tg)
		}
		pending = next
		if len(pending) == 0 {
			return exitOK
		}
		if time.Now().After(deadline) || time.Until(deadline) <= 0 {
			for _, tg := range pending {
				fmt.Fprintf(stderr, "ragstack-ctl wait-ready: %s: %s did not become ready within %ds\n", name, tg.kind, *timeout)
			}
			return exitError
		}
		select {
		case <-ctx.Done():
			for _, tg := range pending {
				fmt.Fprintf(stderr, "ragstack-ctl wait-ready: %s: %s did not become ready within %ds\n", name, tg.kind, *timeout)
			}
			return exitError
		case <-time.After(waitPoll):
		}
	}
}
