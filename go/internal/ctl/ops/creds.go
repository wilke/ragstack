package ops

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path/filepath"
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
	for _, k := range p.t.Keys {
		if k.ID == label && k.RevokedAt == "" {
			return p.refuse("%s already has an effective key labelled %q; revoke it first or pick another label",
				p.t.Name, label)
		}
	}
	path, legacy := p.credFile()
	if legacy {
		p.warn("this tenant's env layout is `legacy`: its key ledger lives in tenant.env rather than secrets.env; " +
			"`env normalize` splits them")
	}
	tenant := p.t.Name

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
	p.result["key_id"] = label
	p.result["role"] = role
	p.addRestartOrPending(args, "the new key is configured; it is EFFECTIVE only after the API reloads its env")
	return nil
}

// ---------------------------------------------------------------- key revoke

func planKeyRevoke(ctx context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	id := argStringOf(args, "id")
	var want string
	for _, k := range p.t.Keys {
		if k.ID == id {
			want = k.Fingerprint
		}
	}
	if want == "" {
		return p.refuse("%s has no key %q in its ledger (the ctl revokes by ledger id, never by value)", p.t.Name, id)
	}
	effective := 0
	for _, k := range p.t.Keys {
		if k.Role == "admin" && k.RevokedAt == "" && k.ID != id {
			effective++
		}
	}
	if effective == 0 && ledgerRole(p.t.Keys, id) == "admin" {
		return p.refuse("%q is the last effective admin key of %s; revoking it would leave the tenant with no "+
			"administrator (mint a replacement first)", id, p.t.Name)
	}
	path, _ := p.credFile()
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
					found = true
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
	p.result["key_id"] = id
	p.addRestartOrPending(args, "the key is revoked in the FILE; it keeps working until the API reloads its env "+
		"(and the proof is the old key answering 401)")
	return nil
}

// ledgerRole is the role recorded for the ledger id, or "".
func ledgerRole(keys []registry.Key, id string) string {
	for _, k := range keys {
		if k.ID == id {
			return k.Role
		}
	}
	return ""
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
func (p *planner) addRestartOrPending(args map[string]any, pending string) {
	if argBoolOf(args, "restart") && p.t.Supervisor == supervisorSystemd {
		legs, _ := p.legs([]string{"api"})
		for _, c := range legs {
			p.addUnitStep("stop", c)
			p.addUnitStep("start", c)
		}
		p.addReadyStep(legs)
		p.result["effective"] = true
		return
	}
	p.result["effective"] = false
	p.result["pending_until_restart"] = true
	p.warn(pending)
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
