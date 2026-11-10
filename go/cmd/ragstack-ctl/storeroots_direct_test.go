package main

import (
	"errors"
	"flag"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/drivers"
)

// --direct builds its driver set with the same CTL_STORE_DATA_ROOTS the
// daemon reads — from the environment, else from ctl.env, which ctl-as-svc.sh
// does not load — and refuses a malformed or overlapping one.
func TestDirectEngineAppliesStoreDataRoots(t *testing.T) {
	root := t.TempDir()
	state := filepath.Join(root, "state")
	cfgDir := filepath.Join(root, "config")
	for _, d := range []string{state, cfgDir} {
		if err := os.MkdirAll(d, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv(api.EnvStateDir, state)
	t.Setenv(api.EnvConfigDir, cfgDir)
	t.Setenv(api.EnvAPIBindRoots, "")
	build := func() ([]string, error) {
		fs := flag.NewFlagSet("test", flag.ContinueOnError)
		o := addOpFlags(fs, filepath.Join(root, "registry.json"), root, false)
		if err := fs.Parse(nil); err != nil {
			t.Fatal(err)
		}
		_, drv, err := buildDirectEngineAndDrivers(o)
		if err != nil {
			return nil, err
		}
		return drv.(*drivers.Real).StoreDataRoots(), nil
	}

	t.Setenv(api.EnvStoreDataRoots, "")
	if got, err := build(); err != nil || len(got) != 0 {
		t.Errorf("--direct with nothing configured = %v, %v; want none", got, err)
	}
	if err := os.WriteFile(filepath.Join(cfgDir, "ctl.env"), []byte("CTL_STORE_DATA_ROOTS=/qdrant\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	if got, err := build(); err != nil || !reflect.DeepEqual(got, []string{"/qdrant"}) {
		t.Errorf("--direct with ctl.env naming /qdrant = %v, %v; want [/qdrant]", got, err)
	}
	t.Setenv(api.EnvStoreDataRoots, "/qdrant:rw")
	if got, err := build(); err == nil {
		t.Errorf("--direct with a malformed CTL_STORE_DATA_ROOTS built %v, want an error", got)
	}
	// Overlapping a bind root (the default /rag/cache:rw) refuses too.
	t.Setenv(api.EnvStoreDataRoots, "/rag/cache")
	if got, err := build(); err == nil {
		t.Errorf("--direct with a store root equal to a bind root built %v, want an error", got)
	}
}

// Second review item 5: a --direct run by an account that cannot read
// ctl.env refuses with that reason instead of running with no store roots.
func TestDirectEngineRefusesAnUnreadableCtlEnv(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root reads a 0000 file")
	}
	root := t.TempDir()
	state := filepath.Join(root, "state")
	cfgDir := filepath.Join(root, "config")
	for _, d := range []string{state, cfgDir} {
		if err := os.MkdirAll(d, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(cfgDir, "ctl.env"), []byte("CTL_STORE_DATA_ROOTS=/qdrant\n"), 0o000); err != nil {
		t.Fatal(err)
	}
	t.Setenv(api.EnvStateDir, state)
	t.Setenv(api.EnvConfigDir, cfgDir)
	t.Setenv(api.EnvAPIBindRoots, "")
	t.Setenv(api.EnvStoreDataRoots, "")
	fs := flag.NewFlagSet("test", flag.ContinueOnError)
	o := addOpFlags(fs, filepath.Join(root, "registry.json"), root, false)
	if err := fs.Parse(nil); err != nil {
		t.Fatal(err)
	}
	if _, _, err := buildDirectEngineAndDrivers(o); !errors.Is(err, api.ErrCtlEnvUnreadable) {
		t.Errorf("--direct over an unreadable ctl.env = %v, want ErrCtlEnvUnreadable", err)
	}
}
