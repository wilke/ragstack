package ops

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// The args schema, as a Go table that is asserted EQUAL to
// contracts/ctl/openapi.yaml's `x-ctl-op-args` by TestArgSchemaMatchesContract.
//
// A table rather than a JSON-Schema evaluator because there is no schema
// library in this module and adding one for eighteen tiny object schemas
// would be a dependency bought with a footprint the ctl deliberately does not
// have. The parity test is what keeps the two honest: the contract stays the
// source of truth, and a field added there fails the build here.

type argKind int

const (
	argString argKind = iota
	argBool
	argStringArray
	argObject // create's nested shapes, checked by the op rather than field-wise
	argObjectArray
)

func (k argKind) String() string {
	switch k {
	case argBool:
		return "boolean"
	case argStringArray, argObjectArray:
		return "array"
	case argObject:
		return "object"
	default:
		return "string"
	}
}

// argField is one property of one verb's args object.
type argField struct {
	Name     string
	Kind     argKind
	Required bool
	// Enum constrains a string; ItemEnum constrains an array's items.
	Enum      []string
	ItemEnum  []string
	Pattern   string
	MaxLength int
	Unique    bool
	// ItemPattern constrains an array of strings.
	ItemPattern string
}

// argSpec is one verb's whole args object. additionalProperties is false for
// every verb in the contract, so it is not a field here — it is the rule.
type argSpec struct {
	Verb   string
	Fields []argField
}

func (s argSpec) field(name string) (argField, bool) {
	for _, f := range s.Fields {
		if f.Name == name {
			return f, true
		}
	}
	return argField{}, false
}

// Required lists the required field names, sorted.
func (s argSpec) required() []string {
	var out []string
	for _, f := range s.Fields {
		if f.Required {
			out = append(out, f.Name)
		}
	}
	sort.Strings(out)
	return out
}

// validate enforces the spec exactly: unknown fields are named, required
// fields are named, and every present field is checked for type, enum,
// pattern, length and uniqueness. Every failure is a jobs.ErrValidation, which
// the API layer answers 422 `validation`.
func (s argSpec) validate(args map[string]any) error {
	var unknown []string
	for k := range args {
		if _, ok := s.field(k); !ok {
			unknown = append(unknown, k)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return fmt.Errorf("%w: %s: unknown argument%s %s (accepted: %s)", jobs.ErrValidation, s.Verb,
			plural(len(unknown)), strings.Join(unknown, ", "), strings.Join(s.names(), ", "))
	}
	var missing []string
	for _, f := range s.Fields {
		if f.Required {
			if _, ok := args[f.Name]; !ok {
				missing = append(missing, f.Name)
			}
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return fmt.Errorf("%w: %s: missing required argument%s %s", jobs.ErrValidation, s.Verb,
			plural(len(missing)), strings.Join(missing, ", "))
	}
	for _, name := range sortedKeys(args) {
		f, _ := s.field(name)
		if err := f.check(s.Verb, args[name]); err != nil {
			return err
		}
	}
	return nil
}

func (s argSpec) names() []string {
	out := make([]string, 0, len(s.Fields))
	for _, f := range s.Fields {
		out = append(out, f.Name)
	}
	sort.Strings(out)
	if len(out) == 0 {
		return []string{"(none)"}
	}
	return out
}

func (f argField) check(verb string, v any) error {
	bad := func(format string, a ...any) error {
		return fmt.Errorf("%w: %s.%s: %s", jobs.ErrValidation, verb, f.Name, fmt.Sprintf(format, a...))
	}
	switch f.Kind {
	case argBool:
		if _, ok := v.(bool); !ok {
			return bad("must be a boolean, got %T", v)
		}
	case argString:
		s, ok := v.(string)
		if !ok {
			return bad("must be a string, got %T", v)
		}
		return f.checkString(verb, s, bad)
	case argStringArray:
		items, err := toSlice(v)
		if err != nil {
			return bad("must be an array, got %T", v)
		}
		seen := map[string]bool{}
		for i, it := range items {
			s, ok := it.(string)
			if !ok {
				return bad("[%d] must be a string, got %T", i, it)
			}
			if len(f.ItemEnum) > 0 && !contains(f.ItemEnum, s) {
				return bad("[%d] = %q is not one of %s", i, s, strings.Join(f.ItemEnum, ", "))
			}
			if f.ItemPattern != "" && !regexp.MustCompile(f.ItemPattern).MatchString(s) {
				return bad("[%d] = %q does not match %s", i, s, f.ItemPattern)
			}
			if f.Unique {
				if seen[s] {
					return bad("[%d] = %q is a duplicate; the items must be unique", i, s)
				}
				seen[s] = true
			}
		}
	case argObject:
		if _, ok := v.(map[string]any); !ok {
			return bad("must be an object, got %T", v)
		}
	case argObjectArray:
		items, err := toSlice(v)
		if err != nil {
			return bad("must be an array, got %T", v)
		}
		for i, it := range items {
			if _, ok := it.(map[string]any); !ok {
				return bad("[%d] must be an object, got %T", i, it)
			}
		}
	}
	return nil
}

func (f argField) checkString(_ string, s string, bad func(string, ...any) error) error {
	if len(f.Enum) > 0 && !contains(f.Enum, s) {
		return bad("%q is not one of %s", s, strings.Join(f.Enum, ", "))
	}
	if f.Pattern != "" && !regexp.MustCompile(f.Pattern).MatchString(s) {
		return bad("%q does not match %s", s, f.Pattern)
	}
	if f.MaxLength > 0 && len(s) > f.MaxLength {
		return bad("is %d bytes, over the %d-byte limit", len(s), f.MaxLength)
	}
	return nil
}

// Patterns the contract spells, named so a reader can see where each is used.
const (
	patTenantName = `^[a-z][a-z0-9-]{0,31}$`
	patBundleID   = `^[0-9]{8}T[0-9]{6}Z-(backup|pre-update|recovery)$`
	patLabel      = `^[a-z0-9][a-z0-9-]{0,63}$`
	patSubjectIss = `^[a-z][a-z0-9]*:[^:\s]{1,128}$`
	patSAName     = `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`
	patEnvKey     = `^[A-Z][A-Z0-9_]{0,127}$`
	patArtifactID = `^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`
	patESHeap     = `^[1-9][0-9]*[mg]$`
	// patGitRef is what `fleet artifact prepare --tag` accepts. Deliberately
	// narrow: the value is handed to `git rev-parse` as one argv element, and a
	// ref grammar that admitted spaces, `-` prefixes or shell metacharacters
	// would be a ref grammar in which an option could be smuggled.
	patGitRef = `^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$`
	patAbsPath = `^/[A-Za-z0-9._/-]{0,255}$`
)

var (
	// components is `only`'s enum. `postgres` joins it with PR-D: a tenant
	// created with `--postgres local` runs a server the ctl owns, and a leg the
	// ctl starts and stops is a leg `--only` has to be able to name.
	components = []string{"api", "ui", "qdrant", "es", "postgres"}
	phases     = []string{"execute", "commit", "rollback"}
	roles      = []string{"admin", "user"}
)

// argSchemas is the table. The three entries with no contract row (create,
// gateway-apply, gateway-reload) are marked in contractVerbs below, so the
// parity test compares exactly the contract's set and no more.
var argSchemas = map[string]argSpec{
	"start": {Verb: "start", Fields: []argField{
		{Name: "only", Kind: argStringArray, ItemEnum: components, Unique: true},
		{Name: "force", Kind: argBool},
	}},
	"stop": {Verb: "stop", Fields: []argField{
		{Name: "only", Kind: argStringArray, ItemEnum: components, Unique: true},
		{Name: "keep_enabled", Kind: argBool},
		{Name: "force", Kind: argBool},
	}},
	"restart": {Verb: "restart", Fields: []argField{
		{Name: "only", Kind: argStringArray, ItemEnum: components, Unique: true},
		{Name: "force", Kind: argBool},
	}},
	"backup": {Verb: "backup", Fields: []argField{
		{Name: "fence", Kind: argBool},
		{Name: "tar", Kind: argBool},
	}},
	"restore": {Verb: "restore", Fields: []argField{
		{Name: "from", Kind: argString, Required: true, Pattern: patBundleID},
		{Name: "as", Kind: argString, Required: true, Pattern: patTenantName},
	}},
	"handover": {Verb: "handover", Fields: []argField{
		{Name: "phase", Kind: argString, Required: true, Enum: phases},
	}},
	"migrate-local": {Verb: "migrate-local", Fields: []argField{
		{Name: "phase", Kind: argString, Required: true, Enum: phases},
	}},
	"decommission": {Verb: "decommission"},
	"key-mint": {Verb: "key-mint", Fields: []argField{
		{Name: "label", Kind: argString, Required: true, Pattern: patLabel},
		{Name: "role", Kind: argString, Required: true, Enum: roles},
		{Name: "restart", Kind: argBool},
	}},
	"key-revoke": {Verb: "key-revoke", Fields: []argField{
		{Name: "id", Kind: argString, Required: true, Pattern: patLabel},
		{Name: "restart", Kind: argBool},
	}},
	"admin-add": {Verb: "admin-add", Fields: []argField{
		{Name: "subject", Kind: argString, Required: true, Pattern: patSubjectIss},
	}},
	"admin-remove": {Verb: "admin-remove", Fields: []argField{
		{Name: "subject", Kind: argString, Required: true, Pattern: patSubjectIss},
	}},
	"sa-create": {Verb: "sa-create", Fields: []argField{
		{Name: "subject", Kind: argString, Required: true, Pattern: patSAName},
		{Name: "role", Kind: argString, Required: true, Enum: roles},
		{Name: "purpose", Kind: argString, MaxLength: 256},
	}},
	"sa-disable": {Verb: "sa-disable", Fields: []argField{
		{Name: "subject", Kind: argString, Required: true, Pattern: patSAName},
	}},
	"sa-enable": {Verb: "sa-enable", Fields: []argField{
		{Name: "subject", Kind: argString, Required: true, Pattern: patSAName},
	}},
	"env-set": {Verb: "env-set", Fields: []argField{
		{Name: "key", Kind: argString, Required: true, Pattern: patEnvKey},
		{Name: "value", Kind: argString, Required: true, MaxLength: 4096},
	}},
	"env-unset": {Verb: "env-unset", Fields: []argField{
		{Name: "key", Kind: argString, Required: true, Pattern: patEnvKey},
	}},
	"env-normalize": {Verb: "env-normalize"},
	"render-units": {Verb: "render-units", Fields: []argField{
		{Name: "apply", Kind: argBool},
	}},
	"update-code": {Verb: "update-code", Fields: []argField{
		{Name: "artifact_id", Kind: argString, Required: true, Pattern: patArtifactID},
		{Name: "restart", Kind: argBool},
	}},

	// Not in x-ctl-op-args: `create` has its own body schema
	// (schemas/create_request.json#/$defs/CreateArgs) and the two gateway
	// entry points are fleet-scoped verbs with no arguments at all.
	"create": {Verb: "create", Fields: []argField{
		{Name: "name", Kind: argString, Required: true, Pattern: patTenantName},
		{Name: "artifact_id", Kind: argString, Required: true, Pattern: patArtifactID},
		{Name: "es_heap", Kind: argString, Pattern: patESHeap},
		{Name: "postgres", Kind: argString, Enum: []string{"sqlite", "local"}},
		{Name: "identity_provider", Kind: argString, Enum: []string{"bvbrc", "none"}},
		{Name: "admin_subjects", Kind: argStringArray, ItemPattern: patSubjectIss, Unique: true},
		{Name: "keys", Kind: argObjectArray},
		{Name: "service_accounts", Kind: argObjectArray},
		{Name: "template_from", Kind: argString, Pattern: patTenantName},
		{Name: "settings", Kind: argObject},
		{Name: "ui_mode", Kind: argString, Enum: []string{"static", "dev", "external"}},
		{Name: "start", Kind: argBool},
		{Name: "gateway", Kind: argBool},
	}},
	// artifact-prepare is CLI-only: contracts/ctl/openapi.yaml carries it under
	// `x-ctl-cli-op-args`, NOT `x-ctl-op-args`, because the latter is the
	// schema list for POST /v1/tenants/{name}/ops/{verb} and this verb has no
	// HTTP route at all (see ops/artifact.go for why).
	"artifact-prepare": {Verb: "artifact-prepare", Fields: []argField{
		{Name: "tag", Kind: argString, Required: true, Pattern: patGitRef},
		{Name: "mirror", Kind: argString, Pattern: patAbsPath},
		{Name: "python_env", Kind: argString, Pattern: patAbsPath},
		{Name: "schema_compatible", Kind: argBool},
	}},
	"gateway-apply":  {Verb: "gateway-apply"},
	"gateway-reload": {Verb: "gateway-reload"},
	// PUT /v1/settings: the HTTP layer validates the writable subset of
	// settings_response.json field by field; the op checks the shape again
	// and decides what the registry can persist.
	"settings-put": {Verb: "settings-put", Fields: []argField{
		{Name: "retention", Kind: argObject},
		{Name: "images", Kind: argObject},
		{Name: "python_env_default", Kind: argString},
		{Name: "ctl", Kind: argObject},
	}},
}

// ContractVerbs is the ops endpoint's verb enum — exactly the keys of
// x-ctl-op-args. The three extra entry points are deliberately not in it.
var ContractVerbs = []string{
	"start", "stop", "restart", "backup", "restore", "handover", "migrate-local",
	"decommission", "key-mint", "key-revoke", "admin-add", "admin-remove",
	"sa-create", "sa-disable", "sa-enable", "env-set", "env-unset",
	"env-normalize", "render-units", "update-code",
}

// CLIVerbs are the operations that are jobs like any other but have NO HTTP
// route: the contract lists them under `x-ctl-cli-op-args` and the ops router
// (api/jobs.go's opVerbs) does not know them, so POST …/ops/<verb> is 422.
var CLIVerbs = []string{"artifact-prepare"}

// ---------------------------------------------------------------- helpers

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

func contains(ss []string, s string) bool {
	for _, v := range ss {
		if v == s {
			return true
		}
	}
	return false
}

func toSlice(v any) ([]any, error) {
	switch t := v.(type) {
	case []any:
		return t, nil
	case []string:
		out := make([]any, len(t))
		for i, s := range t {
			out[i] = s
		}
		return out, nil
	default:
		return nil, fmt.Errorf("not an array")
	}
}

func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// Typed readers for a validated args map.

func argBoolOf(args map[string]any, name string) bool {
	b, _ := args[name].(bool)
	return b
}

func argStringOf(args map[string]any, name string) string {
	s, _ := args[name].(string)
	return s
}

func argStringsOf(args map[string]any, name string) []string {
	items, err := toSlice(args[name])
	if err != nil {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, it := range items {
		if s, ok := it.(string); ok {
			out = append(out, s)
		}
	}
	return out
}
