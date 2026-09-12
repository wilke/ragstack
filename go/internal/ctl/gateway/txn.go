package gateway

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// Publication states, in the order a successful publish passes through them.
// Every transition is written to txn.json before the step it names is
// attempted or immediately after it succeeds, so the file always describes a
// point the process actually reached.
const (
	TxnStaging = "staging" // gen dir written, staged nginx -t running
	// TxnSwitching is written BEFORE the pointer is touched, not after.
	//
	// Without it a crash INSIDE switchTo — `current` already moved, the include
	// symlinks half rewritten — left txn.json saying `staging`, and Repair read
	// that as "the switch never happened" and did nothing. The state that says
	// "the pointer may have moved" has to be durable before the move, or the
	// record is a lie for exactly the window in which it matters.
	TxnSwitching      = "switching"
	TxnSwitched       = "switched"        // `current` repointed; the live includes resolve to the new generation
	TxnReloaded       = "reloaded"        // the master took SIGHUP and accepted the configuration
	TxnVerified       = "verified"        // probes from desired state passed — the terminal success state
	TxnReverted       = "reverted"        // a failure after the switch put `current` back
	TxnFailed         = "failed"          // a failure before the switch; `current` never moved
	TxnRolledBack     = "rolled_back"     // a rollback reached its target and verified it
	TxnRollbackFailed = "rollback_failed" // a rollback failed and went back to where it started
)

// Include states recorded in the txn before a switch.
const (
	IncludeAbsent  = "absent"
	IncludeSymlink = "symlink"
	IncludeRegular = "regular"
)

// IncludeState is what one generated include path in the PROXY tree looked
// like before the switch touched it.
//
// It exists so a revert can put the file back rather than remove it. The proxy
// configuration includes both paths unconditionally (routes.conf includes the
// static snippet; 10-gateway.conf reads `$tenant_api`), so an include that is
// missing — or a symlink to a `current` that was deleted — is not a clean
// rollback, it is an nginx that will not start at the next reload.
type IncludeState struct {
	Rel string `json:"rel"`
	// Kind is absent | symlink | regular.
	Kind string `json:"kind"`
	// Target is the symlink's target, for Kind == symlink.
	Target string `json:"target,omitempty"`
	// Mode and Content are the regular file's permission bits and bytes, for
	// Kind == regular. The bytes are the coconut-proxy bootstrap copy, a few
	// kilobytes of generated configuration — small enough to carry in the
	// record, and the only place they still exist once the symlink replaced
	// them.
	Mode    uint32 `json:"mode,omitempty"`
	Content []byte `json:"content,omitempty"`
}

// Txn is the durable record of the last publication attempt.
//
// FinishedAt is the crash detector: it is written only together with a
// terminal state, so a txn without it means the process died mid-publication
// and Repair has work to do.
type Txn struct {
	Generation         int    `json:"generation"`
	RegistryGeneration int64  `json:"registry_generation"`
	StartedAt          string `json:"started_at"`
	FinishedAt         string `json:"finished_at"`
	State              string `json:"state"`
	PublishedBy        string `json:"published_by"`
	PreviousGeneration int    `json:"previous_generation"`
	NginxPID           int    `json:"nginx_pid"`
	Error              string `json:"error"`

	// ConfigOK is the result of the staged `nginx -t` of this attempt; the
	// contract's nginx.config_ok reads it. Null (absent) until the test ran.
	ConfigOK *bool `json:"config_ok,omitempty"`
	// ReloadedAt is when the master was signalled; the contract's
	// nginx.last_reload_at reads it.
	ReloadedAt string `json:"reloaded_at,omitempty"`
	// Repaired marks a txn an operator has acknowledged with
	// `ragstack-ctl gateway repair`. Repair deliberately does NOT set it: the
	// incomplete publication is a fact about the host that should keep showing
	// on the dashboard until a human has looked at it.
	Repaired bool `json:"repaired,omitempty"`
	// Includes is what the two include paths in the proxy tree looked like
	// before this attempt's switch. See IncludeState.
	Includes []IncludeState `json:"includes,omitempty"`
}

// Complete reports whether the attempt reached a terminal state.
func (t *Txn) Complete() bool { return t != nil && t.FinishedAt != "" }

// ReadTxn reads txn.json. ok is false when no publication has ever been
// attempted.
func (s State) ReadTxn() (*Txn, bool, error) {
	b, err := os.ReadFile(s.TxnPath())
	if errors.Is(err, os.ErrNotExist) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	var t Txn
	if err := json.Unmarshal(b, &t); err != nil {
		return nil, false, fmt.Errorf("%s: %w", s.TxnPath(), err)
	}
	return &t, true, nil
}

// WriteTxn replaces txn.json atomically.
func (s State) WriteTxn(t *Txn) error {
	b, err := json.MarshalIndent(t, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(s.TxnPath(), append(b, '\n'), 0o660)
}

// terminal reports whether a state is one a publication rests in.
func terminal(state string) bool {
	switch state {
	case TxnVerified, TxnReverted, TxnFailed, TxnRolledBack, TxnRollbackFailed:
		return true
	}
	return false
}

// txnWriteHook is a TEST SEAM. When set, transition returns its error instead
// of writing txn.json, which is the only way to exercise "the pointer moved but
// the record could not be updated" — the path that used to return without
// reverting. Nil in production; nothing outside this package's own tests
// assigns it.
var txnWriteHook func(state string) error

// transition records one step of a publication. terminal states carry
// finished_at; everything else leaves it empty so a crash is detectable.
func (s State) transition(t *Txn, state string, err error) error {
	t.State = state
	if err != nil {
		t.Error = err.Error()
	}
	if terminal(state) {
		t.FinishedAt = now().UTC().Format(time.RFC3339)
	} else {
		t.FinishedAt = ""
	}
	if txnWriteHook != nil {
		if herr := txnWriteHook(state); herr != nil {
			return herr
		}
	}
	return s.WriteTxn(t)
}

// VerifiedName is the list of generations a publication actually VERIFIED.
//
// `current` and txn.json between them cannot answer "was gen-3 ever good?":
// txn.json only remembers the LAST attempt, and a generation dir on disk only
// proves it was rendered. A rollback target has to be a generation that
// reached `verified` at some point, so that fact is recorded separately and
// append-only.
const VerifiedName = "verified.json"

// VerifiedGenerations lists the generations that ever reached `verified`,
// ascending. An absent file means none have — which is a fact, not an error.
func (s State) VerifiedGenerations() ([]int, error) {
	b, err := os.ReadFile(filepath.Join(s.Dir, VerifiedName))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []int
	if err := json.Unmarshal(b, &out); err != nil {
		return nil, fmt.Errorf("%s: %w", filepath.Join(s.Dir, VerifiedName), err)
	}
	sort.Ints(out)
	return out, nil
}

// WasVerified reports whether generation n ever reached `verified`.
func (s State) WasVerified(n int) bool {
	gens, err := s.VerifiedGenerations()
	if err != nil {
		return false
	}
	for _, g := range gens {
		if g == n {
			return true
		}
	}
	return false
}

// markVerified adds n to the verified list.
func (s State) markVerified(n int) error {
	gens, err := s.VerifiedGenerations()
	if err != nil {
		// A corrupt list is replaced rather than propagated: the alternative is
		// a host on which nothing can ever be published again.
		gens = nil
	}
	for _, g := range gens {
		if g == n {
			return nil
		}
	}
	gens = append(gens, n)
	sort.Ints(gens)
	b, err := json.Marshal(gens)
	if err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(s.Dir, VerifiedName), append(b, '\n'), 0o660)
}

// RepairReport is what Repair found and did.
type RepairReport struct {
	Incomplete         bool   `json:"incomplete"`
	Generation         int    `json:"generation"`
	PreviousGeneration int    `json:"previous_generation"`
	State              string `json:"state"`
	CurrentGeneration  int    `json:"current_generation"`
	Action             string `json:"action"`
}

// Repair makes the published state consistent with the durable record.
//
// It WRITES — it moves `current` — so it takes the gateway lock for its whole
// duration, and it is not something a read may do on the side. GET /v1/gateway
// used to call it: a viewer polling a dashboard repointed the pointer nginx
// resolves its includes through, with no lock, while an operator's publish was
// in flight. Status is read-only now and reports `incomplete`; putting the
// pointer back is this function, and an operator runs it.
//
// An incomplete txn means the process died somewhere between the pointer
// switch and the verification that the switch was good. The only safe resting
// place is the generation that WAS verified — the previous one — so `current`
// goes back there.
//
// Nothing is reloaded, by any caller. Repair does not signal: it makes the
// pointer honest and stops, and the running workers keep serving whatever they
// last loaded until somebody reloads the proxy. `ragstack-ctl gateway repair`
// PRINTS that command rather than running it — a repair is run on a host whose
// state nobody understands yet, and reloading nginx there is a decision for the
// operator, not a side effect of asking what happened.
//
// It deliberately does not rewrite txn.json. The record of an incomplete
// publication is a fact about the host, and Status keeps reporting
// `incomplete` until a human acknowledges it (Acknowledge, below).
func (s State) Repair() (*RepairReport, error) {
	l, err := s.lock()
	if err != nil {
		return nil, err
	}
	defer l.unlock()
	return s.repair()
}

// repair is Repair with the lock already held.
func (s State) repair() (*RepairReport, error) {
	t, ok, err := s.ReadTxn()
	if err != nil {
		return nil, err
	}
	rep := &RepairReport{CurrentGeneration: s.CurrentGeneration(), Action: "none"}
	if !ok {
		return rep, nil
	}
	rep.Generation, rep.PreviousGeneration, rep.State = t.Generation, t.PreviousGeneration, t.State
	if t.Complete() {
		return rep, nil
	}
	rep.Incomplete = true
	// The question is not what state the record REACHED but where the pointer
	// is: a crash inside switchTo leaves a txn that never got past `switching`
	// and a `current` that already moved. Keying on the pointer answers both
	// that case and the honest `staging` one (where current is still on the old
	// generation and nothing has to be put back) with the same test.
	if rep.CurrentGeneration != t.Generation {
		rep.Action = fmt.Sprintf("none (current is gen-%d, not the incomplete gen-%d)", rep.CurrentGeneration, t.Generation)
		return rep, nil
	}
	if t.PreviousGeneration == 0 {
		// The very first publication crashed after switching. There is no
		// verified generation to fall back to; leaving the pointer where it is
		// beats pointing it at nothing, because the include symlinks would
		// then dangle and nginx would refuse to load at the next reload.
		rep.Action = "kept (no previously verified generation to fall back to)"
		return rep, nil
	}
	if err := s.pointCurrentAt(t.PreviousGeneration); err != nil {
		return rep, err
	}
	rep.CurrentGeneration = t.PreviousGeneration
	rep.Action = fmt.Sprintf("current repointed to gen-%d (last verified)", t.PreviousGeneration)
	return rep, nil
}

// Acknowledge marks an incomplete txn as handled by a human, which is what
// turns the contract's txn_state from `incomplete` into `repaired`.
func (s State) Acknowledge(by string) error {
	l, err := s.lock()
	if err != nil {
		return err
	}
	defer l.unlock()
	t, ok, err := s.ReadTxn()
	if err != nil || !ok {
		return err
	}
	if t.Complete() {
		return nil
	}
	t.Repaired = true
	t.PublishedBy = by
	return s.transition(t, TxnReverted, errors.New("publication was incomplete; repaired by "+by))
}
