package doctor

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// TenantMaps is what the gateway's routing tables say: which port each
// tenant's API and UI route reaches, and which routes are marked read-only.
// It is parsed from the generated 05-tenants.generated.conf when one is
// published, and from the hand-written conf.d/00-maps.conf until then — the
// two carry the same three maps, which is what makes PR-B's first generated
// file a semantic no-op.
type TenantMaps struct {
	API      map[string]int
	UI       map[string]int
	Readonly map[string]bool
	Source   string // the file it was read from

	// APIAddr and UIAddr are the map values in full — `127.0.0.1:24040`, not
	// 24040.
	//
	// The port alone is not the route. `dev "10.0.0.5:24040"` and
	// `dev "127.0.0.1:24040"` reach different machines, and a comparison that
	// kept only the port called them equal — which is exactly the comparison
	// PR-B's "the generated maps route what the hand-written ones route" claim
	// rests on. The renderer only ever emits loopback, so a live row that is
	// not loopback is a difference the operator has to see, not a rounding
	// error.
	APIAddr map[string]string
	UIAddr  map[string]string

	// NamesJSON and TenantsJSON are the `$tenants_names_json` and
	// `$tenants_json` literals — the tenant lists the landing page and
	// /ragstack/tenants serve. Empty when the file carries neither. They are
	// routing too: a tenant present in the maps but missing from the list is
	// reachable and invisible.
	NamesJSON   string
	TenantsJSON string
}

// mapBlock captures `map $tenant $<name> { … }` including nested braces-free
// bodies (these maps never nest).
var mapBlock = regexp.MustCompile(`(?s)map\s+\$tenant\s+\$(tenant_api|tenant_ui|tenant_readonly)\s*\{(.*?)\n\}`)

// mapRow is one `name "value";` row, with an optional trailing comment.
var mapRow = regexp.MustCompile(`^\s*([A-Za-z0-9_.-]+)\s+"([^"]*)"\s*;`)

// ParseTenantMaps reads the three tenant maps out of an nginx configuration.
// Anything it does not understand is ignored rather than guessed at: a
// missing map yields an empty (non-nil) table.
func ParseTenantMaps(b []byte, source string) TenantMaps {
	m := TenantMaps{
		API: map[string]int{}, UI: map[string]int{}, Readonly: map[string]bool{},
		APIAddr: map[string]string{}, UIAddr: map[string]string{},
		Source: source,
	}
	for _, block := range mapBlock.FindAllStringSubmatch(string(b), -1) {
		kind, body := block[1], block[2]
		for _, line := range strings.Split(body, "\n") {
			if i := strings.Index(line, "#"); i >= 0 {
				line = line[:i]
			}
			row := mapRow.FindStringSubmatch(line)
			if row == nil || row[1] == "default" {
				continue
			}
			name, value := row[1], row[2]
			switch kind {
			case "tenant_api", "tenant_ui":
				port := 0
				if i := strings.LastIndex(value, ":"); i >= 0 {
					port, _ = strconv.Atoi(value[i+1:])
				}
				if port == 0 {
					continue
				}
				if kind == "tenant_api" {
					m.API[name], m.APIAddr[name] = port, value
				} else {
					m.UI[name], m.UIAddr[name] = port, value
				}
			case "tenant_readonly":
				m.Readonly[name] = value == "ro"
			}
		}
	}
	if lit := jsonMapLiteral(string(b), "tenants_names_json"); lit != "" {
		m.NamesJSON = lit
	}
	if lit := jsonMapLiteral(string(b), "tenants_json"); lit != "" {
		m.TenantsJSON = lit
	}
	return m
}

// jsonMapDefault matches `map $host $<name> { default '…'; }`. nginx cannot
// escape a quote inside such a literal, so "up to the next quote" is the exact
// grammar, not an approximation.
var jsonMapDefault = map[string]*regexp.Regexp{
	"tenants_names_json": regexp.MustCompile(`map\s+\$\w+\s+\$tenants_names_json\s*\{[^}]*?\bdefault\s+'([^']*)'`),
	"tenants_json":       regexp.MustCompile(`map\s+\$\w+\s+\$tenants_json\s*\{[^}]*?\bdefault\s+'([^']*)'`),
}

func jsonMapLiteral(s, name string) string {
	re, ok := jsonMapDefault[name]
	if !ok {
		return ""
	}
	m := re.FindStringSubmatch(s)
	if m == nil {
		return ""
	}
	return m[1]
}

// GatewayMaps reads the live routing tables from a proxy tree: the generated
// include when it exists, else the hand-written map file. Returns ok=false
// when neither is readable, which is a fact (no gateway generation yet), not
// an error.
func GatewayMaps(proxyDir string) (TenantMaps, bool) {
	for _, rel := range []string{
		filepath.Join("conf.d", "05-tenants.generated.conf"),
		filepath.Join("conf.d", "00-maps.conf"),
	} {
		p := filepath.Join(proxyDir, rel)
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		return ParseTenantMaps(b, p), true
	}
	return TenantMaps{}, false
}

// tenantsLiteral finds the `"tenants":[ … ]` list routes.conf serves from its
// `location = /` block — the live landing-page order.
var tenantsLiteral = regexp.MustCompile(`"tenants"\s*:\s*(\[[^\]]*\])`)

// DisplayOrderFromRoutes reads the tenant order the gateway currently
// advertises, so an adopted fleet's display_order matches what users already
// see. Returns nil when the file is absent or carries no such literal.
func DisplayOrderFromRoutes(routesConf string) []string {
	b, err := os.ReadFile(routesConf)
	if err != nil {
		return nil
	}
	m := tenantsLiteral.FindSubmatch(b)
	if m == nil {
		return nil
	}
	var names []string
	if err := json.Unmarshal(m[1], &names); err != nil {
		return nil
	}
	return names
}
