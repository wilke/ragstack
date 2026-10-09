package settings

// The executable-surface validation table (#714).
//
// An executable-surface key decides WHAT code runs or WHERE the process
// reaches, which is why the env API refuses every one of them over HTTP
// (ADR-0007). `ragstack-ctl env set-surface` — and the surface half of
// `tenant create --set` — accept them from a trusted operator on the CLI
// (`--direct`), and only in the shapes below. The table is the whole rule: a
// key that is executable-surface and is not classified here is refused, so a
// key added to `executableSurface` without a row is refused until somebody
// decides what its values may be.
//
// The checks are PURE: they read the value and the SurfaceContext the planner
// builds from the registry snapshot and the ctl's configuration, never the
// host. The image-mode bind probe (the API instance must see every path, and
// every sqlite store must be in a read-write bind) needs the render package and
// lives in ops; this file is what every row gets.

import (
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// SurfaceClass is how one executable-surface key's value is validated.
type SurfaceClass int

const (
	// SurfaceRefused: never settable through the ctl, by anyone.
	SurfaceRefused SurfaceClass = iota
	// SurfaceURL: one absolute URL whose host is in the endpoint allowlist.
	SurfaceURL
	// SurfaceURLList: a comma list or a JSON array of SurfaceURL values.
	SurfaceURLList
	// SurfacePath: an absolute path under the tenant's data dir or an API
	// bind root, and never under the ctl's own dirs or another tenant's.
	SurfacePath
	// SurfaceCWL: a worktree row's CWL path — under the tenant's worktree
	// or its data dir. (On an image row the three CWL keys are refused.)
	SurfaceCWL
	// SurfaceImageDirs: GOWE_IMAGE_DIRS, a comma list of directories under
	// the API bind roots.
	SurfaceImageDirs
	// SurfaceRoutes: QDRANT_/ES_COLLECTION_ROUTES, a JSON object of physical
	// store name -> SurfaceURL.
	SurfaceRoutes
)

func (c SurfaceClass) String() string {
	switch c {
	case SurfaceURL:
		return "url"
	case SurfaceURLList:
		return "url-list"
	case SurfacePath:
		return "path"
	case SurfaceCWL:
		return "cwl-path"
	case SurfaceImageDirs:
		return "image-dirs"
	case SurfaceRoutes:
		return "routes"
	default:
		return "refused"
	}
}

// surfaceRefusedAlways are the keys the ctl owns in BOTH launch modes, so a
// value in tenant.env is either ignored or a hijack.
var surfaceRefusedAlways = map[string]string{
	"PYTHONPATH": "unit-owned: the worktree launch sets it (render/storeargv.go) and the server image's own " +
		"%environment sets it (render/apiimage.go); a tenant.env value would replace the code the API imports",
	"PATH": "unit-owned in both launch modes (render/storeargv.go, render/apiimage.go); a tenant.env value " +
		"would choose which binaries the API runs",
	"HF_HOME": "unit-owned in both launch modes: the unit sets the shared model cache " +
		"(render/storeargv.go, render/apiimage.go)",
	"PORT": "the API's port is an argv element the ctl derives from the tenant's port block " +
		"(render/apiimage.go), never a setting",
	"ROOT_PATH": "gateway-owned: the gateway generation decides the tenant's public prefix",
}

// surfaceCWLKeys are path-class on a WORKTREE row and refused on an IMAGE row,
// where render.APIImageUnitEnv overrides all three with the copies staged
// inside the image (/opt/ragstack/cwl/…): a tenant.env value there is a value
// nothing reads.
var surfaceCWLKeys = map[string]bool{
	"GOWE_WORKFLOW_CWL": true, "GRAPH_EXTRACT_CWL": true, "COLLECTION_RESTORE_CWL": true,
}

// surfacePathKeys are the path-valued settings (render.APIImagePathKeys).
var surfacePathKeys = map[string]bool{
	"INGEST_ROOT": true, "COLLECTION_MANIFEST_DIR": true,
	"USER_STORE_PATH": true, "JOB_STORE_PATH": true, "COLLECTION_STORE_PATH": true, "GRADING_STORE_PATH": true,
	"COLLECTIONS_FILE": true, "MODELS_REGISTRY_FILE": true, "DOI_ENRICHMENT_CACHE_DIR": true,
	"PROMPT_TEMPLATES_FILE": true,
}

var (
	httpSchemes  = []string{"http", "https"}
	neo4jSchemes = []string{"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
	redisSchemes = []string{"redis", "rediss"}
)

// surfaceURLKeys maps each single-URL key to the schemes it accepts.
var surfaceURLKeys = map[string][]string{
	"LLM_ENDPOINT":                httpSchemes,
	"GOWE_URL":                    httpSchemes,
	"WORKSPACE_URL":               httpSchemes,
	"QDRANT_URL":                  httpSchemes,
	"ELASTICSEARCH_URL":           httpSchemes,
	"NEO4J_URI":                   neo4jSchemes,
	"REDIS_URL":                   redisSchemes,
	"EMBEDDING_SIDECAR_URL":       httpSchemes,
	"CROSSENCODER_SIDECAR_URL":    httpSchemes,
	"OTEL_EXPORTER_OTLP_ENDPOINT": httpSchemes,
}

var surfaceURLListKeys = map[string]bool{"EMBEDDING_ENDPOINTS": true, "MODEL_URL_ALLOWLIST": true}

var surfaceRouteKeys = map[string]bool{"QDRANT_COLLECTION_ROUTES": true, "ES_COLLECTION_ROUTES": true}

// physicalStoreName is a routing table's KEY: the physical Qdrant collection
// or Elasticsearch index name (python/ragstack/store_routing.py), which is
// lower-case for an ES index by Elasticsearch's own rule and is lower-case for
// every Qdrant collection this deployment has ever made.
var physicalStoreName = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,254}$`)

// SurfaceClassOf answers how key's value is validated on a row of the given
// mode, and for a refused key, why. ok is false for a key that is not
// executable-surface at all.
func SurfaceClassOf(key string, imageRow bool) (class SurfaceClass, why string, ok bool) {
	key = strings.TrimSpace(key)
	if Classify(key) != ExecutableSurface {
		return SurfaceRefused, "", false
	}
	if why, refused := surfaceRefusedAlways[key]; refused {
		return SurfaceRefused, why, true
	}
	switch {
	case surfaceCWLKeys[key]:
		if imageRow {
			return SurfaceRefused, "an image-mode row's API reads the CWL staged inside the image: " +
				"render.APIImageUnitEnv sets it to /opt/ragstack/cwl/… and overrides any tenant.env value", true
		}
		return SurfaceCWL, "", true
	case surfacePathKeys[key]:
		return SurfacePath, "", true
	case key == "GOWE_IMAGE_DIRS":
		return SurfaceImageDirs, "", true
	case surfaceRouteKeys[key]:
		return SurfaceRoutes, "", true
	case surfaceURLListKeys[key]:
		return SurfaceURLList, "", true
	}
	if _, isURL := surfaceURLKeys[key]; isURL {
		return SurfaceURL, "", true
	}
	return SurfaceRefused, "it has no row in the validation table (settings/surface.go), so nothing says what " +
		"its values may be", true
}

// DeniedRoot is a directory no surface path may be under — or contain.
type DeniedRoot struct {
	Path string
	Why  string
}

// SurfaceContext is what the validation of one tenant's value needs to know.
type SurfaceContext struct {
	// ImageRow is true for a row that runs its API from a server image.
	ImageRow bool
	// DataDir is the tenant's own data dir; Worktree its worktree (CWL keys
	// on a worktree row only).
	DataDir  string
	Worktree string
	// BindRoots are CTL_API_BIND_ROOTS' paths (the mode suffix dropped).
	BindRoots []string
	// Denied are the ctl's own dirs and every OTHER tenant's data dir.
	Denied []DeniedRoot
	// AllowedHosts is CTL_ALLOWED_ENDPOINT_HOSTS, resolved (lower-case).
	AllowedHosts []string
}

// ValidateSurface checks one KEY=VALUE against the table and returns the value
// to write: the same string, except that a URL list is written back in its
// comma form. Every error names the key and says what to do.
func ValidateSurface(key, value string, c SurfaceContext) (string, error) {
	class, why, ok := SurfaceClassOf(key, c.ImageRow)
	if !ok {
		return "", fmt.Errorf("%s is not an executable-surface key", key)
	}
	if class == SurfaceRefused {
		return "", fmt.Errorf("%s is never settable through the ctl: %s", key, why)
	}
	if value == "" {
		return "", fmt.Errorf("%s: an empty value is not a setting; use `env unset-surface` to remove the key", key)
	}
	if strings.TrimSpace(value) != value || strings.ContainsAny(value, "\n\r\x00") {
		return "", fmt.Errorf("%s: the value has leading or trailing whitespace or a control character", key)
	}
	switch class {
	case SurfaceURL:
		return value, checkSurfaceURL(key, value, surfaceURLKeys[key], c.AllowedHosts)
	case SurfaceURLList:
		elems, err := splitURLList(key, value)
		if err != nil {
			return "", err
		}
		for _, e := range elems {
			if err := checkSurfaceURL(key, e, httpSchemes, c.AllowedHosts); err != nil {
				return "", err
			}
		}
		return strings.Join(elems, ","), nil
	case SurfacePath:
		return value, checkSurfacePath(key, value, c, false)
	case SurfaceCWL:
		return value, checkSurfacePath(key, value, c, true)
	case SurfaceImageDirs:
		var dirs []string
		for _, d := range strings.Split(value, ",") {
			if d = strings.TrimSpace(d); d != "" {
				dirs = append(dirs, d)
			}
		}
		if len(dirs) == 0 {
			return "", fmt.Errorf("%s: no directory in %q", key, value)
		}
		for _, d := range dirs {
			if err := checkImageDir(key, d, c); err != nil {
				return "", err
			}
		}
		return strings.Join(dirs, ","), nil
	case SurfaceRoutes:
		return value, checkRoutes(key, value, c.AllowedHosts)
	}
	return "", fmt.Errorf("%s: unhandled class %s", key, class)
}

// checkSurfaceURL is the URL rule: absolute, an accepted scheme, NO userinfo,
// no fragment, and a host (case-folded, compared exactly, without its port) in
// the endpoint allowlist.
func checkSurfaceURL(key, v string, schemes, allowed []string) error {
	u, err := url.Parse(v)
	if err != nil {
		return fmt.Errorf("%s: %q is not a URL: %v", key, v, err)
	}
	if u.Scheme == "" || u.Opaque != "" || u.Host == "" {
		return fmt.Errorf("%s: %q is not an absolute URL (scheme://host[:port][/path])", key, v)
	}
	scheme := strings.ToLower(u.Scheme)
	okScheme := false
	for _, s := range schemes {
		if scheme == s {
			okScheme = true
		}
	}
	if !okScheme {
		return fmt.Errorf("%s: scheme %q is not one of %s", key, u.Scheme, strings.Join(schemes, ", "))
	}
	if u.User != nil {
		return fmt.Errorf("%s: the URL carries userinfo (user[:password]@): credentials belong in the "+
			"*_API_KEY/DSN secret key in secrets.env, never in an executable-surface URL", key)
	}
	if u.Fragment != "" || strings.Contains(v, "#") {
		return fmt.Errorf("%s: %q has a fragment (#…), which no endpoint takes and the env grammar reads as "+
			"a comment", key, v)
	}
	host := strings.ToLower(u.Hostname())
	if host == "" {
		return fmt.Errorf("%s: %q names no host", key, v)
	}
	for _, a := range allowed {
		if host == a {
			return nil
		}
	}
	return fmt.Errorf("%s: host %q is not in CTL_ALLOWED_ENDPOINT_HOSTS [%s]; an operator adds a host to "+
		"ctl.env deliberately before a tenant may be pointed at it", key, host, strings.Join(allowed, ","))
}

// splitURLList is _split_list_env (python/ragstack/config.py): a JSON array,
// or a comma list with the blanks dropped.
func splitURLList(key, v string) ([]string, error) {
	var out []string
	if strings.HasPrefix(v, "[") {
		var arr []string
		if err := json.Unmarshal([]byte(v), &arr); err != nil {
			return nil, fmt.Errorf("%s: %q starts like a JSON array but is not an array of strings: %v", key, v, err)
		}
		for _, e := range arr {
			if e = strings.TrimSpace(e); e != "" {
				out = append(out, e)
			}
		}
	} else {
		for _, e := range strings.Split(v, ",") {
			if e = strings.TrimSpace(e); e != "" {
				out = append(out, e)
			}
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("%s: the list is empty; use `env unset-surface` to remove the key", key)
	}
	for _, e := range out {
		if strings.Contains(e, ",") {
			return nil, fmt.Errorf("%s: element %q contains a comma, which the comma form cannot carry", key, e)
		}
	}
	return out, nil
}

// pathUnder is p == root or p strictly inside root.
func pathUnder(p, root string) bool {
	if root == "" {
		return false
	}
	root = filepath.Clean(root)
	return p == root || strings.HasPrefix(p, strings.TrimSuffix(root, "/")+"/")
}

// strictlyUnder is p inside root and not root itself.
func strictlyUnder(p, root string) bool {
	return root != "" && p != filepath.Clean(root) && pathUnder(p, root)
}

// forbiddenBasenames are the credential files no setting may point at.
var forbiddenBasenames = map[string]bool{"secrets.env": true, "ctl-secrets.env": true}

// checkDenied refuses a path that is a credential file by name, or that is
// inside — or CONTAINS — a denied root: a directory setting that named a
// parent of another tenant's tree would hand the API that tree too.
func checkDenied(key, p string, c SurfaceContext) error {
	if forbiddenBasenames[filepath.Base(p)] {
		return fmt.Errorf("%s=%s names a credential file (%s); no setting may point at one", key, p, filepath.Base(p))
	}
	for _, d := range c.Denied {
		if d.Path == "" {
			continue
		}
		if pathUnder(p, d.Path) || pathUnder(filepath.Clean(d.Path), p) {
			return fmt.Errorf("%s=%s overlaps %s (%s); a tenant setting may not reach it", key, p, d.Path, d.Why)
		}
	}
	return nil
}

// checkSurfacePath is the path rule: absolute, clean and in the safe charset
// (paths.SafePath); strictly under the tenant's data dir or a bind root (or,
// for a worktree row's CWL key, its worktree or data dir); and clear of every
// denied root.
func checkSurfacePath(key, v string, c SurfaceContext, cwl bool) error {
	p, err := paths.SafePath("/", v)
	if err != nil {
		return fmt.Errorf("%s: %v (an absolute, clean path in [A-Za-z0-9._/-])", key, err)
	}
	if err := checkDenied(key, p, c); err != nil {
		return err
	}
	var roots []string
	if cwl {
		roots = []string{c.Worktree, c.DataDir}
	} else {
		roots = append([]string{c.DataDir}, c.BindRoots...)
	}
	for _, r := range roots {
		if strictlyUnder(p, r) {
			return nil
		}
	}
	var named []string
	for _, r := range roots {
		if r != "" {
			named = append(named, r)
		}
	}
	return fmt.Errorf("%s=%s is outside every approved root [%s]", key, p, strings.Join(named, " "))
}

// checkImageDir is GOWE_IMAGE_DIRS' rule: each entry absolute, clean, under a
// CTL_API_BIND_ROOTS entry and clear of every denied root.
func checkImageDir(key, d string, c SurfaceContext) error {
	p, err := paths.SafePath("/", d)
	if err != nil {
		return fmt.Errorf("%s entry: %v", key, err)
	}
	if err := checkDenied(key, p, c); err != nil {
		return err
	}
	for _, r := range c.BindRoots {
		if pathUnder(p, r) {
			return nil
		}
	}
	return fmt.Errorf("%s entry %s is outside every CTL_API_BIND_ROOTS entry [%s]: the API instance could not "+
		"see it", key, p, strings.Join(c.BindRoots, " "))
}

// checkRoutes is a routing table: a JSON object of physical store name -> URL.
func checkRoutes(key, v string, allowed []string) error {
	var routes map[string]string
	dec := json.NewDecoder(strings.NewReader(v))
	if err := dec.Decode(&routes); err != nil {
		return fmt.Errorf("%s: not a JSON object of name -> URL strings: %v", key, err)
	}
	if dec.More() {
		return fmt.Errorf("%s: trailing input after the JSON object", key)
	}
	if routes == nil {
		return fmt.Errorf("%s: not a JSON object", key)
	}
	names := make([]string, 0, len(routes))
	for k := range routes {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, name := range names {
		if !physicalStoreName.MatchString(name) {
			return fmt.Errorf("%s: key %q is not a physical store name (%s)", key, name, physicalStoreName)
		}
		if err := checkSurfaceURL(key+"["+name+"]", routes[name], httpSchemes, allowed); err != nil {
			return err
		}
	}
	return nil
}

// ---------------------------------------------------------------- the allowlist

// EnvAllowedEndpointHosts is the ctl.env key the allowlist is read from. It is
// declared here, in the leaf package, so the daemon (api/env.go registers it),
// the --direct CLI and the doctor read the same name.
const EnvAllowedEndpointHosts = "CTL_ALLOWED_ENDPOINT_HOSTS"

// hostnamePattern is one allowlist entry that is not an IP literal.
var hostnamePattern = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$`)

// DefaultAllowedHosts is the allowlist when CTL_ALLOWED_ENDPOINT_HOSTS is
// unset: loopback and this host. Anything else is an operator's decision.
func DefaultAllowedHosts(hostname string) []string {
	out := []string{"127.0.0.1", "::1", "localhost"}
	if h := strings.ToLower(strings.TrimSpace(hostname)); h != "" && hostnamePattern.MatchString(h) {
		out = append(out, h)
	}
	return out
}

// ParseAllowedHosts parses a CTL_ALLOWED_ENDPOINT_HOSTS value: a comma list of
// host names or IP literals, no scheme, no port, no wildcard. Blank is the
// default. A malformed entry is an error — a typo in an allowlist must be loud.
func ParseAllowedHosts(v, hostname string) ([]string, error) {
	if strings.TrimSpace(v) == "" {
		return DefaultAllowedHosts(hostname), nil
	}
	var out []string
	seen := map[string]bool{}
	for _, raw := range strings.Split(v, ",") {
		h := strings.ToLower(strings.TrimSpace(raw))
		if h == "" {
			return nil, fmt.Errorf("%s=%q has an empty entry", EnvAllowedEndpointHosts, v)
		}
		if net.ParseIP(strings.Trim(h, "[]")) != nil {
			h = strings.Trim(h, "[]")
		} else if !hostnamePattern.MatchString(h) {
			return nil, fmt.Errorf("%s entry %q is not a host name or an IP literal (no scheme, port, path or "+
				"wildcard)", EnvAllowedEndpointHosts, raw)
		}
		if !seen[h] {
			seen[h] = true
			out = append(out, h)
		}
	}
	return out, nil
}

// AllowedHostsFrom resolves the allowlist: the process environment's
// CTL_ALLOWED_ENDPOINT_HOSTS when it is set; otherwise the value ctlEnvValue
// read out of ctl.env (the --direct CLI run through ctl-as-svc.sh does not
// load ctl.env, and the allowlist must not change with the entry point);
// otherwise the default.
func AllowedHostsFrom(ctlEnvValue string) ([]string, error) {
	host, _ := os.Hostname()
	v := os.Getenv(EnvAllowedEndpointHosts)
	if strings.TrimSpace(v) == "" {
		v = ctlEnvValue
	}
	return ParseAllowedHosts(v, host)
}

// SurfaceURLHosts lists, for doctor, every (key, host) an existing value of a
// URL-class key names — single URLs, list elements and route values. Values
// that do not parse are skipped: that is not this check's finding.
func SurfaceURLHosts(key, value string) []string {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	hostOf := func(s string) string {
		u, err := url.Parse(strings.TrimSpace(s))
		if err != nil || u.Host == "" {
			return ""
		}
		return strings.ToLower(u.Hostname())
	}
	var out []string
	add := func(s string) {
		if h := hostOf(s); h != "" {
			out = append(out, h)
		}
	}
	switch {
	case surfaceURLListKeys[key]:
		elems, err := splitURLList(key, value)
		if err != nil {
			return nil
		}
		for _, e := range elems {
			add(e)
		}
	case surfaceRouteKeys[key]:
		var routes map[string]string
		if json.Unmarshal([]byte(value), &routes) != nil {
			return nil
		}
		for _, k := range sortedMapKeys(routes) {
			add(routes[k])
		}
	default:
		if _, ok := surfaceURLKeys[key]; ok {
			add(value)
		}
	}
	return out
}

// SurfaceURLKeys are every key SurfaceURLHosts reads, sorted.
func SurfaceURLKeys() []string {
	var out []string
	for k := range surfaceURLKeys {
		out = append(out, k)
	}
	for k := range surfaceURLListKeys {
		out = append(out, k)
	}
	for k := range surfaceRouteKeys {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedMapKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
