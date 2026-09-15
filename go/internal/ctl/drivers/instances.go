package drivers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// RealInstances is `apptainer instance run|stop|list` over the shared runner:
// the store half of `supervisor: instance` (plan PR-D2).
//
// It runs the SAME program and the same argv the units' ExecStart names, with
// `instance run <name>` where the unit has `run`. That is what makes a store
// startable, findable and stoppable on a host with no user manager: an
// instance has a name, and a name is something a later job — or a `fleet stop
// --all` after a reboot — can act on without a pidfile the ctl would have to
// keep itself.
//
// Nothing here is idempotent by itself. Run REFUSES a name that is already up
// (apptainer refuses it too), because a start that silently succeeded against
// a running instance would report a tenant started from the artifact the job
// just checked out while the OLD process kept serving. The caller checks List
// first; Stop, whose goal is absence, is the one verb that treats "not there"
// as success.
type RealInstances struct {
	run *runner
	// Bin is apptainer, absolute. The instance driver runs the SAME program
	// the units' ExecStart names.
	Bin string
	// Roots bound the HOST side of every bind. A bind is a mount: the
	// container sees, and usually writes, whatever the host path resolves to,
	// so `--bind /etc:/qdrant/storage` out of a mangled registry row is not a
	// misconfiguration, it is a store with /etc in it.
	Roots []string
}

var _ jobs.Instances = (*RealInstances)(nil)

// The three timeouts, which differ by what the command actually waits for.
//
//   - run: apptainer mounts the image, sets up the namespaces and execs the
//     runscript, then returns — it does NOT wait for the service inside to be
//     ready (the readiness gate is the caller's). Two minutes is for a cold
//     SIF on a busy filesystem, not for elasticsearch's startup.
//   - stop: SIGTERM and wait. Elasticsearch flushes its translog here, which
//     is the whole reason the units carry TimeoutStopSec=120; this has to be
//     longer than that or the ctl would kill the store its unit was still
//     shutting down cleanly.
//   - list: reads a directory of instance files. A list that takes 30 seconds
//     is a host in trouble, not a slow command.
const (
	instanceRunTimeout  = 120 * time.Second
	instanceStopTimeout = 150 * time.Second
	instanceListTimeout = 30 * time.Second
)

// List is `apptainer instance list --json`, every instance of the CURRENT
// account.
//
// The result is sorted by name. apptainer already sorts, and so does the fake,
// so sorting here is belt and braces for one reason: a caller that prints the
// list (`fleet status`, a job log) must not produce output that changes order
// between runs on a build of apptainer that stops sorting.
func (i *RealInstances) List(ctx context.Context) ([]jobs.Instance, error) {
	stdout, _, err := i.run.Run(ctx, Spec{
		Program: i.Bin,
		Args:    []string{"instance", "list", "--json"},
		Timeout: instanceListTimeout,
	})
	if err != nil {
		return nil, err
	}
	return parseInstanceList(stdout)
}

// instanceListJSON is the document `apptainer instance list --json` prints,
// verified against apptainer 1.5.3 on coconut:
//
//	{"instances":[{"instance":"qdrant-dev","pid":189637,
//	  "img":"/rag/apptainer/images/qdrant.sif","ip":"",
//	  "logErrPath":"…","logOutPath":"…"}]}
//
// The field names are `instance`, `pid` and `img` — NOT `name` and `image`,
// which is what a reader guesses from the struct they fill. An instance table
// that silently decoded to empty names would make every `running` check answer
// "no" and every start a second copy, so instances_test.go pins this exact
// sample.
type instanceListJSON struct {
	Instances []struct {
		Instance string `json:"instance"`
		PID      int    `json:"pid"`
		Img      string `json:"img"`
	} `json:"instances"`
}

func parseInstanceList(stdout []byte) ([]jobs.Instance, error) {
	// No instances at all prints `{"instances":[]}` on this build, but a
	// build that printed nothing would be answering the same fact, and an
	// empty fleet is not an error.
	if len(strings.TrimSpace(string(stdout))) == 0 {
		return nil, nil
	}
	var doc instanceListJSON
	if err := json.Unmarshal(stdout, &doc); err != nil {
		return nil, fmt.Errorf("%w: apptainer instance list --json did not print the document this driver parses: %v",
			jobs.ErrRefused, err)
	}
	out := make([]jobs.Instance, 0, len(doc.Instances))
	for _, in := range doc.Instances {
		out = append(out, jobs.Instance{Name: in.Instance, PID: in.PID, Image: in.Img})
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Name < out[b].Name })
	return out, nil
}

// Run is
// `apptainer instance run --no-home <--bind …> <--env …> <sif> <name> <args…>`.
//
// spec.ExtraEnv goes into the CHILD's environment and onto no argv, which is
// how APPTAINERENV_POSTGRES_PASSWORD reaches the container (apptainer forwards
// APPTAINERENV_<KEY> as <KEY>) and how APPTAINER_CACHEDIR/CONFIGDIR reach
// apptainer itself. /proc/<pid>/cmdline is 0444 on a 1869-member host; that is
// the whole reason the two environments are separate types.
func (i *RealInstances) Run(ctx context.Context, spec jobs.InstanceSpec) error {
	if err := checkInstanceName(spec.Name); err != nil {
		return err
	}
	sif, err := checkSIF(spec.SIF)
	if err != nil {
		return err
	}
	binds, err := i.bindArgs(spec.Binds)
	if err != nil {
		return err
	}
	envArgs, err := envArgv(spec.Env)
	if err != nil {
		return err
	}
	// ExtraEnv is validated but never quoted back: its values are secrets.
	childEnv, err := envEntries(spec.ExtraEnv)
	if err != nil {
		return err
	}

	// The name check. apptainer makes it too — a second instance under a
	// taken name is an error, not a no-op — but doing it here means the
	// refusal is a jobs.ErrRefused with a sentence, rather than an exit status
	// and a line of apptainer's stderr, and it means the fake and the host
	// refuse the same way.
	running, err := i.List(ctx)
	if err != nil {
		return err
	}
	for _, in := range running {
		if in.Name == spec.Name {
			return fmt.Errorf("%w: instance %s is already running (pid %d, from %s)",
				jobs.ErrRefused, spec.Name, in.PID, in.Image)
		}
	}

	argv := make([]string, 0, 4+len(binds)+len(envArgs)+len(spec.Args))
	argv = append(argv, "instance", "run", "--no-home")
	argv = append(argv, binds...)
	argv = append(argv, envArgs...)
	argv = append(argv, sif, spec.Name)
	argv = append(argv, spec.Args...)
	_, _, err = i.run.Run(ctx, Spec{
		Program:  i.Bin,
		Args:     argv,
		ExtraEnv: childEnv,
		Timeout:  instanceRunTimeout,
	})
	return err
}

// SeedConfigDir is
// `apptainer exec --bind <hostDir>:/__seed <sif> cp -R <containerDir>/. /__seed/`
// — the argv `ragstack-ctl es-seed-config` runs from the ES unit's
// ExecStartPre, which the instance supervisor must run itself because it has
// no ExecStartPre. Unconditional: "only when the host directory is empty" is
// policy and lives in ops, so an operator's own elasticsearch.yml is never
// overwritten by THIS call being reached twice.
func (i *RealInstances) SeedConfigDir(ctx context.Context, sif, containerDir, hostDir string) error {
	sif, err := checkSIF(sif)
	if err != nil {
		return err
	}
	if !filepath.IsAbs(containerDir) || filepath.Clean(containerDir) != containerDir || containerDir == "/" {
		return fmt.Errorf("%w: the container config directory %q must be an absolute, clean path", jobs.ErrRefused, containerDir)
	}
	binds, err := i.bindArgs([]string{hostDir + ":/__seed"})
	if err != nil {
		return err
	}
	argv := append([]string{"exec"}, binds...)
	argv = append(argv, sif, "cp", "-R", containerDir+"/.", "/__seed/")
	_, _, err = i.run.Run(ctx, Spec{Program: i.Bin, Args: argv, Timeout: instanceRunTimeout})
	return err
}

// Stop is `apptainer instance stop <name>`, and an instance that is not
// running is SUCCESS.
//
// Two ways of establishing that, because one message is not a contract.
// apptainer 1.5.3 answers exit 1 with "no instance found" on stderr; a build
// that words it differently would turn every re-run of a stop — a rollback, a
// resumed job, `fleet stop --all` over a half-down fleet — into a failed job.
// So a failure whose text does not say "absent" is checked against List, and a
// name that is not there is a stop that has nothing left to do.
func (i *RealInstances) Stop(ctx context.Context, name string) error {
	// The allowlist applies to a stop more than to a start: this is the call
	// that takes something down, and the account runs instances this control
	// plane did not start.
	if err := checkInstanceName(name); err != nil {
		return err
	}
	_, _, err := i.run.Run(ctx, Spec{
		Program: i.Bin,
		Args:    []string{"instance", "stop", name},
		Timeout: instanceStopTimeout,
	})
	if err == nil || isNoSuchInstance(err) {
		return nil
	}
	// The List fallback answers about THIS account's instances, which is what
	// the ctl manages and all it can stop.
	running, lerr := i.List(ctx)
	if lerr != nil {
		return err
	}
	for _, in := range running {
		if in.Name == name {
			return err
		}
	}
	return nil
}

// noSuchInstance are the ways apptainer says an instance is not there. The
// first is what 1.5.3 prints; the others are cheap insurance.
var noSuchInstance = []string{"no instance found", "no instances found", "does not exist"}

func isNoSuchInstance(err error) bool {
	var ee *ExecError
	if !errors.As(err, &ee) {
		return false
	}
	msg := strings.ToLower(ee.Stderr)
	for _, phrase := range noSuchInstance {
		if strings.Contains(msg, phrase) {
			return true
		}
	}
	return false
}

// checkSIF holds the image to "an absolute, clean path that is a file here".
//
// It is NOT held to the approved roots: the SIFs live in
// /rag/apptainer/images, which no op writes to and which is deliberately not
// on the write allowlist. Existence is checked before the run so that a
// missing image is a sentence naming the path rather than an apptainer exit
// status in the middle of a start.
func checkSIF(sif string) (string, error) {
	if sif == "" {
		return "", fmt.Errorf("%w: this instance has no image to run", jobs.ErrRefused)
	}
	if _, err := paths.SafePath("/", sif); err != nil {
		return "", fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	st, err := os.Stat(sif)
	if err != nil {
		return "", fmt.Errorf("%w: the image %s is not usable: %v", jobs.ErrRefused, sif, err)
	}
	if st.IsDir() {
		return "", fmt.Errorf("%w: the image %s is a directory", jobs.ErrRefused, sif)
	}
	return sif, nil
}

// bindOptions are the only third fields a bind may carry.
var bindOptions = map[string]bool{"ro": true, "rw": true}

// bindArgs renders the binds as `--bind <host>:<container>` and holds the host
// side inside the approved roots.
//
// The path that goes on the argv is the RESOLVED one, and a symlink AT the
// host path is refused outright rather than followed: a bind whose check and
// whose mount can be made to disagree by a link planted between them is the
// one containment bug that matters here, because what is on the other side of
// it is a store that writes.
func (i *RealInstances) bindArgs(binds []string) ([]string, error) {
	out := make([]string, 0, 2*len(binds))
	for _, b := range binds {
		parts := strings.Split(b, ":")
		if len(parts) < 2 || len(parts) > 3 {
			return nil, fmt.Errorf("%w: %q is not a bind this driver renders (want host:container[:ro|rw])",
				jobs.ErrRefused, b)
		}
		host, container := parts[0], parts[1]
		if len(parts) == 3 && !bindOptions[parts[2]] {
			return nil, fmt.Errorf("%w: %q is not a bind option this driver passes (ro or rw)", jobs.ErrRefused, parts[2])
		}
		if !filepath.IsAbs(container) || filepath.Clean(container) != container {
			return nil, fmt.Errorf("%w: the container path %q of bind %q must be absolute and clean",
				jobs.ErrRefused, container, b)
		}
		if !filepath.IsAbs(host) {
			return nil, fmt.Errorf("%w: the host path %q of bind %q must be absolute", jobs.ErrRefused, host, b)
		}
		resolved, err := resolvedContainedNoLeafLink(host, i.Roots)
		if err != nil {
			return nil, err
		}
		bind := resolved + ":" + container
		if len(parts) == 3 {
			bind += ":" + parts[2]
		}
		out = append(out, "--bind", bind)
	}
	return out, nil
}

// envArgv renders the CONTAINER environment as `--env K=V`, sorted by key.
//
// Sorted because InstanceSpec.Env is a map and a map has no order: a command
// line rendered from one would differ between two runs of the same start,
// which makes a job log and a `ps` line impossible to compare. The units'
// hand-written order (render.Store.EnvOrder) is therefore NOT what an instance
// shows; apptainer does not care, and the golden test that keeps the unit and
// the instance in step compares the argv the renderer produces, not this.
func envArgv(env map[string]string) ([]string, error) {
	entries, err := envEntries(env)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, 2*len(entries))
	for _, e := range entries {
		out = append(out, "--env", e)
	}
	return out, nil
}

// envVarRE is a POSIX environment-variable name. A key that is not one is
// refused rather than passed on: `--env` takes `K=V`, and a key containing an
// `=` or a space is an argument that means something else entirely.
var envVarRE = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// envEntries renders an environment map as sorted "K=V" entries.
//
// No VALUE ever appears in an error from here. The same function renders
// InstanceSpec.Env, which is public, and InstanceSpec.ExtraEnv, which carries
// the postgres password — one of the two is a secret, and the way that stays
// true under later edits is that neither is ever quoted.
func envEntries(env map[string]string) ([]string, error) {
	keys := make([]string, 0, len(env))
	for k := range env {
		if !envVarRE.MatchString(k) {
			return nil, fmt.Errorf("%w: %q is not an environment variable name", jobs.ErrRefused, k)
		}
		if strings.ContainsRune(env[k], 0) {
			return nil, fmt.Errorf("%w: the value of %s contains a NUL", jobs.ErrRefused, k)
		}
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]string, 0, len(keys))
	for _, k := range keys {
		out = append(out, k+"="+env[k])
	}
	return out, nil
}
