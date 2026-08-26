"""Store error types.

Kept dependency-free (no qdrant-client import) so callers can catch them
without pulling in optional backends.
"""
from __future__ import annotations

#: The machine-readable failure kinds. Deliberately three, and deliberately not
#: a taxonomy of transport-exception names — this is a *discriminator for
#: advice*:
#:
#: ``timeout``
#:     The connection succeeded and the *operation* was too slow. Retry is
#:     genuinely the right advice — the second read is warm.
#: ``unreachable``
#:     We never got to the store. Retry probably will not help.
#: ``error``
#:     The store answered, unhappily (a 5xx).
#:
#: ``ConnectTimeout`` is ``unreachable``, **not** ``timeout``. It *is* a timeout,
#: but it does not support "it'll be warm next time" — conflating the two would
#: make the UI's retry advice (#427 item D) a lie on precisely the case where
#: retrying is useless. Keep the split; there is a test pinning it.
KIND_TIMEOUT = "timeout"
KIND_UNREACHABLE = "unreachable"
KIND_ERROR = "error"

#: Every value ``StoreUnavailable.kind`` may take. The UI (#427 W6) branches on
#: this, so adding a fourth is a user-visible change, not an implementation
#: detail — an unknown value must degrade to the generic message, never break.
STORE_FAILURE_KINDS = (KIND_TIMEOUT, KIND_UNREACHABLE, KIND_ERROR)


class StoreUnavailable(RuntimeError):
    """A backing store could not answer: unreachable, timed out, or returned a
    server-side error. Transient by nature — the API maps it to 503 (never 500)
    so callers can tell "retry later" from "bug". ``store`` names the backend.

    Carries two structured fields alongside the message (#427 item C):

    ``kind``
        One of :data:`STORE_FAILURE_KINDS`. **Required**, and deliberately has
        no default. A default would have to be one of the three, and each is a
        specific factual claim about what happened — ``error`` asserts the store
        *answered*. A raise site that had not thought about it would emit that
        claim into the 503 body and, once W6 lands, into the advice a user is
        given. Better to make every new backend decide.

        The cost is a keystroke at two raise sites; the benefit is that
        ``kind ==`` assertions in tests are non-vacuous, which a default
        silently destroyed: with one, a test asserting ``kind == "error"``
        passed even with the whole derivation deleted. That exact shape was
        caught on the Qdrant side and lived on in the ES tests until review.
    ``elapsed_s``
        Wall time the *failing call itself* spent, or ``None`` when the caller
        did not measure it. Genuinely optional — "I did not time this" is a real
        and honest state, unlike "I did not think about the kind". This is
        **not** the timeout value: a ``ConnectError`` fails in milliseconds
        against a 30 s bound, and telling those two apart after the fact is the
        whole reason to record it.

    The message stays a full human sentence — it is what made the #427 incident
    diagnosable in one ``grep`` — and the fields go *alongside* it, never
    instead of it.
    """

    def __init__(
        self,
        store: str,
        message: str,
        *,
        kind: str,
        elapsed_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.store = store
        self.kind = kind
        self.elapsed_s = elapsed_s


class VectorDimMismatch(RuntimeError):
    """An existing collection's vector size disagrees with the configured
    embedding dimension. Fatal: writing would mix incompatible vectors."""
