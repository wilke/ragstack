package drivers

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// unitNameRE is the ONLY unit name shape this driver will name to systemd.
//
// It is the plan's regex, and it is a security boundary rather than a
// convenience: `systemctl --user stop <unit>` with a name that came from a
// registry row would otherwise let whoever can write registry.json stop
// anything in this user's manager — their ssh agent, their tmux, their own
// session scope. Every unit argument is matched against it, on every verb,
// including the ones that only read.
var unitNameRE = regexp.MustCompile(`^ragstack-[a-z][a-z0-9-]{0,31}(-(qdrant|es|postgres|api|ui)\.service|\.target)$`)

// systemdVerbs is the verb allowlist. `systemctl` has around sixty verbs;
// these ten are the ones the ctl's ops need, and a driver that accepted the
// others would accept `kill`, `mask` and `switch-root` from a caller that only
// ever meant to start a tenant.
var systemdVerbs = map[string]bool{
	"daemon-reload": true, "start": true, "stop": true, "enable": true,
	"disable": true, "is-active": true, "is-enabled": true, "show": true,
	"link": true, "reset-failed": true,
}

// systemdTimeout bounds one systemctl call. A start that blocks longer than
// this is a unit whose own TimeoutStartSec is the thing to look at, and the
// job engine's readiness probes are what actually decide a tenant is up.
const systemdTimeout = 90 * time.Second

// RealSystemd is `systemctl --user` behind the jobs.Systemd seam.
//
// --user, never --system: the whole control plane runs in one account's user
// manager (plan "Host facts"), and a system-level unit verb would need root
// this process does not have and must never acquire.
type RealSystemd struct {
	run *runner
	// Bin is the absolute systemctl path from RealOptions.
	Bin string
}

var _ jobs.Systemd = (*RealSystemd)(nil)

// checkUnit enforces the name allowlist.
func checkUnit(unit string) error {
	if !unitNameRE.MatchString(unit) {
		return fmt.Errorf("%w: %q is not a unit this control plane may touch (it manages only ragstack-<tenant>[-store].service and ragstack-<tenant>.target)",
			jobs.ErrRefused, unit)
	}
	return nil
}

// systemctl runs one allowlisted verb. Every call in this file goes through
// it, so the two allowlists cannot be bypassed by a method that builds its own
// argv.
func (s *RealSystemd) systemctl(ctx context.Context, verb string, args ...string) ([]byte, error) {
	if !systemdVerbs[verb] {
		return nil, fmt.Errorf("%w: systemctl %s is not a verb this driver runs", jobs.ErrRefused, verb)
	}
	argv := append([]string{"--user", verb}, args...)
	stdout, _, err := s.run.Run(ctx, Spec{Program: s.Bin, Args: argv, Timeout: systemdTimeout})
	return stdout, err
}

// DaemonReload makes the manager re-read the unit files.
func (s *RealSystemd) DaemonReload(ctx context.Context) error {
	_, err := s.systemctl(ctx, "daemon-reload")
	return err
}

func (s *RealSystemd) Start(ctx context.Context, unit string) error {
	return s.verb(ctx, "start", unit)
}

// Stop is idempotent over absence: a unit the manager does not have loaded
// is a unit that is not running, which is the state Stop wants. `systemctl
// stop` answers exit 5 "not loaded" for it, and a decommission that stops
// units an earlier, interrupted decommission had already removed used to
// fail right there.
func (s *RealSystemd) Stop(ctx context.Context, unit string) error {
	return notLoadedIsFine(unit, s.verb(ctx, "stop", unit))
}
func (s *RealSystemd) Enable(ctx context.Context, unit string) error {
	return s.verb(ctx, "enable", unit)
}
func (s *RealSystemd) Disable(ctx context.Context, unit string) error {
	return notLoadedIsFine(unit, s.verb(ctx, "disable", unit))
}

// notLoadedIsFine turns systemctl's "Unit <unit> not loaded" / "Unit file
// <unit> does not exist" into success for the verbs whose goal is the unit's
// absence.
//
// The message has to NAME the unit that was acted on. Matching the phrases
// anywhere in stderr swallowed a different failure entirely: systemd refuses a
// stop whose transaction would break another unit with
//
//	Transaction for X.service/stop is destructive (Y.service has 'not found' job queued).
//
// which contains "not found", is exit 1, and means the unit was NOT stopped —
// so a decommission reported every unit gone while one of them was still
// running. The phrases below are the ones systemd writes with the unit's own
// name in them, which the destructive-transaction message is not.
func notLoadedIsFine(unit string, err error) error {
	// exit 5 for `stop` ("not loaded"), exit 1 for `disable` ("Unit file X
	// does not exist"): the message is the fact, the code varies by verb.
	var ee *ExecError
	if !errors.As(err, &ee) {
		return err
	}
	msg := strings.ToLower(ee.Stderr)
	u := strings.ToLower(unit)
	for _, absent := range []string{
		"unit " + u + " not loaded",
		"unit " + u + " could not be found",
		"unit " + u + " not found",
		"unit file " + u + " does not exist",
		"unit " + u + " does not exist",
	} {
		if strings.Contains(msg, absent) {
			return nil
		}
	}
	return err
}

// ResetFailed clears a unit's failed state and its start-rate counter.
//
// It is never called implicitly before a Start: a unit that failed five times
// in ten seconds is a unit systemd is right to hold down, and a driver that
// quietly reset the counter on every start would turn a crash loop into an
// invisible one. The ops layer calls it when an operator asked for a retry.
func (s *RealSystemd) ResetFailed(ctx context.Context, unit string) error {
	return s.verb(ctx, "reset-failed", unit)
}

func (s *RealSystemd) verb(ctx context.Context, verb, unit string) error {
	if err := checkUnit(unit); err != nil {
		return err
	}
	_, err := s.systemctl(ctx, verb, unit)
	return err
}

// Link makes a unit file outside the manager's search path loadable.
//
// The ctl writes units into its own config tree, which `systemctl --user` does
// not search, and the drop-in that would add it (SYSTEMD_UNIT_PATH) is a root
// item coconut does not have yet. `link` creates the symlink from the search
// path to the ctl's file, which is how a rendered unit becomes a real one.
//
// It is idempotent, and it has to be: `tenant create` is retried after a
// crash, and a second `link` of a unit that already resolves to that path
// fails on some systemd versions ("Failed to link unit: File exists"). The
// FragmentPath is therefore read first, and a unit that already resolves to
// this exact file is a success with nothing run.
func (s *RealSystemd) Link(ctx context.Context, unitPath string) error {
	if !filepath.IsAbs(unitPath) || filepath.Clean(unitPath) != unitPath {
		return fmt.Errorf("%w: %q must be an absolute, clean path to link", jobs.ErrRefused, unitPath)
	}
	unit := filepath.Base(unitPath)
	if err := checkUnit(unit); err != nil {
		return err
	}
	info, err := s.Show(ctx, unit)
	if err != nil {
		return err
	}
	if info.FragmentPath == unitPath {
		return nil
	}
	_, err = s.systemctl(ctx, "link", unitPath)
	return err
}

// showProperties are the properties Show asks for, in one place so that the
// parse and the request cannot drift.
var showProperties = []string{
	"ActiveState", "SubState", "Result", "UnitFileState", "FragmentPath",
	"MainPID", "NRestarts", "ExecMainStatus",
}

// Show is `systemctl --user show -p … <unit>`.
//
// `--value` is deliberately NOT used: it prints the values alone, in an order
// the caller has to assume, and an older systemd that does not know a property
// simply omits its line — which silently shifts every later value into the
// wrong field. Parsing `Key=Value` lines costs nothing and cannot be
// misaligned by a missing property.
//
// A unit the manager has never heard of is NOT an error (seam decision 1):
// `systemctl show` exits 0 with LoadState=not-found and an empty FragmentPath.
// Callers that mean "the units are gone" — decommission's post-check, the
// selftest's — read that emptiness.
func (s *RealSystemd) Show(ctx context.Context, unit string) (jobs.UnitInfo, error) {
	if err := checkUnit(unit); err != nil {
		return jobs.UnitInfo{}, err
	}
	args := make([]string, 0, len(showProperties)*2+1)
	for _, p := range showProperties {
		args = append(args, "-p", p)
	}
	args = append(args, unit)
	out, err := s.systemctl(ctx, "show", args...)
	if err != nil {
		return jobs.UnitInfo{}, err
	}
	return parseShow(out), nil
}

// parseShow reads the Key=Value lines of `systemctl show`.
func parseShow(out []byte) jobs.UnitInfo {
	var info jobs.UnitInfo
	for _, line := range strings.Split(string(out), "\n") {
		k, v, ok := strings.Cut(strings.TrimRight(line, "\r"), "=")
		if !ok {
			continue
		}
		switch k {
		case "ActiveState":
			info.ActiveState = v
		case "SubState":
			info.SubState = v
		case "Result":
			info.Result = v
		case "UnitFileState":
			info.UnitFileState = v
		case "FragmentPath":
			info.FragmentPath = v
		case "MainPID":
			info.MainPID = atoiOr0(v)
		case "NRestarts":
			info.NRestarts = atoiOr0(v)
		case "ExecMainStatus":
			info.ExecMainStatus = atoiOr0(v)
		}
	}
	// A unit the manager does not know reports state but no fragment. The
	// UnitInfo is zeroed so that "FragmentPath is empty" is the single fact a
	// caller reads, rather than "inactive/dead, which might mean stopped".
	if info.FragmentPath == "" {
		return jobs.UnitInfo{}
	}
	return info
}

func atoiOr0(s string) int {
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		return 0
	}
	return n
}

// activeFalse are the `is-active` answers that mean "not active". Anything
// else with a non-zero exit is a systemctl this driver does not understand —
// a broken bus, a manager that is not running — and reporting THAT as "the
// unit is stopped" would let a job conclude a tenant is down when nobody
// actually asked the manager anything.
var activeFalse = map[string]bool{
	"inactive": true, "failed": true, "deactivating": true,
	"activating": true, "reloading": true, "unknown": true, "": true,
}

// IsActive is `systemctl --user is-active <unit>`.
func (s *RealSystemd) IsActive(ctx context.Context, unit string) (bool, error) {
	if err := checkUnit(unit); err != nil {
		return false, err
	}
	out, err := s.systemctl(ctx, "is-active", unit)
	state := strings.TrimSpace(string(out))
	if err == nil {
		return state == "active", nil
	}
	if activeFalse[state] {
		return false, nil
	}
	return false, err
}

// enabledFalse are the `is-enabled` answers that mean "not enabled at boot".
//
// `linked` is among them: a linked unit is known to the manager and startable,
// but nothing will start it at boot, and desired_boot is exactly the question
// this method answers. The empty string is there because an unknown unit
// exits non-zero with nothing on stdout, and "systemd will not start a unit it
// has never heard of" is a false, not an error.
var enabledFalse = map[string]bool{
	"disabled": true, "masked": true, "masked-runtime": true,
	"linked": true, "linked-runtime": true, "transient": true, "": true,
}

// IsEnabled is `systemctl --user is-enabled <unit>`.
func (s *RealSystemd) IsEnabled(ctx context.Context, unit string) (bool, error) {
	if err := checkUnit(unit); err != nil {
		return false, err
	}
	out, err := s.systemctl(ctx, "is-enabled", unit)
	state := strings.TrimSpace(string(out))
	if err == nil {
		// Exit 0 covers enabled, enabled-runtime, static, indirect,
		// generated and alias. Only the first two mean "an operator asked for
		// this at boot"; the others are systemd's own bookkeeping and are not
		// a desired_boot an operator set.
		return state == "enabled" || state == "enabled-runtime", nil
	}
	if enabledFalse[state] {
		return false, nil
	}
	return false, err
}
