package main

// The operation groups: `tenant start|stop|…`, `key`, `admin`, `sa`, `env`
// and `units`.
//
// Every command in this file does exactly one thing of its own — turn its
// positionals and flags into the `args` object x-ctl-op-args[verb] describes
// — and then hands the envelope to submitOp. The mapping is the ONLY
// per-verb knowledge in the CLI, so it is written as one small builder per
// subverb with nothing else in it: a builder that also validated, defaulted
// or normalized would be a second reader of the schema whose disagreement
// with the engine shows up as a 422 nobody can explain from the command line.
//
// Absent is not false. A boolean the operator did not write is left OUT of
// `args` rather than sent as `false`, because the schema gives these
// properties `default: false` and a value nobody chose still lands in the
// plan hash and the audit row.

import (
	"flag"
	"fmt"
)

// tenantOpVerbs are the `tenant <verb>` subcommands that submit an operation.
// `list`, `show` and `logs` are reads and stay in cmdTenant.
var tenantOpVerbs = map[string]bool{
	"start": true, "stop": true, "restart": true,
	"backup": true, "restore": true, "decommission": true,
}

func tenantOpUsage(verb string) int {
	switch verb {
	case "start", "restart":
		return usageErr("usage: ragstack-ctl tenant %s <name> [--only api,ui,qdrant,es] [--force] %s", verb, opFlagSummary)
	case "stop":
		return usageErr("usage: ragstack-ctl tenant stop <name> [--only api,ui,qdrant,es] [--keep-enabled] [--force] %s", opFlagSummary)
	case "backup":
		return usageErr("usage: ragstack-ctl tenant backup <name> [--fence] [--tar] %s", opFlagSummary)
	case "restore":
		return usageErr("usage: ragstack-ctl tenant restore <name> --from <bundle-id> --as <fresh-tenant> %s", opFlagSummary)
	default:
		return usageErr("usage: ragstack-ctl tenant %s <name> %s", verb, opFlagSummary)
	}
}

const opFlagSummary = "[--dry-run] [--yes|--yes-destructive NAME] [--wait] [--server URL] [--api-key-file F]"

// cmdTenantOp is `tenant start|stop|restart|backup|restore|decommission`.
func cmdTenantOp(verb string, args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)

	var (
		only                multiFlag
		force, keepEnabled  *bool
		fence, tar          *bool
		from, as            *string
		wantsOnly, wantsFrm bool
	)
	switch verb {
	case "start", "restart", "stop":
		wantsOnly = true
		fs.Var(&only, "only", "restrict to these services: api, ui, qdrant, es (repeatable or a comma list)")
		force = fs.Bool("force", false, "proceed over the refusals the plan would otherwise raise")
		if verb == "stop" {
			keepEnabled = fs.Bool("keep-enabled", false, "leave desired_boot alone (stop sets it to disabled by default)")
		}
	case "backup":
		fence = fs.Bool("fence", false, "fenced backup: gateway read-only, API stopped, no running jobs — only fenced bundles verify")
		tar = fs.Bool("tar", false, "tar the bundle")
	case "restore":
		wantsFrm = true
		from = fs.String("from", "", "bundle id (<ts>-<kind>) under the source tenant's backup dir — an id, never a path")
		as = fs.String("as", "", "the FRESH tenant to restore into (v1 restores into an isolated new tenant only)")
	}

	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return tenantOpUsage(verb)
	}
	name := pos[0]
	set := setFlags(fs)

	opArgs := map[string]any{}
	if wantsOnly && len(only) > 0 {
		opArgs["only"] = []string(only)
	}
	if force != nil && set["force"] {
		opArgs["force"] = *force
	}
	if keepEnabled != nil && set["keep-enabled"] {
		opArgs["keep_enabled"] = *keepEnabled
	}
	if fence != nil && set["fence"] {
		opArgs["fence"] = *fence
	}
	if tar != nil && set["tar"] {
		opArgs["tar"] = *tar
	}
	if wantsFrm {
		if *from == "" || *as == "" {
			return tenantOpUsage(verb)
		}
		opArgs["from"] = *from
		opArgs["as"] = *as
	}
	return submitOp(o, tenantOpTarget(name, verb), opArgs)
}

func tenantOpTarget(name, verb string) opTarget {
	return opTarget{path: "/v1/tenants/" + name + "/ops/" + verb, op: verb, tenant: name}
}

// ---------------------------------------------------------------- key

func keyUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl key <verb> <tenant> … %s

  mint   <tenant> <label> --role admin|user [--restart]
  revoke <tenant> <id> [--restart]

The minted value is NEVER printed by the mint itself: collect it once with
`+"`ragstack-ctl job show <id>`"+` and the daemon's secrets envelope.
`, opFlagSummary)
	return exitUsage
}

func cmdKey(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return keyUsage()
	}
	verb, args := args[0], args[1:]
	fs := flag.NewFlagSet("key "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	role := fs.String("role", "", "admin|user (required for mint)")
	restart := fs.Bool("restart", false, "restart the tenant API so the new key set is live")

	want := 2 // <tenant> <label|id>
	pos, rest := takePositionals(args, want)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	set := setFlags(fs)

	switch verb {
	case "mint":
		if len(pos) != 2 || *role == "" {
			return keyUsage()
		}
		opArgs := map[string]any{"label": pos[1], "role": *role}
		if set["restart"] {
			opArgs["restart"] = *restart
		}
		return submitOp(o, tenantOpTarget(pos[0], "key-mint"), opArgs)
	case "revoke":
		if len(pos) != 2 {
			return keyUsage()
		}
		opArgs := map[string]any{"id": pos[1]}
		if set["restart"] {
			opArgs["restart"] = *restart
		}
		return submitOp(o, tenantOpTarget(pos[0], "key-revoke"), opArgs)
	case "help", "-h", "--help":
		keyUsage()
		return exitOK
	default:
		return usageErr("key: unknown verb %q (mint|revoke)", verb)
	}
}

// ---------------------------------------------------------------- admin

func adminUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl admin add|remove <tenant> <subject> %s

  <subject> is an identity subject such as bvbrc:alice@patricbrc.org.
`, opFlagSummary)
	return exitUsage
}

func cmdAdmin(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return adminUsage()
	}
	verb, args := args[0], args[1:]
	var op string
	switch verb {
	case "add":
		op = "admin-add"
	case "remove":
		op = "admin-remove"
	case "help", "-h", "--help":
		adminUsage()
		return exitOK
	default:
		return usageErr("admin: unknown verb %q (add|remove)", verb)
	}
	fs := flag.NewFlagSet("admin "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	pos, rest := takePositionals(args, 2)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 2 {
		return adminUsage()
	}
	return submitOp(o, tenantOpTarget(pos[0], op), map[string]any{"subject": pos[1]})
}

// ---------------------------------------------------------------- sa

func saUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl sa <verb> <tenant> <subject> … %s

  create  <tenant> <subject> --role admin|user [--purpose TEXT]
  disable <tenant> <subject>
  enable  <tenant> <subject>
`, opFlagSummary)
	return exitUsage
}

func cmdSA(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return saUsage()
	}
	verb, args := args[0], args[1:]
	var op string
	switch verb {
	case "create":
		op = "sa-create"
	case "disable":
		op = "sa-disable"
	case "enable":
		op = "sa-enable"
	case "help", "-h", "--help":
		saUsage()
		return exitOK
	default:
		return usageErr("sa: unknown verb %q (create|disable|enable)", verb)
	}
	fs := flag.NewFlagSet("sa "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	role := fs.String("role", "", "admin|user (required for create)")
	purpose := fs.String("purpose", "", "why this account exists (recorded on the tenant)")
	pos, rest := takePositionals(args, 2)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 2 {
		return saUsage()
	}
	opArgs := map[string]any{"subject": pos[1]}
	if op == "sa-create" {
		if *role == "" {
			return saUsage()
		}
		opArgs["role"] = *role
		if *purpose != "" {
			opArgs["purpose"] = *purpose
		}
	}
	return submitOp(o, tenantOpTarget(pos[0], op), opArgs)
}

// ---------------------------------------------------------------- env

func envUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl env <verb> <tenant> … %s

  set       <tenant> KEY VALUE   a PUBLIC-class key only; secret and
                                 executable-surface keys are refused (409)
  unset     <tenant> KEY
  normalize <tenant>             rewrite the env files into canonical form
`, opFlagSummary)
	return exitUsage
}

func cmdEnv(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return envUsage()
	}
	verb, args := args[0], args[1:]
	var (
		op   string
		want int
	)
	switch verb {
	case "set":
		op, want = "env-set", 3
	case "unset":
		op, want = "env-unset", 2
	case "normalize":
		op, want = "env-normalize", 1
	case "help", "-h", "--help":
		envUsage()
		return exitOK
	default:
		return usageErr("env: unknown verb %q (set|unset|normalize)", verb)
	}
	fs := flag.NewFlagSet("env "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	pos, rest := takePositionals(args, want)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != want {
		return envUsage()
	}
	opArgs := map[string]any{}
	switch op {
	case "env-set":
		opArgs["key"], opArgs["value"] = pos[1], pos[2]
	case "env-unset":
		opArgs["key"] = pos[1]
	}
	return submitOp(o, tenantOpTarget(pos[0], op), opArgs)
}

// ---------------------------------------------------------------- units

func unitsUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl units render|diff|apply <tenant> %s

  render | diff   plan only: the rendered units and their diff (args {"apply": false})
  apply           write the units and daemon-reload   (args {"apply": true})
`, opFlagSummary)
	return exitUsage
}

// cmdUnits is the one group whose `args` are decided by the SUBVERB rather
// than by a flag: render-units takes a single `apply` boolean, and naming the
// two halves of it `render`/`diff` and `apply` is what stops an operator from
// writing the apply and believing they asked for the diff.
func cmdUnits(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return unitsUsage()
	}
	verb, args := args[0], args[1:]
	var apply bool
	switch verb {
	case "render", "diff":
		apply = false
	case "apply":
		apply = true
	case "help", "-h", "--help":
		unitsUsage()
		return exitOK
	default:
		return usageErr("units: unknown verb %q (render|diff|apply)", verb)
	}
	fs := flag.NewFlagSet("units "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return unitsUsage()
	}
	return submitOp(o, tenantOpTarget(pos[0], "render-units"), map[string]any{"apply": apply})
}
