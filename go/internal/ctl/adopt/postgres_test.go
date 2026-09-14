package adopt

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The relational store is the one leg the four captured tenants cannot
// exercise: all of them predate `--postgres`, so every question about a
// postgres row has to be asked of a tenant built here. `pgIndex` is the block
// hackathon actually holds on coconut (base 24080, +5 = 24085), so the
// numbers in these tests are the numbers in the live preview.
const (
	pgIndex = 4
	pgPort  = 24085
)

// pgFixture writes a minimal but REAL tenant tree — the config files adopt
// parses, nothing else — with the given provision.env body, and returns the
// roots plus a host where `postgres` holds the block's +5 port.
//
// The listener is the shape the live one has: argv `postgres -c port=<pg> -c
// listen_addresses=127.0.0.1`, owned by wilke.
func pgFixture(t *testing.T, name, provision string, listeners ...hostfacts.Listener) (paths.Roots, *hostfacts.Fake) {
	t.Helper()
	roots := paths.NewRoots(t.TempDir(), paths.Overrides{})
	block := paths.Block(pgIndex)
	dataDir := filepath.Join(roots.DataDir, name)
	worktree := filepath.Join(roots.ReposDir, name)
	for _, d := range []string{roots.ImagesDir, worktree, filepath.Join(dataDir, "config"), filepath.Join(dataDir, "state"), filepath.Join(dataDir, "logs")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	for _, sif := range []string{"qdrant.sif", "elasticsearch.sif", "postgres.sif"} {
		if err := os.WriteFile(filepath.Join(roots.ImagesDir, sif), []byte("sif"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// PORT is how resolvePorts finds the block without a manifest.tsv row.
	tenantEnv := "PORT=" + strconv.Itoa(block.API) + "\n" +
		"QDRANT_URL=http://localhost:" + strconv.Itoa(block.QdrantHTTP) + "\n" +
		"ELASTICSEARCH_URL=http://localhost:" + strconv.Itoa(block.ESHTTP) + "\n" +
		"IDENTITY_PROVIDER=bvbrc\n"
	write := func(rel, body string) {
		if err := os.WriteFile(filepath.Join(dataDir, rel), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(filepath.Join("config", "tenant.env"), tenantEnv)
	write(filepath.Join("config", "provision.env"), provision)

	h := &hostfacts.Fake{
		Self:      "wilke",
		Ports:     listeners,
		Env:       map[int]map[string]string{},
		Gitdirs:   map[string]hostfacts.Gitdir{worktree: {Path: filepath.Join(worktree, ".git"), Location: hostfacts.GitdirOutside}},
		Describes: map[string]string{worktree: "v1.5.3"},
	}
	return roots, h
}

func pgPreview(t *testing.T, roots paths.Roots, h hostfacts.Host, name string) (*registry.Tenant, []model.Finding) {
	t.Helper()
	at := time.Date(2026, 9, 14, 8, 0, 0, 0, time.UTC)
	tenant, findings, err := Preview(roots, name, Options{
		DataDir:  filepath.Join(roots.DataDir, name),
		Worktree: filepath.Join(roots.ReposDir, name),
		Host:     h,
		Now:      func() time.Time { return at },
	})
	if err != nil {
		t.Fatalf("preview %s: %v", name, err)
	}
	return tenant, findings
}

func pgListener() hostfacts.Listener {
	return hostfacts.Listener{
		Port: pgPort, Addr: "127.0.0.1", Pid: 631059, UID: 1000, User: "wilke",
		Cmdline: []string{"postgres", "-c", "port=24085", "-c", "listen_addresses=127.0.0.1"},
	}
}

// TestPreviewPostgresLocalRecordsTheDedicatedInstance is #535's whole point:
// a tenant provisioned with `--postgres local` gets a row naming the instance,
// the port, the SIF and the data at rest — and the process on the +5 port is
// attributed to THIS tenant, so the unexpected_listener warning that fired on
// every pass before is gone.
func TestPreviewPostgresLocalRecordsTheDedicatedInstance(t *testing.T) {
	roots, h := pgFixture(t, "hackathon", "TENANT_ES_HEAP=1g\nTENANT_STORE_KIND=postgres-local\nTENANT_PG_HOST=localhost\nTENANT_PG_PORT=24085\n", pgListener())
	tenant, findings := pgPreview(t, roots, h, "hackathon")

	pg := tenant.Stores.Postgres
	if pg.Kind != registry.PostgresKindLocal {
		t.Fatalf("kind = %q, want %q", pg.Kind, registry.PostgresKindLocal)
	}
	if pg.Ownership != registry.OwnershipExclusive {
		t.Errorf("ownership = %q, want exclusive (the instance and its data dir are this tenant's alone)", pg.Ownership)
	}
	if int(pg.Port) != pgPort {
		t.Errorf("port = %d, want the block's +5 %d", int(pg.Port), pgPort)
	}
	if pg.Instance != "postgres-hackathon" {
		t.Errorf("instance = %q, want postgres-hackathon", pg.Instance)
	}
	if want := filepath.Join(roots.ImagesDir, "postgres.sif"); string(pg.SIF) != want {
		t.Errorf("sif = %q, want %q", pg.SIF, want)
	}
	if want := filepath.Join(roots.DataDir, "hackathon", "postgres"); string(pg.DataDir) != want {
		t.Errorf("data_dir = %q, want %q", pg.DataDir, want)
	}
	if pg.URL != "postgresql://localhost:24085" {
		t.Errorf("url = %q, want host+port only", pg.URL)
	}
	if pg.Capabilities != (registry.Capabilities{}) {
		t.Errorf("capabilities = %+v, want all false like every other store at adoption", pg.Capabilities)
	}
	if n := countCode(findings, doctor.UnexpectedListener); n != 0 {
		t.Errorf("%d unexpected_listener finding(s); the +5 port is now attributed to this tenant: %+v", n, codes(findings))
	}
	if n := countCode(findings, doctor.PostgresNotListening); n != 0 {
		t.Errorf("%d postgres_not_listening finding(s) with a live listener: %+v", n, codes(findings))
	}
}

// TestPreviewPostgresLocalWithNothingListening: the row is still recorded (it
// is what provision.env says the tenant IS, not what is up right now), and the
// dead store is reported.
func TestPreviewPostgresLocalWithNothingListening(t *testing.T) {
	roots, h := pgFixture(t, "hackathon", "TENANT_STORE_KIND=postgres-local\nTENANT_PG_HOST=localhost\nTENANT_PG_PORT=24085\n")
	tenant, findings := pgPreview(t, roots, h, "hackathon")

	if tenant.Stores.Postgres.Kind != registry.PostgresKindLocal || tenant.Stores.Postgres.Instance != "postgres-hackathon" {
		t.Errorf("postgres = %+v, want the local row recorded even with the instance down", tenant.Stores.Postgres)
	}
	if n := countCode(findings, doctor.PostgresNotListening); n != 1 {
		t.Errorf("%d postgres_not_listening findings, want 1: %+v", n, codes(findings))
	}
}

// TestPreviewPostgresLocalPortDrift: provision.env is evidence, the block is
// authority. A disagreement is reported and the block wins.
func TestPreviewPostgresLocalPortDrift(t *testing.T) {
	roots, h := pgFixture(t, "hackathon", "TENANT_STORE_KIND=postgres-local\nTENANT_PG_HOST=localhost\nTENANT_PG_PORT=25432\n", pgListener())
	tenant, findings := pgPreview(t, roots, h, "hackathon")

	if int(tenant.Stores.Postgres.Port) != pgPort {
		t.Errorf("port = %d, want the block's %d, not provision.env's 25432", int(tenant.Stores.Postgres.Port), pgPort)
	}
	if n := countCode(findings, doctor.PostgresPortDrift); n != 1 {
		t.Errorf("%d postgres_port_drift findings, want 1: %+v", n, codes(findings))
	}
}

// TestPreviewPostgresLocalWithAStrangerOnThePort: attribution is not blind.
// The port is the tenant's, but the process holding it is not postgres, and
// that is exactly the case the unexpected_listener warning exists for.
func TestPreviewPostgresLocalWithAStrangerOnThePort(t *testing.T) {
	stranger := hostfacts.Listener{Port: pgPort, Addr: "127.0.0.1", Pid: 4242, UID: 1000, User: "wilke", Cmdline: []string{"/usr/bin/python3", "-m", "http.server"}}
	roots, h := pgFixture(t, "hackathon", "TENANT_STORE_KIND=postgres-local\nTENANT_PG_HOST=localhost\nTENANT_PG_PORT=24085\n", stranger)
	_, findings := pgPreview(t, roots, h, "hackathon")

	if n := countCode(findings, doctor.UnexpectedListener); n != 1 {
		t.Errorf("%d unexpected_listener findings, want 1 (python3 is not postgres): %+v", n, codes(findings))
	}
}

// TestPreviewSQLiteTenantRecordsNoServer: the default kind. Every location
// field is null — there is no server to name — and the +5 port is NOT claimed,
// so a listener there stays a stranger in this tenant's block.
func TestPreviewSQLiteTenantRecordsNoServer(t *testing.T) {
	roots, h := pgFixture(t, "hackathon", "TENANT_ES_HEAP=1g\nTENANT_STORE_KIND=sqlite\nTENANT_PG_HOST=\nTENANT_PG_PORT=\n")
	tenant, findings := pgPreview(t, roots, h, "hackathon")

	pg := tenant.Stores.Postgres
	if pg.Kind != registry.PostgresKindSQLite {
		t.Fatalf("kind = %q, want sqlite", pg.Kind)
	}
	if pg.Ownership != registry.OwnershipExclusive {
		t.Errorf("ownership = %q, want exclusive (the files under state/ are this tenant's alone)", pg.Ownership)
	}
	if pg.URL != "" || pg.Port != 0 || pg.Instance != "" || pg.SIF != "" || pg.DataDir != "" {
		t.Errorf("postgres = %+v, want every location field null", pg)
	}
	if n := countCode(findings, doctor.PostgresNotListening); n != 0 {
		t.Errorf("a sqlite tenant has no server to miss, got %d postgres_not_listening: %+v", n, codes(findings))
	}
}

// A provision.env with no TENANT_STORE_KIND at all is the pre-`--postgres`
// tenant: sqlite, the default the script would have rendered.
func TestPreviewMissingStoreKindIsSQLite(t *testing.T) {
	roots, h := pgFixture(t, "hackathon", "TENANT_ES_HEAP=1g\n")
	tenant, _ := pgPreview(t, roots, h, "hackathon")
	if tenant.Stores.Postgres.Kind != registry.PostgresKindSQLite {
		t.Errorf("kind = %q, want sqlite", tenant.Stores.Postgres.Kind)
	}
}

// TestPreviewPostgresExternalRecordsTheServerNotTheDSN: `--postgres <dsn>`
// puts the tenant's database in somebody else's server. The row names the
// server (host+port from provision.env) and claims nothing: no instance, no
// SIF, no data dir, and ownership `external` like neo4j.
func TestPreviewPostgresExternalRecordsTheServerNotTheDSN(t *testing.T) {
	roots, h := pgFixture(t, "hackathon", "TENANT_STORE_KIND=postgres\nTENANT_PG_HOST=localhost\nTENANT_PG_PORT=5432\n")
	tenant, findings := pgPreview(t, roots, h, "hackathon")

	pg := tenant.Stores.Postgres
	if pg.Kind != registry.PostgresKindExternal || pg.Ownership != registry.OwnershipExternal {
		t.Fatalf("kind/ownership = %q/%q, want external/external", pg.Kind, pg.Ownership)
	}
	if pg.URL != "postgresql://localhost:5432" || int(pg.Port) != 5432 {
		t.Errorf("url/port = %q/%d, want the server host+port", pg.URL, int(pg.Port))
	}
	if pg.Instance != "" || pg.SIF != "" || pg.DataDir != "" {
		t.Errorf("postgres = %+v, want nothing claimed for a server the ctl does not run", pg)
	}
	if n := countCode(findings, doctor.PostgresNotListening); n != 0 {
		t.Errorf("an external server is not this tenant's to be down, got %d: %+v", n, codes(findings))
	}
}

// No DSN, no password, no database name may reach the row under ANY kind —
// the whole reason url is host+port. The DSN keys stay secret_refs.
func TestPostgresRowNeverCarriesACredential(t *testing.T) {
	for _, provision := range []string{
		"TENANT_STORE_KIND=postgres-local\nTENANT_PG_HOST=localhost\nTENANT_PG_PORT=24085\n",
		"TENANT_STORE_KIND=postgres\nTENANT_PG_HOST=localhost\nTENANT_PG_PORT=5432\n",
	} {
		roots, h := pgFixture(t, "hackathon", provision, pgListener())
		tenant, _ := pgPreview(t, roots, h, "hackathon")
		for field, v := range map[string]string{
			"url": string(tenant.Stores.Postgres.URL), "instance": string(tenant.Stores.Postgres.Instance),
			"sif": string(tenant.Stores.Postgres.SIF), "data_dir": string(tenant.Stores.Postgres.DataDir),
		} {
			for _, bad := range []string{"@", "password", "PASSWORD"} {
				if v != "" && strings.Contains(v, bad) {
					t.Errorf("stores.postgres.%s = %q contains %q", field, v, bad)
				}
			}
		}
	}
}
