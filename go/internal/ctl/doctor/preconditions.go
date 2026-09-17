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
	// that will own the processes, and the store URLs it will dial; it must
	// never bring up the dormant pair of a shared-store tenant; and every
	// directory its units BIND has to exist, because apptainer refuses a bind
	// whose source is missing — an absent path.repo (es_snapshots_dir_missing)
	// is an ES service that cannot start, caught before the attempt.
	"start": {
		PortOwnerMismatch, EnvNotSystemdParsable, StoreURLDisallowed,
		DormantProvisionedDirs, VMMaxMapCountLow, ESSnapshotsDirMissing,
	},
	// Stopping needs to be sure it is stopping THIS tenant's processes.
	"stop": {PortOwnerMismatch},
	// Restart is stop then start.
	"restart": {
		PortOwnerMismatch, EnvNotSystemdParsable, StoreURLDisallowed,
		DormantProvisionedDirs, VMMaxMapCountLow, ESSnapshotsDirMissing,
	},
	// A backup writes a bundle plus a transient snapshot copy; without the
	// reserve it fills the filesystem the tenants run on. It must also know
	// which processes are the tenant's before it fences them — and must not
	// capture a store that died mid-write as if it were a clean snapshot.
	// It also WRITES a bundle under <backups>/tenants: an account that can
	// neither own nor reach that root produces a half-written bundle, which
	// is worse than a refused backup.
	"backup": {DiskLow, PortOwnerMismatch, PortNotListening, CtlAccountNoAccess},
	// A restore creates a fresh tenant and stages a whole bundle into it.
	"restore": {DiskLow, StoreURLDisallowed, CtlAccountNoAccess},
	// Handover's row is per DESTINATION (handoverPreconditions below); this
	// entry is the default destination's, so that RedCodes("handover") — the
	// engine's own call — gates the handover this deployment can actually
	// perform. See handoverPreconditions for both lists and why they differ.
	"handover": handoverInstance,
	// set-ui-mode `static` BUILDS a bundle out of the tenant's worktree and
	// then publishes a gateway generation that serves it. Two things must
	// therefore be true and are not negotiable: the code it builds from has to
	// be traceable to the mirror (a UI built out of a home-directory checkout
	// is a bundle nobody can reproduce), and the paths it writes into must not
	// be rewritable by somebody outside ragops between the build and the
	// rename. The gateway's own coherence is the third: publishing over a
	// routing table that already disagrees with the registry would make the
	// "only the UI row changed" claim false.
	"set-ui-mode": {
		WorktreeOutsideMirror, WorktreeGitdirUnreadable, WritableByOthers,
		GatewayMapMismatch, RegistryManifestMismatch, ManifestUnknownRow,
	},
	// set-bind writes ONE registry field and touches no process, so the only
	// thing that can make it wrong is a registry whose projection is already
	// incoherent — the same gate `create` has, for the same reason.
	"set-bind": {RegistryManifestMismatch, ManifestUnknownRow},
	// set-supervisor writes ONE registry field too, and the same reasoning
	// applies. What it must NOT gate on is anything about the processes: it
	// is the repair for a row that disagrees with them, and its own step asks
	// the host the question that matters (can this account act on what the row
	// will claim).
	"set-supervisor": {RegistryManifestMismatch, ManifestUnknownRow},
	// env-pg-password rewrites secrets.env and touches no process. It is run
	// on a tenant that is being prepared for a handover — often one whose
	// findings are exactly what the preparation exists to clear — so nothing
	// blocks it. The empty row is deliberate and is what makes `doctor --op
	// env-pg-password` a known op rather than a typo that silently disables
	// the gate.
	"env-pg-password": {},
	// A local migration is a handover's barrier plus disk for the copy, and
	// it copies a tree the tenant is supposed to be serving from.
	"migrate-local": {DiskLow, PortOwnerMismatch, EnvNotSystemdParsable, PortNotListening},
	// Decommission quarantines a tenant whose recovery bundle must exist and
	// whose identity must be certain. A tenant that is already DOWN is the
	// normal input — the selftest stops its sandbox before it quarantines it,
	// and an operator tidies up a tenant that was stopped first — so
	// port_not_listening is tolerated (see below), not raised.
	"decommission": {PortOwnerMismatch, DiskLow},
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
	"create": {RegistryManifestMismatch, ManifestUnknownRow, DiskLow, VMMaxMapCountLow, CtlAccountNoAccess},
	// Adopting RECORDS a tenant the ctl did not make; nearly everything the
	// run finds is what adoption exists to write down, so almost nothing
	// blocks it. Two things do: a store URL the daemon will refuse to dial
	// (the row would describe a tenant no later op can probe) and a
	// manifest.tsv row the registry does not know (adoption is the moment the
	// allocator's record and the registry are supposed to converge, and a
	// stray row means two allocators).
	"adopt": {StoreURLDisallowed, ManifestUnknownRow},
	// artifact-prepare runs `npm ci` and a `git` checkout into the ctl's own
	// artifact tree. Nothing about a TENANT can make that wrong — it names
	// none — but a host with no room produces a half-checked-out artifact that
	// a later `create` would build from, and the ctl account has to be able to
	// write the tree it is checking out into.
	"artifact-prepare": {DiskLow, CtlAccountNoAccess},
	// create-sandbox is `create` onto the selftest port block, so it is
	// `create`'s row: the same allocator, the same stores, the same host.
	// vm_max_map_count_low is the one that bites — a sandbox Elasticsearch
	// dies on it exactly as a tenant's does.
	"create-sandbox": {RegistryManifestMismatch, ManifestUnknownRow, DiskLow, VMMaxMapCountLow, CtlAccountNoAccess},
	// gateway-reload re-tests and HUPs the LIVE tree without publishing a new
	// generation, so unlike gateway-apply it does not need the routing table
	// to already agree with the registry — that is often WHY it is being run.
	// It needs the live tree to be one this account owns.
	"gateway-reload": {CtlAccountNoAccess},
	// settings-put rewrites the ctl's own configuration, not a tenant's, so
	// no tenant finding is a reason to refuse it — and an empty row is the
	// point: an op absent from this table has NO gate at all (RedCodes
	// returns nil), which is not the same statement as "nothing blocks it".
	"settings-put": {},
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
	// `adopt --readopt --confirm-stores` is the ONLY way to clear
	// stores_unconfirmed, so the finding must not refuse it. (It is a warning
	// on its own merits today; the row is here so that an op-scoped raise
	// elsewhere can never reach the op that repairs it — the same lesson
	// env-normalize taught.)
	"adopt":        {StoresUnconfirmed},
	"start":        {PortNotListening},
	"restart":      {PortNotListening},
	"stop":         {PortNotListening},
	"decommission": {PortNotListening},
	// `handover` is two jobs run by two accounts, and the SECOND of them acts
	// on a tenant the first one stopped: between the release and the take
	// nothing of the tenant is listening, by construction. Raising
	// port_not_listening for this op therefore refused every take there will
	// ever be. The question it was standing in for — "is there a running
	// tenant to hand over" — is the release planner's, which refuses a row
	// whose `state` is not `active`, and that check does not misfire on the
	// phase whose whole precondition is that the tenant is down.
	"handover": {PortNotListening},
	// `set-supervisor` corrects a ROW. Every state it is used to repair is a
	// state in which something is not listening — that is usually why the row
	// needs correcting.
	"set-supervisor": {PortNotListening},
}

// handoverPreconditions is the handover row, per DESTINATION supervisor.
//
// A handover's preconditions are facts about the RUNTIME the tenant is moving
// onto, and PR-D2 gave this deployment a second one. The systemd list demands
// a user manager that will exist at boot — linger, the root drop-in that puts
// SYSTEMD_UNIT_PATH and RequiresMountsFor=/rag on it, a runtime dir to talk to
// — and on coconut none of those three is installable this week. The instance
// list demands what actually brings a tenant back there instead: the service
// account's `@reboot` crontab line (boot_cron_missing) and its ACL access to
// the managed roots (ctl_account_no_access), which are the negatives of the
// brief's `boot_cron_present` and `acl_grant_present`.
//
// Four conditions are in BOTH lists because they are about the tenant rather
// than about the supervisor: an env file the ctl will parse, a port whose
// owner is the account the registry names, code traceable to the mirror, and
// paths nobody outside ragops can rewrite. stores_unconfirmed joins them at
// PR-E: a handover that moves the API and leaves the tenant's own stores
// running as another account splits the tenant between two accounts, and
// neither can then restart it. port_not_listening is deliberately NOT among
// them — see `tolerates` — because the take acts on a tenant the release has
// already stopped.
var (
	handoverSystemd = []string{
		EnvNotSystemdParsable, PortOwnerMismatch, WorktreeOutsideMirror,
		WorktreeGitdirUnreadable, LingerMissing, UserDropInMissing,
		RuntimeDirMissing, WritableByOthers, StoresUnconfirmed,
	}
	handoverInstance = []string{
		EnvNotSystemdParsable, PortOwnerMismatch, WorktreeOutsideMirror,
		WorktreeGitdirUnreadable, WritableByOthers,
		StoresUnconfirmed, BootCronMissing, CtlAccountNoAccess,
	}
	handoverPreconditions = map[string][]string{
		SupervisorSystemd:  handoverSystemd,
		SupervisorInstance: handoverInstance,
	}
)

// The destination supervisors a handover can name. They are the registry's
// `supervisor` values, repeated here rather than imported because doctor is
// below registry in the import graph for every other check too.
const (
	SupervisorSystemd  = "systemd"
	SupervisorInstance = "instance"
	// DefaultHandoverDestination is what RedCodes("handover") gates on: the
	// supervisor this deployment can actually hand a tenant over TO. PR-D2
	// postponed systemd until the machine is dedicated and the three root
	// items are installed; until then every handover lands on `instance`.
	DefaultHandoverDestination = SupervisorInstance
)

// RedCodesForDestination is RedCodes for an op whose preconditions depend on
// where the tenant is going. Only `handover` has such a row today; every other
// op ignores dest.
//
// It is what applyPreconditions calls — the per-destination table is on the
// live path, not a seam waiting for a caller — and doctor.Options.Destination
// is how a caller chooses. An empty destination means
// DefaultHandoverDestination, which is the supervisor this deployment can
// actually hand a tenant over to.
//
// An unknown destination falls back to the default rather than to no gate at
// all: a typo in a destination must never be the thing that turns the
// precondition table off.
func RedCodesForDestination(op, dest string) []string {
	if op != "handover" {
		return RedCodes(op)
	}
	codes, ok := handoverPreconditions[dest]
	if !ok {
		codes = handoverPreconditions[DefaultHandoverDestination]
	}
	out := make([]string, len(codes))
	copy(out, codes)
	return out
}

// GateCodes are the findings whose presence as a WARNING means op needs the
// operator's acknowledgement: its precondition set MINUS what it tolerates.
//
// It is the same subtraction applyPreconditions makes ("tolerating wins: a row
// cannot both raise and lower"), and it has to be, because the engine's yellow
// gate and the doctor's own levels are two readings of one table. Without it
// the gate demanded a `--force-with-doctor-diff` for exactly the finding an op
// exists to clear.
func GateCodes(op, dest string) []string {
	tolerated := map[string]bool{}
	for _, c := range Tolerated(op) {
		tolerated[c] = true
	}
	var out []string
	for _, c := range RedCodesForDestination(op, dest) {
		if !tolerated[c] {
			out = append(out, c)
		}
	}
	return out
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
// instanceTenants names the rows whose supervisor is `instance`; for those,
// env_not_systemd_parsable is never raised (see run.instanceTenants).
func applyPreconditions(findings []model.Finding, op, destination string,
	instanceTenants map[string]bool) []model.Finding {
	out := make([]model.Finding, len(findings))
	copy(out, findings)
	if op == "" {
		return out
	}
	red := map[string]bool{}
	// RedCodesForDestination, not RedCodes: `handover`'s row depends on the
	// runtime the tenant is moving ONTO, and the two lists differ by five
	// findings. Every other op ignores the destination, so this is the same
	// call for all of them.
	for _, c := range RedCodesForDestination(op, destination) {
		red[c] = true
	}
	ok := map[string]bool{}
	for _, c := range Tolerated(op) {
		ok[c] = true
		delete(red, c) // tolerating wins: a row cannot both raise and lower
	}
	for i := range out {
		if out[i].Code == EnvNotSystemdParsable && instanceTenants[string(out[i].Tenant)] {
			// There is no unit to load this file, so systemd's grammar is not
			// what decides whether this tenant can start.
			continue
		}
		switch {
		case out[i].Level == model.LevelWarn && red[out[i].Code]:
			out[i].Level = model.LevelError
		case out[i].Level == model.LevelError && ok[out[i].Code]:
			out[i].Level = model.LevelWarn
		}
	}
	return out
}
