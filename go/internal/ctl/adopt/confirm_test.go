package adopt

import (
	"errors"
	"os"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// confirmFixture is a tenant shaped like the adopted ones on coconut: its own
// qdrant and elasticsearch on its block, a dedicated postgres, and a host on
// which each is served by the process INSIDE an apptainer instance.
//
// That last part is the whole point of the fixture. The recorded argv of every
// live store leg is the container's own — `./qdrant`, the elasticsearch JVM,
// `postgres` — with no `--bind` anywhere, because the `apptainer instance run
// --bind …` that set the mounts up is a different process that exited long
// ago. A fixture whose listeners carried `--bind` (this one did) let a
// confirmation pass in the tests and refuse on every real tenant.
func confirmFixture() (*registry.Tenant, *hostfacts.Fake) {
	block := paths.Block(4)
	data := "/rag/data/tenants/hack"
	t := registry.NewTenant("hack", "hack")
	t.DataDir, t.Worktree, t.Ports = data, "/rag/repos/tenants/hack", block
	t.Stores.Qdrant = registry.Qdrant{
		Ownership: registry.OwnershipExclusive, ExtraEnv: map[string]string{},
		URL: "http://localhost:" + itoa(block.QdrantHTTP), Instance: "qdrant-hack",
	}
	t.Stores.Elasticsearch = registry.Elasticsearch{
		Ownership: registry.OwnershipExclusive, ExtraEnv: map[string]string{},
		URL: "http://localhost:" + itoa(block.ESHTTP), Instance: "elasticsearch-hack",
	}
	t.Stores.Postgres = registry.Postgres{
		Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
		Port: registry.NullPort(block.PG), Instance: "postgres-hack",
		DataDir: registry.NullString(data + "/postgres"),
	}
	host := &hostfacts.Fake{
		Ports: []hostfacts.Listener{
			containedListener(block.QdrantHTTP, 101, "./qdrant"),
			containedListener(block.ESHTTP, 102, "/usr/share/elasticsearch/jdk/bin/java", "-Xms1g", "org.elasticsearch.bootstrap.Elasticsearch"),
			containedListener(block.PG, 103, "postgres"),
		},
		// What /proc/<pid>/mountinfo says, translated to host paths — the
		// shape hostfacts.StorageBinds returns for the live instances, down to
		// the incidental mounts (/etc/hosts, /var/tmp) every apptainer
		// container carries and the broad `/rag` bind two of them have.
		Binds: map[int][]hostfacts.StorageBind{
			101: {
				{HostPath: "/etc/hosts", ContainerPath: "/etc/hosts", Device: "252:0"},
				{HostPath: data + "/qdrant/snapshots", ContainerPath: "/qdrant/snapshots", Device: "252:8"},
				{HostPath: data + "/qdrant/storage", ContainerPath: "/qdrant/storage", Device: "252:8"},
				{HostPath: "/var/tmp", ContainerPath: "/var/tmp", Device: "252:1"},
			},
			102: {
				{HostPath: "/rag", ContainerPath: "/rag", Device: "252:8"},
				{HostPath: data + "/elasticsearch/config", ContainerPath: "/usr/share/elasticsearch/config", Device: "252:8"},
				{HostPath: data + "/elasticsearch/data", ContainerPath: "/usr/share/elasticsearch/data", Device: "252:8"},
				{HostPath: data + "/elasticsearch/logs", ContainerPath: "/usr/share/elasticsearch/logs", Device: "252:8"},
			},
			103: {
				{HostPath: data + "/postgres/run", ContainerPath: "/run/postgresql", Device: "252:8"},
				{HostPath: data + "/postgres/data", ContainerPath: "/var/lib/postgresql/data", Device: "252:8"},
			},
		},
	}
	return t, host
}

// containedListener is a process holding a port from INSIDE a container: a
// plausible argv and no bind on it anywhere.
func containedListener(port, pid int, argv ...string) hostfacts.Listener {
	return hostfacts.Listener{
		Port: port, Addr: "0.0.0.0", Pid: pid, UID: 1000, User: "wilke", Cmdline: argv,
	}
}

// starterListener is the OTHER shape: a process that is itself the `apptainer
// instance run`, whose binds are on its command line. It is the fallback path
// storageUnderDataDir keeps, and a caller outside this host may still be in it.
func starterListener(port, pid int, hostPath, containerPath string) hostfacts.Listener {
	return hostfacts.Listener{
		Port: port, Addr: "0.0.0.0", Pid: pid, UID: 1000, User: "wilke",
		Cmdline: []string{
			"/usr/bin/apptainer", "instance", "run", "--no-home",
			"--bind", hostPath + ":" + containerPath,
			"/rag/apptainer/images/qdrant.sif", "qdrant-hack",
		},
	}
}

func itoa(n int) string {
	return strconv.Itoa(n)
}

func TestConfirmStoresSetsTheThreeCapabilitiesAndNeverPurge(t *testing.T) {
	tn, host := confirmFixture()
	findings, err := ConfirmStores(tn, []string{"qdrant", "elasticsearch", "postgres"}, ConfirmOptions{Host: host})
	if err != nil {
		t.Fatalf("ConfirmStores: %v", err)
	}
	for _, c := range []struct {
		leg  string
		caps registry.Capabilities
	}{
		{"qdrant", tn.Stores.Qdrant.Capabilities},
		{"elasticsearch", tn.Stores.Elasticsearch.Capabilities},
		{"postgres", tn.Stores.Postgres.Capabilities},
	} {
		if !c.caps.Stop || !c.caps.Snapshot || !c.caps.Restore {
			t.Errorf("%s capabilities = %+v, want stop/snapshot/restore", c.leg, c.caps)
		}
		if c.caps.Purge {
			t.Errorf("%s: purge was set. It is the capability that destroys data and nothing asks for it", c.leg)
		}
	}
	// The evidence is reported, so an operator can see what was actually read.
	if len(findings) != 3 {
		t.Fatalf("findings = %d, want one per leg: %+v", len(findings), findings)
	}
	for _, f := range findings {
		if f.Code != "stores_confirmed" || string(f.Tenant) != "hack" {
			t.Errorf("finding = %+v", f)
		}
		if !strings.Contains(f.Detail, "pid ") || !strings.Contains(f.Detail, "/rag/data/tenants/hack") {
			t.Errorf("finding does not carry the evidence: %q", f.Detail)
		}
	}
}

// A shared leg is refused before any process is looked at: no amount of
// evidence makes "the ctl may stop a store other tenants use" safe.
func TestConfirmStoresRefusesASharedLeg(t *testing.T) {
	tn, host := confirmFixture()
	tn.Stores.Qdrant.Ownership = registry.OwnershipShared
	tn.Stores.Qdrant.URL = "http://localhost:6333"
	_, err := ConfirmStores(tn, []string{"qdrant"}, ConfirmOptions{Host: host})
	if err == nil || !strings.Contains(err.Error(), "not exclusive") {
		t.Fatalf("a shared leg = %v, want a refusal saying it is not exclusive", err)
	}
	if tn.Stores.Qdrant.Capabilities.Stop {
		t.Error("a refused confirmation wrote a capability anyway")
	}
}

func TestConfirmStoresRefusesWhatItCannotVerify(t *testing.T) {
	block := paths.Block(4)
	data := "/rag/data/tenants/hack"
	for _, c := range []struct {
		name   string
		break_ func(*registry.Tenant, *hostfacts.Fake)
		want   string
	}{
		{
			name:   "nothing listening",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) { h.Ports = nil },
			want:   "nothing is listening",
		},
		{
			name: "two processes on one port",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) {
				h.Ports = append(h.Ports, containedListener(block.QdrantHTTP, 999, "./qdrant"))
			},
			want: "2 different processes",
		},
		{
			name:   "owner unreadable",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) { h.Ports[0].Pid = 0 },
			want:   "cannot attribute",
		},
		{
			name: "mounts somebody else's storage",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) {
				h.Binds[101] = []hostfacts.StorageBind{
					{HostPath: "/rag/data/tenants/other/qdrant/storage", ContainerPath: "/qdrant/storage"},
				}
			},
			want: "mounts nothing under this tenant's data dir",
		},
		{
			// The anomaly worth seeing: something of the tenant's IS mounted,
			// but not where a qdrant keeps its collections. Its storage is
			// somewhere this tenant does not own.
			name: "mounts the tenant's snapshots but not its storage",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) {
				h.Binds[101] = []hostfacts.StorageBind{
					{HostPath: data + "/qdrant/snapshots", ContainerPath: "/qdrant/snapshots"},
				}
			},
			want: "its storage is somewhere else",
		},
		{
			name: "no mounts and no bind at all",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) {
				h.Binds[101] = nil
				h.Ports[0].Cmdline = []string{"/usr/bin/qdrant"}
			},
			want: "has no mounts and no --bind",
		},
		{
			name: "the mount table cannot be read",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) {
				h.Errs = map[string]error{"storagebinds": errors.New("permission denied")}
			},
			want: "reading the mounts of pid 101",
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			tn, host := confirmFixture()
			c.break_(tn, host)
			_, err := ConfirmStores(tn, []string{"qdrant"}, ConfirmOptions{Host: host})
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("err = %v, want a refusal mentioning %q", err, c.want)
			}
			if tn.Stores.Qdrant.Capabilities.Stop {
				t.Error("a refused confirmation wrote a capability anyway")
			}
		})
	}
}

// The other shape a store can be in: the process holding the port IS the
// `apptainer instance run`, so its binds are on its command line. mountinfo
// says nothing for it, and the argv fallback is what carries the confirmation.
func TestConfirmStoresFallsBackToTheCommandLine(t *testing.T) {
	tn, host := confirmFixture()
	block := paths.Block(4)
	host.Ports[0] = starterListener(block.QdrantHTTP, 101,
		"/rag/data/tenants/hack/qdrant/storage", "/qdrant/storage")
	host.Binds[101] = nil

	findings, err := ConfirmStores(tn, []string{"qdrant"}, ConfirmOptions{Host: host})
	if err != nil {
		t.Fatalf("a starter process with its binds on the argv was refused: %v", err)
	}
	if !tn.Stores.Qdrant.Capabilities.Stop {
		t.Error("the capability was not set")
	}
	if len(findings) != 1 || !strings.Contains(findings[0].Detail, "from the command line") {
		t.Errorf("the finding does not say which source the evidence came from: %+v", findings)
	}
}

// Two registry rows on one port is the definition of shared, whatever either
// row's `ownership` field says — and the row that would be confirmed is
// exactly the one that would then be allowed to stop it.
func TestConfirmStoresRefusesAPortAnotherRowClaims(t *testing.T) {
	tn, host := confirmFixture()
	other := registry.NewTenant("neighbour", "neighbour")
	other.Stores.Qdrant.URL = tn.Stores.Qdrant.URL
	f := registry.NewFleet("/rag")
	f.Tenants["neighbour"] = other

	_, err := ConfirmStores(tn, []string{"qdrant"}, ConfirmOptions{Host: host, Fleet: f})
	if err == nil || !strings.Contains(err.Error(), "neighbour") {
		t.Fatalf("err = %v, want a refusal naming the other row", err)
	}
	// A DECOMMISSIONED row does not claim anything: its ports are tombstoned
	// and nothing of it is running.
	other.State = "decommissioned"
	if _, err := ConfirmStores(tn, []string{"qdrant"}, ConfirmOptions{Host: host, Fleet: f}); err != nil {
		t.Errorf("a decommissioned row blocked a confirmation: %v", err)
	}
}

// All-or-nothing: a batch in which one leg fails leaves every capability as it
// found it, because a half-confirmed tenant is one whose `fleet start` brings
// up some of its stores.
func TestConfirmStoresIsAllOrNothing(t *testing.T) {
	tn, host := confirmFixture()
	host.Ports = host.Ports[:2] // postgres has no listener
	_, err := ConfirmStores(tn, []string{"qdrant", "elasticsearch", "postgres"}, ConfirmOptions{Host: host})
	if err == nil {
		t.Fatal("a batch with an unverifiable leg succeeded")
	}
	for _, caps := range []registry.Capabilities{
		tn.Stores.Qdrant.Capabilities, tn.Stores.Elasticsearch.Capabilities, tn.Stores.Postgres.Capabilities,
	} {
		if caps.Stop || caps.Snapshot || caps.Restore {
			t.Errorf("a failed batch left a capability set: %+v", caps)
		}
	}
}

// A sqlite (or external) relational store has no server of this tenant's, so
// there is nothing for a confirmation to be about.
func TestConfirmStoresRefusesANonLocalPostgres(t *testing.T) {
	tn, host := confirmFixture()
	tn.Stores.Postgres = registry.SQLiteStore()
	_, err := ConfirmStores(tn, []string{"postgres"}, ConfirmOptions{Host: host})
	if err == nil || !strings.Contains(err.Error(), "not `local`") {
		t.Fatalf("err = %v, want a refusal about the postgres kind", err)
	}
}

func TestValidateLegsRefusesTyposBeforeAnyHostIsRead(t *testing.T) {
	for _, c := range []struct {
		legs []string
		want string
	}{
		{nil, "at least one"},
		{[]string{"es"}, "not a store leg"},
		{[]string{"qdrant", "qdrant"}, "appears twice"},
	} {
		if err := ValidateLegs(c.legs); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("ValidateLegs(%v) = %v, want %q", c.legs, err, c.want)
		}
	}
	if err := ValidateLegs([]string{"qdrant", "elasticsearch", "postgres"}); err != nil {
		t.Errorf("the whole legal set was refused: %v", err)
	}
}

// TestConfirmStoresAgainstTheLiveHost is the reviewer's repro, kept as a
// regression test: the five exclusive legs on coconut, confirmed through the
// real /proc probe rather than a fixture.
//
// It is the test the fixture could not be: the fixture's shape is an assertion
// ABOUT the host, and the bug it missed was that the assertion was wrong. Gated
// on REVIEW_LIVE=1 because it reads this host's live processes and skips
// wherever they are not there — which is every machine but this one.
func TestConfirmStoresAgainstTheLiveHost(t *testing.T) {
	if os.Getenv("REVIEW_LIVE") == "" {
		t.Skip("set REVIEW_LIVE=1 to confirm the live coconut tenants (reads /proc, writes nothing)")
	}
	f, err := registry.Load("/rag/data/tenants/registry.json")
	if err != nil {
		t.Skipf("no live registry: %v", err)
	}
	roots := paths.NewRoots("/rag", paths.Overrides{})
	for _, tc := range []struct {
		tenant, leg string
	}{
		{"dev", "qdrant"}, {"dev", "elasticsearch"},
		{"hackathon", "qdrant"}, {"hackathon", "elasticsearch"}, {"hackathon", "postgres"},
		{"lucid-next", "elasticsearch"},
	} {
		t.Run(tc.tenant+"/"+tc.leg, func(t *testing.T) {
			row := f.Tenants[tc.tenant]
			if row == nil {
				t.Skipf("no %s row on this host", tc.tenant)
			}
			copyOf := *row // ConfirmStores MUTATES; never the loaded registry
			findings, err := ConfirmStores(&copyOf, []string{tc.leg}, ConfirmOptions{Fleet: f, Roots: roots})
			if err != nil {
				t.Fatalf("the live %s leg of %s could not be confirmed: %v", tc.leg, tc.tenant, err)
			}
			if len(findings) != 1 || !strings.Contains(findings[0].Detail, "/rag/data/tenants/") {
				t.Fatalf("the evidence does not name the storage: %+v", findings)
			}
			t.Logf("%s", findings[0].Detail)
		})
	}
}
