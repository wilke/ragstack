package ops

// The PR-E preparation ops: `set-ui-mode` and `set-bind`.
//
// Both act on a tenant that is still hand-started — `supervisor: manual`,
// `owner: wilke`, processes the daemon can neither see nor signal — and both
// exist to move ONE fact about that tenant into a shape the control plane can
// operate, without changing anything a user notices. They are the A2 and A4
// rows of the PR-E plan: run them, days before a handover, in any order, as
// often as you like.
//
// They are CLI-only jobs (`x-ctl-cli-op-args`, `ragstack-ctl … --direct`), for
// the reasons the contract spells out: `set-ui-mode static` runs `npm ci` —
// the one step in this whole control plane that reaches the network — and
// renames directories inside a tree the daemon's account does not own.
//
// What makes them safe to run against a live tenant:
//
//   - the UI build lands in a STAGING directory (`dist.building`) and becomes
//     `dist` in one rename, with the previous build kept beside it as
//     `dist.prev-<ts>`; nginx is serving the old directory right up to that
//     rename and the new one immediately after it;
//   - the Vite dev server is stopped by PORT AND IDENTITY (its cwd is under
//     `<worktree>/frontend` and `vite` is in its argv), never by name — the
//     #402 rule;
//   - `set-bind` writes the registry and nothing else, so it cannot disturb a
//     running process at all; it takes effect at the tenant's next start.

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// stagingDist is where `vite build` writes before the swap. A FIXED name
// rather than a stamped one: `vite build --emptyOutDir` clears it first, so a
// directory left behind by an interrupted job is overwritten rather than
// accumulated, and the build step and the swap step do not have to agree on a
// clock reading to name the same directory.
const stagingDist = "dist.building"

// prevDistPrefix is the external-ID namespace the swap records the moved-aside
// directory under, so the rollback puts back the build that was serving before
// this job — including after an interruption, when the planner's own memory of
// it is gone.
const prevDistPrefix = "ui-dist-prev:"

// distDir is where nginx aliases this tenant's static UI from. It is derived
// from the ROW's data_dir rather than from the standard layout, because
// render.NginxStatic derives it that way too and an adopted tenant's data dir
// is not always <data>/<manifest_name>.
func distDir(t *registry.Tenant) string { return filepath.Join(t.DataDir, "ui", "dist") }

// uiBase is the `--base` the bundle is built with and the path the gateway
// serves it at. The row's own value wins (adopt records it from --public-name);
// the name-derived path is the fallback, exactly as the renderer does it.
func uiBase(t *registry.Tenant) string {
	if t.UI.Base != "" {
		return t.UI.Base
	}
	return "/ragstack/" + t.Name + "/ui/"
}

// ---------------------------------------------------------------- set-ui-mode

func planSetUIMode(_ context.Context, p *planner, args map[string]any) error {
	mode, port := argStringOf(args, "mode"), argIntOf(args, "ui_port")
	switch mode {
	case registry.UIModeExternal:
		if port == 0 {
			return p.refuse("ui mode `external` needs the port the UI is served on (--ui-port): the gateway's " +
				"$tenant_ui row is a proxy target, and a row without one is a UI the gateway cannot reach")
		}
		return planSetUIModeExternal(p, port)
	case registry.UIModeStatic:
		if port != 0 {
			return p.refuse("ui mode `static` takes no port: nginx serves %s from a directory, so a port would be "+
				"a value nothing reads", distDir(p.t))
		}
		return planSetUIModeStatic(p)
	default:
		// Unreachable: the args schema's enum is static|external.
		return fmt.Errorf("%w: set-ui-mode: mode %q is not static or external", jobs.ErrValidation, mode)
	}
}

// planSetUIModeExternal is the registry-only direction (plan decision D2:
// `dev` keeps its hand-run Vite server as an unmanaged external UI).
//
// It starts nothing, stops nothing and builds nothing — the gateway keeps
// rendering the tenant's `$tenant_ui` row, at the port this records — so the
// only thing that can change under an operator is the registry, and the
// publish afterwards is a generation that differs from the last one only if
// the port did.
func planSetUIModeExternal(p *planner, port int) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest, model.LockGateway)
	prevMode, prevPort := p.t.UI.Mode, int(p.t.UI.Port)
	if prevMode == registry.UIModeStatic {
		p.warn("this tenant's UI is a static build today: after this op the gateway proxies " + uiBase(p.t) +
			" to 127.0.0.1:" + strconv.Itoa(port) + " instead of serving " + distDir(p.t) +
			", and the built bundle is left on disk untouched")
	}
	p.addUIRegistryStep(fmt.Sprintf("record ui.mode external on port %d (nothing is started or stopped)", port),
		registry.UIModeExternal, port, prevMode, prevPort)
	p.addGatewayPublishFor("the $tenant_ui row now points at 127.0.0.1:" + strconv.Itoa(port))
	p.result["ui_mode"] = registry.UIModeExternal
	p.result["ui_port"] = port
	p.result["pending_until_restart"] = false
	p.warn("nothing about the UI process itself changes: whoever runs the server on " + strconv.Itoa(port) +
		" goes on running it, and the ctl will not start, stop or supervise it")
	return nil
}

// planSetUIModeStatic is the A2 direction: build, install, switch, prove.
func planSetUIModeStatic(p *planner) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest, model.LockGateway)
	t := p.t
	if t.Worktree == "" {
		return p.refuse("%s has no worktree recorded, so there is no frontend to build", t.Name)
	}
	dist := distDir(t)
	staging := filepath.Join(filepath.Dir(dist), stagingDist)
	base := uiBase(t)
	frontend := filepath.Join(t.Worktree, "frontend")
	prevMode, prevPort := t.UI.Mode, int(t.UI.Port)

	// The skip is keyed on the vite BINARY, not on the node_modules directory.
	// An interrupted `npm ci` leaves a directory with some of a thousand
	// packages in it and no `.bin/vite`; keying on the directory declared that
	// install finished, skipped the repair for good, and left the build step to
	// fail with "prepare the artifact first" — a remedy that has nothing to do
	// with the problem. The file this step exists to produce is the thing to
	// look for.
	vite := filepath.Join(frontend, "node_modules", ".bin", "vite")
	p.addFor("build", step{
		Kind: "apptainer", Title: "install the frontend's locked dependencies, unless node_modules/.bin/vite is there",
		Targets: []string{frontend},
		Warnings: []string{"this step reaches the NETWORK (`npm ci`) — and only when " + vite +
			" is not already there; it is why set-ui-mode is CLI-only"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			present, err := filePresent(ctx, sc, vite)
			if err != nil {
				return "", err
			}
			if present {
				sc.Logf("%s is present; nothing to install", vite)
				return "the frontend's dependencies are already installed", nil
			}
			if err := sc.Ops.Drivers.Build().NpmCI(ctx, t.Worktree, npmCacheDir(sc.Ops.Roots)); err != nil {
				return "", err
			}
			return "node_modules installed in " + frontend, nil
		},
	})

	p.addFor("build", step{
		Kind: "apptainer", Title: "vite build --base " + base + " into " + stagingDist,
		Targets:    []string{frontend, staging},
		WouldWrite: []model.WouldWrite{{Path: staging, Mode: "0640", Preview: ""}},
		Warnings: []string{"the bundle hard-codes " + base + " into every asset URL, so it serves from that " +
			"gateway path and no other; the live " + dist + " is not touched by this step"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			// The PARENT, not the staging directory: `vite build
			// --emptyOutDir` creates and clears its own output directory, and
			// a directory this step created would only make that clear step
			// the second thing to happen to it.
			if err := files.MkdirAll(ctx, filepath.Dir(staging), dirMode); err != nil {
				return "", err
			}
			if err := sc.Checkpoint("dir:" + staging); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Build().UI(ctx, t.Worktree, base, staging); err != nil {
				return "", err
			}
			return staging, nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			// The staged build is KEPT, deliberately, and this is the one place
			// that decides it. Two reasons: `Files.Remove` cannot delete a
			// non-empty directory, so every rollback used to end
			// `rolled_back_partial` over a tree nothing serves from; and the
			// swap step's own rollback moves the new build BACK here, so a
			// rollback that also deleted it would contradict the step above it.
			// The next run's `vite build --emptyOutDir` clears it.
			sc.Logf("the staged build is left at %s for inspection; the next build clears it", staging)
			return "left the staged build at " + staging, nil
		},
	})

	// The dev server, if there is one. It is stopped BEFORE the swap, so the
	// window in which the gateway's $tenant_ui row still points at a port and
	// the alias is not published yet is as short as the two steps after it.
	if prevPort != 0 {
		p.addDevServerStop(prevPort, frontend)
	} else {
		p.skip("proc", "stop the tenant's Vite dev server",
			"the registry records no UI port for this tenant, so there is no dev server to stop", t.Name)
	}

	p.addFor("files", step{
		Kind: "fs", Title: "swap the new build into place, keeping the previous one as dist.prev-<ts>",
		Targets:    []string{dist},
		WouldWrite: []model.WouldWrite{{Path: dist, Mode: "0640", Preview: ""}},
		Warnings: []string{"one rename, on the same filesystem: nginx serves the old directory until it happens " +
			"and the new one immediately after. The previous build is MOVED aside, never deleted"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			present, err := dirPresent(ctx, sc, dist)
			if err != nil {
				return "", err
			}
			if present {
				prev := dist + ".prev-" + p.stampOf(sc)
				// Recorded BEFORE the rename: the rollback has to be able to
				// name the directory that was serving, and a job that died
				// between the two has to leave that name behind.
				if err := sc.Checkpoint(prevDistPrefix + prev); err != nil {
					return "", err
				}
				if err := files.Rename(ctx, dist, prev); err != nil {
					return "", fmt.Errorf("moving the previous build aside as %s: %w", prev, err)
				}
				sc.Logf("previous build kept at %s", prev)
			}
			if err := files.Rename(ctx, staging, dist); err != nil {
				return "", fmt.Errorf("installing the new build as %s: %w", dist, err)
			}
			return dist, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			// Back to staging rather than removed: the build is this job's
			// output and a rollback is not a reason to throw work away.
			if err := files.Rename(ctx, dist, staging); err != nil && !errors.Is(err, fs.ErrNotExist) {
				return "", err
			}
			prev, ok := externalIDValue(sc.Step.ExternalIDs, prevDistPrefix)
			if !ok {
				return "there was no previous build to put back", nil
			}
			if err := files.Rename(ctx, prev, dist); err != nil {
				return "", fmt.Errorf("putting %s back as %s: %w", prev, dist, err)
			}
			return dist + " is the previous build again", nil
		},
	})

	if prevPort != 0 {
		p.warn(fmt.Sprintf("the Vite dev server on %d is STOPPED and is not restarted by a rollback: the ctl does "+
			"not know its command line. If this job rolls back, start it again the way you started it before "+
			"(the registry row and the gateway will be pointing at %d again)", prevPort, prevPort))
	}
	p.addUIRegistryStep("record ui.mode static (the port is cleared: nginx serves a directory)",
		registry.UIModeStatic, 0, prevMode, prevPort)
	p.addGatewayPublishFor("the static alias for " + base + " replaces this tenant's $tenant_ui row")
	p.addUIProbe(base)

	p.result["ui_mode"] = registry.UIModeStatic
	p.result["ui_dist"] = dist
	p.result["ui_base"] = base
	return nil
}

// addDevServerStop stops the tenant's OWN Vite dev server and nothing else.
//
// Two independent facts have to agree before a signal is sent: the process
// must be the one holding this tenant's UI port, and its identity must match
// (`Proc.Signal`'s check — cwd under <worktree>/frontend, `vite` in the argv).
// A port whose owner this account cannot read is a REFUSAL, not a skip: the
// operator running this op is the account that runs the dev server, so an
// unreadable owner means the process on that port is somebody else's.
func (p *planner) addDevServerStop(port int, frontend string) {
	p.addFor("proc", step{
		Kind: "proc", Title: fmt.Sprintf("stop the Vite dev server on %d (by port AND identity)", port),
		Targets: []string{strconv.Itoa(port)}, Destructive: true,
		Warnings: []string{"never by process name (MEMORY #402): the pid comes from the LISTEN table for " +
			strconv.Itoa(port) + " and is signalled only if its cwd is under " + frontend +
			" and its argv mentions `vite`"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			proc := sc.Ops.Drivers.Proc()
			pid, _, err := proc.Owner(ctx, port)
			if err != nil {
				return "", err
			}
			if pid == 0 {
				listening, lerr := proc.Listening(ctx, port)
				if lerr != nil {
					return "", lerr
				}
				if !listening {
					sc.Logf("nothing listens on %d; there is no dev server to stop", port)
					return fmt.Sprintf("nothing on %d", port), nil
				}
				return "", fmt.Errorf("%w: port %d is held by a process this account cannot attribute "+
					"(another account owns it): run this op as the account that started the dev server, or stop "+
					"it by hand first", jobs.ErrRefused, port)
			}
			if err := sc.Checkpoint("pid:" + strconv.Itoa(pid)); err != nil {
				return "", err
			}
			if err := proc.Signal(ctx, pid, frontend, "vite", "TERM"); err != nil {
				return "", err
			}
			sc.Logf("SIGTERM to pid %d (vite in %s)", pid, frontend)
			return fmt.Sprintf("stopped the dev server on %d (pid %d)", port, pid), nil
		},
		// Deliberately no Rollback, and this is the one thing a rollback of
		// this op does NOT put back. The ctl does not know how to start that
		// server: its command line is the operator's, not the registry's, and
		// nothing recorded it. A rolled-back row says `dev`/`external` again —
		// which is precisely the statement "a server somebody else runs belongs
		// on this port" — and the gateway routes to it again, so the repair is
		// the operator starting their dev server, exactly as they started it
		// the first time. The plan says so up front, and so does the runbook.
	})
}

// addUIRegistryStep writes ui.mode/ui.port and can put back what was there.
func (p *planner) addUIRegistryStep(title, mode string, port int, prevMode string, prevPort int) {
	name := p.tenant
	apply := func(t *registry.Tenant, mode string, port int) {
		t.UI.Mode = mode
		t.UI.Port = registry.NullPort(port)
		if t.UI.Base == "" {
			t.UI.Base = "/ragstack/" + t.Name + "/ui/"
		}
	}
	write := func(sc *jobs.StepContext, mode string, port int) (string, error) {
		save := p.op.deps.SaveFleet
		if save == nil {
			return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
		}
		f := sc.Ops.Fleet
		if f == nil {
			return "", fmt.Errorf("%w: the engine loaded no registry under the locks", jobs.ErrRefused)
		}
		t, ok := f.Tenants[name]
		if !ok {
			return "", fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, name)
		}
		apply(t, mode, port)
		if t.LastOps == nil {
			t.LastOps = map[string]registry.OpRecord{}
		}
		t.LastOps["set-ui-mode"] = registry.OpRecord{JobID: jobIDOf(sc), At: p.stampRFC3339(sc), Outcome: "succeeded"}
		if err := save(f); err != nil {
			return "", err
		}
		return fmt.Sprintf("%s ui.mode = %s (generation %d)", name, mode, f.Generation), nil
	}
	p.add(step{
		Kind: "registry", Title: title, Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			return write(sc, mode, port)
		},
		// This rollback carries the REPUBLISH as well as the row, because it is
		// the first point in the reverse order at which the row says the right
		// thing again. The publish step above cannot do it (it runs while the
		// row still says what the job set), and a republish left undone would
		// leave nginx serving a generation rendered from a row that no longer
		// exists — the static alias for a tenant whose dist the next rollback
		// step is about to move away.
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if prevMode == "" {
				// The row never recorded a mode (a pre-PR-B adoption). Putting
				// back the empty string would make the gateway renderer refuse
				// the WHOLE fleet's file, so the rollback says so and leaves
				// the mode this op set.
				return "the row recorded no ui.mode before this job; it is left as " + mode, nil
			}
			detail, err := write(sc, prevMode, prevPort)
			if err != nil {
				return "", err
			}
			// Best effort, and it says so: the row is back whatever the gateway
			// does, and a rollback that failed because nginx could not be
			// signalled would leave the operator with neither.
			gen, gdetail, gerr := sc.Ops.Drivers.Gateway().Apply(ctx, false)
			if gerr != nil {
				sc.Logf("the row is %s again but the gateway could not be republished (%v) — "+
					"run `ragstack-ctl gateway apply`", prevMode, gerr)
				return detail + "; the gateway still serves the previous generation: republish it by hand", nil
			}
			sc.Logf("republished as gen-%d from the restored row: %s", gen, gdetail)
			return detail + "; gateway republished from the restored row", nil
		},
	})
}

// addGatewayPublishFor publishes a generation and says what changed in it.
//
// It refuses nothing: a tenant the LIVE gateway does not route has no row for
// this generation to change, and publishing for it would advance the fleet's
// generation for a document nobody reads. That is reported as a skip rather
// than a failure — the registry write above it is the operation, and the
// publish is how the running nginx learns about it.
func (p *planner) addGatewayPublishFor(what string) {
	p.addFor("gateway", step{
		Kind: "nginx", Title: "gateway: publish a generation in which " + what,
		Targets: []string{p.tenant},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			routed, why, err := gatewayRoutes(ctx, sc, p.tenant)
			if err != nil {
				return "", err
			}
			if !routed {
				return "", fmt.Errorf("%w: %s — publish the tenant first (`ragstack-ctl gateway apply`), then "+
					"run this op again so the generation it publishes actually serves the change",
					jobs.ErrRefused, why)
			}
			if err := sc.Checkpoint("gateway:apply:pending"); err != nil {
				return "", err
			}
			gen, detail, err := sc.Ops.Drivers.Gateway().Apply(ctx, false)
			if err != nil {
				return "", err
			}
			if err := sc.Checkpoint("gateway:gen:" + strconv.Itoa(gen)); err != nil {
				return "", err
			}
			p.result["gateway_generation"] = gen
			return detail, nil
		},
		// NO rollback here, and the reason is the engine's rollback ORDER.
		//
		// Steps roll back last-to-first, and this step is planned AFTER the
		// registry write — so at this point the row still says what the job set
		// it to, and a publish from here would render exactly the generation
		// being undone. (The comment that used to sit here claimed the
		// opposite. It was wrong, and a reviewer's repro proved it.)
		//
		// The republish belongs to the step that restores the row, which runs
		// next: addUIRegistryStep's rollback publishes after it has put the row
		// back. This one only says what it left behind.
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if len(sc.Step.ExternalIDs) == 0 {
				return "nothing was published", nil
			}
			sc.Logf("the generation stays published for now; the registry rollback republishes from the restored row")
			return "left to the registry rollback, which republishes from the restored row", nil
		},
	})
}

// addUIProbe is the proof: the gateway answers the UI route with a page.
//
// 200 exactly. A static UI that 404s is the failure this op exists to prevent
// (doctor's ui_dist_missing, one alias away), and a 502 would mean the
// generation is routing to a port after all.
func (p *planner) addUIProbe(base string) {
	p.addFor("gateway", step{
		Kind: "probe", Title: "GET " + base + " through the live gateway (expect 200)",
		Targets: []string{base},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			status, err := sc.Ops.Drivers.Gateway().Probe(ctx, base)
			if err != nil {
				return "", fmt.Errorf("GET %s through the gateway: %w", base, err)
			}
			if status != 200 {
				return "", fmt.Errorf("%w: GET %s answered %d, want 200: the generation is published but the "+
					"static build is not being served from it", jobs.ErrRefused, base, status)
			}
			return fmt.Sprintf("GET %s = 200", base), nil
		},
	})
}

// dirPresent reports whether path is a directory this account can list.
//
// Files.ReadDir, not a stat: the Files driver is the seam the fakes replace,
// and fs.ErrNotExist is the one error that means "absent" — anything else is a
// directory that exists and could not be read, which a step must not silently
// treat as a reason to rebuild or to skip a rename.
func dirPresent(ctx context.Context, sc *jobs.StepContext, path string) (bool, error) {
	if _, err := sc.Ops.Drivers.Files().ReadDir(ctx, path); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return false, nil
		}
		return false, fmt.Errorf("reading %s: %w", path, err)
	}
	return true, nil
}

// filePresent is dirPresent for one FILE, and the distinction matters: the
// question "are the frontend's dependencies installed" is answered by
// node_modules/.bin/vite existing, not by the directory above it existing.
func filePresent(ctx context.Context, sc *jobs.StepContext, path string) (bool, error) {
	if _, err := sc.Ops.Drivers.Files().ReadFile(ctx, path); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return false, nil
		}
		return false, fmt.Errorf("reading %s: %w", path, err)
	}
	return true, nil
}

// ---------------------------------------------------------------- set-bind

func planSetBind(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	bind := argStringOf(args, "bind")
	prev := p.t.API.Bind
	if prev == bind {
		p.warn("api.bind is already " + bind + ": this op writes the registry anyway, so the job record says when " +
			"the value was last confirmed")
	}
	if bind == "0.0.0.0" {
		p.warn("0.0.0.0 makes this tenant's API reachable from every host that can route to coconut, on a port " +
			"whose only authentication is a header. The gateway proxies over loopback and does not need it " +
			"(plan PR-E decision D1: handed-over APIs bind 127.0.0.1)")
	} else if prev == "0.0.0.0" {
		p.warn("anything that reaches coconut:" + strconv.Itoa(p.t.Ports.API) + " directly from another host stops " +
			"working at the next restart; everything through the gateway is unaffected (it proxies over loopback)")
	}

	name := p.tenant
	p.add(step{
		Kind: "registry", Title: fmt.Sprintf("record api.bind = %s (was %s)", bind, orNone(prev)),
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"api.bind is the ONE place this value lives: `render.APIArgv` reads it for the " +
			"`--host` of a ctl-started uvicorn and ops/coconut/restore.sh reads it out of the same registry row " +
			"for a hand start. The tenant API has never read an env key for its bind, so none is written"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			f := sc.Ops.Fleet
			if f == nil {
				return "", fmt.Errorf("%w: the engine loaded no registry under the locks", jobs.ErrRefused)
			}
			t, ok := f.Tenants[name]
			if !ok {
				return "", fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, name)
			}
			if err := sc.Checkpoint("api-bind-prev:" + t.API.Bind); err != nil {
				return "", err
			}
			t.API.Bind = bind
			// The row now describes a process that is still bound the old way.
			// restart_pending is the contract's word for exactly that, and it
			// is what `tenant show` prints and doctor reads.
			t.RestartPending = true
			if t.LastOps == nil {
				t.LastOps = map[string]registry.OpRecord{}
			}
			t.LastOps["set-bind"] = registry.OpRecord{JobID: jobIDOf(sc), At: p.stampRFC3339(sc), Outcome: "succeeded"}
			if err := save(f); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s api.bind = %s (generation %d)", name, bind, f.Generation), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			was, ok := externalIDValue(sc.Step.ExternalIDs, "api-bind-prev:")
			if !ok || save == nil {
				return "nothing was written", nil
			}
			f := sc.Ops.Fleet
			t, exists := f.Tenants[name]
			if !exists {
				return "the registry row is gone; nothing to restore", nil
			}
			t.API.Bind = was
			if err := save(f); err != nil {
				return "", err
			}
			return "api.bind is " + was + " again", nil
		},
	})

	p.result["bind"] = bind
	p.result["previous_bind"] = prev
	p.result["pending_until_restart"] = true
	p.warn("the bind reaches uvicorn as a command-line argument at START-UP: the running API keeps its current " +
		"bind until it is restarted. Nothing here restarts it")
	return nil
}

// orNone renders an empty recorded value as the word rather than as nothing.
func orNone(s string) string {
	if strings.TrimSpace(s) == "" {
		return "(unrecorded)"
	}
	return s
}

// ---------------------------------------------------------------- set-supervisor

// planSetSupervisor corrects the registry's `supervisor` and touches nothing
// else.
//
// It exists for the row that disagrees with the host: a take that wrote
// `instance` and then failed before it started anything, an abandon that could
// not be run because the operator had already gone home, a tenant somebody
// restarted by hand under a row the ctl still thinks it owns. The repair for
// those is one field, and before this op the only way to write it was an
// editor and a lock file.
//
// What it refuses is the mistake it would otherwise make permanent: recording
// a supervisor for processes THIS account cannot act on. `instance` means "the
// ctl starts and stops this tenant", and an API port held by a pid this
// account cannot even attribute is an API the ctl can neither signal nor
// restart — a row that claimed otherwise would make `fleet stop --all` a
// no-op that reports success. `manual` means "somebody else started it", and
// writing that while this account's own apptainer instances are running for
// the tenant would orphan them: nothing would ever stop them again.
//
// `desired_boot` is the optional second field, and it is here because it is
// the other half of the same sentence: `supervisor` says who starts the
// tenant, `desired_boot` says whether anybody does at boot. A row can lose
// them together — a `--readopt` that dropped the handover block dropped the
// boot intent with it — and until this field there was no command that could
// write the second one back. Absent means "no opinion", so the common repair
// still touches exactly one field.
func planSetSupervisor(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	want := argStringOf(args, "supervisor")
	prev := p.t.Supervisor
	wantBoot := argStringOf(args, "desired_boot")
	prevBoot := p.t.DesiredBoot
	if p.t.Handover != nil {
		return p.refuse("%s has a handover in flight (phase %s): its supervisor is being moved by that protocol, "+
			"and a row edited underneath it would strand the tenant between two accounts. Take it, commit it, or "+
			"abandon it first (`ragstack-ctl tenant handover %s --abandon`)", p.t.Name, p.t.Handover.Phase, p.t.Name)
	}
	if prev == want && (wantBoot == "" || wantBoot == prevBoot) {
		p.warn("supervisor is already `" + want + "`: this op writes the registry anyway, so the job record says " +
			"when the value was last confirmed")
	}

	name := p.tenant
	port := p.t.Ports.API
	instances := tenantInstanceNames(p.t)
	p.addFor("proc", step{
		Kind: "probe", Title: "check that this account can act on what the row will claim",
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			proc := sc.Ops.Drivers.Proc()
			listening, err := proc.Listening(ctx, port)
			if err != nil {
				return "", err
			}
			pid := 0
			if listening {
				if pid, _, err = proc.Owner(ctx, port); err != nil {
					return "", err
				}
			}
			if want == supervisorInstance && listening && pid == 0 {
				return "", fmt.Errorf("%w: %d is held by a process this account cannot attribute, so it belongs to "+
					"another account. Recording `instance` would claim the ctl supervises an API it can neither "+
					"signal nor restart — hand the tenant over instead (`ragstack-ctl tenant handover %s "+
					"--release`, as its owner)", jobs.ErrRefused, port, name)
			}
			if want == supervisorManual {
				var running []string
				// BOTH registries. The question this refusal asks is "would
				// recording `manual` orphan a process", and an instance is
				// just as orphaned whether the ctl started it in its own
				// namespace or the operator started it by hand in the
				// account's default one (jobs.InstanceNamespace) — which is
				// the pair a half-finished handover leaves behind.
				for _, in := range instances {
					for _, ns := range []jobs.InstanceNamespace{jobs.NamespaceCtl, jobs.NamespaceAccountDefault} {
						up, err := instanceRunning(ctx, sc, in, ns)
						if err != nil {
							return "", err
						}
						if up {
							running = append(running, in)
							break
						}
					}
				}
				// The API counts too, and it was the leg this check forgot:
				// `manual` says "somebody else started it", and a uvicorn THIS
				// account is running under a row that says that is a process
				// `tenant stop` will refuse to touch. A port whose owner this
				// account can read is, by definition, this account's.
				if listening && pid != 0 {
					running = append(running, fmt.Sprintf("the API on %d (pid %d)", port, pid))
				}
				if len(running) > 0 {
					return "", fmt.Errorf("%w: this account is running %s for %s. Recording `manual` would say "+
						"somebody else started them, and nothing would ever stop them again: "+
						"`ragstack-ctl tenant stop %s` first", jobs.ErrRefused, strings.Join(running, ", "),
						name, name)
				}
			}
			if !listening {
				return fmt.Sprintf("nothing listens on %d", port), nil
			}
			return fmt.Sprintf("pid %d holds %d and this account can see it", pid, port), nil
		},
	})

	title := fmt.Sprintf("record supervisor = %s (was %s)", want, orNone(prev))
	if wantBoot != "" {
		title += fmt.Sprintf(" and desired_boot = %s (was %s)", wantBoot, orNone(prevBoot))
	}
	p.add(step{
		Kind: "registry", Title: title,
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"the REGISTRY only: no process is started, stopped or signalled by this op. It says " +
			"what the ctl believes about this tenant, and believing it is what makes the lifecycle verbs act"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "set-supervisor", func(t *registry.Tenant) error {
				t.Supervisor = want
				if wantBoot != "" {
					t.DesiredBoot = wantBoot
				}
				return nil
			})
			if err != nil {
				return "", err
			}
			if wantBoot != "" {
				return fmt.Sprintf("%s supervisor = %s, desired_boot = %s", name, want, wantBoot), nil
			}
			return fmt.Sprintf("%s supervisor = %s", name, want), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.Supervisor = prev
				if wantBoot != "" {
					t.DesiredBoot = prevBoot
				}
				return nil
			}); err != nil {
				return "", err
			}
			return name + " supervisor = " + prev + " again", nil
		},
	})
	p.result["supervisor"] = want
	p.result["previous_supervisor"] = prev
	// The boot fields are reported only when this call had an opinion about
	// them: a result carrying `desired_boot` for a run that never named it
	// would read as a write that did not happen.
	boot := p.t.DesiredBoot
	if wantBoot != "" {
		boot = wantBoot
		p.result["desired_boot"] = wantBoot
		p.result["previous_desired_boot"] = prevBoot
	}
	if want == supervisorManual {
		p.warn("`manual` means the ctl will not START this tenant: `ragstack-ctl tenant start " + name +
			"` refuses, and `ops/coconut/restore.sh --tenant " + name + "` is what brings it up")
	} else {
		p.warn("`instance` means `ragstack-ctl fleet start --all` will start this tenant at boot when " +
			"desired_boot is `enabled`; it is `" + boot + "` " + bootTense(wantBoot))
	}
	return nil
}

// bootTense keeps the supervisor warning honest about WHICH desired_boot it
// just quoted: the one on the row, or the one this same op is about to write.
func bootTense(wantBoot string) string {
	if wantBoot != "" {
		return "after this op"
	}
	return "today"
}

// tenantInstanceNames are the apptainer instances this tenant's own stores run
// under, in the order they are started.
func tenantInstanceNames(t *registry.Tenant) []string {
	var out []string
	if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive {
		out = append(out, instanceNameFor(render.LegQdrant, t.ManifestName))
	}
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive {
		out = append(out, instanceNameFor(render.LegES, t.ManifestName))
	}
	if t.Stores.Postgres.Kind == registry.PostgresKindLocal {
		out = append(out, instanceNameFor(render.LegPostgres, t.ManifestName))
	}
	return out
}
