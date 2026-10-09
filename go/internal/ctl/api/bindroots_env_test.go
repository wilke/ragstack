package api

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

func TestParseAPIBindRoots(t *testing.T) {
	def := []drivers.BindRoot{
		{Path: "/rag/cache"}, {Path: "/scout/containers", ReadOnly: true}, {Path: "/rag/config", ReadOnly: true},
	}
	for _, c := range []struct {
		in   string
		want []drivers.BindRoot
	}{
		{"", def},
		{"   ", def},
		{DefaultAPIBindRoots, def},
		// A bare path is read-only.
		{"/rag/cache", []drivers.BindRoot{{Path: "/rag/cache", ReadOnly: true}}},
		{"/rag/cache:rw", []drivers.BindRoot{{Path: "/rag/cache"}}},
		{" /rag/cache:rw , /scout/containers:ro ", []drivers.BindRoot{{Path: "/rag/cache"}, {Path: "/scout/containers", ReadOnly: true}}},
		// Trailing and doubled slashes are normalised.
		{"/rag/cache/:rw", []drivers.BindRoot{{Path: "/rag/cache"}}},
		{"/rag//cache/", []drivers.BindRoot{{Path: "/rag/cache", ReadOnly: true}}},
		{"/a/./b:ro", []drivers.BindRoot{{Path: "/a/b", ReadOnly: true}}},
	} {
		got, err := ParseAPIBindRoots(c.in)
		if err != nil {
			t.Errorf("ParseAPIBindRoots(%q) = %v", c.in, err)
			continue
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("ParseAPIBindRoots(%q) = %v, want %v", c.in, got, c.want)
		}
	}
	for _, bad := range []string{
		"rag/cache",                    // relative
		"./cache:rw",                   // relative
		"/rag/../etc:ro",               // ..
		"/rag/cache/..:ro",             // .. at the end
		"/",                            // everything
		"/:ro",                         // everything
		"//",                           // everything, after Clean
		"/rag/cache:rwx",               // mode
		"/rag/cache:RO",                // mode
		"/rag/cache:",                  // empty mode
		"/rag/cache,,/rag/config",      // empty entry
		"/rag/cache,",                  // trailing empty entry
		"/rag/cache:ro,/rag/cache/:rw", // the same root twice
		"/rag/ca che",                  // charset
	} {
		if got, err := ParseAPIBindRoots(bad); err == nil {
			t.Errorf("ParseAPIBindRoots(%q) = %v, want an error", bad, got)
		}
	}
}

// buildRealDrivers builds the engine over the REAL driver set (nothing runs:
// constructing drivers touches no host) and returns its bind roots.
func buildRealDrivers(t *testing.T, cfg EngineConfig) []drivers.BindRoot {
	t.Helper()
	dir := t.TempDir()
	cfg.Roots = paths.NewRoots(dir, paths.Overrides{})
	cfg.RegistryPath = filepath.Join(dir, "registry.json")
	cfg.StorePath = filepath.Join(dir, "jobs.db")
	cfg.Mode = model.WorkerDirect
	cfg.Host = "test"
	writeFleet(t, cfg.RegistryPath, FixtureFleet())
	_, drv, err := BuildEngineAndDrivers(cfg)
	if err != nil {
		t.Fatalf("BuildEngineAndDrivers: %v", err)
	}
	real, ok := drv.(*drivers.Real)
	if !ok {
		t.Fatalf("BuildEngineAndDrivers built %T, want *drivers.Real", drv)
	}
	return real.ExtraBindRoots()
}

func TestBuildEngineAppliesAPIBindRoots(t *testing.T) {
	def, _ := ParseAPIBindRoots("")
	// No configuration at all: the default, not "nothing bindable".
	if got := buildRealDrivers(t, EngineConfig{}); !reflect.DeepEqual(got, def) {
		t.Errorf("unset APIBindRoots → %v, want the default %v", got, def)
	}
	// The daemon's path: the environment, through SetAPIBindRootsFromEnv.
	t.Setenv(EnvAPIBindRoots, "/srv/models:rw,/srv/images")
	var cfg EngineConfig
	if err := SetAPIBindRootsFromEnv(&cfg); err != nil {
		t.Fatal(err)
	}
	want := []drivers.BindRoot{{Path: "/srv/models"}, {Path: "/srv/images", ReadOnly: true}}
	if got := buildRealDrivers(t, cfg); !reflect.DeepEqual(got, want) {
		t.Errorf("CTL_API_BIND_ROOTS → %v, want %v", got, want)
	}
	// A malformed value is an error, not a partial list.
	t.Setenv(EnvAPIBindRoots, "/srv/models:rw,relative")
	cfg = EngineConfig{}
	if err := SetAPIBindRootsFromEnv(&cfg); err == nil || cfg.APIBindRoots != nil {
		t.Errorf("a malformed CTL_API_BIND_ROOTS = %v (roots %v), want an error and nothing set", err, cfg.APIBindRoots)
	}
}

// RunServe refuses to start on a malformed CTL_API_BIND_ROOTS.
func TestServeRefusesAMalformedAPIBindRoots(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "state"), 0o750); err != nil {
		t.Fatal(err)
	}
	t.Setenv(EnvStateDir, filepath.Join(dir, "state"))
	t.Setenv(EnvConfigDir, filepath.Join(dir, "config"))
	t.Setenv(EnvAPIBindRoots, "/:rw")
	t.Setenv(EnvAPIKeys, "")
	done := make(chan int, 1)
	go func() { done <- RunServe([]string{"--fake-drivers", "--listen", "127.0.0.1:0"}) }()
	select {
	case rc := <-done:
		if rc != exitUsage {
			t.Fatalf("RunServe with CTL_API_BIND_ROOTS=/:rw = %d, want %d", rc, exitUsage)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("RunServe started serving with CTL_API_BIND_ROOTS=/:rw; it must refuse")
	}
}
