package main

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

const cliTestImage = "ragstack-server-v1.6.6-b1.sif"

// imageRegistry is the live fixture with one prepared server image and dev
// running it.
func imageRegistry(t *testing.T) (dir, reg string) {
	t.Helper()
	dir = t.TempDir()
	reg = filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	commit := strings.Repeat("ab", 20)
	sha := strings.Repeat("cd", 32)
	path := "/rag/data/ctl/images/server/" + cliTestImage
	f.ServerImages = map[string]*registry.ServerImageRecord{cliTestImage: {Version: "v1.6.6", Commit: commit, Build: 1,
		SHA256: sha, Path: path, PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:1000"}}
	f.Tenants["dev"].ServerImage = &registry.ServerImage{Name: cliTestImage, Version: "v1.6.6", Commit: commit,
		Build: 1, SHA256: sha, Path: path}
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	return dir, reg
}

func TestFleetImageList(t *testing.T) {
	_, reg := imageRegistry(t)
	rc, out, errs := capture(t, "fleet", "image", "list", "--registry", reg)
	if rc != exitOK || !strings.Contains(out, cliTestImage) || !strings.Contains(out, "dev") {
		t.Fatalf("rc %d %s\n%s", rc, errs, out)
	}
	rc, out, _ = capture(t, "fleet", "image", "list", "--registry", reg, "--json")
	var rows []map[string]any
	if rc != exitOK || json.Unmarshal([]byte(out), &rows) != nil || len(rows) != 1 || rows[0]["name"] != cliTestImage {
		t.Fatalf("--json: rc %d %s", rc, out)
	}
	if ts, _ := rows[0]["tenants"].([]any); len(ts) != 1 || ts[0] != "dev" {
		t.Errorf("tenants = %v", rows[0]["tenants"])
	}
	_, empty := scratchRegistry(t)
	if rc, out, _ := capture(t, "fleet", "image", "list", "--registry", empty); rc != exitOK || !strings.Contains(out, "no server images") {
		t.Errorf("empty: rc %d %s", rc, out)
	}
}

func TestFleetImagePrepareIsCLIOnly(t *testing.T) {
	for name, args := range map[string][]string{
		"no --sif":        {"fleet", "image", "prepare"},
		"--server":        {"fleet", "image", "prepare", "--sif", "/x/" + cliTestImage, "--server", "http://127.0.0.1:1"},
		"an unknown verb": {"fleet", "image", "pull"},
		"no verb":         {"fleet", "image"},
	} {
		if rc, _, _ := capture(t, args...); rc != exitUsage {
			t.Errorf("%s: rc %d, want usage", name, rc)
		}
	}
}

// `tenant show` and `fleet status` say which mode the API runs in and from
// which image.
func TestTenantShowAndFleetStatusShowTheImage(t *testing.T) {
	dir, reg := imageRegistry(t)
	rc, out, errs := capture(t, "tenant", "show", "dev", "--registry", reg, "--rag-root", dir)
	if rc != exitOK || !strings.Contains(out, "api mode image · server image "+cliTestImage) {
		t.Errorf("tenant show dev: rc %d %s\n%s", rc, errs, out)
	}
	rc, out, _ = capture(t, "tenant", "show", "demo", "--registry", reg, "--rag-root", dir)
	if rc != exitOK || !strings.Contains(out, "api mode worktree · server image -") {
		t.Errorf("tenant show demo: rc %d\n%s", rc, out)
	}
	rc, out, errs = capture(t, "fleet", "status", "--registry", reg, "--rag-root", dir)
	if rc != exitOK || !strings.Contains(out, "MODE") || !strings.Contains(out, cliTestImage) || !strings.Contains(out, "worktree") {
		t.Errorf("fleet status: rc %d %s\n%s", rc, errs, out)
	}
}
