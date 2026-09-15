package drivers

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// defaultCrontabBin is crontab(1) on coconut.
const defaultCrontabBin = "/usr/bin/crontab"

// RealCrontab is the CURRENT account's crontab through crontab(1).
//
// It is the only boot hook this host has: svcbvbrc has no linger and a cron
// job gets no logind session here, so `systemctl --user` cannot be driven from
// `@reboot` at all (plan PR-D2, "Host facts"). `fleet enable-boot --cron` adds
// one marked line through this driver.
//
// There is no `-u` anywhere in this file, and there is no way for a caller to
// introduce one: the argv is a constant. `crontab -u <user>` needs root and
// edits somebody else's jobs, and a control plane that could name the account
// would be one registry typo away from rewriting the crontab of one of this
// host's 1869 group members.
type RealCrontab struct {
	run *runner
	// Bin is crontab(1), absolute.
	Bin string
}

var _ jobs.Crontab = (*RealCrontab)(nil)

// crontabTimeout bounds both verbs. crontab(1) reads or writes one small file
// and returns; a call that takes longer than this is a host problem, not a
// slow command.
const crontabTimeout = 30 * time.Second

// maxCrontab is the largest body this driver will install.
//
// A crontab is lines a person wrote. 64 KiB is thousands of them, so the limit
// never bites an operator and does bite the case it exists for: a body built
// from something that was not a crontab — a log, a rendered file, a loop that
// appended instead of replacing — which crontab(1) would happily install as
// the account's jobs.
const maxCrontab = 64 << 10

// List is `crontab -l`.
//
// An account with NO crontab is an empty body and no error. crontab(1) exits 1
// with "no crontab for <user>" on stderr for that case, and a driver that
// passed the status through would make "this host has never had a crontab" —
// the state of a freshly created service account, and exactly the state
// `enable-boot` runs in — indistinguishable from a broken cron.
func (c *RealCrontab) List(ctx context.Context) ([]byte, error) {
	stdout, _, err := c.run.Run(ctx, Spec{
		Program: c.Bin,
		Args:    []string{"-l"},
		Timeout: crontabTimeout,
	})
	if err != nil {
		if isNoCrontab(err) {
			return nil, nil
		}
		return nil, err
	}
	return stdout, nil
}

// isNoCrontab recognises the "this account has no crontab" answer.
//
// The TEXT, not the exit status: crontab(1) exits 1 for that and for a dozen
// real failures, and treating every exit 1 as an empty crontab would let
// `enable-boot` overwrite a crontab it had failed to read.
func isNoCrontab(err error) bool {
	var ee *ExecError
	if !errors.As(err, &ee) {
		return false
	}
	return strings.Contains(strings.ToLower(ee.Stderr), "no crontab for")
}

// Set is `crontab -` with body on STDIN.
//
// Not a file argument: a file the ctl wrote and crontab(1) then read is a
// window in which anything that can write that path chooses the account's cron
// jobs. body replaces the WHOLE crontab — which line the ctl owns and how it
// is marked is policy, and lives in ops.
func (c *RealCrontab) Set(ctx context.Context, body []byte) error {
	if len(body) > maxCrontab {
		return fmt.Errorf("%w: a crontab of %d bytes is not one this driver installs (limit %d)",
			jobs.ErrRefused, len(body), maxCrontab)
	}
	// A NUL cannot be in a crontab, and a body carrying one is not a crontab
	// that lost a character — it is bytes from somewhere else.
	if bytes.IndexByte(body, 0) >= 0 {
		return fmt.Errorf("%w: the crontab body contains a NUL", jobs.ErrRefused)
	}
	_, _, err := c.run.Run(ctx, Spec{
		Program: c.Bin,
		Args:    []string{"-"},
		Stdin:   body,
		Timeout: crontabTimeout,
	})
	return err
}
