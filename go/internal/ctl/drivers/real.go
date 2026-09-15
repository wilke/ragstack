package drivers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
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
	// CrontabBin is crontab(1), the only boot hook a host whose service
	// account has no user manager has (PR-D2). ctl.env CTL_CRONTAB_BIN.
	CrontabBin string // /usr/bin/crontab
	// Mirror is the bare repository artifacts are prepared from. Default
	// <RagRoot>/repos/ragstack.git. The ctl never creates it — cloning the
	// mirror is an operator's deploy-time act.
	Mirror string
	// NpmCache is the npm cache `fleet artifact prepare` installs through.
	// Default <RagRoot>/cache/npm.
	NpmCache string
	// Redact is applied to a program's captured stderr and to everything a
	// store says back before it reaches an error, a job log or an audit row.
	// Nil is the identity function — which is why nothing in this package
	// DEPENDS on it for a secret the ctl itself holds: those are kept out of
	// the string instead. The daemon passes the engine's redactor.
	Redact func(string) string
	// Logger records each host program run at debug level (program, argv,
	// duration — never the output). Nil discards.
	Logger *slog.Logger
	// ApprovedRoots are the only directories the Files driver writes under.
	// Empty means the two roots every op needs — the tenant data tree and the
	// ctl config tree — never "everything".
	ApprovedRoots []string
	// StoreLongTimeout is the ceiling on a store call that can legitimately
	// take a long time (a snapshot, a restore, a recover, an exact count, a
	// pg_dump). Zero takes LongTimeout.
	StoreLongTimeout time.Duration
}

// Default program paths. They are the ones ops/ansible installs and the ones
// the plan names; a host that puts them elsewhere says so in ctl.env.
const (
	defaultSystemctlBin = "/usr/bin/systemctl"
	defaultGitBin       = "/usr/bin/git"
	defaultNodeBin      = "/rag/tools/node/current/bin/node"
	defaultNpmBin       = "/rag/tools/node/current/bin/npm"
)

// Real is the real driver set: the gateway and the filesystem (PR-C), the
// host drivers (systemd, proc, git, build) and the store drivers (qdrant,
// elasticsearch, tenant API, postgres, sqlite, archive) PR-D wired, and
// PR-D2's instances and crontab. Every driver in it runs: Pending() is empty.
type Real struct {
	opts    RealOptions
	gateway *RealGateway
	files   *RealFiles
	systemd *RealSystemd
	proc    *RealProc
	git     *RealGit
	build   *RealBuild
	qdrant  *RealQdrant
	es      *RealElasticsearch
	api     *RealTenantAPI
	pg      *RealPostgres
	sqlite  *RealSQLite
	archive *RealArchive
	// PR-D2: the store half and the boot hook of `supervisor: instance`.
	instances *RealInstances
	crontab   *RealCrontab
}

var _ jobs.Drivers = (*Real)(nil)

// NewReal builds the real driver set.
func NewReal(o RealOptions) *Real {
	if len(o.ApprovedRoots) == 0 {
		// The tenant worktrees root is on the list too: `tenant create`
		// checks a worktree out under it and `decommission` moves one aside.
		// It was missing on coconut's first selftest and the create refused
		// its own worktree path.
		o.ApprovedRoots = []string{o.Roots.DataDir, o.Roots.CtlConfigDir, o.Roots.CtlStateDir, o.Roots.BackupsDir, o.Roots.ReposDir}
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
	// One HTTP client for the three HTTP drivers, so the connection pool, the
	// redirect refusal and the timeouts are one decision rather than three.
	h := newHTTPStores(o)
	return &Real{
		opts:    o,
		gateway: &RealGateway{opts: o},
		files:   &RealFiles{Roots: roots},
		systemd: &RealSystemd{run: run, Bin: o.SystemctlBin},
		// The listener table comes from hostfacts, so this driver and
		// `doctor` answer a port question from the same parser; the roots
		// bound the pidfile and the log Spawn creates.
		proc: &RealProc{
			Listeners: hostfacts.NewReal(o.Roots).Listeners,
			ProcRoot:  func() string { return "/proc" },
			Roots:     roots,
		},
		git:     &RealGit{run: run, Bin: o.GitBin, Roots: roots},
		build:   &RealBuild{run: run, Node: o.NodeBin, Npm: o.NpmBin, Roots: roots},
		qdrant:  &RealQdrant{h: h},
		es:      &RealElasticsearch{h: h},
		api:     &RealTenantAPI{h: h},
		pg:      &RealPostgres{opts: o, run: run},
		sqlite:  &RealSQLite{opts: o},
		archive: &RealArchive{opts: o},
		// The instance driver binds host paths into a container, so it gets
		// the same approved roots the drivers that write under them do.
		instances: &RealInstances{run: run, Bin: orDefault(o.Apptainer, defaultApptainer), Roots: roots},
		crontab:   &RealCrontab{run: run, Bin: orDefault(o.CrontabBin, defaultCrontabBin)},
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
func (r *Real) Qdrant() jobs.Qdrant               { return r.qdrant }
func (r *Real) Elasticsearch() jobs.Elasticsearch { return r.es }
func (r *Real) TenantAPI() jobs.TenantAPI         { return r.api }
func (r *Real) Git() jobs.Git                     { return r.git }
func (r *Real) Build() jobs.Build                 { return r.build }
func (r *Real) Postgres() jobs.Postgres           { return r.pg }
func (r *Real) SQLite() jobs.SQLite               { return r.sqlite }
func (r *Real) Archive() jobs.Archive             { return r.archive }
func (r *Real) Instances() jobs.Instances         { return r.instances }
func (r *Real) Crontab() jobs.Crontab             { return r.crontab }

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
		// This driver runs only inside a job, and every op whose plan carries
		// a gateway step declares LockGateway (an ops test asserts it), so
		// the engine already holds the gateway package's lock file.
		LockHeld: true,
	}
}

// Routes reads the tenant list the live proxy tree serves.
func (g *RealGateway) Routes(_ context.Context) ([]string, error) {
	lists, err := doctor.LiveTenantLists(g.opts.Roots.ProxyDir)
	if err != nil {
		return nil, err
	}
	if lists.NamesJSON == "" {
		return nil, nil
	}
	var names []string
	if err := json.Unmarshal([]byte(lists.NamesJSON), &names); err != nil {
		return nil, fmt.Errorf("the live tenant list %s does not parse: %w", lists.NamesSource, err)
	}
	return names, nil
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

// check resolves path against this driver's approved roots (both spellings:
// a deployment whose /rag/data is itself a symlink is a normal host, not an
// escape) and returns the RESOLVED path the caller must operate on.
func (f *RealFiles) check(path string) (string, error) {
	return resolvedContained(path, f.roots())
}

// resolvedContained is the containment check every driver that writes,
// deletes or binds a host path uses, and the path it returns is the one the
// caller must then operate on.
//
// paths.SafePath is LEXICAL: it compares cleaned strings, so
// `<root>/link/../../etc/passwd` is refused but `<root>/link/passwd`, where
// `link` is a symlink to /etc, is not — nothing in the string says the
// component is a link. Five drivers used to check the path they were given
// and then operate on another (or on the same string, having resolved it only
// for a Stat), which is containment a single symlinked directory defeats. So
// the parent directory is resolved through filepath.EvalSymlinks FIRST, the
// check is made on what came back, and the RESOLVED path is what is returned:
// the check and the syscall cannot then be made to disagree by a link planted
// between them.
func resolvedContained(path string, roots []string) (string, error) {
	if _, err := paths.SafePath("/", path); err != nil {
		return "", fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	dir, err := resolveDir(filepath.Dir(path))
	if err != nil {
		return "", fmt.Errorf("%w: resolving the parent of %s: %v", jobs.ErrRefused, path, err)
	}
	resolved := filepath.Join(dir, filepath.Base(path))
	// Both spellings of every root are accepted: a deployment whose /rag/data
	// is itself reached through a symlink is a normal host, not an escape, and
	// resolving the PATH without resolving the roots would refuse it.
	if !contained(resolved, resolveRoots(roots)) {
		if resolved != path {
			return "", fmt.Errorf("%w: %s resolves to %s, which is outside every approved root %v",
				jobs.ErrRefused, path, resolved, roots)
		}
		return "", outsideRoots(path, roots)
	}
	return resolved, nil
}

// resolveRoots is roots plus the symlink-resolved spelling of each. A root
// that does not resolve (it is not there yet) is kept as written.
func resolveRoots(roots []string) []string {
	out := append([]string(nil), roots...)
	for _, r := range roots {
		if real, err := resolveDir(r); err == nil && real != r {
			out = append(out, real)
		}
	}
	return out
}

// resolvedContainedNoLeafLink is resolvedContained for a path whose LEAF is
// about to be written through or deleted: an extraction directory, a vite
// `--emptyOutDir`, a `git worktree remove --force`.
//
// Resolving the parent is not enough for those. `<root>/dist`, where `dist` is
// a symlink to /etc, is lexically inside the root AND its parent resolves
// inside the root — and `vite build --emptyOutDir` then empties /etc. A
// symlink at the leaf is therefore refused outright rather than followed:
// nothing the ctl creates is a symlink, so one sitting where a destructive
// operation is about to happen is not a layout, it is a plant.
func resolvedContainedNoLeafLink(path string, roots []string) (string, error) {
	resolved, err := resolvedContained(path, roots)
	if err != nil {
		return "", err
	}
	st, lerr := os.Lstat(resolved)
	switch {
	case lerr == nil && st.Mode()&os.ModeSymlink != 0:
		return "", fmt.Errorf("%w: %s is a symlink; the ctl never writes or deletes through one",
			jobs.ErrRefused, resolved)
	case lerr != nil && !errors.Is(lerr, os.ErrNotExist):
		return "", lerr
	}
	return resolved, nil
}

// roots is Roots plus the symlink-resolved spelling of each, resolved once.
func (f *RealFiles) roots() []string {
	f.once.Do(func() { f.approved = resolveRoots(f.Roots) })
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
	return writeAtomic(path, data, mode)
}

// writeAtomic is WriteAtomic's body without the containment check, for the one
// other place that writes a file the ctl's own bookkeeping depends on:
// proc.Spawn's pidfile, which is written after THAT driver has checked the
// path against its own roots. Two copies of "temporary file in the same
// directory, mode before the rename, fsync the directory afterwards" would be
// two places to get it wrong.
//
// path must already be resolved and approved by the caller.
func writeAtomic(path string, data []byte, mode uint32) (err error) {
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
	//
	// fileMode, not os.FileMode: a caller asking for 0o2770 means setgid, and
	// os.FileMode(0o2770) silently drops it (Go keeps setuid/setgid/sticky in
	// flag bits outside the low nine) — the same bug CopyFile already avoided.
	if err = tmp.Chmod(fileMode(mode)); err != nil {
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

// CopyFile streams src to dst, atomically and under the approved roots.
//
// It is WriteAtomic with an io.Copy where the []byte was: the same temporary
// file in the destination's own directory, the same mode set BEFORE the
// rename, the same directory fsync afterwards. What it does not do is hold the
// file in memory — a restore copies qdrant snapshots and elasticsearch
// segments, which are gigabytes each.
//
// The SOURCE is opened with O_NOFOLLOW. Reading is not root-checked here (it is
// not for ReadFile either), but a bundle is operator input that may have been
// copied in from another host, and a symlink inside one must not become a copy
// of whatever it points at inside a tenant's data directory.
func (f *RealFiles) CopyFile(_ context.Context, src, dst string, mode uint32) (err error) {
	in, err := os.OpenFile(src, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		if errors.Is(err, syscall.ELOOP) {
			return fmt.Errorf("%w: %s is a symlink; the ctl never copies through one", jobs.ErrRefused, src)
		}
		return err
	}
	defer in.Close()
	st, err := in.Stat()
	if err != nil {
		return err
	}
	if st.IsDir() {
		return fmt.Errorf("%w: %s is a directory, not a file to copy", jobs.ErrRefused, src)
	}

	dst, err = f.check(dst)
	if err != nil {
		return err
	}
	dir := filepath.Dir(dst)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(dst)+".tmp-")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer func() {
		if err != nil {
			_ = os.Remove(name)
		}
	}()
	if _, err = io.Copy(tmp, in); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Chmod(fileMode(mode)); err != nil {
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
	if err = os.Rename(name, dst); err != nil {
		return err
	}
	if d, derr := os.Open(dir); derr == nil {
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
//     re-permissioning a directory shared with the whole host. Only the
//     directories THIS call created are chmodded.
//   - the mode is applied with an explicit chmod rather than left to mkdir.
//     mkdir(2) masks the mode with the process umask and does not reliably
//     keep the setgid bit, and setgid is the whole point of a 2770 tenant
//     tree: it is what makes every file the tenant later writes inherit the
//     group instead of the writer's primary one.
//
// The second rule used to hold for the LEAF alone: os.MkdirAll created every
// intermediate level and only the leaf was chmodded, so `MkdirAll(<tenant>/a/b,
// 0o2770)` left `a` at 0750 under the ordinary 022 umask — no setgid, no group
// write. A tenant tree is created a level at a time by different steps, so the
// missing levels are real ones. Each component is therefore created here, one
// mkdir at a time, and chmodded only when THIS call is the one whose mkdir
// succeeded; a component another process won the race for is left exactly as
// an existing directory would be.
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
	// The missing components, deepest first, down to the first existing
	// ancestor.
	var missing []string
	for p := resolved; ; {
		if _, lerr := os.Lstat(p); lerr == nil {
			break
		} else if !errors.Is(lerr, os.ErrNotExist) {
			return lerr
		}
		missing = append(missing, p)
		parent := filepath.Dir(p)
		if parent == p {
			break // reached the filesystem root without finding one
		}
		p = parent
	}
	perm := fileMode(mode)
	for i := len(missing) - 1; i >= 0; i-- {
		dir := missing[i]
		if err := os.Mkdir(dir, perm); err != nil {
			if errors.Is(err, os.ErrExist) {
				// Somebody else created it between the scan and now: it is
				// not a directory this call made, so its mode is not this
				// call's to set.
				continue
			}
			return err
		}
		// A component ABOVE the approved root — /rag/data itself, when a
		// fixture's tree starts empty — is created because MkdirAll must, but
		// its mode is not this call's business either: it is the deployment's,
		// not the tenant's. Only what is strictly inside a root is chmodded.
		if !contained(dir, f.roots()) {
			continue
		}
		if err := os.Chmod(dir, perm); err != nil {
			return err
		}
	}
	return nil
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

// Rename moves from to to; both must be under an approved root, and `to` must
// not exist.
//
// rename(2) REPLACES an existing destination silently — a file, or an empty
// directory — which in this package would be the one write path that
// overwrites without saying so: every other refuses (WriteAtomic is atomic
// over its own target, Archive.Create, SQLite.Backup and Extract all refuse an
// existing name). `decommission` renames a tenant tree aside and `restore`
// moves a staged one into place; both would rather fail than land on top of
// something that is already there.
func (f *RealFiles) Rename(_ context.Context, from, to string) error {
	rfrom, err := f.check(from)
	if err != nil {
		return err
	}
	rto, err := f.check(to)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(rto); err == nil {
		return fmt.Errorf("%w: %s already exists; the ctl never renames over an existing path", jobs.ErrRefused, rto)
	} else if !errors.Is(err, os.ErrNotExist) {
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

// ReadDir lists one directory level, sorted by name (os.ReadDir's order).
// Like ReadFile it is a read and so is not root-checked; an absent directory
// comes back as fs.ErrNotExist for callers that tell absence from failure.
func (f *RealFiles) ReadDir(_ context.Context, dir string) ([]jobs.DirEntry, error) {
	ents, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	out := make([]jobs.DirEntry, 0, len(ents))
	for _, e := range ents {
		// A symlink is reported as neither: it is not a directory this driver
		// will descend into, and a caller that copies it would be copying
		// whatever it points at, which may be outside the tenant tree
		// entirely. IsDir() is false for a symlink here (ReadDir does not
		// follow), so the entry lands as a plain name and the copy step's own
		// open decides.
		out = append(out, jobs.DirEntry{Name: e.Name(), IsDir: e.IsDir()})
	}
	return out, nil
}

// Sha256 streams path through sha256 and reports the digest and the size.
//
// Streaming rather than ReadFile: a bundle's SHA256SUMS covers elasticsearch
// segment files of several gigabytes each, and hashing them by first loading
// them into the daemon's heap is how a backup takes the host down.
func (f *RealFiles) Sha256(_ context.Context, path string) (string, int64, error) {
	fh, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer fh.Close()
	h := sha256.New()
	n, err := io.Copy(h, fh)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(h.Sum(nil)), n, nil
}

// DiskFree is the bytes available to THIS account on the filesystem holding
// path — statfs f_bavail, not f_bfree: the difference is the reserved blocks
// only root may use, and a precheck that counted those would approve a backup
// that then filled the filesystem for everything else on the host.
//
// The deepest existing ancestor is measured, because the bundle directory
// itself does not exist yet when the precheck runs.
func (f *RealFiles) DiskFree(_ context.Context, path string) (int64, error) {
	dir := path
	for {
		var st syscall.Statfs_t
		err := syscall.Statfs(dir, &st)
		if err == nil {
			return int64(st.Bavail) * int64(st.Bsize), nil
		}
		if !errors.Is(err, os.ErrNotExist) {
			return 0, err
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return 0, err
		}
		dir = parent
	}
}
