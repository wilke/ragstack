package drivers

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"syscall"

	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// RealOptions configure the real driver set: the gateway
// (internal/ctl/gateway), the filesystem, and the four host drivers that run a
// program — systemd, proc, git and build.
type RealOptions struct {
	Roots paths.Roots
	// Fleet loads the registry the gateway renders from. It is a FUNCTION,
	// not a value: a publish happens under the registry lock, long after the
	// driver set was built, and rendering a generation from a snapshot taken
	// before the lock is how a gateway comes to serve a fleet that no longer
	// exists.
	Fleet func() (*registry.Fleet, error)
	// By is the audit principal recorded in txn.json.
	By string
	// The gateway knobs cmd/ragstack-ctl/gateway.go exposes as flags. Empty
	// values take the gateway package's defaults.
	BaseURL   string
	PIDFile   string
	NginxSIF  string
	Apptainer string
	// The host programs the PR-D drivers run: absolute paths, all overridable
	// from ctl.env (CTL_SYSTEMCTL_BIN and friends). They are configuration
	// rather than constants because coconut's node is not where a packaged one
	// would be (plan "Host facts"), and because a test points them at stubs.
	// Empty takes the default named beside each.
	SystemctlBin string // /usr/bin/systemctl
	GitBin       string // /usr/bin/git
	NodeBin      string // /rag/tools/node/current/bin/node
	NpmBin       string // /rag/tools/node/current/bin/npm
	// Mirror is the bare repository artifacts are prepared from. Default
	// <RagRoot>/repos/ragstack.git. The ctl never creates it — cloning the
	// mirror is an operator's deploy-time act.
	Mirror string
	// NpmCache is the npm cache `fleet artifact prepare` installs through.
	// Default <RagRoot>/cache/npm.
	NpmCache string
	// Redact is applied to a program's captured stderr before it reaches an
	// error, a job log or an audit row. Nil is the identity function; the
	// daemon passes the engine's redactor.
	Redact func(string) string
	// Logger records each host program run at debug level (program, argv,
	// duration — never the output). Nil discards.
	Logger *slog.Logger
	// ApprovedRoots are the only directories the Files driver writes under.
	// Empty means the two roots every op needs — the tenant data tree and the
	// ctl config tree — never "everything".
	ApprovedRoots []string
}

// Default program paths. They are the ones ops/ansible installs and the ones
// the plan names; a host that puts them elsewhere says so in ctl.env.
const (
	defaultSystemctlBin = "/usr/bin/systemctl"
	defaultGitBin       = "/usr/bin/git"
	defaultNodeBin      = "/rag/tools/node/current/bin/node"
	defaultNpmBin       = "/rag/tools/node/current/bin/npm"
)

// Real is the real driver set: the gateway, the filesystem, the four host
// drivers PR-D wires (systemd, proc, git, build), and an honest refusal for
// the store drivers that are still landing.
type Real struct {
	opts    RealOptions
	gateway *RealGateway
	files   *RealFiles
	systemd *RealSystemd
	proc    *RealProc
	git     *RealGit
	build   *RealBuild
}

var _ jobs.Drivers = (*Real)(nil)

// NewReal builds the real driver set.
func NewReal(o RealOptions) *Real {
	if len(o.ApprovedRoots) == 0 {
		o.ApprovedRoots = []string{o.Roots.DataDir, o.Roots.CtlConfigDir, o.Roots.CtlStateDir, o.Roots.BackupsDir}
	}
	o.SystemctlBin = orDefault(o.SystemctlBin, defaultSystemctlBin)
	o.GitBin = orDefault(o.GitBin, defaultGitBin)
	o.NodeBin = orDefault(o.NodeBin, defaultNodeBin)
	o.NpmBin = orDefault(o.NpmBin, defaultNpmBin)
	o.Mirror = orDefault(o.Mirror, filepath.Join(o.Roots.RagRoot, "repos", "ragstack.git"))
	o.NpmCache = orDefault(o.NpmCache, filepath.Join(o.Roots.RagRoot, "cache", "npm"))

	// ONE runner behind every host driver: the env sanitizer, the redactor
	// and the process-group kill are rules that hold for all of them, and a
	// driver with its own exec.Cmd would be a place they could stop holding.
	run := &runner{Redact: o.Redact, Logger: o.Logger}
	roots := append([]string(nil), o.ApprovedRoots...)
	return &Real{
		opts:    o,
		gateway: &RealGateway{opts: o},
		files:   &RealFiles{Roots: roots},
		systemd: &RealSystemd{run: run, Bin: o.SystemctlBin},
		// The listener table comes from hostfacts, so this driver and
		// `doctor` answer a port question from the same parser.
		proc:  &RealProc{Listeners: hostfacts.NewReal(o.Roots).Listeners, ProcRoot: func() string { return "/proc" }},
		git:   &RealGit{run: run, Bin: o.GitBin, Roots: roots},
		build: &RealBuild{run: run, Node: o.NodeBin, Npm: o.NpmBin, Roots: roots},
	}
}

// orDefault is the empty-means-default rule every program path follows.
func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}

// Mirror is the bare repository this driver set prepares artifacts from, as
// NewReal resolved it. `fleet artifact prepare` reads it rather than
// recomputing the default beside its own flag.
func (r *Real) Mirror() string { return r.opts.Mirror }

// NpmCache is the npm cache directory, as NewReal resolved it.
func (r *Real) NpmCache() string { return r.opts.NpmCache }

func (r *Real) Systemd() jobs.Systemd             { return r.systemd }
func (r *Real) Proc() jobs.Proc                   { return r.proc }
func (r *Real) Gateway() jobs.GatewayDriver       { return r.gateway }
func (r *Real) Files() jobs.Files                 { return r.files }
func (r *Real) Qdrant() jobs.Qdrant               { return pendingQdrant{} }
func (r *Real) Elasticsearch() jobs.Elasticsearch { return pendingES{} }
func (r *Real) TenantAPI() jobs.TenantAPI         { return pendingTenantAPI{} }
func (r *Real) Git() jobs.Git                     { return r.git }
func (r *Real) Build() jobs.Build                 { return r.build }
func (r *Real) Postgres() jobs.Postgres           { return pendingPostgres{} }
func (r *Real) SQLite() jobs.SQLite               { return pendingSQLite{} }
func (r *Real) Archive() jobs.Archive             { return pendingArchive{} }

// ---------------------------------------------------------------- gateway

// RealGateway is internal/ctl/gateway behind the jobs.GatewayDriver seam.
type RealGateway struct{ opts RealOptions }

// options builds gateway.Options the way cmd/ragstack-ctl/gateway.go does.
func (g *RealGateway) options(dryRun bool) gateway.Options {
	return gateway.Options{
		Roots:     g.opts.Roots,
		Exec:      gateway.NewRealExec(),
		Sig:       gateway.NewRealSignaller(),
		Prober:    gateway.NewRealProber(),
		By:        g.opts.By,
		BaseURL:   g.opts.BaseURL,
		PIDFile:   g.opts.PIDFile,
		NginxSIF:  g.opts.NginxSIF,
		Apptainer: g.opts.Apptainer,
		DryRun:    dryRun,
	}
}

func (g *RealGateway) fleet() (*registry.Fleet, error) {
	if g.opts.Fleet == nil {
		return nil, fmt.Errorf("%w: no registry loader is configured for the gateway driver", jobs.ErrRefused)
	}
	return g.opts.Fleet()
}

// Apply publishes the next generation.
func (g *RealGateway) Apply(ctx context.Context, dryRun bool) (int, string, error) {
	f, err := g.fleet()
	if err != nil {
		return 0, "", err
	}
	res, err := gateway.Publish(ctx, f, g.options(dryRun))
	return describe(res, err)
}

// Reload tests, HUPs, confirms and probes the LIVE tree — no new generation.
func (g *RealGateway) Reload(ctx context.Context, dryRun bool) (string, error) {
	res, err := gateway.Reload(ctx, g.options(dryRun))
	_, detail, err := describe(res, err)
	return detail, err
}

// Rollback returns to a previously verified generation.
func (g *RealGateway) Rollback(ctx context.Context, to int) (string, error) {
	f, err := g.fleet()
	if err != nil {
		return "", err
	}
	res, err := gateway.Rollback(ctx, f, to, g.options(false))
	_, detail, err := describe(res, err)
	return detail, err
}

// describe renders a gateway result as (generation, one-line detail) and
// passes the error through with gateway.ErrRefused translated to
// jobs.ErrRefused, so the API layer answers 409 `refused` for a gateway
// refusal exactly as it does for an op's.
func describe(res *gateway.Result, err error) (int, string, error) {
	gen, detail := 0, ""
	if res != nil {
		gen = res.Generation
		detail = res.State
		if n := len(res.Steps); n > 0 {
			last := res.Steps[n-1]
			detail = fmt.Sprintf("%s: %s (%s)", res.State, last.Name, last.Detail)
		}
	}
	if err != nil && errors.Is(err, gateway.ErrRefused) {
		err = fmt.Errorf("%w: %s", jobs.ErrRefused, err.Error())
	}
	return gen, detail, err
}

// ---------------------------------------------------------------- files

// RealFiles is the atomic-write surface, with the plan's filesystem-safety
// rules in one place:
//
//   - every path is checked against the approved roots BEFORE it is opened,
//     and the check is paths.SafePath (absolute, clean, safe charset, strictly
//     under the root), not a string prefix;
//   - the MODE is set on the temporary file before the rename, never after,
//     so no window exists in which the final path is readable by more accounts
//     than it should ever have been;
//   - the rename is within the same directory, so it is atomic;
//   - Remove opens with O_NOFOLLOW, so a symlink planted at a path the ctl is
//     about to delete deletes the link and never its target.
type RealFiles struct {
	Roots []string

	once     sync.Once
	approved []string // Roots, plus each root as EvalSymlinks resolves it
}

// check resolves path and returns the RESOLVED path the caller must operate
// on.
//
// paths.SafePath is lexical: it compares cleaned strings, so
// `<root>/link/../../etc/passwd` is refused but `<root>/link/passwd`, where
// `link` is a symlink to /etc, is not — nothing in the string says the
// component is a link. Containment that a single symlinked directory defeats
// is not containment, so the parent directory is resolved through
// filepath.EvalSymlinks FIRST and the check is made on what came back. The
// caller then opens THAT path, not the one it was given, so the check and the
// syscall cannot be made to disagree by a link planted between them.
//
// The approved roots are resolved the same way and both spellings accepted: a
// deployment whose /rag/data is itself a symlink is a normal host, not an
// escape.
func (f *RealFiles) check(path string) (string, error) {
	if _, err := paths.SafePath("/", path); err != nil {
		return "", fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	dir, err := resolveDir(filepath.Dir(path))
	if err != nil {
		return "", fmt.Errorf("%w: resolving the parent of %s: %v", jobs.ErrRefused, path, err)
	}
	resolved := filepath.Join(dir, filepath.Base(path))
	if !contained(resolved, f.roots()) {
		if resolved != path {
			return "", fmt.Errorf("%w: %s resolves to %s, which is outside every approved root %v",
				jobs.ErrRefused, path, resolved, f.Roots)
		}
		return "", outsideRoots(path, f.Roots)
	}
	return resolved, nil
}

// roots is Roots plus the symlink-resolved spelling of each.
func (f *RealFiles) roots() []string {
	f.once.Do(func() {
		f.approved = append([]string(nil), f.Roots...)
		for _, r := range f.Roots {
			if real, err := resolveDir(r); err == nil && real != r {
				f.approved = append(f.approved, real)
			}
		}
	})
	return f.approved
}

// resolveDir is filepath.EvalSymlinks for a directory that may not exist yet.
// A component that does not exist cannot be a symlink, so the deepest EXISTING
// ancestor is resolved and the missing tail appended unchanged.
func resolveDir(dir string) (string, error) {
	rest := ""
	for {
		real, err := filepath.EvalSymlinks(dir)
		if err == nil {
			if rest == "" {
				return real, nil
			}
			return filepath.Join(real, rest), nil
		}
		if !errors.Is(err, os.ErrNotExist) {
			return "", err
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", err
		}
		rest = filepath.Join(filepath.Base(dir), rest)
		dir = parent
	}
}

// WriteAtomic writes data to path via a temporary file in the same directory
// whose mode is set before the rename.
func (f *RealFiles) WriteAtomic(_ context.Context, path string, data []byte, mode uint32) (err error) {
	path, err = f.check(path)
	if err != nil {
		return err
	}
	// The RESOLVED directory: the temporary file and the rename target have to
	// be the same directory the check approved, or the two are different
	// places whenever a component is a link.
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer func() {
		if err != nil {
			_ = os.Remove(name)
		}
	}()
	if _, err = tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	// Mode BEFORE the rename: CreateTemp makes 0600, and a chmod after the
	// rename would leave the final path at the wrong mode for as long as the
	// two syscalls are apart — on a secrets file, that window is the bug.
	if err = tmp.Chmod(os.FileMode(mode)); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	// fsync the directory so the rename itself survives a crash.
	d, derr := os.Open(dir)
	if derr == nil {
		_ = d.Sync()
		_ = d.Close()
	}
	return nil
}

// MkdirAll creates path and every missing parent under it, and is REAL: it is
// how `tenant create` lays down a tenant tree.
//
// Two rules make it different from a bare os.MkdirAll:
//
//   - it never chmods a directory that already existed. /rag/data/tenants is
//     wilke 755 and its group, `cels`, has 1869 members; a driver that
//     "corrected" the mode of a parent it did not create would be silently
//     re-permissioning a directory shared with the whole host. Only the leaf,
//     and only when this call is the one that made it, is chmodded.
//   - the mode is applied with an explicit chmod rather than left to mkdir.
//     mkdir(2) masks the mode with the process umask and does not reliably
//     keep the setgid bit, and setgid is the whole point of a 2770 tenant
//     tree: it is what makes every file the tenant later writes inherit the
//     group instead of the writer's primary one.
func (f *RealFiles) MkdirAll(_ context.Context, path string, mode uint32) error {
	resolved, err := f.check(path)
	if err != nil {
		return err
	}
	// Lstat, not Stat: a symlink sitting where the directory should be is not
	// a directory this driver will write through, whatever it points at.
	if st, lerr := os.Lstat(resolved); lerr == nil {
		if !st.IsDir() {
			return fmt.Errorf("%w: %s already exists and is not a directory", jobs.ErrRefused, resolved)
		}
		return nil
	} else if !errors.Is(lerr, os.ErrNotExist) {
		return lerr
	}
	perm := fileMode(mode)
	if err := os.MkdirAll(resolved, perm); err != nil {
		return err
	}
	return os.Chmod(resolved, perm)
}

// fileMode turns a POSIX mode as the callers write it (0o2770) into the
// os.FileMode Go wants, where setuid/setgid/sticky are flag bits outside the
// low nine rather than the octal digits they are in a shell.
func fileMode(mode uint32) os.FileMode {
	perm := os.FileMode(mode & 0o777)
	if mode&syscall.S_ISUID != 0 {
		perm |= os.ModeSetuid
	}
	if mode&syscall.S_ISGID != 0 {
		perm |= os.ModeSetgid
	}
	if mode&syscall.S_ISVTX != 0 {
		perm |= os.ModeSticky
	}
	return perm
}

// Rename moves from to to; both must be under an approved root.
func (f *RealFiles) Rename(_ context.Context, from, to string) error {
	rfrom, err := f.check(from)
	if err != nil {
		return err
	}
	rto, err := f.check(to)
	if err != nil {
		return err
	}
	return os.Rename(rfrom, rto)
}

// Remove deletes path, refusing to follow a symlink to get there.
func (f *RealFiles) Remove(_ context.Context, path string) error {
	path, err := f.check(path)
	if err != nil {
		return err
	}
	// O_NOFOLLOW is the check, and opening is how it is made: a lstat+unlink
	// pair can be raced, an open that refuses to follow cannot. ELOOP means
	// the path ITSELF is a symlink, which is exactly the case to refuse (the
	// DIRECTORY components are already resolved by check). A directory opens
	// fine read-only, so it falls through to os.Remove, which removes it when
	// it is empty and refuses when it is not — what a caller naming a
	// directory asked for either way.
	fd, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	switch {
	case err == nil:
		_ = fd.Close()
	case errors.Is(err, os.ErrNotExist):
		return nil // already gone; Remove is idempotent
	case errors.Is(err, syscall.ELOOP):
		return fmt.Errorf("%w: %s is a symlink; the ctl never deletes through one", jobs.ErrRefused, path)
	default:
		return err
	}
	return os.Remove(path)
}

// ReadFile reads path (no root check: reading is not a mutation, and doctor
// already reads outside the approved roots).
func (f *RealFiles) ReadFile(_ context.Context, path string) ([]byte, error) {
	return os.ReadFile(path)
}

// ---------------------------------------------------------------- PR-D

// The STORE drivers PR-D is still landing; the host drivers above are wired.
// Each method refuses with the driver and method named, so a job that reaches
// one stops with a sentence an operator can act on rather than with a
// nil-pointer panic. They are separate types rather than one because
// Qdrant.Snapshot and Elasticsearch.Snapshot are different methods with the
// same name.
type (
	pendingQdrant    struct{}
	pendingES        struct{}
	pendingTenantAPI struct{}
	pendingPostgres  struct{}
	pendingSQLite    struct{}
	pendingArchive   struct{}
)

var (
	_ jobs.Qdrant        = pendingQdrant{}
	_ jobs.Elasticsearch = pendingES{}
	_ jobs.TenantAPI     = pendingTenantAPI{}
	_ jobs.Postgres      = pendingPostgres{}
	_ jobs.SQLite        = pendingSQLite{}
	_ jobs.Archive       = pendingArchive{}
)

func (pendingQdrant) Collections(context.Context, string) ([]string, error) {
	return nil, pending(jobs.ErrRefused, "qdrant", "Collections")
}
func (pendingQdrant) Snapshot(context.Context, string, string) (string, error) {
	return "", pending(jobs.ErrRefused, "qdrant", "Snapshot")
}
func (pendingES) Indices(context.Context, string) ([]string, error) {
	return nil, pending(jobs.ErrRefused, "elasticsearch", "Indices")
}
func (pendingES) Snapshot(context.Context, string, string, string) error {
	return pending(jobs.ErrRefused, "elasticsearch", "Snapshot")
}
func (pendingQdrant) Ready(context.Context, string) error {
	return pending(jobs.ErrRefused, "qdrant", "Ready")
}
func (pendingQdrant) Count(context.Context, string, string) (int64, error) {
	return 0, pending(jobs.ErrRefused, "qdrant", "Count")
}
func (pendingQdrant) Recover(context.Context, string, string, string) error {
	return pending(jobs.ErrRefused, "qdrant", "Recover")
}
func (pendingQdrant) DeleteSnapshot(context.Context, string, string, string) error {
	return pending(jobs.ErrRefused, "qdrant", "DeleteSnapshot")
}
func (pendingES) Ready(context.Context, string) error {
	return pending(jobs.ErrRefused, "elasticsearch", "Ready")
}
func (pendingES) RegisterRepo(context.Context, string, string, string, bool) error {
	return pending(jobs.ErrRefused, "elasticsearch", "RegisterRepo")
}
func (pendingES) UnregisterRepo(context.Context, string, string) error {
	return pending(jobs.ErrRefused, "elasticsearch", "UnregisterRepo")
}
func (pendingES) Restore(context.Context, string, string, string, []string) error {
	return pending(jobs.ErrRefused, "elasticsearch", "Restore")
}
func (pendingES) Count(context.Context, string, string) (int64, error) {
	return 0, pending(jobs.ErrRefused, "elasticsearch", "Count")
}
func (pendingTenantAPI) Health(context.Context, string) error {
	return pending(jobs.ErrRefused, "tenantapi", "Health")
}
func (pendingTenantAPI) ServiceAccount(context.Context, string, string, string, string, string, string) error {
	return pending(jobs.ErrRefused, "tenantapi", "ServiceAccount")
}
func (pendingTenantAPI) Version(context.Context, string, string) (map[string]any, error) {
	return nil, pending(jobs.ErrRefused, "tenantapi", "Version")
}
func (pendingTenantAPI) DeepHealth(context.Context, string, string) error {
	return pending(jobs.ErrRefused, "tenantapi", "DeepHealth")
}
func (pendingTenantAPI) Collections(context.Context, string, string) ([]string, error) {
	return nil, pending(jobs.ErrRefused, "tenantapi", "Collections")
}
func (pendingPostgres) Ready(context.Context, jobs.PostgresSpec) error {
	return pending(jobs.ErrRefused, "postgres", "Ready")
}
func (pendingPostgres) Dump(context.Context, jobs.PostgresSpec, string) error {
	return pending(jobs.ErrRefused, "postgres", "Dump")
}
func (pendingPostgres) Restore(context.Context, jobs.PostgresSpec, string) error {
	return pending(jobs.ErrRefused, "postgres", "Restore")
}
func (pendingSQLite) Backup(context.Context, string, string) (string, error) {
	return "", pending(jobs.ErrRefused, "sqlite", "Backup")
}
func (pendingArchive) Create(context.Context, string, string) error {
	return pending(jobs.ErrRefused, "archive", "Create")
}
func (pendingArchive) Extract(context.Context, string, string, jobs.ArchiveLimits) error {
	return pending(jobs.ErrRefused, "archive", "Extract")
}
