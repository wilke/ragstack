package main

// `tenant set-ui-mode` and `tenant set-bind` — the two PR-E preparation ops
// on the command line.
//
// They are JOBS (planned, locked, checkpointed, audited, rollback-able), like
// every other verb in ops.go; what is different is that the daemon has no
// route for them, so `--direct` is implied and `--server` is refused rather
// than quietly ignored. The reasons are in contracts/ctl/openapi.yaml's
// x-ctl-cli-op-args comment and in internal/ctl/ops/prepare.go: one of them
// reaches the network and renames directories the daemon's account does not
// own, and both act on tenants the daemon cannot supervise until the handover.
//
// Everything else here is what every command in this binary does: turn the
// flags into the `args` object the contract describes and hand it to submitOp.

import (
	"flag"
	"fmt"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

func setUIModeUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant set-ui-mode <name> static|external [--ui-port P] %s

How this tenant's UI is served. CLI-only (--direct is implied): the static
direction may run `+"`npm ci`"+` and renames directories under the tenant's data
dir, both of which belong to the account that OWNS those paths.

  static            Build the UI from <worktree>/frontend and let nginx serve
                    it from <data_dir>/ui/dist:
                      1. npm ci, only when node_modules is absent
                      2. vite build --base /ragstack/<name>/ui/ into dist.building
                      3. stop this tenant's Vite dev server, if one holds the
                         recorded ui port AND its cwd is under <worktree>/frontend
                         and its argv mentions vite (never by process name)
                      4. one rename: dist -> dist.prev-<ts>, dist.building -> dist
                      5. registry ui.mode=static (port cleared)
                      6. publish a gateway generation — the static alias replaces
                         this tenant's $tenant_ui row
                      7. GET /ragstack/<name>/ui/ through the gateway, expect 200
                    Every step rolls back, including the dist swap.

  external --ui-port P
                    Registry only: ui.mode=external, ui.port=P, and a gateway
                    generation that keeps the $tenant_ui row pointing at
                    127.0.0.1:P. NOTHING is built, started or stopped — it is
                    for a dev server somebody else runs and goes on running
                    (the `+"`dev`"+` tenant's documented exception).

--dry-run prints the plan and changes nothing. --yes-destructive <name> is
required to execute the static direction (it stops a process and moves the
directory nginx is serving).
`, opFlagSummary)
	return exitUsage
}

// cmdTenantSetUIMode is `ragstack-ctl tenant set-ui-mode <name> <mode>`.
func cmdTenantSetUIMode(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant set-ui-mode", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	uiPort := fs.Int("ui-port", 0, "the port an `external` UI is served on (refused for `static`)")

	// Two positionals: the tenant and the mode. The mode is a positional
	// rather than a flag because it is the whole point of the command — an
	// operator who typed `set-ui-mode dev` and forgot `--mode` would otherwise
	// get a plan for whatever the default was.
	pos, rest := takePositionals(args, 2)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 2 {
		return setUIModeUsage()
	}
	name, mode := pos[0], pos[1]
	if mode != registry.UIModeStatic && mode != registry.UIModeExternal {
		if mode == registry.UIModeDev {
			return usageErr("set-ui-mode does not move a tenant INTO `dev`: a Vite dev server is not something the "+
				"ctl supervises (instance mode refuses one outright). A hand-run dev server is `external` "+
				"— `ragstack-ctl tenant set-ui-mode %s external --ui-port <P>`", name)
		}
		return usageErr("set-ui-mode: mode %q is not static or external", mode)
	}
	if code := refuseServerFlag(fs, "tenant set-ui-mode", "it may run `npm ci` (the one step that touches the "+
		"network) and it renames directories inside the tenant's data dir, which only that tree's owner may do"); code != exitOK {
		return code
	}
	*o.direct = true

	opArgs := map[string]any{"mode": mode}
	if setFlags(fs)["ui-port"] {
		opArgs["ui_port"] = *uiPort
	}
	return submitOp(o, tenantOpTarget(name, "set-ui-mode"), opArgs)
}

func setBindUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant set-bind <name> 127.0.0.1|0.0.0.0 %s

The address this tenant's API binds. It writes the registry's `+"`api.bind`"+` and
nothing else — that field is where BOTH launch paths read the bind from:

  * a ctl-supervised start, through render.APIArgv's --host
  * a hand start after a reboot, through ops/coconut/restore.sh, which reads
    the tenant's row out of registry.json

There is deliberately no tenant.env key: the tenant API has never read one
(nothing under python/ragstack looks for a bind in the environment), the value
reaches uvicorn only as a command-line argument, and a key written here for
symmetry would be configuration nothing reads.

Effective at the tenant's NEXT restart; the running API keeps its current bind
and this command does not restart it. The row is marked restart_pending.

127.0.0.1 is the one to want (plan decision D1): everything reaches these APIs
through the coconut gateway, which proxies over loopback. 0.0.0.0 makes the
API reachable from any host that can route to coconut, on a port whose only
authentication is a header.
`, opFlagSummary)
	return exitUsage
}

// cmdTenantSetBind is `ragstack-ctl tenant set-bind <name> <bind>`.
func cmdTenantSetBind(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant set-bind", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)

	pos, rest := takePositionals(args, 2)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 2 {
		return setBindUsage()
	}
	name, bind := pos[0], pos[1]
	if bind != "127.0.0.1" && bind != "0.0.0.0" {
		return usageErr("set-bind: %q is not 127.0.0.1 or 0.0.0.0 (`localhost` is a name, not an address: it "+
			"resolves differently depending on /etc/hosts, so the registry does not take it)", bind)
	}
	if code := refuseServerFlag(fs, "tenant set-bind", "it is a preparation op for a tenant the daemon cannot yet "+
		"supervise — the row it edits says supervisor: manual"); code != exitOK {
		return code
	}
	*o.direct = true
	return submitOp(o, tenantOpTarget(name, "set-bind"), map[string]any{"bind": bind})
}

// refuseServerFlag says why a CLI-only op cannot be sent to the daemon.
//
// Refused rather than ignored, for the reason `fleet artifact prepare` gives:
// the daemon has no route for these verbs, so a request would come back 422
// "not a verb", which reads as a bug in the CLI rather than as a deliberate
// design. One helper, so the three CLI-only commands say it the same way.
func refuseServerFlag(fs *flag.FlagSet, what, why string) int {
	if fs.Lookup("server") == nil || !setFlags(fs)["server"] {
		return exitOK
	}
	fmt.Fprintf(stderr, "ragstack-ctl: `%s` has no daemon route and --server cannot reach it: %s. "+
		"Run it on the host, without --server.\n", what, why)
	return exitUsage
}
