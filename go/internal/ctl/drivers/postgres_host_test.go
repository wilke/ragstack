package drivers

// A read-only check against THIS host's postgres image.
//
// The handover's postgres migration is a dump and a restore into a cluster the
// taking account initialises, and all three programs live in the tenant's own
// image: `pg_dump` (the release), `pg_restore` (the take) and `initdb` (run by
// the image's entrypoint when it finds an empty PGDATA). Every other test in
// this package asks a stub apptainer; this one asks the image that is actually
// deployed, because "the tools are there" is a fact about coconut and not about
// the code.
//
// It runs nothing that writes: three `--version` calls.

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

func TestTheDeployedPostgresImageCarriesTheToolsAHandoverNeeds(t *testing.T) {
	const sif = "/rag/apptainer/images/postgres.sif"
	if _, err := os.Stat(sif); err != nil {
		t.Skipf("no deployed postgres image at %s: %v", sif, err)
	}
	bin, err := exec.LookPath("apptainer")
	if err != nil {
		t.Skipf("apptainer is not on this host: %v", err)
	}
	run := t.TempDir() // bound at /var/run/postgresql; nothing connects through it

	d := NewReal(RealOptions{ApprovedRoots: []string{run}, Apptainer: bin})
	got, err := d.Postgres().ToolVersions(context.Background(),
		jobs.PostgresSpec{SIF: sif, RunDir: run, DB: "dev", User: "dev"})
	if err != nil {
		t.Fatalf("the deployed image cannot run a handover's postgres tools: %v", err)
	}
	for _, tool := range handoverTools {
		v := got[tool]
		if !strings.Contains(v, "PostgreSQL") {
			t.Errorf("%s --version = %q, want a PostgreSQL version", tool, v)
		}
	}
	// The three have to be the SAME major version: a pg_dump older than the
	// server refuses outright, and one newer produces an archive the server
	// cannot read back. They come from one image precisely so that this holds.
	major := func(v string) string {
		f := strings.Fields(v)
		if len(f) < 3 {
			return v
		}
		return strings.SplitN(f[2], ".", 2)[0]
	}
	want := major(got["pg_dump"])
	for _, tool := range handoverTools {
		if m := major(got[tool]); m != want {
			t.Errorf("%s is major %s and pg_dump is %s: one image must carry one version", tool, m, want)
		}
	}
	t.Logf("%s: %s", sif, got)
}
