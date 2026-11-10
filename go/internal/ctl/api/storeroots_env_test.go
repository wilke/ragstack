package api

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

func TestParseStoreDataRoots(t *testing.T) {
	for _, c := range []struct {
		in   string
		want []string
	}{
		{"", nil},
		{"   ", nil},
		{"/qdrant", []string{"/qdrant"}},
		{" /qdrant , /vol2/stores ", []string{"/qdrant", "/vol2/stores"}},
	} {
		got, err := ParseStoreDataRoots(c.in)
		if err != nil {
			t.Errorf("ParseStoreDataRoots(%q) = %v", c.in, err)
			continue
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("ParseStoreDataRoots(%q) = %#v, want %#v", c.in, got, c.want)
		}
	}
	for _, bad := range []string{
		"/",                 // everything
		"//",                // everything, unclean
		"qdrant",            // relative
		"./qdrant",          // relative
		"/qdrant/../etc",    // ..
		"/qdrant/..",        // .. at the end
		"/qdrant/",          // not clean
		"/qdrant//x",        // not clean
		"/qdrant,/qdrant",   // duplicate
		"/qdrant:rw",        // a suffix — store roots are rw by definition
		"/qdrant:ro",        // a suffix
		"/qdrant:",          // an empty suffix
		"/qdrant,,/vol2",    // empty entry
		"/qdrant,",          // trailing empty entry
		"/qd rant",          // charset
		"/qdrant/tenants$x", // charset
	} {
		if got, err := ParseStoreDataRoots(bad); err == nil {
			t.Errorf("ParseStoreDataRoots(%q) = %v, want an error", bad, got)
		}
	}
	// A mode suffix is refused AS a suffix (the charset would refuse the
	// colon too, but the operator needs to read why: CTL_API_BIND_ROOTS takes
	// one and this list does not).
	for _, suffixed := range []string{"/qdrant:rw", "/qdrant:ro"} {
		if _, err := ParseStoreDataRoots(suffixed); err == nil || !strings.Contains(err.Error(), "suffix") {
			t.Errorf("ParseStoreDataRoots(%q) = %v, want an error naming the suffix", suffixed, err)
		}
	}
}

// The environment first; unset (or blank), ctl.env; neither, none. A
// malformed value from either is an error and sets nothing.
func TestSetStoreDataRootsFromEnvReadsTheEnvironmentThenCtlEnv(t *testing.T) {
	cfgDir := t.TempDir()
	cfg := EngineConfig{Roots: paths.Roots{CtlConfigDir: cfgDir}}

	t.Setenv(EnvStoreDataRoots, "")
	if err := SetStoreDataRootsFromEnv(&cfg); err != nil || cfg.StoreDataRoots != nil {
		t.Fatalf("nothing configured = %v, %v; want none", cfg.StoreDataRoots, err)
	}
	if err := os.WriteFile(filepath.Join(cfgDir, "ctl.env"),
		[]byte("CTL_LISTEN=127.0.0.1:23990\nCTL_STORE_DATA_ROOTS=/qdrant\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := SetStoreDataRootsFromEnv(&cfg); err != nil || !reflect.DeepEqual(cfg.StoreDataRoots, []string{"/qdrant"}) {
		t.Fatalf("ctl.env only = %v, %v; want [/qdrant]", cfg.StoreDataRoots, err)
	}
	t.Setenv(EnvStoreDataRoots, "/vol2")
	if err := SetStoreDataRootsFromEnv(&cfg); err != nil || !reflect.DeepEqual(cfg.StoreDataRoots, []string{"/vol2"}) {
		t.Fatalf("environment and ctl.env = %v, %v; want the environment's [/vol2]", cfg.StoreDataRoots, err)
	}
	t.Setenv(EnvStoreDataRoots, "/vol2:rw")
	cfg.StoreDataRoots = nil
	if err := SetStoreDataRootsFromEnv(&cfg); err == nil || cfg.StoreDataRoots != nil {
		t.Fatalf("a malformed value = %v (roots %v), want an error and nothing set", err, cfg.StoreDataRoots)
	}

	found := false
	for _, k := range HostToolEnvKeys() {
		found = found || k == EnvStoreDataRoots
	}
	if !found {
		t.Error("CTL_STORE_DATA_ROOTS is not registered in HostToolEnvKeys")
	}
}

// buildEngine builds the engine over a fresh /rag-shaped temp root and returns
// the driver set (nothing runs: constructing drivers touches no host).
func buildEngine(t *testing.T, cfg EngineConfig) (jobs.Drivers, paths.Roots, error) {
	t.Helper()
	dir := t.TempDir()
	cfg.Roots = paths.NewRoots(dir, paths.Overrides{})
	cfg.RegistryPath = filepath.Join(dir, "registry.json")
	cfg.StorePath = filepath.Join(dir, "jobs.db")
	cfg.Mode = model.WorkerDirect
	cfg.Host = "test"
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	writeFleet(t, cfg.RegistryPath, FixtureFleet())
	_, drv, err := BuildEngineAndDrivers(cfg)
	return drv, cfg.Roots, err
}

// The store roots reach the REAL driver set, and the files driver writes
// under them while the git driver — which never gets them — refuses.
func TestBuildEngineAppliesStoreDataRoots(t *testing.T) {
	store := t.TempDir()
	drv, _, err := buildEngine(t, EngineConfig{StoreDataRoots: []string{store}})
	if err != nil {
		t.Fatal(err)
	}
	real := drv.(*drivers.Real)
	if got := real.StoreDataRoots(); !reflect.DeepEqual(got, []string{store}) {
		t.Fatalf("Real.StoreDataRoots() = %v, want [%s]", got, store)
	}
	dir := filepath.Join(store, "tenants", "dev")
	if err := real.Files().MkdirAll(context.Background(), dir, 0o750); err != nil {
		t.Fatalf("MkdirAll under a store root through the engine's drivers: %v", err)
	}

	// None configured: none admitted, and the same write refuses.
	drv, _, err = buildEngine(t, EngineConfig{})
	if err != nil {
		t.Fatal(err)
	}
	if got := drv.(*drivers.Real).StoreDataRoots(); len(got) != 0 {
		t.Errorf("no store roots configured → %v, want none", got)
	}
	if err := drv.Files().MkdirAll(context.Background(), filepath.Join(store, "tenants", "x"), 0o750); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("MkdirAll under an unconfigured store root = %v, want a refusal", err)
	}
}

// The fake driver set takes the store roots too, so a conformance run against
// --fake-drivers refuses and accepts what the host would.
func TestBuildEngineFakeDriversTakeStoreDataRoots(t *testing.T) {
	store := t.TempDir()
	for _, roots := range [][]string{{store}, nil} {
		drv, _, err := buildEngine(t, EngineConfig{FakeDrivers: true, StoreDataRoots: roots})
		if err != nil {
			t.Fatal(err)
		}
		err = drv.Files().MkdirAll(context.Background(), filepath.Join(store, "tenants", "dev"), 0o750)
		if roots != nil && err != nil {
			t.Errorf("fake MkdirAll under a configured store root = %v, want success", err)
		}
		if roots == nil && !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("fake MkdirAll under an unconfigured store root = %v, want a refusal", err)
		}
	}
}

// A store root that is also (or overlaps) a CTL_API_BIND_ROOTS entry, or that
// overlaps an approved root, refuses the engine's construction: the
// most-specific/tie rule would otherwise turn `/qdrant:ro` into a refusal of
// every store start, or a store root around /rag/data would widen the files
// driver to all of it.
func TestBuildEngineRefusesOverlappingStoreDataRoots(t *testing.T) {
	for _, c := range []struct {
		name  string
		bind  []drivers.BindRoot
		store func(r paths.Roots) []string
	}{
		{"equal to a read-only bind root", []drivers.BindRoot{{Path: "/qdrant", ReadOnly: true}},
			func(paths.Roots) []string { return []string{"/qdrant"} }},
		{"equal to a writable bind root", []drivers.BindRoot{{Path: "/qdrant"}},
			func(paths.Roots) []string { return []string{"/qdrant"} }},
		{"under a bind root", []drivers.BindRoot{{Path: "/qdrant", ReadOnly: true}},
			func(paths.Roots) []string { return []string{"/qdrant/tenants"} }},
		{"containing a bind root", []drivers.BindRoot{{Path: "/qdrant/hf", ReadOnly: true}},
			func(paths.Roots) []string { return []string{"/qdrant"} }},
	} {
		t.Run(c.name, func(t *testing.T) {
			_, _, err := buildEngine(t, EngineConfig{APIBindRoots: c.bind, StoreDataRoots: c.store(paths.Roots{})})
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), EnvStoreDataRoots) {
				t.Fatalf("BuildEngineAndDrivers = %v, want a refusal naming %s", err, EnvStoreDataRoots)
			}
		})
	}
	// Overlap with an approved root: the roots are only known once the engine
	// has them, so the store root is spelled from the temp tree's layout.
	for _, rel := range []string{"", "data", filepath.Join("data", "tenants"), filepath.Join("data", "tenants", "x")} {
		dir := t.TempDir()
		cfg := EngineConfig{
			Roots:          paths.NewRoots(dir, paths.Overrides{}),
			RegistryPath:   filepath.Join(dir, "registry.json"),
			StorePath:      filepath.Join(dir, "jobs.db"),
			Mode:           model.WorkerDirect,
			Host:           "test",
			Now:            time.Now,
			StoreDataRoots: []string{filepath.Join(dir, rel)},
		}
		writeFleet(t, cfg.RegistryPath, FixtureFleet())
		if _, _, err := BuildEngineAndDrivers(cfg); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("a store root at <rag-root>/%s = %v, want a refusal (it overlaps an approved root)", rel, err)
		}
	}
}

// RunServe refuses to start on a malformed CTL_STORE_DATA_ROOTS, and on one
// that overlaps the bind roots.
func TestServeRefusesABadStoreDataRoots(t *testing.T) {
	for _, c := range []struct{ store, bind string }{
		{"/qdrant:rw", ""},
		{"/rag/cache", "/rag/cache:rw"},
	} {
		dir := t.TempDir()
		if err := os.MkdirAll(filepath.Join(dir, "state"), 0o750); err != nil {
			t.Fatal(err)
		}
		t.Setenv(EnvStateDir, filepath.Join(dir, "state"))
		t.Setenv(EnvConfigDir, filepath.Join(dir, "config"))
		t.Setenv(EnvStoreDataRoots, c.store)
		t.Setenv(EnvAPIBindRoots, c.bind)
		t.Setenv(EnvAPIKeys, "")
		done := make(chan int, 1)
		go func() { done <- RunServe([]string{"--fake-drivers", "--listen", "127.0.0.1:0"}) }()
		select {
		case rc := <-done:
			if rc == exitOK {
				t.Fatalf("RunServe with CTL_STORE_DATA_ROOTS=%s (bind roots %q) = 0, want a refusal", c.store, c.bind)
			}
		case <-time.After(20 * time.Second):
			t.Fatalf("RunServe started serving with CTL_STORE_DATA_ROOTS=%s (bind roots %q); it must refuse", c.store, c.bind)
		}
	}
}

// The role templates CTL_STORE_DATA_ROOTS only when it is set: a rendered
// `CTL_STORE_DATA_ROOTS=` is harmless (blank is none) but a row nobody chose
// is a setting an operator then has to explain.
func TestCtlEnvTemplateGuardsStoreDataRoots(t *testing.T) {
	b, err := os.ReadFile("../../../../ops/ansible/roles/ragstack-ctl/templates/ctl.env.j2")
	if os.IsNotExist(err) {
		t.Skip("no ops/ansible in this checkout")
	}
	if err != nil {
		t.Fatal(err)
	}
	const guarded = "{% if ctl_store_data_roots %}\nCTL_STORE_DATA_ROOTS={{ ctl_store_data_roots }}\n{% endif %}"
	if !strings.Contains(string(b), guarded) {
		t.Errorf("ctl.env.j2 does not carry the guarded row:\n%s", guarded)
	}
}
