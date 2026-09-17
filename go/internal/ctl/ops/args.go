package ops

import (
	"encoding/json"
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
	argInt
	argStringArray
	argObject // create's nested shapes, checked by the op rather than field-wise
	argObjectArray
)

func (k argKind) String() string {
	switch k {
	case argBool:
		return "boolean"
	case argInt:
		return "integer"
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
	// Minimum and Maximum bound an argInt. Both zero means unbounded — no
	// contract integer today is legitimately allowed to be 0, so "unset" and
	// "zero" do not have to be told apart.
	Minimum int
	Maximum int
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
	case argInt:
		n, ok := asInt(v)
		if !ok {
			return bad("must be an integer, got %T", v)
		}
		if f.Minimum != 0 && n < f.Minimum {
			return bad("%d is below the minimum %d", n, f.Minimum)
		}
		if f.Maximum != 0 && n > f.Maximum {
			return bad("%d is above the maximum %d", n, f.Maximum)
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
	patGitRef  = `^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$`
	patAbsPath = `^/[A-Za-z0-9._/-]{0,255}$`
	// patSandboxName is patTenantName narrowed to the selftest's own names.
	//
	// The prefix is part of the GRAMMAR rather than a check further down,
	// because `create-sandbox` allocates out of the selftest port range: a
	// request whose name does not say `ctltest-` has to be refused by the
	// argument validator, before a plan exists at all. (The 23-character tail
	// keeps the whole name inside patTenantName's 32.)
	patSandboxName = `^ctltest-[a-z0-9-]{1,23}$`
	// patHandoverToken is the release's nonce: 16 random bytes as hex.
	patHandoverToken = `^[0-9a-f]{32}$`
)

var (
	// components is `only`'s enum. `postgres` joins it with PR-D: a tenant
	// created with `--postgres local` runs a server the ctl owns, and a leg the
	// ctl starts and stops is a leg `--only` has to be able to name.
	components = []string{"api", "ui", "qdrant", "es", "postgres"}
	phases     = []string{"execute", "commit", "rollback"}
	// handoverPhases is NOT `phases`: a handover is two jobs run by two
	// accounts plus a continuation, and naming its phases `execute` would
	// promise the single job this host cannot run (ops/handover.go).
	handoverPhases = []string{"release", "take", "commit", "abandon"}
	roles          = []string{"admin", "user"}
	// backupScopes is `backup.scope`'s item enum: the three legs a bundle can
	// be asked for. `secrets` is NOT one of them — the sealed payload follows
	// `config`, because a bundle carrying a tenant's configuration and not its
	// credentials is a bundle a restore cannot finish from.
	backupScopes = []string{"config", "state", "stores"}
	// uiModes is set-ui-mode's enum. It is NARROWER than the registry's
	// (static|dev|external): `dev` is a Vite server the CTL would have to
	// supervise, which `supervisor: instance` refuses outright, so there is no
	// direction for this op to move a tenant in.
	uiModes = []string{"static", "external"}
	// apiBinds is set-bind's enum: registry.json's api.bind minus
	// `localhost`, which is a name rather than an address and resolves
	// differently depending on /etc/hosts.
	apiBinds = []string{"127.0.0.1", "0.0.0.0"}
	// setSupervisors is set-supervisor's enum: the registry's three values
	// minus `systemd`, which PR-D2 postponed — a row this op moved onto
	// `systemd` would name units no manager on this host can be made to load.
	setSupervisors = []string{"manual", "instance"}
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
		{Name: "scope", Kind: argStringArray, ItemEnum: backupScopes, Unique: true},
	}},
	"restore": {Verb: "restore", Fields: []argField{
		{Name: "from", Kind: argString, Required: true, Pattern: patBundleID},
		{Name: "as", Kind: argString, Required: true, Pattern: patTenantName},
	}},
	"handover": {Verb: "handover", Fields: []argField{
		{Name: "phase", Kind: argString, Required: true, Enum: handoverPhases},
		{Name: "token", Kind: argString, Pattern: patHandoverToken},
		{Name: "accept_no_backup", Kind: argBool},
	}},
	"migrate-local": {Verb: "migrate-local", Fields: []argField{
		{Name: "phase", Kind: argString, Required: true, Enum: phases},
	}},
	"decommission": {Verb: "decommission"},
	"key-mint": {Verb: "key-mint", Fields: []argField{
		{Name: "label", Kind: argString, Required: true, Pattern: patLabel},
		{Name: "role", Kind: argString, Required: true, Enum: roles},
		{Name: "restart", Kind: argBool},
		{Name: "prove", Kind: argBool},
	}},
	"key-revoke": {Verb: "key-revoke", Fields: []argField{
		{Name: "id", Kind: argString, Required: true, Pattern: patLabel},
		{Name: "restart", Kind: argBool},
		{Name: "prove", Kind: argBool},
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
		// `manual` is not offered: it describes a tenant somebody else
		// started, which `adopt` records and `create` cannot produce. Absent
		// means the ctl's own default (ctl.env CTL_DEFAULT_SUPERVISOR), which
		// is why this is not required and has no default here — a value
		// nobody chose must not land in the plan hash or the audit row.
		{Name: "supervisor", Kind: argString, Enum: []string{"systemd", "instance"}},
		{Name: "ui_mode", Kind: argString, Enum: []string{"static", "dev", "external"}},
		{Name: "start", Kind: argBool},
		{Name: "gateway", Kind: argBool},
	}},
	// artifact-prepare is CLI-only: contracts/ctl/openapi.yaml carries it under
	// `x-ctl-cli-op-args`, NOT `x-ctl-op-args`, because the latter is the
	// schema list for POST /v1/tenants/{name}/ops/{verb} and this verb has no
	// HTTP route at all (see ops/artifact.go for why).
	// create-sandbox is create's args with create's name rule replaced. The
	// field list is kept in the same order as `create`'s and must stay equal to
	// it but for `name`: the two verbs build the SAME createSpec and differ only
	// in which allocator they take a port block from.
	"create-sandbox": {Verb: "create-sandbox", Fields: []argField{
		{Name: "name", Kind: argString, Required: true, Pattern: patSandboxName},
		{Name: "artifact_id", Kind: argString, Required: true, Pattern: patArtifactID},
		{Name: "es_heap", Kind: argString, Pattern: patESHeap},
		{Name: "postgres", Kind: argString, Enum: []string{"sqlite", "local"}},
		{Name: "identity_provider", Kind: argString, Enum: []string{"bvbrc", "none"}},
		{Name: "admin_subjects", Kind: argStringArray, ItemPattern: patSubjectIss, Unique: true},
		{Name: "keys", Kind: argObjectArray},
		{Name: "service_accounts", Kind: argObjectArray},
		{Name: "template_from", Kind: argString, Pattern: patTenantName},
		{Name: "settings", Kind: argObject},
		// `manual` is not offered: it describes a tenant somebody else
		// started, which `adopt` records and `create` cannot produce. Absent
		// means the ctl's own default (ctl.env CTL_DEFAULT_SUPERVISOR), which
		// is why this is not required and has no default here — a value
		// nobody chose must not land in the plan hash or the audit row.
		{Name: "supervisor", Kind: argString, Enum: []string{"systemd", "instance"}},
		{Name: "ui_mode", Kind: argString, Enum: []string{"static", "dev", "external"}},
		{Name: "start", Kind: argBool},
		{Name: "gateway", Kind: argBool},
	}},
	// set-ui-mode and set-bind are CLI-only for the reasons the contract's
	// x-ctl-cli-op-args comment gives: the first reaches the network and
	// renames directories the daemon's account does not own, and both act on
	// rows whose tenant the daemon cannot supervise at all (supervisor:
	// manual, owner: wilke) until PR-E2's handover.
	"set-ui-mode": {Verb: "set-ui-mode", Fields: []argField{
		{Name: "mode", Kind: argString, Required: true, Enum: uiModes},
		{Name: "ui_port", Kind: argInt, Minimum: 1024, Maximum: 65535},
	}},
	"set-bind": {Verb: "set-bind", Fields: []argField{
		{Name: "bind", Kind: argString, Required: true, Enum: apiBinds},
	}},
	// set-supervisor corrects the ROW and touches no process. CLI-only for
	// the reason the contract gives: it is the repair for a row that
	// disagrees with the host, and the account that can see which of the two
	// is wrong is the one sitting in front of it.
	"set-supervisor": {Verb: "set-supervisor", Fields: []argField{
		{Name: "supervisor", Kind: argString, Required: true, Enum: setSupervisors},
	}},
	// env-pg-password takes no arguments: what it writes is derived from what
	// is already on disk, and a value passed in would be a credential on a
	// command line.
	"env-pg-password": {Verb: "env-pg-password"},
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
var CLIVerbs = []string{"artifact-prepare", "create-sandbox", "set-ui-mode", "set-bind", "set-supervisor",
	"env-pg-password"}

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

// asInt reads a contract integer out of a decoded args map. JSON numbers reach
// the daemon as float64 (encoding/json's default) and the CLI builds the same
// map with a plain int, so both have to be accepted — and a float64 that is not
// a whole number is NOT an integer, which is the case a bare type switch on
// float64 would have let through.
func asInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		if n != float64(int(n)) {
			return 0, false
		}
		return int(n), true
	case json.Number:
		i, err := n.Int64()
		if err != nil {
			return 0, false
		}
		return int(i), true
	default:
		return 0, false
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

// argIntOf reads a validated integer; a missing or malformed value is 0, which
// every caller reads as "absent" (no contract integer may be zero).
func argIntOf(args map[string]any, name string) int {
	n, _ := asInt(args[name])
	return n
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
