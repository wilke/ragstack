package main

import (
	"path/filepath"
	"testing"
)

// The release's escape hatches have to be REACHABLE. Both of them name
// themselves in a refusal an operator reads mid-handover — "pass
// accept_extra_databases", "pass accept_no_backup" — and a refusal that names a
// flag the CLI does not register is a dead end with the tenant still up.
//
// The assertion is the flag LAYER: an unknown flag is exit `usage`, a
// registered one is not. What the op then does with it is ops/handover.go's,
// and its own tests cover that.
func TestTheReleasesOverridesAreRealFlags(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("CTL_STATE_DIR", filepath.Join(dir, "state"))
	t.Setenv("CTL_CONFIG_DIR", filepath.Join(dir, "config"))
	registry := filepath.Join(dir, "registry.json") // absent: the run stops well after flag parsing

	run := func(extra ...string) int {
		args := append([]string{"dev", "--release", "--dry-run"}, extra...)
		return cmdTenantHandover(args, registry, dir, false)
	}

	// The control: a flag nobody registered is a usage error.
	if got := run("--accept-nothing-at-all"); got != exitUsage {
		t.Fatalf("an unknown flag = %d, want exitUsage (%d) — this test cannot tell the two apart otherwise",
			got, exitUsage)
	}
	for _, flag := range []string{"--accept-extra-databases", "--accept-no-backup"} {
		if got := run(flag); got == exitUsage {
			t.Errorf("%s is not a registered flag, but a refusal tells operators to pass it", flag)
		}
	}
}
