package render

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// UnitConfig is the host side of the unit templates (plan: Supervision).
type UnitConfig struct {
	CtlBin       string // /rag/bin/ragstack-ctl (ExecStartPre wait-ready)
	ApptainerBin string // absolute apptainer path
	CtlStateDir  string // /rag/data/ctl → apptainer/{cache,config}
	HFHome       string // /rag/cache
	RagRoot      string // /rag (path root every rendered path hangs off)
	ReadyTimeout int    // seconds for wait-ready; default 180
	// MountPoint is what every unit's ConditionPathIsMountPoint names.
	// Defaults to RagRoot, which is right for the deployment.
	//
	// It is separate from RagRoot because the selftest runs against a SANDBOX
	// root UNDER /rag (a scratch data dir, a scratch state dir), and
	// `ConditionPathIsMountPoint` on such a directory is false for the same
	// reason it is true for /rag: it is a path inside the mount, not the mount.
	// A unit conditioned on it would silently never start, which is the least
	// debuggable failure systemd has. So the sandbox keeps conditioning on the
	// real mount (/rag) while every path in the unit points at its own tree.
	MountPoint string
	// AllowNonLoopbackBind lets Units render an api unit whose --host is not
	// a loopback address. It is OFF by default and must stay a deliberate,
	// per-call decision: coconut is internet-reachable, and `--host 0.0.0.0`
	// in a unit publishes a tenant API — which authenticates with an API key
	// carried in a header — to the whole internet. See the bind check below.
	AllowNonLoopbackBind bool
	// AllowOffLayoutDataDir lets Units render a tenant whose data dir is not
	// named by its manifest_name (`<…>/tenants/<manifest_name>`). Every other
	// path in the unit — the store binds, the log files — is derived from the
	// data dir, and the instance and log names are derived from manifest_name;
	// when the two disagree the unit half-describes one tenant and half
	// another. adopt records the disagreement as data_dir_off_layout, so the
	// renderer refuses by default and an operator who really has such a tenant
	// names it here.
	AllowOffLayoutDataDir bool
}

func (c UnitConfig) withDefaults() UnitConfig {
	if c.CtlBin == "" {
		c.CtlBin = "/rag/bin/ragstack-ctl"
	}
	if c.ApptainerBin == "" {
		c.ApptainerBin = "/usr/bin/apptainer"
	}
	if c.RagRoot == "" {
		c.RagRoot = "/rag"
	}
	if c.CtlStateDir == "" {
		c.CtlStateDir = filepath.Join(c.RagRoot, "data", "ctl")
	}
	if c.HFHome == "" {
		c.HFHome = filepath.Join(c.RagRoot, "cache")
	}
	if c.ReadyTimeout <= 0 {
		c.ReadyTimeout = 180
	}
	if c.MountPoint == "" {
		c.MountPoint = c.RagRoot
	}
	return c
}

// loopbackBinds are the addresses a rendered api unit may bind to.
var loopbackBinds = map[string]bool{"127.0.0.1": true, "localhost": true, "::1": true}

// apiBind is the --host the api unit gets.
//
// An UNRECORDED bind ("" — adopt could not observe the API process, because
// it is not running or its /proc is another account's) is the loopback
// default, never a guess: guessing 0.0.0.0 from an absent observation is how
// a unit came to publish a tenant API to the internet on the strength of no
// evidence at all.
//
// A recorded non-loopback bind is REFUSED unless the caller opted in. The
// live tenants really do bind 0.0.0.0 today, so adopt records that (it is a
// fact about the host and the registry fixture keeps it); rendering a unit
// that re-creates it is a different act, and one an operator has to ask for.
func apiBind(t *registry.Tenant, cfg UnitConfig) (string, error) {
	bind := t.API.Bind
	if bind == "" {
		return "127.0.0.1", nil
	}
	if !loopbackBinds[bind] && !cfg.AllowNonLoopbackBind {
		return "", fmt.Errorf("tenant %s: api.bind is %q, which is not loopback — this host is internet-reachable and the tenant API authenticates on a header; set UnitConfig.AllowNonLoopbackBind to render it anyway", t.Name, bind)
	}
	return bind, nil
}

// UnitNames returns the unit file names for tenant name, in the order the
// target wants them (stores, api, ui). The postgres service exists only for a
// tenant whose relational store kind is `local`; the NAME is returned for every
// tenant so a caller can ask systemd about a unit that should not be there.
func UnitNames(name string) (target, qdrant, es, postgres, api, ui string) {
	p := "ragstack-" + name
	return p + ".target", p + "-qdrant.service", p + "-es.service", p + "-postgres.service",
		p + "-api.service", p + "-ui.service"
}

// Units renders the systemd --user unit files for t: the target, one
// service per exclusively-owned store, the api service and (dev UI mode
// only) the ui service. Keys are file names. Shared/external stores get no
// unit — the ctl never supervises what it does not own.
func Units(t *registry.Tenant, cfg UnitConfig) (map[string][]byte, error) {
	if err := checkTenant(t, checkOptions{AllowOffLayoutDataDir: cfg.AllowOffLayoutDataDir}); err != nil {
		return nil, err
	}
	cfg = cfg.withDefaults()
	for _, p := range []string{t.DataDir, t.Worktree, t.PythonEnv, cfg.CtlBin, cfg.ApptainerBin, cfg.CtlStateDir, cfg.HFHome} {
		if p == "" {
			return nil, fmt.Errorf("tenant %s: data_dir, worktree, python_env and every UnitConfig path are required", t.Name)
		}
		if _, err := paths.SafePath("/", p); err != nil {
			return nil, err
		}
	}
	if t.DesiredBoot != "enabled" && t.DesiredBoot != "disabled" {
		return nil, fmt.Errorf("tenant %s: desired_boot must be enabled|disabled, got %q", t.Name, t.DesiredBoot)
	}
	for _, s := range []string{t.Code.Tag, string(t.Code.SHA), t.API.Bind} {
		if strings.ContainsAny(s, " \t\n\"'\\$;") {
			return nil, fmt.Errorf("tenant %s: unsafe unit value %q", t.Name, s)
		}
	}
	// Every path in the units is derived from t.DataDir ITSELF — its parent as
	// the tenants root and its basename as the directory name — not from
	// <parent>/<manifest_name>. TenantPaths joins the root with the manifest
	// name, so feeding it t.ManifestName rebuilt a path that only equals
	// t.DataDir when the two already agree: for the adopted pairs whose row
	// name and directory differ, the ES unit bound a data dir the tenant does
	// not use. checkTenant above refuses the disagreement outright unless the
	// caller opted in, and this derivation follows the data dir either way.
	tp := paths.TenantPaths(
		paths.NewRoots(cfg.RagRoot, paths.Overrides{DataDir: filepath.Dir(t.DataDir)}),
		t.Name, filepath.Base(t.DataDir))
	target, qdrantU, esU, pgU, apiU, uiU := UnitNames(t.Name)
	out := map[string][]byte{}
	var stores []string

	if q := &t.Stores.Qdrant; q.Ownership == registry.OwnershipExclusive {
		sif := string(q.SIF)
		if sif == "" {
			return nil, fmt.Errorf("tenant %s: exclusive qdrant store has no sif", t.Name)
		}
		if _, err := paths.SafePath("/", sif); err != nil {
			return nil, err
		}
		stores = append(stores, qdrantU)
		// LimitNOFILE on every service: the user manager starts a service with
		// the SOFT limit of 1024 descriptors (the hard limit is a million), while
		// a login shell — where the hand-started instances came from — has both
		// at a million. Qdrant starts one actix worker per CPU (383 on coconut)
		// and died at boot with "Too many open files" the first time a sandbox
		// tenant was started from a unit; Elasticsearch's bootstrap check wants
		// 65535 or more. Soft and hard are set together, to the hard limit.
		out[qdrantU] = []byte(fmt.Sprintf(`[Unit]
Description=ragstack tenant %[1]s — qdrant (:%[2]d)
PartOf=%[3]s
ConditionPathIsMountPoint=%[4]s
ConditionPathIsDirectory=%[5]s
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
UMask=0002
Environment=APPTAINER_CACHEDIR=%[6]s/apptainer/cache
Environment=APPTAINER_CONFIGDIR=%[6]s/apptainer/config
ExecStart=%[7]s run --no-home --bind %[5]s:/qdrant/storage --bind %[8]s:/qdrant/snapshots --env QDRANT__SERVICE__HTTP_PORT=%[2]d --env QDRANT__SERVICE__GRPC_PORT=%[9]d %[10]s /bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'
LimitNOFILE=1048576
KillMode=mixed
TimeoutStopSec=120
Restart=on-failure
RestartSec=5
StandardOutput=append:%[11]s/qdrant-%[12]s.log
StandardError=append:%[11]s/qdrant-%[12]s.log
`, t.Name, t.Ports.QdrantHTTP, target, cfg.MountPoint, tp.QdrantStorage, cfg.CtlStateDir, cfg.ApptainerBin,
			tp.QdrantSnapshots, t.Ports.QdrantGRPC, sif, tp.LogsDir, t.ManifestName))
	}

	if e := &t.Stores.Elasticsearch; e.Ownership == registry.OwnershipExclusive {
		sif := string(e.SIF)
		if sif == "" {
			return nil, fmt.Errorf("tenant %s: exclusive elasticsearch store has no sif", t.Name)
		}
		if _, err := paths.SafePath("/", sif); err != nil {
			return nil, err
		}
		heap := string(e.Heap)
		if heap == "" {
			heap = string(e.ProvisionHeap)
		}
		if heap == "" {
			heap = "512m"
		}
		if !heapPattern.MatchString(heap) {
			return nil, fmt.Errorf("tenant %s: invalid ES heap %q", t.Name, heap)
		}
		repo := string(e.PathRepo)
		if repo == "" {
			repo = "/usr/share/elasticsearch/snapshots"
		}
		if _, err := paths.SafePath("/", repo); err != nil {
			return nil, err
		}
		stores = append(stores, esU)
		// ExecStartPre=es-seed-config is the unit's port of bin/up.sh's
		// seed_if_empty plus its vm.max_map_count preflight, and both halves
		// are load-bearing. The config bind shadows the image's
		// /usr/share/elasticsearch/config, so an EMPTY host directory hands
		// Elasticsearch no jvm.options, no log4j2.properties and no
		// elasticsearch.yml and it exits before it logs anything useful; the
		// script seeded it from the image and the unit did not, so a tenant
		// that had only ever been started by systemd never booted. The
		// max_map_count check is a warning on the same command because
		// 65530 (this host's default) is an ES that dies at startup with a
		// bootstrap check an operator has to go read the log to see.
		out[esU] = []byte(fmt.Sprintf(`[Unit]
Description=ragstack tenant %[1]s — elasticsearch (:%[2]d)
PartOf=%[3]s
ConditionPathIsMountPoint=%[4]s
ConditionPathIsDirectory=%[5]s
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
UMask=0002
Environment=APPTAINER_CACHEDIR=%[6]s/apptainer/cache
Environment=APPTAINER_CONFIGDIR=%[6]s/apptainer/config
ExecStartPre=%[17]s es-seed-config %[1]s --rag-root %[18]s
ExecStart=%[7]s run --no-home --bind %[5]s:/usr/share/elasticsearch/data --bind %[8]s:/usr/share/elasticsearch/logs --bind %[9]s:/usr/share/elasticsearch/config --bind %[10]s:%[11]s --env "ES_JAVA_OPTS=-Xms%[12]s -Xmx%[12]s" %[13]s /usr/local/bin/docker-entrypoint.sh eswrapper -Ediscovery.type=single-node -Expack.security.enabled=false -Ehttp.port=%[2]d -Etransport.port=%[14]d -Epath.repo=%[11]s
LimitNOFILE=1048576
KillMode=mixed
TimeoutStopSec=120
Restart=on-failure
RestartSec=5
StandardOutput=append:%[15]s/es-%[16]s.log
StandardError=append:%[15]s/es-%[16]s.log
`, t.Name, t.Ports.ESHTTP, target, cfg.MountPoint, tp.ESData, cfg.CtlStateDir, cfg.ApptainerBin,
			tp.ESLogs, tp.ESConfig, tp.ESSnapshots, repo, heap, sif, t.Ports.ESTransport, tp.LogsDir, t.ManifestName,
			cfg.CtlBin, cfg.RagRoot))
	}

	// The tenant's OWN postgres, when it has one. `local` is the only kind
	// that runs a server the ctl owns: `sqlite` runs none and `external` is a
	// database inside somebody else's.
	//
	// The password is NOT in this file, and it is NOT ON THE ARGV EITHER.
	//
	// /rag/config/ctl/units is 0755, so a literal in the unit is a credential
	// every account on a 1869-member host can read — which is why the unit
	// reads the tenant's secrets.env (0640) through EnvironmentFile. But
	// `--env POSTGRES_PASSWORD=${TENANT_PG_PASSWORD}` in ExecStart was the
	// same leak one step later: systemd expands ${…} into the ARGV, and
	// /proc/<pid>/cmdline is 0444 — every account on the host could read the
	// running postgres unit's password out of it with `ps` or `cat`.
	//
	// So the flag is gone and nothing replaces it on the command line.
	// secrets.env carries the value a SECOND time under the name
	// APPTAINERENV_POSTGRES_PASSWORD (ops/create.go writes both), systemd's
	// EnvironmentFile puts that into the service's environment, and apptainer
	// forwards any APPTAINERENV_<KEY> from its own environment into the
	// container as <KEY> — so the postgres entrypoint still sees
	// POSTGRES_PASSWORD, and the value never appears in a world-readable file
	// or a world-readable argv. (`Environment=` would not do: those lines live
	// in the unit FILE, which is the 0755 directory again, and systemd does
	// not expand a variable there in any case.)
	//
	// It matters only on the FIRST start — the postgres entrypoint ignores
	// POSTGRES_* once PGDATA is initialised — but a first start is exactly when
	// the role is created, so getting it from the wrong place would create a
	// role whose password nothing else knows.
	if pgs := &t.Stores.Postgres; pgs.Kind == registry.PostgresKindLocal {
		sif := string(pgs.SIF)
		if sif == "" {
			return nil, fmt.Errorf("tenant %s: postgres kind `local` has no sif", t.Name)
		}
		if _, err := paths.SafePath("/", sif); err != nil {
			return nil, err
		}
		port := int(pgs.Port)
		if port == 0 {
			port = t.Ports.PG
		}
		if port != t.Ports.PG {
			return nil, fmt.Errorf("tenant %s: stores.postgres.port is %d but the block's +5 port is %d", t.Name, port, t.Ports.PG)
		}
		stores = append(stores, pgU)
		out[pgU] = []byte(fmt.Sprintf(`[Unit]
Description=ragstack tenant %[1]s — postgres (:%[2]d)
PartOf=%[3]s
ConditionPathIsMountPoint=%[4]s
ConditionPathIsDirectory=%[5]s
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
UMask=0002
Environment=APPTAINER_CACHEDIR=%[6]s/apptainer/cache
Environment=APPTAINER_CONFIGDIR=%[6]s/apptainer/config
EnvironmentFile=%[7]s
ExecStart=%[8]s run --no-home --bind %[5]s:/var/lib/postgresql/data --bind %[9]s:/var/run/postgresql --env POSTGRES_USER=%[1]s --env POSTGRES_DB=%[1]s --env PGDATA=/var/lib/postgresql/data/pgdata %[10]s postgres -c port=%[2]d -c listen_addresses=127.0.0.1
LimitNOFILE=1048576
KillMode=mixed
TimeoutStopSec=90
Restart=on-failure
RestartSec=5
StandardOutput=append:%[11]s/postgres-%[12]s.log
StandardError=append:%[11]s/postgres-%[12]s.log
`, t.Name, port, target, cfg.MountPoint, tp.PostgresData, cfg.CtlStateDir, tp.SecretsEnv, cfg.ApptainerBin,
			tp.PostgresRun, sif, tp.LogsDir, t.ManifestName))
	}

	bind, err := apiBind(t, cfg)
	if err != nil {
		return nil, err
	}
	requires := ""
	if len(stores) > 0 {
		requires = "Requires=" + strings.Join(stores, " ") + "\nAfter=" + strings.Join(stores, " ") + "\n"
	}
	out[apiU] = []byte(fmt.Sprintf(`[Unit]
Description=ragstack tenant %[1]s — api (:%[2]d)
PartOf=%[3]s
%[4]sConditionPathIsMountPoint=%[5]s
ConditionPathIsDirectory=%[6]s
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
UMask=0002
WorkingDirectory=%[6]s/python
EnvironmentFile=%[7]s
EnvironmentFile=-%[8]s
Environment=PYTHONPATH=%[6]s/python
Environment=HF_HOME=%[9]s
Environment=PYTHONUNBUFFERED=1
Environment=RAGSTACK_GIT_TAG=%[10]s
Environment=RAGSTACK_GIT_SHA=%[11]s
ExecStartPre=%[12]s wait-ready %[1]s --timeout %[13]d
ExecStart=%[14]s/bin/python -m uvicorn ragstack.api.main:app --host %[15]s --port %[2]d
LimitNOFILE=1048576
KillMode=mixed
TimeoutStopSec=60
Restart=on-failure
RestartSec=5
StandardOutput=append:%[16]s
StandardError=append:%[16]s
`, t.Name, t.Ports.API, target, requires, cfg.MountPoint, t.Worktree, tp.TenantEnv, tp.SecretsEnv, cfg.HFHome,
		t.Code.Tag, t.Code.SHA, cfg.CtlBin, cfg.ReadyTimeout, t.PythonEnv, bind, tp.APILog))

	wants := append(append([]string{}, stores...), apiU)
	if t.UI.Mode == registry.UIModeDev {
		if t.UI.Port < 1024 || t.UI.Port > 65535 {
			return nil, fmt.Errorf("tenant %s: ui mode dev needs a port in range, got %d", t.Name, t.UI.Port)
		}
		base := t.UI.Base
		if base == "" {
			base = "/ragstack/" + t.Name + "/ui/"
		}
		if base != "/ragstack/"+t.Name+"/ui/" {
			return nil, fmt.Errorf("tenant %s: ui base %q must be /ragstack/<name>/ui/", t.Name, base)
		}
		wants = append(wants, uiU)
		out[uiU] = []byte(fmt.Sprintf(`[Unit]
Description=ragstack tenant %[1]s — vite dev ui (:%[2]d)
PartOf=%[3]s
After=%[4]s
ConditionPathIsMountPoint=%[5]s
ConditionPathIsDirectory=%[6]s/frontend/node_modules
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
UMask=0002
WorkingDirectory=%[6]s/frontend
Environment=VITE_API_TARGET=http://127.0.0.1:%[7]d
ExecStart=%[6]s/frontend/node_modules/.bin/vite --host 127.0.0.1 --port %[2]d --strictPort --base %[8]s
LimitNOFILE=1048576
KillMode=mixed
TimeoutStopSec=30
Restart=on-failure
RestartSec=5
StandardOutput=append:%[9]s/ui-%[10]s.log
StandardError=append:%[9]s/ui-%[10]s.log
`, t.Name, t.UI.Port, target, apiU, cfg.MountPoint, t.Worktree, t.Ports.API, base, tp.LogsDir, t.ManifestName))
	}

	install := ""
	if t.DesiredBoot == "enabled" {
		install = "\n[Install]\nWantedBy=default.target\n"
	}
	out[target] = []byte(fmt.Sprintf(`[Unit]
Description=ragstack tenant %[1]s
Wants=%[2]s
ConditionPathIsMountPoint=%[3]s
%[4]s`, t.Name, strings.Join(wants, " "), cfg.MountPoint, install))
	return out, nil
}
