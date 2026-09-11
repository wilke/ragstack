package doctor

import (
	"sort"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// preconditions is the op × finding table: for each operation, the findings
// that operation cannot proceed over. A code listed here is RAISED from warn
// to error for that op, which makes the run red and the mutation a 409
// doctor_red. Nothing in this table ever lowers a level — a finding that is
// an error on its own merits (a manifest split-brain, a world-writable unit
// file, vm.max_map_count too low) blocks every op regardless. The ONE place a
// level comes back down is the `tolerates` table below, and only for an op
// whose whole purpose is to leave the state that finding describes.
//
// Read-only operations are absent on purpose: `fleet`, `tenant show`, `logs`,
// `doctor` itself and `gateway render` never block on a warning.
//
// Each row is one sentence of reasoning, because the reasoning is the part a
// reviewer has to check:
var preconditions = map[string][]string{
	// Starting a tenant trusts the env file the unit will load, the account
	// that will own the processes, and the store URLs it will dial; and it
	// must never bring up the dormant pair of a shared-store tenant.
	"start": {
		PortOwnerMismatch, EnvNotSystemdParsable, StoreURLDisallowed,
		DormantProvisionedDirs, VMMaxMapCountLow,
	},
	// Stopping needs to be sure it is stopping THIS tenant's processes.
	"stop": {PortOwnerMismatch},
	// Restart is stop then start.
	"restart": {
		PortOwnerMismatch, EnvNotSystemdParsable, StoreURLDisallowed,
		DormantProvisionedDirs, VMMaxMapCountLow,
	},
	// A backup writes a bundle plus a transient snapshot copy; without the
	// reserve it fills the filesystem the tenants run on. It must also know
	// which processes are the tenant's before it fences them — and must not
	// capture a store that died mid-write as if it were a clean snapshot.
	"backup": {DiskLow, PortOwnerMismatch, PortNotListening},
	// A restore creates a fresh tenant and stages a whole bundle into it.
	"restore": {DiskLow, StoreURLDisallowed},
	// Handover moves a tenant onto systemd units under the service account:
	// the env file must be loadable by systemd, the code traceable, the
	// paths not writable by anyone outside ragops, and boot persistence real.
	"handover": {
		EnvNotSystemdParsable, PortOwnerMismatch, WorktreeOutsideMirror,
		WorktreeGitdirUnreadable, LingerMissing, UserDropInMissing,
		RuntimeDirMissing, WritableByOthers, PortNotListening,
	},
	// A local migration is a handover's barrier plus disk for the copy, and
	// it copies a tree the tenant is supposed to be serving from.
	"migrate-local": {DiskLow, PortOwnerMismatch, EnvNotSystemdParsable, PortNotListening},
	// Decommission quarantines a tenant whose recovery bundle must exist and
	// whose identity must be certain; a tenant that is already down is not a
	// tenant whose state the ctl can vouch for.
	"decommission": {PortOwnerMismatch, DiskLow, PortNotListening},
	// Credential and env edits rewrite the env files the API will reload, so
	// the grammar must already be clean.
	"key-mint":      {EnvNotSystemdParsable},
	"key-revoke":    {EnvNotSystemdParsable},
	"admin-add":     {EnvNotSystemdParsable},
	"admin-remove":  {EnvNotSystemdParsable},
	"sa-create":     {EnvNotSystemdParsable},
	"sa-disable":    {EnvNotSystemdParsable},
	"sa-enable":     {EnvNotSystemdParsable},
	"env-set":       {EnvNotSystemdParsable},
	"env-unset":     {EnvNotSystemdParsable},
	"env-normalize": {}, // see tolerates: it FIXES that finding
	// Rendering units bakes the registry's paths into files systemd
	// executes; a path anyone outside ragops can rewrite is not renderable.
	"render-units": {WritableByOthers},
	// Publishing a gateway generation must start from a routing table that
	// already matches the registry, or the "no-op diff" claim is false.
	"gateway-apply": {GatewayMapMismatch, RegistryManifestMismatch, ManifestUnknownRow},
	// Creating a tenant allocates from the registry, so the projection must
	// be coherent, and the host must have room.
	"create": {RegistryManifestMismatch, ManifestUnknownRow, DiskLow, VMMaxMapCountLow},
	// update-code swaps the worktree under a running API — which has to BE
	// running for "swap it under" to mean anything.
	"update-code": {WorktreeOutsideMirror, WorktreeGitdirUnreadable, EnvNotSystemdParsable, PortNotListening},
}

// tolerates is the inverse table: for each operation, the findings whose
// level that operation LOWERS to warn. It exists because a precondition table
// that only ever raises made two ops unreachable — the op that repairs a
// finding was refused by the finding it repairs:
//
//   - `env-normalize` is the only way to fix env_not_systemd_parsable, and on
//     a systemd tenant that finding is an error on its own merits, so the op
//     was refused on every host that needed it.
//   - `start` / `restart` / `stop` are the only ways out of a crashed tenant
//     (port_not_listening). It is a warning now, but an op-scoped raise
//     elsewhere must still never reach these three.
//
// Nothing else may be listed here: tolerating a finding is a claim that the
// op leaves the condition behind, not that the condition is acceptable.
var tolerates = map[string][]string{
	"env-normalize": {EnvNotSystemdParsable},
	"start":         {PortNotListening},
	"restart":       {PortNotListening},
	"stop":          {PortNotListening},
}

// Tolerated lists the findings op lowers to warn.
func Tolerated(op string) []string {
	codes, ok := tolerates[op]
	if !ok {
		return nil
	}
	out := make([]string, len(codes))
	copy(out, codes)
	return out
}

// RedCodes lists the findings op cannot proceed over. An unknown or empty op
// returns nil: nothing is raised.
func RedCodes(op string) []string {
	codes, ok := preconditions[op]
	if !ok {
		return nil
	}
	out := make([]string, len(codes))
	copy(out, codes)
	return out
}

// Ops lists every operation the precondition gate knows, sorted. `--op` is
// validated against it: an op nobody has written a row for silently disabled
// the whole gate (RedCodes returned nil), so a typo in a runbook bought a
// mutation with no preconditions at all.
func Ops() []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(preconditions)+len(tolerates))
	for op := range preconditions {
		seen[op] = true
		out = append(out, op)
	}
	for op := range tolerates {
		if !seen[op] {
			out = append(out, op)
		}
	}
	sort.Strings(out)
	return out
}

// KnownOp reports whether op is in Ops().
func KnownOp(op string) bool {
	if _, ok := preconditions[op]; ok {
		return true
	}
	_, ok := tolerates[op]
	return ok
}

// applyPreconditions raises the warnings op cannot tolerate to errors. It
// copies: the caller's findings keep their unscoped levels, so the same run
// can be presented to several ops.
func applyPreconditions(findings []model.Finding, op string) []model.Finding {
	out := make([]model.Finding, len(findings))
	copy(out, findings)
	if op == "" {
		return out
	}
	red := map[string]bool{}
	for _, c := range RedCodes(op) {
		red[c] = true
	}
	ok := map[string]bool{}
	for _, c := range Tolerated(op) {
		ok[c] = true
		delete(red, c) // tolerating wins: a row cannot both raise and lower
	}
	for i := range out {
		switch {
		case out[i].Level == model.LevelWarn && red[out[i].Code]:
			out[i].Level = model.LevelError
		case out[i].Level == model.LevelError && ok[out[i].Code]:
			out[i].Level = model.LevelWarn
		}
	}
	return out
}
