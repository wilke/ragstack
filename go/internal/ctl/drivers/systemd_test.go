package drivers

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// newSystemd is a RealSystemd pointed at a stub `systemctl`.
func newSystemd(t *testing.T) (*RealSystemd, *stub) {
	t.Helper()
	s := newStub(t, keyFirstWord)
	return &RealSystemd{run: &runner{}, Bin: s.Path}, s
}

func TestSystemdRefusesEveryUnitNameOutsideTheAllowlist(t *testing.T) {
	ctx := context.Background()
	// Each of these is a name somebody could get into a registry row, a
	// rendered unit file or a CLI argument. None of them is a unit this
	// control plane manages, and the driver must refuse before systemctl sees
	// it: `stop sshd.service` in this account's manager is the whole reason
	// the allowlist exists.
	for _, unit := range []string{
		"sshd.service", "ssh-agent.service", "ragstack.service",
		"ragstack-dev-redis.service", "ragstack-Dev-api.service", "ragstack--api.service",
		"ragstack-dev-api.service extra", "ragstack-dev.target\n", "../../ragstack-dev.target",
		"ragstack-dev-api.service;stop", "", "*",
	} {
		d, stub := newSystemd(t)
		calls := []struct {
			name string
			err  error
		}{
			{"Start", d.Start(ctx, unit)},
			{"Stop", d.Stop(ctx, unit)},
			{"Enable", d.Enable(ctx, unit)},
			{"Disable", d.Disable(ctx, unit)},
			{"ResetFailed", d.ResetFailed(ctx, unit)},
		}
		_, activeErr := d.IsActive(ctx, unit)
		_, enabledErr := d.IsEnabled(ctx, unit)
		_, showErr := d.Show(ctx, unit)
		calls = append(calls,
			struct {
				name string
				err  error
			}{"IsActive", activeErr},
			struct {
				name string
				err  error
			}{"IsEnabled", enabledErr},
			struct {
				name string
				err  error
			}{"Show", showErr})
		for _, c := range calls {
			if !errors.Is(c.err, jobs.ErrRefused) {
				t.Errorf("%s(%q) = %v, want a refusal", c.name, unit, c.err)
			}
		}
		stub.ranNothing("a unit outside the allowlist")
	}
}

func TestSystemdAcceptsTheUnitsTheRendererProduces(t *testing.T) {
	ctx := context.Background()
	for _, unit := range []string{
		"ragstack-dev.target", "ragstack-dev-qdrant.service", "ragstack-dev-es.service",
		"ragstack-dev-postgres.service", "ragstack-dev-api.service", "ragstack-dev-ui.service",
		"ragstack-ctltest-20260914t101500.target", "ragstack-a.target",
	} {
		d, stub := newSystemd(t)
		if err := d.Start(ctx, unit); err != nil {
			t.Fatalf("Start(%q) = %v", unit, err)
		}
		if got := stub.argv(); len(got) != 1 || got[0] != "--user start "+unit {
			t.Errorf("Start(%q) ran %v", unit, got)
		}
	}
}

func TestSystemdVerbsBuildTheArgvSystemctlExpects(t *testing.T) {
	ctx := context.Background()
	const unit = "ragstack-dev-api.service"
	d, stub := newSystemd(t)
	if err := d.DaemonReload(ctx); err != nil {
		t.Fatal(err)
	}
	if err := d.Stop(ctx, unit); err != nil {
		t.Fatal(err)
	}
	if err := d.Enable(ctx, unit); err != nil {
		t.Fatal(err)
	}
	if err := d.Disable(ctx, unit); err != nil {
		t.Fatal(err)
	}
	if err := d.ResetFailed(ctx, unit); err != nil {
		t.Fatal(err)
	}
	want := []string{
		"--user daemon-reload",
		"--user stop " + unit,
		"--user enable " + unit,
		"--user disable " + unit,
		"--user reset-failed " + unit,
	}
	got := stub.argv()
	if len(got) != len(want) {
		t.Fatalf("argv = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("call %d = %q, want %q", i, got[i], want[i])
		}
	}
}

func TestSystemdRefusesAVerbThatIsNotOnTheAllowlist(t *testing.T) {
	d, stub := newSystemd(t)
	// `kill`, `mask` and `switch-root` are systemctl verbs this driver must
	// never run; the allowlist is checked in the one place every method goes
	// through, so a future method cannot smuggle one past it.
	if _, err := d.systemctl(context.Background(), "kill", "ragstack-dev-api.service"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("systemctl kill = %v, want a refusal", err)
	}
	stub.ranNothing("a verb outside the allowlist")
}

func TestSystemdShowParsesKeyValueLines(t *testing.T) {
	d, stub := newSystemd(t)
	stub.respond("show", strings.Join([]string{
		"ActiveState=active",
		"SubState=running",
		"Result=success",
		"UnitFileState=enabled",
		"FragmentPath=/rag/config/ctl/units/ragstack-dev-api.service",
		"MainPID=4242",
		"NRestarts=2",
		"ExecMainStatus=0",
	}, "\n")+"\n", "", 0)

	info, err := d.Show(context.Background(), "ragstack-dev-api.service")
	if err != nil {
		t.Fatalf("Show = %v", err)
	}
	want := jobs.UnitInfo{
		ActiveState: "active", SubState: "running", Result: "success",
		UnitFileState: "enabled", FragmentPath: "/rag/config/ctl/units/ragstack-dev-api.service",
		MainPID: 4242, NRestarts: 2, ExecMainStatus: 0,
	}
	if info != want {
		t.Errorf("Show = %+v, want %+v", info, want)
	}
	// Every property is asked for by name, so a systemd that does not know one
	// omits its LINE rather than shifting every later value by one field.
	if argv := stub.argv(); len(argv) != 1 || !strings.Contains(argv[0], "-p ActiveState") || strings.Contains(argv[0], "--value") {
		t.Errorf("Show ran %v; it must name each property and must not use --value", argv)
	}
}

func TestSystemdShowOfAnUnknownUnitIsZeroAndNotAnError(t *testing.T) {
	d, stub := newSystemd(t)
	// This is what `systemctl show` answers for a unit the manager never
	// loaded: exit 0, no FragmentPath. decommission's post-check and the
	// selftest read that emptiness as "the units are gone".
	stub.respond("show", "ActiveState=inactive\nSubState=dead\nFragmentPath=\nUnitFileState=\n", "", 0)
	info, err := d.Show(context.Background(), "ragstack-gone.target")
	if err != nil {
		t.Fatalf("Show of an unknown unit = %v, want no error", err)
	}
	if info != (jobs.UnitInfo{}) {
		t.Errorf("Show of an unknown unit = %+v, want the zero UnitInfo", info)
	}
}

func TestSystemdLinkIsIdempotentWhenTheUnitAlreadyResolvesToThatFile(t *testing.T) {
	ctx := context.Background()
	const path = "/rag/config/ctl/units/ragstack-dev-api.service"

	// Already linked to this exact path: nothing to run. `systemctl link` of
	// such a unit fails on some versions, and a retried `tenant create` must
	// not fail there.
	d, stub := newSystemd(t)
	stub.respond("show", "FragmentPath="+path+"\nActiveState=inactive\n", "", 0)
	if err := d.Link(ctx, path); err != nil {
		t.Fatalf("Link of an already-linked unit = %v, want success", err)
	}
	for _, call := range stub.argv() {
		if strings.Contains(call, " link ") {
			t.Errorf("Link ran %q for a unit that already resolves to that path", call)
		}
	}

	// Linked somewhere else — or not known at all — and the link runs.
	d, stub = newSystemd(t)
	stub.respond("show", "FragmentPath=/usr/lib/systemd/user/ragstack-dev-api.service\n", "", 0)
	if err := d.Link(ctx, path); err != nil {
		t.Fatalf("Link = %v", err)
	}
	if got := stub.argv(); len(got) != 2 || got[1] != "--user link "+path {
		t.Errorf("Link ran %v, want a show then a link", got)
	}
}

func TestSystemdLinkRefusesAPathThatIsNotAManagedUnit(t *testing.T) {
	ctx := context.Background()
	for _, path := range []string{
		"units/ragstack-dev-api.service",                   // relative
		"/rag/config/ctl/units/../../../etc/passwd",        // not clean
		"/usr/lib/systemd/user/sshd.service",               // not a managed name
		"/rag/config/ctl/units/ragstack-dev-redis.service", // not a managed store
	} {
		d, stub := newSystemd(t)
		if err := d.Link(ctx, path); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Link(%q) = %v, want a refusal", path, err)
		}
		stub.ranNothing("Link of " + filepath.Base(path))
	}
}

func TestSystemdIsActiveMapsExitStatus(t *testing.T) {
	for _, c := range []struct {
		out     string
		code    int
		want    bool
		wantErr bool
	}{
		{"active\n", 0, true, false},
		{"inactive\n", 3, false, false},
		{"failed\n", 3, false, false},
		{"activating\n", 3, false, false},
		{"deactivating\n", 3, false, false},
		{"unknown\n", 3, false, false},
		{"", 1, false, false}, // an unknown unit: no output, non-zero
		// Anything else with a non-zero exit is a systemctl that could not
		// answer — a dead bus, a manager that is not running. Reporting THAT
		// as "the unit is stopped" would let a job conclude a tenant is down
		// when nobody asked the manager anything.
		{"Failed to connect to bus: No such file or directory\n", 1, false, true},
	} {
		d, stub := newSystemd(t)
		stub.respond("is-active", c.out, "", c.code)
		got, err := d.IsActive(context.Background(), "ragstack-dev-api.service")
		if (err != nil) != c.wantErr {
			t.Errorf("IsActive with %q/%d = err %v, wantErr %v", c.out, c.code, err, c.wantErr)
			continue
		}
		if got != c.want {
			t.Errorf("IsActive with %q/%d = %v, want %v", c.out, c.code, got, c.want)
		}
	}
}

func TestSystemdIsEnabledMapsExitStatus(t *testing.T) {
	for _, c := range []struct {
		out     string
		code    int
		want    bool
		wantErr bool
	}{
		{"enabled\n", 0, true, false},
		{"enabled-runtime\n", 0, true, false},
		// static, indirect and generated exit 0 but are systemd's own
		// bookkeeping, not a desired_boot an operator set.
		{"static\n", 0, false, false},
		{"indirect\n", 0, false, false},
		{"disabled\n", 1, false, false},
		{"masked\n", 1, false, false},
		// A LINKED unit is known and startable but nothing starts it at boot,
		// which is exactly the question this method answers.
		{"linked\n", 1, false, false},
		{"", 1, false, false},
		{"Failed to connect to bus\n", 1, false, true},
	} {
		d, stub := newSystemd(t)
		stub.respond("is-enabled", c.out, "", c.code)
		got, err := d.IsEnabled(context.Background(), "ragstack-dev.target")
		if (err != nil) != c.wantErr {
			t.Errorf("IsEnabled with %q/%d = err %v, wantErr %v", c.out, c.code, err, c.wantErr)
			continue
		}
		if got != c.want {
			t.Errorf("IsEnabled with %q/%d = %v, want %v", c.out, c.code, got, c.want)
		}
	}
}

func TestSystemdReportsAFailedVerbWithTheProgramAndTheStderr(t *testing.T) {
	d, stub := newSystemd(t)
	stub.respond("start", "", "Job for ragstack-dev-api.service failed because the control process exited\n", 1)
	err := d.Start(context.Background(), "ragstack-dev-api.service")
	if err == nil {
		t.Fatal("a systemctl that exited 1 produced no error")
	}
	if !strings.Contains(err.Error(), "prog exited 1") || !strings.Contains(err.Error(), "control process exited") {
		t.Errorf("the error does not say what happened: %v", err)
	}
}
