package ops

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"sort"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// settings-put is PUT /v1/settings: the fleet-level fields an operator may
// change over HTTP, saved into the registry as one generation.
//
// What the registry persists today is `images` and `ctl` (registry.json has
// no home for retention or a default Python env yet — those two are answered
// by GET /v1/settings from the daemon's defaults). A request that names them
// is refused, not silently dropped: a 202 that "saved" a value nowhere would
// be the one kind of lie an operator cannot see through.
//
// `recipients` never reaches this op: the HTTP layer rejects it as 422
// (backup recipients are a CLI-only, trusted-operator surface).

// persistedSettings lists the top-level settings the registry can hold.
var persistedSettings = map[string]bool{"images": true, "ctl": true}

func planSettingsPut(_ context.Context, p *planner, args map[string]any) error {
	var unpersisted []string
	for k := range args {
		if !persistedSettings[k] {
			unpersisted = append(unpersisted, k)
		}
	}
	sort.Strings(unpersisted)
	if len(unpersisted) > 0 {
		return p.refuse("the registry has no field for %v yet; only images and ctl are persisted in this release",
			unpersisted)
	}
	if len(args) == 0 {
		return p.refuse("nothing to change: the settings document is empty")
	}
	if p.oc.Fleet == nil {
		return p.refuse("no registry loaded")
	}

	// Plan against a copy: the merge must not touch the snapshot the engine
	// hashes, and a refused merge (a value outside the contract) must be a
	// refusal at plan time, not a failed step.
	next, err := mergeSettings(p.oc.Fleet, args)
	if err != nil {
		return p.refuse("%v", err)
	}
	if err := next.Validate(); err != nil {
		return p.refuse("the merged registry does not validate: %v", err)
	}
	changed, err := json.Marshal(args)
	if err != nil {
		return err
	}
	regPath := filepath.Join(p.oc.Roots.DataDir, "tenants", "registry.json")
	from := p.oc.Fleet.Generation
	p.need(model.LockRegistry, model.LockManifest)
	p.result = map[string]any{"registry_generation": from + 1}
	p.add(step{
		Kind:    "registry",
		Title:   fmt.Sprintf("save the settings into the registry (generation %d → %d)", from, from+1),
		Targets: []string{regPath},
		WouldWrite: []model.WouldWrite{{
			Path: regPath, Mode: "0660",
			Preview: model.NullString(changed),
		}},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if p.op.deps.SaveFleet == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			// Re-merge onto the fleet the ENGINE loaded under the locks, not
			// the plan-time snapshot: the plan hash already proved they agree.
			cur, err := mergeSettings(sc.Ops.Fleet, args)
			if err != nil {
				return "", err
			}
			if err := p.op.deps.SaveFleet(cur); err != nil {
				return "", err
			}
			sc.Logf("registry saved at generation %d", cur.Generation)
			return fmt.Sprintf("registry generation %d", cur.Generation), nil
		},
		Reconcile: func(_ context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			// Done when the fleet already carries every requested value.
			want, err := mergeSettings(sc.Ops.Fleet, args)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if settingsEqual(sc.Ops.Fleet, want) {
				return jobs.ReconcileDone, nil
			}
			return jobs.ReconcileRedo, nil
		},
	})
	return nil
}

// mergeSettings returns a copy of f with the settings document applied. The
// merge is a JSON merge over the registry's own field names (they are the
// contract's), which is what makes a PARTIAL document safe: an absent key is
// "keep", not "clear".
func mergeSettings(f *registry.Fleet, args map[string]any) (*registry.Fleet, error) {
	raw, err := json.Marshal(f)
	if err != nil {
		return nil, err
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, err
	}
	for k, v := range args {
		if !persistedSettings[k] {
			return nil, fmt.Errorf("%w: %s is not a persisted setting", jobs.ErrRefused, k)
		}
		patch, ok := v.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%w: %s must be an object", jobs.ErrValidation, k)
		}
		base, _ := doc[k].(map[string]any)
		if base == nil {
			base = map[string]any{}
		}
		doc[k] = deepMerge(base, patch)
	}
	merged, err := json.Marshal(doc)
	if err != nil {
		return nil, err
	}
	var out registry.Fleet
	if err := json.Unmarshal(merged, &out); err != nil {
		return nil, fmt.Errorf("%w: %v", jobs.ErrValidation, err)
	}
	return &out, nil
}

func deepMerge(base, patch map[string]any) map[string]any {
	out := make(map[string]any, len(base)+len(patch))
	for k, v := range base {
		out[k] = v
	}
	for k, v := range patch {
		if pm, ok := v.(map[string]any); ok {
			if bm, ok := out[k].(map[string]any); ok {
				out[k] = deepMerge(bm, pm)
				continue
			}
		}
		out[k] = v
	}
	return out
}

// settingsEqual compares only the persisted settings blocks.
func settingsEqual(a, b *registry.Fleet) bool {
	ja, _ := json.Marshal(map[string]any{"images": a.Images, "ctl": a.Ctl})
	jb, _ := json.Marshal(map[string]any{"images": b.Images, "ctl": b.Ctl})
	return string(ja) == string(jb)
}
