package registry

import (
	"encoding/json"
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

// AdminSubjectsIsJSON reports whether an ADMIN_SUBJECTS value is in the JSON
// form, so an edit can write back the form it found.
func AdminSubjectsIsJSON(v string) bool {
	return strings.HasPrefix(strings.TrimSpace(v), "[")
}

// IdentityFromEnv derives the row's Identity from the two env values. An
// unset or empty provider is `none` (the API's own default). ok is false when
// the provider is not one of bvbrc|oidc|none; the identity then records
// `none`, and the caller decides whether that is a warning or a refusal.
func IdentityFromEnv(provider, adminSubjects string) (id Identity, ok bool) {
	id = Identity{Provider: "none", AdminSubjectsCount: len(AdminSubjects(adminSubjects))}
	switch provider {
	case "bvbrc", "oidc", "none":
		id.Provider = provider
	case "":
	default:
		return id, false
	}
	return id, true
}
