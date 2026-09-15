package render

import (
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// ONE description of how a tenant's store is launched (plan PR-D2, "Supervisor
// seam").
//
// There are two supervisors now. `systemd` puts the command in a unit's
// ExecStart; `instance` runs the same command as `apptainer instance run
// <name>` so that a control plane with no user manager can still start, find
// and stop it. The two differ in FOUR characters of argv — `run` versus
// `instance run <name>` — and in nothing else, which is exactly the kind of
// agreement that decays: a bind added to the unit and not to the supervisor
// gives a tenant whose store starts and then cannot see its own data, and the
// failure appears at the tenant's first query, not at the start.
//
// So the command lives here, once. Units builds its ExecStart from StoreArgv,
// the instance supervisor builds its InstanceSpec from StoreArgv, and
// render_test asserts the unit's line IS the rendered StoreArgv. The same is
// done for the api process by APIArgv.

// The legs StoreArgv knows. They are the component names `start --only` uses,
// not the instance kinds: the elasticsearch leg is `es` everywhere in the ctl
// and `elasticsearch-<tenant>` only as an instance name.
const (
	LegQdrant   = "qdrant"
	LegES       = "es"
	LegPostgres = "postgres"
)

// Store is how ONE of a tenant's stores is launched.
//
// It is the argv AFTER `apptainer run --no-home` (or `apptainer instance run
// --no-home … <Instance>`), split into the parts a caller has to keep apart,
// plus the process environment apptainer itself needs.
type Store struct {
	// Instance is the apptainer instance name the `instance` supervisor
	// starts this store under: `<kind>-<manifest_name>`. It is the name the
	// hand-started tenants ALREADY run under (`qdrant-dev`,
	// `elasticsearch-hackathon`), so that after an adopted tenant's handover
	// the ctl manages the instances that are already there rather than
	// starting second copies beside them. The unit path ignores it.
	Instance string
	// SIF is the absolute image path.
	SIF string
	// Binds are "host:container" pairs, in the order the command renders them.
	Binds []string
	// Env are the `--env` pairs: the CONTAINER's environment, as apptainer
	// puts it on the argv. That argv is world-readable
	// (`/proc/<pid>/cmdline` is 0444 on a 1869-member host), so NO SECRET may
	// be in here — see ProcessEnv for where the postgres password goes and
	// render/units.go's postgres comment for what it cost to learn.
	Env map[string]string
	// EnvOrder is the order the --env flags render in.
	//
	// A map has no order and the units were written by hand, so qdrant's HTTP
	// port comes before its GRPC port and postgres's PGDATA comes last — not
	// the order sorting gives. The alternative was to re-order the units,
	// which would change what is on every running tenant's command line for
	// the sake of a data structure. Env answers "what is this store's
	// FOO set to"; EnvOrder answers "how does the command read".
	EnvOrder []string
	// Args are the runscript arguments — everything after the image (and,
	// for an instance, after the instance name).
	Args []string
	// ProcessEnv is apptainer's OWN environment, not the container's:
	// APPTAINER_CACHEDIR and APPTAINER_CONFIGDIR, which the unit sets with
	// `Environment=` lines, and — for postgres — the name
	// `APPTAINERENV_POSTGRES_PASSWORD` with an EMPTY value.
	//
	// That empty value is the contract: it means "this variable must be in
	// the child's environment, and its value comes from the tenant's
	// secrets.env, read at RUN time". The renderer never sees the password,
	// because a renderer that saw it would eventually print it. apptainer
	// forwards any APPTAINERENV_<KEY> into the container as <KEY>, which is
	// how the postgres entrypoint gets POSTGRES_PASSWORD without it ever
	// appearing in a 0755 unit file or a 0444 cmdline.
	ProcessEnv map[string]string
}

// APPTAINERENVPostgresPassword is the ProcessEnv key whose value comes from
// secrets.env at run time. Named, so the supervisor that fills it in and the
// renderer that asks for it cannot disagree about the spelling.
const APPTAINERENVPostgresPassword = "APPTAINERENV_POSTGRES_PASSWORD"

// BindArgs renders the binds as argv: `--bind h:c` each.
func (s Store) BindArgs() []string {
	out := make([]string, 0, 2*len(s.Binds))
	for _, b := range s.Binds {
		out = append(out, "--bind", b)
	}
	return out
}

// EnvArgs renders the container environment as argv: `--env K=V` each, in
// EnvOrder.
func (s Store) EnvArgs() []string {
	out := make([]string, 0, 2*len(s.EnvOrder))
	for _, k := range s.EnvOrder {
		out = append(out, "--env", k+"="+s.Env[k])
	}
	return out
}

// Argv is the whole command after `apptainer run --no-home`, as EXEC takes it
// — one element per argument, nothing quoted, because nothing is going through
// a shell.
func (s Store) Argv() []string {
	out := append(s.BindArgs(), s.EnvArgs()...)
	out = append(out, s.SIF)
	return append(out, s.Args...)
}

// CommandLine is Argv as a single line, quoted the way the unit files are.
//
// It exists for the unit renderer and for anything that PRINTS the command (a
// plan preview, a job log). The quoting is by role rather than by a general
// shell escaper: an `--env` pair with a space is double-quoted, so that
// systemd's own parser takes it as one argument; a runscript argument with a
// space is single-quoted, so that the `sh -c` inside the container sees it
// verbatim. Those are the two forms the hand-written units use, and this
// function reproduces them exactly — render_test asserts it.
func (s Store) CommandLine() string {
	parts := s.BindArgs()
	for _, k := range s.EnvOrder {
		pair := k + "=" + s.Env[k]
		if strings.ContainsAny(pair, " \t") {
			pair = `"` + pair + `"`
		}
		parts = append(parts, "--env", pair)
	}
	parts = append(parts, s.SIF)
	for _, a := range s.Args {
		if strings.ContainsAny(a, " \t") {
			a = "'" + a + "'"
		}
		parts = append(parts, a)
	}
	return strings.Join(parts, " ")
}

// prepared is the validation and the path derivation Units, StoreArgv and
// APIArgv all need, done once.
type prepared struct {
	cfg UnitConfig
	tp  paths.Tenant
}

// prepare validates t against cfg and derives its paths.
//
// Every path is derived from t.DataDir ITSELF — its parent as the tenants root
// and its basename as the directory name — not from <parent>/<manifest_name>.
// TenantPaths joins the root with the manifest name, so feeding it
// t.ManifestName rebuilt a path that only equals t.DataDir when the two
// already agree: for the adopted pairs whose row name and directory differ,
// the ES unit bound a data dir the tenant does not use. checkTenant refuses
// that disagreement outright unless the caller opted in, and this derivation
// follows the data dir either way.
func prepare(t *registry.Tenant, cfg UnitConfig) (prepared, error) {
	if err := checkTenant(t, checkOptions{AllowOffLayoutDataDir: cfg.AllowOffLayoutDataDir}); err != nil {
		return prepared{}, err
	}
	cfg = cfg.withDefaults()
	for _, p := range []string{t.DataDir, t.Worktree, t.PythonEnv, cfg.CtlBin, cfg.ApptainerBin, cfg.CtlStateDir, cfg.HFHome} {
		if p == "" {
			return prepared{}, fmt.Errorf("tenant %s: data_dir, worktree, python_env and every UnitConfig path are required", t.Name)
		}
		if _, err := paths.SafePath("/", p); err != nil {
			return prepared{}, err
		}
	}
	for _, s := range []string{t.Code.Tag, string(t.Code.SHA), t.API.Bind} {
		if strings.ContainsAny(s, " \t\n\"'\\$;") {
			return prepared{}, fmt.Errorf("tenant %s: unsafe unit value %q", t.Name, s)
		}
	}
	tp := paths.TenantPaths(
		paths.NewRoots(cfg.RagRoot, paths.Overrides{DataDir: filepath.Dir(t.DataDir)}),
		t.Name, filepath.Base(t.DataDir))
	return prepared{cfg: cfg, tp: tp}, nil
}

// apptainerProcessEnv is the pair of directories apptainer needs of its own.
// They are under the ctl's state dir rather than a home directory, which is
// the whole point: nothing in production may reference /home, and a rootless
// apptainer with no cache dir writes into one.
func apptainerProcessEnv(cfg UnitConfig) map[string]string {
	return map[string]string{
		"APPTAINER_CACHEDIR":  cfg.CtlStateDir + "/apptainer/cache",
		"APPTAINER_CONFIGDIR": cfg.CtlStateDir + "/apptainer/config",
	}
}

// StoreArgv is how leg (`qdrant`, `es` or `postgres`) is launched for t.
//
// It refuses a leg this tenant does not OWN — a shared or external store is
// somebody else's process, and the ctl never supervises what it does not own —
// with the same message the unit renderer used to fail with.
func StoreArgv(t *registry.Tenant, leg string, cfg UnitConfig) (Store, error) {
	p, err := prepare(t, cfg)
	if err != nil {
		return Store{}, err
	}
	cfg, tp := p.cfg, p.tp

	switch leg {
	case LegQdrant:
		q := &t.Stores.Qdrant
		if q.Ownership != registry.OwnershipExclusive {
			return Store{}, fmt.Errorf("tenant %s: the qdrant store is %s, not exclusive: the ctl does not supervise a store it does not own", t.Name, q.Ownership)
		}
		sif := string(q.SIF)
		if sif == "" {
			return Store{}, fmt.Errorf("tenant %s: exclusive qdrant store has no sif", t.Name)
		}
		if _, err := paths.SafePath("/", sif); err != nil {
			return Store{}, err
		}
		return Store{
			Instance: "qdrant-" + t.ManifestName,
			SIF:      sif,
			Binds: []string{
				tp.QdrantStorage + ":/qdrant/storage",
				tp.QdrantSnapshots + ":/qdrant/snapshots",
			},
			Env: map[string]string{
				"QDRANT__SERVICE__HTTP_PORT": strconv.Itoa(t.Ports.QdrantHTTP),
				"QDRANT__SERVICE__GRPC_PORT": strconv.Itoa(t.Ports.QdrantGRPC),
			},
			EnvOrder: []string{"QDRANT__SERVICE__HTTP_PORT", "QDRANT__SERVICE__GRPC_PORT"},
			// The image's entrypoint.sh resolves its own paths relative to
			// /qdrant, and apptainer starts a runscript in the caller's cwd.
			Args:       []string{"/bin/sh", "-c", "cd /qdrant && exec ./entrypoint.sh"},
			ProcessEnv: apptainerProcessEnv(cfg),
		}, nil

	case LegES:
		e := &t.Stores.Elasticsearch
		if e.Ownership != registry.OwnershipExclusive {
			return Store{}, fmt.Errorf("tenant %s: the elasticsearch store is %s, not exclusive: the ctl does not supervise a store it does not own", t.Name, e.Ownership)
		}
		sif := string(e.SIF)
		if sif == "" {
			return Store{}, fmt.Errorf("tenant %s: exclusive elasticsearch store has no sif", t.Name)
		}
		if _, err := paths.SafePath("/", sif); err != nil {
			return Store{}, err
		}
		heap := string(e.Heap)
		if heap == "" {
			heap = string(e.ProvisionHeap)
		}
		if heap == "" {
			heap = "512m"
		}
		if !heapPattern.MatchString(heap) {
			return Store{}, fmt.Errorf("tenant %s: invalid ES heap %q", t.Name, heap)
		}
		repo := string(e.PathRepo)
		if repo == "" {
			repo = "/usr/share/elasticsearch/snapshots"
		}
		if _, err := paths.SafePath("/", repo); err != nil {
			return Store{}, err
		}
		return Store{
			Instance: "elasticsearch-" + t.ManifestName,
			SIF:      sif,
			Binds: []string{
				tp.ESData + ":/usr/share/elasticsearch/data",
				tp.ESLogs + ":/usr/share/elasticsearch/logs",
				// The config bind SHADOWS the image's own config directory, so
				// the host directory has to be seeded before this ever runs —
				// the unit does it with `ExecStartPre=… es-seed-config`, and
				// any other supervisor owes the same step. An empty config
				// directory is an Elasticsearch that exits before it logs
				// anything useful.
				tp.ESConfig + ":/usr/share/elasticsearch/config",
				tp.ESSnapshots + ":" + repo,
			},
			Env:      map[string]string{"ES_JAVA_OPTS": "-Xms" + heap + " -Xmx" + heap},
			EnvOrder: []string{"ES_JAVA_OPTS"},
			Args: []string{
				"/usr/local/bin/docker-entrypoint.sh", "eswrapper",
				"-Ediscovery.type=single-node",
				"-Expack.security.enabled=false",
				"-Ehttp.port=" + strconv.Itoa(t.Ports.ESHTTP),
				"-Etransport.port=" + strconv.Itoa(t.Ports.ESTransport),
				"-Epath.repo=" + repo,
			},
			ProcessEnv: apptainerProcessEnv(cfg),
		}, nil

	case LegPostgres:
		pg := &t.Stores.Postgres
		if pg.Kind != registry.PostgresKindLocal {
			return Store{}, fmt.Errorf("tenant %s: postgres kind is %q, not `local`: only a local postgres is a server the ctl runs", t.Name, pg.Kind)
		}
		sif := string(pg.SIF)
		if sif == "" {
			return Store{}, fmt.Errorf("tenant %s: postgres kind `local` has no sif", t.Name)
		}
		if _, err := paths.SafePath("/", sif); err != nil {
			return Store{}, err
		}
		port := int(pg.Port)
		if port == 0 {
			port = t.Ports.PG
		}
		if port != t.Ports.PG {
			return Store{}, fmt.Errorf("tenant %s: stores.postgres.port is %d but the block's +5 port is %d", t.Name, port, t.Ports.PG)
		}
		env := apptainerProcessEnv(cfg)
		// The NAME, with no value: the supervisor reads the value out of the
		// tenant's secrets.env at run time. See Store.ProcessEnv.
		env[APPTAINERENVPostgresPassword] = ""
		return Store{
			Instance: "postgres-" + t.ManifestName,
			SIF:      sif,
			Binds: []string{
				tp.PostgresData + ":/var/lib/postgresql/data",
				tp.PostgresRun + ":/var/run/postgresql",
			},
			Env: map[string]string{
				"POSTGRES_USER": t.Name,
				"POSTGRES_DB":   t.Name,
				"PGDATA":        "/var/lib/postgresql/data/pgdata",
			},
			EnvOrder: []string{"POSTGRES_USER", "POSTGRES_DB", "PGDATA"},
			Args: []string{
				"postgres",
				"-c", "port=" + strconv.Itoa(port),
				"-c", "listen_addresses=127.0.0.1",
			},
			ProcessEnv: env,
		}, nil
	}
	return Store{}, fmt.Errorf("tenant %s: %q is not a store leg (want %s, %s or %s)", t.Name, leg, LegQdrant, LegES, LegPostgres)
}

// APIArgv is how the tenant's API process is launched: the program, its
// arguments, and the environment the api unit sets with `Environment=` lines.
//
// env is ONLY those lines — PYTHONPATH, HF_HOME, PYTHONUNBUFFERED and the two
// build stamps. It is not the whole environment the process needs: the unit
// gets tenant.env and secrets.env through `EnvironmentFile=`, and a supervisor
// that spawns the process directly parses those two files itself and unions
// them with this. Keeping the secret-bearing half out of a RENDERER is the
// same rule the stores follow: nothing that can be printed ever holds a
// secret.
func APIArgv(t *registry.Tenant, cfg UnitConfig) (program string, args []string, env []string, err error) {
	p, prepErr := prepare(t, cfg)
	if prepErr != nil {
		return "", nil, nil, prepErr
	}
	cfg = p.cfg
	bind, err := apiBind(t, cfg)
	if err != nil {
		return "", nil, nil, err
	}
	program = t.PythonEnv + "/bin/python"
	args = []string{
		"-m", "uvicorn", "ragstack.api.main:app",
		"--host", bind,
		"--port", strconv.Itoa(t.Ports.API),
	}
	env = []string{
		"PYTHONPATH=" + t.Worktree + "/python",
		"HF_HOME=" + cfg.HFHome,
		"PYTHONUNBUFFERED=1",
		"RAGSTACK_GIT_TAG=" + t.Code.Tag,
		"RAGSTACK_GIT_SHA=" + string(t.Code.SHA),
	}
	return program, args, env, nil
}

// APICommandLine is the api's ExecStart after the `ExecStart=`.
func APICommandLine(program string, args []string) string {
	return strings.Join(append([]string{program}, args...), " ")
}
