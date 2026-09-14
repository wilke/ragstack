package doctor

import (
	"encoding/json"
	"errors"
	"fmt"
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

// IncludeRel and LegacyMapsRel are the two files that can carry the live
// routing tables: the generated include coconut-proxy's deploy drops in (a
// regular bootstrap copy at first, a symlink into a published generation
// afterwards), and the hand-written map file that predates it.
var (
	IncludeRel    = filepath.Join("conf.d", "05-tenants.generated.conf")
	LegacyMapsRel = filepath.Join("conf.d", "00-maps.conf")
	routesRel     = filepath.Join("snippets", "routes.conf")
)

// GatewayMaps reads the live routing tables from a proxy tree: the generated
// include when it exists, else the hand-written map file. ok=false with a nil
// error means neither file is THERE, which is a fact (no gateway generation
// yet) rather than a problem.
//
// A read that fails for any OTHER reason — EACCES because the proxy tree is
// owned by another account, a dangling symlink left by a half-finished deploy
// — is returned. Swallowing it and falling through to the legacy file was the
// bug: the caller could not tell "coconut-proxy has not deployed yet" from
// "the live routing is unreadable", and both a semantic-no-op verdict and an
// adopted display_order were being decided on a file that is not what nginx
// serves.
func GatewayMaps(proxyDir string) (TenantMaps, bool, error) {
	for _, rel := range []string{IncludeRel, LegacyMapsRel} {
		p := filepath.Join(proxyDir, rel)
		b, err := os.ReadFile(p)
		switch {
		case err == nil:
			return ParseTenantMaps(b, resolveSource(p)), true, nil
		case errors.Is(err, os.ErrNotExist):
			continue
		default:
			return TenantMaps{}, false, err
		}
	}
	return TenantMaps{}, false, nil
}

// resolveSource names the file a report should cite. The generated include is
// a SYMLINK into a published generation once the ctl has published; naming the
// link tells the operator nothing they did not already know, while the
// generation directory it points at is the answer to "compared against what?".
func resolveSource(p string) string {
	fi, err := os.Lstat(p)
	if err != nil || fi.Mode()&os.ModeSymlink == 0 {
		return p
	}
	target, err := os.Readlink(p)
	if err != nil {
		return p
	}
	if !filepath.IsAbs(target) {
		target = filepath.Join(filepath.Dir(p), target)
	}
	return target
}

// liveTenantsNames matches the bare `"tenants":["a","b"]` list the landing
// page and the 404 bodies carry; liveTenantsObjects matches the table
// `location = /ragstack/tenants` returns. Both live inside a single-quoted
// nginx `return` body, which cannot contain a quote of its own.
var (
	liveTenantsNames   = regexp.MustCompile(`"tenants"\s*:\s*(\["[^\]]*"\]|\[\])`)
	liveTenantsObjects = regexp.MustCompile(`return\s+200\s+'\{"tenants":(\[\{.*?\}\])\}`)
)

// TenantLists is the pair of tenant-list literals the gateway serves today,
// and where each was read from.
type TenantLists struct {
	NamesJSON   string // $tenants_names_json / the bare `"tenants":[…]` list
	TenantsJSON string // $tenants_json / the `[{name,api,ui}]` table

	// NamesSource and TenantsSource name the file each list came from. They
	// are separate because a MIXED tree is real: routes.conf can still carry
	// one hand-written literal while the generated include already supplies
	// the other.
	NamesSource   string
	TenantsSource string
}

// Source is the one file to cite for the pair.
func (l TenantLists) Source() string {
	if l.NamesSource != "" {
		return l.NamesSource
	}
	return l.TenantsSource
}

// LiveTenantLists is the single answer to "where does the tenant list nginx
// serves actually live?", and both doctor.DisplayOrder and the gateway's
// DiffDetail go through it so the two cannot disagree.
//
// Precedence is what nginx SERVES, not what looks newest:
//
//  1. A literal list inside snippets/routes.conf. Pre-deploy the two lists are
//     hand-written `return 200 '…'` bodies, and a `return` body is served
//     verbatim — it wins over any map, in a mixed tree too.
//  2. Otherwise the generated include, whose `$tenants_names_json` /
//     `$tenants_json` maps are what a post-deploy routes.conf interpolates.
//     It is read through a symlink, because after the first publish that is
//     what it is.
//  3. Otherwise nothing.
//
// An EMPTY list is "nothing here", not an answer: a routes.conf whose literal
// is `[]` is a tree that advertises no tenants, and the include — which does
// carry them — is what the next reload will serve.
func LiveTenantLists(proxyDir string) (TenantLists, error) {
	var out TenantLists
	routes := filepath.Join(proxyDir, routesRel)
	b, err := readIfPresent(routes)
	if err != nil {
		return out, err
	}
	if b != nil {
		if m := liveTenantsNames.FindSubmatch(b); m != nil && !emptyList(string(m[1])) {
			out.NamesJSON, out.NamesSource = string(m[1]), routes
		}
		if m := liveTenantsObjects.FindSubmatch(b); m != nil && !emptyList(string(m[1])) {
			out.TenantsJSON, out.TenantsSource = string(m[1]), routes
		}
	}
	if out.NamesJSON != "" && out.TenantsJSON != "" {
		return out, nil
	}
	inc := filepath.Join(proxyDir, IncludeRel)
	b, err = readIfPresent(inc)
	if err != nil || b == nil {
		return out, err
	}
	m := ParseTenantMaps(b, resolveSource(inc))
	if out.NamesJSON == "" && !emptyList(m.NamesJSON) {
		out.NamesJSON, out.NamesSource = m.NamesJSON, m.Source
	}
	if out.TenantsJSON == "" && !emptyList(m.TenantsJSON) {
		out.TenantsJSON, out.TenantsSource = m.TenantsJSON, m.Source
	}
	return out, nil
}

// emptyList is true for a list that advertises nothing — absent, `[]`, or
// whitespace around the two brackets.
func emptyList(s string) bool {
	s = strings.TrimSpace(s)
	return s == "" || s == "[]" || s == "[ ]"
}

func readIfPresent(p string) ([]byte, error) {
	b, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	return b, err
}

// DisplayOrderFromRoutes reads the tenant order the gateway currently
// advertises, so an adopted fleet's display_order matches what users already
// see. Returns nil when the file is absent or carries no such literal.
func DisplayOrderFromRoutes(routesConf string) []string {
	b, err := os.ReadFile(routesConf)
	if err != nil {
		return nil
	}
	m := liveTenantsNames.FindSubmatch(b)
	if m == nil {
		return nil
	}
	var names []string
	if err := json.Unmarshal(m[1], &names); err != nil {
		return nil
	}
	return names
}

// DisplayOrder reads the tenant order the gateway currently advertises, from
// whichever file LiveTenantLists says is serving it. Returns nil, nil when no
// source has an order to give.
//
// An unreadable proxy tree is an ERROR, not an empty order: `adopt-all
// --commit` writes display_order from this, and "the file could not be read"
// silently became "the fleet has no order", which reorders the landing page
// on the next publish. Fail loudly instead.
func DisplayOrder(proxyDir string) ([]string, error) {
	lists, err := LiveTenantLists(proxyDir)
	if err != nil {
		return nil, fmt.Errorf("reading the live tenant list under %s: %w", proxyDir, err)
	}
	if lists.NamesJSON == "" {
		return nil, nil
	}
	var names []string
	if err := json.Unmarshal([]byte(lists.NamesJSON), &names); err != nil {
		return nil, fmt.Errorf("%s: the live tenant list is not a JSON array of names: %w", lists.NamesSource, err)
	}
	return names, nil
}

// UIDistIndex is the file nginx serves a static tenant's UI from:
// <data_dir>/ui/dist/index.html, the `vite build` output the alias block in
// tenants-ui-static.generated.conf points at.
func UIDistIndex(dataDir string) string {
	return filepath.Join(dataDir, "ui", "dist", "index.html")
}

// StaticUIDistOK reports whether that index is a REGULAR file. A directory, a
// dangling symlink or an unreadable parent all mean the same thing to nginx:
// the mount 404s. adopt and doctor both ask through here so a tenant cannot
// pass adoption and then be judged by a different rule afterwards — the dist
// is built once and deleted or rebuilt many times, which is exactly why the
// check has to run again on every doctor pass.
func StaticUIDistOK(dataDir string) bool {
	fi, err := os.Stat(UIDistIndex(dataDir))
	return err == nil && fi.Mode().IsRegular()
}
