package main

import (
	"flag"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/drivers"
)

// --direct builds its driver set with the same CTL_API_BIND_ROOTS the daemon
// reads (PR-F F2), and refuses a malformed one before opening anything.
func TestDirectEngineAppliesAPIBindRoots(t *testing.T) {
	root := t.TempDir()
	state := filepath.Join(root, "state")
	if err := os.MkdirAll(state, 0o750); err != nil {
		t.Fatal(err)
	}
	t.Setenv(api.EnvStateDir, state)
	t.Setenv(api.EnvConfigDir, filepath.Join(root, "config"))
	build := func() ([]drivers.BindRoot, error) {
		fs := flag.NewFlagSet("test", flag.ContinueOnError)
		o := addOpFlags(fs, filepath.Join(root, "registry.json"), root, false)
		if err := fs.Parse(nil); err != nil {
			t.Fatal(err)
		}
		_, drv, err := buildDirectEngineAndDrivers(o)
		if err != nil {
			return nil, err
		}
		real, ok := drv.(*drivers.Real)
		if !ok {
			t.Fatalf("--direct built %T, want *drivers.Real", drv)
		}
		return real.ExtraBindRoots(), nil
	}

	t.Setenv(api.EnvAPIBindRoots, "")
	def, _ := api.ParseAPIBindRoots("")
	if got, err := build(); err != nil || !reflect.DeepEqual(got, def) {
		t.Errorf("--direct with CTL_API_BIND_ROOTS unset = %v, %v; want %v", got, err, def)
	}

	t.Setenv(api.EnvAPIBindRoots, "/srv/models:rw")
	want := []drivers.BindRoot{{Path: "/srv/models"}}
	if got, err := build(); err != nil || !reflect.DeepEqual(got, want) {
		t.Errorf("--direct with CTL_API_BIND_ROOTS=/srv/models:rw = %v, %v; want %v", got, err, want)
	}

	t.Setenv(api.EnvAPIBindRoots, "/srv/../etc")
	if got, err := build(); err == nil {
		t.Errorf("--direct with a malformed CTL_API_BIND_ROOTS built %v, want an error", got)
	}
}
