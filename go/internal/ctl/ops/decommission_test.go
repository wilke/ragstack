package ops

// PR-G1.3: `decommission --archive` (the default) takes a fenced, checked
// bundle with the tenant's secrets sealed, keeps the API down, and only then
// quarantines; `--archive=false` is the decommission that existed before. The
// row records the quarantine either way.

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// noArchive is the decommission that existed before PR-G1.3.
var noArchive = map[string]any{"archive": false}

// testRecipient is the fake sealer's one recipient fingerprint.
var testRecipient = fakeSealer{fps: []string{"sha256:0123456789abcdef"}}

// decomBackedUp is a managed row with a fenced, verified bundle — what
// `--archive=false` requires.
func decomBackedUp(tn *registry.Tenant) {
	managed(tn)
	tn.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/dev/20260914T080000Z-backup",
		At: "2026-09-14T08:00:00Z", Kind: "backup", Fenced: true, Verified: true, Scope: fullScope}
}

// archiveDeps are testDeps with a recipient configured.
func archiveDeps(oc jobs.Context) Deps {
	d := testDeps(oc)
	d.Sealer = testRecipient
	return d
}

func planDecom(t *testing.T, oc jobs.Context, d Deps, args map[string]any) *jobs.Planned {
	t.Helper()
	op, _ := NewRegistry(d).Lookup("decommission")
	p, err := op.Plan(context.Background(), oc, args)
	if err != nil {
		t.Fatalf("decommission %v: %v", args, err)
	}
	return p
}

// The plan without an archive is, step for step, the plan of the code before
// PR-G1.3 (titles recorded from be87096).
func TestDecommissionWithoutArchivePlansTodaysSteps(t *testing.T) {
	oc, _ := fixture(t, "dev", decomBackedUp)
	want := []string{
		"systemd: skip ui",
		"systemd: stop ragstack-dev-api.service",
		"systemd: disable ragstack-dev-api.service (desired_boot)",
		"systemd: skip postgres",
		"systemd: stop ragstack-dev-es.service",
		"systemd: disable ragstack-dev-es.service (desired_boot)",
		"systemd: stop ragstack-dev-qdrant.service",
		"systemd: disable ragstack-dev-qdrant.service (desired_boot)",
		"fs: remove the rendered unit files",
		"systemd: systemctl --user daemon-reload",
		"registry: mark dev quarantined (the row keeps its port block)",
		"nginx: publish a generation without dev",
		"fs: quarantine the data directory (rename to .quarantined-<ts>)",
		"fs: write RECOVERY.json into the quarantined directory",
		"git: remove the tenant's git worktree",
	}
	// A recipient changes nothing when the archive is off.
	for name, d := range map[string]Deps{"no recipient": testDeps(oc), "a recipient": archiveDeps(oc)} {
		if got := titles(planDecom(t, oc, d, noArchive)); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: --archive=false plans\n%s\nwant\n%s", name, strings.Join(got, "\n"), strings.Join(want, "\n"))
		}
	}

	ocI, _ := instanceFixture(t)
	ocI.Tenant.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/dev/20260914T090000Z-backup",
		Fenced: true, Verified: true, At: "2026-09-14T09:00:00Z", Scope: fullScope}
	d := testDeps(ocI)
	d.Owner = "svcbvbrc"
	wantI := []string{
		"instance: skip ui",
		"proc: stop the API through its pidfile (TERM, then KILL)",
		"instance: disable api (desired_boot)",
		"instance: stop the instance postgres-dev",
		"instance: disable postgres (desired_boot)",
		"instance: stop the instance elasticsearch-dev",
		"instance: disable es (desired_boot)",
		"instance: stop the instance qdrant-dev",
		"instance: disable qdrant (desired_boot)",
		"instance: skip removing the unit files",
		"registry: mark dev quarantined (the row keeps its port block)",
		"nginx: publish a generation without dev",
		"fs: quarantine the data directory (rename to .quarantined-<ts>)",
		"fs: write RECOVERY.json into the quarantined directory",
		"git: remove the tenant's git worktree",
	}
	if got := titles(planDecom(t, ocI, d, noArchive)); !reflect.DeepEqual(got, wantI) {
		t.Errorf("instance --archive=false plans\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(wantI, "\n"))
	}
	// And it still needs the bundle it always needed.
	ocNone, _ := fixture(t, "dev", managed)
	if err := planErr(t, ocNone, "decommission", noArchive); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "has no backup") {
		t.Errorf("--archive=false without a bundle = %v", err)
	}
}

// The archive is the default, and it refuses — at plan time, before any fence
// — when nobody could open the sealed secrets.
func TestDecommissionArchiveRefusesWithoutARecipient(t *testing.T) {
	oc, _ := fixture(t, "dev", decomBackedUp)
	for name, args := range map[string]map[string]any{"default": nil, "explicit": {"archive": true}} {
		for dname, d := range map[string]Deps{"no sealer": testDeps(oc), "an empty sealer": func() Deps {
			d := testDeps(oc)
			d.Sealer = fakeSealer{}
			return d
		}()} {
			op, _ := NewRegistry(d).Lookup("decommission")
			_, err := op.Plan(context.Background(), oc, args)
			if !errors.Is(err, jobs.ErrRefused) {
				t.Fatalf("%s/%s: %v, want ErrRefused", name, dname, err)
			}
			for _, want := range []string{"fleet backup-identity init", "secrets=require", "backup-recipients.txt",
				"--archive=false"} {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("%s/%s: the refusal does not say %q: %v", name, dname, want, err)
				}
			}
		}
	}
	// The ownership refusal still comes first: a tenant the ctl does not run
	// is refused for THAT reason, recipient or not.
	ocW, _ := fixture(t, "dev", nil)
	op, _ := NewRegistry(archiveDeps(ocW)).Lookup("decommission")
	if _, err := op.Plan(context.Background(), ocW, nil); err == nil ||
		!strings.Contains(err.Error(), "quarantines only what the ctl runs") {
		t.Errorf("a wilke-owned tenant with archive = %v", err)
	}
}

// The archive plan: fence stop → fence verify → the bundle legs → the deep
// check → the record → the quarantine, with NO API start anywhere after the
// fence. No prior bundle is needed: the job takes its own.
func TestDecommissionArchiveStepOrder(t *testing.T) {
	oc, _ := fixture(t, "dev", managed) // no last_backup at all
	p := planDecom(t, oc, archiveDeps(oc), nil)
	ts := titles(p)

	order := []struct{ kind, substr string }{
		{"probe", "check the backup filesystem has room"},
		{"systemd", "stop ragstack-dev-api.service"},
		{"probe", "fence verify"},
		{"fs", "create the bundle directory"},
		{"fs", "seal the secret files into secrets.age"},
		{"fs", "write SHA256SUMS and the bundle manifest"},
		{"fs", "rename the bundle into place"},
		{"backup", "check the bundle"},
		{"registry", "record the bundle as this tenant's last backup"},
		{"systemd", "disable ragstack-dev-api.service"},
		{"fs", "remove the rendered unit files"},
		{"registry", "mark dev quarantined"},
		{"nginx", "publish a generation without dev"},
		{"fs", "quarantine the data directory"},
		{"fs", "write RECOVERY.json"},
		{"git", "remove the tenant's git worktree"},
	}
	last := -1
	for _, o := range order {
		i := -1
		for j := last + 1; j < len(p.Steps); j++ {
			if p.Steps[j].Plan.Kind == o.kind && strings.Contains(p.Steps[j].Plan.Title, o.substr) {
				i = j
				break
			}
		}
		if i < 0 {
			t.Fatalf("no %s %q after step %d:\n%s", o.kind, o.substr, last+1, strings.Join(ts, "\n"))
		}
		last = i
	}
	// Nothing starts anything: the API stays down from the fence to the end.
	for _, s := range p.Steps {
		title := s.Plan.Title
		if strings.HasPrefix(title, "start ") || strings.Contains(title, "wait for") ||
			strings.Contains(title, "ready") || strings.Contains(title, "start the API") {
			t.Errorf("the archive decommission starts something: %q\n%s", title, strings.Join(ts, "\n"))
		}
	}
	// The bundle is required to carry the secrets: no skip step for them.
	if hasStep(p, "fs", "skip the encrypted secrets payload") {
		t.Errorf("an archive plans the secrets skip:\n%s", strings.Join(ts, "\n"))
	}
	// The locks are the union of backup's and decommission's.
	want := map[model.LockName]bool{model.LockRegistry: true, model.LockManifest: true, model.LockTenant: true,
		model.LockGateway: true}
	got := map[model.LockName]bool{}
	for _, l := range p.Locks {
		got[l] = true
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("locks = %v, want %v", p.Locks, want)
	}
	// The plan is a contract document: no `<ts>` in any would_write path.
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if strings.ContainsAny(w.Path, "<>") {
				t.Errorf("step %q plans a would_write path plan.json refuses: %s", s.Plan.Title, w.Path)
			}
		}
	}
}

// runDecom runs a planned decommission through the unit runner and returns the
// runner. The fixture host has the tenant's state seeded so the bundle's
// sqlite leg copies.
func runDecom(t *testing.T, oc jobs.Context, fake *drivers.Fake, p *jobs.Planned) *runner {
	t.Helper()
	r := newRunner(oc, fake)
	r.runAll(t, p)
	return r
}

// The archive decommission end to end on the fake host: a bundle that is
// fenced, checked and carries the sealed secrets; a row that says
// quarantined; and a `quarantine` block naming the renamed tree, the job and
// THIS bundle.
func TestDecommissionArchiveRunsAndRecordsTheQuarantine(t *testing.T) {
	oc, fake := fixture(t, "dev", decomBackedUp)
	seedState(fake, "dev")
	p := planDecom(t, oc, archiveDeps(oc), nil)
	r := runDecom(t, oc, fake, p)

	row := oc.Fleet.Tenants["dev"]
	if row.State != registry.StateQuarantined || row.DesiredBoot != "disabled" {
		t.Fatalf("row state = %s/%s", row.State, row.DesiredBoot)
	}
	bundle := "/rag/backups/tenants/dev/20260914T093000Z-backup"
	if lb := row.LastBackup; lb == nil || lb.Bundle != bundle || !lb.Fenced || !lb.Checked {
		t.Fatalf("last_backup = %+v, want the archive, fenced and checked", row.LastBackup)
	}
	q := row.Quarantine
	if q == nil {
		t.Fatal("no quarantine block on the row")
	}
	wantDir := "/rag/data/tenants/dev.quarantined-20260914T093000Z"
	if q.Dir != wantDir || q.At != "2026-09-14T09:30:00Z" || q.JobID != r.job.ID || string(q.Bundle) != bundle {
		t.Errorf("quarantine = %+v, want dir %s, the job %s and bundle %s", *q, wantDir, r.job.ID, bundle)
	}
	// The tree really is where the row says, with the note inside it.
	if fake.FakeFiles().Content(wantDir+"/config/tenant.env") == nil {
		t.Errorf("the tree is not at quarantine.dir; files = %v", fake.FakeFiles().Paths())
	}
	if fake.FakeFiles().Content(wantDir+"/"+recoveryFile) == nil {
		t.Errorf("no %s in the quarantined tree", recoveryFile)
	}
	// The archive carries the sealed secrets.
	if sealed := fake.FakeFiles().Content(bundle + "/secrets.age"); !strings.HasPrefix(string(sealed), "age-encrypted-to:") {
		t.Errorf("the archive has no sealed secrets.age")
	}
	// And the row as written satisfies the registry contract (testDeps'
	// SaveFleet validated every write; this is the end state).
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("the quarantined registry does not validate: %v", err)
	}
	// The API was never started again: the only API-unit calls are stops,
	// disables and resets.
	for _, c := range fake.Calls() {
		if c.Driver == "systemd" && c.Method == "Start" {
			t.Errorf("the archive decommission started a unit: %v", c)
		}
	}
	if got := p.Result()["archive_bundle"]; got != bundle {
		t.Errorf("result archive_bundle = %v", got)
	}
}

// Without an archive the block still lands, with the row's existing bundle.
func TestDecommissionWithoutArchiveRecordsTheQuarantine(t *testing.T) {
	oc, fake := fixture(t, "dev", decomBackedUp)
	p := plan(t, oc, "decommission", noArchive)
	r := runDecom(t, oc, fake, p)
	q := oc.Fleet.Tenants["dev"].Quarantine
	if q == nil {
		t.Fatal("no quarantine block")
	}
	if q.Dir != "/rag/data/tenants/dev.quarantined-20260914T093000Z" || q.JobID != r.job.ID ||
		string(q.Bundle) != "/rag/backups/tenants/dev/20260914T080000Z-backup" {
		t.Errorf("quarantine = %+v", *q)
	}
}

// A quarantine whose rename fails is rolled back the way the engine rolls a
// job back — newest first, every succeeded step plus the failed one when it
// checkpointed — and the row ends with NO quarantine block and its old state.
func TestAFailedQuarantineRenameLeavesNoQuarantineBlock(t *testing.T) {
	for name, archive := range map[string]bool{"archive": true, "no archive": false} {
		t.Run(name, func(t *testing.T) {
			oc, fake := fixture(t, "dev", decomBackedUp)
			seedState(fake, "dev")
			fake.Fail("files.Rename:/rag/data/tenants/dev", errors.New("EXDEV"))
			d := testDeps(oc)
			if archive {
				d.Sealer = testRecipient
			}
			before := *oc.Fleet.Tenants["dev"]
			p := planDecom(t, oc, d, map[string]any{"archive": archive})
			r := newRunner(oc, fake)
			failed := -1
			for i, s := range p.Steps {
				if _, err := r.run(s); err != nil {
					if !strings.Contains(s.Plan.Title, "quarantine the data directory") {
						t.Fatalf("step %d (%s) failed: %v", s.Plan.N, s.Plan.Title, err)
					}
					failed = i
					break
				}
			}
			if failed < 0 {
				t.Fatal("the rename did not fail")
			}
			if oc.Fleet.Tenants["dev"].Quarantine == nil {
				t.Fatal("the registry step did not write the block before the rename")
			}
			for i := failed; i >= 0; i-- {
				s := p.Steps[i]
				st := r.steps[s.Plan.N]
				if i == failed && (st == nil || len(st.ExternalIDs) == 0) {
					continue
				}
				if s.Rollback == nil {
					continue
				}
				if _, err := r.rollback(s); err != nil {
					t.Fatalf("rollback of step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
				}
			}
			row := oc.Fleet.Tenants["dev"]
			if row.Quarantine != nil {
				t.Errorf("the rolled-back row still records a quarantine: %+v", *row.Quarantine)
			}
			if row.State != before.State || row.DesiredBoot != before.DesiredBoot {
				t.Errorf("the rolled-back row is %s/%s, want %s/%s", row.State, row.DesiredBoot, before.State,
					before.DesiredBoot)
			}
			if _, ok := row.LastOps["decommission"]; ok {
				t.Errorf("the rolled-back row records a decommission: %+v", row.LastOps)
			}
			if fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env") == nil {
				t.Errorf("the tree moved")
			}
			if archive && row.LastBackup.Bundle != "/rag/backups/tenants/dev/20260914T080000Z-backup" {
				t.Errorf("last_backup after the rollback = %+v, want the bundle the row had before", row.LastBackup)
			}
		})
	}
}

// A stopped tenant cannot be archived (its stores are down); the plan says so
// from the row instead of failing after the fence. --archive=false is still
// open to it.
func TestDecommissionArchiveRefusesAStoppedTenant(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		decomBackedUp(tn)
		tn.State = "stopped"
	})
	op, _ := NewRegistry(archiveDeps(oc)).Lookup("decommission")
	_, err := op.Plan(context.Background(), oc, nil)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "is stopped") ||
		!strings.Contains(err.Error(), "--archive=false") {
		t.Fatalf("archive of a stopped tenant = %v", err)
	}
	planDecom(t, oc, archiveDeps(oc), noArchive)
}
