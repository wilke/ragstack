package jobs

import "errors"

// The contract's error bodies carry an `extra` object beside `code` and
// `detail`, and for some refusals that object is the only actionable part:
// "locked" is useless without WHO holds the lock, "duplicate" without WHICH
// job the key already named. Neither fact can be derived from the request —
// only the engine knows them — so they travel with the error.
//
// The API layer reads them through the `ErrorExtra() map[string]any` method,
// found with errors.As anywhere in the chain, which keeps the coupling to one
// tiny interface rather than to this concrete type.

// RefusalError wraps one of the package's sentinels with the `extra` object
// the contract puts in the error body. errors.Is against the sentinel still
// works: Unwrap returns the wrapped error.
type RefusalError struct {
	Err   error
	Extra map[string]any
}

func (e *RefusalError) Error() string { return e.Err.Error() }

func (e *RefusalError) Unwrap() error { return e.Err }

// ErrorExtra is the contract's `extra` for this refusal.
func (e *RefusalError) ErrorExtra() map[string]any { return e.Extra }

// refuse attaches extra to err.
func refuse(err error, extra map[string]any) error {
	return &RefusalError{Err: err, Extra: extra}
}

// ErrorExtra returns the `extra` object of the first error in err's chain
// that carries one, or nil.
func ErrorExtra(err error) map[string]any {
	var carrier interface{ ErrorExtra() map[string]any }
	if errors.As(err, &carrier) {
		return carrier.ErrorExtra()
	}
	return nil
}

// lockRefusal turns a Take failure into the contract's `locked` refusal, with
// the holder's identity read from the lock file.
func lockRefusal(err error) error {
	var le *LockedError
	if errors.As(err, &le) {
		return refuse(err, map[string]any{
			"job_id": le.Holder.JobID,
			"pid":    le.Holder.PID,
			"since":  le.Holder.Since,
			"lock":   string(le.Name),
		})
	}
	return err
}
