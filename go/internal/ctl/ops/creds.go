package ops

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The three env keys that are the tenant API's key ledger. They are edited
// as ONE unit: a key present in API_KEYS but missing from API_KEY_ROLES is a
// key with the default role, which is not what anybody asked for.
const (
	keyAPIKeys   = "API_KEYS"
	keyAPITenant = "API_KEY_TENANTS"
	keyAPIRoles  = "API_KEY_ROLES"
	keyAdminSubs = "ADMIN_SUBJECTS"
)

// credFile is the file a credential edit rewrites, and why.
func (p *planner) credFile() (path string, legacy bool) {
	if p.t.SecretsFileSHA256 != "" {
		return p.tpaths.SecretsEnv, false
	}
	// No secrets.env: this tenant keeps its credentials in tenant.env, which
	// is the `legacy` env layout every hand-started tenant has. The edit is
	// the same edit; where it lands is a fact about the tenant.
	return p.tpaths.TenantEnv, true
}

// ---------------------------------------------------------------- key mint

func planKeyMint(ctx context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	label, role := argStringOf(args, "label"), argStringOf(args, "role")
	if _, ok := effectiveKey(p.t.Keys, label); ok {
		return p.refuse("%s already has an effective key labelled %q; revoke it first or pick another label",
			p.t.Name, label)
	}
	path, legacy := p.credFile()
	if legacy {
		p.warn("this tenant's env layout is `legacy`: its key ledger lives in tenant.env rather than secrets.env; " +
			"`env normalize` splits them")
	}
	tenant, err := p.mintTenantString(ctx, args, path)
	if err != nil {
		return err
	}

	// minted is filled by the RUN half and read by Planned.Secrets. The value
	// is generated when the job runs and never at plan time: a plan is
	// computed twice, shown, logged and hashed, and a key minted into one
	// would exist in all of those places before anybody asked for it.
	var minted string
	p.secrets = func() []model.Secret {
		if minted == "" {
			return nil
		}
		return []model.Secret{{ID: label, Label: label, Role: role, Value: minted}}
	}

	p.addEnvEdit(ctx, envEdit{
		Path: path, Op: "key-mint", Secret: true,
		Title:   fmt.Sprintf("mint the %s key %q into the ledger", role, label),
		Targets: []string{keyAPIKeys, keyAPITenant, keyAPIRoles},
		Mutate: func(f *envfile.File) (string, error) {
			key, err := mintToken()
			if err != nil {
				return "", err
			}
			keys, err := jsonList(f, keyAPIKeys)
			if err != nil {
				return "", err
			}
			tenants, err := jsonMap(f, keyAPITenant)
			if err != nil {
				return "", err
			}
			rolesByKey, err := jsonMap(f, keyAPIRoles)
			if err != nil {
				return "", err
			}
			keys = append(keys, key)
			tenants[key], rolesByKey[key] = tenant, role
			if err := setJSON(f, keyAPIKeys, keys); err != nil {
				return "", err
			}
			if err := setJSON(f, keyAPITenant, tenants); err != nil {
				return "", err
			}
			if err := setJSON(f, keyAPIRoles, rolesByKey); err != nil {
				return "", err
			}
			minted = key
			return "minted " + label + " (" + fingerprint(key) + ")", nil
		},
	})
	p.addKeyLedgerRow(label, role, tenant, &minted)
	p.result["key_id"] = label
	p.result["role"] = role
	if err := p.addRestartOrPending(args, "the new key is configured; it is EFFECTIVE only after the API reloads "+
		"its env"); err != nil {
		return err
	}
	// The proof is the MINTED value answering 200, and a surviving admin key
	// answering 200 beside it — the second is what tells "the key works" apart
	// from "the API accepts anything", which is the failure a proof of one
	// credential cannot see.
	return p.addKeyProof(args, keyProof{minted: &minted, mintedLabel: label})
}

// addKeyLedgerRow records the minted key in the registry's `keys[]`.
//
// Without it a ctl-minted key could not be ctl-revoked: `key revoke` looks the
// id up in this ledger and refuses an id it does not find, so every key this
// control plane minted before PR-E2 was a key only an editor could withdraw.
//
// The FINGERPRINT is recorded, never the value — that is the whole discipline
// of this ledger — and it is computed at run time from the value the env step
// minted, because a plan is hashed, logged and shown twice.
func (p *planner) addKeyLedgerRow(label, role, tenantString string, minted *string) {
	name := p.tenant
	p.add(step{
		Kind: "registry", Title: fmt.Sprintf("record the key %q in the registry ledger (fingerprint only)", label),
		Targets:    []string{name, label},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"the ledger holds a FINGERPRINT (sha256 of the value, truncated) and never the value: " +
			"it is what `key revoke` withdraws by, and what makes a ctl-minted key a key the ctl can withdraw"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if *minted == "" {
				return "", fmt.Errorf("%w: the mint step recorded no value, so there is nothing to put in the ledger",
					jobs.ErrRefused)
			}
			fp := fingerprint(*minted)
			err := p.saveTenant(sc, "key-mint", func(t *registry.Tenant) error {
				t.Keys = append(t.Keys, registry.Key{
					ID: label, Label: label, Role: role, TenantString: tenantString, Fingerprint: fp,
					CreatedAt: p.stampRFC3339(sc), CreatedBy: sc.Job.Principal, Effective: true,
				})
				return nil
			})
			if err != nil {
				return "", err
			}
			p.result["fingerprint"] = fp
			return label + " (" + fp + ") is in the ledger", nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				kept := t.Keys[:0:0]
				for _, k := range t.Keys {
					if k.ID == label && k.RevokedAt == "" {
						continue
					}
					kept = append(kept, k)
				}
				t.Keys = kept
				return nil
			})
			if err != nil {
				return "", err
			}
			return label + " is out of the ledger again", nil
		},
	})
}

// mintTenantString decides what a minted key's TENANT STRING will be, and it
// asks the file rather than assuming the tenant's name.
//
// `API_KEY_TENANTS` maps each key to the string the tenant API stamps on
// everything that key writes — the owner of a collection, the principal in an
// ACL row. It is NOT the tenant's name on the adopted rows: asm-next's live
// ledger uses `asm-ops`, `svc-asm-web` and `asm-ro`, and a key minted with
// `asm-next` in that slot authenticates perfectly and then sees none of the
// corpus, because every existing document belongs to somebody else. That is a
// credential that looks like it works and does not, which is the worst kind to
// hand somebody.
//
// So: an explicit `--tenant-string` wins. Otherwise the file's own convention
// decides, and only when the file HAS one — a ledger whose keys already
// disagree is a ledger this op will not guess for.
func (p *planner) mintTenantString(ctx context.Context, args map[string]any, path string) (string, error) {
	if s := argStringOf(args, "tenant_string"); s != "" {
		return s, nil
	}
	b, err := p.readFile(ctx, path)
	if err != nil {
		// No file to read a convention out of: the tenant's name is the only
		// answer there is, and `create` writes exactly that.
		return p.t.Name, nil
	}
	f, _, perr := envfile.ParseLenient(b)
	if perr != nil {
		return p.t.Name, nil
	}
	tenants, jerr := jsonMap(f, keyAPITenant)
	if jerr != nil {
		return p.t.Name, nil
	}
	seen := map[string]bool{}
	for _, v := range tenants {
		if v != "" {
			seen[v] = true
		}
	}
	switch len(seen) {
	case 0:
		return p.t.Name, nil
	case 1:
		for v := range seen {
			if v != p.t.Name {
				p.warn("the minted key's tenant string is `" + v + "`, which is what every key already in " +
					filepath.Base(path) + " uses — not the tenant's name (`" + p.t.Name + "`). A key stamped with " +
					"the wrong one authenticates and then sees none of the tenant's documents")
			}
			return v, nil
		}
	}
	names := make([]string, 0, len(seen))
	for v := range seen {
		names = append(names, v)
	}
	sort.Strings(names)
	return "", p.refuse("%s's key ledger uses %d different tenant strings (%s), so there is no convention for this "+
		"mint to follow — and a key stamped with the wrong one authenticates and then sees none of the tenant's "+
		"documents. Name it: `--tenant-string <one of them>`", p.t.Name, len(names), strings.Join(names, ", "))
}

// ---------------------------------------------------------------- key revoke

func planKeyRevoke(ctx context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	id := argStringOf(args, "id")
	// ONE lookup, and it is the EFFECTIVE row.
	//
	// This used to be two: the fingerprint came from the LAST row with this
	// id and the role from the FIRST. A ledger holding a revoked `ops` and a
	// current `ops` — which the mint→revoke→mint sequence produces — therefore
	// answered "the role of the withdrawn key" to the last-admin guard while
	// revoking the value of the current one. A `user` row in front of an
	// `admin` row was enough to walk past the guard and empty the file's
	// `API_KEYS` to `[]`.
	row, ok := effectiveKey(p.t.Keys, id)
	if !ok {
		if _, historic := anyKey(p.t.Keys, id); historic {
			return p.refuse("%s's key %q is already revoked; there is nothing left to withdraw (the ledger keeps "+
				"the row so that who held it stays answerable)", p.t.Name, id)
		}
		return p.refuse("%s has no key %q in its ledger (the ctl revokes by ledger id, never by value)", p.t.Name, id)
	}
	want := row.Fingerprint
	effective := 0
	for _, k := range p.t.Keys {
		if k.Role == "admin" && k.RevokedAt == "" && k.ID != id {
			effective++
		}
	}
	if effective == 0 && row.Role == "admin" {
		return p.refuse("%q is the last effective admin key of %s; revoking it would leave the tenant with no "+
			"administrator (mint a replacement first)", id, p.t.Name)
	}
	path, _ := p.credFile()
	// The value that was removed, captured by the run half and read by the
	// proof: "the old key answers 401" is a claim about a VALUE, and after the
	// edit the file no longer holds it.
	var removed string
	p.addEnvEdit(ctx, envEdit{
		Path: path, Op: "key-revoke", Secret: true, Destructive: true,
		Title:   fmt.Sprintf("revoke the key %q (%s) from the ledger", id, want),
		Targets: []string{keyAPIKeys, keyAPITenant, keyAPIRoles},
		Mutate: func(f *envfile.File) (string, error) {
			keys, err := jsonList(f, keyAPIKeys)
			if err != nil {
				return "", err
			}
			tenants, err := jsonMap(f, keyAPITenant)
			if err != nil {
				return "", err
			}
			rolesByKey, err := jsonMap(f, keyAPIRoles)
			if err != nil {
				return "", err
			}
			// The ledger records a FINGERPRINT, never a value, so the value to
			// remove is found by hashing each one in the file. A key the
			// registry claims exists but the file does not hold is a real
			// disagreement and is reported rather than papered over.
			kept, found := keys[:0:0], false
			for _, k := range keys {
				if fingerprint(k) == want {
					found, removed = true, k
					delete(tenants, k)
					delete(rolesByKey, k)
					continue
				}
				kept = append(kept, k)
			}
			if !found {
				return "", fmt.Errorf("%w: no key in %s hashes to %s, so the registry ledger and the env file "+
					"disagree; run `doctor` before revoking", jobs.ErrRefused, filepath.Base(path), want)
			}
			if err := setJSON(f, keyAPIKeys, kept); err != nil {
				return "", err
			}
			if err := setJSON(f, keyAPITenant, tenants); err != nil {
				return "", err
			}
			if err := setJSON(f, keyAPIRoles, rolesByKey); err != nil {
				return "", err
			}
			return "revoked " + id, nil
		},
	})
	p.addKeyRevokeLedgerRow(id)
	p.result["key_id"] = id
	if err := p.addRestartOrPending(args, "the key is revoked in the FILE; it keeps working until the API reloads "+
		"its env (and the proof is the old key answering 401)"); err != nil {
		return err
	}
	return p.addKeyProof(args, keyProof{revoked: &removed, revokedLabel: id})
}

// addKeyRevokeLedgerRow marks the ledger row revoked rather than deleting it.
//
// Deleting it would lose the record that the key ever existed, which is the
// one thing a credential ledger is for: "who could reach this tenant in
// August" has to stay answerable after the key is gone. `revoked_at` and
// `effective: false` are the contract's way of saying it.
func (p *planner) addKeyRevokeLedgerRow(id string) {
	name := p.tenant
	// Written by the Run and read by the Rollback: which row this step
	// revoked, as opposed to every row that ever carried this id.
	var revokedAt string
	p.add(step{
		Kind: "registry", Title: fmt.Sprintf("mark the key %q revoked in the registry ledger", id),
		Destructive: true, Targets: []string{name, id},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"the row is KEPT with revoked_at set: a ledger that deleted withdrawn keys could not " +
			"answer who held a credential last month, which is the question a ledger exists for"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			at := p.stampRFC3339(sc)
			revokedAt = at
			found := false
			err := p.saveTenant(sc, "key-revoke", func(t *registry.Tenant) error {
				for i := range t.Keys {
					if t.Keys[i].ID == id && t.Keys[i].RevokedAt == "" {
						t.Keys[i].RevokedAt = registry.NullString(at)
						t.Keys[i].Effective = false
						found = true
					}
				}
				if !found {
					return fmt.Errorf("%w: %s has no effective ledger row for %q any more", jobs.ErrRefused, name, id)
				}
				return nil
			})
			if err != nil {
				return "", err
			}
			return id + " is revoked in the ledger", nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			// Only the row THIS step revoked, found by the timestamp it wrote.
			// Undoing every row with this id would resurrect the ones an
			// earlier rotation withdrew — a mint→revoke→mint→revoke ledger
			// would come back with two effective keys of the same name, which
			// the contract refuses and which is a credential nobody meant to
			// reissue.
			err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				for i := range t.Keys {
					if t.Keys[i].ID == id && string(t.Keys[i].RevokedAt) == revokedAt {
						t.Keys[i].RevokedAt, t.Keys[i].Effective = "", true
					}
				}
				return nil
			})
			if err != nil {
				return "", err
			}
			return id + " is effective in the ledger again", nil
		},
	})
}

// keyProof is what one `--prove` run has to dial and what it expects.
//
// The values are POINTERS because they are produced by the run halves of
// earlier steps — a minted key exists only after the mint has run, and a
// revoked one only after the file has been rewritten — and a plan that held
// either would be a plan with a credential in it.
type keyProof struct {
	minted       *string
	mintedLabel  string
	revoked      *string
	revokedLabel string
}

// proveVisibility is the second half of a mint's proof: the key can SEE
// something.
//
// The tenant API filters every listing by the caller's principal, so a key
// minted with the wrong tenant string answers 200 to an authenticated request
// and returns an empty world. Comparing against what the ADMIN key sees is
// what makes "empty" meaningful: a tenant that really has no collections is
// not a failure, and one that has forty and shows this key none is.
func proveVisibility(ctx context.Context, sc *jobs.StepContext, api jobs.TenantAPI,
	origin, admin, minted string, results map[string]any) error {
	theirs, err := api.Collections(ctx, origin, admin)
	if err != nil {
		sc.Logf("the tenant's own collection listing could not be read (%v); the visibility half of the proof "+
			"is skipped", err)
		return nil
	}
	if len(theirs) == 0 {
		results["minted_visibility"] = map[string]any{"collections": 0, "note": "the tenant has none to see"}
		return nil
	}
	mine, err := api.Collections(ctx, origin, minted)
	if err != nil {
		return fmt.Errorf("listing collections with the minted key: %w", err)
	}
	results["minted_visibility"] = map[string]any{"collections": len(mine), "tenant_has": len(theirs)}
	if len(mine) == 0 {
		return fmt.Errorf("%w: the minted key authenticates (200) and can see NONE of this tenant's %d "+
			"collection(s). That is what a wrong tenant string looks like: the key is valid and every document "+
			"belongs to somebody else. Revoke it and mint again with `--tenant-string <the value the ledger uses>`",
			jobs.ErrRefused, len(theirs))
	}
	sc.Logf("the minted key sees %d of the tenant's %d collection(s)", len(mine), len(theirs))
	return nil
}

// addKeyProof dials the tenant API and records the verdict on the job.
//
// Three questions, and the second is the one that makes the other two mean
// anything:
//
//   - the minted key answers 200 (it works);
//   - a SURVIVING admin key answers 200 (the API is up and authenticating, so
//     a 401 from the third question is a statement about that key rather than
//     about the tenant);
//   - the revoked key answers 401 (it is gone).
//
// It needs `restart`: until the API has reloaded its environment it is still
// serving the previous ledger, and a proof taken then proves the opposite of
// what it claims. The credentials are read from the tenant's own files at run
// time and never appear in the plan, the log, a step target or the result —
// the result carries fingerprints and status codes.
func (p *planner) addKeyProof(args map[string]any, pr keyProof) error {
	if !argBoolOf(args, "prove") {
		return nil
	}
	if !argBoolOf(args, "restart") {
		return p.refuse("`prove` without `restart` would prove the opposite of what it claims: until the API " +
			"reloads its environment it is still serving the PREVIOUS key ledger, so the minted key answers 401 " +
			"and the revoked one answers 200. Pass restart as well (`ragstack-ctl key … --restart --prove`)")
	}
	origin := fmt.Sprintf("http://127.0.0.1:%d", p.t.Ports.API)
	tenantEnv, secretsEnv := p.tpaths.TenantEnv, p.tpaths.SecretsEnv
	revokedFP := ""
	for _, k := range p.t.Keys {
		if k.ID == pr.revokedLabel {
			revokedFP = k.Fingerprint
		}
	}
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "prove the ledger against the restarted API (200 for what works, 401 for what does not)",
		Targets: []string{origin},
		Warnings: []string{"every credential this step presents is read from the tenant's own env files at RUN " +
			"time; the job records fingerprints and status codes, never a value"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			api := sc.Ops.Drivers.TenantAPI()
			results := map[string]any{}
			// The results map is published on the job BEFORE any refusal can
			// leave this step: a proof that failed is exactly the one an
			// operator needs the numbers from, and `p.result` set only on the
			// happy path meant a failed job recorded nothing at all.
			p.result["proof"] = results
			check := func(what, key, fp string, wantStatus int) error {
				status, err := api.KeyStatus(ctx, origin, key)
				if err != nil {
					return fmt.Errorf("presenting the %s key to %s: %w", what, origin, err)
				}
				results[what] = map[string]any{"fingerprint": fp, "status": status, "expected": wantStatus}
				if status != wantStatus {
					return fmt.Errorf("%w: the %s key answered %d, want %d", jobs.ErrRefused, what, status, wantStatus)
				}
				sc.Logf("the %s key (%s) answers %d, as expected", what, fp, status)
				return nil
			}
			// The surviving admin first: a tenant that answers 401 to
			// everything would otherwise "prove" a revocation it never made.
			admin, err := adminKeyFromEnvFiles(ctx, sc, tenantEnv, secretsEnv)
			if err != nil {
				return "", err
			}
			if admin == "" {
				return "", fmt.Errorf("%w: no admin key could be read from the tenant's env files, so there is no "+
					"credential to check the API against", jobs.ErrRefused)
			}
			// It must be a DIFFERENT key from the one being proved. On a
			// tenant whose only admin key is the one this job just minted,
			// `adminKeyFromEnvFiles` hands back that very value — and the
			// proof then becomes "the minted key answers 200, and so does the
			// minted key", which rules nothing out. Say so rather than
			// reporting a check that checked one thing twice.
			if pr.minted != nil && admin == *pr.minted {
				sc.Logf("the only admin credential in this tenant's env files IS the key this job minted; the " +
					"surviving-admin half of the proof would be the same request twice and is recorded as skipped")
				results["surviving_admin"] = map[string]any{
					"skipped": "the tenant's only admin key is the one this job minted",
				}
			} else if err := check("surviving_admin", admin, fingerprint(admin), 200); err != nil {
				return "", err
			}
			if pr.minted != nil && *pr.minted != "" {
				if err := check("minted", *pr.minted, fingerprint(*pr.minted), 200); err != nil {
					return "", err
				}
				// A 200 is not enough. A key whose TENANT STRING is wrong
				// authenticates perfectly and then sees none of the tenant's
				// documents, because every one of them belongs to a different
				// principal — a credential that looks like it works and does
				// not. So: if the tenant has collections at all, the minted
				// key has to be able to see some.
				if err := proveVisibility(ctx, sc, api, origin, admin, *pr.minted, results); err != nil {
					return "", err
				}
			}
			if pr.revoked != nil && *pr.revoked != "" {
				fp := revokedFP
				if fp == "" {
					fp = fingerprint(*pr.revoked)
				}
				if err := check("revoked", *pr.revoked, fp, 401); err != nil {
					return "", err
				}
			}
			return fmt.Sprintf("%d credential(s) answered as expected", len(results)), nil
		},
	})
	return nil
}

// effectiveKey is THE row for a ledger id: the one that is not revoked.
//
// The contract makes that unique (registry.ValidateContract refuses a second
// effective row for one id), so "the effective row" is a definite thing and
// every caller asks for it the same way. A ledger with only revoked rows for
// the id answers false, which is a different outcome from "no such id" and the
// caller says so.
func effectiveKey(keys []registry.Key, id string) (registry.Key, bool) {
	for _, k := range keys {
		if k.ID == id && k.RevokedAt == "" {
			return k, true
		}
	}
	return registry.Key{}, false
}

// anyKey is "this id has been used before", revoked rows included.
func anyKey(keys []registry.Key, id string) (registry.Key, bool) {
	for _, k := range keys {
		if k.ID == id {
			return k, true
		}
	}
	return registry.Key{}, false
}

// ---------------------------------------------------------------- admins

func planAdminAdd(ctx context.Context, p *planner, args map[string]any) error {
	return p.planAdminEdit(ctx, args, true)
}

func planAdminRemove(ctx context.Context, p *planner, args map[string]any) error {
	return p.planAdminEdit(ctx, args, false)
}

func (p *planner) planAdminEdit(ctx context.Context, args map[string]any, add bool) error {
	p.need(model.LockTenant)
	subject := argStringOf(args, "subject")
	issuer := strings.SplitN(subject, ":", 2)[0]
	provider := p.t.Identity.Provider
	if provider == "none" {
		return p.refuse("%s has identity_provider `none`: it authenticates with API keys only, so there are no admin "+
			"subjects to add or remove", p.t.Name)
	}
	if issuer != provider {
		return p.refuse("subject %q is issued by %q but %s's identity provider is %q; an admin subject's issuer must "+
			"be the tenant's provider", subject, issuer, p.t.Name, provider)
	}
	if !add && p.t.Identity.AdminSubjectsCount <= 1 {
		return p.refuse("%s has %d admin subject(s); removing the last one would leave the tenant with no bearer "+
			"administrator", p.t.Name, p.t.Identity.AdminSubjectsCount)
	}
	verb := "add"
	if !add {
		verb = "remove"
	}
	// ADMIN_SUBJECTS is a PUBLIC key: it lives in tenant.env, and the plan may
	// show it.
	p.addEnvEdit(ctx, envEdit{
		Path: p.tpaths.TenantEnv, Op: "admin-" + verb, Destructive: !add,
		Title:   fmt.Sprintf("%s %s in %s", verb, subject, keyAdminSubs),
		Targets: []string{keyAdminSubs},
		Mutate: func(f *envfile.File) (string, error) {
			subs, err := jsonList(f, keyAdminSubs)
			if err != nil {
				return "", err
			}
			out := subs[:0:0]
			present := false
			for _, s := range subs {
				if s == subject {
					present = true
					if !add {
						continue
					}
				}
				out = append(out, s)
			}
			if add && !present {
				out = append(out, subject)
			}
			if !add && !present {
				return "", fmt.Errorf("%w: %s is not in %s", jobs.ErrRefused, subject, keyAdminSubs)
			}
			if err := setJSON(f, keyAdminSubs, out); err != nil {
				return "", err
			}
			return verb + " " + subject, nil
		},
	})
	p.result["subject"] = subject
	p.result["pending_until_restart"] = true
	p.warn("admin subjects are read at start-up: this change is `pending` until the API restarts, and the proof is a " +
		"surviving admin answering 200 and the removed one 403")
	return nil
}

// ---------------------------------------------------------------- service accounts

func planSACreate(_ context.Context, p *planner, args map[string]any) error {
	return p.planSA(args, "create", false)
}

func planSADisable(_ context.Context, p *planner, args map[string]any) error {
	return p.planSA(args, "disable", true)
}

func planSAEnable(_ context.Context, p *planner, args map[string]any) error {
	return p.planSA(args, "enable", false)
}

func (p *planner) planSA(args map[string]any, action string, destructive bool) error {
	p.need(model.LockTenant)
	subject := argStringOf(args, "subject")
	existing := false
	for _, sa := range p.t.ServiceAccounts {
		if sa.Subject == subject {
			existing = true
		}
	}
	switch action {
	case "create":
		if existing {
			return p.refuse("%s already has a service account %q", p.t.Name, subject)
		}
	default:
		if !existing {
			return p.refuse("%s has no service account %q", p.t.Name, subject)
		}
	}
	if action == "disable" {
		// A disabled subject whose key is still in the ledger is a subject
		// that keeps working: the key authenticates, the subject never gets
		// looked up. Saying "disabled" then would be false.
		for _, k := range p.t.Keys {
			if k.RevokedAt == "" && k.TenantString == subject {
				return p.refuse("a surviving API key (%s) carries the subject %q; disabling the service account would "+
					"not stop it — revoke the key first", k.ID, subject)
			}
		}
	}
	origin := fmt.Sprintf("http://127.0.0.1:%d", p.t.Ports.API)
	// Role and purpose are sa-create's arguments; sa-disable and sa-enable do
	// not take them, and the tenant API does not need them to act on a subject
	// that already exists — so they are empty there rather than guessed from
	// the registry row, which can disagree with the tenant's own ledger.
	role, purpose := argStringOf(args, "role"), argStringOf(args, "purpose")
	// The admin credential this call presents is read from the tenant's
	// secrets.env by the real client (PR-D). The planner does not read secrets,
	// so it passes none and the pending driver refuses before any request is
	// made.
	apiKey := ""
	p.addFor("tenantapi", step{
		Kind: "tenantapi", Title: fmt.Sprintf("%s the service account %q through the tenant API", action, subject),
		Destructive: destructive, Targets: []string{subject, origin},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("sa:" + action + ":" + subject); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.TenantAPI().ServiceAccount(
				ctx, origin, apiKey, subject, role, purpose, action); err != nil {
				return "", err
			}
			return action + " " + subject, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			inverse := map[string]string{"create": "disable", "disable": "enable", "enable": "disable"}[action]
			return "rolled back to " + inverse, sc.Ops.Drivers.TenantAPI().ServiceAccount(
				ctx, origin, apiKey, subject, role, purpose, inverse)
		},
	})
	p.result["subject"] = subject
	p.result["service_account_status"] = map[string]string{"create": "active", "enable": "active", "disable": "disabled"}[action]
	return nil
}

// ---------------------------------------------------------------- env edits

// envEdit is one atomic rewrite of one env file: a timestamped backup of what
// was there, then the new content, both through the Files driver.
type envEdit struct {
	Path        string
	Op          string
	Title       string
	Targets     []string
	Secret      bool // the file's content is credentials: no preview, ever
	Destructive bool
	// Mutate applies the change to the parsed file and returns the log line.
	Mutate func(f *envfile.File) (string, error)
}

// addEnvEdit plans the edit and, when the file is not secret-bearing, shows
// what the result would look like.
//
// The preview is computed by applying Mutate to a copy of the file AT PLAN
// TIME, which is also the only honest way to show it. For a secret-bearing
// file there is no preview at all (plan.json: `null` for secret content) —
// the plan names the path and the mode, which is what an operator needs to
// check, and a minted key never reaches an audit row.
func (p *planner) addEnvEdit(ctx context.Context, e envEdit) {
	mode := "0640"
	var writes []model.WouldWrite
	var warns []string
	if e.Secret {
		writes = []model.WouldWrite{secretWrite(e.Path, mode)}
		warns = append(warns, "the content is credentials, so the plan shows no preview")
	} else if b, err := p.readFile(ctx, e.Path); err != nil {
		warns = append(warns, "the file could not be read while planning ("+err.Error()+"), so there is no preview")
		writes = []model.WouldWrite{{Path: e.Path, Mode: mode, Preview: ""}}
	} else if f, perr := envfile.Parse(b); perr != nil {
		warns = append(warns, "the file does not parse ("+perr.Error()+"), so there is no preview; `env normalize` first")
		writes = []model.WouldWrite{{Path: e.Path, Mode: mode, Preview: ""}}
	} else if _, merr := e.Mutate(f); merr != nil {
		warns = append(warns, "the edit could not be previewed ("+merr.Error()+")")
		writes = []model.WouldWrite{{Path: e.Path, Mode: mode, Preview: ""}}
	} else {
		writes = []model.WouldWrite{p.preview(e.Path, mode, f.Render())}
	}
	p.add(step{
		Kind: "envfile", Title: e.Title, Destructive: e.Destructive, Targets: append(e.Targets, e.Path),
		WouldWrite: writes,
		Warnings:   append(warns, "the previous content is kept beside it as `.bak-"+e.Op+"-<ts>`"),
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			b, err := files.ReadFile(ctx, e.Path)
			if err != nil {
				return "", fmt.Errorf("reading %s: %w", e.Path, err)
			}
			f, err := envfile.Parse(b)
			if err != nil {
				return "", fmt.Errorf("%w: %s does not parse: %v", jobs.ErrRefused, e.Path, err)
			}
			log, err := e.Mutate(f)
			if err != nil {
				return "", err
			}
			bak := e.Path + ".bak-" + e.Op + "-" + p.stampOf(sc)
			// Both paths are recorded BEFORE the first write: a crash between
			// the backup and the rewrite leaves a record naming the copy that
			// holds what was there.
			if err := sc.Checkpoint("file:"+bak, "file:"+e.Path); err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, bak, b, 0o640); err != nil {
				return "", fmt.Errorf("writing the backup %s: %w", bak, err)
			}
			if err := files.WriteAtomic(ctx, e.Path, f.Render(), 0o640); err != nil {
				return "", err
			}
			sc.Logf("%s (backup %s)", log, filepath.Base(bak))
			return log, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// The backup is the rollback: whatever the step wrote, what was
			// there is one rename away for as long as the copy exists.
			for _, id := range sc.Step.ExternalIDs {
				if strings.HasPrefix(id, "file:") && strings.Contains(id, ".bak-"+e.Op+"-") {
					bak := strings.TrimPrefix(id, "file:")
					b, err := sc.Ops.Drivers.Files().ReadFile(ctx, bak)
					if err != nil {
						return "", err
					}
					return "restored " + e.Path + " from " + filepath.Base(bak),
						sc.Ops.Drivers.Files().WriteAtomic(ctx, e.Path, b, 0o640)
				}
			}
			return "", fmt.Errorf("no backup was recorded for %s", e.Path)
		},
	})
}

// addRestartOrPending plans the restart the caller asked for, or records that
// the change is configured but not yet effective.
func (p *planner) addRestartOrPending(args map[string]any, pending string) error {
	if argBoolOf(args, "restart") && p.sup == nil {
		// It used to fall through to the `pending` branch, which reported
		// "configured, pending a restart" for a restart the operator had
		// asked for and the ctl had silently declined to perform. A hand-
		// started tenant has no supervision the ctl owns; saying so is the
		// only honest answer, and the remedy is a real one.
		return p.refuse("`restart` was asked for but %s is a hand-started tenant (supervisor: %s): the ctl has no "+
			"way to restart it. Restart it the way you started it, or hand it over first "+
			"(`ragstack-ctl tenant handover %s --release`)", p.t.Name, p.t.Supervisor, p.t.Name)
	}
	if argBoolOf(args, "restart") && p.sup != nil {
		legs, _ := p.legs([]string{"api"})
		for _, c := range legs {
			if err := p.sup.stopLeg(p, c); err != nil {
				return err
			}
			if err := p.sup.startLeg(p, c); err != nil {
				return err
			}
		}
		p.addReadyStep(legs)
		p.result["effective"] = true
		return nil
	}
	p.result["effective"] = false
	p.result["pending_until_restart"] = true
	p.warn(pending)
	return nil
}

// readFile reads a file through the Files driver for a PLAN preview.
func (p *planner) readFile(ctx context.Context, path string) ([]byte, error) {
	if p.oc.Drivers == nil {
		return nil, fmt.Errorf("no drivers")
	}
	return p.oc.Drivers.Files().ReadFile(ctx, path)
}

// ---------------------------------------------------------------- JSON env values

// jsonList reads a JSON array env value ([] when absent).
func jsonList(f *envfile.File, key string) ([]string, error) {
	v, ok := f.Get(key)
	if !ok || strings.TrimSpace(v) == "" {
		return nil, nil
	}
	var out []string
	if err := json.Unmarshal([]byte(v), &out); err != nil {
		return nil, fmt.Errorf("%w: %s is not a JSON array: %v", jobs.ErrRefused, key, err)
	}
	return out, nil
}

// jsonMap reads a JSON object env value ({} when absent).
func jsonMap(f *envfile.File, key string) (map[string]string, error) {
	out := map[string]string{}
	v, ok := f.Get(key)
	if !ok || strings.TrimSpace(v) == "" {
		return out, nil
	}
	if err := json.Unmarshal([]byte(v), &out); err != nil {
		return nil, fmt.Errorf("%w: %s is not a JSON object: %v", jobs.ErrRefused, key, err)
	}
	return out, nil
}

// setJSON writes v as compact JSON. envfile chooses the quoting, and for a
// value carrying `"` that is single quotes — the form the file is shell-
// sourced with.
func setJSON(f *envfile.File, key string, v any) error {
	if v == nil {
		v = []string{}
	}
	if ss, ok := v.([]string); ok && ss == nil {
		v = []string{}
	}
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return f.Set(key, string(b))
}

// mintToken is Python's `secrets.token_hex(32)`: 32 random bytes, 64 hex
// characters.
func mintToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// fingerprint is the registry's key fingerprint: "sha256:" + hex[:16].
func fingerprint(key string) string {
	sum := sha256.Sum256([]byte(key))
	return "sha256:" + hex.EncodeToString(sum[:])[:16]
}
