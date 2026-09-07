package auth

import (
	"reflect"
	"testing"
)

// The keyed conformance harness writes API_KEYS as a JSON array and the two
// maps as JSON objects (conformance/run_authz_keyed.sh, which pydantic-settings
// parses on the Python side). Both servers must read the same file, so both
// forms are pinned here — including the comma-separated shorthand a person
// types by hand.
func TestParsesTheHarnessConfigAndTheHandForm(t *testing.T) {
	got := parseList(`["k1","k2"]`)
	if want := []string{"k1", "k2"}; !reflect.DeepEqual(got, want) {
		t.Errorf("parseList(JSON) = %v, want %v", got, want)
	}
	if got := parseList("k1, k2 ,"); !reflect.DeepEqual(got, []string{"k1", "k2"}) {
		t.Errorf("parseList(csv) = %v", got)
	}
	if got := parseList("  "); got != nil {
		t.Errorf("an empty API_KEYS is a KEYLESS server, not one key: %v", got)
	}
	m := parseMap(`{"k1":"admin"}`)
	if m["k1"] != "admin" {
		t.Errorf("parseMap(JSON) = %v", m)
	}
	if m := parseMap("k1=admin,k2=user"); m["k1"] != "admin" || m["k2"] != "user" {
		t.Errorf("parseMap(pairs) = %v", m)
	}
	// Malformed JSON must not half-configure a key set: a partially parsed
	// allowlist is a door left open in a shape nobody reviewed.
	if got := parseList(`["k1",`); got != nil {
		t.Errorf("malformed API_KEYS must yield no keys, got %v", got)
	}
}

func TestResolveMapsKeysToSubjectsAndRoles(t *testing.T) {
	a := New(
		[]string{"admin-key", "user-key", "unmapped-key"},
		map[string]string{"admin-key": "s-admin", "user-key": "s-user"},
		map[string]string{"admin-key": RoleAdmin},
		RoleUser,
	)
	if !a.Enabled() {
		t.Fatalf("configured keys must enable authentication")
	}
	p, ok := a.Resolve("admin-key")
	if !ok || p.Tenant != "s-admin" || !p.IsAdmin() {
		t.Fatalf("admin: %+v ok=%v", p, ok)
	}
	p, ok = a.Resolve("user-key")
	if !ok || p.Tenant != "s-user" || p.IsAdmin() {
		t.Fatalf("user: %+v ok=%v", p, ok)
	}
	// A valid but unmapped key gets the default tenant and the default role —
	// never an elevated one.
	p, ok = a.Resolve("unmapped-key")
	if !ok || p.Tenant != DefaultTenant || p.Role != RoleUser {
		t.Fatalf("unmapped: %+v ok=%v", p, ok)
	}
	for _, bad := range []string{"", "nope", "admin-ke", "admin-keyy"} {
		if _, ok := a.Resolve(bad); ok {
			t.Errorf("%q must not authenticate", bad)
		}
	}
}

// Keyless is the open dev/test path this scaffold has always had: every caller
// is the default tenant and nothing is rejected.
func TestKeylessResolvesEveryoneToTheDefaultTenant(t *testing.T) {
	a := New(nil, nil, nil, "")
	if a.Enabled() {
		t.Fatalf("no keys means authentication is off")
	}
	p, ok := a.Resolve("")
	if !ok || p.Tenant != DefaultTenant || p.Role != RoleUser {
		t.Fatalf("keyless: %+v ok=%v", p, ok)
	}
}

// `researcher` is a deprecated alias for `user` (ADR-0003). Normalising it in
// both servers is what keeps a config written against the old vocabulary from
// meaning two different things depending on which one reads it.
func TestResearcherIsNormalizedToUser(t *testing.T) {
	a := New([]string{"k"}, nil, map[string]string{"k": "researcher"}, RoleUser)
	p, _ := a.Resolve("k")
	if p.Role != RoleUser {
		t.Fatalf("role = %q, want %q", p.Role, RoleUser)
	}
}
