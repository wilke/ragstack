// Package paths is the single source of truth for tenant naming, the /rag
// host layout and the port-block arithmetic. Every path the ctl reads or
// writes is spelled exactly once here; apptainer/new-tenant.sh must agree
// (render/parity_test.go checks the reserved list and the port math).
package paths

import (
	"errors"
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
)

// Port layout. A tenant's block is PortBase + PortStride*index; offsets within
// the block are +0 API, +1 qdrant http, +2 qdrant grpc, +3 ES http, +4 ES
// transport, +5 postgres (reserved). The selftest range is disjoint from the
// first 100 blocks (see TestSelftestRangeNeverOverlaps).
const (
	PortBase     = 24000
	PortStride   = 20
	CtlPort      = 23990
	SelftestBase = 26000
	SelftestEnd  = 26099
)

// namePattern is the tenant-name grammar shared with new-tenant.sh: the name
// feeds instance names, directory paths, env-file tokens, gateway segments and
// (with --postgres) SQL identifiers. No ':' (subject strings are issuer:sub),
// no uppercase, no leading '-' or digit.
var namePattern = regexp.MustCompile(`^[a-z][a-z0-9-]{0,31}$`)

// Reserved names collide with a shared instance, a gateway route or a
// built-in. Keep in sync with the `case "$NAME"` list in
// apptainer/new-tenant.sh (asserted by the parity test).
var Reserved = []string{
	"qdrant", "elasticsearch", "neo4j", "postgres", "redis", "embedding",
	"crossencoder", "faiss", "tenants", "manifest", "default", "public",
	"admin", "services", "health", "ragstack", "api", "ui", "gowe", "vaxpipe",
	"grafana", "sfr", "ctl",
}

// ValidateName reports why name is not an acceptable tenant name, or nil.
func ValidateName(name string) error {
	if name == "" {
		return errors.New("tenant name is empty")
	}
	if !namePattern.MatchString(name) {
		return fmt.Errorf("invalid tenant name %q — must match ^[a-z][a-z0-9-]{0,31}$", name)
	}
	for _, r := range Reserved {
		if name == r {
			return fmt.Errorf("tenant name %q is reserved (collides with a shared instance, a gateway route or a built-in)", name)
		}
	}
	return nil
}

// Roots are the fixed directories of one /rag deployment. Every field is an
// absolute path. Build one with NewRoots; the zero value is not usable.
type Roots struct {
	RagRoot      string // /rag
	DataDir      string // /rag/data/tenants — tenant data trees + registry + manifest
	ReposDir     string // /rag/repos/tenants — per-tenant worktrees
	BackupsDir   string // /rag/backups/tenants
	CtlConfigDir string // /rag/config/ctl — ctl.env, units/, templates/
	CtlStateDir  string // /rag/data/ctl — jobs.db, gateway generations, artifacts
	ImagesDir    string // /rag/apptainer/images — shared SIFs
	ProxyDir     string // /rag/config/proxy — nginx tree (owned by coconut-proxy)
}

// Overrides replaces individual Roots fields; empty fields keep the default
// derived from RagRoot.
type Overrides struct {
	DataDir, ReposDir, BackupsDir, CtlConfigDir, CtlStateDir, ImagesDir, ProxyDir string
}

// NewRoots derives the standard layout under ragRoot and applies ov.
func NewRoots(ragRoot string, ov Overrides) Roots {
	ragRoot = filepath.Clean(ragRoot)
	r := Roots{
		RagRoot:      ragRoot,
		DataDir:      filepath.Join(ragRoot, "data", "tenants"),
		ReposDir:     filepath.Join(ragRoot, "repos", "tenants"),
		BackupsDir:   filepath.Join(ragRoot, "backups", "tenants"),
		CtlConfigDir: filepath.Join(ragRoot, "config", "ctl"),
		CtlStateDir:  filepath.Join(ragRoot, "data", "ctl"),
		ImagesDir:    filepath.Join(ragRoot, "apptainer", "images"),
		ProxyDir:     filepath.Join(ragRoot, "config", "proxy"),
	}
	pick := func(dst *string, v string) {
		if v != "" {
			*dst = filepath.Clean(v)
		}
	}
	pick(&r.DataDir, ov.DataDir)
	pick(&r.ReposDir, ov.ReposDir)
	pick(&r.BackupsDir, ov.BackupsDir)
	pick(&r.CtlConfigDir, ov.CtlConfigDir)
	pick(&r.CtlStateDir, ov.CtlStateDir)
	pick(&r.ImagesDir, ov.ImagesDir)
	pick(&r.ProxyDir, ov.ProxyDir)
	return r
}

// Registry is the fleet registry file (source of truth).
func (r Roots) Registry() string { return filepath.Join(r.DataDir, "registry.json") }

// Manifest is the manifest.tsv projection new-tenant.sh and the ops scripts read.
func (r Roots) Manifest() string { return filepath.Join(r.DataDir, "manifest.tsv") }

// UnitsDir holds the canonical per-tenant systemd unit files (SYSTEMD_UNIT_PATH).
func (r Roots) UnitsDir() string { return filepath.Join(r.CtlConfigDir, "units") }

// CtlUIDist is the built admin UI served by the gateway.
func (r Roots) CtlUIDist() string { return filepath.Join(r.CtlStateDir, "ui", "dist") }

// Tenant is every path of one tenant, spelled once. name is the registry key
// (gateway segment, worktree, pidfile/log suffix); manifestName is the
// manifest row and data-dir basename (equal to name for tenants the ctl
// creates; the adopted lucid-next/asm-next pairs differ).
type Tenant struct {
	Name         string
	ManifestName string

	DataDir      string // <DataDir>/<manifestName>
	ConfigDir    string // …/config
	TenantEnv    string // …/config/tenant.env
	SecretsEnv   string // …/config/secrets.env
	ProvisionEnv string // …/config/provision.env
	StateDir     string // …/state (sqlite ACL/registry/jobs)
	ManifestsDir string // …/manifests
	IngestDir    string // …/ingest
	LogsDir      string // …/logs
	BinDir       string // …/bin (new-tenant.sh up.sh/down.sh; never edited by the ctl)

	QdrantStorage   string // …/qdrant/storage
	QdrantSnapshots string // …/qdrant/snapshots
	ESData          string // …/elasticsearch/data
	ESLogs          string // …/elasticsearch/logs
	ESConfig        string // …/elasticsearch/config
	ESSnapshots     string // …/elasticsearch/snapshots (ES path.repo)
	UIDist          string // …/ui/dist (static vite build)

	Worktree string // <ReposDir>/<name>
	PidFile  string // <DataDir>/api-<name>.pid (ops/coconut/restore.sh convention)
	APILog   string // <DataDir>/logs/api-<name>.log
}

// TenantPaths lays out a tenant under root. manifestName defaults to name.
func TenantPaths(root Roots, name, manifestName string) Tenant {
	if manifestName == "" {
		manifestName = name
	}
	d := filepath.Join(root.DataDir, manifestName)
	return Tenant{
		Name:            name,
		ManifestName:    manifestName,
		DataDir:         d,
		ConfigDir:       filepath.Join(d, "config"),
		TenantEnv:       filepath.Join(d, "config", "tenant.env"),
		SecretsEnv:      filepath.Join(d, "config", "secrets.env"),
		ProvisionEnv:    filepath.Join(d, "config", "provision.env"),
		StateDir:        filepath.Join(d, "state"),
		ManifestsDir:    filepath.Join(d, "manifests"),
		IngestDir:       filepath.Join(d, "ingest"),
		LogsDir:         filepath.Join(d, "logs"),
		BinDir:          filepath.Join(d, "bin"),
		QdrantStorage:   filepath.Join(d, "qdrant", "storage"),
		QdrantSnapshots: filepath.Join(d, "qdrant", "snapshots"),
		ESData:          filepath.Join(d, "elasticsearch", "data"),
		ESLogs:          filepath.Join(d, "elasticsearch", "logs"),
		ESConfig:        filepath.Join(d, "elasticsearch", "config"),
		ESSnapshots:     filepath.Join(d, "elasticsearch", "snapshots"),
		UIDist:          filepath.Join(d, "ui", "dist"),
		Worktree:        filepath.Join(root.ReposDir, name),
		PidFile:         filepath.Join(d, "api-"+name+".pid"),
		APILog:          filepath.Join(d, "logs", "api-"+name+".log"),
	}
}

// ProvisionDirs are the directories new-tenant.sh creates (its TENANT_DIRS
// array, same order) — every writable path an instance touches.
func (t Tenant) ProvisionDirs() []string {
	return []string{
		t.QdrantStorage, t.QdrantSnapshots,
		t.ESData, t.ESLogs, t.ESConfig,
		t.StateDir, t.ManifestsDir, t.IngestDir, t.ConfigDir, t.BinDir,
	}
}

// Ports is one tenant's port block.
type Ports struct {
	Index       int `json:"index"`
	Base        int `json:"base"`
	API         int `json:"api"`
	QdrantHTTP  int `json:"qdrant_http"`
	QdrantGRPC  int `json:"qdrant_grpc"`
	ESHTTP      int `json:"es_http"`
	ESTransport int `json:"es_transport"`
	PG          int `json:"pg"`
}

// Block computes the port block for index with the default base and stride.
func Block(index int) Ports { return BlockAt(PortBase, PortStride, index) }

// BlockAt computes the port block for index under an explicit base/stride
// (new-tenant.sh honours TENANT_PORT_BASE/TENANT_PORT_STRIDE the same way).
func BlockAt(base, stride, index int) Ports {
	b := base + index*stride
	return Ports{
		Index: index, Base: b,
		API: b, QdrantHTTP: b + 1, QdrantGRPC: b + 2,
		ESHTTP: b + 3, ESTransport: b + 4, PG: b + 5,
	}
}

// safePathChars is the charset a rendered path may contain: it ends up in
// unit files, nginx config and shell wrappers, so nothing that any of those
// parsers could interpret is allowed.
var safePathChars = regexp.MustCompile(`^[A-Za-z0-9._/-]+$`)

// SafePath returns p cleaned, or an error unless p is absolute,
// filepath.Clean-stable, strictly under root and within the safe charset.
func SafePath(root, p string) (string, error) {
	if p == "" {
		return "", errors.New("path is empty")
	}
	if !filepath.IsAbs(p) {
		return "", fmt.Errorf("path %q is not absolute", p)
	}
	if !safePathChars.MatchString(p) {
		return "", fmt.Errorf("path %q contains characters outside [A-Za-z0-9._/-]", p)
	}
	if filepath.Clean(p) != p {
		return "", fmt.Errorf("path %q is not clean (want %q)", p, filepath.Clean(p))
	}
	root = filepath.Clean(root)
	if !filepath.IsAbs(root) {
		return "", fmt.Errorf("root %q is not absolute", root)
	}
	prefix := root + string(filepath.Separator)
	if root == string(filepath.Separator) {
		prefix = root // "/" is its own prefix; everything absolute is under it
	}
	if p == root || !strings.HasPrefix(p, prefix) {
		return "", fmt.Errorf("path %q is not under %q", p, root)
	}
	return p, nil
}
