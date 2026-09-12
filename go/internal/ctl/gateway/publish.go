package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// ErrRefused is every "no, and here is why" answer of this package: a hand
// edit in the way, a master owned by another account, a rollback with nothing
// to roll back to. The CLI maps it to exit 3 and the API to 409 `refused`.
var ErrRefused = errors.New("refused")

// Defaults of a publish.
const (
	DefaultBaseURL       = "http://127.0.0.1:9000"
	DefaultProbeTimeout  = 10 * time.Second
	DefaultProbeInterval = 250 * time.Millisecond
	DefaultKeep          = 5
)

// ExpectPaths maps a golden body file (testdata/live-2026-09-10/gateway/) to
// the request path it is the answer for. These four are PR-B's go/no-go: the
// gateway's own bodies must not change when the tenant maps move into a
// generated include.
var ExpectPaths = map[string]string{
	"root.json":            "/",
	"tenants.json":         "/ragstack/tenants",
	"api-unknown-404.json": "/ragstack/nope/api/health",
	"catchall-404.json":    "/ragstack/x",
}

// Options configure a publish. Everything that touches the host goes through
// Exec/Sig/Prober, so a test drives the whole flow with fakes.
type Options struct {
	Roots  paths.Roots
	Exec   Exec
	Sig    Signaller
	Prober Prober

	By           string // audit principal
	Apptainer    string // default /usr/bin/apptainer
	NginxSIF     string // default <ImagesDir>/nginx.sif
	PIDFile      string // default <ProxyDir>/run/nginx.pid
	ErrorLog     string // default <ProxyDir>/run/logs/error.log
	BaseURL      string // default http://127.0.0.1:9000
	ExpectBodies map[string][]byte

	ProbeTimeout  time.Duration
	ProbeInterval time.Duration
	Keep          int // generations to retain; default 5
	// StageParent is where the staged copy of the proxy tree is made. The
	// default is <CtlStateDir>/tmp, mode 0700, NOT the OS temp dir: the staged
	// tree contains tls/, and on a host where the certificate key IS readable
	// by the publishing account the copy is a readable private key. /tmp is
	// world-traversable and survives until reboot; the ctl's own state dir is
	// neither.
	StageParent string
	KeepStage   bool // leave the staged copy behind (dry run / debugging)

	DryRun bool
	Log    func(step, msg string)
}

func (o *Options) defaults() {
	if o.Apptainer == "" {
		o.Apptainer = "/usr/bin/apptainer"
	}
	if o.NginxSIF == "" {
		o.NginxSIF = filepath.Join(o.Roots.ImagesDir, "nginx.sif")
	}
	if o.PIDFile == "" {
		o.PIDFile = filepath.Join(o.Roots.ProxyDir, "run", "nginx.pid")
	}
	if o.ErrorLog == "" {
		o.ErrorLog = filepath.Join(o.Roots.ProxyDir, "run", "logs", "error.log")
	}
	if o.BaseURL == "" {
		o.BaseURL = DefaultBaseURL
	}
	if o.ProbeTimeout == 0 {
		o.ProbeTimeout = DefaultProbeTimeout
	}
	if o.ProbeInterval == 0 {
		o.ProbeInterval = DefaultProbeInterval
	}
	if o.Keep == 0 {
		o.Keep = DefaultKeep
	}
	if o.Log == nil {
		o.Log = func(string, string) {}
	}
}

// Step is one recorded step of a publish, in the order it ran.
type Step struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
}

// Result is what a publish did, complete for a success and for a failure.
type Result struct {
	Generation         int      `json:"generation"`
	PreviousGeneration int      `json:"previous_generation"`
	RegistryGeneration int64    `json:"registry_generation"`
	SHA256             string   `json:"sha256"`
	DryRun             bool     `json:"dry_run"`
	State              string   `json:"state"`
	NginxPID           int      `json:"nginx_pid"`
	Reverted           bool     `json:"reverted"`
	StagedDir          string   `json:"staged_dir"`
	ConfigTest         string   `json:"config_test"`
	Steps              []Step   `json:"steps"`
	Pruned             []int    `json:"pruned"`
	Warnings           []string `json:"warnings"`
}

func (r *Result) step(name string, err error, detail string) {
	if err != nil && detail == "" {
		detail = err.Error()
	}
	r.Steps = append(r.Steps, Step{Name: name, OK: err == nil, Detail: detail})
}

// Publish renders the next generation and makes it the serving one.
//
// The steps are render → prove the nginx master is ours → write gen-<N> →
// stage + `nginx -t` → switch → HUP → confirm the master took the new
// configuration → probe from desired state → prune, each idempotent and each
// recorded in txn.json before or immediately after it runs. A failure BEFORE
// the switch leaves `current` untouched (txn `failed`); a failure after it puts
// `current` back and HUPs again (txn `reverted`).
//
// It holds the gateway lock for the whole run: see LockName.
func Publish(ctx context.Context, f *registry.Fleet, opts Options) (*Result, error) {
	opts.defaults()
	st := NewState(opts.Roots)
	res := &Result{DryRun: opts.DryRun}

	lock, err := st.lock()
	if err != nil {
		res.step("lock", err, "")
		return res, err
	}
	defer lock.unlock()

	// An incomplete publication is repaired before a new one starts: the
	// pointer must be honest before it is moved again.
	//
	// A DRY RUN does not repair, because repair WRITES — it moves `current`, the
	// symlink nginx resolves both generated includes through. `gateway apply
	// --dry-run` against a host with an interrupted publication used to repoint
	// the live gateway at the previous generation and say nothing about it,
	// arming a routing change nobody asked for at whatever reload came next.
	//
	// Reporting the incomplete transaction and carrying on (rather than
	// refusing) is a FAITHFUL simulation here, because nothing the dry run does
	// reads `current`: stage() copies the proxy tree and then overwrites both
	// generated include paths with this generation's bytes, and rewriteConfPaths
	// only rewrites proxy-dir references — so no include in the staged tree
	// resolves through the pointer, and the staged `nginx -t` tests the same
	// bytes whether or not the pointer is still on the interrupted generation.
	// If that ever stops holding — a generated include gaining an
	// `include <state>/current/...` line of its own — this has to become a
	// refusal (ErrRefused), because the simulation would then depend on a
	// pointer the dry run is not allowed to correct.
	if opts.DryRun {
		t, ok, err := st.ReadTxn()
		if err != nil {
			res.step("repair", err, "")
			return res, err
		}
		if ok && !t.Complete() {
			msg := fmt.Sprintf("the previous publication is incomplete (gen-%d, state %q) and a dry run does not repair it, "+
				"so `current` still points at gen-%d: run `ragstack-ctl gateway repair` before applying",
				t.Generation, t.State, st.CurrentGeneration())
			res.Warnings = append(res.Warnings, msg)
			res.step("repair", nil, msg)
			opts.Log("repair", msg)
		}
	} else if rep, err := st.repair(); err != nil {
		res.step("repair", err, "")
		return res, err
	} else if rep.Incomplete {
		res.Warnings = append(res.Warnings, "the previous publication was incomplete: "+rep.Action)
		opts.Log("repair", rep.Action)
	}

	gen, err := Render(f, opts.Roots)
	if err != nil {
		res.step("render", err, "")
		return res, err
	}
	res.Generation, res.RegistryGeneration, res.SHA256 = gen.N, gen.RegistryGeneration, gen.SHA256()
	res.PreviousGeneration = st.CurrentGeneration()
	res.step("render", nil, fmt.Sprintf("gen-%d from registry generation %d (%s)", gen.N, gen.RegistryGeneration, gen.SHA256()))
	opts.Log("render", res.Steps[len(res.Steps)-1].Detail)

	txn := &Txn{
		Generation:         gen.N,
		RegistryGeneration: gen.RegistryGeneration,
		StartedAt:          now().UTC().Format(time.RFC3339),
		PublishedBy:        opts.By,
		PreviousGeneration: res.PreviousGeneration,
	}

	// A dry run writes nothing at all — not the generation dir, not txn.json —
	// and never looks at the master. It must be runnable against a production
	// state dir by an account that is only allowed to look, which on this host
	// is not the account that owns the proxy.
	if !opts.DryRun {
		// ---- the master is ours, BEFORE anything moves ---------------------
		//
		// These three checks (the pidfile parses, the pid is nginx, the uid is
		// this account) used to run inside reload(), i.e. AFTER the switch. A
		// proxy owned by another account therefore got: gen-<N> written,
		// `current` moved, both include symlinks rewritten, then a refusal and
		// a revert — a lot of writing to discover something knowable from
		// /proc before the first one. The in-reload check stays, because the
		// master can change between here and the signal.
		master, err := preflightMaster(opts)
		res.NginxPID, txn.NginxPID = master.pid, master.pid
		if err != nil {
			res.step("preflight", err, "")
			res.State = TxnFailed
			return res, err
		}
		res.step("preflight", nil, fmt.Sprintf("nginx master pid %d (%s) is owned by this account", master.pid, master.info.Comm))

		if err := gen.Write(st); err != nil {
			res.step("write", err, "")
			return res, err
		}
		res.step("write", nil, st.GenDir(gen.N))
		if err := st.transition(txn, TxnStaging, nil); err != nil {
			res.step("txn", err, "")
			return res, err
		}
	}

	// ---- stage + nginx -t ------------------------------------------------
	staged, warns, err := stage(opts, gen)
	res.StagedDir, res.Warnings = staged, append(res.Warnings, warns...)
	if err == nil {
		var out string
		out, err = nginxTest(ctx, opts, staged)
		res.ConfigTest = out
	}
	if !opts.KeepStage && staged != "" {
		defer func() { _ = os.RemoveAll(staged) }()
	}
	ok := err == nil
	txn.ConfigOK = &ok
	if err != nil {
		res.step("nginx -t", err, strings.TrimSpace(res.ConfigTest+"\n"+err.Error()))
		if !opts.DryRun {
			_ = st.transition(txn, TxnFailed, err)
		}
		res.State = TxnFailed
		return res, fmt.Errorf("staged nginx -t failed: %w", err)
	}
	res.step("nginx -t", nil, "configuration test passed against the staged tree")
	opts.Log("nginx -t", "passed")

	if opts.DryRun {
		res.State = "dry-run"
		res.step("switch", nil, "skipped (--dry-run)")
		res.step("reload", nil, "skipped (--dry-run)")
		res.step("probe", nil, "skipped (--dry-run)")
		return res, nil
	}

	// ---- switch ----------------------------------------------------------
	//
	// What each include path looks like NOW is recorded, and made durable,
	// before the first one is touched — see IncludeState. A crash inside
	// switchTo otherwise leaves a half-switched tree whose previous contents
	// exist nowhere.
	prior, err := inspectIncludes(opts.Roots.ProxyDir)
	if err == nil {
		err = validateIncludes(opts, st, gen, txn.PreviousGeneration, prior)
	}
	if err != nil {
		res.step("switch", err, "")
		_ = st.transition(txn, TxnFailed, err)
		res.State = TxnFailed
		return res, err
	}
	txn.Includes = prior
	if err := st.transition(txn, TxnSwitching, nil); err != nil {
		res.step("txn", err, "")
		res.State = TxnFailed
		return res, err
	}

	// From here on every failure reverts — including a failure to WRITE the
	// record. A transition that returns an error after the pointer has moved
	// used to return straight out of Publish, leaving `current` on an
	// unverified generation with a txn.json that did not say so.
	fail := func(step string, err error) (*Result, error) {
		res.step(step, err, "")
		rerr := revert(ctx, opts, st, txn, res, gen)
		res.State = TxnReverted
		res.Reverted = true
		if rerr != nil {
			return res, fmt.Errorf("%s failed (%w) and the revert did not complete: %v", step, err, rerr)
		}
		return res, fmt.Errorf("%s failed, reverted to generation %d: %w", step, txn.PreviousGeneration, err)
	}

	notes, err := switchTo(opts, st, gen, txn.PreviousGeneration, prior)
	res.Warnings = append(res.Warnings, notes...)
	if err != nil {
		// The switch may have got halfway: `current` moved, one include
		// rewritten. That is a state after the switch, so it reverts.
		return fail("switch", err)
	}
	res.step("switch", nil, fmt.Sprintf("current -> gen-%d; include symlinks in %s", gen.N, opts.Roots.ProxyDir))
	opts.Log("switch", res.Steps[len(res.Steps)-1].Detail)
	if err := st.transition(txn, TxnSwitched, nil); err != nil {
		return fail("txn", err)
	}

	// ---- reload ----------------------------------------------------------
	//
	// The master's identity is sampled before the HUP so the confirmation can
	// tell "these are new workers" from "the master was replaced under us".
	before, beforeErr := reloadWitness(opts, txn.NginxPID)
	hupAt := now()
	pid, err := reload(opts)
	res.NginxPID = pid
	txn.NginxPID = pid
	if err != nil {
		return fail("reload", err)
	}
	txn.ReloadedAt = hupAt.UTC().Format(time.RFC3339)
	res.step("reload", nil, fmt.Sprintf("SIGHUP to nginx master pid %d", pid))
	opts.Log("reload", res.Steps[len(res.Steps)-1].Detail)

	// A SIGHUP nginx cannot use is not an error anywhere: kill(2) succeeds,
	// the master logs `[emerg]`, keeps the OLD configuration and carries on
	// serving. Without this step the publish then "verified" a generation the
	// gateway had refused, and the probes passed because the old configuration
	// answers the same tenant list whenever the change is a no-op.
	if beforeErr != nil {
		res.Warnings = append(res.Warnings, "could not sample the master's workers before the reload ("+
			beforeErr.Error()+"); the reload was NOT confirmed")
	} else if err := confirmReload(opts, before, hupAt); err != nil {
		return fail("reload", err)
	} else {
		res.step("confirm reload", nil, "the master spawned new workers and logged no [emerg]/[alert]")
		opts.Log("confirm reload", "the master took the new configuration")
	}

	if err := st.transition(txn, TxnReloaded, nil); err != nil {
		return fail("txn", err)
	}

	// ---- probe from desired state ---------------------------------------
	if err := probe(ctx, opts, f, gen); err != nil {
		return fail("probe", err)
	}
	res.step("probe", nil, "gateway answers the desired state")
	opts.Log("probe", "ok")

	if err := st.transition(txn, TxnVerified, nil); err != nil {
		return fail("txn", err)
	}
	if err := st.markVerified(gen.N); err != nil {
		// Not fatal: the generation IS serving and verified. It only means a
		// later rollback will refuse this one as a target, which is the safe
		// direction for a bookkeeping failure.
		res.Warnings = append(res.Warnings, fmt.Sprintf(
			"gen-%d is verified but could not be recorded in %s (%v); a rollback TO it will be refused", gen.N, VerifiedName, err))
	}
	res.State = TxnVerified

	res.Pruned = prune(st, opts.Keep, gen.N, txn.PreviousGeneration)
	res.step("prune", nil, fmt.Sprintf("kept the last %d generations, removed %v", opts.Keep, res.Pruned))
	return res, nil
}

// revert undoes a switch: the pointer goes back to the generation that was
// serving, every include path is restored to the CONTENT it had before, and
// the master is signalled again so the running workers leave the bad
// generation behind.
//
// "Restores content, never absence" is the whole rule, and it is the one this
// function used to break. The proxy configuration includes both generated
// paths unconditionally — snippets/routes.conf includes the static snippet,
// conf.d/10-gateway.conf reads `$tenant_api` — so any state in which an
// include is missing, or is a symlink into a `current` that was deleted, is an
// nginx that refuses to start at its next reload. A failed FIRST publish used
// to produce exactly that: it removed the symlinks it had created and deleted
// `current`, which turned an adopted coconut-proxy bootstrap copy into a
// dangling link and an absent-before path into a missing include. A generation
// nobody verified, left loadable, is strictly better than a gateway that
// cannot start.
func revert(ctx context.Context, opts Options, st State, txn *Txn, res *Result, gen *Generation) error {
	var errs []string
	if txn.PreviousGeneration > 0 {
		if err := st.pointCurrentAt(txn.PreviousGeneration); err != nil {
			errs = append(errs, err.Error())
		}
	}
	notes, restoreErrs, keepCurrent := restoreIncludes(opts, st, gen, txn)
	res.Warnings = append(res.Warnings, notes...)
	errs = append(errs, restoreErrs...)
	if txn.PreviousGeneration == 0 && !keepCurrent {
		// Nothing was published before this attempt and no include resolves
		// through `current` any more, so the pointer can go: leaving it on a
		// generation that was never verified would make Status report it as
		// the serving one.
		if err := os.Remove(st.CurrentLink()); err != nil && !errors.Is(err, os.ErrNotExist) {
			errs = append(errs, err.Error())
		}
	}
	if pid, err := reload(opts); err != nil {
		errs = append(errs, "reload after revert: "+err.Error())
	} else {
		res.NginxPID = pid
	}
	_ = ctx
	revertErr := errors.New(strings.Join(errs, "; "))
	if len(errs) == 0 {
		revertErr = nil
	}
	_ = st.transition(txn, TxnReverted, revertErr)
	res.step("revert", revertErr, fmt.Sprintf("current -> gen-%d", txn.PreviousGeneration))
	return revertErr
}

// restoreIncludes puts each include path back to the state switchTo found it
// in. keepCurrent is true when at least one restored path still resolves
// through `current`, so the caller must not remove that pointer.
func restoreIncludes(opts Options, st State, gen *Generation, txn *Txn) (notes, errs []string, keepCurrent bool) {
	for _, p := range txn.Includes {
		live := filepath.Join(opts.Roots.ProxyDir, filepath.FromSlash(p.Rel))
		switch p.Kind {
		case IncludeRegular:
			// The coconut-proxy bootstrap copy, adopted by the switch. Its
			// bytes exist nowhere else now, which is why the txn carries them.
			mode := os.FileMode(p.Mode).Perm()
			if mode == 0 {
				mode = 0o644
			}
			if err := replaceWithRegular(live, p.Content, mode); err != nil {
				errs = append(errs, fmt.Sprintf("restoring %s: %v", p.Rel, err))
				continue
			}
			notes = append(notes, fmt.Sprintf("%s was restored to the regular file it was before the publish "+
				"(the coconut-proxy bootstrap copy, %d bytes)", p.Rel, len(p.Content)))
		case IncludeSymlink:
			if got, err := os.Readlink(live); err == nil && got == p.Target {
				keepCurrent = keepCurrent || strings.HasPrefix(p.Target, st.Dir)
				continue
			}
			_ = os.Remove(live)
			if err := os.Symlink(p.Target, live); err != nil {
				errs = append(errs, fmt.Sprintf("restoring %s: %v", p.Rel, err))
				continue
			}
			keepCurrent = keepCurrent || strings.HasPrefix(p.Target, st.Dir)
			notes = append(notes, fmt.Sprintf("%s points at %s again", p.Rel, p.Target))
		default: // IncludeAbsent
			if txn.PreviousGeneration > 0 {
				// The symlink through `current` resolves to the previous
				// generation now, which is loadable. Leaving it is better than
				// recreating the hole the publish found.
				keepCurrent = true
				notes = append(notes, fmt.Sprintf("%s did not exist before this publish; it is left as the symlink "+
					"through current, which now resolves to gen-%d", p.Rel, txn.PreviousGeneration))
				continue
			}
			// Nothing was ever published and the path was missing, so there is
			// no earlier content to go back to. Write the generation's own
			// bytes as a REGULAR file: the configuration that failed to VERIFY
			// still parses (the staged `nginx -t` passed before the switch), and
			// a gateway that starts beats a gateway with a missing include.
			if err := replaceWithRegular(live, gen.Files[p.Rel], 0o640); err != nil {
				errs = append(errs, fmt.Sprintf("restoring %s: %v", p.Rel, err))
				continue
			}
			notes = append(notes, fmt.Sprintf("%s did not exist before this publish and there is no earlier generation "+
				"to fall back to; gen-%d's bytes were left there as a REGULAR file so the configuration still loads. "+
				"The next `gateway apply` adopts it, or remove it by hand once the proxy tree's own bootstrap copy is back",
				p.Rel, gen.N))
		}
	}
	return notes, errs, keepCurrent
}

// replaceWithRegular writes b at path as a regular file, removing whatever is
// there first — which may be a SYMLINK, and writing through that would put the
// bytes in the generation dir instead.
func replaceWithRegular(path string, b []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return writeFileAtomic(path, b, mode)
}

// ---------------------------------------------------------------- staging

// stage builds a throwaway copy of the proxy tree whose two generated
// includes are this generation's bytes, so `nginx -t` tests exactly what
// would be served — without a single write inside /rag/config/proxy.
//
// run/ is not copied (it is the live pid, sockets and logs); the staged tree
// gets its own empty one. tls/ IS copied, because nginx -t opens the
// certificate it is pointed at; a key this account cannot read is replaced by
// a symlink to the original and reported as a warning rather than failing the
// copy — if it is unreadable, `nginx -t` will say so itself and that is the
// honest answer.
//
// Every absolute reference to the real proxy dir inside the staged *.conf
// files is rewritten to the staging dir. Without that, the staged nginx.conf
// would include the LIVE conf.d and test the live files under a different
// name — the one thing the staging copy exists to avoid.
func stage(opts Options, gen *Generation) (string, []string, error) {
	src := opts.Roots.ProxyDir
	parent, err := stageParent(opts)
	if err != nil {
		return "", nil, err
	}
	dir, err := os.MkdirTemp(parent, "ragstack-ctl-gateway-stage-")
	if err != nil {
		return "", nil, err
	}
	warns, err := copyTree(src, dir, map[string]bool{"run": true})
	if err != nil {
		return dir, warns, fmt.Errorf("staging a copy of %s: %w", src, err)
	}
	for _, d := range []string{"run/logs", "run/tmp"} {
		if err := os.MkdirAll(filepath.Join(dir, filepath.FromSlash(d)), 0o750); err != nil {
			return dir, warns, err
		}
	}
	// The generation's own files go in BEFORE the path rewrite, not after.
	//
	// tenants-ui-static.generated.conf carries `include <ProxyDir>/snippets/…`
	// lines of its own. Written after the rewrite, those lines still named the
	// LIVE tree, so the staged `nginx -t` pulled two snippets out of
	// /rag/config/proxy — the one thing the staging copy exists to avoid, and
	// invisible because the test passed either way while the two trees agreed.
	for _, rel := range RelPaths() {
		p := filepath.Join(dir, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			return dir, warns, err
		}
		// Remove first: the copy may have left a symlink into the live tree
		// there, and writing through it would touch the real file.
		_ = os.Remove(p)
		if err := os.WriteFile(p, gen.Files[rel], 0o640); err != nil {
			return dir, warns, err
		}
	}
	// The canonical tree, which is what the config files NAME even when the
	// tree being staged is a checkout somewhere else (testing a coconut-proxy
	// branch before it is deployed).
	canonical := filepath.Join(opts.Roots.RagRoot, "config", "proxy")
	for _, from := range dedupe(src, canonical) {
		if err := rewriteConfPaths(dir, from, dir); err != nil {
			return dir, warns, err
		}
	}
	// nginx -t OPENS the certificate, so a tree without a tls/ of its own (a
	// checkout, again) borrows the deployed one rather than failing the test
	// on a file the change under test has nothing to do with.
	if _, err := os.Stat(filepath.Join(dir, "tls")); errors.Is(err, os.ErrNotExist) && canonical != src {
		if _, err := os.Stat(filepath.Join(canonical, "tls")); err == nil {
			w, err := copyTree(filepath.Join(canonical, "tls"), filepath.Join(dir, "tls"), nil)
			warns = append(warns, w...)
			if err != nil {
				return dir, warns, err
			}
			warns = append(warns, "the staged tree has no tls/ of its own; staged the one from "+canonical)
		}
	}
	// nginx -t opens every key it is pointed at, and the live one is 0600 and
	// owned by the account that runs the proxy. See substituteUnreadableTLS.
	tlsWarns, err := substituteUnreadableTLS(filepath.Join(dir, "tls"))
	warns = append(warns, tlsWarns...)
	if err != nil {
		return dir, warns, err
	}
	return dir, warns, nil
}

// stageParent is where the throwaway copy of the proxy tree is made.
//
// <CtlStateDir>/tmp at 0700, not os.TempDir(): the copy includes tls/, and
// wherever the publishing account CAN read the certificate key the staged tree
// holds a readable copy of it. In /tmp that is a private key under a
// world-traversable directory with a predictable name prefix, kept until the
// process finishes (and until the next reboot if it is killed). Here it is
// under a directory only the control-plane account can enter.
func stageParent(opts Options) (string, error) {
	if opts.StageParent != "" {
		return opts.StageParent, nil
	}
	dir := filepath.Join(opts.Roots.CtlStateDir, "tmp")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("creating the staging parent %s: %w", dir, err)
	}
	// MkdirAll leaves an existing directory's mode alone, so say it explicitly.
	if err := os.Chmod(dir, 0o700); err != nil {
		return "", err
	}
	return dir, nil
}

// copyTree copies src into dst, skipping the named top-level entries.
func copyTree(src, dst string, skipTop map[string]bool) ([]string, error) {
	var warns []string
	if err := os.MkdirAll(dst, 0o750); err != nil {
		return nil, err
	}
	err := filepath.WalkDir(src, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, p)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		if top := strings.SplitN(rel, string(filepath.Separator), 2)[0]; skipTop[top] {
			if d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		target := filepath.Join(dst, rel)
		switch {
		case d.IsDir():
			return os.MkdirAll(target, 0o750)
		case d.Type()&fs.ModeSymlink != 0:
			link, err := os.Readlink(p)
			if err != nil {
				return err
			}
			return os.Symlink(link, target)
		case !d.Type().IsRegular():
			// Sockets and devices have no place in a config tree; skipping one
			// is safer than trying to reproduce it.
			warns = append(warns, "skipped non-regular file "+rel)
			return nil
		}
		if err := copyFile(p, target); err != nil {
			// A file this account cannot read (a key another account owns)
			// becomes a symlink to the original, so the staged config still
			// points at something real.
			if errors.Is(err, os.ErrPermission) {
				warns = append(warns, fmt.Sprintf("%s is not readable by this account; staged as a symlink to the original", rel))
				return os.Symlink(p, target)
			}
			return err
		}
		return nil
	})
	return warns, err
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	st, err := in.Stat()
	if err != nil {
		return err
	}
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, st.Mode().Perm())
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

// rewriteConfPaths replaces every absolute reference to `from` with `to` in
// the staged *.conf files (and nginx.conf itself).
func rewriteConfPaths(root, from, to string) error {
	return filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !d.Type().IsRegular() {
			return err
		}
		if filepath.Ext(p) != ".conf" {
			return nil
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		nb := bytes.ReplaceAll(b, []byte(from), []byte(to))
		if bytes.Equal(nb, b) {
			return nil
		}
		return os.WriteFile(p, nb, 0o640)
	})
}

// nginxTest runs the configuration test inside the nginx image against the
// staged tree. The second bind makes the staging dir visible under its own
// path inside the container, because that is the path the rewritten config
// now names.
func nginxTest(ctx context.Context, opts Options, staged string) (string, error) {
	argv := []string{
		opts.Apptainer, "exec",
		"-B", opts.Roots.RagRoot,
		"-B", staged + ":" + staged,
		opts.NginxSIF,
		"nginx", "-t", "-c", filepath.Join(staged, "nginx.conf"),
	}
	stdout, stderr, err := opts.Exec.Run(ctx, argv)
	out := strings.TrimRight(string(stdout)+string(stderr), "\n")
	return out, err
}

// ---------------------------------------------------------------- switching

// pointCurrentAt repoints `current` at gen-<n> with a rename, which is the
// only atomic way to move a symlink: a remove+create leaves a window in which
// the two published includes resolve to nothing and a concurrent reload
// fails.
func (s State) pointCurrentAt(n int) error {
	if err := os.MkdirAll(s.Dir, 0o2770); err != nil {
		return err
	}
	tmp := filepath.Join(s.Dir, fmt.Sprintf(".current.tmp-%d-%d", os.Getpid(), now().UnixNano()))
	_ = os.Remove(tmp)
	// A RELATIVE target keeps the state dir relocatable (a restore into a
	// different root must not resolve back into the old one).
	if err := os.Symlink("gen-"+strconv.Itoa(n), tmp); err != nil {
		return err
	}
	if err := os.Rename(tmp, s.CurrentLink()); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

// inspectIncludes records what each generated include path in the proxy tree
// looks like right now, so a revert can put it back. It is called — and its
// result made durable in txn.json — BEFORE switchTo touches anything.
func inspectIncludes(proxyDir string) ([]IncludeState, error) {
	out := make([]IncludeState, 0, len(RelPaths()))
	for _, rel := range RelPaths() {
		live := filepath.Join(proxyDir, filepath.FromSlash(rel))
		st, err := os.Lstat(live)
		switch {
		case errors.Is(err, os.ErrNotExist):
			out = append(out, IncludeState{Rel: rel, Kind: IncludeAbsent})
		case err != nil:
			return out, err
		case st.Mode()&fs.ModeSymlink != 0:
			target, err := os.Readlink(live)
			if err != nil {
				return out, err
			}
			out = append(out, IncludeState{Rel: rel, Kind: IncludeSymlink, Target: target})
		default:
			b, err := os.ReadFile(live)
			if err != nil {
				return out, err
			}
			out = append(out, IncludeState{Rel: rel, Kind: IncludeRegular, Mode: uint32(st.Mode().Perm()), Content: b})
		}
	}
	return out, nil
}

// switchTo repoints `current` and makes sure the two paths inside the proxy
// tree are symlinks through it. prior is what inspectIncludes found, already
// recorded in the txn; the notes are what is worth telling the operator.
func switchTo(opts Options, s State, gen *Generation, prev int, prior []IncludeState) ([]string, error) {
	if err := s.pointCurrentAt(gen.N); err != nil {
		return nil, err
	}
	var notes []string
	link := func(live, want string) error {
		tmp := live + fmt.Sprintf(".tmp-%d", os.Getpid())
		_ = os.Remove(tmp)
		if err := os.Symlink(want, tmp); err != nil {
			return err
		}
		if err := os.Rename(tmp, live); err != nil {
			_ = os.Remove(tmp)
			return err
		}
		return nil
	}
	for _, p := range prior {
		live := filepath.Join(opts.Roots.ProxyDir, filepath.FromSlash(p.Rel))
		want := filepath.Join(s.CurrentLink(), filepath.FromSlash(p.Rel))
		switch p.Kind {
		case IncludeAbsent:
			if err := os.MkdirAll(filepath.Dir(live), 0o755); err != nil {
				return notes, err
			}
			if err := link(live, want); err != nil {
				return notes, err
			}
		case IncludeSymlink:
			if p.Target == want {
				continue
			}
			if err := link(live, want); err != nil {
				return notes, err
			}
		default:
			// validateIncludes already proved this regular file is the
			// coconut-proxy bootstrap copy, before the pointer moved.
			if err := link(live, want); err != nil {
				return notes, err
			}
			notes = append(notes, fmt.Sprintf("%s was the coconut-proxy bootstrap copy (a regular file with identical "+
				"configuration); it is now the symlink into %s", p.Rel, s.CurrentLink()))
		}
	}
	return notes, nil
}

// validateIncludes decides whether the switch is allowed to happen at all, and
// runs BEFORE `current` is repointed.
//
// A REGULAR file at an include path is normally somebody's hand edit — the one
// thing generation-based publishing must never overwrite. The exception is the
// BOOTSTRAP: coconut-proxy ships a committed copy of each generated include so
// that a freshly deployed proxy tree loads before the ctl has ever published,
// and deploy.sh writes it as a regular file. That file is a ctl render — its
// ROUTING is identical either to the generation being published or to the one
// already published — and replacing it with the symlink is precisely the
// hand-over this step exists for. Anything else is refused.
//
// Deciding it here rather than mid-switch is what keeps a refusal free of side
// effects: the pointer has not moved, so there is nothing to put back and no
// reason to signal the master.
func validateIncludes(opts Options, s State, gen *Generation, prev int, prior []IncludeState) error {
	for _, p := range prior {
		if p.Kind != IncludeRegular {
			continue
		}
		if !isGeneratedCopy(p.Content, p.Rel, s, gen, prev) {
			return fmt.Errorf("%w: %s is a regular file whose contents are neither the generation "+
				"being published nor the one currently published, so it is a hand edit, not the coconut-proxy "+
				"bootstrap. Move it aside before publishing",
				ErrRefused, filepath.Join(opts.Roots.ProxyDir, filepath.FromSlash(p.Rel)))
		}
	}
	return nil
}

// isGeneratedCopy reports whether b is a ctl-rendered copy of this include:
// the same configuration as the generation being published or as the one
// currently published. The provenance header (timestamp, binary version)
// differs between two renders of one registry and is stripped first.
func isGeneratedCopy(b []byte, rel string, s State, gen *Generation, prev int) bool {
	body := StripHeader(b)
	if bytes.Equal(body, StripHeader(gen.Files[rel])) {
		return true
	}
	// prev, not `current`: the pointer has already been moved to the new
	// generation by the time this runs, so "what was published" has to be
	// named explicitly.
	if prev > 0 {
		if files, err := s.ReadGeneration(prev); err == nil {
			return bytes.Equal(body, StripHeader(files[rel]))
		}
	}
	return false
}

// ---------------------------------------------------------------- reloading

// master is the identified nginx master.
type master struct {
	pid  int
	info ProcInfo
}

// preflightMaster answers "could this publish reload?" WITHOUT signalling
// anything, and is run before the first byte is written.
//
// The same three facts reload() needs — the pidfile parses, the pid is nginx,
// the master is owned by this account — are all readable from /proc, and all of
// them are reasons to refuse. Discovering them after the switch meant a refused
// publish still wrote a generation, moved `current` and rewrote both include
// symlinks before undoing it all.
func preflightMaster(opts Options) (master, error) {
	pid, err := readPID(opts.PIDFile)
	if err != nil {
		return master{}, err
	}
	info, err := opts.Sig.Proc(pid)
	if err != nil {
		return master{pid: pid}, err
	}
	m := master{pid: pid, info: info}
	if !info.Exists {
		return m, fmt.Errorf("%w: %s names pid %d, which is not running", ErrRefused, opts.PIDFile, pid)
	}
	if !looksLikeNginx(info) {
		return m, fmt.Errorf("%w: pid %d is %q, not nginx (stale pidfile %s)", ErrRefused, pid, info.Comm, opts.PIDFile)
	}
	if self := opts.Sig.Self(); info.UID != self {
		return m, fmt.Errorf("%w: nginx master is owned by uid %d; run as that account (this process is uid %d)",
			ErrRefused, info.UID, self)
	}
	return m, nil
}

// reload finds the nginx master, proves it IS nginx and that it is ours, and
// sends SIGHUP. The identity check is not ceremony: a pidfile is a file, the
// pid in it can have been reused, and a HUP to a stranger's process is a bug
// with no error message. It is repeated here rather than trusted from the
// preflight because the master can be restarted between the two.
func reload(opts Options) (int, error) {
	m, err := preflightMaster(opts)
	if err != nil {
		return m.pid, err
	}
	if err := opts.Sig.Signal(m.pid, sigHUP); err != nil {
		return m.pid, fmt.Errorf("SIGHUP to pid %d: %w", m.pid, err)
	}
	return m.pid, nil
}

// witness is what the master looked like immediately before a SIGHUP.
type witness struct {
	pid       int
	startTime uint64
	workers   []int
}

// reloadWitness samples the master's identity and worker set before the HUP.
func reloadWitness(opts Options, pid int) (witness, error) {
	info, err := opts.Sig.Proc(pid)
	if err != nil {
		return witness{pid: pid}, err
	}
	workers, err := opts.Sig.Workers(pid)
	if err != nil {
		return witness{pid: pid, startTime: info.StartTime}, err
	}
	return witness{pid: pid, startTime: info.StartTime, workers: workers}, nil
}

// confirmReload proves the master ACCEPTED the configuration it was HUPed for.
//
// nginx answers a SIGHUP it cannot use by logging `[emerg] …` to error.log,
// keeping the configuration it already had, and carrying on. The master does
// not exit, kill(2) reported success, and every probe afterwards passes
// because the OLD configuration is still serving — so "the HUP worked" is not
// evidence of anything on its own. Two things are:
//
//   - the worker set changes. A reload nginx accepted starts new workers and
//     retires the old ones; a reload it rejected leaves them exactly as they
//     were.
//   - error.log carries no [emerg]/[alert] written after the HUP.
//
// Both are required. The wait is bounded by the probe budget, because a master
// that has not spawned a worker by then has not reloaded.
func confirmReload(opts Options, before witness, hupAt time.Time) error {
	deadline := hupAt.Add(opts.ProbeTimeout)
	var lastWorkers []int
	for {
		info, err := opts.Sig.Proc(before.pid)
		if err != nil {
			return fmt.Errorf("reading the master after the reload: %w", err)
		}
		if !info.Exists {
			return fmt.Errorf("%w: nginx master %d is gone after the reload", ErrRefused, before.pid)
		}
		if before.startTime != 0 && info.StartTime != 0 && info.StartTime != before.startTime {
			return fmt.Errorf("%w: pid %d is not the master that was signalled (it was restarted under us)",
				ErrRefused, before.pid)
		}
		current, err := opts.Sig.Workers(before.pid)
		if err != nil {
			return fmt.Errorf("listing the master's workers after the reload: %w", err)
		}
		lastWorkers = current
		if !equalInts(current, before.workers) {
			break
		}
		if !now().Before(deadline) {
			return fmt.Errorf("%w: nginx master %d kept the same workers %v for %s after the SIGHUP, so it did not take "+
				"the new configuration (it logs [emerg] and keeps the old one rather than failing the signal); "+
				"check %s", ErrRefused, before.pid, lastWorkers, opts.ProbeTimeout, opts.ErrorLog)
		}
		time.Sleep(opts.ProbeInterval)
	}
	if line, found, err := scanErrorLog(opts.ErrorLog, hupAt); err != nil {
		// An unreadable error.log is not proof of a rejection; it is one check
		// that could not run, and the worker set already said the reload took.
		return nil
	} else if found {
		return fmt.Errorf("%w: nginx logged a configuration error after the SIGHUP: %s (%s)", ErrRefused, line, opts.ErrorLog)
	}
	return nil
}

// errorLogStamp is the `2026/09/10 12:34:56` prefix every nginx error.log line
// opens with, followed by its severity in brackets.
var errorLogStamp = regexp.MustCompile(`^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\]`)

// errorLogTail is how much of error.log is read. A rejected reload logs its
// [emerg] within a few lines of the HUP; reading the whole file would mean
// reading a log that is gigabytes on a busy gateway.
const errorLogTail = 64 << 10

// scanErrorLog looks for an [emerg] or [alert] line stamped at or after since.
func scanErrorLog(path string, since time.Time) (string, bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", false, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return "", false, err
	}
	off := st.Size() - errorLogTail
	if off < 0 {
		off = 0
	}
	buf := make([]byte, st.Size()-off)
	if _, err := f.ReadAt(buf, off); err != nil && !errors.Is(err, io.EOF) {
		return "", false, err
	}
	// nginx writes local time with no zone, so the comparison is in local time
	// too. One second of slack: the stamp has second resolution, so a line
	// written in the same second as the HUP rounds down below it.
	cutoff := since.Add(-time.Second)
	for _, line := range strings.Split(string(buf), "\n") {
		m := errorLogStamp.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		if m[2] != "emerg" && m[2] != "alert" {
			continue
		}
		ts, err := time.ParseInLocation("2006/01/02 15:04:05", m[1], time.Local)
		if err != nil || ts.Before(cutoff) {
			continue
		}
		return strings.TrimSpace(line), true, nil
	}
	return "", false, nil
}

func equalInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func looksLikeNginx(info ProcInfo) bool {
	if strings.Contains(info.Comm, "nginx") {
		return true
	}
	for _, a := range info.Cmdline {
		if strings.Contains(a, "nginx") {
			return true
		}
	}
	return false
}

func readPID(path string) (int, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return 0, fmt.Errorf("reading the nginx pidfile: %w", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 0 {
		return 0, fmt.Errorf("%w: %s does not contain a pid (%q)", ErrRefused, path, strings.TrimSpace(string(b)))
	}
	return pid, nil
}

// ---------------------------------------------------------------- probing

// namesJSON pulls $tenants_names_json out of a rendered generation. The
// expected tenant list is taken from the BYTES that were published rather
// than recomputed from the registry: the probe asks "is the gateway serving
// this generation", and a second derivation of the same list could differ
// from it and would then be testing itself.
var namesJSON = regexp.MustCompile(`map \$host \$tenants_names_json \{\n\s*default\s+'(\[[^']*\])';`)

// tenantsBody is the shape of /ragstack/tenants.
type tenantsBody struct {
	Tenants []struct {
		Name string `json:"name"`
	} `json:"tenants"`
}

// probe polls the gateway until it answers the DESIRED state or the budget
// runs out. Desired state, not "any 200": the tenant list must be the one
// this generation advertises, an active tenant's health must be 200, and a
// tenant the registry says is down must be answered as down rather than
// silently routed somewhere else.
func probe(ctx context.Context, opts Options, f *registry.Fleet, gen *Generation) error {
	want, err := expectedNames(gen)
	if err != nil {
		return err
	}
	deadline := now().Add(opts.ProbeTimeout)
	var last error
	for {
		last = probeOnce(ctx, opts, f, want)
		if last == nil {
			return nil
		}
		if !now().Before(deadline) {
			return fmt.Errorf("gateway did not reach the desired state within %s: %w", opts.ProbeTimeout, last)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(opts.ProbeInterval):
		}
	}
}

// probeGeneration polls until the gateway answers what THIS generation's own
// bytes advertise: its tenant list, every tenant in it actually routed (a 404
// means the route is not there at all), and the golden bodies.
//
// It is the rollback's probe. `probe` above asks "does the gateway serve what
// the REGISTRY wants", which is the right question for a publish and the wrong
// one for a generation rendered weeks ago: a tenant stopped since then would
// make the rollback fail on a difference that is the point of rolling back.
func probeGeneration(ctx context.Context, opts Options, gen *Generation) error {
	want, err := expectedNames(gen)
	if err != nil {
		return err
	}
	deadline := now().Add(opts.ProbeTimeout)
	var last error
	for {
		last = probeGenerationOnce(ctx, opts, want)
		if last == nil {
			return nil
		}
		if !now().Before(deadline) {
			return fmt.Errorf("gateway did not answer generation %d's routing within %s: %w", gen.N, opts.ProbeTimeout, last)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(opts.ProbeInterval):
		}
	}
}

func probeGenerationOnce(ctx context.Context, opts Options, want []string) error {
	if err := probeTenantList(ctx, opts, want); err != nil {
		return err
	}
	for _, name := range want {
		status, _, err := opts.Prober.Get(ctx, opts.BaseURL+"/ragstack/"+name+"/api/health")
		if err != nil {
			return fmt.Errorf("GET /ragstack/%s/api/health: %w", name, err)
		}
		// Not "200": the generation says the route EXISTS, not that whatever is
		// behind it is up. 404 is the gateway saying it has no such route,
		// which is the one answer that contradicts the generation.
		if status == 404 {
			return fmt.Errorf("GET /ragstack/%s/api/health: 404 — generation lists %s but the gateway has no route for it",
				name, name)
		}
	}
	return probeGoldenBodies(ctx, opts)
}

func probeTenantList(ctx context.Context, opts Options, want []string) error {
	status, body, err := opts.Prober.Get(ctx, opts.BaseURL+"/ragstack/tenants")
	if err != nil {
		return fmt.Errorf("GET /ragstack/tenants: %w", err)
	}
	if status != 200 {
		return fmt.Errorf("GET /ragstack/tenants: %d", status)
	}
	var tb tenantsBody
	if err := json.Unmarshal(bytes.TrimSpace(body), &tb); err != nil {
		return fmt.Errorf("GET /ragstack/tenants: body is not the tenant table: %w", err)
	}
	got := make([]string, 0, len(tb.Tenants))
	for _, t := range tb.Tenants {
		got = append(got, t.Name)
	}
	if !equalStrings(got, want) {
		return fmt.Errorf("GET /ragstack/tenants lists %v, the published generation says %v", got, want)
	}
	return nil
}

// probeGoldenBodies is PR-B's go/no-go: moving the maps into a generated
// include must change nothing a client can see.
func probeGoldenBodies(ctx context.Context, opts Options) error {
	for _, p := range sortedKeys(opts.ExpectBodies) {
		wantBody := opts.ExpectBodies[p]
		_, body, err := opts.Prober.Get(ctx, opts.BaseURL+p)
		if err != nil {
			return fmt.Errorf("GET %s: %w", p, err)
		}
		if bytes.Equal(body, wantBody) {
			continue
		}
		if bytes.Equal(bytes.TrimRight(body, "\n"), bytes.TrimRight(wantBody, "\n")) {
			return fmt.Errorf("GET %s: body differs from the golden only in trailing newlines", p)
		}
		return fmt.Errorf("GET %s: body differs from the golden:\n got: %s\nwant: %s", p, body, wantBody)
	}
	return nil
}

func expectedNames(gen *Generation) ([]string, error) {
	m := namesJSON.FindSubmatch(gen.Files[FileTenants])
	if m == nil {
		return nil, fmt.Errorf("gateway: the rendered %s carries no $tenants_names_json", FileTenants)
	}
	var names []string
	if err := json.Unmarshal(m[1], &names); err != nil {
		return nil, fmt.Errorf("gateway: $tenants_names_json is not a JSON array: %w", err)
	}
	return names, nil
}

func probeOnce(ctx context.Context, opts Options, f *registry.Fleet, want []string) error {
	if err := probeTenantList(ctx, opts, want); err != nil {
		return err
	}

	// Per tenant, from the registry's desired state.
	routed := map[string]bool{}
	for _, n := range want {
		routed[n] = true
	}
	for _, name := range sortedKeys(f.Tenants) {
		if !routed[name] {
			continue
		}
		t := f.Tenants[name]
		url := opts.BaseURL + "/ragstack/" + name + "/api/health"
		status, _, err := opts.Prober.Get(ctx, url)
		if err != nil {
			return fmt.Errorf("GET /ragstack/%s/api/health: %w", name, err)
		}
		if t.State == "active" {
			if status != 200 {
				return fmt.Errorf("GET /ragstack/%s/api/health: %d, want 200 (registry state active)", name, status)
			}
			continue
		}
		// A tenant the registry does not call active must be answered as
		// down. 502 is "routed, nothing listening"; 404 is "no such route".
		// Anything else means the gateway is sending its traffic somewhere.
		if status != 502 && status != 404 {
			return fmt.Errorf("GET /ragstack/%s/api/health: %d, want 502 or 404 (registry state %s)", name, status, t.State)
		}
	}
	return probeGoldenBodies(ctx, opts)
}

// LoadExpectBodies reads the golden gateway bodies from a directory laid out
// like testdata/live-2026-09-10/gateway.
func LoadExpectBodies(dir string) (map[string][]byte, error) {
	out := map[string][]byte{}
	for file, path := range ExpectPaths {
		b, err := os.ReadFile(filepath.Join(dir, file))
		if err != nil {
			return nil, err
		}
		out[path] = b
	}
	return out, nil
}

// ---------------------------------------------------------------- pruning

// prune removes generations older than the last keep, never the current one
// and never the one a revert would go back to.
func prune(s State, keep, current, previous int) []int {
	gens := s.Generations()
	if len(gens) <= keep {
		return nil
	}
	protect := map[int]bool{current: true, previous: true}
	for _, n := range gens[len(gens)-keep:] {
		protect[n] = true
	}
	var removed []int
	for _, n := range gens {
		if protect[n] {
			continue
		}
		if err := os.RemoveAll(s.GenDir(n)); err == nil {
			removed = append(removed, n)
		}
	}
	return removed
}

// ---------------------------------------------------------------- rollback

// Rollback repoints `current` at an earlier generation, reloads and verifies
// it, and goes back to where it started if that fails.
//
// It is a publish of a generation that already exists, and it runs the same
// steps for the same reasons: stage the TARGET's bytes and `nginx -t` them
// (the proxy tree around them has moved since they were written — a snippet
// renamed, a certificate replaced — so "it loaded once" is not "it loads"),
// switch, HUP, confirm the master took it, and probe. What it does NOT do is
// judge the target against today's registry: an old generation routes what it
// routed, and the desired state to check it against is the one its own bytes
// declare.
//
// Targets are limited to generations that actually reached `verified` (see
// VerifiedName). A generation that was published and reverted is on disk and
// is not a resting place.
//
// The only case that skips the sequence is a target the durable record proves
// nginx was CONFIRMED on (nginxConfirmedOn). `current` pointing at the target
// is not that proof — see the comment at the shortcut.
func Rollback(ctx context.Context, f *registry.Fleet, to int, opts Options) (*Result, error) {
	opts.defaults()
	st := NewState(opts.Roots)
	res := &Result{}

	lock, err := st.lock()
	if err != nil {
		res.step("lock", err, "")
		return res, err
	}
	defer lock.unlock()

	// The record is read BEFORE the repair, because the repair is one of the
	// things that makes the pointer stop meaning what it says: it moves
	// `current` back to the previous generation without signalling anything, so
	// afterwards the pointer names a generation the running workers are not
	// serving. Whether the shortcut below is allowed is decided from this
	// record, not from what the pointer looks like once repair has been at it.
	txn, haveTxn, err := st.ReadTxn()
	if err != nil {
		return res, err
	}
	rep, err := st.repair()
	if err != nil {
		res.step("repair", err, "")
		return res, err
	}
	if rep.Incomplete {
		res.Warnings = append(res.Warnings, "the previous publication was incomplete: "+rep.Action)
		opts.Log("repair", rep.Action)
	}
	if to == 0 {
		if !haveTxn || txn.PreviousGeneration == 0 {
			return res, fmt.Errorf("%w: no previous generation is recorded; pass --to N (available: %v)", ErrRefused, st.Generations())
		}
		to = txn.PreviousGeneration
	}
	if _, err := os.Stat(filepath.Join(st.GenDir(to), ManifestName)); err != nil {
		return res, fmt.Errorf("%w: generation %d is not on disk (available: %v)", ErrRefused, to, st.Generations())
	}
	if !st.WasVerified(to) {
		verified, _ := st.VerifiedGenerations()
		return res, fmt.Errorf("%w: generation %d never reached `verified`, so rolling back to it is not going back to a "+
			"state this host is known to have served (verified: %v)", ErrRefused, to, verified)
	}
	// `current` already naming the target is NOT on its own a reason to do
	// nothing: the pointer is where the next reload will READ from, never proof
	// of what the running workers are serving.
	//
	// The case that made this a bug: gen-2 is published, the master is HUPed and
	// the workers pick gen-2 up, and the process dies before the txn reaches
	// `verified`. The rollback's own st.repair() then moves `current` from gen-2
	// back to gen-1 — pointer only, repair deliberately never signals — and the
	// shortcut saw cur == to and reported `rolled_back` without a HUP. The
	// gateway went on serving gen-2 while the pointer and the result both said
	// gen-1, which is the exact state a rollback exists to leave nobody in.
	//
	// So the shortcut needs evidence about nginx, and the only durable evidence
	// is a COMPLETE transaction that reached one of the two states reached after
	// confirmReload and a probe both passed, naming this generation.
	cur := st.CurrentGeneration()
	if cur == to && nginxConfirmedOn(txn, haveTxn, to) {
		res.Generation, res.PreviousGeneration = to, cur
		res.State = TxnRolledBack
		res.step("switch", nil, fmt.Sprintf("current already points at gen-%d and the last transaction (%s, finished %s) "+
			"confirmed nginx on it; nothing to do", to, txn.State, txn.FinishedAt))
		return res, nil
	}
	if cur == to {
		res.Warnings = append(res.Warnings, fmt.Sprintf("current already points at gen-%d, but no completed publication "+
			"confirmed nginx on it (the last record is %s), so this rollback stages, reloads and verifies gen-%d rather "+
			"than trusting the pointer", to, describeTxn(txn, haveTxn), to))
	}

	files, err := st.ReadGeneration(to)
	if err != nil {
		return res, err
	}
	man, err := readManifest(st, to)
	if err != nil {
		return res, err
	}
	gen := &Generation{N: to, RegistryGeneration: man.RegistryGeneration, Files: files}
	res.Generation, res.PreviousGeneration, res.RegistryGeneration = to, cur, man.RegistryGeneration
	res.SHA256 = gen.SHA256()

	roll := &Txn{
		Generation:         to,
		RegistryGeneration: man.RegistryGeneration,
		StartedAt:          now().UTC().Format(time.RFC3339),
		PublishedBy:        opts.By,
		PreviousGeneration: cur,
	}
	if err := st.transition(roll, TxnStaging, nil); err != nil {
		return res, err
	}
	// The master has to be ours before anything moves, exactly as in Publish.
	m, err := preflightMaster(opts)
	res.NginxPID, roll.NginxPID = m.pid, m.pid
	if err != nil {
		res.step("preflight", err, "")
		_ = st.transition(roll, TxnFailed, err)
		res.State = TxnFailed
		return res, err
	}

	// ---- stage the TARGET and test it against today's proxy tree ---------
	staged, warns, err := stage(opts, gen)
	res.StagedDir, res.Warnings = staged, append(res.Warnings, warns...)
	if err == nil {
		res.ConfigTest, err = nginxTest(ctx, opts, staged)
	}
	if !opts.KeepStage && staged != "" {
		defer func() { _ = os.RemoveAll(staged) }()
	}
	ok := err == nil
	roll.ConfigOK = &ok
	if err != nil {
		res.step("nginx -t", err, strings.TrimSpace(res.ConfigTest+"\n"+err.Error()))
		_ = st.transition(roll, TxnFailed, err)
		res.State = TxnFailed
		return res, fmt.Errorf("staged nginx -t of generation %d failed, so it was not switched to: %w", to, err)
	}
	res.step("nginx -t", nil, fmt.Sprintf("gen-%d tests clean against the current proxy tree", to))

	prior, err := inspectIncludes(opts.Roots.ProxyDir)
	if err == nil {
		err = validateIncludes(opts, st, gen, cur, prior)
	}
	if err != nil {
		res.step("switch", err, "")
		_ = st.transition(roll, TxnFailed, err)
		res.State = TxnFailed
		return res, err
	}
	roll.Includes = prior
	if err := st.transition(roll, TxnSwitching, nil); err != nil {
		res.State = TxnFailed
		return res, err
	}

	// Every failure from here puts `current` back on the generation the
	// rollback started from and reloads again, the same way a failed publish
	// does — a rollback that fails halfway is the worst of both configurations.
	fail := func(step string, err error) (*Result, error) {
		res.step(step, err, "")
		rerr := revert(ctx, opts, st, roll, res, gen)
		res.Reverted, res.State = true, TxnRollbackFailed
		_ = st.transition(roll, TxnRollbackFailed, err)
		if rerr != nil {
			return res, fmt.Errorf("rollback to generation %d failed at %s (%w) and the return to generation %d did not "+
				"complete: %v", to, step, err, cur, rerr)
		}
		return res, fmt.Errorf("rollback to generation %d failed at %s, back on generation %d: %w", to, step, cur, err)
	}

	notes, err := switchTo(opts, st, gen, cur, prior)
	res.Warnings = append(res.Warnings, notes...)
	if err != nil {
		return fail("switch", err)
	}
	res.step("switch", nil, fmt.Sprintf("current -> gen-%d", to))
	if err := st.transition(roll, TxnSwitched, nil); err != nil {
		return fail("txn", err)
	}

	before, beforeErr := reloadWitness(opts, roll.NginxPID)
	hupAt := now()
	pid, err := reload(opts)
	res.NginxPID, roll.NginxPID = pid, pid
	if err != nil {
		return fail("reload", err)
	}
	roll.ReloadedAt = hupAt.UTC().Format(time.RFC3339)
	res.step("reload", nil, fmt.Sprintf("SIGHUP to nginx master pid %d", pid))
	if beforeErr != nil {
		res.Warnings = append(res.Warnings, "could not sample the master's workers before the reload ("+
			beforeErr.Error()+"); the reload was NOT confirmed")
	} else if err := confirmReload(opts, before, hupAt); err != nil {
		return fail("reload", err)
	}
	if err := st.transition(roll, TxnReloaded, nil); err != nil {
		return fail("txn", err)
	}

	// ---- probe the TARGET's own desired state ---------------------------
	if err := probeGeneration(ctx, opts, gen); err != nil {
		return fail("probe", err)
	}
	res.step("probe", nil, fmt.Sprintf("the gateway answers what gen-%d advertises", to))
	if err := st.transition(roll, TxnRolledBack, nil); err != nil {
		return fail("txn", err)
	}
	res.State = TxnRolledBack
	_ = f // the registry is deliberately NOT the yardstick here; see the doc comment.
	return res, nil
}

// nginxConfirmedOn reports whether the durable record proves the RUNNING nginx
// was confirmed on generation n.
//
// Only two states qualify, and both are terminal and are only ever written
// AFTER confirmReload proved the master replaced its workers with no
// [emerg]/[alert] and after the probes passed:
//
//   - `verified`: a publish of n reached the end.
//   - `rolled_back`: a rollback TO n reached the end (confirmReload plus
//     probeGeneration against n's own bytes).
//
// Everything else — an incomplete record, `switched`/`reloaded` from a crashed
// publish, `reverted` (which includes the one `gateway repair --acknowledge`
// writes), `failed`, `rollback_failed`, or a terminal record naming a DIFFERENT
// generation — says nothing about what the workers loaded, so it is not a
// reason to skip a reload.
func nginxConfirmedOn(t *Txn, ok bool, n int) bool {
	if !ok || !t.Complete() || t.Generation != n {
		return false
	}
	return t.State == TxnVerified || t.State == TxnRolledBack
}

// describeTxn is the record in one phrase, for the warning that explains why a
// rollback is doing the full sequence over a pointer that already agrees.
func describeTxn(t *Txn, ok bool) string {
	if !ok {
		return "absent"
	}
	state := t.State
	if state == "" {
		state = "unknown"
	}
	if !t.Complete() {
		return fmt.Sprintf("an INCOMPLETE %s of gen-%d", state, t.Generation)
	}
	return fmt.Sprintf("%s of gen-%d", state, t.Generation)
}

func readManifest(s State, n int) (Manifest, error) {
	var m Manifest
	b, err := os.ReadFile(filepath.Join(s.GenDir(n), ManifestName))
	if err != nil {
		return m, err
	}
	return m, json.Unmarshal(b, &m)
}

// ---------------------------------------------------------------- helpers

// dedupe returns the distinct, non-empty values in order.
func dedupe(vals ...string) []string {
	seen := map[string]bool{}
	var out []string
	for _, v := range vals {
		if v == "" || seen[v] {
			continue
		}
		seen[v] = true
		out = append(out, v)
	}
	return out
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
