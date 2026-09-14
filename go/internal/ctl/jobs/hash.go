package jobs

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// The plan digest. plan.json: `plan_hash` = sha256 over the canonical JSON of
// `{op, tenant, args_redacted, registry_generation, doctor_findings_sha256,
// steps[].{kind, target, args_sha256}, schema_version}`.
//
// The hash is what makes "the plan you were shown is the plan that ran" a
// checkable claim: the client sees it in the dry run, the engine recomputes
// it AFTER taking the locks, and a difference is 409 `plan_stale` rather than
// a surprise. So everything that could change the outcome has to be IN it —
// the registry generation, the doctor's verdict, and each step's would-write
// and would-run bodies — and nothing that merely varies run to run (a
// timestamp, a map's iteration order) may be.

// canonicalJSON renders v with object keys in sorted order at every depth.
// encoding/json already sorts map[string]any keys, but a struct emits its
// fields in declaration order, so v is round-tripped through `any` first:
// after that every object is a map and the ordering is total.
func canonicalJSON(v any) ([]byte, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var generic any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber() // 1 and 1.0 must not both become "1"
	if err := dec.Decode(&generic); err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(generic); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// sha256Of is the contract's digest spelling: `sha256:<64 hex>`.
func sha256Of(b []byte) string {
	sum := sha256.Sum256(b)
	return "sha256:" + hex.EncodeToString(sum[:])
}

// canonicalSHA256 hashes the canonical rendering of v.
func canonicalSHA256(v any) (string, error) {
	b, err := canonicalJSON(v)
	if err != nil {
		return "", err
	}
	return sha256Of(b), nil
}

// planHashStep is one step's contribution: what it acts on, and a digest of
// what it would write and run. The step's executable halves are closures and
// cannot be hashed; its declared effects can, and those are what an operator
// approved.
type planHashStep struct {
	Kind       string   `json:"kind"`
	Targets    []string `json:"targets"`
	ArgsSHA256 string   `json:"args_sha256"`
}

type planHashInput struct {
	Op                 string         `json:"op"`
	Tenant             string         `json:"tenant"`
	ArgsRedacted       map[string]any `json:"args_redacted"`
	RegistryGeneration int64          `json:"registry_generation"`
	DoctorHash         string         `json:"doctor_hash"`
	Steps              []planHashStep `json:"steps"`
	SchemaVersion      int            `json:"schema_version"`
}

// stepArgsSHA256 digests the step's declared effects: title, would_write
// (path, mode and redacted preview) and would_run (argv).
func stepArgsSHA256(s model.PlannedStep) (string, error) {
	return canonicalSHA256(struct {
		Title      string             `json:"title"`
		WouldWrite []model.WouldWrite `json:"would_write"`
		WouldRun   []model.WouldRun   `json:"would_run"`
	}{s.Title, s.WouldWrite, s.WouldRun})
}

// PlanHash computes plan.plan_hash for p with the given redacted args. The
// engine calls it twice per job — once when it answers, once under the locks
// — and refuses with ErrPlanStale when the two differ.
func PlanHash(p model.Plan, argsRedacted map[string]any) (string, error) {
	in := planHashInput{
		Op:                 p.Op,
		Tenant:             string(p.Tenant),
		ArgsRedacted:       argsRedacted,
		RegistryGeneration: p.RegistryGeneration,
		DoctorHash:         p.Doctor.Hash,
		Steps:              make([]planHashStep, 0, len(p.Steps)),
		SchemaVersion:      p.SchemaVersion,
	}
	if in.ArgsRedacted == nil {
		in.ArgsRedacted = map[string]any{}
	}
	for _, s := range p.Steps {
		d, err := stepArgsSHA256(s)
		if err != nil {
			return "", fmt.Errorf("hashing step %d: %w", s.N, err)
		}
		t := s.Targets
		if t == nil {
			t = []string{}
		}
		in.Steps = append(in.Steps, planHashStep{Kind: s.Kind, Targets: t, ArgsSHA256: d})
	}
	return canonicalSHA256(in)
}

// Fingerprint is the idempotency key's companion: the request this key was
// first used for. A second call with the same key and the same fingerprint is
// the same request (the original job comes back); a different fingerprint is
// 409 `duplicate`. It hashes the UNREDACTED args on purpose — two requests
// that differ only in a secret are different requests — which is also why it
// is the one place unredacted args reach the database, and only as a digest.
func Fingerprint(op, tenant string, args map[string]any, planHash string) (string, error) {
	if args == nil {
		args = map[string]any{}
	}
	return canonicalSHA256(struct {
		Op       string         `json:"op"`
		Tenant   string         `json:"tenant"`
		Args     map[string]any `json:"args"`
		PlanHash string         `json:"plan_hash"`
	}{op, tenant, args, planHash})
}
