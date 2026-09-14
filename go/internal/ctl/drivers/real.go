package drivers

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"

	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// RealOptions configure the real driver set. Two drivers are real today —
// the gateway (internal/ctl/gateway) and the filesystem — because those are
// the two host surfaces PR-C owns end to end.
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
	// ApprovedRoots are the only directories the Files driver writes under.
	// Empty means the two roots every op needs — the tenant data tree and the
	// ctl config tree — never "everything".
	ApprovedRoots []string
}

// Real is the real driver set: gateway and files, and an honest refusal for
// everything that lands in PR-D.
type Real struct {
	opts    RealOptions
	gateway *RealGateway
	files   *RealFiles
}

var _ jobs.Drivers = (*Real)(nil)

// NewReal builds the real driver set.
func NewReal(o RealOptions) *Real {
	if len(o.ApprovedRoots) == 0 {
		o.ApprovedRoots = []string{o.Roots.DataDir, o.Roots.CtlConfigDir, o.Roots.CtlStateDir, o.Roots.BackupsDir}
	}
	return &Real{
		opts:    o,
		gateway: &RealGateway{opts: o},
		files:   &RealFiles{Roots: append([]string(nil), o.ApprovedRoots...)},
	}
}

func (r *Real) Systemd() jobs.Systemd             { return pendingSystemd{} }
func (r *Real) Proc() jobs.Proc                   { return pendingProc{} }
func (r *Real) Gateway() jobs.GatewayDriver       { return r.gateway }
func (r *Real) Files() jobs.Files                 { return r.files }
func (r *Real) Qdrant() jobs.Qdrant               { return pendingQdrant{} }
func (r *Real) Elasticsearch() jobs.Elasticsearch { return pendingES{} }
func (r *Real) TenantAPI() jobs.TenantAPI         { return pendingTenantAPI{} }

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
type RealFiles struct{ Roots []string }

func (f *RealFiles) check(path string) error {
	if !contained(path, f.Roots) {
		return outsideRoots(path, f.Roots)
	}
	return nil
}

// WriteAtomic writes data to path via a temporary file in the same directory
// whose mode is set before the rename.
func (f *RealFiles) WriteAtomic(_ context.Context, path string, data []byte, mode uint32) (err error) {
	if err := f.check(path); err != nil {
		return err
	}
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

// Rename moves from to to; both must be under an approved root.
func (f *RealFiles) Rename(_ context.Context, from, to string) error {
	if err := f.check(from); err != nil {
		return err
	}
	if err := f.check(to); err != nil {
		return err
	}
	return os.Rename(from, to)
}

// Remove deletes path, refusing to follow a symlink to get there.
func (f *RealFiles) Remove(_ context.Context, path string) error {
	if err := f.check(path); err != nil {
		return err
	}
	// O_NOFOLLOW is the check, and opening is how it is made: a lstat+unlink
	// pair can be raced, an open that refuses to follow cannot. ELOOP means
	// the path IS a symlink, which is exactly the case to refuse, and EISDIR
	// (a directory opened for writing) means the caller asked for a directory.
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

// The drivers PR-D ships. Each method refuses with the driver and method
// named, so a job that reaches one stops with a sentence an operator can act
// on rather than with a nil-pointer panic. They are five types rather than
// one because Qdrant.Snapshot and Elasticsearch.Snapshot are different
// methods with the same name.
type (
	pendingSystemd   struct{}
	pendingProc      struct{}
	pendingQdrant    struct{}
	pendingES        struct{}
	pendingTenantAPI struct{}
)

var (
	_ jobs.Systemd       = pendingSystemd{}
	_ jobs.Proc          = pendingProc{}
	_ jobs.Qdrant        = pendingQdrant{}
	_ jobs.Elasticsearch = pendingES{}
	_ jobs.TenantAPI     = pendingTenantAPI{}
)

func (pendingSystemd) DaemonReload(context.Context) error {
	return pending(jobs.ErrRefused, "systemd", "DaemonReload")
}
func (pendingSystemd) Start(context.Context, string) error {
	return pending(jobs.ErrRefused, "systemd", "Start")
}
func (pendingSystemd) Stop(context.Context, string) error {
	return pending(jobs.ErrRefused, "systemd", "Stop")
}
func (pendingSystemd) Enable(context.Context, string) error {
	return pending(jobs.ErrRefused, "systemd", "Enable")
}
func (pendingSystemd) Disable(context.Context, string) error {
	return pending(jobs.ErrRefused, "systemd", "Disable")
}
func (pendingSystemd) IsActive(context.Context, string) (bool, error) {
	return false, pending(jobs.ErrRefused, "systemd", "IsActive")
}
func (pendingProc) Listening(context.Context, int) (bool, error) {
	return false, pending(jobs.ErrRefused, "proc", "Listening")
}
func (pendingProc) Signal(context.Context, int, string, string, string) error {
	return pending(jobs.ErrRefused, "proc", "Signal")
}
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
func (pendingTenantAPI) Health(context.Context, string) error {
	return pending(jobs.ErrRefused, "tenantapi", "Health")
}
func (pendingTenantAPI) ServiceAccount(context.Context, string, string, string) error {
	return pending(jobs.ErrRefused, "tenantapi", "ServiceAccount")
}
