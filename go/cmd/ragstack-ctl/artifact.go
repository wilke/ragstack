package main

// `fleet artifact prepare|list` and `tenant create` — the two commands that
// decide what code exists on this host and which tenant runs it.
//
// Both are thin, like every other command in this binary: turn the flags into
// the `args` object the contract describes, hand the envelope to submitOp, and
// print what comes back. The one thing they add is that `tenant create` mints
// credentials, so it collects the job's one-time envelope and prints it —
// which is why opFlags carries wantSecrets.

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// envMirror names the bare repository artifacts are prepared from. It is
// deployment configuration, so it comes from the environment (or the default
// under the deployment root), never from a request.
const envMirror = "CTL_MIRROR"

// mirrorPath is the mirror this invocation would use.
func mirrorPath(ragRoot string) string {
	if v := strings.TrimSpace(os.Getenv(envMirror)); v != "" {
		return v
	}
	return filepath.Join(ragRoot, "repos", "ragstack.git")
}

// ---------------------------------------------------------------- fleet artifact

func artifactUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl fleet artifact prepare|list …

  prepare --tag REF [--mirror DIR] [--python-env DIR] [--schema-compatible]
                  Resolve REF in the mirror, check a detached worktree out
                  under <state>/artifacts/<tag>-<sha[:12]>/worktree, run
                  npm ci in it, and record the artifact in the registry.

                  CLI-only: it is the one operation that reaches the network
                  and the one that takes a repository path, so it runs in THIS
                  process (--direct is implied) and the daemon exposes no route
                  for it.

  list [--json]   The prepared artifacts, newest first.

%s
`, opFlagSummary)
	return exitUsage
}

func cmdFleetArtifact(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return artifactUsage()
	}
	switch args[0] {
	case "prepare":
		return cmdArtifactPrepare(args[1:], registryPath, ragRoot, jsonOut)
	case "list":
		return cmdArtifactList(args[1:], registryPath, ragRoot, jsonOut)
	case "help", "-h", "--help":
		artifactUsage()
		return exitOK
	default:
		return usageErr("fleet artifact: unknown verb %q (prepare|list)", args[0])
	}
}

func cmdArtifactPrepare(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("fleet artifact prepare", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	tag := fs.String("tag", "", "a ref or 40-hex sha in the mirror (required)")
	mirror := fs.String("mirror", "", "the bare mirror to resolve and check out from (default $"+envMirror+" or <rag-root>/repos/ragstack.git)")
	pythonEnv := fs.String("python-env", "", "the Python env tenants created from this artifact run under")
	compatible := fs.Bool("schema-compatible", false,
		"assert from the release notes that update-code to this artifact needs no store/DB migration")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if *tag == "" {
		return artifactUsage()
	}
	// --server is REFUSED rather than ignored. The daemon has no route for this
	// op, so a request would come back 422 "not a verb", which reads as a bug in
	// the CLI. Saying why here is the difference between a confusing error and
	// an explanation of a deliberate design.
	if fs.Lookup("server") != nil && setFlags(fs)["server"] {
		fmt.Fprintf(stderr, "ragstack-ctl: `fleet artifact prepare` has no daemon route and --server cannot reach it: "+
			"it runs `npm ci` (the one step that touches the network) and takes a repository path, both of which are "+
			"trusted-operator, CLI-only inputs. Run it on the host without --server.\n")
		return exitUsage
	}
	// --direct is implied: there is nowhere else to send it.
	*o.direct = true

	set := setFlags(fs)
	opArgs := map[string]any{"tag": *tag}
	if *mirror != "" {
		opArgs["mirror"] = *mirror
	} else {
		opArgs["mirror"] = mirrorPath(*o.ragRoot)
	}
	if *pythonEnv != "" {
		opArgs["python_env"] = *pythonEnv
	}
	if set["schema-compatible"] {
		opArgs["schema_compatible"] = *compatible
	}
	return submitOp(o, opTarget{path: "/v1/fleet/artifacts", op: "artifact-prepare"}, opArgs)
}

func cmdArtifactList(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("fleet artifact list", flag.ContinueOnError)
	fs.SetOutput(stderr)
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	f, err := loadForRead(resolveRegistry(*reg, *root))
	if err != nil {
		return fail(err)
	}
	ids := make([]string, 0, len(f.Artifacts))
	for id := range f.Artifacts {
		ids = append(ids, id)
	}
	// Newest first, with the id as the tie-break so the order is total: two
	// artifacts prepared in the same second must not swap places between runs.
	sort.Slice(ids, func(i, j int) bool {
		a, b := f.Artifacts[ids[i]], f.Artifacts[ids[j]]
		if a.PreparedAt != b.PreparedAt {
			return a.PreparedAt > b.PreparedAt
		}
		return ids[i] < ids[j]
	})
	if *asJSON {
		out := make([]map[string]any, 0, len(ids))
		for _, id := range ids {
			a := f.Artifacts[id]
			out = append(out, map[string]any{
				"id": id, "sha": a.SHA, "tag": a.Tag, "worktree": a.Worktree, "python_env": a.PythonEnv,
				"prepared_at": a.PreparedAt, "prepared_by": a.PreparedBy, "schema_compatible": a.SchemaCompatible,
				"tenants": artifactTenants(f, id),
			})
		}
		return encode(out)
	}
	if len(ids) == 0 {
		fmt.Fprintln(stdout, "no artifacts are prepared — `ragstack-ctl fleet artifact prepare --tag <ref>`")
		return exitOK
	}
	fmt.Fprintf(stdout, "%-40s %-12s %-20s %-6s %s\n", "ARTIFACT", "SHA", "PREPARED", "SCHEMA", "TENANTS")
	for _, id := range ids {
		a := f.Artifacts[id]
		sha := a.SHA
		if len(sha) > 12 {
			sha = sha[:12]
		}
		compat := "—"
		if a.SchemaCompatible {
			compat = "ok"
		}
		fmt.Fprintf(stdout, "%-40s %-12s %-20s %-6s %s\n", id, sha, a.PreparedAt, compat,
			orNone(strings.Join(artifactTenants(f, id), ",")))
	}
	return exitOK
}

// artifactTenants are the tenants pinned to this artifact — the reason an
// artifact must never be deleted while one names it.
func artifactTenants(f *registry.Fleet, id string) []string {
	var out []string
	for name, t := range f.Tenants {
		if string(t.ArtifactID) == id {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

// ---------------------------------------------------------------- tenant create

func createUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant create <name> --artifact ID [options] %s

  --artifact ID          a PREPARED artifact (fleet artifact prepare) — never a
                         git ref and never a path
  --postgres local       give the tenant its own postgres instance on the
                         block's +5 port (default: sqlite files under state/)
  --es-heap 1g           JVM heap for the dedicated Elasticsearch
  --identity bvbrc|none  bearer identity provider (default none)
  --admin-subject S      an admin subject, issuer:sub, repeatable
  --key label:role       an extra API key to mint, repeatable. An admin key
                         labelled bootstrap-admin is ALWAYS minted.
  --sa subject:role:purpose   a service account to register, repeatable
  --template-from T      copy T's PUBLIC settings as a starting point
  --set KEY=VALUE        a public setting, repeatable
  --ui-mode static|dev|external
  --no-start             provision and enable, but do not start
  --no-gateway           do not publish a gateway generation

The minted credentials are printed ONCE, when the job succeeds. That needs the
job to be followed, so --wait is implied (as it already is under --direct).
`, opFlagSummary)
	return exitUsage
}

func cmdTenantCreate(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant create", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	var (
		artifact   = fs.String("artifact", "", "a prepared artifact id (required)")
		postgres   = fs.String("postgres", "", "sqlite|local — where the ACL/job/collection state lives")
		esHeap     = fs.String("es-heap", "", "JVM heap for the dedicated Elasticsearch, e.g. 1g")
		identity   = fs.String("identity", "", "bvbrc|none")
		template   = fs.String("template-from", "", "copy this tenant's PUBLIC settings")
		uiMode     = fs.String("ui-mode", "", "static|dev|external")
		noStart    = fs.Bool("no-start", false, "provision and enable, but do not start")
		noGateway  = fs.Bool("no-gateway", false, "do not publish a gateway generation")
		subjects   multiFlag
		keyFlags   multiFlag
		saFlags    multiFlag
		setFlagsIn multiFlag
	)
	fs.Var(&subjects, "admin-subject", "an admin subject (issuer:sub), repeatable or a comma list")
	fs.Var(&keyFlags, "key", "label:role of a key to mint, repeatable")
	fs.Var(&saFlags, "sa", "subject:role:purpose of a service account, repeatable")
	fs.Var(&setFlagsIn, "set", "KEY=VALUE public setting, repeatable")

	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 || *artifact == "" {
		return createUsage()
	}
	name := pos[0]
	set := setFlags(fs)

	opArgs := map[string]any{"name": name, "artifact_id": *artifact}
	if *postgres != "" {
		opArgs["postgres"] = *postgres
	}
	if *esHeap != "" {
		opArgs["es_heap"] = *esHeap
	}
	if *identity != "" {
		opArgs["identity_provider"] = *identity
	}
	if *template != "" {
		opArgs["template_from"] = *template
	}
	if *uiMode != "" {
		opArgs["ui_mode"] = *uiMode
	}
	if len(subjects) > 0 {
		opArgs["admin_subjects"] = []string(subjects)
	}
	// Absent is not false: a boolean nobody chose still lands in the plan hash
	// and the audit row, so only a flag the operator actually wrote is sent.
	if set["no-start"] {
		opArgs["start"] = !*noStart
	}
	if set["no-gateway"] {
		opArgs["gateway"] = !*noGateway
	}
	if len(keyFlags) > 0 {
		keys := make([]any, 0, len(keyFlags))
		for _, spec := range keyFlags {
			label, role, ok := strings.Cut(spec, ":")
			if !ok {
				return usageErr("--key %q is not label:role", spec)
			}
			keys = append(keys, map[string]any{"label": label, "role": role})
		}
		opArgs["keys"] = keys
	}
	if len(saFlags) > 0 {
		accounts := make([]any, 0, len(saFlags))
		for _, spec := range saFlags {
			parts := strings.SplitN(spec, ":", 3)
			if len(parts) != 3 {
				return usageErr("--sa %q is not subject:role:purpose", spec)
			}
			accounts = append(accounts, map[string]any{"subject": parts[0], "role": parts[1], "purpose": parts[2]})
		}
		opArgs["service_accounts"] = accounts
	}
	if len(setFlagsIn) > 0 {
		settingsArg := map[string]any{}
		for _, kv := range setFlagsIn {
			k, v, ok := strings.Cut(kv, "=")
			if !ok {
				return usageErr("--set %q is not KEY=VALUE", kv)
			}
			settingsArg[k] = v
		}
		opArgs["settings"] = settingsArg
	}
	// A name the grammar refuses is a usage error here rather than a 422 from
	// across the network — the same courtesy --idempotency-key gets.
	if err := paths.ValidateName(name); err != nil {
		return usageErr("%v", err)
	}

	// The credentials exist for exactly one read, so the command has to be
	// there when the job ends. --wait is implied for an execute (a dry run
	// prints a plan and mints nothing).
	if !*o.dryRun {
		*o.wait = true
		o.wantSecrets = true
	}
	return submitOp(o, opTarget{path: "/v1/tenants", op: "create", tenant: name}, opArgs)
}
