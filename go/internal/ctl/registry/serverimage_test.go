package registry

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// goldenPrePRF is a registry written by the binary BEFORE the server-image
// fields existed (PR-F F3), generated from LiveFixture plus an artifact and a
// pinned code sha. It is the document every coconut registry looks like on the
// day this ships.
const goldenPrePRF = "testdata/registry-pre-prf-f3.json"

const (
	testImageName = "ragstack-server-v1.6.6-b1.sif"
	testImagePath = "/rag/data/ctl/images/server/" + testImageName
	testImageSHA  = "3fe461f9e64cbe6d6dd229ced558704eb399dcc51639ccf1976c3fa5f004e7fb"
	testCommit    = "a730cc9a730cc9a730cc9a730cc9a730cc9a730c"
)

// withServerImages is the golden fleet carrying every new member: a prepared
// image in the fleet, a tenant in image mode and a previous image in its code.
func withServerImages(t *testing.T) *Fleet {
	t.Helper()
	f, err := LoadNoRepair(goldenPrePRF)
	if err != nil {
		t.Fatal(err)
	}
	f.Generation = 0 // a fresh file: Save refuses a generation the target never had
	f.ServerImages = map[string]*ServerImageRecord{
		testImageName: {Version: "v1.6.6", Commit: testCommit, Build: 1, SHA256: testImageSHA, Path: testImagePath,
			PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:1000"},
	}
	dev := f.Tenants["dev"]
	dev.ServerImage = &ServerImage{Name: testImageName, Version: "v1.6.6", Commit: testCommit, Build: 1,
		SHA256: testImageSHA, Path: testImagePath}
	dev.Code.PreviousImage = "ragstack-server-v1.6.5-b2.sif"
	return f
}

// A registry the deployed binary wrote loads under this one and re-marshals
// BYTE-IDENTICALLY: no new member appears on a row that does not use it, so
// the first write by the new binary is still readable by the old one.
func TestARegistryWrittenBeforeServerImagesLoadsUnchanged(t *testing.T) {
	raw, err := os.ReadFile(goldenPrePRF)
	if err != nil {
		t.Fatal(err)
	}
	f, err := LoadNoRepair(goldenPrePRF)
	if err != nil {
		t.Fatalf("a pre-PR-F registry was refused at load: %v", err)
	}
	if f.ServerImages != nil {
		t.Errorf("server_images = %v, want absent", f.ServerImages)
	}
	for name, tn := range f.Tenants {
		if tn.ServerImage != nil || tn.Code.PreviousImage != "" || tn.ImageMode() {
			t.Errorf("%s: a pre-PR-F row grew image fields: %+v %+v", name, tn.ServerImage, tn.Code)
		}
	}
	out, err := Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out, raw) {
		t.Errorf("a pre-PR-F registry does not re-marshal byte-identically:\n%s", firstDiff(raw, out))
	}
	for _, key := range []string{`"server_images"`, `"server_image"`, `"previous_image"`} {
		if bytes.Contains(out, []byte(key)) {
			t.Errorf("%s is serialised on a registry that does not use it; the deployed binary would refuse it", key)
		}
	}
}

// A registry carrying every new member loads and saves byte-stably: Save →
// Load → Marshal is the file Save wrote.
func TestServerImageFieldsRoundTripByteStable(t *testing.T) {
	f := withServerImages(t)
	reg := filepath.Join(t.TempDir(), "registry.json")
	if err := Save(reg, f, "local:1000"); err != nil {
		t.Fatalf("a registry with server images was refused: %v", err)
	}
	written, err := os.ReadFile(reg)
	if err != nil {
		t.Fatal(err)
	}
	back, err := LoadNoRepair(reg)
	if err != nil {
		t.Fatalf("a registry with server images does not load back: %v", err)
	}
	again, err := Marshal(back)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(written, again) {
		t.Errorf("not byte-stable:\n%s", firstDiff(written, again))
	}
	rec := back.ServerImages[testImageName]
	if rec == nil || rec.SHA256 != testImageSHA || rec.Build != 1 || rec.Path != testImagePath {
		t.Errorf("server_images did not round-trip: %+v", rec)
	}
	si := back.Tenants["dev"].ServerImage
	if si == nil || si.Name != testImageName || si.Commit != testCommit || !back.Tenants["dev"].ImageMode() {
		t.Errorf("server_image did not round-trip: %+v", si)
	}
	if back.Tenants["dev"].Code.PreviousImage != "ragstack-server-v1.6.5-b2.sif" {
		t.Errorf("previous_image = %q", back.Tenants["dev"].Code.PreviousImage)
	}
	if back.Tenants["demo"].ImageMode() {
		t.Error("a tenant without server_image reads as image mode")
	}
	// The schema version did not move: the fields are additive.
	if back.SchemaVersion != SchemaVersion {
		t.Errorf("schema_version %d", back.SchemaVersion)
	}
}

func TestServerImageFieldsAreChecked(t *testing.T) {
	for name, c := range map[string]struct {
		mutate func(*Fleet)
		want   string
	}{
		"a fleet key that is not an image name": {func(f *Fleet) {
			f.ServerImages["server.sif"] = f.ServerImages[testImageName]
			delete(f.ServerImages, testImageName)
		}, "/server_images/server.sif"},
		"a short sha256": {func(f *Fleet) { f.ServerImages[testImageName].SHA256 = "abc" }, "/sha256"},
		"an abbreviated commit": {func(f *Fleet) {
			f.Tenants["dev"].ServerImage.Commit = testCommit[:12]
		}, "/server_image/commit"},
		"a relative path": {func(f *Fleet) {
			f.Tenants["dev"].ServerImage.Path = "images/server/" + testImageName
		}, "/server_image/path"},
		"a path naming another file": {func(f *Fleet) {
			f.Tenants["dev"].ServerImage.Path = "/rag/data/ctl/images/server/ragstack-server-v1.6.6-b2.sif"
		}, "is not the file"},
		"a dotdot path": {func(f *Fleet) {
			f.ServerImages[testImageName].Path = "/rag/data/ctl/images/../server/" + testImageName
		}, "not a clean path"},
		"a build the name disagrees with": {func(f *Fleet) { f.ServerImages[testImageName].Build = 2 }, "/build"},
		"build zero":                      {func(f *Fleet) { f.Tenants["dev"].ServerImage.Build = 0 }, "below the minimum"},
		"a name without the prefix": {func(f *Fleet) {
			f.Tenants["dev"].ServerImage.Name = "ragstack-tools-v1.6.6-b1.sif"
		}, "/server_image/name"},
		"no version":    {func(f *Fleet) { f.ServerImages[testImageName].Version = "" }, "/version"},
		"no preparer":   {func(f *Fleet) { f.ServerImages[testImageName].PreparedBy = "" }, "/prepared_by"},
		"a null record": {func(f *Fleet) { f.ServerImages[testImageName] = nil }, "is null"},
		"a plus name stored under its own spelling": {func(f *Fleet) {
			f.Tenants["dev"].ServerImage.Name = "ragstack-server-v1.6.6+abc-b1.sif"
			f.Tenants["dev"].ServerImage.Path = "/rag/data/ctl/images/server/ragstack-server-v1.6.6+abc-b1.sif"
		}, "/server_image/path"},
		"two records sharing a file": {func(f *Fleet) {
			r := *f.ServerImages[testImageName]
			r.Build = 1
			f.ServerImages["ragstack-server-v1.6.6+x-b1.sif"] = &r
			f.ServerImages[testImageName].Path = "/rag/data/ctl/images/server/ragstack-server-v1.6.6-plus-x-b1.sif"
			r.Path = "/rag/data/ctl/images/server/ragstack-server-v1.6.6-plus-x-b1.sif"
		}, "already the file of"},
		"a previous image that is not one": {func(f *Fleet) {
			f.Tenants["dev"].Code.PreviousImage = "v1.6.5"
		}, "/code/previous_image"},
	} {
		t.Run(name, func(t *testing.T) {
			f := withServerImages(t)
			c.mutate(f)
			err := f.ValidateContract()
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Errorf("ValidateContract = %v, want a refusal containing %q", err, c.want)
			}
		})
	}
	if err := withServerImages(t).ValidateContract(); err != nil {
		t.Errorf("the valid fixture is refused: %v", err)
	}
	// A `+sha` dev build is legal, stored with `+` spelled `-plus-`.
	f := withServerImages(t)
	f.Tenants["dev"].ServerImage.Name = "ragstack-server-v1.6.6+abc-b1.sif"
	f.Tenants["dev"].ServerImage.Path = "/rag/data/ctl/images/server/ragstack-server-v1.6.6-plus-abc-b1.sif"
	if err := f.ValidateContract(); err != nil {
		t.Errorf("a +sha image row is refused: %v", err)
	}
	if got := ServerImageFileName("ragstack-server-v1.6.6+abc-b1.sif"); got != "ragstack-server-v1.6.6-plus-abc-b1.sif" {
		t.Errorf("ServerImageFileName = %q", got)
	}
	if got := ServerImageFileName(testImageName); got != testImageName {
		t.Errorf("ServerImageFileName changed a plain name: %q", got)
	}
}

// The JSON schema accepts the new members and refuses what the Go mirror
// refuses (for the rules the schema can express).
func TestServerImageFieldsAgreeWithTheSchema(t *testing.T) {
	schema := "../../../../contracts/ctl/schemas/registry.json"
	py := ""
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil && exec.Command(p, "-c", "import jsonschema").Run() == nil {
			py = p
			break
		}
	}
	if py == "" {
		t.Skip("no python with jsonschema")
	}
	script := `
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open(sys.argv[1])); doc = json.load(open(sys.argv[2]))
errs = list(Draft202012Validator(schema).iter_errors(doc))
for e in errs[:10]:
    print("/" + "/".join(str(p) for p in e.path), "->", e.message[:200])
sys.exit(1 if errs else 0)
`
	dir := t.TempDir()
	good := filepath.Join(dir, "registry.json")
	if err := Save(good, withServerImages(t), "local:1000"); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(py, "-c", script, schema, good).CombinedOutput(); err != nil {
		t.Fatalf("a registry with server images does not validate against registry.json: %v\n%s", err, out)
	}
	if out, err := exec.Command(py, "-c", script, schema, goldenPrePRF).CombinedOutput(); err != nil {
		t.Fatalf("the pre-PR-F golden does not validate against registry.json: %v\n%s", err, out)
	}
	raw, err := os.ReadFile(good)
	if err != nil {
		t.Fatal(err)
	}
	for name, edit := range map[string][2]string{
		"a bad image name":  {`"name": "` + testImageName + `"`, `"name": "server.sif"`},
		"an extra field":    {`"build": 1,`, `"build": 1, "label": "x",`},
		"a string build":    {`"build": 1,`, `"build": "1",`},
		"a short sha":       {`"sha256": "` + testImageSHA + `"`, `"sha256": "abc"`},
		"a bad previous":    {`"previous_image": "ragstack-server-v1.6.5-b2.sif"`, `"previous_image": "v1.6.5"`},
		"a null image":      {`"server_image": {`, `"server_image": null, "x": {`},
		"a bad fleet key":   {`"server_images": {` + "\n    \"" + testImageName, `"server_images": {` + "\n    \"server.sif"},
		"no prepared_by":    {`"prepared_by": "local:1000"` + "\n    }\n  },\n  \"tenants\"", `"prepared_by": ""` + "\n    }\n  },\n  \"tenants\""},
		"a relative path":   {`"path": "` + testImagePath + `",`, `"path": "images/` + testImageName + `",`},
		"an abbreviated sh": {`"commit": "` + testCommit + `",` + "\n      \"build\"", `"commit": "a730cc9",` + "\n      \"build\""},
	} {
		doc := bytes.Replace(raw, []byte(edit[0]), []byte(edit[1]), 1)
		if bytes.Equal(doc, raw) {
			t.Fatalf("%s: test setup, %q not in the saved registry", name, edit[0])
		}
		p := filepath.Join(dir, strings.ReplaceAll(name, " ", "-")+".json")
		if err := os.WriteFile(p, doc, 0o600); err != nil {
			t.Fatal(err)
		}
		if err := exec.Command(py, "-c", script, schema, p).Run(); err == nil {
			t.Errorf("%s: registry.json accepted it", name)
		}
	}
}

// An absent image block is not serialised — on a tenant, in code, or on the
// fleet — and a Tenant built by NewTenant is not in image mode.
func TestAbsentServerImageFieldsAreNotSerialised(t *testing.T) {
	b, err := json.Marshal(LiveFixture())
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{`"server_images"`, `"server_image"`, `"previous_image"`} {
		if bytes.Contains(b, []byte(key)) {
			t.Errorf("%s is serialised while absent", key)
		}
	}
	if NewTenant("x", "x").ImageMode() {
		t.Error("NewTenant is in image mode")
	}
	var nilTenant *Tenant
	if nilTenant.ImageMode() {
		t.Error("a nil tenant is in image mode")
	}
}

// firstDiff renders the first differing line of two documents.
func firstDiff(a, b []byte) string {
	al, bl := strings.Split(string(a), "\n"), strings.Split(string(b), "\n")
	for i := 0; i < len(al) || i < len(bl); i++ {
		var x, y string
		if i < len(al) {
			x = al[i]
		}
		if i < len(bl) {
			y = bl[i]
		}
		if x != y {
			return "line " + itoa(i+1) + ":\n  want " + x + "\n  got  " + y
		}
	}
	return "(no line differs)"
}

func itoa(i int) string {
	b, _ := json.Marshal(i)
	return string(b)
}
