package main

import (
	"strings"
	"testing"
)

// The two preparation commands are argument translators: the engine's own
// tests (internal/ctl/ops) are what prove the plans. What is asserted HERE is
// the part only the CLI can get wrong — which positionals it takes, which
// values it refuses before anything is submitted, and that it never tries to
// send a routeless verb to a daemon.

func TestSetUIModeUsageRefusalsHappenBeforeAnythingIsSubmitted(t *testing.T) {
	for _, c := range []struct {
		name string
		args []string
		want string
	}{
		{"no positionals", []string{"tenant", "set-ui-mode"}, "usage:"},
		{"no mode", []string{"tenant", "set-ui-mode", "dev"}, "usage:"},
		{"three positionals", []string{"tenant", "set-ui-mode", "dev", "static", "extra"}, "usage:"},
		// `dev` is a real ui.mode in the registry and NOT a direction this op
		// moves a tenant in, so the message has to say where to go instead
		// rather than "not a mode".
		{"dev is not a direction", []string{"tenant", "set-ui-mode", "dev", "dev"}, "external"},
		{"unknown mode", []string{"tenant", "set-ui-mode", "dev", "sideways"}, "not static or external"},
	} {
		t.Run(c.name, func(t *testing.T) {
			rc, _, errb := capture(t, c.args...)
			if rc != exitUsage {
				t.Fatalf("rc = %d, want %d (%s)", rc, exitUsage, errb)
			}
			if !strings.Contains(errb, c.want) {
				t.Errorf("stderr %q does not mention %q", errb, c.want)
			}
		})
	}
}

func TestSetBindRefusesAnAddressTheRegistryCannotHold(t *testing.T) {
	for _, bind := range []string{"localhost", "::1", "10.0.0.5", "0.0.0.0/0"} {
		rc, _, errb := capture(t, "tenant", "set-bind", "dev", bind)
		if rc != exitUsage {
			t.Errorf("set-bind %s: rc = %d, want %d", bind, rc, exitUsage)
		}
		if bind == "localhost" && !strings.Contains(errb, "/etc/hosts") {
			t.Errorf("the refusal for `localhost` does not say why a name is not an address: %q", errb)
		}
	}
	rc, _, errb := capture(t, "tenant", "set-bind", "dev")
	if rc != exitUsage || !strings.Contains(errb, "usage:") {
		t.Errorf("set-bind with no address: rc = %d, stderr %q", rc, errb)
	}
}

// TestSetSupervisorDesiredBootIsValidatedBeforeSubmission: `--desired-boot` is
// registry-only and enum-valued, so a bad value is a usage error from the CLI
// rather than a job that plans and then refuses.
func TestSetSupervisorDesiredBootIsValidatedBeforeSubmission(t *testing.T) {
	for _, boot := range []string{"on", "true", "yes", "ENABLED"} {
		rc, _, errb := capture(t, "tenant", "set-supervisor", "dev", "instance", "--desired-boot", boot)
		if rc != exitUsage {
			t.Errorf("--desired-boot %s: rc = %d, want %d (%s)", boot, rc, exitUsage, errb)
		}
		if !strings.Contains(errb, "enabled or disabled") {
			t.Errorf("--desired-boot %s: stderr %q does not name the enum", boot, errb)
		}
	}
	// The usage text has to say what the field is for: an operator reaches for
	// it to repair a row whose boot intent went missing with its handover block.
	_, _, help := capture(t, "tenant", "set-supervisor")
	for _, want := range []string{"--desired-boot", "desired_boot", "@reboot", "left alone"} {
		if !strings.Contains(help, want) {
			t.Errorf("set-supervisor usage does not mention %q:\n%s", want, help)
		}
	}
}

// Both verbs are CLI-only. --server is REFUSED rather than ignored: the daemon
// has no route for either, so a request would come back 422 "not a verb",
// which reads as a bug in the CLI rather than as a deliberate design.
func TestThePreparationOpsRefuseServer(t *testing.T) {
	for _, args := range [][]string{
		{"tenant", "set-ui-mode", "dev", "static", "--server", "http://127.0.0.1:23990"},
		{"tenant", "set-bind", "dev", "127.0.0.1", "--server", "http://127.0.0.1:23990"},
	} {
		rc, _, errb := capture(t, args...)
		if rc != exitUsage {
			t.Errorf("%v: rc = %d, want %d (%s)", args, rc, exitUsage, errb)
		}
		if !strings.Contains(errb, "no daemon route") || !strings.Contains(errb, "without --server") {
			t.Errorf("%v: stderr %q does not explain the refusal", args, errb)
		}
	}
}

// The help text is part of the deliverable: an operator reaches for these two
// commands once, days before a handover, and has to be able to read what they
// will do from the terminal.
func TestThePreparationOpsDocumentThemselves(t *testing.T) {
	_, _, uiHelp := capture(t, "tenant", "set-ui-mode")
	for _, want := range []string{
		"npm ci", "vite build --base", "dist.prev-<ts>", "$tenant_ui", "expect 200", "--direct is implied",
	} {
		if !strings.Contains(uiHelp, want) {
			t.Errorf("set-ui-mode usage does not mention %q:\n%s", want, uiHelp)
		}
	}
	_, _, bindHelp := capture(t, "tenant", "set-bind")
	for _, want := range []string{"api.bind", "restore.sh", "NEXT restart", "no tenant.env key"} {
		if !strings.Contains(bindHelp, want) {
			t.Errorf("set-bind usage does not mention %q:\n%s", want, bindHelp)
		}
	}
}

// `tenant backup --scope` is the light bundle's entry point; the flag has to
// reach the args object, and the help has to say what the bundle is and is not.
func TestBackupScopeIsDocumented(t *testing.T) {
	_, _, help := capture(t, "tenant", "backup")
	for _, want := range []string{"--scope", "config,state", "no\n            store snapshots", "restore prerequisite"} {
		if !strings.Contains(help, want) {
			t.Errorf("tenant backup usage does not mention %q:\n%s", want, help)
		}
	}
}
