package render

import (
	"fmt"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The seam's one load-bearing assertion: the unit's ExecStart IS the rendered
// StoreArgv, for every store leg.
//
// The instance supervisor builds its `apptainer instance run` from StoreArgv.
// If the two ever drift — a bind added to a unit template and not to the
// factored function — a tenant's store starts under one supervisor with its
// data visible and under the other without, and the failure surfaces at the
// tenant's first query rather than at its start. Asserting the equality here
// is what makes that impossible rather than unlikely.
func TestUnitExecStartIsExactlyStoreArgv(t *testing.T) {
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	postgresLocal(tn)
	cfg := UnitConfig{}
	units, err := Units(tn, cfg)
	if err != nil {
		t.Fatal(err)
	}
	// withDefaults is what Units applies, so the apptainer path the assertion
	// uses is the one the unit was rendered with.
	apptainer := cfg.withDefaults().ApptainerBin

	for _, c := range []struct{ leg, unit, instance string }{
		{LegQdrant, "ragstack-sandbox-qdrant.service", "qdrant-sandbox"},
		{LegES, "ragstack-sandbox-es.service", "elasticsearch-sandbox"},
		{LegPostgres, "ragstack-sandbox-postgres.service", "postgres-sandbox"},
	} {
		t.Run(c.leg, func(t *testing.T) {
			st, err := StoreArgv(tn, c.leg, cfg)
			if err != nil {
				t.Fatal(err)
			}
			want := "ExecStart=" + apptainer + " run --no-home " + st.CommandLine()
			body, ok := units[c.unit]
			if !ok {
				t.Fatalf("no unit %s", c.unit)
			}
			got := execStartOf(t, string(body))
			if got != want {
				t.Errorf("the unit and StoreArgv disagree\n unit: %s\n argv: %s", got, want)
			}
			// The instance name is the one the hand-started tenants already
			// run under, so an adopted tenant's live instances are the ones
			// the ctl manages after handover.
			if st.Instance != c.instance {
				t.Errorf("instance name = %q, want %q", st.Instance, c.instance)
			}
			// Argv is the same command for exec: one element per argument,
			// nothing quoted.
			if n := len(st.Argv()); n < 3 {
				t.Errorf("Argv() = %v, too short to be a command", st.Argv())
			}
			// The unit still writes its own `Environment=` lines for
			// apptainer's two directories; ProcessEnv has to agree with them,
			// or the instance supervisor would pull images into a different
			// cache than the unit does — and on a rootless host with no cache
			// dir, into a home directory nothing in production may reference.
			for _, k := range []string{"APPTAINER_CACHEDIR", "APPTAINER_CONFIGDIR"} {
				want := "Environment=" + k + "=" + st.ProcessEnv[k]
				if !strings.Contains(string(body), want) {
					t.Errorf("the unit lacks %q; ProcessEnv and the unit disagree", want)
				}
			}
			// The password's variable is asked for by NAME and never as an
			// Environment= line: that file is in a 0755 directory.
			if _, ok := st.ProcessEnv[APPTAINERENVPostgresPassword]; ok &&
				strings.Contains(string(body), "Environment="+APPTAINERENVPostgresPassword) {
				t.Error("the postgres password's variable became a unit Environment= line")
			}
		})
	}
}

// execStartOf returns the single ExecStart= line of a unit.
func execStartOf(t *testing.T, unit string) string {
	t.Helper()
	var found []string
	for _, line := range strings.Split(unit, "\n") {
		if strings.HasPrefix(line, "ExecStart=") {
			found = append(found, line)
		}
	}
	if len(found) != 1 {
		t.Fatalf("want exactly one ExecStart line, got %d: %v", len(found), found)
	}
	return found[0]
}

// The api half of the same seam: the unit's ExecStart and its `Environment=`
// lines are APIArgv's, so a supervisor that spawns the process itself starts
// the same program with the same environment.
func TestAPIUnitIsExactlyAPIArgv(t *testing.T) {
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	cfg := UnitConfig{}
	units, err := Units(tn, cfg)
	if err != nil {
		t.Fatal(err)
	}
	program, args, env, err := APIArgv(tn, cfg)
	if err != nil {
		t.Fatal(err)
	}
	api := string(units["ragstack-sandbox-api.service"])
	if got, want := execStartOf(t, api), "ExecStart="+APICommandLine(program, args); got != want {
		t.Errorf("the api unit and APIArgv disagree\n unit: %s\n argv: %s", got, want)
	}
	// Every Environment= line in the unit is one of APIArgv's, and every one
	// of APIArgv's is in the unit — the same set, in the same order.
	var inUnit []string
	for _, line := range strings.Split(api, "\n") {
		if strings.HasPrefix(line, "Environment=") {
			inUnit = append(inUnit, strings.TrimPrefix(line, "Environment="))
		}
	}
	if strings.Join(inUnit, "|") != strings.Join(env, "|") {
		t.Errorf("the api unit's Environment lines are %v, APIArgv says %v", inUnit, env)
	}
	// The secret-bearing half is deliberately NOT here: the unit gets
	// tenant.env and secrets.env through EnvironmentFile, and a supervisor
	// that spawns the process parses those two itself.
	for _, e := range env {
		if strings.Contains(e, "API_KEY") || strings.Contains(e, "PASSWORD") {
			t.Errorf("APIArgv returned a secret-class variable: %s", e)
		}
	}
	if !strings.Contains(api, "EnvironmentFile=-/rag/data/tenants/sandbox/config/secrets.env") {
		t.Error("the api unit no longer reads secrets.env")
	}
}

// The postgres password reaches the container through the CHILD's environment
// and never through the argv, which is the lesson render/units.go's postgres
// comment records. StoreArgv has to preserve it, and a renderer must not see
// the value at all.
func TestStoreArgvKeepsThePostgresPasswordOffTheArgv(t *testing.T) {
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	postgresLocal(tn)
	st, err := StoreArgv(tn, LegPostgres, UnitConfig{})
	if err != nil {
		t.Fatal(err)
	}
	line := st.CommandLine()
	for _, forbidden := range []string{"POSTGRES_PASSWORD", "TENANT_PG_PASSWORD", "PGPASSWORD"} {
		if strings.Contains(line, forbidden) {
			t.Errorf("the command line names %s: %s", forbidden, line)
		}
	}
	v, ok := st.ProcessEnv[APPTAINERENVPostgresPassword]
	if !ok {
		t.Fatalf("ProcessEnv does not ask for %s; the container would never get a password",
			APPTAINERENVPostgresPassword)
	}
	if v != "" {
		t.Errorf("%s = %q, want empty: the VALUE comes from secrets.env at run time, and a renderer that "+
			"held it would eventually print it", APPTAINERENVPostgresPassword, v)
	}
	// The two apptainer directories are there too, and under the ctl's state
	// dir rather than a home directory.
	for _, k := range []string{"APPTAINER_CACHEDIR", "APPTAINER_CONFIGDIR"} {
		if p := st.ProcessEnv[k]; !strings.HasPrefix(p, "/rag/data/ctl/apptainer/") {
			t.Errorf("%s = %q, want a path under the ctl state dir", k, p)
		}
	}
}

func TestStoreArgvRefusesWhatTheCtlDoesNotSupervise(t *testing.T) {
	base := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)

	// A leg that is not a leg.
	if _, err := StoreArgv(base, "redis", UnitConfig{}); err == nil {
		t.Error("an unknown leg was accepted")
	}

	// A shared store is somebody else's process.
	shared := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	shared.Stores.Qdrant.Ownership = registry.OwnershipShared
	if _, err := StoreArgv(shared, LegQdrant, UnitConfig{}); err == nil {
		t.Error("a shared qdrant was accepted: the ctl does not supervise what it does not own")
	}

	// sqlite state runs no server at all.
	if _, err := StoreArgv(base, LegPostgres, UnitConfig{}); err == nil {
		t.Error("a sqlite tenant produced a postgres command")
	}

	// An exclusive store with no image.
	noSIF := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	noSIF.Stores.Elasticsearch.SIF = ""
	if _, err := StoreArgv(noSIF, LegES, UnitConfig{}); err == nil {
		t.Error("an exclusive elasticsearch with no sif was accepted")
	}

	// And the whole-tenant validation is the units': a tenant Units refuses,
	// StoreArgv refuses too, so the two supervisors cannot disagree about
	// which rows are renderable.
	bad := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	bad.DataDir = "/rag/data/tenants/other"
	if _, unitErr := Units(bad, UnitConfig{}); unitErr == nil {
		t.Fatal("the off-layout fixture no longer trips checkTenant; this test proves nothing")
	}
	if _, err := StoreArgv(bad, LegQdrant, UnitConfig{}); err == nil {
		t.Error("StoreArgv rendered a tenant Units refuses")
	}
}

// The quoting CommandLine applies is the units' own, and it is by role: an
// --env pair with a space is double-quoted (systemd parses it as one
// argument), a runscript argument with a space is single-quoted (the `sh -c`
// inside the container sees it verbatim). Both forms are live in the fixture.
func TestCommandLineQuotesTheWayTheUnitsDo(t *testing.T) {
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	es, err := StoreArgv(tn, LegES, UnitConfig{})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(es.CommandLine(), `--env "ES_JAVA_OPTS=-Xms1g -Xmx1g"`) {
		t.Errorf("the ES heap pair is not double-quoted: %s", es.CommandLine())
	}
	q, err := StoreArgv(tn, LegQdrant, UnitConfig{})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(q.CommandLine(), `/bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'`) {
		t.Errorf("the qdrant runscript argument is not single-quoted: %s", q.CommandLine())
	}
	// Argv is the unquoted form: the same argument, as exec takes it.
	if got := q.Argv()[len(q.Argv())-1]; got != "cd /qdrant && exec ./entrypoint.sh" {
		t.Errorf("Argv's last element = %q, want it unquoted", got)
	}
	// EnvOrder is what makes the command reproducible: a map has no order and
	// the units were written by hand.
	if strings.Join(q.EnvOrder, ",") != "QDRANT__SERVICE__HTTP_PORT,QDRANT__SERVICE__GRPC_PORT" {
		t.Errorf("EnvOrder = %v, want the order the unit renders", q.EnvOrder)
	}
	for _, k := range q.EnvOrder {
		if _, ok := q.Env[k]; !ok {
			t.Errorf("EnvOrder names %s, which Env does not hold", k)
		}
	}
	if len(q.EnvOrder) != len(q.Env) {
		t.Errorf("EnvOrder covers %d of %d Env keys", len(q.EnvOrder), len(q.Env))
	}
}

// The ports a store is told to listen on are the tenant's block, in the argv
// and nowhere else — the instance supervisor has no other source for them.
func TestStoreArgvCarriesTheBlockPorts(t *testing.T) {
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	postgresLocal(tn)
	for _, c := range []struct {
		leg  string
		want []string
	}{
		{LegQdrant, []string{
			fmt.Sprintf("--env QDRANT__SERVICE__HTTP_PORT=%d", tn.Ports.QdrantHTTP),
			fmt.Sprintf("--env QDRANT__SERVICE__GRPC_PORT=%d", tn.Ports.QdrantGRPC),
		}},
		{LegES, []string{
			fmt.Sprintf("-Ehttp.port=%d", tn.Ports.ESHTTP),
			fmt.Sprintf("-Etransport.port=%d", tn.Ports.ESTransport),
		}},
		{LegPostgres, []string{fmt.Sprintf("port=%d", tn.Ports.PG), "listen_addresses=127.0.0.1"}},
	} {
		st, err := StoreArgv(tn, c.leg, UnitConfig{})
		if err != nil {
			t.Fatalf("%s: %v", c.leg, err)
		}
		for _, w := range c.want {
			if !strings.Contains(st.CommandLine(), w) {
				t.Errorf("%s command lacks %q: %s", c.leg, w, st.CommandLine())
			}
		}
	}
}
