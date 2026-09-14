package drivers

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// RealPostgres runs pg_isready, pg_dump and pg_restore INSIDE the tenant's own
// postgres image, over the socket directory the unit already binds.
//
// Two things follow from that and shape everything below. The image is where
// the client tools of the RIGHT major version live — a pg_dump from the host
// (or from another tenant's image) against a newer server refuses outright, and
// against an older one produces an archive the server cannot read back. And the
// socket is where authentication is `trust` (the image's own pg_hba has
// `local all all trust`), so nothing here needs — or is given — the role
// password: it exists only in the tenant's secrets.env and reaches the postgres
// unit through EnvironmentFile.
type RealPostgres struct{ opts RealOptions }

var _ jobs.Postgres = (*RealPostgres)(nil)

// pgIdentRe is the database and role rule. Both are derived from the tenant
// name, so this is the tenant-name shape; anything else is a registry row the
// ctl will not build a command line from.
var pgIdentRe = regexp.MustCompile(`^[a-z][a-z0-9-]{0,31}$`)

// The paths the container sees. /mnt/ctl is where the dump's DIRECTORY is
// bound — the directory and not the file, because pg_dump creates the file and
// a bind of a path that does not exist yet is an error.
const (
	pgSocketDir = "/var/run/postgresql"
	pgCtlDir    = "/mnt/ctl"
)

// defaultApptainer is where apptainer 1.5.3 lives on coconut.
const defaultApptainer = "/usr/bin/apptainer"

// check validates a spec and returns the apptainer binary to run it with.
func (p *RealPostgres) check(spec jobs.PostgresSpec) (string, error) {
	if _, err := paths.SafePath("/", spec.SIF); err != nil {
		return "", fmt.Errorf("%w: the postgres image path is unusable: %v", jobs.ErrRefused, err)
	}
	if _, err := paths.SafePath("/", spec.RunDir); err != nil {
		return "", fmt.Errorf("%w: the postgres socket directory is unusable: %v", jobs.ErrRefused, err)
	}
	if !pgIdentRe.MatchString(spec.DB) {
		return "", fmt.Errorf("%w: %q is not a database name the ctl will pass to postgres (want %s)",
			jobs.ErrRefused, spec.DB, pgIdentRe)
	}
	if !pgIdentRe.MatchString(spec.User) {
		return "", fmt.Errorf("%w: %q is not a role name the ctl will pass to postgres (want %s)",
			jobs.ErrRefused, spec.User, pgIdentRe)
	}
	bin := p.opts.Apptainer
	if bin == "" {
		bin = defaultApptainer
	}
	if !filepath.IsAbs(bin) {
		return "", fmt.Errorf("%w: the apptainer binary %q is not an absolute path", jobs.ErrRefused, bin)
	}
	return bin, nil
}

// argv builds `apptainer exec …` for one tool. bindCtl is the host directory
// bound at /mnt/ctl, or "" when the call needs no file.
//
// --no-home because the tenant's postgres tools have no business reading the
// ctl account's home directory, and because a $HOME/.psqlrc or .pgpass picked
// up from it would change what these commands do.
func argvFor(bin string, spec jobs.PostgresSpec, bindCtl, tool string, toolArgs ...string) []string {
	argv := []string{bin, "exec", "--no-home", "--bind", spec.RunDir + ":" + pgSocketDir}
	if bindCtl != "" {
		argv = append(argv, "--bind", bindCtl+":"+pgCtlDir)
	}
	argv = append(argv, spec.SIF, tool)
	return append(argv, toolArgs...)
}

// Ready is pg_isready over the socket.
func (p *RealPostgres) Ready(ctx context.Context, spec jobs.PostgresSpec) error {
	bin, err := p.check(spec)
	if err != nil {
		return err
	}
	argv := argvFor(bin, spec, "", "pg_isready", "-h", pgSocketDir, "-U", spec.User, "-d", spec.DB)
	if out, err := runArgv(ctx, argv, 30*time.Second); err != nil {
		return fmt.Errorf("pg_isready for %s: %w%s", spec.DB, err, detail(out))
	}
	return nil
}

// Dump writes a custom-format archive of the tenant's database to out.
func (p *RealPostgres) Dump(ctx context.Context, spec jobs.PostgresSpec, out string) error {
	bin, err := p.check(spec)
	if err != nil {
		return err
	}
	dir, base, err := p.ctlFile(out)
	if err != nil {
		return err
	}
	// The DIRECTORY must already exist: the backup makes the bundle tree
	// before it dumps, and a driver that created it would be creating it with
	// the wrong mode, outside the Files driver's containment rules.
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return fmt.Errorf("%w: %s is not an existing directory to write the dump into", jobs.ErrRefused, dir)
	}
	argv := argvFor(bin, spec, dir, "pg_dump",
		"-h", pgSocketDir, "-U", spec.User, "-d", spec.DB, "-Fc", "-f", pgCtlDir+"/"+base)
	if o, err := runArgv(ctx, argv, p.longTimeout()); err != nil {
		// A partial dump is worse than none: it opens, it restores, and it is
		// short. Remove it so the bundle's checksum step cannot record it.
		_ = os.Remove(out)
		return fmt.Errorf("pg_dump of %s: %w%s", spec.DB, err, detail(o))
	}
	// chmod AFTER the fact, not before: apptainer runs pg_dump with the host
	// umask and creates the file itself, so there is no earlier moment at
	// which the mode can be set. The window is a directory the ctl created
	// 2770 inside a tenant tree, which is what makes it acceptable.
	if err := os.Chmod(out, 0o640); err != nil {
		return fmt.Errorf("setting the mode of the dump %s: %w", out, err)
	}
	return nil
}

// Restore loads a custom-format archive into the tenant's database.
func (p *RealPostgres) Restore(ctx context.Context, spec jobs.PostgresSpec, in string) error {
	bin, err := p.check(spec)
	if err != nil {
		return err
	}
	dir, base, err := p.ctlFile(in)
	if err != nil {
		return err
	}
	// fs.ErrNotExist, matching SQLite.Backup, so a caller telling "the bundle
	// has no postgres dump" apart from "the restore failed" can do it with
	// errors.Is rather than by reading a message.
	if st, err := os.Stat(in); err != nil || st.IsDir() {
		if err == nil {
			err = fmt.Errorf("%s is a directory", in)
		}
		return fmt.Errorf("the dump to restore is not readable: %w", err)
	}
	argv := argvFor(bin, spec, dir, "pg_restore",
		"-h", pgSocketDir, "-U", spec.User, "-d", spec.DB,
		// --no-owner + --role: the bundle's dump belongs to whichever role
		// wrote it, and `restore --as` creates a tenant with a different one.
		// --exit-on-error because pg_restore's default is to log an error,
		// carry on, and exit 0 — which would report a half-restored database
		// as a success.
		"--no-owner", "--role="+spec.User, "--exit-on-error", pgCtlDir+"/"+base)
	if o, err := runArgv(ctx, argv, p.longTimeout()); err != nil {
		return fmt.Errorf("pg_restore into %s: %w%s", spec.DB, err, detail(o))
	}
	return nil
}

// ctlFile splits a host path into the directory to bind and the base name to
// name inside the container, after checking containment.
func (p *RealPostgres) ctlFile(path string) (dir, base string, err error) {
	if _, err := paths.SafePath("/", path); err != nil {
		return "", "", fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	dir = filepath.Dir(path)
	if !contained(path, p.opts.ApprovedRoots) {
		return "", "", outsideRoots(path, p.opts.ApprovedRoots)
	}
	return dir, filepath.Base(path), nil
}

func (p *RealPostgres) longTimeout() time.Duration {
	if p.opts.StoreLongTimeout > 0 {
		return p.opts.StoreLongTimeout
	}
	return LongTimeout
}

// detail renders captured output for an error message, or "" when there was
// none.
func detail(out string) string {
	s := strings.Join(strings.Fields(out), " ")
	if s == "" {
		return ""
	}
	if len(s) > errBodyBytes {
		s = s[:errBodyBytes] + "…"
	}
	return ": " + s
}

// runArgv runs one program by absolute path with a sanitized environment and a
// deadline, returning its combined output.
//
// TEMPORARY: agent A's drivers/exec.go carries the argv runner every host
// driver shares, and this is the same idea in miniature so that the postgres
// driver does not depend on a file landing in another branch. At integration
// this function goes and the calls above move to the shared one; it is kept
// under forty lines deliberately, so that there is nothing here worth keeping
// instead.
func runArgv(ctx context.Context, argv []string, timeout time.Duration) (string, error) {
	if len(argv) == 0 || !filepath.IsAbs(argv[0]) {
		return "", fmt.Errorf("%w: a driver runs absolute program paths only", jobs.ErrRefused)
	}
	if _, err := os.Stat(argv[0]); err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return "", fmt.Errorf("%w: %s is not installed on this host", jobs.ErrRefused, argv[0])
		}
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	// A sanitized environment, not the daemon's: the tenant's secrets reach
	// the ctl's process environment in some deployments, and a child that
	// inherited them would put them in front of a container.
	cmd.Env = []string{
		"PATH=/usr/bin:/bin",
		"HOME=" + os.Getenv("HOME"),
		"LANG=C.UTF-8",
	}
	for _, k := range []string{"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"} {
		if v := os.Getenv(k); v != "" {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
	}
	// WaitDelay, because killing the program is not enough: apptainer forks the
	// container process, which inherits the output pipe, and CombinedOutput
	// blocks until that pipe closes — so a timeout without this waits for the
	// grandchild the deadline was supposed to end.
	cmd.WaitDelay = 5 * time.Second
	out, err := cmd.CombinedOutput()
	if ctx.Err() != nil {
		return string(out), fmt.Errorf("timed out after %s", timeout)
	}
	return string(out), err
}
