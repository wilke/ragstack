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
	"sort"
	"strings"
)

// tenantOpVerbs are the `tenant <verb>` subcommands that submit an operation.
// `list`, `show` and `logs` are reads and stay in cmdTenant.
var tenantOpVerbs = map[string]bool{
	"start": true, "stop": true, "restart": true,
	"backup": true, "restore": true, "decommission": true, "purge": true,
	"update-code": true,
}

func tenantOpUsage(verb string) int {
	switch verb {
	case "start", "restart":
		return usageErr("usage: ragstack-ctl tenant %s <name> [--only api,ui,qdrant,es] [--force] %s", verb, opFlagSummary)
	case "stop":
		return usageErr("usage: ragstack-ctl tenant stop <name> [--only api,ui,qdrant,es] [--keep-enabled] [--force] %s", opFlagSummary)
	case "backup":
		fmt.Fprintf(stderr, `usage: ragstack-ctl tenant backup <name> [--fence] [--tar] [--scope config,state] [--secrets include|skip|require] %s

  --fence   stop the API for the duration so the stores hold still. Only a
            fenced bundle can be verified and count toward a prerequisite.
  --scope   which legs to capture; the default is all of them. `+"`config,state`"+`
            is the LIGHT bundle: the config allowlist, the sealed secret files,
            the SQLite state (VACUUM INTO, as the full bundle does), the
            rendered units, the registry row and the rollback descriptor — no
            store snapshots, no fence, and the API keeps serving. It runs in
            seconds and is the safety net a handover takes; it is never a
            restore prerequisite. --fence with a light scope is refused.
  --secrets what happens to the tenant's secret files. include (default):
            sealed with age to <ctl config dir>/backup-recipients.txt when it
            names a recipient, otherwise left out with a warning. skip: never
            sealed. require: refused unless a recipient is configured (`+"`ragstack-ctl fleet backup-identity init`"+`).
            Secrets are never written in the clear.
`, opFlagSummary)
		return exitUsage
	case "restore":
		fmt.Fprintf(stderr, `usage: ragstack-ctl tenant restore <source> --from <bundle-id> --as <fresh-tenant> %s

  <source>  the tenant the bundle was taken from. restore is destructive on it
            only in the bookkeeping sense — it marks the bundle verified — so
            its confirm value is the SOURCE name: --yes-destructive <source>.
  --as      a tenant that does NOT exist. v1 restores side by side: the fresh
            tenant is built from the source's artifact, the bundle is poured
            into it, and its counts are checked against the manifest before
            anything is published.

The restored tenant's credentials are FRESH and are printed ONCE, when the job
succeeds. That needs the job to be followed, so --wait is implied.
`, opFlagSummary)
		return exitUsage
	case "decommission":
		fmt.Fprintf(stderr, `usage: ragstack-ctl tenant decommission <name> [--archive=false] %s

  Quarantines a tenant the ctl runs: its legs stopped and disabled, its units
  removed, its row marked quarantined (with a `+"`quarantine`"+` block naming the
  renamed tree, the job and the bundle), its data directory renamed to
  <data_dir>.quarantined-<ts> with a RECOVERY.json inside. Nothing is deleted.

  --archive  (default true) archive first, in the same job: a fenced full
             backup with the tenant's secrets sealed (secrets=require), checked,
             recorded as last_backup — and the API is NOT started again before
             the quarantine. Refused unless a backup recipient is configured
             (`+"`ragstack-ctl fleet backup-identity init`"+`).
  --archive=false
             quarantine without a new bundle; requires the row's last_backup to
             be fenced and checked or verified (a selftest sandbox needs none).
`, opFlagSummary)
		return exitUsage
	case "purge":
		fmt.Fprintf(stderr, `usage: ragstack-ctl tenant purge <name> [--keep-archive] %s

  DELETES a tenant `+"`decommission`"+` has quarantined — the one destructive op in the
  control plane. Refused unless the row is at state quarantined with a
  quarantine.dir, and on a tenant the ctl does not run. In order: proves nothing
  listens on the tenant's port block and that the quarantined tree's
  RECOVERY.json names it; removes the worktree (and prunes the mirror), the
  units directory, the archive (every bundle and .tar under the backup root),
  the quarantined data tree; then deletes the registry row. A production block
  leaves a tombstone so its ports are never reused; a selftest sandbox leaves
  none. Nothing of the tenant remains but the tombstone and the audit rows.
  None of it can be undone. Confirm with --yes-destructive <name>.

  --keep-archive  keep <backups>/<name>/ (every bundle and .tar); delete the rest.
`, opFlagSummary)
		return exitUsage
	case "update-code":
		fmt.Fprintf(stderr, `usage: ragstack-ctl tenant update-code <name> --image NAME [--artifact ID] [--no-rebuild-ui|--rebuild-ui] %s

  Moves a ctl-run tenant's API onto a PREPARED server image in one job
  (`+"`ragstack-ctl fleet image list`"+`). A worktree-mode tenant is migrated by it:
  its API becomes the apptainer instance api-<name>. In order: a light
  pre-update bundle (config + state), the image proved (sha256, labels, its
  commit in the mirror), the worktree checked out at the image's commit, the
  static UI rebuilt from --artifact and swapped in (dist.prev-<ts> kept), the
  API stopped, the registry pointed at the image, the API started from it, and
  post-checks (/health, /v1/health/deep, /v1/version == the image's version
  and commit, the UI through the gateway). Any failure rolls all of it back:
  the old UI, the old image (or the worktree launch) and the OLD API running.

  --image NAME      the prepared server image (required)
  --artifact ID     the prepared artifact the static UI is rebuilt from; its
                    commit must be the image's. Required when the UI is rebuilt
  --no-rebuild-ui   an API-only patch: the served UI is left alone and no
                    artifact is needed (the default for a non-static UI)
  --rebuild-ui      rebuild the UI (the default for a static UI)

  A job interrupted between the registry swap and the new start (the daemon
  died) cannot be resumed: recover with `+"`ragstack-ctl tenant start <name>`"+`
  — the row already names the new image — or run update-code again.
  Destructive: confirm with --yes-destructive <name>.
`, opFlagSummary)
		return exitUsage
	default:
		return usageErr("usage: ragstack-ctl tenant %s <name> %s", verb, opFlagSummary)
	}
}

const opFlagSummary = "[--dry-run] [--yes|--yes-destructive NAME] [--wait] [--server URL] [--api-key-file F]"

// cmdTenantOp is `tenant start|stop|restart|backup|restore|decommission|purge`.
func cmdTenantOp(verb string, args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)

	var (
		only                multiFlag
		scope               multiFlag
		force, keepEnabled  *bool
		fence, tar          *bool
		secrets             *string
		from, as            *string
		archive             *bool
		keepArchive         *bool
		wantsOnly, wantsFrm bool
		wantsScope          bool
		image, artifact     *string
		noRebuildUI         *bool
		rebuildUI           *bool
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
		wantsScope = true
		fs.Var(&scope, "scope", "which legs to capture: config, state, stores (repeatable or a comma list; default all three)")
		secrets = fs.String("secrets", "", "include (default) | skip | require: whether the secret files are sealed into the bundle")
	case "decommission":
		archive = fs.Bool("archive", true, "archive first: a fenced, checked backup with the secrets sealed, then the quarantine (--archive=false: quarantine over the existing last_backup)")
	case "purge":
		keepArchive = fs.Bool("keep-archive", false, "keep the tenant's bundles under the backup root; delete everything else")
	case "update-code":
		image = fs.String("image", "", "the prepared server image to move the API onto (required)")
		artifact = fs.String("artifact", "", "the prepared artifact the static UI is rebuilt from (at the image's commit)")
		noRebuildUI = fs.Bool("no-rebuild-ui", false, "leave the served UI alone (an API-only patch)")
		rebuildUI = fs.Bool("rebuild-ui", false, "rebuild the static UI from --artifact (the default for a static UI)")
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
	if archive != nil && set["archive"] {
		opArgs["archive"] = *archive
	}
	if keepArchive != nil && set["keep-archive"] {
		opArgs["keep_archive"] = *keepArchive
	}
	if wantsScope && len(scope) > 0 {
		opArgs["scope"] = []string(scope)
	}
	if secrets != nil && set["secrets"] {
		// Passed through as given: the args schema owns the enum, so a typo is
		// the same 422 `validation` the HTTP surface answers.
		opArgs["secrets"] = *secrets
	}
	if verb == "update-code" {
		if *image == "" {
			return tenantOpUsage(verb)
		}
		if set["no-rebuild-ui"] && set["rebuild-ui"] && *noRebuildUI && *rebuildUI {
			return usageErr("usage: --rebuild-ui and --no-rebuild-ui say opposite things; pass one")
		}
		opArgs["image"] = *image
		if *artifact != "" {
			opArgs["artifact_id"] = *artifact
		}
		// Absent is "the row decides" (rebuild a static UI): only a flag the
		// operator wrote lands in args, for the reason the file comment gives.
		switch {
		case set["no-rebuild-ui"]:
			opArgs["rebuild_ui"] = !*noRebuildUI
		case set["rebuild-ui"]:
			opArgs["rebuild_ui"] = *rebuildUI
		}
	}
	if wantsFrm {
		if *from == "" || *as == "" {
			return tenantOpUsage(verb)
		}
		opArgs["from"] = *from
		opArgs["as"] = *as
		// A restore MINTS the fresh tenant's credentials, and they exist for
		// exactly one read. So the command has to still be there when the job
		// ends: --wait is implied for an execute, exactly as it is for `tenant
		// create`. A dry run prints a plan and mints nothing.
		if !*o.dryRun {
			*o.wait = true
			o.wantSecrets = true
		}
	}
	return submitOp(o, tenantOpTarget(name, verb), opArgs)
}

func tenantOpTarget(name, verb string) opTarget {
	return opTarget{path: "/v1/tenants/" + name + "/ops/" + verb, op: verb, tenant: name}
}

// ---------------------------------------------------------------- key

func keyUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl key <verb> <tenant> … %s

  mint   <tenant> <label> --role admin|user [--restart] [--prove] [--tenant-string S]
  revoke <tenant> <id> [--restart] [--prove]

--tenant-string is the principal the tenant API stamps on everything the key
writes — NOT the tenant's name: the adopted ledgers use values like asm-ops,
svc-asm-web and asm-ro. Left out, the mint follows whatever convention the
tenant's own ledger already uses, and refuses when that ledger uses more than
one. A key with the wrong one authenticates and then sees none of the tenant's
documents, which is what --prove now checks for.

--prove dials the tenant API after the restart and records the verdict on the
job: a surviving admin key answers 200 (so the API is up and authenticating),
the minted key answers 200 AND can see the tenant's collections, the revoked
key answers 401. The credentials are read from the tenant's own env files; the
job records fingerprints and status codes, never a value.

An executed mint waits for its job (--wait is implied) and prints the minted
value ONCE, from the job's one-time secrets envelope; nothing stores it. If the
command was interrupted before it could collect it, `+"`ragstack-ctl job secrets <id>`"+`
reads the envelope within its 15-minute window.
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
	prove := fs.Bool("prove", false, "after the restart, dial the tenant: 200 for what should work, 401 for what "+
		"should not, and the minted key must SEE the tenant's collections (needs --restart)")
	tenantString := fs.String("tenant-string", "", "the principal API_KEY_TENANTS maps the minted key to "+
		"(default: the convention the tenant's own ledger already uses)")

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
		if set["prove"] {
			opArgs["prove"] = *prove
		}
		if *tenantString != "" {
			opArgs["tenant_string"] = *tenantString
		}
		// The minted value exists for exactly one read of the job's secrets
		// envelope, so the command has to still be there when the job ends:
		// --wait is implied for an execute, exactly as it is for `tenant
		// create`, and the value is collected and printed once. A dry run
		// prints a plan and mints nothing.
		if !*o.dryRun {
			*o.wait = true
			o.wantSecrets = true
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
		if set["prove"] {
			opArgs["prove"] = *prove
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
                                 executable-surface keys are refused (409) —
                                 the latter go through set-surface below
  unset     <tenant> KEY
  normalize <tenant>             rewrite the env files into canonical form,
                                 split the secrets into secrets.env, and record
                                 env_layout + the new checksums in the registry
  pg-password <tenant>           derive APPTAINERENV_POSTGRES_PASSWORD into
                                 secrets.env from the connection strings already
                                 there (falling back to the literal in
                                 <data_dir>/bin/up.sh). It is the ONE name the
                                 instance supervisor looks for, and a tenant
                                 provisioned by new-tenant.sh has it under no
                                 such name — so without this its handover stops
                                 everything and then cannot start its postgres.
                                 Idempotent, backed up, never printed. CLI-only.
  set-surface <tenant> KEY=VALUE…
                                 an EXECUTABLE-SURFACE key (LLM_ENDPOINT, GOWE_URL,
                                 COLLECTIONS_FILE, GOWE_IMAGE_DIRS, the store URLs
                                 and routing tables, …). CLI-only and --direct
                                 only (implied; --server is refused): run it on
                                 the host as the ctl account, e.g.
                                   ops/coconut/ctl-as-svc.sh env set-surface clark \
                                     LLM_ENDPOINT=http://mango.cels.anl.gov:8003 --dry-run
                                 Every value is validated (settings/surface.go):
                                 a URL's host must be in CTL_ALLOWED_ENDPOINT_HOSTS
                                 and carry no user:password@; a path must be under
                                 the tenant's data dir or CTL_API_BIND_ROOTS and
                                 never the ctl's dirs or another tenant's.
                                 PYTHONPATH PATH HF_HOME PORT ROOT_PATH are refused.
                                 Backed up as tenant.env.bak-env-set-surface-<ts>;
                                 the registry records restart_pending and the new
                                 env_file_sha256; effective at the next restart.
  unset-surface <tenant> KEY…    remove executable-surface keys (same rules).

  The idempotency key is derived from the request, so re-running the same
  set-surface/set command returns the EARLIER job — even after a hand edit
  reverted what it did. Pass --new-key to run it again on purpose.
`, opFlagSummary)
	return exitUsage
}

// cmdEnvSurface is `env set-surface` / `env unset-surface` (#714). Variadic
// positionals, so it does not go through cmdEnv's fixed-arity parse.
func cmdEnvSurface(verb string, args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("env "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	pos, rest := takePositionals(args, 1<<16)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) < 2 {
		return envUsage()
	}
	// CLI-only AND --direct-only, as `env pg-password`: the verbs are not on
	// the daemon's HTTP surface at all, and their planners refuse any engine
	// that is not a --direct one.
	if code := refuseServerFlag(fs, "env "+verb, "it edits executable-surface keys, which ADR-0007 keeps off every "+
		"HTTP surface; the daemon refuses the verb (422) and its planner refuses any engine but a --direct one"); code != exitOK {
		return code
	}
	*o.direct = true
	tenant := pos[0]
	switch verb {
	case "set-surface":
		values := map[string]any{}
		for _, kv := range pos[1:] {
			k, v, ok := strings.Cut(kv, "=")
			if !ok || k == "" {
				return usageErr("env set-surface: %q is not KEY=VALUE", kv)
			}
			if _, dup := values[k]; dup {
				return usageErr("env set-surface: %s is given twice", k)
			}
			values[k] = v
		}
		return submitOp(o, tenantOpTarget(tenant, "env-set-surface"), map[string]any{"values": values})
	default:
		keys := append([]string(nil), pos[1:]...)
		// Sorted, so the derived idempotency key does not depend on the order
		// the keys were typed in (the op sorts them again).
		sort.Strings(keys)
		for i := 1; i < len(keys); i++ {
			if keys[i] == keys[i-1] {
				return usageErr("env unset-surface: %s is given twice", keys[i])
			}
		}
		return submitOp(o, tenantOpTarget(tenant, "env-unset-surface"), map[string]any{"keys": keys})
	}
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
	case "pg-password":
		op, want = "env-pg-password", 1
	case "set-surface", "unset-surface":
		return cmdEnvSurface(verb, args, registryPath, ragRoot, jsonOut)
	case "help", "-h", "--help":
		envUsage()
		return exitOK
	default:
		return usageErr("env: unknown verb %q (set|unset|normalize|pg-password|set-surface|unset-surface)", verb)
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
	case "env-pg-password":
		// CLI-only: it rewrites a 0640 file the daemon's account may only
		// READ, on a tenant the daemon cannot supervise yet.
		if code := refuseServerFlag(fs, "env pg-password", "it rewrites the tenant's secrets.env, which belongs to "+
			"the tenant's own account — the daemon has read access through an ACL and nothing more"); code != exitOK {
			return code
		}
		*o.direct = true
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
