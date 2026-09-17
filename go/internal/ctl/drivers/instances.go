package drivers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/user"
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
	// Env is apptainer's OWN environment for a call in jobs.NamespaceCtl:
	// APPTAINER_CACHEDIR and APPTAINER_CONFIGDIR under the ctl's state dir.
	// The instance registry lives under the config dir, so a `list` or a
	// `stop` made without it looks in $HOME/.apptainer and sees NOTHING —
	// which is how coconut's first instance-mode selftests left every
	// sandbox store running: Run set the directories for the start, Stop and
	// List ran without them, reported "absent", and three sandboxes' worth
	// of qdrant and elasticsearch kept the block's ports for the next run.
	Env []string
	// AccountEnv is apptainer's own environment for a call in
	// jobs.NamespaceAccountDefault: the ctl's CACHEDIR (a cache is a cache,
	// and the ctl's is the one every account running this binary can write)
	// and NO CONFIGDIR AT ALL, so apptainer falls back to `$HOME/.apptainer` —
	// the registry a tenant somebody started BY HAND actually lives in, and
	// the one the handover's release half has to act in.
	//
	// The runner's environment allowlist (exec.go's envKeep) carries HOME and
	// deliberately does NOT carry APPTAINER_CONFIGDIR, so an operator whose
	// own shell exports one cannot redirect this call either: "the account's
	// default" means the account's default.
	AccountEnv []string
	// ConfigDir is the SAME directory Env's APPTAINER_CONFIGDIR names, as a
	// path. LogPaths needs it: apptainer keeps an instance's stdout and stderr
	// under `<configdir>/instances/logs/<host>/<user>/`, and the whole reason
	// to compose that path rather than read it out of the instance table is
	// that the instance a caller wants the log of is the one that is no longer
	// in the table.
	ConfigDir string
}

var _ jobs.Instances = (*RealInstances)(nil)

// env is a call's own additions (spec.ExtraEnv) followed by the driver's
// environment FOR THAT NAMESPACE: os/exec keeps the LAST value of a repeated
// key, so the driver's directories win and a spec cannot redirect apptainer's
// instance registry.
func (i *RealInstances) env(ns jobs.InstanceNamespace, extra []string) []string {
	out := append([]string(nil), extra...)
	if ns == jobs.NamespaceAccountDefault {
		return append(out, i.AccountEnv...)
	}
	return append(out, i.Env...)
}

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
// account in opts.Namespace.
//
// The result is sorted by name. apptainer already sorts, and so does the fake,
// so sorting here is belt and braces for one reason: a caller that prints the
// list (`fleet status`, a job log) must not produce output that changes order
// between runs on a build of apptainer that stops sorting.
func (i *RealInstances) List(ctx context.Context, opts jobs.ListOptions) ([]jobs.Instance, error) {
	stdout, _, err := i.run.Run(ctx, Spec{
		Program:  i.Bin,
		Args:     []string{"instance", "list", "--json"},
		ExtraEnv: i.env(opts.Namespace, nil),
		Timeout:  instanceListTimeout,
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
		// The two log files apptainer appends the instance's output to. They
		// are the container's own account of why it died, and an instance that
		// exits leaves nothing else behind.
		LogErrPath string `json:"logErrPath"`
		LogOutPath string `json:"logOutPath"`
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
		out = append(out, jobs.Instance{
			Name: in.Instance, PID: in.PID, Image: in.Img,
			LogOut: in.LogOutPath, LogErr: in.LogErrPath,
		})
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
	running, err := i.List(ctx, jobs.ListOptions{Namespace: spec.Namespace})
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
		ExtraEnv: i.env(spec.Namespace, childEnv),
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
	_, _, err = i.run.Run(ctx, Spec{Program: i.Bin, Args: argv,
		ExtraEnv: i.env(jobs.NamespaceCtl, nil), Timeout: instanceRunTimeout})
	return err
}

// LogPaths is where apptainer writes <name>'s stdout and stderr in
// opts.Namespace.
//
// A RUNNING instance answers for itself: the instance table carries
// logOutPath and logErrPath, and taking them from there means this driver
// cannot disagree with apptainer about where a live instance's output is
// going.
//
// An instance that is NOT in the table is the case this method exists for. A
// store that started and died — a postgres refusing a data directory it does
// not own — is out of the table within seconds, has no exit status anything
// here can read, and has exactly one artefact: a file under
// `<configdir>/instances/logs/<hostname>/<user>/<name>.err`. That layout is
// apptainer's (verified on 1.5.3 on coconut: wilke's hand-started instances
// are under `$HOME/.apptainer/instances/logs/coconut/wilke/`), and composing
// it is the only way to answer at all.
//
// It answers PATHS, not content, and never fails because a path does not
// exist: "there is no log" is the caller's to report, and the caller is the
// one holding the redactor.
func (i *RealInstances) LogPaths(ctx context.Context, name string, opts jobs.ListOptions) (string, string, error) {
	if err := checkInstanceName(name); err != nil {
		return "", "", err
	}
	// The table first — but a List that FAILS must not lose the composed
	// answer: the reason a caller is here is usually that something is wrong.
	if list, err := i.List(ctx, opts); err == nil {
		for _, in := range list {
			if in.Name == name && (in.LogOut != "" || in.LogErr != "") {
				return in.LogOut, in.LogErr, nil
			}
		}
	}
	base, err := i.logDir(opts.Namespace)
	if err != nil {
		return "", "", err
	}
	return filepath.Join(base, name+".out"), filepath.Join(base, name+".err"), nil
}

// logDir composes `<configdir>/instances/logs/<hostname>/<user>` for a
// namespace. The account-default namespace has no configured directory by
// construction (that IS the namespace: apptainer with no APPTAINER_CONFIGDIR),
// so it resolves $HOME/.apptainer the way apptainer itself would.
func (i *RealInstances) logDir(ns jobs.InstanceNamespace) (string, error) {
	root := i.ConfigDir
	if ns == jobs.NamespaceAccountDefault {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("%w: this account has no home directory, so apptainer's default instance log "+
				"directory cannot be named: %v", jobs.ErrRefused, err)
		}
		root = filepath.Join(home, ".apptainer")
	}
	if root == "" {
		return "", fmt.Errorf("%w: this instance driver was built without an apptainer config directory, so it "+
			"cannot say where the instance logs are", jobs.ErrRefused)
	}
	host, err := os.Hostname()
	if err != nil {
		return "", fmt.Errorf("%w: this host has no name, and apptainer files its instance logs under one: %v",
			jobs.ErrRefused, err)
	}
	// apptainer uses the SHORT hostname, which is what os.Hostname answers on
	// coconut; a fully-qualified one is cut to its first label so a host whose
	// /etc/hostname carries a domain does not send this to a path apptainer
	// never writes.
	host, _, _ = strings.Cut(host, ".")
	u, err := user.Current()
	if err != nil {
		return "", fmt.Errorf("%w: this account cannot be named, and apptainer files its instance logs under one: %v",
			jobs.ErrRefused, err)
	}
	return filepath.Join(root, "instances", "logs", host, u.Username), nil
}

// Stop is `apptainer instance stop <name>` in opts.Namespace, and an instance
// that is not running THERE is SUCCESS.
//
// Two ways of establishing that, because one message is not a contract.
// apptainer 1.5.3 answers exit 1 with "no instance found" on stderr; a build
// that words it differently would turn every re-run of a stop — a rollback, a
// resumed job, `fleet stop --all` over a half-down fleet — into a failed job.
// So a failure whose text does not say "absent" is checked against List, and a
// name that is not there is a stop that has nothing left to do.
//
// "Not there" is a statement about ONE registry, and the namespace decides
// which. A caller that needs the PORT freed rather than the instance absent
// must not read this success as that proof (see jobs.Instances.Stop).
func (i *RealInstances) Stop(ctx context.Context, name string, opts jobs.StopOptions) error {
	// The allowlist applies to a stop more than to a start: this is the call
	// that takes something down, and the account runs instances this control
	// plane did not start.
	if err := checkInstanceName(name); err != nil {
		return err
	}
	_, _, err := i.run.Run(ctx, Spec{
		Program:  i.Bin,
		Args:     []string{"instance", "stop", name},
		ExtraEnv: i.env(opts.Namespace, nil),
		Timeout:  instanceStopTimeout,
	})
	if err == nil || isNoSuchInstance(err) {
		return nil
	}
	// The List fallback answers about THIS account's instances in the SAME
	// registry the stop acted in — any other one would answer a different
	// question from the one that just failed.
	running, lerr := i.List(ctx, jobs.ListOptions{Namespace: opts.Namespace})
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
