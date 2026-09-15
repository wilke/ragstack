package doctor

// The stable finding vocabulary. A code never changes meaning: it is what an
// operator greps for, what preconditions.go keys its op table on, and what
// `--force-with-doctor-diff` pins through the run hash. Adding a code is
// additive; renaming one is a breaking change to every runbook that quotes it.
//
// Each constant carries the condition that raises it and the level it is
// raised at when nothing narrows it further (an op's preconditions can only
// RAISE a warn to an error for that op, never lower an error).
const (
	// RegistryManifestMismatch: manifest.tsv disagrees with the registry on a
	// tenant's index or base port. The projection is derived, so this means
	// someone hand-edited it or a write was interrupted. Error.
	RegistryManifestMismatch = "registry_manifest_mismatch"

	// ManifestUnknownRow: manifest.tsv names a tenant the registry does not
	// know. Adoption is incomplete (or a row was added by hand); the port
	// block it claims is not protected by the registry's allocator. Error.
	ManifestUnknownRow = "manifest_unknown_row"

	// PortOwnerMismatch: the process listening on the tenant's API port runs
	// as a different account than registry `owner`. Every start/stop decision
	// downstream assumes the owner, so this blocks mutations. Error.
	//
	// Raised only when the owner IS readable and differs, or when it is
	// unreadable but the registry owner IS the ctl's own account (in which
	// case the ctl's failure to attribute its own listener is itself the
	// defect). An unreadable owner on a pre-handover tenant is
	// port_owner_unverifiable instead — see there.
	PortOwnerMismatch = "port_owner_mismatch"

	// PortOwnerUnverifiable: the tenant's API port is held by a process whose
	// owner this account cannot read (/proc/<pid>/fd is not readable across
	// accounts) AND the registry owner is not the ctl's own account. This is
	// not a mismatch — there is no readable owner to compare — it is the
	// pre-handover shape every tenant is in until PR-E, the same class as
	// secrets_unreadable_by_ctl one directory over. Info, not error.
	PortOwnerUnverifiable = "port_owner_unverifiable"

	// PortNotListening: the registry says `state: active` but nothing listens
	// on the API port — a crashed tenant. WARN, because this is precisely the
	// state `start` and `restart` exist to leave, and an unconditional error
	// refused them. preconditions.go tolerates it for those two (and `stop`,
	// the other half of the repair) and raises it for the ops that must not
	// act on a tenant whose live state contradicts the registry.
	PortNotListening = "port_not_listening"

	// UnexpectedListener: something listens on a port inside a tenant's block
	// that the registry says is unused. A second, unmanaged process on a
	// tenant's block is how two stores end up on one data dir. Warn.
	UnexpectedListener = "unexpected_listener"

	// GatewayMapMismatch: the generated (or hand-written) nginx tenant map
	// points a tenant's route at a port the registry does not allocate to it.
	// Error: traffic reaches the wrong tenant.
	GatewayMapMismatch = "gateway_map_mismatch"

	// EnvNotSystemdParsable: tenant.env or secrets.env — the unit loads
	// BOTH — violates the grammar systemd's
	// EnvironmentFile= accepts (inline comments, duplicate keys, …). Error
	// for a systemd-supervised tenant — the unit would load wrong values —
	// and warn for a manual one, where the shell still sources it correctly.
	EnvNotSystemdParsable = "env_not_systemd_parsable"

	// WorktreeGitdirUnreadable: `git describe` / the .git pointer cannot be
	// read from this account. Expected for svcbvbrc before handover, so warn.
	WorktreeGitdirUnreadable = "worktree_gitdir_unreadable"

	// SecretsUnreadableByCtl: a secret-class env file of the tenant
	// (secrets.env, tenant.env, provision.env, or a historical copy of one
	// beside them) cannot be read by the account the ctl runs as, while the
	// registry records a different `owner`. The pre-handover shape of
	// worktree_gitdir_unreadable, one directory over — and INFO rather than
	// warn, because it is the DESIGNED state until PR-E, not a defect.
	//
	// It is reported rather than left implicit because it is the reason
	// GET /v1/tenants/<t>/logs answers 409 `refused`: those values seed the
	// log redactor, and the ctl returns no log line it could not redact.
	SecretsUnreadableByCtl = "secrets_unreadable_by_ctl"

	// WorktreeOutsideMirror: the worktree's gitdir is neither in the bare
	// mirror nor in a prepared artifact — the running code is not traceable
	// to a reviewed tag. Warn until PR-E moves tenants onto artifacts.
	WorktreeOutsideMirror = "worktree_outside_mirror"

	// ImportRagstackOutsideWorktree: `python -c 'import ragstack'` under the
	// unit's PYTHONPATH resolves outside the tenant's worktree, so the
	// running code is not the code the registry records. Warn.
	ImportRagstackOutsideWorktree = "import_ragstack_outside_worktree"

	// WritableByOthers: a path the ctl trusts (its binary, a unit file, a
	// SIF, a tenant data dir, secrets.env) is writable by other or by a group
	// that is not ragops. Error: anyone in that set can replace what the ctl
	// executes.
	WritableByOthers = "writable_by_others"

	// ACLGrantsOthers: a path the ctl trusts carries a named POSIX ACL entry
	// giving WRITE to somebody who is neither the ctl account nor the path's
	// own owner — a named user, or any named group at all. Error, and the
	// same error writable_by_others is, for the same reason: mode 0750 says
	// nothing about a named ACL entry, so without this check `fleet grant`
	// would open a hole the permission table cannot see.
	//
	// A named GROUP is always reported, whichever group it is: the grant the
	// ctl writes never creates one, and on this host the only group anyone
	// would reach for is the 1869-member `cels` — which is exactly what the
	// ACL scheme exists to avoid.
	ACLGrantsOthers = "acl_grants_others"

	// ACLGrantPresent: the ctl account holds rwx on a managed root through a
	// named ACL entry — the deliberate result of `fleet grant --user
	// svcbvbrc`. Info, listing the roots, so a review sees the interim
	// arrangement stated rather than having to infer it from a mode line.
	ACLGrantPresent = "acl_grant_present"

	// CtlAccountNoAccess: the account the daemon runs as neither OWNS a
	// managed root nor holds rwx on it through an ACL. Every op that writes
	// under that root fails partway with EACCES instead, so this is a warn on
	// its own and an error for create, backup and restore (preconditions.go).
	// The repair is `fleet grant`, run by the owner — not by the ctl.
	CtlAccountNoAccess = "ctl_account_no_access"

	// LingerMissing: /var/lib/systemd/linger/<ctl user> is absent, so the
	// user manager dies at logout and nothing starts at boot. Warn (error for
	// the ops that depend on boot persistence).
	LingerMissing = "linger_missing"

	// UserDropInMissing: /etc/systemd/system/user@<uid>.service.d does not
	// declare SYSTEMD_UNIT_PATH and RequiresMountsFor=/rag, so units load
	// from the NFS home and may start before /rag is mounted. Warn.
	UserDropInMissing = "user_dropin_missing"

	// RuntimeDirMissing: /run/user/<uid> is absent — there is no user bus to
	// talk to. Warn.
	RuntimeDirMissing = "runtime_dir_missing"

	// VMMaxMapCountLow: vm.max_map_count is below 262144, which Elasticsearch
	// needs and Qdrant's mmap sizing depends on. Error.
	VMMaxMapCountLow = "vm_max_map_count_low"

	// DiskLow: free space on the /rag filesystem is under the configured
	// reserve (default 200 GiB). Warn, and an error for backup/restore.
	DiskLow = "disk_low"

	// ESHeapSumHigh: the sum of the exclusive Elasticsearch heaps plus 1g for
	// each shared store exceeds half of MemTotal. Warn.
	ESHeapSumHigh = "es_heap_sum_high"

	// SudoersGroup: informational listing of ADR-0007's fleet-admin group
	// members, so a review sees who can act as the service account. Info.
	SudoersGroup = "sudoers_group"

	// DormantProvisionedDirs: the tenant runs on shared stores but has
	// per-tenant store directories provisioned. Warn — and error when a
	// bin/up.sh exists that would start empty instances on the block's store
	// ports while the config points elsewhere. The ctl never edits that file.
	DormantProvisionedDirs = "dormant_provisioned_dirs"

	// StoreURLDisallowed: a store URL is not loopback http on a port in the
	// tenant's block or in the external-store set. Error: the daemon refuses
	// to probe it.
	StoreURLDisallowed = "store_url_disallowed"

	// CapabilitiesUnconfirmed: every store capability is still false, so no
	// op may stop, purge, snapshot or restore this tenant's stores. Info —
	// the expected state for every adopted tenant.
	CapabilitiesUnconfirmed = "capabilities_unconfirmed"

	// OwnerNotInEnum: the account running a tenant's API is not one of the
	// contract's owners (svcbvbrc|wilke), so the row adopt would write is one
	// registry.Load can never read back. Error: the commit is refused rather
	// than recording either the unloadable value silently or a legal-looking
	// substitute that is not true.
	OwnerNotInEnum = "owner_not_in_enum"

	// ESSnapshotsDirMissing: an exclusively-owned Elasticsearch store has no
	// <data_dir>/elasticsearch/snapshots directory, which its unit binds as
	// path.repo. apptainer refuses a bind whose SOURCE does not exist, so the
	// unit fails to start — the directory is not optional, it is a
	// precondition of starting ES at all. Warn on its own (a stopped tenant
	// is not broken by it) and an error for the ops that start the store:
	// see preconditions.go.
	ESSnapshotsDirMissing = "es_snapshots_dir_missing"

	// ESHeapDrift: the live ES heap differs from what provision.env records.
	// Warn; recorded as a registry drift row by adopt.
	ESHeapDrift = "es_heap_drift"

	// ESHeapUnparsable: a recorded Elasticsearch heap is not <n><k|m|g>, so
	// it cannot enter the es_heap_sum_high sum and that sum is an
	// under-estimate. Warn — loudly, because the alternative (counting it as
	// zero) hid the largest heap on the host.
	ESHeapUnparsable = "es_heap_unparsable"

	// StoreNotListening: a store the tenant owns exclusively has no listener.
	// Warn: the tenant's API is up but half its data path is down.
	StoreNotListening = "store_not_listening"

	// PostgresPortDrift: provision.env's TENANT_PG_PORT is not the +5 port of
	// the tenant's block. Ports in the registry are evidence of what was
	// allocated, and the block is what every other reader computes, so the
	// block wins and the disagreement is reported rather than silently
	// preferred one way or the other. Warn.
	PostgresPortDrift = "postgres_port_drift"

	// PostgresNotListening: the tenant's relational store is a DEDICATED
	// instance (stores.postgres.kind == "local", i.e. `new-tenant.sh
	// --postgres local`) and the tenant is active, but nothing listens on
	// stores.postgres.port. Its ACL, job and collection state all live in
	// that server, so this is not a degraded leg — the API cannot answer.
	// Warn rather than error for the same reason PortNotListening is a warn:
	// an unconditional error would refuse `start`, the very op that fixes it
	// (preconditions.go decides where it must be fatal).
	//
	// A `sqlite` tenant has no server to miss, and an `external` one points
	// at a host the ctl does not manage — neither raises it.
	PostgresNotListening = "postgres_not_listening"

	// UIPortNotListening: the registry records a dev-mode UI port with no
	// listener, so the gateway's UI route 502s. Warn.
	UIPortNotListening = "ui_port_not_listening"

	// UIDistMissing: the tenant's UI is recorded as `static`, but
	// <data_dir>/ui/dist/index.html is not a regular file. The gateway
	// aliases that directory and `try_files` falls back to that exact file,
	// so every path under the UI route answers 404. Error: the row would
	// describe a gateway mount with nothing behind it, and the build that
	// fills it is a step the ctl does not take for the tenant.
	UIDistMissing = "ui_dist_missing"

	// UnsupportedEnvKey: a key in tenant.env that the settings classification
	// table does not know. Recorded as drift, never rendered. Info.
	UnsupportedEnvKey = "unsupported_env_key"

	// APIKeyRoleUnknown: an API_KEY_ROLES entry names a role outside
	// {admin, user}. The key is recorded as `user` and the original is kept
	// in the finding. Warn.
	APIKeyRoleUnknown = "api_key_role_unknown"

	// ExternalRefOutsideDataDir: a configuration key points at a file outside
	// the tenant's data dir; it is backed up by reference and never moved.
	// Info.
	ExternalRefOutsideDataDir = "external_ref_outside_data_dir"

	// UnmanagedFiles: files under the tenant tree that no renderer produces
	// (*.bak-*, backup-*/, 0-byte *.db, *.orig, *.recovered). Info — listed
	// so nothing is silently orphaned.
	UnmanagedFiles = "unmanaged_files"

	// DataDirOffLayout: the adopted data dir is not <rag_root>/data/tenants/
	// <manifest_name>. Info: legal, but every path convention downstream is
	// then explicit rather than derived.
	DataDirOffLayout = "data_dir_off_layout"

	// APIBindFromScript: the API was not listening, so `api.bind` was read
	// from the `--host` in the tenant's own launch script rather than from a
	// live command line. Info: still evidence, one step removed.
	APIBindFromScript = "api_bind_from_script"

	// APIBindAssumed: nothing could be observed about the API's bind — no
	// listener on the API port (a stopped tenant, or a host where the
	// listener→pid mapping is unreadable) and no `--host` in a launch script
	// — so the contract's default 127.0.0.1 was recorded. Warn: the row is a
	// default, not an observation, and the tenant's next start confirms or
	// corrects it. The alternative was an empty bind, which registry.Save
	// refuses against contracts/ctl/schemas/registry.json, so adopt could not
	// commit a stopped tenant at all.
	APIBindAssumed = "api_bind_assumed"
)
