package adopt

import (
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// confirmFixture is a tenant shaped like the adopted ones: its own qdrant and
// elasticsearch on its block, a dedicated postgres, and a host on which each
// is served by exactly one apptainer instance binding a path under the
// tenant's data dir.
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
	host := &hostfacts.Fake{Ports: []hostfacts.Listener{
		instanceListener(block.QdrantHTTP, 101, data+"/qdrant/storage", "/qdrant/storage"),
		instanceListener(block.ESHTTP, 102, data+"/elasticsearch/data", "/usr/share/elasticsearch/data"),
		instanceListener(block.PG, 103, data+"/postgres/data", "/var/lib/postgresql/data"),
	}}
	return t, host
}

// instanceListener is one apptainer instance holding a port, with the argv
// shape the live instances have (`--bind host:container`).
func instanceListener(port, pid int, hostPath, containerPath string) hostfacts.Listener {
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
	out := ""
	for n > 0 {
		out = string(rune('0'+n%10)) + out
		n /= 10
	}
	return out
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
				h.Ports = append(h.Ports, instanceListener(block.QdrantHTTP, 999,
					"/rag/data/tenants/hack/qdrant/storage", "/qdrant/storage"))
			},
			want: "2 different processes",
		},
		{
			name:   "owner unreadable",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) { h.Ports[0].Pid = 0 },
			want:   "cannot attribute",
		},
		{
			name: "binds somebody else's storage",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) {
				h.Ports[0] = instanceListener(block.QdrantHTTP, 101,
					"/rag/data/tenants/other/qdrant/storage", "/qdrant/storage")
			},
			want: "none of which is under this tenant's data dir",
		},
		{
			name: "no bind at all",
			break_: func(_ *registry.Tenant, h *hostfacts.Fake) {
				h.Ports[0].Cmdline = []string{"/usr/bin/qdrant"}
			},
			want: "no --bind at all",
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
