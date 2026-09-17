package drivers

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
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
type RealPostgres struct {
	opts RealOptions
	// run is the shared argv runner (exec.go); nil builds one from opts, so a
	// test that constructs the driver by hand still gets the sanitized env.
	run *runner
}

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
// pgConn is the connection half of every tool's argv: the socket directory,
// the port (which also names the socket file), the role and the database.
func pgConn(spec jobs.PostgresSpec) []string {
	args := []string{"-h", pgSocketDir}
	if spec.Port > 0 {
		args = append(args, "-p", strconv.Itoa(spec.Port))
	}
	return append(args, "-U", spec.User, "-d", spec.DB)
}

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
	argv := argvFor(bin, spec, "", "pg_isready", pgConn(spec)...)
	if out, err := p.exec(ctx, argv, 30*time.Second); err != nil {
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
	out, dir, base, err := p.ctlFile(out)
	if err != nil {
		return err
	}
	// The DIRECTORY must already exist: the backup makes the bundle tree
	// before it dumps, and a driver that created it would be creating it with
	// the wrong mode, outside the Files driver's containment rules.
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return fmt.Errorf("%w: %s is not an existing directory to write the dump into", jobs.ErrRefused, dir)
	}
	argv := argvFor(bin, spec, dir, "pg_dump", append(pgConn(spec), "-Fc", "-f", pgCtlDir+"/"+base)...)
	if o, err := p.exec(ctx, argv, p.longTimeout()); err != nil {
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
	in, dir, base, err := p.ctlFile(in)
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
	argv := argvFor(bin, spec, dir, "pg_restore", append(pgConn(spec),
		// --no-owner + --role: the bundle's dump belongs to whichever role
		// wrote it, and `restore --as` creates a tenant with a different one.
		// --exit-on-error because pg_restore's default is to log an error,
		// carry on, and exit 0 — which would report a half-restored database
		// as a success.
		"--no-owner", "--role="+spec.User, "--exit-on-error", pgCtlDir+"/"+base)...)
	if o, err := p.exec(ctx, argv, p.longTimeout()); err != nil {
		return fmt.Errorf("pg_restore into %s: %w%s", spec.DB, err, detail(o))
	}
	return nil
}

// censusSQL is the ONE query Census runs, and it is a literal: nothing from a
// registry row, a tenant name or an operator reaches it.
//
// It counts every ordinary table EXACTLY. `count(*)` per table rather than
// `pg_stat_user_tables.n_live_tup`, because that column is an estimate, it is
// reset by a restore, and a handover that proved its migration with an
// estimate would have proved nothing. `query_to_xml` is how one statement
// counts every table without the caller first asking for the table list and
// then building a second statement out of the answer — which is the pattern
// that turns a schema into a command line.
//
// The `=size=` row carries pg_database_size in the same result, so the two
// facts are read at ONE instant; two queries could straddle a write. And it is
// one LINE because the argv runner refuses an argument containing a newline —
// a rule worth keeping, so the SQL is spelled to fit it.
const censusSQL = "select '=size=' as n, pg_database_size(current_database()) as c" +
	" union all " +
	"select t.table_schema || '.' || t.table_name," +
	" (xpath('/row/c/text()'," +
	"        query_to_xml(format('select count(*) as c from %I.%I', t.table_schema, t.table_name)," +
	"                     false, true, '')))[1]::text::bigint" +
	" from information_schema.tables t" +
	" where t.table_type = 'BASE TABLE'" +
	"   and t.table_schema not in ('pg_catalog', 'information_schema')" +
	" order by 1"

// Census asks the database what it holds, in the terms a dump and a restore
// preserve: its size, and the exact row count of every table.
func (p *RealPostgres) Census(ctx context.Context, spec jobs.PostgresSpec) (jobs.PostgresCensus, error) {
	bin, err := p.check(spec)
	if err != nil {
		return jobs.PostgresCensus{}, err
	}
	// -A unaligned, -t tuples only, -F the separator, -X no .psqlrc (there is
	// no home in the container anyway), -v ON_ERROR_STOP so a query that
	// fails is an exit status rather than a line in the output.
	argv := argvFor(bin, spec, "", "psql", append(pgConn(spec),
		"-XAt", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", censusSQL)...)
	out, err := p.exec(ctx, argv, p.longTimeout())
	if err != nil {
		return jobs.PostgresCensus{}, fmt.Errorf("counting the rows of %s: %w%s", spec.DB, err, detail(out))
	}
	return parsePostgresCensus(out)
}

// parsePostgresCensus reads psql's unaligned output. A line that is not
// `<name>|<number>` is a refusal rather than a skip: this answer is a PROOF,
// and a parser that dropped what it did not understand would prove less than
// it claims while looking the same.
func parsePostgresCensus(out string) (jobs.PostgresCensus, error) {
	c := jobs.PostgresCensus{Tables: map[string]int64{}}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		name, count, ok := strings.Cut(line, "|")
		n, cerr := strconv.ParseInt(strings.TrimSpace(count), 10, 64)
		if !ok || cerr != nil {
			return jobs.PostgresCensus{}, fmt.Errorf("%w: the row census returned a line this driver does not "+
				"parse (%q); nothing about this database has been proved", jobs.ErrRefused, line)
		}
		if name == "=size=" {
			c.SizeBytes = n
			continue
		}
		c.Tables[name] = n
	}
	return c, nil
}

// handoverTools are the three programs a handover's postgres migration runs,
// and which of the two halves runs each. initdb is run by the IMAGE's
// entrypoint rather than by the ctl, which is exactly why it is on this list:
// nothing else would notice it was missing until a take had already renamed a
// tenant's cluster aside.
var handoverTools = []string{"pg_dump", "pg_restore", "initdb"}

// ToolVersions asks each of those tools, inside the tenant's own image, for its
// version. No socket and no database: this is a question about the IMAGE.
func (p *RealPostgres) ToolVersions(ctx context.Context, spec jobs.PostgresSpec) (map[string]string, error) {
	bin, err := p.check(spec)
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	for _, tool := range handoverTools {
		// argvFor binds the socket directory, which costs nothing and keeps
		// every call this driver makes shaped the same way.
		argv := argvFor(bin, spec, "", tool, "--version")
		o, err := p.exec(ctx, argv, 60*time.Second)
		if err != nil {
			return nil, fmt.Errorf("%w: %s cannot run %s, which a handover needs (the release dumps with pg_dump; "+
				"the take restores with pg_restore, and the image's own entrypoint initialises the new cluster "+
				"with initdb): %v%s", jobs.ErrRefused, spec.SIF, tool, err, detail(o))
		}
		out[tool] = strings.TrimSpace(o)
	}
	return out, nil
}

// ctlFile splits a host path into the RESOLVED path itself, the directory to
// bind and the base name to name inside the container, after checking
// containment.
//
// The check is made on the resolved path and the resolved directory is what
// gets bound, for a reason particular to this driver: the directory is handed
// to apptainer as a bind SOURCE, and apptainer resolves it. A dump path under
// a symlinked component therefore passed the lexical check and then bound
// whatever the link pointed at into the container, where pg_dump writes with
// this account's privileges.
func (p *RealPostgres) ctlFile(path string) (resolved, dir, base string, err error) {
	resolved, err = resolvedContained(path, p.opts.ApprovedRoots)
	if err != nil {
		return "", "", "", err
	}
	return resolved, filepath.Dir(resolved), filepath.Base(resolved), nil
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

// exec runs argv through the shared runner and returns the combined output,
// which is what the pg tools' diagnostics are read from: pg_dump writes its
// progress and its errors to stderr alike, so the two streams are joined in
// order of arrival rather than reported apart.
func (p *RealPostgres) exec(ctx context.Context, argv []string, timeout time.Duration) (string, error) {
	run := p.run
	if run == nil {
		run = &runner{Redact: p.opts.Redact, Logger: p.opts.Logger}
	}
	stdout, stderr, err := run.Run(ctx, Spec{Program: argv[0], Args: argv[1:], Timeout: timeout})
	return string(stdout) + string(stderr), err
}
