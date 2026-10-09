package main

import (
	"strings"
	"testing"
)

// `env set-surface` / `env unset-surface` (#714) are CLI-only and --direct
// only: --server is refused before anything is built, exactly as for
// `env pg-password`, and the positionals are checked here rather than as a
// 422 from an engine.
func TestEnvSurfaceRefusesServerAndBadPositionals(t *testing.T) {
	cases := []struct {
		args []string
		want string
	}{
		{[]string{"env", "set-surface", "clark", "LLM_ENDPOINT=http://mango.cels.anl.gov:8003",
			"--server", "http://127.0.0.1:1"}, "no daemon route"},
		{[]string{"env", "unset-surface", "clark", "LLM_ENDPOINT", "--server", "http://127.0.0.1:1"},
			"no daemon route"},
		{[]string{"env", "set-surface", "clark", "LLM_ENDPOINT"}, "not KEY=VALUE"},
		{[]string{"env", "set-surface", "clark", "=x"}, "not KEY=VALUE"},
		{[]string{"env", "set-surface", "clark", "A=1", "A=2"}, "given twice"},
		{[]string{"env", "unset-surface", "clark", "A", "A"}, "given twice"},
		{[]string{"env", "set-surface", "clark"}, "usage: ragstack-ctl env"},
	}
	for _, c := range cases {
		rc, _, errs := capture(t, c.args...)
		if rc != exitUsage || !strings.Contains(errs, c.want) {
			t.Errorf("%v: rc %d, stderr %q; want exit %d mentioning %q", c.args, rc, errs, exitUsage, c.want)
		}
	}
	// The usage names both verbs and the --new-key caveat.
	_, _, errs := capture(t, "env", "help")
	for _, want := range []string{"set-surface <tenant> KEY=VALUE", "unset-surface <tenant> KEY", "--new-key",
		"CTL_ALLOWED_ENDPOINT_HOSTS"} {
		if !strings.Contains(errs, want) {
			t.Errorf("env usage lacks %q", want)
		}
	}
}
