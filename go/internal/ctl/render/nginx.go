package render

import (
	"bytes"
	"encoding/json"
	"fmt"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// nginxRefuse are the characters no rendered nginx value may contain:
// statement/block delimiters, variable expansion, quotes, escapes, newlines.
var nginxRefuse = regexp.MustCompile("[;{}$\"'\\\\\n]")

// Map targets are always loopback: the gateway never forwards off-host.
func loopback(port int) string { return fmt.Sprintf("127.0.0.1:%d", port) }

// validPort is the contract's Port range (unprivileged, ≤ 65535).
func validPort(p int) bool { return p >= 1024 && p <= 65535 }

// NginxConfig is the host side of the two generated includes.
type NginxConfig struct {
	ProxyDir   string // /rag/config/proxy — for the include paths
	CtlUIDist  string // admin bundle; default fleet.ctl.ui_dist
	CtlPort    int    // default fleet.ctl.port
	Generation int64  // stamped into the header; default fleet.generation
}

func (c NginxConfig) withDefaults(f *registry.Fleet) NginxConfig {
	if c.ProxyDir == "" {
		c.ProxyDir = paths.NewRoots(f.RagRoot, paths.Overrides{}).ProxyDir
	}
	if c.CtlUIDist == "" {
		c.CtlUIDist = f.Ctl.UIDist
	}
	if c.CtlPort == 0 {
		c.CtlPort = f.Ctl.Port
	}
	if c.Generation == 0 {
		c.Generation = f.Generation
	}
	return c
}

func checkValue(what, v string) error {
	if v == "" {
		return fmt.Errorf("%s: empty value", what)
	}
	if loc := nginxRefuse.FindStringIndex(v); loc != nil {
		return fmt.Errorf("%s: value contains a character nginx would interpret (%q at %d)", what, v[loc[0]:loc[1]], loc[0])
	}
	for _, r := range v {
		if r < 0x20 || r == 0x7f {
			return fmt.Errorf("%s: control character in value", what)
		}
	}
	return nil
}

type mapRow struct{ key, val string }

// tenantJSON is one entry of $tenants_json (mirrors routes.conf's literal).
type tenantJSON struct {
	Name string `json:"name"`
	API  string `json:"api"`
	UI   string `json:"ui"`
}

// NginxTenants renders conf.d/05-tenants.generated.conf: the three
// `map $tenant …` tables (legacy routes first, then tenants in display
// order) and the two JSON constants routes.conf and ragstack.html read.
func NginxTenants(f *registry.Fleet, cfg NginxConfig) ([]byte, error) {
	cfg = cfg.withDefaults(f)
	var api, ui, ro []mapRow
	seen := map[string]bool{}
	// addName is the gateway's only name gate, so it has to hold for both
	// kinds of row. legacy says which set of rules applies.
	addName := func(n string, legacy bool) error {
		if legacy {
			if err := checkLegacyName(n); err != nil {
				return err
			}
		} else if err := paths.ValidateName(n); err != nil {
			return err
		}
		if err := checkValue("name", n); err != nil {
			return err
		}
		if seen[n] {
			return fmt.Errorf("duplicate gateway name %q", n)
		}
		seen[n] = true
		return nil
	}
	for _, l := range f.LegacyRoutes {
		// A retired row is not in the output, so it must not reserve its name
		// either. addName ran first, so `lucid`, retired the day the tenant
		// `lucid` was created, made the whole gateway file fail to render
		// with "duplicate gateway name" — the registry could not express the
		// ordinary sequence of retiring a legacy route and keeping its name.
		if l.Status == "retired" {
			continue
		}
		if err := addName(l.Name, true); err != nil {
			return nil, fmt.Errorf("legacy route: %w", err)
		}
		if l.API != 0 {
			if !validPort(l.API) {
				return nil, fmt.Errorf("legacy route %s: api port %d out of range", l.Name, l.API)
			}
			api = append(api, mapRow{l.Name, loopback(l.API)})
		}
		if l.UI != 0 {
			if !validPort(l.UI) {
				return nil, fmt.Errorf("legacy route %s: ui port %d out of range", l.Name, l.UI)
			}
			ui = append(ui, mapRow{l.Name, loopback(l.UI)})
		}
		if l.Readonly {
			ro = append(ro, mapRow{l.Name, "ro"})
		}
	}
	var names []string
	var entries []tenantJSON
	for _, t := range sortedTenants(f) {
		if t.Name == "admin" {
			continue
		}
		if err := addName(t.Name, false); err != nil {
			return nil, err
		}
		if t.State == "decommissioned" || t.State == "quarantined" {
			continue
		}
		if !validPort(t.Ports.API) {
			return nil, fmt.Errorf("tenant %s: api port %d out of range", t.Name, t.Ports.API)
		}
		api = append(api, mapRow{t.Name, loopback(t.Ports.API)})
		switch t.UI.Mode {
		case registry.UIModeExternal:
			// `external` with no port is how adopt records "this tenant has
			// no UI the ctl can see" — the contract's mode enum is
			// static|dev|external, so there is no `none` to record. Such a
			// tenant simply gets no $tenant_ui row (the map default is the
			// empty string, which routes.conf already treats as "no UI").
			// Failing here instead took the WHOLE fleet's gateway file down
			// because one tenant was adopted without --ui-port.
			if t.UI.Port == 0 {
				break
			}
			if !validPort(int(t.UI.Port)) {
				return nil, fmt.Errorf("tenant %s: ui mode %s needs a port in range, got %d", t.Name, t.UI.Mode, t.UI.Port)
			}
			ui = append(ui, mapRow{t.Name, loopback(int(t.UI.Port))})
		case registry.UIModeDev:
			// A dev UI IS a port: the ctl renders a vite unit for it, so a
			// missing one is a broken row, not an absent UI.
			if !validPort(int(t.UI.Port)) {
				return nil, fmt.Errorf("tenant %s: ui mode %s needs a port in range, got %d", t.Name, t.UI.Mode, t.UI.Port)
			}
			ui = append(ui, mapRow{t.Name, loopback(int(t.UI.Port))})
		case registry.UIModeStatic, "":
			// served by tenants-ui-static.generated.conf
		default:
			return nil, fmt.Errorf("tenant %s: unknown ui mode %q", t.Name, t.UI.Mode)
		}
		names = append(names, t.Name)
		entries = append(entries, tenantJSON{Name: t.Name, API: "/ragstack/" + t.Name + "/api/v1/...", UI: "/ragstack/" + t.Name + "/ui/"})
	}
	if names == nil {
		names = []string{}
	}
	if entries == nil {
		entries = []tenantJSON{}
	}
	namesJSON, err := compactJSON(names)
	if err != nil {
		return nil, err
	}
	tenantsJSON, err := compactJSON(entries)
	if err != nil {
		return nil, err
	}
	for _, j := range []string{namesJSON, tenantsJSON} {
		// The JSON is built from validated names, so only its own quotes,
		// brackets and braces appear; a single quote, newline, `$` or
		// backslash would break out of the nginx '…' literal.
		if strings.ContainsAny(j, "'\n$\\") {
			return nil, fmt.Errorf("rendered JSON contains a character nginx would interpret: %q", j)
		}
	}

	var b bytes.Buffer
	fmt.Fprintf(&b, "# 05-tenants.generated.conf — generated by ragstack-ctl from registry generation %d. DO NOT EDIT.\n", cfg.Generation)
	b.WriteString("# Tenant -> API / UI / read-only maps and the tenant lists routes.conf and\n# html/ragstack.html read. Legacy routes first, then registry tenants in display order.\n\n")
	writeMap(&b, "$tenant", "$tenant_api", api)
	writeMap(&b, "$tenant", "$tenant_ui", ui)
	writeMap(&b, "$tenant", "$tenant_readonly", ro)
	fmt.Fprintf(&b, "map $host $tenants_names_json {\n    default  '%s';\n}\n\n", namesJSON)
	fmt.Fprintf(&b, "map $host $tenants_json {\n    default  '%s';\n}\n", tenantsJSON)
	return b.Bytes(), nil
}

// legacySlug is the pre-registry gateway name grammar. It is deliberately the
// same slug shape paths.ValidateName enforces, MINUS the reserved list: the
// legacy rows carry names (lucid, asm) that predate the registry and are not
// tenants, so they cannot be required to pass a tenant-name check.
var legacySlug = regexp.MustCompile(`^[a-z][a-z0-9-]{0,31}$`)

// checkLegacyName accepts a legacy route name: a plain slug that is NOT a
// reserved gateway route.
//
// The previous test (`ValidateName(n) != nil && !isLegacyName(n)`) could
// never refuse anything, because isLegacyName was the same regex
// ValidateName applies BEFORE its reserved check — so every name ValidateName
// rejected as reserved, isLegacyName accepted. A legacy row called `admin`
// or `ctl` would have shadowed the gateway's own /ragstack/admin mount with a
// proxy_pass to a port anyone who can edit the registry chooses.
func checkLegacyName(n string) error {
	if !legacySlug.MatchString(n) {
		return fmt.Errorf("legacy name %q is not a slug matching %s", n, legacySlug)
	}
	for _, r := range paths.Reserved {
		if n == r {
			return fmt.Errorf("legacy name %q is reserved (it collides with a gateway route, a shared instance or a built-in)", n)
		}
	}
	return nil
}

func writeMap(b *bytes.Buffer, src, dst string, rows []mapRow) {
	fmt.Fprintf(b, "map %s %s {\n    default  \"\";\n", src, dst)
	w := 0
	for _, r := range rows {
		if len(r.key) > w {
			w = len(r.key)
		}
	}
	for _, r := range rows {
		fmt.Fprintf(b, "    %-*s  \"%s\";\n", w, r.key, r.val)
	}
	b.WriteString("}\n\n")
}

func compactJSON(v any) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	return strings.TrimSuffix(buf.String(), "\n"), nil
}

// NginxStatic renders snippets/tenants-ui-static.generated.conf: for every
// static-UI tenant the redirect + alias pair, and when the ctl's gateway
// mount is enabled the admin UI/API blocks.
func NginxStatic(f *registry.Fleet, cfg NginxConfig) ([]byte, error) {
	cfg = cfg.withDefaults(f)
	cors := filepath.Join(cfg.ProxyDir, "snippets", "cors.conf")
	common := filepath.Join(cfg.ProxyDir, "snippets", "proxy-common.conf")
	for _, p := range []string{cfg.ProxyDir, cors, common} {
		if _, err := paths.SafePath("/", p); err != nil {
			return nil, err
		}
	}
	var b bytes.Buffer
	fmt.Fprintf(&b, "# tenants-ui-static.generated.conf — generated by ragstack-ctl from registry generation %d. DO NOT EDIT.\n", cfg.Generation)
	b.WriteString("# Static (vite build) tenant UIs served by nginx, plus the admin mount. Included by\n# routes.conf before the Vite dev-server UI regex; `^~` makes these win over it.\n")
	for _, t := range sortedTenants(f) {
		if t.UI.Mode != registry.UIModeStatic || t.State == "decommissioned" || t.State == "quarantined" {
			continue
		}
		if err := checkValue("name", t.Name); err != nil {
			return nil, err
		}
		dist := filepath.Join(t.DataDir, "ui", "dist")
		if _, err := paths.SafePath("/", dist); err != nil {
			return nil, fmt.Errorf("tenant %s: %w", t.Name, err)
		}
		fmt.Fprintf(&b, `
location = /ragstack/%[1]s/ui {
    return 301 /ragstack/%[1]s/ui/;
}

location ^~ /ragstack/%[1]s/ui/ {
    include %[2]s;
    alias %[3]s/;
    try_files $uri $uri/ /ragstack/%[1]s/ui/index.html;
}
`, t.Name, cors, dist)
	}
	if f.Ctl.GatewayEnabled {
		if cfg.CtlPort <= 0 || cfg.CtlPort > 65535 {
			return nil, fmt.Errorf("ctl port %d out of range", cfg.CtlPort)
		}
		if _, err := paths.SafePath("/", cfg.CtlUIDist); err != nil {
			return nil, fmt.Errorf("ctl ui_dist: %w", err)
		}
		fmt.Fprintf(&b, `
location = /ragstack/admin/ui {
    return 301 /ragstack/admin/ui/;
}

location ^~ /ragstack/admin/ui/ {
    include %[1]s;
    alias %[2]s/;
    try_files $uri $uri/ /ragstack/admin/ui/admin.html;
}

location ^~ /ragstack/admin/api/ {
    include %[1]s;
    include %[3]s;
    proxy_set_header X-Forwarded-Prefix /ragstack/admin/api;
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:%[4]d/;
}
`, cors, cfg.CtlUIDist, common, cfg.CtlPort)
	}
	return b.Bytes(), nil
}
