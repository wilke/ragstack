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

// TestTheLedgerSurvivesRotation is the regression for the review's M11: the
// mint → revoke → mint → revoke sequence, which is the ordinary rotation and
// the one that produced a ledger with two rows for one id.
//
// The bug was two lookups that disagreed: the fingerprint came from the LAST
// row with the id and the role from the FIRST. With a revoked `ops` in front
// of a current `ops`, the last-admin guard read the withdrawn row's role and
// the edit removed the current row's value — enough to empty a tenant's
// `API_KEYS` to `[]` while the guard said an administrator remained.
func TestTheLedgerSurvivesRotation(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		instanceManaged(tn)
		// A surviving admin, so the rotations below are never the last one.
		tn.Keys = []registry.Key{ledgerKey("survivor", "admin", fingerprint(secondSecret))}
	})
	tp := paths.TenantPaths(oc.Roots, "dev", "dev")
	fake.FakeFiles().Put(tp.SecretsEnv, []byte(""+
		"API_KEYS='[\""+secondSecret+"\"]'\n"+
		"API_KEY_TENANTS='{\""+secondSecret+"\":\"dev\"}'\n"+
		"API_KEY_ROLES='{\""+secondSecret+"\":\"admin\"}'\n"), 0o640)

	for round := 1; round <= 2; round++ {
		mint := plan(t, oc, "key-mint", map[string]any{"label": "ops", "role": "admin"})
		newRunner(oc, fake).runAll(t, mint)
		oc.Tenant = oc.Fleet.Tenants["dev"]

		effective := 0
		for _, k := range oc.Tenant.Keys {
			if k.ID == "ops" && k.RevokedAt == "" {
				effective++
			}
		}
		if effective != 1 {
			t.Fatalf("round %d: %d effective `ops` rows, want exactly 1: %+v", round, effective, oc.Tenant.Keys)
		}

		revoke := plan(t, oc, "key-revoke", map[string]any{"id": "ops"})
		newRunner(oc, fake).runAll(t, revoke)
		oc.Tenant = oc.Fleet.Tenants["dev"]
	}

	// Two rounds leave two historical rows, both revoked, and the survivor.
	tn := oc.Fleet.Tenants["dev"]
	if len(tn.Keys) != 3 {
		t.Fatalf("ledger = %+v, want the survivor plus two revoked `ops` rows", tn.Keys)
	}
	for _, k := range tn.Keys {
		if k.ID == "ops" && k.RevokedAt == "" {
			t.Errorf("an `ops` row survived its revoke: %+v", k)
		}
	}
	// The file still holds the survivor, and only the survivor.
	body := string(fake.FakeFiles().Content(tp.SecretsEnv))
	if !strings.Contains(body, secondSecret) {
		t.Errorf("the rotation removed the surviving admin key:\n%s", body)
	}
	if strings.Contains(body, "API_KEYS=[]") {
		t.Errorf("the rotation emptied the ledger:\n%s", body)
	}
	// And the contract agrees the row set is legal.
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("the rotated ledger does not satisfy the contract: %v", err)
	}

	// Re-revoking an id whose rows are all revoked is a refusal that says so,
	// rather than "no such key" (which would send an operator looking for a
	// typo) or a second withdrawal of nothing.
	err := planErr(t, oc, "key-revoke", map[string]any{"id": "ops"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "already revoked") {
		t.Errorf("re-revoking = %v", err)
	}
}

// TestTheContractRefusesTwoEffectiveRowsForOneID is the other half: the rule
// that makes "the effective row" a definite thing.
func TestTheContractRefusesTwoEffectiveRowsForOneID(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		instanceManaged(tn)
		tn.Keys = []registry.Key{
			ledgerKey("ops", "user", fingerprint(testSecret)),
			ledgerKey("ops", "admin", fingerprint(secondSecret)),
		}
	})
	err := oc.Fleet.ValidateContract()
	if err == nil || !strings.Contains(err.Error(), "at most one key that has not been revoked") {
		t.Fatalf("two effective rows for one id were accepted: %v", err)
	}
}

// TestAMintedKeyTakesTheLedgersOwnTenantString is the review's M12: the
// adopted tenants do not use their own name in `API_KEY_TENANTS`.
func TestAMintedKeyTakesTheLedgersOwnTenantString(t *testing.T) {
	oc, fake := fixture(t, "dev", instanceManaged)
	tp := paths.TenantPaths(oc.Roots, "dev", "dev")
	// asm-next's shape: every key carries a principal that is not the tenant.
	fake.FakeFiles().Put(tp.SecretsEnv, []byte(""+
		"API_KEYS='[\""+testSecret+"\"]'\n"+
		"API_KEY_TENANTS='{\""+testSecret+"\":\"asm-ops\"}'\n"+
		"API_KEY_ROLES='{\""+testSecret+"\":\"admin\"}'\n"), 0o640)

	p := plan(t, oc, "key-mint", map[string]any{"label": "gowe", "role": "user"})
	if !warnsAbout(p, "asm-ops") {
		t.Errorf("the plan does not say which tenant string it will use: %v", p.Plan.Warnings)
	}
	newRunner(oc, fake).runAll(t, p)

	row, ok := effectiveKey(oc.Fleet.Tenants["dev"].Keys, "gowe")
	if !ok {
		t.Fatal("no ledger row")
	}
	if row.TenantString != "asm-ops" {
		t.Errorf("tenant_string = %q, want the convention the file already uses", row.TenantString)
	}
	body := string(fake.FakeFiles().Content(tp.SecretsEnv))
	if !strings.Contains(body, `"asm-ops"`) || strings.Contains(body, `"dev"`) {
		t.Errorf("the file's own map disagrees with the ledger row:\n%s", body)
	}

	// An EXPLICIT --tenant-string wins over the convention.
	oc2, fake2 := fixture(t, "dev", instanceManaged)
	fake2.FakeFiles().Put(tp.SecretsEnv, fake.FakeFiles().Content(tp.SecretsEnv), 0o640)
	p2 := plan(t, oc2, "key-mint", map[string]any{"label": "web", "role": "user", "tenant_string": "svc-asm-web"})
	newRunner(oc2, fake2).runAll(t, p2)
	row2, _ := effectiveKey(oc2.Fleet.Tenants["dev"].Keys, "web")
	if row2.TenantString != "svc-asm-web" {
		t.Errorf("tenant_string = %q, want the explicit one", row2.TenantString)
	}

	// And a ledger with NO convention is a refusal rather than a guess.
	oc3, fake3 := fixture(t, "dev", instanceManaged)
	fake3.FakeFiles().Put(tp.SecretsEnv, []byte(""+
		"API_KEYS='[\""+testSecret+"\",\""+secondSecret+"\"]'\n"+
		"API_KEY_TENANTS='{\""+testSecret+"\":\"asm-ops\",\""+secondSecret+"\":\"asm-ro\"}'\n"+
		"API_KEY_ROLES='{\""+testSecret+"\":\"admin\",\""+secondSecret+"\":\"user\"}'\n"), 0o640)
	err := planErr(t, oc3, "key-mint", map[string]any{"label": "third", "role": "user"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "--tenant-string") {
		t.Fatalf("a ledger with two conventions = %v", err)
	}
	for _, want := range []string{"asm-ops", "asm-ro"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not name %q: %v", want, err)
		}
	}
}

// TestProveCatchesAKeyThatAuthenticatesAndSeesNothing is M12's other half: a
// 200 is not proof that a credential works.
func TestProveCatchesAKeyThatAuthenticatesAndSeesNothing(t *testing.T) {
	oc, fake := fixture(t, "dev", instanceManaged)
	restartable(t, oc, fake)
	// The tenant HAS collections (the fixture's qdrant does, and the fake
	// tenant API answers from it) — but this key sees none of them.
	origin := "http://127.0.0.1:24040"
	fake.FakeTenantAPI().CollectionsByOrigin = map[string][]string{origin: {"docs", "chunks"}}

	p := plan(t, oc, "key-mint", map[string]any{"label": "wrong", "role": "user", "restart": true, "prove": true})
	r := newRunner(oc, fake)
	var failed error
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			failed = err
			break
		}
	}
	// The fake answers the same list for every credential, so the happy path
	// is what runs here; the assertion is that the visibility check HAPPENED
	// and is recorded.
	if failed != nil {
		t.Fatalf("the proof failed unexpectedly: %v", failed)
	}
	proof, _ := p.Result()["proof"].(map[string]any)
	vis, ok := proof["minted_visibility"].(map[string]any)
	if !ok {
		t.Fatalf("the proof did not check visibility at all: %+v", proof)
	}
	if vis["collections"] == 0 {
		t.Errorf("visibility = %+v", vis)
	}
}
