package gateway

import (
	"context"
	"fmt"
	"os"
	"strings"
)

// Reload makes the running nginx re-read the configuration it is ALREADY
// pointed at — with the same proof a publish gets, and without publishing
// anything.
//
// It exists because the generated includes are not the only thing in the
// proxy tree. A hand edit in the coconut-proxy repo (a new header, a changed
// upstream, a TLS rotation) has to reach the running master somehow, and the
// two ways it could reach it today were `gateway apply`, which publishes a
// generation nobody asked for, and a bare `kill -HUP`, which proves nothing:
// nginx answers a SIGHUP it cannot use by logging `[emerg]`, keeping the old
// configuration and carrying on, so the signal succeeding says nothing about
// whether the edit took. This runs the same staged `nginx -t`, the same
// master preflight, the same worker-set confirmation and the same probes that
// a publish runs, against the generation that is already current.
//
// What it deliberately does NOT do: render, write a generation directory,
// move the `current` pointer, or write txn.json. The published generation
// before and after a Reload is the same number, and the durable record of
// what was published stays the record of the last PUBLICATION — a reload is
// not one, and a txn.json saying otherwise would let a later rollback skip a
// signal on the strength of a transaction that never switched anything.
//
// The steps are render (read the live generation) → staged `nginx -t` →
// preflight the master → HUP → confirm → probe. A dry run stops after the
// configuration test, and it deliberately does not need to own the master:
// "would this tree load?" is a question a read-only account may ask.
//
// It holds the gateway lock for the whole run, so it cannot interleave with a
// publish, a rollback or a repair.
func Reload(ctx context.Context, opts Options) (*Result, error) {
	opts.defaults()
	st := NewState(opts.Roots)
	res := &Result{DryRun: opts.DryRun}

	lock, err := st.lock()
	if err != nil {
		res.step("lock", err, "")
		return res, err
	}
	defer lock.unlock()

	// The LIVE generation, not a fresh render: a reload's job is to load what
	// is published, and rendering here would silently arm a registry change
	// nobody published.
	n := st.CurrentGeneration()
	if n == 0 {
		err := fmt.Errorf("%w: nothing is published (no `current` generation), so there is no gateway configuration to "+
			"reload; run `ragstack-ctl gateway apply` first", ErrRefused)
		res.step("render", err, "")
		res.State = TxnFailed
		return res, err
	}
	files, err := st.ReadGeneration(n)
	if err != nil {
		res.step("render", err, "")
		res.State = TxnFailed
		return res, err
	}
	man, err := readManifest(st, n)
	if err != nil {
		res.step("render", err, "")
		res.State = TxnFailed
		return res, err
	}
	gen := &Generation{N: n, RegistryGeneration: man.RegistryGeneration, Files: files}
	res.Generation, res.PreviousGeneration = n, n
	res.RegistryGeneration, res.SHA256 = man.RegistryGeneration, gen.SHA256()
	res.step("render", nil, fmt.Sprintf("the live generation gen-%d (registry generation %d, %s) — nothing is rendered "+
		"and nothing is published", n, man.RegistryGeneration, gen.SHA256()))
	opts.Log("render", res.Steps[len(res.Steps)-1].Detail)

	// ---- stage the LIVE tree + nginx -t ----------------------------------
	//
	// The staged copy is the whole proxy tree as it stands — which is the
	// point: the change being reloaded is IN that tree, and testing the
	// generation alone would test the one part of the configuration this
	// operation is not about.
	staged, warns, err := stage(opts, gen)
	res.StagedDir, res.Warnings = staged, append(res.Warnings, warns...)
	if err == nil {
		res.ConfigTest, err = nginxTest(ctx, opts, staged)
	}
	if !opts.KeepStage && staged != "" {
		defer func() { _ = os.RemoveAll(staged) }()
	}
	if err != nil {
		res.step("nginx -t", err, strings.TrimSpace(res.ConfigTest+"\n"+err.Error()))
		res.State = TxnFailed
		return res, fmt.Errorf("staged nginx -t failed, so nothing was reloaded: %w", err)
	}
	res.step("nginx -t", nil, "configuration test passed against a staged copy of the live tree")
	opts.Log("nginx -t", "passed")

	if opts.DryRun {
		res.State = "dry-run"
		res.step("preflight", nil, "skipped (--dry-run)")
		res.step("reload", nil, "skipped (--dry-run)")
		res.step("confirm reload", nil, "skipped (--dry-run)")
		res.step("probe", nil, "skipped (--dry-run)")
		return res, nil
	}

	// ---- the master is ours ---------------------------------------------
	m, err := preflightMaster(opts)
	res.NginxPID = m.pid
	if err != nil {
		res.step("preflight", err, "")
		res.State = TxnFailed
		return res, err
	}
	res.step("preflight", nil, fmt.Sprintf("nginx master pid %d (%s) is owned by this account", m.pid, m.info.Comm))

	// ---- HUP + confirm ---------------------------------------------------
	before, beforeErr := reloadWitness(opts, m.pid)
	hupAt := now()
	pid, err := reload(opts)
	res.NginxPID = pid
	if err != nil {
		res.step("reload", err, "")
		res.State = TxnFailed
		return res, err
	}
	res.step("reload", nil, fmt.Sprintf("SIGHUP to nginx master pid %d", pid))
	opts.Log("reload", res.Steps[len(res.Steps)-1].Detail)

	if beforeErr != nil {
		msg := "could not sample the master's workers before the reload (" + beforeErr.Error() + "); the reload was NOT confirmed"
		res.Warnings = append(res.Warnings, msg)
		res.step("confirm reload", nil, msg)
	} else if err := confirmReload(opts, before, hupAt); err != nil {
		res.step("confirm reload", err, "")
		res.State = TxnFailed
		// Nothing moved, so there is nothing to revert: the gateway is on the
		// configuration it was on before, which is what a rejected reload
		// leaves behind by construction.
		return res, fmt.Errorf("the master did not take the configuration: %w", err)
	} else {
		res.step("confirm reload", nil, "the master spawned new workers and logged no [emerg]/[alert]")
		opts.Log("confirm reload", "the master took the new configuration")
	}

	// ---- probe THIS generation's own routing -----------------------------
	//
	// probeGeneration, not probe: the yardstick is what the published
	// generation advertises, not what the registry wants today. A reload that
	// failed because a tenant was stopped since the generation was published
	// would be a false alarm about a change this operation did not make.
	if err := probeGeneration(ctx, opts, gen); err != nil {
		res.step("probe", err, "")
		res.State = TxnFailed
		return res, err
	}
	res.step("probe", nil, fmt.Sprintf("the gateway answers what gen-%d advertises", n))
	opts.Log("probe", "ok")
	res.State = TxnReloaded
	return res, nil
}
