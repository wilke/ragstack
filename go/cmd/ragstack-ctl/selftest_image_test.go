package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// fixtureImage is the server image the image-mode selftest creates from.
const fixtureImage = "ragstack-server-v1.6.6-b1.sif"

// newFixtureSelftestImage is the instance-mode fixture with a server image
// PREPARED: the record in the registry (at the fixture artifact's commit, so
// the static UI has an artifact to build from), and — on the fake host — the
// file the record's sha256 is of and the labels `apptainer inspect` would give
// it. The fake's Labels refuses an image it was never told about and its
// Sha256 hashes what is there, so the start's probe answers real facts about
// fixture bytes rather than being stubbed out.
func newFixtureSelftestImage(t *testing.T) (*selftest, *drivers.Fake, *bytes.Buffer) {
	t.Helper()
	s, fake, out := newFixtureSelftestWith(t, "instance")
	body := []byte("fixture server image")
	sum := sha256.Sum256(body)
	path := filepath.Join(s.roots.ServerImagesDir(), fixtureImage)
	commit := strings.Repeat("ab", 20)
	f, err := registry.Load(s.registryPath)
	if err != nil {
		t.Fatal(err)
	}
	f.ServerImages = map[string]*registry.ServerImageRecord{fixtureImage: {
		Version: "v1.6.6", Commit: commit, Build: 1, SHA256: hex.EncodeToString(sum[:]), Path: path,
		PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:0",
	}}
	if err := registry.Save(s.registryPath, f, "test"); err != nil {
		t.Fatal(err)
	}
	fake.FakeFiles().Put(path, body, 0o640)
	fake.FakeInstances().SetLabels(path, map[string]string{
		ops.LabelVersion: "v1.6.6", ops.LabelCommit: commit, ops.LabelBuild: "1", ops.LabelRole: ops.RoleServer,
	})
	s.opts.image, s.opts.artifact = fixtureImage, ""
	return s, fake, out
}

// The whole sequence in IMAGE mode: create → ingest → credentials (restarts)
// → backup --fence → stop → restore --as → decommission → purge, with the API
// of both sandboxes running as the apptainer instance api-<sandbox> and never
// as a spawned process — and the host clean at the end.
func TestSelftestRunsTheWholeSequenceInImageMode(t *testing.T) {
	s, fake, out := newFixtureSelftestImage(t)
	err := s.execute(context.Background())
	s.report()
	if err != nil {
		t.Fatalf("selftest.execute: %v\n%s", err, out.String())
	}
	log := strings.Join(fake.CallKeys(), "\n")
	for _, want := range []string{
		"instances.Run(api-" + fixturePrimary + ",",
		"instances.Stop(api-" + fixturePrimary + ",",
		"instances.Run(api-" + fixtureRestored + ",",
		"instances.Labels(",
	} {
		if !strings.Contains(log, want) {
			t.Errorf("no %q in the call log:\n%s", want, log)
		}
	}
	if strings.Contains(log, "proc.Spawn(") {
		t.Errorf("an image-mode selftest spawned a worktree API:\n%s", log)
	}
	if !strings.Contains(out.String(), "image mode: the API runs from server image "+fixtureImage) {
		t.Errorf("the run does not say it proved image mode:\n%s", out.String())
	}
	if s.failedChecks() > 0 {
		t.Errorf("%d check(s) failed:\n%s", s.failedChecks(), out.String())
	}
	var compared bool
	for _, c := range s.checks {
		if c.Name == "restored tenant matches the source" {
			compared = c.Verdict == checkPass
		}
	}
	if !compared {
		t.Errorf("the restored twin does not match the source (server_image included):\n%s", out.String())
	}
	// Nothing of either sandbox is left running.
	list, _ := s.drv.Instances().List(context.Background(), jobs.ListOptions{Namespace: jobs.NamespaceCtl})
	if len(list) != 0 {
		t.Errorf("instances still running: %+v", list)
	}
}

// --image without the instance supervisor is a usage error, before anything
// is built.
func TestSelftestImageNeedsTheInstanceSupervisor(t *testing.T) {
	if rc, _, _ := capture(t, "selftest", "--image", fixtureImage, "--supervisor", "systemd"); rc != exitUsage {
		t.Errorf("rc %d, want %d", rc, exitUsage)
	}
}

// An image that is not prepared is a refusal before the create.
func TestSelftestRefusesAnUnpreparedImage(t *testing.T) {
	s, _, _ := newFixtureSelftestWith(t, "instance")
	s.opts.image, s.opts.artifact = "ragstack-server-v9-b1.sif", ""
	if _, err := s.pickArtifact(); err == nil || !strings.Contains(err.Error(), "not prepared") {
		t.Errorf("pickArtifact = %v", err)
	}
}

// fixtureImage2 is a second build at the same commit: what F7's `-b2`
// upgrade on coconut is (image → image).
const fixtureImage2 = "ragstack-server-v1.6.6-b2.sif"

// newFixtureSelftestUpdate is the instance-mode fixture with TWO server images
// prepared (b1 and b2 at the fixture artifact's commit), and the sandbox
// created in WORKTREE mode: the run's --update-code legs take it worktree → b1
// (the migration) → b2 (image → image).
func newFixtureSelftestUpdate(t *testing.T) (*selftest, *drivers.Fake, *bytes.Buffer) {
	t.Helper()
	s, fake, out := newFixtureSelftestWith(t, "instance")
	commit := strings.Repeat("ab", 20)
	f, err := registry.Load(s.registryPath)
	if err != nil {
		t.Fatal(err)
	}
	f.ServerImages = map[string]*registry.ServerImageRecord{}
	for build, name := range map[int]string{1: fixtureImage, 2: fixtureImage2} {
		body := []byte("fixture server image b" + strconv.Itoa(build))
		sum := sha256.Sum256(body)
		path := filepath.Join(s.roots.ServerImagesDir(), name)
		f.ServerImages[name] = &registry.ServerImageRecord{
			Version: "v1.6.6", Commit: commit, Build: build, SHA256: hex.EncodeToString(sum[:]), Path: path,
			PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:0",
		}
		fake.FakeFiles().Put(path, body, 0o640)
		fake.FakeInstances().SetLabels(path, map[string]string{
			ops.LabelVersion: "v1.6.6", ops.LabelCommit: commit, ops.LabelBuild: strconv.Itoa(build),
			ops.LabelRole: ops.RoleServer,
		})
	}
	if err := registry.Save(s.registryPath, f, "test"); err != nil {
		t.Fatal(err)
	}
	s.opts.updateCode = []string{fixtureImage, fixtureImage2}
	return s, fake, out
}

// The update-code legs (PR-F F5): a worktree sandbox is migrated onto b1 and
// moved on to b2, each leg proved (row, instance, /v1/version), and the rest
// of the sequence — backup, stop, restore --as, decommission, purge — runs on
// the image-mode sandbox, with the restored twin inheriting b2.
func TestSelftestRunsTheUpdateCodeLegs(t *testing.T) {
	s, fake, out := newFixtureSelftestUpdate(t)
	err := s.execute(context.Background())
	s.report()
	if err != nil {
		t.Fatalf("selftest.execute: %v\n%s", err, out.String())
	}
	if s.failedChecks() > 0 {
		t.Errorf("%d check(s) failed:\n%s", s.failedChecks(), out.String())
	}
	log := strings.Join(fake.CallKeys(), "\n")
	b1 := filepath.Join(s.roots.ServerImagesDir(), fixtureImage)
	b2 := filepath.Join(s.roots.ServerImagesDir(), fixtureImage2)
	spawn := strings.Index(log, "proc.Spawn(")
	run1 := strings.Index(log, "instances.Run(api-"+fixturePrimary+","+b1)
	run2 := strings.Index(log, "instances.Run(api-"+fixturePrimary+","+b2)
	if spawn < 0 || run1 < spawn || run2 < run1 {
		t.Errorf("want the worktree API spawned, then api-%s from b1, then from b2 (spawn %d, b1 %d, b2 %d):\n%s",
			fixturePrimary, spawn, run1, run2, log)
	}
	if !strings.Contains(log, "instances.Run(api-"+fixtureRestored+","+b2) {
		t.Errorf("the restored twin did not run from b2:\n%s", log)
	}
	passed := map[string]bool{}
	for _, c := range s.checks {
		if c.Verdict == checkPass {
			passed[c.Name] = true
		}
	}
	for _, want := range []string{
		"update-code worktree → " + fixtureImage + ": /v1/version",
		"update-code " + fixtureImage + " → " + fixtureImage2 + ": registry",
		"update-code " + fixtureImage + " → " + fixtureImage2 + ": API instance",
		"update-code " + fixtureImage + " → " + fixtureImage2 + ": /v1/version",
	} {
		if !passed[want] {
			t.Errorf("no PASS for %q:\n%s", want, out.String())
		}
	}
}

// --update-code without the instance supervisor is a usage error.
func TestSelftestUpdateCodeNeedsTheInstanceSupervisor(t *testing.T) {
	if rc, _, _ := capture(t, "selftest", "--update-code", fixtureImage, "--supervisor", "systemd"); rc != exitUsage {
		t.Errorf("rc %d, want %d", rc, exitUsage)
	}
}
