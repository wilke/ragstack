package render

import (
	"fmt"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The API as an apptainer instance of a SERVER IMAGE (PR-F, brief §1.2).
//
// A row with `server_image` runs its API as
//
//	apptainer instance run --no-home --cleanenv <binds> <sif> api-<manifest> --host <bind> --port <api>
//
// and this file is the one description of that command, the way StoreArgv is
// the one description of a store's. What it renders is PUBLIC: the binds, the
// image, the instance name, the runscript arguments and the unit environment
// (build stamps and container paths). The tenant's own environment — tenant.env
// and secrets.env — is NOT here and never on the argv: the supervisor reads it
// at RUN time and hands it to apptainer as APPTAINERENV_<KEY> in the child's
// environment only (`--cleanenv` keeps those and drops everything else).

// APIImageInstancePrefix is the instance kind of an image-mode API.
const APIImageInstancePrefix = "api-"

// APIImageInstanceName is the instance an image-mode API runs under:
// `api-<manifest_name>`, the sibling of `qdrant-<manifest_name>`.
func APIImageInstanceName(t *registry.Tenant) string {
	m := t.ManifestName
	if m == "" {
		m = t.Name
	}
	return APIImageInstancePrefix + m
}

// The CWL paths inside a server image. The image stages `cwl/` at
// /opt/ragstack/cwl (ragstack-server.def), so the unit environment points the
// three CWL settings there and a tenant.env value naming the worktree's copy is
// shadowed (doctor: setting_shadowed_by_image).
const (
	APIImageWorkflowCWL = "/opt/ragstack/cwl/pdf-ingest-scatter.cwl"
	APIImageGraphCWL    = "/opt/ragstack/cwl/graph-extract.cwl"
	APIImageRestoreCWL  = "/opt/ragstack/cwl/restore-collection.cwl"
)

// APIImageUnitEnv is the image-mode API's unit environment: the third and
// WINNING layer over tenant.env ∪ secrets.env. There is no PYTHONPATH — the
// image's own %environment points it at the staged tree, and a host value would
// hijack it.
func APIImageUnitEnv(t *registry.Tenant, cfg UnitConfig, apiLog string) []string {
	cfg = cfg.withDefaults()
	si := t.ServerImage
	return []string{
		"HF_HOME=" + cfg.HFHome,
		"PYTHONUNBUFFERED=1",
		"RAGSTACK_GIT_TAG=" + si.Version,
		"RAGSTACK_GIT_SHA=" + si.Commit,
		"RAGSTACK_API_LOG=" + apiLog,
		"GOWE_WORKFLOW_CWL=" + APIImageWorkflowCWL,
		"GRAPH_EXTRACT_CWL=" + APIImageGraphCWL,
		"COLLECTION_RESTORE_CWL=" + APIImageRestoreCWL,
	}
}

// APIImageForbiddenEnvKey reports whether an env-file key would hijack the
// container if it were forwarded as APPTAINERENV_<KEY>: the interpreter's
// search paths, the loader's, and apptainer's own knobs. An image row whose
// tenant.env or secrets.env defines one is refused, at plan time from what the
// plan can see and again at run time from the files themselves.
func APIImageForbiddenEnvKey(k string) bool {
	switch k {
	case "PYTHONPATH", "PATH", "PREPEND_PATH", "APPEND_PATH":
		return true
	}
	return strings.HasPrefix(k, "LD_") || strings.HasPrefix(k, "APPTAINER")
}

// APIImageDroppedEnvKey is a secrets.env key that is NOT forwarded to an API
// instance and is not a refusal either: APPTAINERENV_POSTGRES_PASSWORD is the
// postgres INSTANCE's credential, written beside TENANT_PG_PASSWORD by `create`
// for every postgres-local tenant (ops/create.go). The API reads its DSNs, not
// this; forwarding it would only plant `APPTAINERENV_POSTGRES_PASSWORD` inside
// the API container, and refusing it would refuse every postgres-local tenant.
func APIImageDroppedEnvKey(k string) bool { return k == APPTAINERENVPostgresPassword }

// APIImagePathKeys are the API's path-valued settings (python/ragstack/
// config.py) the bind derivation and the step-time path probe look at. The
// three CWL keys are not here: the unit environment overrides them with paths
// inside the image. HF_HOME is not here: it is always bound read-write.
var APIImagePathKeys = []string{
	"COLLECTIONS_FILE",
	"COLLECTION_STORE_PATH",
	"MODELS_REGISTRY_FILE",
	"COLLECTION_MANIFEST_DIR",
	"DOI_ENRICHMENT_CACHE_DIR",
	"INGEST_ROOT",
	"JOB_STORE_PATH",
	"GRADING_STORE_PATH",
	"USER_STORE_PATH",
	"PROMPT_TEMPLATES_FILE",
}

// APIImageSQLiteStores pairs each sqlite store's path key with its backend key
// and the RELATIVE default config.py gives the path. A relative default is
// resolved against the process's cwd, which inside the image is /opt/ragstack
// — the read-only image root — so a sqlite store with no absolute path is a
// store that cannot be written.
var APIImageSQLiteStores = []struct{ Backend, Path, Default string }{
	{"COLLECTION_STORE_BACKEND", "COLLECTION_STORE_PATH", "ragstack_collections.db"},
	{"JOB_STORE_BACKEND", "JOB_STORE_PATH", "ragstack_jobs.db"},
	{"GRADING_STORE_BACKEND", "GRADING_STORE_PATH", "ragstack_grading.db"},
	{"USER_STORE_BACKEND", "USER_STORE_PATH", "ragstack_users.db"},
}

// APIImage is how ONE tenant's API is launched from its server image.
type APIImage struct {
	// Instance is `api-<manifest_name>`.
	Instance string
	// SIF is server_image.path.
	SIF string
	// Binds are identity binds (`<p>:<p>:rw|ro`), deduplicated and sorted.
	Binds []string
	// Args are the runscript's (uvicorn's) arguments after the instance name.
	Args []string
	// UnitEnv is APIImageUnitEnv.
	UnitEnv []string
}

// Argv is the whole command as the plan shows it, from the apptainer binary
// on. The real driver renders the same thing (instances.go Run).
func (a APIImage) Argv(apptainerBin string) []string {
	out := []string{apptainerBin, "instance", "run", "--no-home", "--cleanenv"}
	for _, b := range a.Binds {
		out = append(out, "--bind", b)
	}
	out = append(out, a.SIF, a.Instance)
	return append(out, a.Args...)
}

var (
	reImageSHA256 = regexp.MustCompile(`^[0-9a-f]{64}$`)
	reImageCommit = regexp.MustCompile(`^[0-9a-f]{40}$`)
)

// APIImageLaunch renders an image-mode row's API instance. env is what the
// planner can see of the tenant's PUBLIC environment (the registry's
// settings{} under tenant.env, or the env file a create is about to write): it
// decides the binds. It is never rendered into anything — only its path-valued
// keys are read.
//
// Binds (identity: host path == container path):
//
//   - <data_dir> rw — the tenant's whole tree: state, logs, manifests, ingest;
//   - HF_HOME (/rag/cache) rw — the shared model cache;
//   - each GOWE_IMAGE_DIRS entry ro — the tool-image store the boot check reads;
//   - every other path-valued setting (APIImagePathKeys) that is absolute and
//     NOT already under one of the above: the setting's OWN path, ro. Never the
//     governing root — /rag/config holds /rag/config/ctl (the ctl's secrets and
//     the backup identity), and a bind of the root would hand them to the API.
//
// A relative or empty value binds nothing; the step-time probe refuses a
// relative path the API would resolve against the read-only image root.
func APIImageLaunch(t *registry.Tenant, cfg UnitConfig, env map[string]string, apiLog string) (APIImage, error) {
	if t == nil || t.ServerImage == nil {
		return APIImage{}, fmt.Errorf("the row has no server_image, so it has no image launch")
	}
	p, err := prepare(t, cfg)
	if err != nil {
		return APIImage{}, err
	}
	cfg = p.cfg
	si := t.ServerImage
	if !filepath.IsAbs(si.Path) || filepath.Clean(si.Path) != si.Path {
		return APIImage{}, fmt.Errorf("tenant %s: server_image.path %q is not an absolute, clean path", t.Name, si.Path)
	}
	if _, err := paths.SafePath("/", si.Path); err != nil {
		return APIImage{}, fmt.Errorf("tenant %s: server_image.path: %w", t.Name, err)
	}
	if !reImageCommit.MatchString(si.Commit) {
		return APIImage{}, fmt.Errorf("tenant %s: server_image.commit %q is not a full 40-hex sha", t.Name, si.Commit)
	}
	if !reImageSHA256.MatchString(si.SHA256) {
		return APIImage{}, fmt.Errorf("tenant %s: server_image.sha256 %q is not 64 hex characters", t.Name, si.SHA256)
	}
	if si.Version == "" || strings.ContainsAny(si.Version, " \t\n\"'\\$;=") {
		return APIImage{}, fmt.Errorf("tenant %s: server_image.version %q is not a value the unit environment can carry",
			t.Name, si.Version)
	}
	if apiLog == "" || !filepath.IsAbs(apiLog) {
		return APIImage{}, fmt.Errorf("tenant %s: the API log path %q is not absolute", t.Name, apiLog)
	}
	bind, err := apiBind(t, cfg)
	if err != nil {
		return APIImage{}, err
	}
	return APIImage{
		Instance: APIImageInstanceName(t),
		SIF:      si.Path,
		Binds:    APIImageBinds(t.DataDir, cfg.HFHome, env),
		Args:     []string{"--host", bind, "--port", strconv.Itoa(t.Ports.API)},
		UnitEnv:  APIImageUnitEnv(t, cfg, apiLog),
	}, nil
}

// APIImageBinds is the bind derivation APIImageLaunch documents, exported so
// the step-time probe and the tests read the same rule.
func APIImageBinds(dataDir, hfHome string, env map[string]string) []string {
	type bind struct {
		path string
		rw   bool
	}
	var chosen []bind
	covered := func(p string) bool {
		for _, b := range chosen {
			if pathUnder(p, b.path) {
				return true
			}
		}
		return false
	}
	add := func(p string, rw bool) {
		p = filepath.Clean(p)
		if !filepath.IsAbs(p) || covered(p) {
			return
		}
		chosen = append(chosen, bind{p, rw})
	}
	// The read-write roots first, so a read-only entry under one of them is
	// covered by it rather than bound a second time read-only.
	add(dataDir, true)
	add(hfHome, true)
	for _, dir := range SplitImageDirs(env["GOWE_IMAGE_DIRS"]) {
		add(dir, false)
	}
	// The rest: sorted by path, so a parent directory is chosen before a file
	// inside it and the result does not depend on map order.
	var rest []string
	for _, k := range APIImagePathKeys {
		if v := strings.TrimSpace(env[k]); v != "" && filepath.IsAbs(v) {
			rest = append(rest, filepath.Clean(v))
		}
	}
	sort.Strings(rest)
	for _, p := range rest {
		add(p, false)
	}
	out := make([]string, 0, len(chosen))
	for _, b := range chosen {
		mode := "ro"
		if b.rw {
			mode = "rw"
		}
		out = append(out, b.path+":"+b.path+":"+mode)
	}
	sort.Strings(out)
	return out
}

// SplitImageDirs is GOWE_IMAGE_DIRS as python/ragstack/tool_image.py reads
// it: comma-separated, blanks dropped, order kept.
func SplitImageDirs(v string) []string {
	var out []string
	for _, d := range strings.Split(v, ",") {
		if d = strings.TrimSpace(d); d != "" {
			out = append(out, d)
		}
	}
	return out
}

// APIImageBindFor answers "is path inside one of these binds, and may the
// container write it": the bind that covers it, and whether that bind is rw.
func APIImageBindFor(binds []string, path string) (bind string, rw bool, ok bool) {
	path = filepath.Clean(path)
	best := ""
	for _, b := range binds {
		parts := strings.Split(b, ":")
		if len(parts) < 2 {
			continue
		}
		if pathUnder(path, parts[1]) && len(parts[1]) > len(best) {
			best, bind = parts[1], b
			rw = len(parts) < 3 || parts[2] != "ro"
		}
	}
	return bind, rw, best != ""
}

// pathUnder reports whether p is root or inside it.
func pathUnder(p, root string) bool {
	p, root = filepath.Clean(p), filepath.Clean(root)
	if p == root {
		return true
	}
	if root == "/" {
		return true
	}
	return strings.HasPrefix(p, root+"/")
}
