package registry

import (
	"encoding/json"
	"fmt"
	"strings"
)

// The bearer-identity summary on a row is DERIVED from two public tenant.env
// keys, IDENTITY_PROVIDER and ADMIN_SUBJECTS. adopt derives it on the way in,
// and every op that edits either key re-derives it from the file it wrote
// (#736) — one function, so the two can never disagree about what a value
// means.

// AdminSubjects parses ADMIN_SUBJECTS in either accepted form: a JSON array
// (`["a","b"]`, what the ctl writes) or comma-separated (`a,b`, what a hand-
// written env uses). The subjects themselves are identities and are
// deliberately NOT carried into the registry; callers count them.
func AdminSubjects(v string) []string {
	v = strings.TrimSpace(v)
	if v == "" {
		return nil
	}
	if strings.HasPrefix(v, "[") {
		var arr []string
		if err := json.Unmarshal([]byte(v), &arr); err == nil {
			return arr
		}
	}
	var out []string
	for _, s := range strings.Split(v, ",") {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// AdminSubjectsStrict is AdminSubjects for a WRITER: a value that starts
// with `[` is JSON or it is an error. The lenient reader comma-splits a broken
// array, and an edit built on that would "repair" `["bvbrc:alice"` into a
// list holding that literal garbage subject — the API (which fails on the
// broken JSON today) would then boot with alice silently no longer an admin.
func AdminSubjectsStrict(v string) ([]string, error) {
	if AdminSubjectsIsJSON(v) {
		var arr []string
		if err := json.Unmarshal([]byte(strings.TrimSpace(v)), &arr); err != nil {
			return nil, fmt.Errorf("ADMIN_SUBJECTS starts with `[` but is not a JSON array of strings: %v", err)
		}
		return arr, nil
	}
	return AdminSubjects(v), nil
}

// NormalizeProvider is IDENTITY_PROVIDER as the API reads it: stripped and
// lower-cased (api/security.py `_configured_provider`, identity/factory.py).
func NormalizeProvider(v string) string { return strings.ToLower(strings.TrimSpace(v)) }

// AdminSubjectsIsJSON reports whether an ADMIN_SUBJECTS value is in the JSON
// form, so an edit can write back the form it found.
func AdminSubjectsIsJSON(v string) bool {
	return strings.HasPrefix(strings.TrimSpace(v), "[")
}

// IdentityFromEnv derives the row's Identity from the two env values. An
// unset or empty provider is `none` (the API's own default). ok is false when
// the provider is not one of bvbrc|oidc|none; the identity then records
// `none`, and the caller decides whether that is a warning or a refusal.
//
// The provider is normalised as the API normalises it (NormalizeProvider), and
// the normalised value is what is recorded. The subject count is the lenient
// one; writers check AdminSubjectsStrict first.
func IdentityFromEnv(provider, adminSubjects string) (id Identity, ok bool) {
	id = Identity{Provider: "none", AdminSubjectsCount: len(AdminSubjects(adminSubjects))}
	provider = NormalizeProvider(provider)
	switch provider {
	case "bvbrc", "oidc", "none":
		id.Provider = provider
	case "":
	default:
		return id, false
	}
	return id, true
}
