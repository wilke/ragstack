package ops

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// planErrWith is planWith for a plan that is expected to be refused.
func planErrWith(t *testing.T, oc jobs.Context, d Deps, verb string, args map[string]any) error {
	t.Helper()
	d.Roots, d.Now = oc.Roots, oc.Now
	if d.SaveFleet == nil {
		d.SaveFleet = func(f *registry.Fleet) error { f.Generation++; return nil }
	}
	op, ok := NewRegistry(d).Lookup(verb)
	if !ok {
		t.Fatalf("no op %q", verb)
	}
	_, err := op.Plan(context.Background(), oc, args)
	return err
}

// TestSecretsRequireRefusesWithoutARecipient: the fail-closed mode refuses at
// PLAN time — before a fence stops anything — when nobody could open the
// payload, and names the file and the command that fixes it.
func TestSecretsRequireRefusesWithoutARecipient(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	for name, d := range map[string]Deps{"no sealer": {}, "a sealer with no recipients": {Sealer: fakeSealer{}}} {
		err := planErrWith(t, oc, d, "backup", map[string]any{"fence": true, "secrets": "require"})
		if !errors.Is(err, jobs.ErrRefused) {
			t.Fatalf("%s: secrets=require = %v, want ErrRefused", name, err)
		}
		for _, want := range []string{"secrets=require", "backup-recipients.txt", "fleet backup-identity init"} {
			if !strings.Contains(err.Error(), want) {
				t.Errorf("%s: the refusal does not say %q: %v", name, want, err)
			}
		}
	}
}

// TestSecretsRequirePlansAndRunsTheSealWithARecipient: with one recipient the
// require plan is the include plan, and the bundle carries secrets.age.
func TestSecretsRequirePlansAndRunsTheSealWithARecipient(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	sealer := fakeSealer{fps: []string{"sha256:0123456789abcdef"}}
	p := planWith(t, oc, Deps{Sealer: sealer}, "backup", map[string]any{"fence": true, "secrets": "require"})
	if !hasStep(p, "fs", "seal the secret files into secrets.age") {
		t.Fatalf("no seal step: %v", titles(p))
	}
	if hasStep(p, "fs", "skip the encrypted secrets payload") {
		t.Fatalf("a require plan carries the skip step: %v", titles(p))
	}
	newRunner(oc, fake).runAll(t, p)
	sealed := fake.FakeFiles().Content(bundlePath("dev", "secrets.age"))
	if !strings.HasPrefix(string(sealed), "age-encrypted-to:") {
		t.Fatalf("no sealed secrets.age; the bundle holds %v", bundleFiles(fake))
	}
	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatal(err)
	}
	if sec := man["secrets"].(map[string]any); sec["included"] != true {
		t.Errorf("manifest secrets = %v", sec)
	}
}

// TestSecretsRequireFailsTheStepWhenTheSealerLosesItsRecipients: `require`
// never degrades into a quiet exclusion, even if the sealer answers
// ErrNoRecipients at run time.
func TestSecretsRequireFailsTheStepWhenTheSealerLosesItsRecipients(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	// Fingerprints say one recipient (so the plan proceeds); Seal says none.
	sealer := fakeSealer{fps: []string{"sha256:0123456789abcdef"}, empty: true}
	p := planWith(t, oc, Deps{Sealer: sealer}, "backup", map[string]any{"secrets": "require"})
	r := newRunner(oc, fake)
	var sealErr error
	for _, s := range p.Steps {
		if !strings.Contains(s.Plan.Title, "seal the secret files") {
			if _, err := r.run(s); err != nil {
				t.Fatalf("step %q: %v", s.Plan.Title, err)
			}
			continue
		}
		_, sealErr = r.run(s)
		break
	}
	if sealErr == nil {
		t.Fatal("secrets=require sealed nothing and the step succeeded")
	}
}

// TestSecretsSkipNeverSeals: `skip` excludes the payload whatever recipients
// exist, says so in the plan, and the bundle carries no secrets.age.
func TestSecretsSkipNeverSeals(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	sealer := fakeSealer{fps: []string{"sha256:0123456789abcdef"}}
	p := planWith(t, oc, Deps{Sealer: sealer}, "backup", map[string]any{"fence": true, "secrets": "skip"})
	if hasStep(p, "fs", "seal the secret files") {
		t.Fatalf("a skip plan seals: %v", titles(p))
	}
	if !hasStep(p, "fs", "skip the encrypted secrets payload") {
		t.Fatalf("no skip step: %v", titles(p))
	}
	warned := false
	for _, w := range p.Plan.Warnings {
		if strings.Contains(w, "secrets=skip") {
			warned = true
		}
	}
	if !warned {
		t.Errorf("the plan does not say the secrets are skipped on request: %v", p.Plan.Warnings)
	}
	for _, s := range p.Plan.Steps {
		for _, w := range s.WouldWrite {
			if strings.HasSuffix(w.Path, "secrets.age") {
				t.Errorf("step %q would write %s", s.Title, w.Path)
			}
		}
	}
	newRunner(oc, fake).runAll(t, p)
	if fake.FakeFiles().Content(bundlePath("dev", "secrets.age")) != nil {
		t.Fatal("a secrets=skip bundle carries secrets.age")
	}
	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatal(err)
	}
	if sec := man["secrets"].(map[string]any); sec["included"] != false {
		t.Errorf("manifest secrets = %v", sec)
	}
}

// TestSecretsIncludeIsTheDefault: an absent `secrets` and an explicit
// `include` plan the same steps and warnings, with and without a sealer — the
// argument changed nothing for a caller that does not send it.
func TestSecretsIncludeIsTheDefault(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	for _, d := range []Deps{{}, {Sealer: fakeSealer{fps: []string{"sha256:0123456789abcdef"}}}} {
		absent := planWith(t, oc, d, "backup", map[string]any{"fence": true})
		include := planWith(t, oc, d, "backup", map[string]any{"fence": true, "secrets": "include"})
		if !reflect.DeepEqual(absent.Plan.Steps, include.Plan.Steps) {
			t.Errorf("steps differ:\n%v\n%v", titles(absent), titles(include))
		}
		if !reflect.DeepEqual(absent.Plan.Warnings, include.Plan.Warnings) {
			t.Errorf("warnings differ:\n%v\n%v", absent.Plan.Warnings, include.Plan.Warnings)
		}
	}
}

func TestSecretsOutsideTheEnumIsAValidationError(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	err := planErrWith(t, oc, Deps{}, "backup", map[string]any{"secrets": "plaintext"})
	if !errors.Is(err, jobs.ErrValidation) {
		t.Fatalf("secrets=plaintext = %v, want ErrValidation", err)
	}
}
