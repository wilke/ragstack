package drivers

// From the adversarial re-review of the handover fix, and KEPT: the take's new
// cluster did not inherit the ORIGINAL cluster's encoding or collation, and the
// row-count proof could not see the difference.
//
// The take swaps in an empty directory and lets the image's entrypoint run
// initdb. initdb had no --encoding/--locale — nothing set POSTGRES_INITDB_ARGS
// — so it took them from its environment. The image sets `LANG=en_US.utf8` and
// does not set LC_ALL, and `envKeep` in exec.go forwards BOTH from the ctl
// process into `apptainer instance run`. An LC_ALL in the environment of
// whatever ran the take therefore decided the encoding of a tenant's database.
//
// Measured on coconut with the deployed image:
//
//	SOURCE  : UTF8 / en_US.utf8      2 rows, max length(s) = 8
//	TARGET  : SQL_ASCII / C          (initdb with LC_ALL=C in the environment)
//	pg_restore --no-owner --role=hackathon  →  EXIT 0
//	TARGET t: 2 rows, max length(s) = 10
//
// Two rows in, two rows out: the row-count proof passed and the take reported
// success. `length()`, `upper()`, `LIKE`, `ORDER BY` and every index's
// collation were different, and `--commit` then invited the operator to delete
// the only correctly-encoded copy.
//
// Two things fix it, and both are pinned below: the instance driver stops
// letting the caller's locale reach the container at all, and the handover pins
// what it actually wants (ops/pgdata.go passes POSTGRES_INITDB_ARGS from what
// the release recorded, and refuses the restore when the new cluster came out
// any other way).

import (
	"os"
	"os/exec"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// TestAnInstanceRunDoesNotInheritTheCallersLocale is the first half, in the
// driver that runs every store.
//
// exec.go's envKeep still forwards LANG and LC_ALL — every other program the
// ctl runs wants them — so the instance driver overrides them on its own calls.
// For a qdrant or an elasticsearch that is cosmetic. For the postgres whose
// FIRST start initialises a cluster it is the on-disk encoding of a tenant's
// database, which is not a thing to inherit from whichever shell ran the job.
func TestAnInstanceRunDoesNotInheritTheCallersLocale(t *testing.T) {
	i := &RealInstances{Env: []string{"APPTAINER_CONFIGDIR=/rag/data/ctl/apptainer/config"}}
	for _, ns := range []jobs.InstanceNamespace{jobs.NamespaceCtl, jobs.NamespaceAccountDefault} {
		env := i.env(ns, []string{"APPTAINERENV_POSTGRES_PASSWORD=secret"})
		// os/exec keeps the LAST value of a repeated key, so what matters is
		// the last setting of each — and it has to be the empty one.
		last := map[string]string{}
		for _, e := range env {
			if k, v, ok := strings.Cut(e, "="); ok {
				last[k] = v
			}
		}
		for _, k := range []string{"LANG", "LC_ALL"} {
			v, set := last[k]
			if !set || v != "" {
				t.Errorf("namespace %q: %s reaches the container as %q — initdb reads it, and the encoding of a "+
					"tenant's database would then be decided by whoever ran the job", ns, k, v)
			}
		}
		// …and the call's own environment still arrives.
		if last["APPTAINERENV_POSTGRES_PASSWORD"] != "secret" {
			t.Errorf("namespace %q: the call's own environment was dropped: %v", ns, env)
		}
	}
}

// TestTheDeployedImagesInitdbHonoursAnExplicitEncoding is the second half, and
// a read-only check against the image that is actually deployed: initdb into
// t.TempDir(), nothing on this host written or started.
//
// It asserts the property the handover depends on — that `--encoding` and
// `--locale` BEAT the environment, so the cluster a take builds is the one the
// release described whatever ran the take — and, on the way, that the locale
// the live tenants' clusters use exists in the image at all.
func TestTheDeployedImagesInitdbHonoursAnExplicitEncoding(t *testing.T) {
	const sif = "/rag/apptainer/images/postgres.sif"
	if _, err := os.Stat(sif); err != nil {
		t.Skipf("no deployed postgres image at %s: %v", sif, err)
	}
	bin, err := exec.LookPath("apptainer")
	if err != nil {
		t.Skipf("apptainer is not on this host: %v", err)
	}

	// The locale the live hackathon cluster is collated with. An image without
	// it would make a handover of that tenant impossible, and the failure would
	// land on the take — after the release had already stopped the tenant.
	locales, err := exec.Command(bin, "exec", "--no-home", sif, "locale", "-a").Output()
	if err != nil {
		t.Skipf("the image would not list its locales: %v", err)
	}
	if !strings.Contains(string(locales), "en_US.utf8") {
		t.Errorf("the deployed image has no en_US.utf8, which is what the live tenants' clusters are collated "+
			"with; a handover of one could not rebuild it. locale -a:\n%s", locales)
	}

	// initdb returns the LOCALE AND ENCODING lines of initdb's own report.
	//
	// Not one line: initdb prints "The default database encoding has
	// accordingly been set to …" only when it DERIVES the encoding from the
	// locale, which is precisely what an explicit --encoding stops it doing.
	// The lines that survive both spellings are the ones naming the locale and
	// the encoding, whichever way round it reached them.
	initdb := func(env []string, args ...string) string {
		t.Helper()
		dir := t.TempDir()
		argv := append([]string{"exec", "--no-home", "--bind", dir + ":/pg", sif,
			"initdb", "-D", "/pg/d", "-U", "dev", "--auth=trust"}, args...)
		cmd := exec.Command(bin, argv...)
		cmd.Env = env
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Skipf("initdb would not run here: %v\n%s", err, out)
		}
		var keep []string
		for _, line := range strings.Split(string(out), "\n") {
			l := strings.TrimSpace(line)
			low := strings.ToLower(l)
			switch {
			case strings.Contains(low, "locale"), strings.Contains(low, "encoding"),
				strings.HasPrefix(l, "LC_"), strings.HasPrefix(l, "CTYPE"), strings.HasPrefix(l, "COLLATE"):
				keep = append(keep, l)
			}
		}
		return strings.Join(keep, " / ")
	}

	// The bug, unpinned: a BARE initdb under a hostile environment.
	bareC := initdb([]string{"PATH=/usr/bin:/bin", "LC_ALL=C"})

	// The fix: the same flags the handover passes, from that same environment
	// and from a clean one.
	pinned := initdb([]string{"PATH=/usr/bin:/bin", "LC_ALL=C"}, "--encoding=UTF8", "--locale=en_US.utf8")
	clean := initdb([]string{"PATH=/usr/bin:/bin"}, "--encoding=UTF8", "--locale=en_US.utf8")
	if pinned == "" || clean == "" {
		t.Fatalf("initdb did not report what it initialised: %q / %q", pinned, clean)
	}

	// The property the handover depends on: what the flags ask for wins, and
	// the environment does not get a vote.
	if pinned != clean {
		t.Errorf("--encoding/--locale do not beat the environment on this image:\n"+
			"  with LC_ALL=C : %s\n  without       : %s\n"+
			"The handover pins them from what the release recorded; if the environment can still win, the cluster "+
			"a take builds is not the one the release described.", pinned, clean)
	}
	// …and they CHANGED something: the same environment without them produces
	// a different cluster, which is the failure this whole pinning exists for.
	if pinned == bareC {
		t.Errorf("--encoding/--locale made no difference under LC_ALL=C (%q); this test would pass against an "+
			"image that ignored them", pinned)
	}
	if !strings.Contains(pinned, "en_US.utf8") {
		t.Errorf("initdb --locale=en_US.utf8 did not report it: %q", pinned)
	}
	// initdb echoes the ENCODING only when it DERIVES it from the locale, so
	// an explicit --encoding is proved by the two comparisons above rather than
	// by its own words. That it accepts the flag at all is proved by the run
	// having succeeded: initdb rejects an encoding the locale cannot support.
	t.Logf("initdb --encoding=UTF8 --locale=en_US.utf8: %s\n  (bare, LC_ALL=C: %s)", pinned, bareC)
}
