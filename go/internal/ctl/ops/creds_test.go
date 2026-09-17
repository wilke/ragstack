package ops

// The credential ops as PR-E2 finishes them: a ledger a revoke can act on, a
// restart that is performed or refused rather than quietly skipped, and a
// proof that is a dialled fact rather than an assumption about a rewritten
// file.

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// secondSecret is the surviving admin key in the two-key fixtures: a revoke
// needs one, and so does a proof (a tenant that answered 401 to everything
// would otherwise "prove" a revocation it never made).
var secondSecret = strings.Repeat("9f8e7d6c", 8)

// twoKeyLedger is a secrets.env with two admin keys in it.
func twoKeyLedger() []byte {
	return []byte("" +
		"API_KEYS='[\"" + testSecret + "\",\"" + secondSecret + "\"]'\n" +
		"API_KEY_TENANTS='{\"" + testSecret + "\":\"dev\",\"" + secondSecret + "\":\"dev\"}'\n" +
		"API_KEY_ROLES='{\"" + testSecret + "\":\"admin\",\"" + secondSecret + "\":\"admin\"}'\n")
}

func TestKeyMintRecordsTheLedgerRowThatMakesItRevocable(t *testing.T) {
	oc, fake := fixture(t, "dev", instanceManaged)
	p := plan(t, oc, "key-mint", map[string]any{"label": "gowe", "role": "user"})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	tn := oc.Fleet.Tenants["dev"]
	var row *registry.Key
	for i := range tn.Keys {
		if tn.Keys[i].ID == "gowe" {
			row = &tn.Keys[i]
		}
	}
	if row == nil {
		t.Fatalf("the mint wrote no ledger row: %+v", tn.Keys)
	}
	if row.Role != "user" || row.TenantString != "dev" || !row.Effective || row.RevokedAt != "" {
		t.Errorf("ledger row = %+v", row)
	}
	if !strings.HasPrefix(row.Fingerprint, "sha256:") || len(row.Fingerprint) != len("sha256:")+16 {
		t.Errorf("fingerprint = %q", row.Fingerprint)
	}
	if row.CreatedBy != "local:3581" {
		t.Errorf("created_by = %q, want the job's principal", row.CreatedBy)
	}
	// The VALUE is nowhere in the row, the result or the plan.
	secrets := p.Secrets()
	if len(secrets) != 1 || secrets[0].Value == "" {
		t.Fatalf("the minted value did not reach the delivery envelope: %+v", secrets)
	}
	if strings.Contains(strings.Join(titles(p), "|"), secrets[0].Value) {
		t.Error("the minted value is in a step title")
	}
	if fp := p.Result()["fingerprint"]; fp != row.Fingerprint {
		t.Errorf("result fingerprint = %v, want %q", fp, row.Fingerprint)
	}
	// And the round trip: what was minted can now be revoked by id, which is
	// what the ledger row exists for.
	oc.Tenant.Keys = tn.Keys
	revoke, _ := NewRegistry(testDeps(oc)).Lookup("key-revoke")
	if _, err := revoke.Plan(context.Background(), oc, map[string]any{"id": "gowe"}); err != nil {
		t.Errorf("a ctl-minted key could not be ctl-revoked: %v", err)
	}
}

func TestKeyRevokeMarksTheLedgerRowRatherThanDeletingIt(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		instanceManaged(tn)
		tn.Keys = []registry.Key{
			ledgerKey("ops", "admin", fingerprint(testSecret)),
			ledgerKey("survivor", "admin", fingerprint(secondSecret)),
		}
	})
	tp := paths.TenantPaths(oc.Roots, "dev", "dev")
	fake.FakeFiles().Put(tp.SecretsEnv, twoKeyLedger(), 0o640)

	p := plan(t, oc, "key-revoke", map[string]any{"id": "ops"})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	tn := oc.Fleet.Tenants["dev"]
	if len(tn.Keys) != 2 {
		t.Fatalf("a revoke removed the ledger row: %+v", tn.Keys)
	}
	for _, k := range tn.Keys {
		switch k.ID {
		case "ops":
			if k.RevokedAt == "" || k.Effective {
				t.Errorf("the revoked row is not marked: %+v", k)
			}
		case "survivor":
			if k.RevokedAt != "" || !k.Effective {
				t.Errorf("the surviving row was touched: %+v", k)
			}
		}
	}
}

func TestARestartAskedForOnAHandStartedTenantIsRefusedRatherThanSkipped(t *testing.T) {
	// The fixture `dev` is `supervisor: manual` — a tenant somebody else
	// started. Before PR-E2 this reported "configured, pending a restart" for
	// a restart the operator had asked for and the ctl had silently declined.
	oc, _ := fixture(t, "dev", nil)
	err := planErr(t, oc, "key-mint", map[string]any{"label": "ops2", "role": "user", "restart": true})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "hand-started") {
		t.Fatalf("err = %v, want a refusal naming the hand-started tenant", err)
	}
	if !strings.Contains(err.Error(), "handover") {
		t.Errorf("the refusal does not say what would fix it: %v", err)
	}
	// Without --restart it is the ordinary pending change.
	p := plan(t, oc, "key-mint", map[string]any{"label": "ops2", "role": "user"})
	if p.Result()["pending_until_restart"] != true {
		t.Errorf("result = %v", p.Result())
	}
}

func TestProveNeedsARestartAndDialsTheTenant(t *testing.T) {
	oc, _ := fixture(t, "dev", instanceManaged)
	err := planErr(t, oc, "key-mint", map[string]any{"label": "ops2", "role": "user", "prove": true})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "still serving the PREVIOUS key ledger") {
		t.Fatalf("prove without restart = %v", err)
	}

	oc, fake := fixture(t, "dev", instanceManaged)
	restartable(t, oc, fake)
	p := plan(t, oc, "key-mint", map[string]any{"label": "ops2", "role": "user", "restart": true, "prove": true})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	proof, ok := p.Result()["proof"].(map[string]any)
	if !ok {
		t.Fatalf("no proof on the result: %v", p.Result())
	}
	for _, what := range []string{"surviving_admin", "minted"} {
		row, ok := proof[what].(map[string]any)
		if !ok {
			t.Fatalf("proof has no %s: %+v", what, proof)
		}
		if row["status"] != 200 || row["expected"] != 200 {
			t.Errorf("%s = %+v, want 200", what, row)
		}
		fp, _ := row["fingerprint"].(string)
		if !strings.HasPrefix(fp, "sha256:") {
			t.Errorf("%s records %q rather than a fingerprint", what, row["fingerprint"])
		}
	}
	// The values themselves never reach the fake's call log.
	for _, probe := range fake.FakeTenantAPI().KeyProbes {
		if strings.Contains(probe, testSecret) {
			t.Errorf("a credential reached the call log: %q", probe)
		}
	}
}

func TestProveOfARevokeWantsThe401(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		instanceManaged(tn)
		tn.Keys = []registry.Key{
			ledgerKey("ops", "admin", fingerprint(testSecret)),
			ledgerKey("survivor", "admin", fingerprint(secondSecret)),
		}
	})
	tp := paths.TenantPaths(oc.Roots, "dev", "dev")
	fake.FakeFiles().Put(tp.SecretsEnv, twoKeyLedger(), 0o640)
	// The tenant, restarted, refuses the key that is no longer in its ledger.
	fake.FakeTenantAPI().KeyStatuses = map[string]int{testSecret: 401}
	restartable(t, oc, fake)

	p := plan(t, oc, "key-revoke", map[string]any{"id": "ops", "restart": true, "prove": true})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	proof := p.Result()["proof"].(map[string]any)
	row, ok := proof["revoked"].(map[string]any)
	if !ok {
		t.Fatalf("no revoked row in the proof: %+v", proof)
	}
	if row["status"] != 401 {
		t.Errorf("the revoked key answered %v, want 401", row["status"])
	}
	if row["fingerprint"] != fingerprint(testSecret) {
		t.Errorf("the proof names %v, want the ledger's fingerprint", row["fingerprint"])
	}

	// And the failure direction: a tenant that still accepts the revoked key
	// fails the job rather than reporting success.
	oc2, fake2 := fixture(t, "dev", func(tn *registry.Tenant) {
		instanceManaged(tn)
		tn.Keys = []registry.Key{
			ledgerKey("ops", "admin", fingerprint(testSecret)),
			ledgerKey("survivor", "admin", fingerprint(secondSecret)),
		}
	})
	fake2.FakeFiles().Put(tp.SecretsEnv, twoKeyLedger(), 0o640)
	restartable(t, oc2, fake2)
	p2 := plan(t, oc2, "key-revoke", map[string]any{"id": "ops", "restart": true, "prove": true})
	r2 := newRunner(oc2, fake2)
	failed := false
	for _, s := range p2.Steps {
		if _, err := r2.run(s); err != nil {
			if !strings.Contains(err.Error(), "the revoked key answered 200, want 401") {
				t.Fatalf("step %d: %v", s.Plan.N, err)
			}
			failed = true
			break
		}
	}
	if !failed {
		t.Error("a revoke whose key still authenticates reported success")
	}
}

// ---------------------------------------------------------------- env

func TestEnvNormalizeRecordsTheLayoutItCreated(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.EnvLayout = "legacy"
		tn.SecretsFileSHA256 = ""
		tn.SecretRefs = []registry.SecretRef{{Key: "API_KEYS", File: "tenant.env"}}
	})
	p := plan(t, oc, "env-normalize", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	tn := oc.Fleet.Tenants["dev"]
	if tn.EnvLayout != "managed" {
		t.Errorf("env_layout = %q after a normalize that split the secrets out", tn.EnvLayout)
	}
	if tn.SecretsFileSHA256 == "" || tn.EnvFileSHA256 == emptyFixtureSHA {
		t.Errorf("the checksums were not refreshed: env %q secrets %q", tn.EnvFileSHA256, tn.SecretsFileSHA256)
	}
	if tn.SecretRefs[0].File != "secrets.env" {
		t.Errorf("a secret_ref still points at %q after its key moved", tn.SecretRefs[0].File)
	}
	if p.Result()["env_layout"] != "managed" {
		t.Errorf("result = %v", p.Result())
	}
}

// emptyFixtureSHA is the placeholder hash LiveFixture gives every row.
const emptyFixtureSHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

func TestEnvPGPasswordDerivesFromTheDSNsAndIsIdempotent(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.Stores.Postgres = registry.Postgres{
			Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
			Capabilities: registry.Capabilities{Stop: true},
			URL:          registry.NullString("postgresql://localhost:24045"),
			Port:         registry.NullPort(24045), Instance: registry.NullString("postgres-dev"),
			SIF: "/rag/apptainer/images/postgres.sif", DataDir: registry.NullString(tn.DataDir + "/postgres"),
		}
	})
	tp := paths.TenantPaths(oc.Roots, "dev", "dev")
	const pw = "s3cr3t-role-pw"
	fake.FakeFiles().Put(tp.SecretsEnv, []byte(""+
		"POSTGRES_DSN=postgresql://dev:"+pw+"@localhost:24045/dev\n"+
		"USER_STORE_DSN=postgresql://dev:"+pw+"@localhost:24045/dev\n"+
		"COLLECTION_STORE_DSN=postgresql://dev:"+pw+"@localhost:24045/dev\n"), 0o640)

	p := plan(t, oc, "env-pg-password", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	body := string(fake.FakeFiles().Content(tp.SecretsEnv))
	if !strings.Contains(body, "APPTAINERENV_POSTGRES_PASSWORD="+pw) {
		t.Fatalf("the key was not written:\n%s", body)
	}
	if p.Result()["written"] != true {
		t.Errorf("result = %v", p.Result())
	}
	// A second run changes nothing and says so — which is what makes this a
	// preparation step an operator can re-run without thinking about it.
	p2 := plan(t, oc, "env-pg-password", nil)
	r2 := newRunner(oc, fake)
	r2.runAll(t, p2)
	if p2.Result()["written"] != false {
		t.Errorf("the second run rewrote the file: %v", p2.Result())
	}

	// Disagreeing connection strings are a refusal, not a coin toss.
	oc3, fake3 := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.Stores.Postgres = oc.Tenant.Stores.Postgres
	})
	fake3.FakeFiles().Put(tp.SecretsEnv, []byte(""+
		"POSTGRES_DSN=postgresql://dev:one@localhost:24045/dev\n"+
		"USER_STORE_DSN=postgresql://dev:two@localhost:24045/dev\n"), 0o640)
	p3 := plan(t, oc3, "env-pg-password", nil)
	r3 := newRunner(oc3, fake3)
	if _, err := r3.run(p3.Steps[0]); err == nil || !strings.Contains(err.Error(), "disagree") {
		t.Fatalf("err = %v, want the disagreement refusal", err)
	}

	// And the whole op is refused for a tenant with no postgres server.
	oc4, _ := fixture(t, "dev", managed)
	if err := planErr(t, oc4, "env-pg-password", nil); !strings.Contains(err.Error(), "no postgres server of its own") {
		t.Errorf("err = %v", err)
	}
}

// restartable is the host a `--restart` runs on: the API pid the fixture's
// pidfile names is alive and holding the port, so the stop half has something
// to stop rather than a stale file over a port it cannot account for.
func restartable(t *testing.T, oc jobs.Context, fake *drivers.Fake) {
	t.Helper()
	fake.FakeProc().MarkAlive(4242, oc.Tenant.Ports.API)
}
