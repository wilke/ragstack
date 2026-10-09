package ops

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

const (
	testImg       = "ragstack-server-v1.6.6-b1.sif"
	testImgSrc    = "/scout/containers/build/" + testImg
	testImgStore  = "/rag/data/ctl/images/server"
	testImgDst    = testImgStore + "/" + testImg
	testImgCommit = "a730cc9a730cc9a730cc9a730cc9a730cc9a730c"
)

var testImgBytes = []byte("pretend this is 250 MB of squashfs")

func testImgSHA() string {
	s := sha256.Sum256(testImgBytes)
	return hex.EncodeToString(s[:])
}

func testReceipt(mut func(map[string]any)) []byte {
	r := map[string]any{
		"name": testImg, "version": "v1.6.6", "commit": testImgCommit, "build": "1",
		"build_date": "2026-10-09T00:00:00Z", "sha256": testImgSHA(),
	}
	if mut != nil {
		mut(r)
	}
	var b strings.Builder
	b.WriteString("{")
	first := true
	for _, k := range []string{"name", "version", "commit", "build", "build_date", "sha256"} {
		v, ok := r[k]
		if !ok {
			continue
		}
		if !first {
			b.WriteString(",")
		}
		first = false
		switch x := v.(type) {
		case string:
			fmt.Fprintf(&b, "%q:%q", k, x)
		default:
			fmt.Fprintf(&b, "%q:%v", k, x)
		}
	}
	b.WriteString("}")
	return []byte(b.String())
}

func serverLabels() map[string]string {
	return map[string]string{
		"org.ragstack.version": "v1.6.6", "org.ragstack.commit": testImgCommit,
		"org.ragstack.build": "1", "org.ragstack.role": "server", "org.ragstack.build-date": "2026-10-09T00:00:00Z",
	}
}

// imageFixture is a fleet-scoped context with a built image and its receipt
// on the fake host, and the image's labels.
func imageFixture(t *testing.T) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := fixture(t, "dev", nil)
	oc.Tenant = nil
	fake.FakeFiles().Put(testImgSrc, testImgBytes, 0o755)
	fake.FakeFiles().Put(testImgSrc+ReceiptSuffix, testReceipt(nil), 0o644)
	fake.FakeInstances().SetLabels(testImgDst, serverLabels())
	return oc, fake
}

// runUntilError runs the steps in order and returns the first error.
func runUntilError(r *runner, p *jobs.Planned) (int, error) {
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			return s.Plan.N, err
		}
	}
	return 0, nil
}

func TestImagePrepareVerifiesCopiesAndRecords(t *testing.T) {
	oc, fake := imageFixture(t)
	p := plan(t, oc, "image-prepare", map[string]any{"sif": testImgSrc})
	// A plan is pure: no host was read to make it.
	if calls := fake.CallKeys(); len(calls) != 0 {
		t.Fatalf("planning read the host: %v", calls)
	}
	r := newRunner(oc, fake)
	r.runAll(t, p)

	rec := oc.Fleet.ServerImages[testImg]
	if rec == nil {
		t.Fatalf("no server image recorded; have %v", oc.Fleet.ServerImages)
	}
	want := registry.ServerImageRecord{Version: "v1.6.6", Commit: testImgCommit, Build: 1, SHA256: testImgSHA(),
		Path: testImgDst, PreparedAt: rec.PreparedAt, PreparedBy: "local:3581"}
	if *rec != want || rec.PreparedAt == "" {
		t.Errorf("record = %+v, want %+v", *rec, want)
	}
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("the registry the prepare wrote does not match the contract: %v", err)
	}
	files := fake.FakeFiles()
	for _, path := range []string{testImgDst, testImgDst + ReceiptSuffix} {
		f, ok := files.Files[path]
		if !ok {
			t.Fatalf("%s was not copied into the store", path)
		}
		if f.Mode != 0o640 {
			t.Errorf("%s mode %04o, want 0640 (group-readable, never group-writable)", path, f.Mode)
		}
	}
	if string(files.Files[testImgDst].Data) != string(testImgBytes) {
		t.Error("the stored image is not the source's bytes")
	}
	// The destination was checkpointed BEFORE the copy that created it, and
	// every witness was asked.
	calls := strings.Join(fake.CallKeys(), "\n")
	cp := strings.Index(calls, "job.checkpoint(file:"+testImgDst)
	cpy := strings.Index(calls, "files.CopyFile("+testImgSrc+","+testImgDst)
	if cp < 0 || cpy < 0 || cp > cpy {
		t.Errorf("checkpoint at %d, copy at %d; the path must be recorded first:\n%s", cp, cpy, calls)
	}
	for _, want := range []string{"files.Sha256(" + testImgSrc + ")", "instances.Labels(" + testImgDst + ")",
		"git.ResolveRef(" + testImgCommit + "," + testMirror + ")"} {
		if !strings.Contains(calls, want) {
			t.Errorf("%s was never called:\n%s", want, calls)
		}
	}
	res := p.Result()
	if res["name"] != testImg || res["path"] != testImgDst || res["commit"] != testImgCommit || res["build"] != 1 {
		t.Errorf("result = %v", res)
	}

	// A prepared name is immutable: preparing it again is refused at plan time.
	op, _ := NewRegistry(testDeps(oc)).Lookup("image-prepare")
	if _, err := op.Plan(context.Background(), oc, map[string]any{"sif": testImgSrc}); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "already prepared") {
		t.Errorf("re-preparing = %v, want a refusal", err)
	}
}

func TestImagePrepareRefusesAnyDisagreement(t *testing.T) {
	for name, c := range map[string]struct {
		setup func(*drivers.Fake)
		step  int
		want  string
	}{
		"no receipt": {func(f *drivers.Fake) {
			delete(f.FakeFiles().Files, testImgSrc+ReceiptSuffix)
		}, 1, "no receipt"},
		"a receipt for another file": {func(f *drivers.Fake) {
			f.FakeFiles().Put(testImgSrc+ReceiptSuffix, testReceipt(func(r map[string]any) {
				r["name"] = "ragstack-server-v1.6.6-b2.sif"
			}), 0o644)
		}, 1, "image file is"},
		"a build the name disagrees with": {func(f *drivers.Fake) {
			f.FakeFiles().Put(testImgSrc+ReceiptSuffix, testReceipt(func(r map[string]any) { r["build"] = 2 }), 0o644)
		}, 1, "name says b1"},
		"an abbreviated commit": {func(f *drivers.Fake) {
			f.FakeFiles().Put(testImgSrc+ReceiptSuffix, testReceipt(func(r map[string]any) { r["commit"] = "a730cc9" }), 0o644)
		}, 1, "full 40-hex"},
		"a receipt that is not JSON": {func(f *drivers.Fake) {
			f.FakeFiles().Put(testImgSrc+ReceiptSuffix, []byte("name=x"), 0o644)
		}, 1, "not the document"},
		"bytes the receipt does not describe": {func(f *drivers.Fake) {
			f.FakeFiles().Put(testImgSrc, []byte("rebuilt since"), 0o755)
		}, 2, "not the image the receipt describes"},
		"a label that disagrees": {func(f *drivers.Fake) {
			l := serverLabels()
			l["org.ragstack.commit"] = strings.Repeat("f", 40)
			f.FakeInstances().SetLabels(testImgDst, l)
		}, 5, "org.ragstack.commit"},
		"a TOOLS image": {func(f *drivers.Fake) {
			l := serverLabels()
			l["org.ragstack.role"] = "worker"
			f.FakeInstances().SetLabels(testImgDst, l)
		}, 5, `org.ragstack.role is "worker"`},
		"no labels at all": {func(f *drivers.Fake) {
			f.FakeInstances().SetLabels(testImgDst, map[string]string{})
		}, 5, "org.ragstack.version is missing"},
		"a commit that is not in the mirror": {func(f *drivers.Fake) {
			f.Fail("git.ResolveRef", fmt.Errorf("%w: unknown revision", jobs.ErrRefused))
		}, 3, "must be pushed to the mirror"},
		"a different image already in the store under the name": {func(f *drivers.Fake) {
			f.FakeFiles().Put(testImgDst, []byte("somebody else's bytes"), 0o640)
		}, 4, "never overwrites"},
	} {
		t.Run(name, func(t *testing.T) {
			oc, fake := imageFixture(t)
			c.setup(fake)
			p := plan(t, oc, "image-prepare", map[string]any{"sif": testImgSrc})
			n, err := runUntilError(newRunner(oc, fake), p)
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("step %d: %v; want a refusal containing %q", n, err, c.want)
			}
			if n != c.step {
				t.Errorf("refused at step %d, want step %d", n, c.step)
			}
			if len(oc.Fleet.ServerImages) != 0 {
				t.Errorf("a refused prepare recorded %v", oc.Fleet.ServerImages)
			}
		})
	}
}

// The registry already names the image with different bytes: refused at the
// receipt, before anything is hashed or copied.
func TestImagePrepareRefusesANameRecordedWithOtherBytes(t *testing.T) {
	oc, fake := imageFixture(t)
	p := plan(t, oc, "image-prepare", map[string]any{"sif": testImgSrc})
	// Recorded AFTER the plan (another prepare won the race to the lock).
	oc.Fleet.ServerImages = map[string]*registry.ServerImageRecord{testImg: {Version: "v1.6.6", Commit: testImgCommit,
		Build: 1, SHA256: strings.Repeat("0", 64), Path: testImgDst, PreparedAt: "x", PreparedBy: "y"}}
	n, err := runUntilError(newRunner(oc, fake), p)
	if n != 1 || !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "never re-pointed") {
		t.Fatalf("step %d: %v", n, err)
	}
}

// The same bytes already in the store (a re-run after a crash between the
// copy and the registry) are left alone and recorded.
func TestImagePrepareAdoptsTheSameBytesAlreadyInTheStore(t *testing.T) {
	oc, fake := imageFixture(t)
	fake.FakeFiles().Put(testImgDst, testImgBytes, 0o640)
	p := plan(t, oc, "image-prepare", map[string]any{"sif": testImgSrc})
	r := newRunner(oc, fake)
	r.runAll(t, p)
	if fake.Count("files.CopyFile") != 1 { // the receipt only
		t.Errorf("CopyFile called %d times; the image must not be copied over itself", fake.Count("files.CopyFile"))
	}
	if oc.Fleet.ServerImages[testImg] == nil {
		t.Error("not recorded")
	}
	// Nothing was checkpointed, so a rollback removes nothing it did not make.
	copyStep := p.Steps[3]
	if out, err := r.rollback(copyStep); err != nil || !strings.Contains(out, "nothing was copied") {
		t.Errorf("rollback = %q, %v", out, err)
	}
	if _, ok := fake.FakeFiles().Files[testImgDst]; !ok {
		t.Error("the rollback removed an image this job did not copy")
	}
}

func TestImagePrepareRollsBack(t *testing.T) {
	oc, fake := imageFixture(t)
	p := plan(t, oc, "image-prepare", map[string]any{"sif": testImgSrc})
	r := newRunner(oc, fake)
	r.runAll(t, p)
	// Reverse order, as the engine does.
	for i := len(p.Steps) - 1; i >= 0; i-- {
		if p.Steps[i].Rollback == nil {
			continue
		}
		if _, err := r.rollback(p.Steps[i]); err != nil {
			t.Fatalf("rollback of step %d: %v", p.Steps[i].Plan.N, err)
		}
	}
	if oc.Fleet.ServerImages != nil {
		t.Errorf("server_images = %v after rollback; want absent (an empty map would still be the one-way door)",
			oc.Fleet.ServerImages)
	}
	for _, path := range []string{testImgDst, testImgDst + ReceiptSuffix} {
		if _, ok := fake.FakeFiles().Files[path]; ok {
			t.Errorf("%s is still in the store after rollback", path)
		}
	}
	if _, ok := fake.FakeFiles().Files[testImgSrc]; !ok {
		t.Error("the rollback touched the operator's source image")
	}
}

func TestImagePreparePlanRefusals(t *testing.T) {
	oc, _ := imageFixture(t)
	op, _ := NewRegistry(testDeps(oc)).Lookup("image-prepare")
	for name, c := range map[string]struct {
		args map[string]any
		want string
	}{
		"not a server image name": {map[string]any{"sif": "/scout/containers/ragstack/ragstack-tools-v1.6.5-b1.sif"}, "not a server image"},
		"a dotdot path":           {map[string]any{"sif": "/scout/../etc/" + testImg}, "clean"},
	} {
		if _, err := op.Plan(context.Background(), oc, c.args); !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: %v, want a refusal containing %q", name, err, c.want)
		}
	}
	noMirror, _ := NewRegistry(Deps{Roots: oc.Roots, Now: oc.Now}).Lookup("image-prepare")
	if _, err := noMirror.Plan(context.Background(), oc, map[string]any{"sif": testImgSrc}); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "mirror") {
		t.Errorf("no mirror: %v", err)
	}
	if _, err := op.Plan(context.Background(), oc, map[string]any{}); !errors.Is(err, jobs.ErrValidation) {
		t.Errorf("no sif: %v, want a validation error", err)
	}
}

func TestParseImageReceiptAcceptsAStringOrANumberBuild(t *testing.T) {
	for _, b := range []any{"1", 1} {
		r, err := ParseImageReceipt(testReceipt(func(m map[string]any) { m["build"] = b }), testImg)
		if err != nil || r.Build != 1 {
			t.Errorf("build %#v: %+v, %v", b, r, err)
		}
	}
	for _, bad := range []any{"one", "+1", "-1", "1.0", "", 1.5} {
		if _, err := ParseImageReceipt(testReceipt(func(m map[string]any) { m["build"] = bad }), testImg); err == nil {
			t.Errorf("build %#v was accepted", bad)
		}
	}
}

// A `+sha` dev build is a legal image name, but `+` is outside the charset of
// every path the ctl writes or binds: the source (only read) may carry it, and
// the store spells it `-plus-`. A second name that would land on the same
// stored file is refused at plan time.
func TestImagePrepareStoresAPlusShaDevBuild(t *testing.T) {
	const name = "ragstack-server-v1.6.6+c1e7d46-b1.sif"
	const src = "/scout/containers/build/" + name
	const dst = testImgStore + "/ragstack-server-v1.6.6-plus-c1e7d46-b1.sif"
	oc, fake := fixture(t, "dev", nil)
	oc.Tenant = nil
	fake.FakeFiles().Put(src, testImgBytes, 0o755)
	fake.FakeFiles().Put(src+ReceiptSuffix, testReceipt(func(r map[string]any) {
		r["name"], r["version"] = name, "v1.6.6+c1e7d46"
	}), 0o644)
	l := serverLabels()
	l["org.ragstack.version"] = "v1.6.6+c1e7d46"
	fake.FakeInstances().SetLabels(dst, l)

	p := plan(t, oc, "image-prepare", map[string]any{"sif": src})
	newRunner(oc, fake).runAll(t, p)
	rec := oc.Fleet.ServerImages[name]
	if rec == nil || rec.Path != dst || rec.Version != "v1.6.6+c1e7d46" {
		t.Fatalf("record = %+v", rec)
	}
	if _, ok := fake.FakeFiles().Files[dst]; !ok {
		t.Errorf("not stored as %s", dst)
	}
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("contract: %v", err)
	}

	op, _ := NewRegistry(testDeps(oc)).Lookup("image-prepare")
	_, err := op.Plan(context.Background(), oc, map[string]any{"sif": "/x/ragstack-server-v1.6.6-plus-c1e7d46-b1.sif"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "already the file of") {
		t.Errorf("a colliding name: %v", err)
	}
}
