package ops

// The API as an apptainer instance of a server image (PR-F F4, brief §1.2).
//
// A row with `server_image` runs its API as `apptainer instance run
// --no-home --cleanenv … <sif> api-<manifest> --host … --port …`. Everything
// here is the image half of the two API process functions in supervisor.go:
// startAPIProcess and stopAPIProcess switch on apiLaunch.Mode and come here, so
// every caller of those two — start, stop, restart, their rollbacks, the
// handover release, the backup fence, decommission, `fleet start --all` — runs
// an image row's API as an instance without a line of its own.
//
// Four rules, each the image twin of one the worktree half keeps:
//
//   - IDENTITY is the instance table plus the port: the API is running when
//     `api-<manifest>` is listed in the ctl's registry AND the process holding
//     the API port descends from that instance (Proc.InstanceOwnsPort). There
//     is no pidfile anywhere in this mode.
//   - NO SECRET ON THE ARGV. tenant.env ∪ secrets.env ∪ the unit environment is
//     read at RUN time and handed to apptainer as APPTAINERENV_<KEY> in the
//     child's environment only; `--cleanenv` drops everything else of the
//     daemon's (its own credentials, a host PYTHONPATH).
//   - THE IMAGE IS PROVED BEFORE EVERY START: its labels agree with the row's
//     server_image (the receipt the row copied), and its bytes hash to the
//     recorded sha256. A start, a restart and a stop's rollback all go
//     through it.
//   - THE BINDS ARE THE PLAN'S. They are derived at plan time from the row and
//     the tenant's public environment; at run time the probe holds every
//     path-valued setting the API will actually see to them, so a path the plan
//     did not bind is a refusal, not a container that cannot see its data.

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// apptainerBinary is what the plan's argv preview names; the driver runs its
// own configured binary (the stores' previews name the same path).
const apptainerBinary = "/usr/bin/apptainer"

// apiImageLaunch fills an image row's launch: the instance, the rendered
// command, and the refusal of env keys that would hijack the container.
func (p *planner) apiImageLaunch(row *registry.Tenant, tp paths.Tenant, l apiLaunch) (apiLaunch, error) {
	l.Mode = apiModeImage
	l.Instance = render.APIImageInstanceName(row)
	l.PidFile, l.Dir = "", ""
	si := *row.ServerImage
	l.ServerImage = &si
	env, fileKeys := p.apiPublicEnv(row, tp)
	if bad := APIImageHijackKeys(env, fileKeys, row.SecretRefs); len(bad) > 0 {
		return l, fmt.Errorf("%w: %s defines %s, which would hijack the server image's container if forwarded as "+
			"APPTAINERENV_<KEY> (the image sets its own interpreter and loader paths). Remove %s from the env files "+
			"before the API runs from an image", jobs.ErrRefused, row.Name, strings.Join(bad, ", "),
			map[bool]string{true: "it", false: "them"}[len(bad) == 1])
	}
	img, err := render.APIImageLaunch(row, p.unitConfig(), env, tp.APILog)
	if err != nil {
		return l, err
	}
	l.Image = &img
	l.UnitEnv = img.UnitEnv
	return l, nil
}

// APIImageHijackKeys names every key of an image row's environment, as the
// plan can see it, that render.APIImageForbiddenEnvKey refuses: the public
// env (settings{} under tenant.env), the keys tenant.env itself defines, and
// the secret_refs key list (secrets.env is never read at plan time; its KEYS
// are on the row). Sorted, deduplicated.
func APIImageHijackKeys(env map[string]string, fileKeys []string, refs []registry.SecretRef) []string {
	seen := map[string]bool{}
	check := func(k string) {
		if render.APIImageForbiddenEnvKey(k) && !render.APIImageDroppedEnvKey(k) {
			seen[k] = true
		}
	}
	for k := range env {
		check(k)
	}
	for _, k := range fileKeys {
		check(k)
	}
	for _, r := range refs {
		check(r.Key)
	}
	out := make([]string, 0, len(seen))
	for k := range seen {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// apiPublicEnv is the tenant's PUBLIC environment as the plan can see it: the
// registry's settings{}, under the tenant.env the plan reads through the same
// file reader the env ops preview with — or, for a tenant this job is about to
// create, the tenant.env this job will write (planCreateSteps hands it over).
//
// The registry's settings{} is not enough on its own: it records PUBLIC keys
// only, and every path a bind is derived from (COLLECTIONS_FILE,
// GOWE_IMAGE_DIRS, the store paths) is an executable-surface key, which adopt
// and the env API deliberately keep out of it. A tenant.env that cannot be
// read while planning leaves the binds to settings{} alone and says so; the
// start's own probe then refuses any path the real file names that the plan
// did not bind.
func (p *planner) apiPublicEnv(row *registry.Tenant, tp paths.Tenant) (map[string]string, []string) {
	env := map[string]string{}
	for k, v := range row.Settings {
		env[k] = v
	}
	var fileKeys []string
	merge := func(vals map[string]string) {
		for k, v := range vals {
			env[k] = v
			fileKeys = append(fileKeys, k)
		}
	}
	if p.pendingEnv != nil && p.pendingEnvTenant == row.Name {
		merge(p.pendingEnv)
		sort.Strings(fileKeys)
		return env, fileKeys
	}
	b, err := p.readFile(context.Background(), tp.TenantEnv)
	if err != nil {
		p.warn("%s could not be read while planning (%v): the API instance's binds are derived from the registry's "+
			"settings{} alone, and the start refuses any path in the real file that they do not cover", tp.TenantEnv, err)
		return env, nil
	}
	f, _, perr := envfile.ParseLenient(b)
	if perr != nil {
		p.warn("%s does not parse while planning (%v): the API instance's binds are derived from the registry's "+
			"settings{} alone", tp.TenantEnv, perr)
		return env, nil
	}
	vals := map[string]string{}
	for _, a := range f.Assignments() {
		vals[a.Key] = a.Value
	}
	merge(vals)
	sort.Strings(fileKeys)
	return env, fileKeys
}

// addAPIInstanceStart plans the image-mode API start.
func (p *planner) addAPIInstanceStart(l apiLaunch) {
	name, port, img := l.Instance, l.Port, l.Image
	p.addFor("instance", step{
		Kind: "instance", Title: fmt.Sprintf("start the API instance %s from %s once its stores answer", name,
			l.ServerImage.Name),
		Targets:  []string{name},
		WouldRun: []model.WouldRun{{Argv: img.Argv(apptainerBinary)}},
		Warnings: []string{"the container's environment is tenant.env ∪ secrets.env ∪ the unit environment (" +
			strings.Join(envKeys(img.UnitEnv), " ") + "), read at RUN time and passed to apptainer as " +
			"APPTAINERENV_<KEY> in its own environment — never `--env` on this command line, which is world-readable",
			"before the start the image is proved: its labels must agree with server_image (version " +
				l.ServerImage.Version + ", commit " + l.ServerImage.Commit + ", build " + fmt.Sprint(l.ServerImage.Build) +
				") and its bytes must hash to sha256 " + l.ServerImage.SHA256 + "; every path-valued setting must be " +
				"absolute and inside one of the binds above",
			"the binds are derived from tenant.env as it was read while planning, and the plan hash covers them: a " +
				"tenant.env changed between the plan and the run makes the job plan_stale, not a start with stale binds",
			"readiness is the instance's: something listening on " + fmt.Sprint(port) + " is not enough — the " +
				"listener must descend from " + name + ", or the start fails and is rolled back",
			"there is no pidfile in image mode: the API is found by the instance table and the process holding " +
				fmt.Sprint(port) + "; there is no restart-on-failure either — `fleet start --all` is the watchdog"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return startAPIProcess(ctx, sc, l)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return rollbackAPIInstanceStart(ctx, sc, l)
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			up, _, err := apiInstanceRunning(ctx, sc, name, port)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if up {
				return jobs.ReconcileDone, nil
			}
			return jobs.ReconcileRedo, nil
		},
	})
}

// addAPIInstanceStop plans the image-mode API stop. launch is the start it
// undoes (#693), rendered from the same row; lerr is kept for the rollback to
// report, so a stop is never refused because its undo could not be rendered.
func (p *planner) addAPIInstanceStop(launch apiLaunch, lerr error) {
	name, port, tenant := launch.Instance, launch.Port, p.t.Name
	p.addFor("instance", step{
		Kind: "instance", Title: "stop the API instance " + name + ", then prove " + fmt.Sprint(port) + " is free",
		Destructive: true, Targets: []string{name},
		WouldRun: []model.WouldRun{{Argv: []string{apptainerBinary, "instance", "stop", name}}},
		Warnings: []string{"`apptainer instance stop` (SIGTERM to the instance), then up to " + apiStopTimeout.String() +
			" for the port to free; an instance that is not running is success, so this step is safe to re-run",
			"rollback starts the instance again from the same image, if this step found it running"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return stopAPIProcess(ctx, sc, launch)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if !recorded(sc, wasRunningID+name) {
				return name + " was not running before this step; nothing to start", nil
			}
			if lerr != nil {
				return "", fmt.Errorf("%s cannot be rendered from the registry row (%v); start it with "+
					"`ragstack-ctl tenant start %s`", name, lerr, tenant)
			}
			detail, err := startAPIProcess(ctx, sc, launch)
			if err != nil {
				return "", err
			}
			ready, err := awaitAPIReady(ctx, sc, launch)
			if err != nil {
				return "", err
			}
			return "the API is up again: " + detail + "; " + ready, nil
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			_, listed, err := instanceIn(ctx, sc, name, jobs.NamespaceCtl)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if listed || listening {
				return jobs.ReconcileRedo, nil
			}
			return jobs.ReconcileDone, nil
		},
	})
}

// apiInstanceRunning is the image-mode `running`: the instance is in the
// ctl's table AND the process holding port descends from it. It returns the
// instance row too, for a caller that reports its pid.
func apiInstanceRunning(ctx context.Context, sc *jobs.StepContext, name string, port int) (bool, jobs.Instance, error) {
	in, listed, err := instanceIn(ctx, sc, name, jobs.NamespaceCtl)
	if err != nil || !listed {
		return false, in, err
	}
	if in.PID <= 1 {
		// A table that gives the instance no pid cannot prove the port is
		// its; listed is all there is, and it is not "running" by this rule.
		sc.Logf("the instance table gives %s no pid, so it cannot be tied to %d", name, port)
		return false, in, nil
	}
	owns, err := sc.Ops.Drivers.Proc().InstanceOwnsPort(ctx, in.PID, port)
	if err != nil {
		return false, in, err
	}
	return owns, in, nil
}

// startAPIInstance is the image-mode start: the idempotency check, the image
// proof, the environment read at RUN time, the path probe, the store waits,
// and the instance run.
func startAPIInstance(ctx context.Context, sc *jobs.StepContext, l apiLaunch) (string, error) {
	if l.Image == nil || l.ServerImage == nil {
		return "", fmt.Errorf("%w: the API instance %s has no rendered launch", jobs.ErrRefused, l.Instance)
	}
	name, port, proc := l.Instance, l.Port, sc.Ops.Drivers.Proc()
	in, listed, err := instanceIn(ctx, sc, name, jobs.NamespaceCtl)
	if err != nil {
		return "", err
	}
	listening, err := proc.Listening(ctx, port)
	if err != nil {
		return "", err
	}
	if listed {
		owns := false
		if in.PID > 1 {
			if owns, err = proc.InstanceOwnsPort(ctx, in.PID, port); err != nil {
				return "", err
			}
		}
		switch {
		case owns:
			// Idempotent, as the store start is: `fleet start --all` runs over
			// a fleet half of which is up.
			sc.Logf("the API instance %s is already running (pid %d) and holds %d", name, in.PID, port)
			return fmt.Sprintf("already running: %s (pid %d)", name, in.PID), nil
		case listening:
			return "", fmt.Errorf("%w: the instance %s is running (pid %d) but %d is held by a process outside it — "+
				"something else has the API's port. Look at the port before starting anything",
				jobs.ErrRefused, name, in.PID, port)
		default:
			// Listed, port not yet held: an instance that is still importing.
			// apptainer refuses a second instance under the name, and the
			// readiness wait after this step is what decides.
			sc.Logf("the API instance %s is there (pid %d) but nothing listens on %d yet; not starting a second one",
				name, in.PID, port)
			return fmt.Sprintf("already started: %s (pid %d), not yet listening", name, in.PID), nil
		}
	}
	if listening {
		// Held, and no API instance of ours: by THIS account's own process
		// (a worktree uvicorn still running, the likeliest case) is a
		// refusal — the instance's uvicorn would fail to bind, and the
		// readiness wait would then be answered by the old process. A holder
		// this account cannot attribute (another account's socket) is the
		// doctor's to gate (port_owner_mismatch); it is logged here.
		owner, _, err := proc.Owner(ctx, port)
		if err != nil {
			return "", err
		}
		if owner > 0 {
			return "", fmt.Errorf("%w: %s is not running but pid %d already listens on %d: the instance would fail "+
				"to bind it. Stop that process first",
				jobs.ErrRefused, name, owner, port)
		}
		sc.Logf("WARNING: %d is held by a process this account cannot attribute; starting %s anyway", port, name)
	}
	detail, err := probeAPIImage(ctx, sc, l)
	if err != nil {
		return "", err
	}
	sc.Logf("%s", detail)
	childEnv, visible, err := apiInstanceEnviron(ctx, sc, l)
	if err != nil {
		return "", err
	}
	if err := checkAPIImagePaths(visible, l.Image.Binds); err != nil {
		return "", err
	}
	// The stores first, exactly as the worktree start waits for them.
	if err := awaitStores(ctx, sc, l.OwnStores, createReadyTimeout); err != nil {
		return "", err
	}
	if err := awaitStores(ctx, sc, l.SharedStores, sharedStoreWait); err != nil {
		return "", fmt.Errorf("%w (the ctl does not start this store: it belongs to another account, and "+
			"this wait is all it can do)", err)
	}
	// The NAME is the external id, recorded before the call that creates it,
	// with the stderr log's length beside it (errLogMark).
	if err := sc.Checkpoint("instance:"+name, errLogMark(ctx, sc, name)); err != nil {
		return "", err
	}
	if err := sc.Ops.Drivers.Instances().Run(ctx, jobs.InstanceSpec{
		Name: name, SIF: l.Image.SIF, Binds: append([]string(nil), l.Image.Binds...),
		Args: append([]string(nil), l.Image.Args...), ExtraEnv: childEnv, CleanEnv: true,
		Namespace: jobs.NamespaceCtl,
	}); err != nil {
		return "", err
	}
	return fmt.Sprintf("started %s from %s (log %s)", name, l.ServerImage.Name, l.LogPath), nil
}

// probeAPIImage proves the image file IS the row's server_image: the labels
// agree with what the row copied from the receipt, and the bytes hash to the
// recorded digest. A step-time probe — a plan never hashes or inspects.
func probeAPIImage(ctx context.Context, sc *jobs.StepContext, l apiLaunch) (string, error) {
	si := l.ServerImage
	labels, err := sc.Ops.Drivers.Instances().Labels(ctx, si.Path)
	if err != nil {
		return "", fmt.Errorf("reading the labels of %s: %w", si.Path, err)
	}
	if err := CheckImageLabels(labels, ImageReceipt{Name: si.Name, Version: si.Version, Commit: si.Commit,
		Build: receiptNumber(si.Build), SHA256: si.SHA256}); err != nil {
		return "", fmt.Errorf("%s (server_image %s): %w", si.Path, si.Name, err)
	}
	sum, _, err := sc.Ops.Drivers.Files().Sha256(ctx, si.Path)
	if err != nil {
		return "", fmt.Errorf("%w: hashing %s: %v", jobs.ErrRefused, si.Path, err)
	}
	if sum != si.SHA256 {
		return "", fmt.Errorf("%w: %s hashes to %s, but the row's server_image %s records %s — the file was "+
			"rebuilt, truncated or swapped since it was prepared", jobs.ErrRefused, si.Path, sum, si.Name, si.SHA256)
	}
	return fmt.Sprintf("%s: labels agree with %s (version %s, commit %s, build %d) and sha256 %s matches",
		si.Path, si.Name, si.Version, si.Commit, si.Build, sum), nil
}

// apiInstanceEnviron reads tenant.env and secrets.env at RUN time, refuses a
// key that would hijack the container, lays the unit environment over them,
// and returns (1) the child's environment — every key as APPTAINERENV_<KEY> —
// and (2) the same environment unprefixed, which the path probe reads. Neither
// is logged, checkpointed or previewed; errors name keys and files, never
// values.
func apiInstanceEnviron(ctx context.Context, sc *jobs.StepContext, l apiLaunch) (map[string]string,
	map[string]string, error) {
	merged := map[string]string{}
	for _, path := range []string{l.TenantEnv, l.SecretsEnv} {
		vals, err := readEnvFile(ctx, sc, path)
		if err != nil {
			return nil, nil, err
		}
		var bad []string
		for k, v := range vals {
			if render.APIImageDroppedEnvKey(k) {
				continue
			}
			if render.APIImageForbiddenEnvKey(k) {
				bad = append(bad, k)
				continue
			}
			merged[k] = v
		}
		if len(bad) > 0 {
			sort.Strings(bad)
			return nil, nil, fmt.Errorf("%w: %s defines %s, which would hijack the server image's container; "+
				"remove it before the API runs from an image", jobs.ErrRefused, path, strings.Join(bad, ", "))
		}
	}
	for _, kv := range l.UnitEnv {
		if i := strings.Index(kv, "="); i > 0 {
			merged[kv[:i]] = kv[i+1:]
		}
	}
	child := make(map[string]string, len(merged))
	for k, v := range merged {
		child["APPTAINERENV_"+k] = v
	}
	sc.Logf("the API instance's environment is %d variables from tenant.env, secrets.env and the unit environment, "+
		"passed as APPTAINERENV_<KEY>", len(child))
	return child, merged, nil
}

// checkAPIImagePaths is the step-time path probe: every path-valued setting the
// API will see is absolute and inside one of the plan's binds, every GOWE_IMAGE_DIRS
// entry too, and every sqlite store the API writes resolves to an absolute path
// inside a READ-WRITE bind. A relative path inside the container resolves
// against /opt/ragstack, the read-only image root.
func checkAPIImagePaths(env map[string]string, binds []string) error {
	var bad []string
	need := func(key, path string, rw bool) {
		if !filepath.IsAbs(path) {
			bad = append(bad, fmt.Sprintf("%s=%q is relative (it would resolve inside the read-only image)", key, path))
			return
		}
		_, bindRW, ok := render.APIImageBindFor(binds, path)
		switch {
		case !ok:
			bad = append(bad, fmt.Sprintf("%s=%s is outside every bind of this plan", key, path))
		case rw && !bindRW:
			bad = append(bad, fmt.Sprintf("%s=%s is a sqlite store inside a read-only bind", key, path))
		}
	}
	for _, k := range render.APIImagePathKeys {
		if v := strings.TrimSpace(env[k]); v != "" {
			need(k, v, false)
		}
	}
	for _, dir := range render.SplitImageDirs(env["GOWE_IMAGE_DIRS"]) {
		need("GOWE_IMAGE_DIRS", dir, false)
	}
	for _, s := range render.APIImageSQLiteStores {
		if strings.TrimSpace(env[s.Backend]) != "sqlite" {
			continue
		}
		path := strings.TrimSpace(env[s.Path])
		if path == "" {
			path = s.Default
		}
		need(s.Path+" (sqlite)", path, true)
	}
	if len(bad) == 0 {
		return nil
	}
	sort.Strings(bad)
	return fmt.Errorf("%w: the API instance would not see its own files: %s. Set the paths in tenant.env to "+
		"absolute paths under the data dir (or another bound path) and plan again", jobs.ErrRefused,
		strings.Join(bad, "; "))
}

// stopAPIInstance is the image-mode stop: `instance stop`, then proof the port
// is free. The instance's presence is checkpointed BEFORE the stop
// (was-running:<name>), which is what the rollback reads.
func stopAPIInstance(ctx context.Context, sc *jobs.StepContext, l apiLaunch) (string, error) {
	name, port, proc := l.Instance, l.Port, sc.Ops.Drivers.Proc()
	in, listed, err := instanceIn(ctx, sc, name, jobs.NamespaceCtl)
	if err != nil {
		return "", err
	}
	if !listed {
		listening, err := proc.Listening(ctx, port)
		if err != nil {
			return "", err
		}
		if listening {
			return "", fmt.Errorf("%w: the API instance %s is not running but something listens on %d; the ctl "+
				"will not report a stop it did not make. Find what holds the port", jobs.ErrRefused, name, port)
		}
		sc.Logf("the API instance %s is not running: nothing to stop", name)
		return name + " is not running: nothing to stop", nil
	}
	if in.PID > 1 {
		if owns, err := proc.InstanceOwnsPort(ctx, in.PID, port); err != nil {
			return "", err
		} else if !owns {
			if held, _ := proc.Listening(ctx, port); held {
				sc.Logf("WARNING: the instance %s (pid %d) does not hold %d — another process does; the instance is "+
					"stopped, and the port proof below decides", name, in.PID, port)
			}
		}
	}
	if err := sc.Checkpoint("instance:"+name, wasRunningID+name); err != nil {
		return "", err
	}
	if err := sc.Ops.Drivers.Instances().Stop(ctx, name, jobs.StopOptions{Namespace: jobs.NamespaceCtl}); err != nil {
		return "", err
	}
	deadline := time.Now().Add(apiStopTimeout)
	for {
		listening, err := proc.Listening(ctx, port)
		if err != nil {
			return "", err
		}
		if !listening {
			break
		}
		if time.Now().After(deadline) {
			return "", fmt.Errorf("%w: %s is stopped but %d is still held %s later — by a process that is not the "+
				"instance's", jobs.ErrRefused, name, port, apiStopTimeout)
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(apiStopPoll):
		}
	}
	return fmt.Sprintf("stopped %s (pid %d); %d is free", name, in.PID, port), nil
}

// envKeys is the KEY half of K=V entries, for a warning that names the unit
// environment without its values (they are public, but the step's warning is
// about which variables, not what they say).
func envKeys(kvs []string) []string {
	out := make([]string, 0, len(kvs))
	for _, kv := range kvs {
		if i := strings.Index(kv, "="); i > 0 {
			out = append(out, kv[:i])
		}
	}
	return out
}

// ---------------------------------------------------------------- readiness

// errAPINotComing marks a readiness answer that waiting cannot change: the API
// instance is gone (its uvicorn exited, typically because it could not bind),
// or the port is held by a process this account CAN attribute that does not
// descend from the instance. The readiness loops stop at it instead of serving
// out the bound.
var errAPINotComing = errors.New("the API is not coming up")

// apiReadyTarget is the identity a readiness wait needs: the mode, the
// instance name (image mode) and the port. Built from a launch or a row.
func apiReadyTarget(t *registry.Tenant, port int) apiLaunch {
	l := apiLaunch{Mode: apiModeWorktree, Port: port}
	if t != nil && t.ImageMode() {
		l.Mode, l.Instance = apiModeImage, render.APIImageInstanceName(t)
	}
	return l
}

// apiReadyProbe is ONE readiness question about the API: nil when it is up.
//
// Worktree mode: something listens on the port (unchanged — the pidfile
// identity is the start's business). Image mode: something listens AND the
// instance is in the ctl's table AND the listener descends from it. A listener
// alone proves nothing there: a foreign process on the API port makes the
// instance's uvicorn fail to bind, the instance exits, and the foreign process
// goes on answering the port.
func apiReadyProbe(ctx context.Context, sc *jobs.StepContext, l apiLaunch) error {
	proc := sc.Ops.Drivers.Proc()
	listening, err := proc.Listening(ctx, l.Port)
	if err != nil {
		return err
	}
	if l.Mode != apiModeImage {
		if !listening {
			return fmt.Errorf("no listener on %d", l.Port)
		}
		return nil
	}
	in, listed, err := instanceIn(ctx, sc, l.Instance, jobs.NamespaceCtl)
	if err != nil {
		return err
	}
	if !listed {
		reason, _, _ := instanceGoneReason(ctx, sc, l.Instance)
		if listening {
			owner, _, _ := proc.Owner(ctx, l.Port)
			return fmt.Errorf("%w: instance gone — %s; %d is held by %s, which is not the API", errAPINotComing,
				reason, l.Port, describePid(owner))
		}
		return fmt.Errorf("%w: instance gone — %s", errAPINotComing, reason)
	}
	if !listening {
		return fmt.Errorf("no listener on %d (the instance %s, pid %d, is running)", l.Port, l.Instance, in.PID)
	}
	owns := false
	if in.PID > 1 {
		if owns, err = proc.InstanceOwnsPort(ctx, in.PID, l.Port); err != nil {
			return err
		}
	}
	if owns {
		return nil
	}
	owner, _, err := proc.Owner(ctx, l.Port)
	if err != nil {
		return err
	}
	err = fmt.Errorf("port %d held by %s which is not the instance %s (pid %d)", l.Port, describePid(owner),
		l.Instance, in.PID)
	if owner > 0 {
		// An attributable holder outside the instance's tree will not become
		// the instance's by waiting.
		return fmt.Errorf("%w: %v", errAPINotComing, err)
	}
	return err
}

func describePid(pid int) string {
	if pid > 0 {
		return fmt.Sprintf("pid %d", pid)
	}
	return "a process this account cannot attribute"
}

// awaitAPIReady is the API's readiness after a start: apiReadyProbe polled
// under the readiness bound, stopping early on an answer waiting cannot
// change. In worktree mode it is exactly awaitListening's question.
func awaitAPIReady(ctx context.Context, sc *jobs.StepContext, l apiLaunch) (string, error) {
	deadline := time.Now().Add(createReadyTimeout)
	for {
		err := apiReadyProbe(ctx, sc, l)
		if err == nil {
			if l.Mode == apiModeImage {
				return fmt.Sprintf("port %d is listening and held by the instance %s", l.Port, l.Instance), nil
			}
			return fmt.Sprintf("port %d is listening", l.Port), nil
		}
		if errors.Is(err, errAPINotComing) {
			return "", fmt.Errorf("the API did not come up: %w", err)
		}
		if time.Now().After(deadline) {
			return "", fmt.Errorf("the API did not come up within %s: %w", createReadyTimeout, err)
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(createReadyPoll):
		}
	}
}

// rollbackAPIInstanceStart undoes an image-mode START: the instance this job
// ran is stopped if it is still there. Unlike a stop, it does not demand the
// port be free — a start that failed because something ELSE holds the port
// leaves that process alone, and "our instance is not running" is the whole
// undo.
func rollbackAPIInstanceStart(ctx context.Context, sc *jobs.StepContext, l apiLaunch) (string, error) {
	_, listed, err := instanceIn(ctx, sc, l.Instance, jobs.NamespaceCtl)
	if err != nil {
		return "", err
	}
	if !listed {
		return l.Instance + " is not running: nothing to undo", nil
	}
	if err := sc.Ops.Drivers.Instances().Stop(ctx, l.Instance, jobs.StopOptions{Namespace: jobs.NamespaceCtl}); err != nil {
		return "", err
	}
	return "stopped " + l.Instance, nil
}
