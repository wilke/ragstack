package api

// Every CTL_* environment variable the daemon reads, in one place.
//
// This list is the reconciliation point for `ops/ansible/roles/ragstack-ctl`'s
// `ctl.env` / `ctl-secrets.env` templates: a variable that exists here and not
// in the template is a setting nobody can configure on the host, and one that
// exists in the template and not here is a setting that silently does nothing.
// EnvKeys() is what a test (and, from PR-D, `doctor`) compares the template
// against.
//
// Which file a variable belongs in is part of the contract with the installer:
// the four that carry credential material live in `ctl-secrets.env` (0600),
// everything else in `ctl.env` (0640).
const (
	// EnvListen is the bind address. Loopback only unless the operator passes
	// --allow-non-loopback: the admin mount is public by decision and the
	// gateway is the only thing that should be able to reach the daemon.
	// ctl.env. Default 127.0.0.1:23990.
	EnvListen = "CTL_LISTEN"

	// EnvAPIKeys is the JSON list (or bare comma list) of ctl API keys.
	// ctl-secrets.env — these ARE the credentials.
	EnvAPIKeys = "CTL_API_KEYS"
	// EnvAPIKeyRoles maps each key to "operator" or "viewer". A key with no
	// entry authenticates and is then 403: enrolment is not authorization.
	// ctl-secrets.env (its keys are the credentials).
	EnvAPIKeyRoles = "CTL_API_KEY_ROLES"
	// EnvAPIKeyNames maps each key to a human label used as the principal id
	// ("key:<label>"); without one the fingerprint stands in.
	// ctl-secrets.env. Optional.
	EnvAPIKeyNames = "CTL_API_KEY_NAMES"
	// EnvAPIKeyPrincipals maps each key to the IDENTITY principal it belongs
	// to ("bvbrc:alice@patricbrc.org"). It is what lets that identity
	// re-present the key in a mutation body (`ctl_api_key`) — the flow the
	// browser UI needs, since a session authenticates reads only. Without a
	// binding a bearer principal can never satisfy the body-key rule: a key's
	// own principal id is "key:<label>" and can never equal "bvbrc:<un>".
	// ctl-secrets.env, for the same reason as the two above: its KEYS are the
	// ctl API keys. Optional.
	EnvAPIKeyPrincipals = "CTL_API_KEY_PRINCIPALS"

	// EnvAdminSubjects lists the BV-BRC subjects ("bvbrc:<un>") with the
	// operator role, comma-separated or as a JSON list. ctl.env — a subject is
	// a user name, not a credential.
	EnvAdminSubjects = "CTL_ADMIN_SUBJECTS"
	// EnvViewerSubjects is the same for the viewer role. ctl.env.
	EnvViewerSubjects = "CTL_VIEWER_SUBJECTS"

	// EnvIdentityIssuerAllowlist narrows the pinned BV-BRC SigningSubject set
	// (default: the four canonical URLs from P3AuthConstants.pm). An
	// explicitly EMPTY value is refused at start-up — it would authenticate
	// nobody, and the failure mode of treating empty as "allow any issuer" is
	// the forgery vector the pinning exists to close. ctl.env.
	EnvIdentityIssuerAllowlist = "CTL_IDENTITY_ISSUER_ALLOWLIST"

	// EnvIdentityKeyFetchFile is a TEST-ONLY override: read the BV-BRC key
	// server's body from this file instead of fetching it over HTTP, so the
	// conformance suite can present a token signed with the committed fixture
	// key. REFUSED unless --fake-drivers is also given — on a real daemon it
	// would let whoever can write that file choose who may authenticate.
	// Never in a template; set only by the conformance runner.
	EnvIdentityKeyFetchFile = "CTL_IDENTITY_KEY_FETCH_FILE"

	// EnvRegistry is the registry.json path. ctl.env.
	// Default <rag-root>/data/tenants/registry.json.
	EnvRegistry = "CTL_REGISTRY"
	// EnvRagRoot is the deployment root every other path derives from.
	// ctl.env. Default /rag.
	EnvRagRoot = "CTL_RAG_ROOT"
	// EnvStateDir holds jobs.db, audit.jsonl, locks, gateway generations and
	// artifacts. ctl.env. Default <rag-root>/data/ctl.
	EnvStateDir = "CTL_STATE_DIR"
	// EnvConfigDir holds ctl.env, ctl-secrets.env, unit templates and the
	// canonical unit files. ctl.env. Default <rag-root>/config/ctl.
	EnvConfigDir = "CTL_CONFIG_DIR"

	// EnvLogLevel is the slog level: debug|info|warn|error. ctl.env.
	EnvLogLevel = "CTL_LOG_LEVEL"
	// EnvLogFormat is text|json. ctl.env. Default text.
	EnvLogFormat = "CTL_LOG_FORMAT"

	// EnvRateLimitPerCredential is how many authentication failures one
	// credential may produce per minute before it is answered 429. Negative
	// disables the limiter. ctl.env.
	EnvRateLimitPerCredential = "CTL_RATE_LIMIT_PER_CREDENTIAL"
	// EnvRateLimitTarpitAt is the fleet-wide failures-per-minute that turns
	// the tarpit on. Negative disables it. ctl.env.
	EnvRateLimitTarpitAt = "CTL_RATE_LIMIT_TARPIT_AT"
	// EnvRateLimitTarpitDelay is how long a FAILURE response is held once the
	// tarpit is on — a Go duration ("2s") or a bare number of seconds. The
	// delay occupies a request goroutine and its connection for its whole
	// length, so it is capped at ratelimit.MaxTarpitDelay however it is set,
	// and stays below the server's WriteTimeout. ctl.env. Default 2s.
	EnvRateLimitTarpitDelay = "CTL_RATE_LIMIT_TARPIT_DELAY"

	// EnvExternalStorePorts lists the shared-store ports a tenant may point at
	// from outside its own block (6333, 6343, 9200). ctl.env.
	EnvExternalStorePorts = "CTL_EXTERNAL_STORE_PORTS"

	// EnvDefaultSupervisor is the supervisor `tenant create` gives a tenant
	// whose request does not name one: systemd|instance (PR-D2). ctl.env.
	//
	// It is a DEPLOYMENT fact, not a contract one, which is why it lives here
	// rather than as the default in create_request.json: that schema promises
	// `systemd`, and on coconut — where the service account has no linger, no
	// user manager and no logind session under cron — the only supervisor that
	// can actually start a tenant at boot today is `instance`. A host says so
	// once, here, instead of every caller remembering to pass the argument.
	EnvDefaultSupervisor = "CTL_DEFAULT_SUPERVISOR"

	// The host programs the real drivers run and the two directories they work
	// from (PR-D). Each is an ABSOLUTE path; the drivers never search PATH, so
	// a host that keeps one of these somewhere unusual — coconut's node lives
	// under a user's ~/.local, not /rag/tools — says so here rather than
	// relying on the daemon's environment. ctl.env, all optional: each falls
	// back to the default drivers.NewReal names.
	EnvSystemctlBin = "CTL_SYSTEMCTL_BIN"
	EnvGitBin       = "CTL_GIT_BIN"
	EnvNodeBin      = "CTL_NODE_BIN"
	EnvNpmBin       = "CTL_NPM_BIN"
	EnvApptainerBin = "CTL_APPTAINER_BIN"
	// EnvCrontabBin is crontab(1) — the boot hook of `supervisor: instance`,
	// which is the only one an account with no user manager and no logind
	// session under cron has (PR-D2, "Host facts").
	EnvCrontabBin = "CTL_CRONTAB_BIN"
	// EnvMirror is the BARE repository `fleet artifact prepare` resolves refs
	// and adds worktrees in. Default <rag-root>/repos/ragstack.git. The ctl
	// never creates it: cloning the mirror is an operator's deploy-time act.
	EnvMirror = "CTL_MIRROR"
	// EnvNpmCache is the npm cache `fleet artifact prepare` installs through.
	// Default <rag-root>/cache/npm — never ~/.npm, which is shared with
	// whatever else the account runs and is in no backup.
	EnvNpmCache = "CTL_NPM_CACHE"
)

// HostToolEnvKeys are the PR-D host-program variables.
//
// They are kept out of the Ansible-template reconciliation for now: the
// `ragstack-ctl` role's ctl.env template is another agent's file in this PR
// series, and the defaults are correct for a host that installs node where the
// plan says. An operator sets them by hand in ctl.env today (documented in
// docs/runbooks/ctl-deploy.md); when the role templates them, fold this list
// into the reconciled set by deleting the exception in env_template_test.go.
func HostToolEnvKeys() []string {
	return []string{
		EnvSystemctlBin, EnvGitBin, EnvNodeBin, EnvNpmBin, EnvApptainerBin,
		EnvCrontabBin, EnvMirror, EnvNpmCache,
	}
}

// EnvKeys returns every CTL_* variable the daemon reads, sorted the way this
// file declares them. Used by the Ansible-template reconciliation test.
func EnvKeys() []string {
	return append([]string{
		EnvListen,
		EnvAPIKeys, EnvAPIKeyRoles, EnvAPIKeyNames, EnvAPIKeyPrincipals,
		EnvAdminSubjects, EnvViewerSubjects,
		EnvIdentityIssuerAllowlist, EnvIdentityKeyFetchFile,
		EnvRegistry, EnvRagRoot, EnvStateDir, EnvConfigDir,
		EnvLogLevel, EnvLogFormat,
		EnvRateLimitPerCredential, EnvRateLimitTarpitAt, EnvRateLimitTarpitDelay,
		EnvExternalStorePorts, EnvDefaultSupervisor,
	}, HostToolEnvKeys()...)
}

// SecretEnvKeys are the variables that must live in ctl-secrets.env (0600)
// rather than ctl.env (0640), because their keys or values are credentials.
func SecretEnvKeys() []string {
	return []string{EnvAPIKeys, EnvAPIKeyRoles, EnvAPIKeyNames, EnvAPIKeyPrincipals}
}
