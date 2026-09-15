package main

import (
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The crontab editor is the part of `fleet enable-boot` that touches somebody
// else's file, so it is the part that is tested line by line.
func TestBootCrontabEditKeepsEveryOtherLine(t *testing.T) {
	// The gateway's own @reboot line really is in this account's crontab.
	other := "@reboot /home/svcbvbrc/start-proxy.sh\n" +
		"# a comment an operator wrote\n" +
		"*/10 * * * * /usr/bin/true\n"

	next, changed, err := editBootCrontab(other, true)
	if err != nil || !changed {
		t.Fatalf("install: changed=%v err=%v", changed, err)
	}
	for _, line := range strings.Split(strings.TrimRight(other, "\n"), "\n") {
		if !strings.Contains(next, line) {
			t.Errorf("install dropped %q:\n%s", line, next)
		}
	}
	if !strings.Contains(next, bootMarker) || !strings.Contains(next, "fleet start --all --direct") {
		t.Errorf("the marked line is not there:\n%s", next)
	}
	if !strings.HasSuffix(next, "\n") {
		t.Errorf("crontab(1) drops a final line with no newline: %q", next)
	}

	// Installing twice is a no-op, which is what makes the command safe to
	// re-run from a runbook.
	again, changed, err := editBootCrontab(next, true)
	if err != nil || changed || again != next {
		t.Errorf("a second install changed something: changed=%v err=%v", changed, err)
	}

	// And removing it leaves exactly the lines it started with.
	back, changed, err := editBootCrontab(next, false)
	if err != nil || !changed {
		t.Fatalf("remove: changed=%v err=%v", changed, err)
	}
	if back != other {
		t.Errorf("remove did not restore the original crontab:\ngot  %q\nwant %q", back, other)
	}
	if _, changed, err := editBootCrontab(back, false); err != nil || changed {
		t.Errorf("removing a line that is not there changed something: changed=%v err=%v", changed, err)
	}
}

// An empty crontab — the common case on a fresh account — installs one line
// and nothing else.
func TestBootCrontabFromEmpty(t *testing.T) {
	next, changed, err := editBootCrontab("", true)
	if err != nil || !changed {
		t.Fatalf("changed=%v err=%v", changed, err)
	}
	if next != bootLine()+"\n" {
		t.Errorf("crontab = %q, want just the marked line", next)
	}
}

// A line whose COMMAND drifted (an older ctl wrote it, or somebody edited it)
// is rewritten in place rather than duplicated.
func TestBootCrontabRewritesADriftedLine(t *testing.T) {
	old := "@reboot /rag/bin/ragstack-ctl fleet start --all  " + bootMarker + "\n"
	next, changed, err := editBootCrontab(old, true)
	if err != nil || !changed {
		t.Fatalf("changed=%v err=%v", changed, err)
	}
	if strings.Count(next, bootMarker) != 1 {
		t.Errorf("the marker is not unique any more:\n%s", next)
	}
	if !strings.Contains(next, bootCommand) {
		t.Errorf("the line was not brought up to date:\n%s", next)
	}
}

// Two marked lines is a REFUSAL. Deleting "the" line when there are two is
// deleting something somebody meant.
func TestBootCrontabRefusesTwoMarkedLines(t *testing.T) {
	two := bootLine() + "\n" + bootLine() + "\n"
	for _, want := range []bool{true, false} {
		if _, _, err := editBootCrontab(two, want); err == nil {
			t.Fatalf("want=%v: two marked lines were accepted", want)
		}
	}
}

// fleetSkip is the whole policy of which rows a fleet run touches, and every
// skip carries a reason an operator can act on.
func TestFleetSkipRules(t *testing.T) {
	row := func(sup, boot, state string) *registry.Tenant {
		t := registry.NewTenant("acme", "acme")
		t.Supervisor, t.DesiredBoot, t.State = sup, boot, state
		return t
	}
	cases := []struct {
		name  string
		verb  string
		t     *registry.Tenant
		act   bool
		saysA string
	}{
		{"systemd enabled starts", "start", row("systemd", "enabled", "active"), true, ""},
		{"instance enabled starts", "start", row("instance", "enabled", "active"), true, ""},
		{"disabled is skipped", "start", row("instance", "disabled", "stopped"), false, "desired_boot"},
		{"manual is skipped", "start", row("manual", "enabled", "active"), false, "handover"},
		{"manual is skipped by stop too", "stop", row("manual", "enabled", "active"), false, "handover"},
		{"stop ignores desired_boot", "stop", row("instance", "disabled", "active"), true, ""},
		{"quarantined is skipped", "stop", row("instance", "enabled", "quarantined"), false, "quarantined"},
	}
	for _, c := range cases {
		why, act := fleetSkip(c.verb, c.t)
		if act != c.act {
			t.Errorf("%s: act=%v want %v (%s)", c.name, act, c.act, why)
			continue
		}
		if !act && !strings.Contains(why, c.saysA) {
			t.Errorf("%s: reason %q does not mention %q", c.name, why, c.saysA)
		}
	}
}

// Display order is what a fleet run follows, and a row the order forgot is
// still started: a registry defect must not leave a tenant down.
func TestFleetOrderFollowsDisplayOrderAndForgetsNobody(t *testing.T) {
	f := registry.LiveFixture()
	extra := registry.NewTenant("zz-orphan", "zz-orphan")
	extra.DataDir = paths.NewRoots("/rag", paths.Overrides{}).DataDir + "/zz-orphan"
	f.Tenants["zz-orphan"] = extra

	got := fleetOrder(f)
	if len(got) != len(f.Tenants) {
		t.Fatalf("fleetOrder returned %d of %d tenants: %v", len(got), len(f.Tenants), got)
	}
	for i, name := range f.DisplayOrder {
		if got[i] != name {
			t.Fatalf("fleetOrder[%d] = %s, want the display order %v", i, got[i], f.DisplayOrder)
		}
	}
	if got[len(got)-1] != "zz-orphan" {
		t.Errorf("the row display_order forgot is not last: %v", got)
	}
	rev := reverseStrings(got)
	if rev[0] != got[len(got)-1] || rev[len(rev)-1] != got[0] {
		t.Errorf("reverseStrings did not reverse: %v", rev)
	}
}

// The three commands exist and refuse the shapes that would be dangerous.
func TestFleetOpsUsageRefusals(t *testing.T) {
	cases := []struct {
		args []string
		rc   int
	}{
		{[]string{"fleet", "start"}, exitUsage},                  // --all is mandatory
		{[]string{"fleet", "stop"}, exitUsage},                   // and for stop especially
		{[]string{"fleet", "start", "--all", "acme"}, exitUsage}, // no tenant names
		{[]string{"fleet", "stop", "--all"}, exitRefused},        // confirm the SCOPE first
		{[]string{"fleet", "enable-boot"}, exitUsage},            // --cron or --no-cron
		{[]string{"fleet", "enable-boot", "--cron", "--no-cron"}, exitUsage},
	}
	for _, c := range cases {
		if rc, _, _ := capture(t, c.args...); rc != c.rc {
			t.Errorf("%v: rc %d, want %d", c.args, rc, c.rc)
		}
	}
}
