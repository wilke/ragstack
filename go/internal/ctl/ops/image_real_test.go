package ops

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The real server image F1 built on coconut (read-only here), and the mirror
// its commit is in. The test runs only where both exist and apptainer is on
// PATH; it copies ~250 MB into a temporary root, so -short skips it.
const (
	realServerImage = "/rag/data/ctl-selftest/server-image/ragstack-server-v1.6.6-b1.sif"
	realMirror      = "/rag/repos/ragstack.git"
)

// TestImagePrepareOnTheRealHost runs every step of `fleet image prepare`
// through the REAL drivers — the streaming sha256, `apptainer inspect
// --labels`, `git rev-parse` in the bare mirror and the atomic copy — against
// the real image, into a throwaway deployment root. It does not go through
// the engine (whose doctor gate a temporary root cannot pass: no 200 GiB
// reserve, no service-account ACL), so it is the drivers and the planner that
// are proven here, and the gate is proven by the doctor tests.
func TestImagePrepareOnTheRealHost(t *testing.T) {
	if testing.Short() {
		t.Skip("-short: copies a 250 MB image")
	}
	if _, err := os.Stat(realServerImage); err != nil {
		t.Skip("no real server image on this host")
	}
	if _, err := os.Stat(realMirror); err != nil {
		t.Skip("no mirror on this host")
	}
	bin, err := exec.LookPath("apptainer")
	if err != nil {
		t.Skip("no apptainer")
	}
	root := t.TempDir()
	roots := paths.NewRoots(root, paths.Overrides{})
	for _, d := range []string{roots.DataDir, roots.CtlStateDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	real := drivers.NewReal(drivers.RealOptions{Roots: roots, Apptainer: bin, Mirror: realMirror})
	f := registry.NewFleet(root)
	f.UpdatedBy = "test"
	reg := roots.Registry()
	oc := jobs.Context{Roots: roots, Fleet: f, Drivers: real, Now: time.Now,
		Doctor: model.DoctorResponse{Status: model.StatusGreen}}
	deps := Deps{Roots: roots, Now: time.Now, Mirror: realMirror,
		SaveFleet: func(fl *registry.Fleet) error { return registry.Save(reg, fl, "test") }}
	op, _ := NewRegistry(deps).Lookup("image-prepare")
	p, err := op.Plan(context.Background(), oc, map[string]any{"sif": realServerImage})
	if err != nil {
		t.Fatal(err)
	}
	r := &runner{oc: oc, fake: drivers.NewFake(drivers.FakeOptions{}), steps: map[int]*model.Step{},
		job: &model.Job{ID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", Principal: "local:test"}}
	r.runAll(t, p)

	back, err := registry.LoadNoRepair(reg)
	if err != nil {
		t.Fatalf("the registry the prepare wrote does not load: %v", err)
	}
	name := filepath.Base(realServerImage)
	rec := back.ServerImages[name]
	if rec == nil || rec.Version != "v1.6.6" || rec.Build != 1 || len(rec.Commit) != 40 ||
		rec.Path != filepath.Join(roots.ServerImagesDir(), name) {
		t.Fatalf("record = %+v", rec)
	}
	st, err := os.Stat(rec.Path)
	if err != nil || st.Mode().Perm() != 0o640 {
		t.Fatalf("stored image: %v, mode %v", err, st)
	}
	if _, err := os.Stat(rec.Path + ReceiptSuffix); err != nil {
		t.Errorf("the receipt was not stored: %v", err)
	}
	t.Logf("prepared %s: commit %s sha256 %s", name, rec.Commit, rec.SHA256)

	// The dev build of a branch commit that was never pushed is refused at the
	// mirror step, by the message the brief asks for — when that image exists
	// and its commit really is absent from the mirror.
	dev := filepath.Join(filepath.Dir(realServerImage), "ragstack-server-v1.6.6+c1e7d46-b1.sif")
	if _, err := os.Stat(dev); err == nil {
		p2, err := op.Plan(context.Background(), oc, map[string]any{"sif": dev})
		if err != nil {
			t.Fatal(err)
		}
		r2 := &runner{oc: oc, fake: r.fake, steps: map[int]*model.Step{}, job: r.job}
		n, err := runUntilError(r2, p2)
		switch {
		case err == nil:
			t.Logf("%s's commit IS in the mirror; prepared it too", filepath.Base(dev))
		case n == 3 && strings.Contains(err.Error(), "must be pushed to the mirror"):
			t.Logf("refused as expected: %v", err)
		default:
			t.Errorf("step %d: %v", n, err)
		}
	}
}
