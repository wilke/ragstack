"""A controllable clock + timer for the log-level TTL tests (#427).

``ragstack.observability.log_control`` reads time and arms its auto-revert
through one seam (``log_control._timebase``). This is the fake that goes in its
place, shared by the unit tests and the API tests because both need it.

**Why a fake rather than a short sleep.** Two of the properties under test
cannot be observed by sleeping:

* the countdown in ``GET`` has to be seen *decreasing*, which needs the clock to
  move by a known amount, not by however long the test happened to take;
* the supersede rule's whole content is "the superseded timer must not fire".
  A real timer proves that by not firing, which is indistinguishable from a
  timer that was never armed and from a test that finished too early. Here the
  stale timer is fired **deliberately**, and the assertion is that nothing moved.

The real path is still exercised end to end — ``test_admin_log_level.py`` runs a
one-second TTL on a live event loop, and conformance runs one against a real
server — so the fake is a magnifying glass, not a substitute.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta


class FakeHandle:
    """What :meth:`FakeTimebase.call_later` hands back. ``cancel`` is observable
    so a test can assert that supersede actually cancelled rather than merely
    got away with the staleness guard."""

    def __init__(self, tb: FakeTimebase, due: float, callback: Callable[[], None]) -> None:
        self._tb = tb
        self.due = due
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeTimebase:
    """A monotonic clock that only moves when told, and timers that only fire
    when the clock passes them.

    Substituted with ``monkeypatch.setattr(log_control, "_timebase", fake)``.
    """

    #: A fixed wall-clock epoch, so ``expires_at`` is exactly predictable.
    START_WALL = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)

    def __init__(self) -> None:
        self._t = 1000.0
        self.handles: list[FakeHandle] = []

    # -- the Timebase interface -------------------------------------------- #

    def monotonic(self) -> float:
        return self._t

    def utcnow(self) -> datetime:
        return self.START_WALL + timedelta(seconds=self._t - 1000.0)

    def check_schedulable(self) -> None:
        """Always schedulable: the fake IS the scheduler, no loop required."""

    def call_later(self, delay: float, callback: Callable[[], None]) -> FakeHandle:
        handle = FakeHandle(self, self._t + delay, callback)
        self.handles.append(handle)
        return handle

    # -- the test controls -------------------------------------------------- #

    def advance(self, seconds: float) -> int:
        """Move the clock forward and fire every timer that came due.

        Cancelled handles are skipped, exactly as an event loop skips a cancelled
        ``TimerHandle`` — so a test that wants to prove the staleness guard has to
        call :meth:`fire_regardless`, and cannot get a false pass from a cancel
        that happened to work.
        """
        self._t += seconds
        fired = 0
        for handle in [h for h in self.handles if not h.cancelled and h.due <= self._t]:
            handle.cancelled = True  # a fired timer does not fire twice
            handle.callback()
            fired += 1
        return fired

    def fire_regardless(self, handle: FakeHandle) -> None:
        """Run a timer's callback even though it was cancelled or is not due.

        This is the one thing a real scheduler will not do for you, and it is
        what makes "a superseded timer can never clobber a newer change" an
        assertion rather than an absence of evidence.
        """
        handle.callback()

    @property
    def armed(self) -> list[FakeHandle]:
        """Handles that would still fire — i.e. armed and not cancelled."""
        return [h for h in self.handles if not h.cancelled]
