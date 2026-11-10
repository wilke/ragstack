package main

import (
	"bytes"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// `tenant set-store-dir` is an argument translator: internal/ctl/ops proves
// the plan. What is asserted here is what only the CLI can get wrong.

func TestSetStoreDirUsageRefusalsHappenBeforeAnythingIsSubmitted(t *testing.T) {
	for _, c := range []struct {
		name string
		args []string
		want string
	}{
		{"no positionals", []string{"tenant", "set-store-dir"}, "usage:"},
		{"two positionals", []string{"tenant", "set-store-dir", "dev", "extra", "--store", "qdrant", "--clear"}, "usage:"},
		{"no store", []string{"tenant", "set-store-dir", "dev", "--clear"}, "--store is required"},
		{"elasticsearch", []string{"tenant", "set-store-dir", "dev", "--store", "elasticsearch", "--clear"}, "only qdrant"},
		{"neither", []string{"tenant", "set-store-dir", "dev", "--store", "qdrant"}, "one of --dir"},
		{"both", []string{"tenant", "set-store-dir", "dev", "--store", "qdrant", "--dir", "/qdrant/tenants/dev", "--clear"}, "exclusive"},
		{"empty dir", []string{"tenant", "set-store-dir", "dev", "--store", "qdrant", "--dir", ""}, "absolute, clean"},
		{"relative dir", []string{"tenant", "set-store-dir", "dev", "--store", "qdrant", "--dir", "qdrant/dev"}, "absolute, clean"},
		{"unclean dir", []string{"tenant", "set-store-dir", "dev", "--store", "qdrant", "--dir", "/qdrant/tenants/dev/"}, "absolute, clean"},
	} {
		t.Run(c.name, func(t *testing.T) {
			rc, _, errb := capture(t, c.args...)
			if rc != exitUsage {
				t.Fatalf("rc = %d, want %d (%s)", rc, exitUsage, errb)
			}
			if !strings.Contains(errb, c.want) {
				t.Errorf("stderr %q does not mention %q", errb, c.want)
			}
		})
	}
}

func TestSetStoreDirRefusesServer(t *testing.T) {
	rc, _, errb := capture(t, "tenant", "set-store-dir", "dev", "--store", "qdrant", "--clear",
		"--server", "http://127.0.0.1:23990")
	if rc != exitUsage || !strings.Contains(errb, "no daemon route") {
		t.Errorf("rc = %d, stderr %q; want a usage refusal of --server", rc, errb)
	}
}

func TestSetStoreDirArgs(t *testing.T) {
	got, err := setStoreDirArgs("qdrant", "/qdrant/tenants/dev", false, true)
	if err != nil || !reflect.DeepEqual(got, map[string]any{"store": "qdrant", "dir": "/qdrant/tenants/dev"}) {
		t.Errorf("--dir args = %v, %v", got, err)
	}
	got, err = setStoreDirArgs("qdrant", "", true, false)
	if err != nil || !reflect.DeepEqual(got, map[string]any{"store": "qdrant", "clear": true}) {
		t.Errorf("--clear args = %v, %v", got, err)
	}
}

func TestSetStoreDirDocumentsItself(t *testing.T) {
	_, _, help := capture(t, "tenant", "set-store-dir")
	for _, want := range []string{
		"--store qdrant", "--dir ABS_PATH", "--clear", "STOPPED", "CTL_STORE_DATA_ROOTS", "snapshots/",
		"restart_pending", "no-op", "--direct", "registry validate", "docs/runbooks/qdrant-data-dir.md",
		"symlinks", "mounted volume",
	} {
		if !strings.Contains(help, want) {
			t.Errorf("set-store-dir usage does not mention %q:\n%s", want, help)
		}
	}
	_, _, tenantHelp := capture(t, "tenant")
	if !strings.Contains(tenantHelp, "tenant set-store-dir <name> --store qdrant") {
		t.Errorf("`tenant` usage does not list set-store-dir:\n%s", tenantHelp)
	}
}

// The other-binaries line is printed after a successful run, and only then.
func TestSetStoreDirPrintsTheOtherBinariesWarningOnSuccessOnly(t *testing.T) {
	for _, c := range []struct {
		rc     int
		dryRun bool
		want   bool
	}{{exitOK, false, true}, {exitOK, true, false}, {exitRefused, false, false}, {exitError, false, false}} {
		var buf bytes.Buffer
		old := stderr
		stderr = &buf
		printSetStoreDirWarning(c.rc, c.dryRun)
		stderr = old
		if got := strings.Contains(buf.String(), ops.OtherBinariesWarning); got != c.want {
			t.Errorf("rc %d dry-run %v: printed = %v, want %v", c.rc, c.dryRun, got, c.want)
		}
	}
	if strings.Count(ops.OtherBinariesWarning, "\n") != 0 || !strings.Contains(ops.OtherBinariesWarning, "registry validate") {
		t.Errorf("the warning is not one line naming `registry validate`: %q", ops.OtherBinariesWarning)
	}
}

// A registry carrying the field, as set-store-dir leaves it, is accepted by
// `registry validate`.
func TestRegistryValidateAcceptsAMovedQdrantDir(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	dev := f.Tenants["dev"]
	dev.Stores.Qdrant.Ownership = registry.OwnershipExclusive
	dev.Stores.Qdrant.DataDir = "/qdrant/tenants/dev"
	dev.RestartPending = true
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatalf("Save: %v", err)
	}
	rc, out, errb := capture(t, "registry", "validate", "--registry", reg, "--rag-root", dir)
	if rc != exitOK || !strings.Contains(out, "valid") {
		t.Errorf("registry validate: rc %d out %q err %q", rc, out, errb)
	}
}
