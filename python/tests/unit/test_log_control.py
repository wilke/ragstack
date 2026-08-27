"""Runtime log-level control — the mechanism behind ``PUT /v1/admin/log-level``.

These are the assertions the HTTP tests cannot make cheaply: that damping and
per-logger overrides compose in the right order, that a refusal changes nothing,
that the audit line survives the very change that raises the threshold, and that
the bounds on logger names actually bound something.

Everything here mutates **process-global** logging state, so the autouse fixture
below restores it. Without that, a test that raises the level to CRITICAL leaks
into every test that runs after it in the same process and the failure surfaces
somewhere else entirely.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from ragstack.observability import log_control
from ragstack.observability.logging_config import configured_dampen_loggers
from tests.log_time_support import FakeTimebase

#: Shorthand — the cap turns up in a lot of the TTL boundary cases below.
MAX_TTL = log_control.MAX_TTL_SECONDS


@pytest.fixture(autouse=True)
def _restore_logging():
    """Snapshot every level this module can touch; restore it afterwards."""
    root = logging.getLogger()
    watched = set(configured_dampen_loggers()) | {
        log_control.AUDIT_LOGGER,
        "ragstack",
        __name__,
    }
    before = {name: logging.getLogger(name).level for name in watched}
    before_root = root.level
    try:
        yield
    finally:
        log_control._reset_for_tests()
        for name, level in before.items():
            logging.getLogger(name).setLevel(level)
        root.setLevel(before_root)


@pytest.fixture
def captured(caplog):
    """``caplog`` at a level that cannot itself suppress what we are testing.

    ``caplog.set_level(0)`` on the ROOT logger would fight the very thing under
    test, so the handler is left wide open and the assertions read the records
    that reached it.
    """
    caplog.set_level(logging.DEBUG, logger="")
    return caplog


# --------------------------------------------------------------------------- #
# The level actually changes, immediately, with no restart
# --------------------------------------------------------------------------- #


def test_set_level_takes_effect_on_the_next_log_call():
    """The whole point of the endpoint: no reload."""
    log_control.set_level(level="WARNING")
    assert logging.getLogger("ragstack.somewhere").isEnabledFor(logging.INFO) is False
    log_control.set_level(level="DEBUG")
    assert logging.getLogger("ragstack.somewhere").isEnabledFor(logging.DEBUG) is True


def test_lowercase_and_warn_are_accepted_like_w1():
    """Same parsing as start-up: ``.upper()`` plus the documented ``warn``.

    ``warn`` is not a stdlib level name (``WARN`` is), and ``.env.example`` has
    documented it since before ``LOG_LEVEL`` was honoured at all — so an operator
    who types what the docs say must not get a 422.
    """
    assert log_control.set_level(level="debug")["effective_level"] == "DEBUG"
    # WARN and WARNING are the same number; getLevelName(30) canonicalises.
    assert log_control.set_level(level="warn")["effective_level"] == "WARNING"
    assert logging.getLogger().level == logging.WARNING


def test_reset_restores_the_configured_default():
    """The caller does not have to know what ``LOG_LEVEL`` said."""
    log_control.set_level(level="CRITICAL", loggers={"httpx": "DEBUG"})
    state = log_control.reset()
    assert state["runtime_override"] is False
    assert state["effective_level"] == state["configured_level_resolved"]
    assert state["logger_override_count"] == 0
    assert state["changed_by"] == ""


def test_reset_returns_an_overridden_dampened_logger_to_the_configured_dampening():
    """A dampen-set member goes back to WARNING, because ``apply_dampening``
    re-pins it. Note this does NOT exercise the ``touched`` set — dampening would
    put ``httpx`` back on its own. The next test is the one that does."""
    log_control.set_level(loggers={"httpx": "DEBUG"})
    assert logging.getLogger("httpx").level == logging.DEBUG
    log_control.reset()
    assert logging.getLogger("httpx").level == logging.WARNING


@pytest.mark.parametrize("drop", ["reset", "replace"])
def test_a_dropped_override_on_an_UNDAMPENED_logger_stops_applying(drop):
    """The ``touched`` set doing its job, and the only test that can see it.

    Nothing re-applies a logger outside the dampen set: ``apply_dampening`` does
    not touch it and the override is gone from the map, so without the explicit
    reset-to-NOTSET in ``_reapply`` it would keep DEBUG for the life of the
    process — a debugging session that never ends, on a logger nobody is looking
    at any more. Both ways of dropping an override are covered, because they take
    different code paths (``reset`` clears the map, ``loggers={}`` replaces it).
    """
    name = "ragstack"  # exists, and is deliberately NOT in the dampen set
    assert name not in set(configured_dampen_loggers())
    inherited = logging.getLogger(name).level

    log_control.set_level(loggers={name: "DEBUG"})
    assert logging.getLogger(name).level == logging.DEBUG

    if drop == "reset":
        log_control.reset()
    else:
        log_control.set_level(loggers={})
    assert logging.getLogger(name).level == logging.NOTSET == inherited


# --------------------------------------------------------------------------- #
# Damping: the reason to lower the level at all
# --------------------------------------------------------------------------- #


def test_debug_releases_the_dampened_loggers_and_info_re_damps_them():
    """W1 pins the HTTP transports to WARNING above DEBUG and releases them at
    DEBUG. That has to keep working when the level moves at runtime, or the one
    thing an operator turns DEBUG on *for* — seeing the outbound calls — is
    exactly the thing that stays off."""
    dampened = list(configured_dampen_loggers())
    assert dampened, "the dampen set must not be empty for this test to mean anything"

    log_control.set_level(level="DEBUG")
    for name in dampened:
        assert logging.getLogger(name).level == logging.NOTSET
        assert logging.getLogger(name).isEnabledFor(logging.DEBUG)

    log_control.set_level(level="INFO")
    for name in dampened:
        assert logging.getLogger(name).level == logging.WARNING
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)


def test_an_explicit_override_beats_dampening_and_survives_a_level_toggle():
    """Order is load-bearing: ``apply_dampening`` overwrites every name in the
    dampen set, so overrides must be re-applied *after* it on every change. Get
    this backwards and ``httpx=DEBUG`` silently reverts the next time anyone
    touches the root level."""
    log_control.set_level(level="INFO", loggers={"httpx": "DEBUG"})
    assert logging.getLogger("httpx").level == logging.DEBUG

    log_control.set_level(level="DEBUG")
    assert logging.getLogger("httpx").level == logging.DEBUG
    log_control.set_level(level="INFO")
    assert logging.getLogger("httpx").level == logging.DEBUG

    entry = next(e for e in log_control.describe()["loggers"] if e["name"] == "httpx")
    assert entry == {"name": "httpx", "level": "DEBUG", "source": "override"}


def test_loggers_map_replaces_rather_than_merges():
    """PUT semantics: what you send becomes the whole override set, and ``{}``
    clears it. There is deliberately no 'unset one logger' verb to get wrong."""
    log_control.set_level(loggers={"httpx": "DEBUG", "httpcore": "DEBUG"})
    assert log_control.describe()["logger_override_count"] == 2

    state = log_control.set_level(loggers={"httpx": "DEBUG"})
    assert state["logger_override_count"] == 1
    assert logging.getLogger("httpcore").level == logging.WARNING  # back to damped

    assert log_control.set_level(loggers={})["logger_override_count"] == 0


def test_omitting_a_field_leaves_it_alone():
    log_control.set_level(level="ERROR", loggers={"httpx": "DEBUG"})
    log_control.set_level(level="WARNING")  # no `loggers` → overrides untouched
    assert logging.getLogger("httpx").level == logging.DEBUG
    log_control.set_level(loggers={})  # no `level` → root untouched
    assert logging.getLogger().level == logging.WARNING


# --------------------------------------------------------------------------- #
# Refusals — and the promise that a refusal changes NOTHING
# --------------------------------------------------------------------------- #


def test_an_unknown_level_is_refused_and_nothing_changes():
    log_control.set_level(level="WARNING")
    with pytest.raises(log_control.LogControlError):
        log_control.set_level(level="verbose")
    assert logging.getLogger().level == logging.WARNING


def test_notset_is_refused_for_the_root_level():
    """NOTSET on root means "no threshold", not "inherit" — every DEBUG line from
    every library in the process. Nobody typing it means that."""
    with pytest.raises(log_control.LogControlError, match="NOTSET"):
        log_control.set_level(level="NOTSET")


def test_a_half_valid_body_applies_neither_half():
    """The atomicity assertion. A good root level plus one bad logger name must
    leave the root level alone — not apply the half that parsed."""
    log_control.set_level(level="WARNING")
    with pytest.raises(log_control.LogControlError):
        log_control.set_level(level="DEBUG", loggers={"httpx": "nonsense"})
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING


def test_an_empty_body_is_refused():
    with pytest.raises(log_control.LogControlError, match="nothing to change"):
        log_control.set_level()


# --------------------------------------------------------------------------- #
# The bounds on logger names — the unbounded-growth path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    [
        "has space",
        "new\nline",  # log injection: the name is printed on every line
        "semi;colon",
        "",
        "x" * 129,
        "unicodé",
    ],
)
def test_hostile_logger_names_are_refused(name):
    with pytest.raises(log_control.LogControlError):
        log_control.set_level(loggers={name: "DEBUG"})


def test_a_logger_that_does_not_exist_is_refused_and_is_not_created():
    """``logging.getLogger(name)`` creates a logger permanently. Requiring the
    name to exist already is what makes the growth from this endpoint exactly
    zero — a count cap alone would not, because dropping an override does not
    drop the logger."""
    name = "definitely.not.a.real.logger.427"
    assert name not in logging.Logger.manager.loggerDict
    with pytest.raises(log_control.LogControlError, match="no logger named"):
        log_control.set_level(loggers={name: "DEBUG"})
    assert name not in logging.Logger.manager.loggerDict, "the refusal created it anyway"


def test_an_existing_ancestor_is_accepted():
    """The rejection message tells the caller to try an ancestor, so an ancestor
    had better work. ``ragstack`` exists as soon as any ``ragstack.*`` module has
    been imported — which, in this process, they have."""
    state = log_control.set_level(loggers={"ragstack": "DEBUG"})
    assert state["logger_override_count"] == 1
    assert logging.getLogger("ragstack.anything").isEnabledFor(logging.DEBUG)


def test_more_overrides_than_the_cap_are_refused():
    """The cap bounds one call's blast radius — how large the response can get,
    and how much of the process's logging one request can rewrite. It is NOT
    what bounds logger creation; the existence rule above is.

    The names are created here rather than borrowed from whatever this process
    happens to have imported, so the test exercises the cap and not the
    existence rule. A test may create loggers; the endpoint may not.
    """
    names = [f"test427cap.{i}" for i in range(log_control.MAX_LOGGER_OVERRIDES + 1)]
    for name in names:
        logging.getLogger(name)
    try:
        with pytest.raises(log_control.LogControlError, match="at most"):
            log_control.set_level(loggers=dict.fromkeys(names, "DEBUG"))
        assert log_control.describe()["logger_override_count"] == 0
        # And exactly at the cap it is accepted, so the boundary is the boundary.
        state = log_control.set_level(loggers=dict.fromkeys(names[:-1], "DEBUG"))
        assert state["logger_override_count"] == log_control.MAX_LOGGER_OVERRIDES
    finally:
        log_control.reset()
        for name in names + ["test427cap"]:
            logging.Logger.manager.loggerDict.pop(name, None)


def test_the_audit_logger_cannot_be_overridden():
    """The only remaining way to silence the audit trail from this endpoint."""
    with pytest.raises(log_control.LogControlError, match="audit"):
        log_control.set_level(loggers={log_control.AUDIT_LOGGER: "CRITICAL"})


def test_the_name_root_is_refused_even_once_a_placeholder_exists():
    """``logging.getLogger("root")`` returns THE ROOT LOGGER — CPython
    short-circuits on ``name == root.name`` before consulting the manager. So an
    override of ``"root"`` would move the root level while ``_state.level``
    stayed ``None`` and the response kept reporting ``runtime_override: false``:
    the endpoint lying about itself.

    The existence rule does not catch it on its own. It only has to be true that
    *some* dependency creates a ``root.<something>`` logger — which registers a
    ``PlaceHolder`` under the key ``"root"`` — and the name becomes reachable.
    Nothing in this tree does that today, so the placeholder is created here to
    prove the refusal is the explicit check and not an accident of what happens
    to be imported.
    """
    root_before = logging.getLogger().level
    created = "root" not in logging.Logger.manager.loggerDict
    logging.getLogger("root.someplugin")  # registers a PlaceHolder under "root"
    try:
        assert "root" in logging.Logger.manager.loggerDict
        with pytest.raises(log_control.LogControlError, match="per-logger target"):
            log_control.set_level(loggers={"root": "CRITICAL"})
        assert logging.getLogger().level == root_before
        assert log_control.describe()["runtime_override"] is False
    finally:
        logging.Logger.manager.loggerDict.pop("root.someplugin", None)
        if created:
            logging.Logger.manager.loggerDict.pop("root", None)


def test_uvicorn_loggers_are_not_governed_by_the_root_level():
    """``uvicorn.*`` sets ``propagate=False`` and carries its own handlers, so it
    consults its own level and never root's. An operator reading
    ``effective_level: DEBUG`` may reasonably expect otherwise, so the behaviour
    is pinned here and documented in the contract.

    The side benefit: a complete denial of observability is not reachable through
    this endpoint — uvicorn's access log survives any level set here.
    """
    access = logging.getLogger("uvicorn.access")
    before, before_propagate = access.level, access.propagate
    try:
        access.propagate = False
        access.setLevel(logging.INFO)

        log_control.set_level(level="CRITICAL")
        assert access.isEnabledFor(logging.INFO), "root CRITICAL silenced the access log"

        log_control.set_level(level="DEBUG")
        assert not access.isEnabledFor(logging.DEBUG), "root DEBUG enabled uvicorn debug"

        # …and naming it explicitly IS the way through.
        log_control.set_level(loggers={"uvicorn.access": "CRITICAL"})
        assert not access.isEnabledFor(logging.INFO)
    finally:
        log_control.reset()
        access.setLevel(before)
        access.propagate = before_propagate


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def _audit_records(captured):
    return [r for r in captured.records if r.name == log_control.AUDIT_LOGGER]


def test_a_change_is_audited_at_warning_with_the_principal_and_before_after(captured):
    log_control.set_level(level="INFO")
    captured.clear()
    log_control.set_level(level="DEBUG", principal="bvbrc:alice")

    (record,) = _audit_records(captured)
    assert record.levelno == logging.WARNING
    assert record.principal == "bvbrc:alice"
    assert record.level_before == "INFO"
    assert record.level_after == "DEBUG"
    assert record.audit == "set"


def test_the_audit_line_survives_the_change_that_raises_the_threshold(captured):
    """Someone turning the logs off must not be able to turn off the record of
    them turning the logs off. The audit logger carries its own WARNING level, so
    ``getEffectiveLevel`` never consults root."""
    log_control.set_level(level="CRITICAL", principal="bvbrc:mallory")
    assert logging.getLogger().level == logging.CRITICAL

    records = _audit_records(captured)
    assert records, "the change to CRITICAL suppressed its own audit line"
    assert records[-1].principal == "bvbrc:mallory"
    assert records[-1].level_after == "CRITICAL"

    captured.clear()
    log_control.set_level(level="INFO", principal="bvbrc:mallory")
    assert _audit_records(captured)[-1].level_before == "CRITICAL"


def test_a_reset_is_audited_too(captured):
    """A reset is a change; it needs the same record."""
    log_control.set_level(level="DEBUG", principal="bvbrc:alice")
    captured.clear()
    log_control.reset(principal="bvbrc:bob")
    (record,) = _audit_records(captured)
    assert record.audit == "reset"
    assert record.principal == "bvbrc:bob"
    assert record.level_before == "DEBUG"


def test_the_audit_line_names_the_overrides_on_both_sides(captured):
    log_control.set_level(loggers={"httpx": "DEBUG"}, principal="bvbrc:alice")
    captured.clear()
    log_control.set_level(loggers={}, principal="bvbrc:alice")
    (record,) = _audit_records(captured)
    assert record.overrides_before == "httpx=DEBUG"
    assert record.overrides_after == "-"


def test_a_refused_change_is_not_audited(captured):
    captured.clear()
    with pytest.raises(log_control.LogControlError):
        log_control.set_level(level="verbose")
    assert not _audit_records(captured)


# --------------------------------------------------------------------------- #
# The configured-vs-effective distinction (the gap W1's review flagged)
# --------------------------------------------------------------------------- #


def test_configured_and_effective_are_reported_separately(monkeypatch):
    """``GET /v1/config`` echoes the RAW ``LOG_LEVEL``, so a value the server
    rejected is reported there while INFO is what is actually in force. This
    response must not repeat that: the raw string, what it resolves to, and what
    is live are three separate fields."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "log_level", "verbose")
    log_control.reset()
    state = log_control.describe()
    assert state["configured_level"] == "verbose"
    assert state["configured_level_resolved"] == "INFO"  # the documented fallback
    assert state["effective_level"] == "INFO"
    assert state["runtime_override"] is False

    log_control.set_level(level="DEBUG")
    state = log_control.describe()
    assert state["configured_level"] == "verbose"
    assert state["effective_level"] == "DEBUG"
    assert state["runtime_override"] is True


def test_describe_reports_the_pid_because_the_state_is_process_local():
    import os

    assert log_control.describe()["pid"] == os.getpid()


# --------------------------------------------------------------------------- #
# TTL / auto-revert (#427 follow-on)
#
# Every test below drives a CONTROLLED clock (tests/log_time_support.py). Not to
# be quick — to be able to assert things a real timer cannot show you: that the
# countdown decreases by a known amount, and that a superseded timer, fired
# deliberately, changes nothing. The real asyncio path is covered end to end in
# tests/api/test_admin_log_level.py and in conformance.
# --------------------------------------------------------------------------- #


@pytest.fixture
def clock(monkeypatch):
    """Swap the module's clock+scheduler for one this test drives."""
    fake = FakeTimebase()
    monkeypatch.setattr(log_control, "_timebase", fake)
    return fake


def test_a_ttl_change_reverts_itself_to_the_configured_defaults(clock):
    """The feature, in one assertion: the level goes back on its own.

    Asserted on the EFFECTIVE level, not on "a timer ran" — a timer that fires
    and reverts nothing is the bug this is guarding against.
    """
    log_control.set_level(level="DEBUG", loggers={"httpx": "DEBUG"}, ttl_seconds=600)
    assert logging.getLogger().level == logging.DEBUG

    assert clock.advance(599) == 0, "reverted early"
    assert logging.getLogger().level == logging.DEBUG

    assert clock.advance(1) == 1
    state = log_control.describe()
    assert state["effective_level"] == state["configured_level_resolved"]
    assert state["runtime_override"] is False
    assert state["logger_override_count"] == 0
    assert state["auto_revert_pending"] is False
    assert state["expires_in_seconds"] is None


def test_the_revert_produces_exactly_the_state_delete_produces(clock):
    """Documented as "the same end state DELETE produces" — so compare them.

    Note what this also pins down: the revert goes to the CONFIGURED defaults,
    not back to whatever was in force before the PUT. An override an operator
    had set beforehand is dropped at expiry too, which is why the contract says
    so out loud.
    """
    log_control.set_level(level="ERROR")  # a deliberate pre-existing override
    log_control.set_level(level="DEBUG", ttl_seconds=60)
    clock.advance(60)
    expired = log_control.describe()

    log_control.set_level(level="DEBUG")
    reset = log_control.reset()

    volatile = {"changed_at", "changed_by"}
    assert {k: v for k, v in expired.items() if k not in volatile} == {
        k: v for k, v in reset.items() if k not in volatile
    }


def test_no_ttl_means_no_expiry_which_is_the_unchanged_default(clock):
    """The behaviour this endpoint shipped with, still exactly itself."""
    state = log_control.set_level(level="DEBUG")
    assert state["auto_revert_pending"] is False
    assert state["ttl_seconds"] is None
    assert state["expires_at"] == ""
    assert state["expires_in_seconds"] is None

    assert clock.advance(MAX_TTL + 10_000) == 0
    assert logging.getLogger().level == logging.DEBUG


def test_get_reports_the_pending_expiry_and_it_decreases(clock):
    """An operator has to be able to see that the level will change under them."""
    state = log_control.set_level(level="DEBUG", ttl_seconds=600)
    assert state["auto_revert_pending"] is True
    assert state["ttl_seconds"] == 600
    # Rounded up, so a freshly armed 600 reads 600 rather than 599.
    assert state["expires_in_seconds"] == 600
    assert state["expires_at"] == "2026-08-26T12:10:00Z"

    clock.advance(30)
    later = log_control.describe()
    assert later["expires_in_seconds"] == 570
    assert later["ttl_seconds"] == 600, "ttl_seconds is the TTL as sent, not a countdown"
    assert later["expires_at"] == state["expires_at"], "the deadline must not drift"
    assert later["auto_revert_pending"] is True

    # A FRACTIONAL step, which is the only thing that distinguishes rounding up
    # from rounding down. Every other advance in this file lands on a whole
    # second, where floor and ceil agree — so without this, "rounded UP so a
    # freshly armed 600s TTL reads 600" is a comment decorating an assertion
    # that never exercises it, and `math.floor` passes the suite. Found by
    # review. 569.5s left must read 570: "at most this long", never less.
    clock.advance(0.5)
    assert log_control.describe()["expires_in_seconds"] == 570
    clock.advance(0.5)
    assert log_control.describe()["expires_in_seconds"] == 569


def test_max_ttl_seconds_is_reported_so_the_bound_is_discoverable(clock):
    assert log_control.describe()["max_ttl_seconds"] == log_control.MAX_TTL_SECONDS


# --------------------------------------------------------------------------- #
# Supersede — the sharpest case. Two overlapping TTLs must never fight.
# --------------------------------------------------------------------------- #


def test_a_second_put_supersedes_the_first_and_the_first_timer_is_cancelled(clock):
    log_control.set_level(level="DEBUG", ttl_seconds=60)
    first = clock.armed[0]
    log_control.set_level(level="ERROR", ttl_seconds=600)

    assert first.cancelled is True, "the superseded timer was left armed"
    assert len(clock.armed) == 1, "two timers are armed at once"
    assert clock.armed[0].due == pytest.approx(clock.monotonic() + 600)

    # The first TTL's deadline passes; the second change must be untouched.
    assert clock.advance(120) == 0
    assert logging.getLogger().level == logging.ERROR
    assert log_control.describe()["expires_in_seconds"] == 480


def test_the_superseded_timer_changes_nothing_even_if_it_fires_anyway(clock):
    """The assertion the cancel alone cannot make.

    ``handle.cancel()`` is what frees the timer, but "the first must never
    clobber the second" then rests on the scheduler honouring a cancel. Here the
    stale timer is fired DELIBERATELY — the epoch guard is what has to catch it,
    and if it does not, this test sees the second change undone.
    """
    log_control.set_level(level="DEBUG", ttl_seconds=60)
    stale = clock.armed[0]
    log_control.set_level(level="ERROR", ttl_seconds=600)

    clock.fire_regardless(stale)

    assert logging.getLogger().level == logging.ERROR
    state = log_control.describe()
    assert state["runtime_override"] is True
    assert state["auto_revert_pending"] is True
    assert state["ttl_seconds"] == 600


def test_a_later_put_without_a_ttl_disarms_the_earlier_expiry(clock):
    """Documented, and deliberately the direction it goes: the expiry belongs to
    the change that armed it, and "omitted means no expiry" is the default we
    were told not to change. Disarming is visible in the response and on the
    audit line, which is why it is safe to state rather than guard."""
    log_control.set_level(level="DEBUG", ttl_seconds=60)
    state = log_control.set_level(loggers={"httpx": "DEBUG"})

    assert state["auto_revert_pending"] is False
    assert state["ttl_seconds"] is None
    assert clock.armed == []
    assert clock.advance(600) == 0
    assert logging.getLogger().level == logging.DEBUG


def test_delete_cancels_a_pending_revert(clock):
    """It produces that revert's end state right now; leaving the timer armed
    would put a second, pointless change in the audit trail minutes later."""
    log_control.set_level(level="DEBUG", ttl_seconds=60)
    armed = clock.armed[0]
    state = log_control.reset()

    assert armed.cancelled is True
    assert state["auto_revert_pending"] is False
    assert clock.advance(600) == 0

    # And nothing was audited by a timer that should not have fired.
    assert log_control.describe()["changed_by"] == ""


def test_cancel_pending_revert_disarms_without_reverting(clock):
    """The shutdown hook. It must not fire the revert (pointless — the process is
    going away) and it must not raise."""
    log_control.set_level(level="DEBUG", ttl_seconds=60)
    log_control.cancel_pending_revert()

    assert clock.armed == []
    assert logging.getLogger().level == logging.DEBUG, "shutdown must not revert"
    assert log_control.describe()["auto_revert_pending"] is False
    log_control.cancel_pending_revert()  # idempotent


def test_cancel_pending_revert_survives_a_handle_that_raises(clock):
    """At shutdown the loop may already be gone, and shutdown must not raise."""

    class Exploding:
        def cancel(self) -> None:
            raise RuntimeError("event loop is closed")

    log_control.set_level(level="DEBUG", ttl_seconds=60)
    with log_control._lock:
        log_control._state.handle = Exploding()
    log_control.cancel_pending_revert()
    assert log_control.describe()["auto_revert_pending"] is False


def test_reset_for_tests_disarms_so_a_timer_cannot_leak_into_a_later_test(clock):
    """Both autouse fixtures call ``_reset_for_tests``. A timer armed in one test
    and left armed would fire in a later one and move the root level out from
    under it — the same process-global leak, one file further along."""
    log_control.set_level(level="DEBUG", ttl_seconds=60)
    log_control._reset_for_tests()
    assert clock.armed == []
    assert clock.advance(600) == 0


# --------------------------------------------------------------------------- #
# The auto-revert is audited, and distinguishably so
# --------------------------------------------------------------------------- #


def test_the_auto_revert_is_audited_at_warning_as_expired(clock, captured):
    """A level change nobody typed is exactly what someone will later need to
    explain, so it must be in the log — at WARNING, on the pinned logger, and
    naming the principal who armed it."""
    log_control.set_level(level="DEBUG", ttl_seconds=60, principal="acme")
    captured.clear()
    clock.advance(60)

    (record,) = _audit_records(captured)
    assert record.levelno == logging.WARNING
    assert record.audit == "expired"
    assert record.principal == "acme", "the principal who armed it, not '-'"
    assert record.level_before == "DEBUG"
    assert record.level_after == log_control.describe()["configured_level_resolved"]
    assert record.ttl_seconds == 60
    assert record.expires_at == "2026-08-26T12:01:00Z"


def test_an_expiry_is_distinguishable_from_an_operators_reset(clock, captured):
    """``audit=expired`` vs ``audit=reset`` is the whole of the distinction, and
    it has to survive the two producing the identical end state."""
    log_control.set_level(level="DEBUG", ttl_seconds=60, principal="acme")
    captured.clear()
    clock.advance(60)
    log_control.set_level(level="DEBUG", principal="acme")
    log_control.reset(principal="acme")

    assert [r.audit for r in _audit_records(captured)] == ["expired", "set", "reset"]


def test_the_audit_line_carries_the_ttl_columns_on_every_change(clock, captured):
    """Including the change that DISARMS an expiry — otherwise a superseding PUT
    that silently dropped a TTL would be invisible in the log."""
    captured.clear()
    log_control.set_level(level="DEBUG", ttl_seconds=60, principal="acme")
    log_control.set_level(level="ERROR", principal="acme")
    log_control.reset(principal="acme")

    armed, disarmed, reset = _audit_records(captured)
    assert (armed.audit, armed.ttl_seconds, armed.expires_at) == (
        "set", 60, "2026-08-26T12:01:00Z",
    )
    assert (disarmed.audit, disarmed.ttl_seconds, disarmed.expires_at) == ("set", "-", "-")
    assert (reset.audit, reset.ttl_seconds, reset.expires_at) == ("reset", "-", "-")


def test_the_expiry_clears_changed_by_because_nobody_typed_it(clock):
    """A non-empty ``changed_at`` with an empty ``changed_by`` is the response's
    signature for "the process changed its own level"."""
    log_control.set_level(level="DEBUG", ttl_seconds=60, principal="acme")
    clock.advance(60)
    state = log_control.describe()
    assert state["changed_by"] == ""
    assert state["changed_at"] != ""


# --------------------------------------------------------------------------- #
# Refusals: out of range, wrong type, and TTL with nothing to modify
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ttl, why",
    [
        (0, "zero"),
        (-1, "negative"),
        (MAX_TTL + 1, "over the cap"),
        (1.5, "fractional"),
        ("60", "a string"),
        (True, "a bool is an int in Python, and is not a duration"),
    ],
)
def test_a_bad_ttl_is_refused_and_applies_nothing(clock, ttl, why):
    """Atomic, like every other refusal here: the level must be exactly what it
    was, and no timer may be armed."""
    log_control.set_level(level="ERROR")
    before = logging.getLogger().level

    with pytest.raises(log_control.LogControlError):
        log_control.set_level(level="DEBUG", ttl_seconds=ttl)

    assert logging.getLogger().level == before, f"{why}: the level moved anyway"
    assert clock.armed == []
    assert log_control.describe()["auto_revert_pending"] is False


def test_the_bounds_are_inclusive_at_both_ends(clock):
    assert log_control.set_level(level="DEBUG", ttl_seconds=1)["ttl_seconds"] == 1
    assert log_control.set_level(level="DEBUG", ttl_seconds=MAX_TTL)["ttl_seconds"] == MAX_TTL


def test_a_ttl_with_nothing_to_change_is_refused(clock):
    """A TTL modifies a change; it is not one. And the message says what to do."""
    with pytest.raises(log_control.LogControlError) as exc:
        log_control.set_level(ttl_seconds=60)
    assert "ttl_seconds modifies a change" in str(exc.value)
    assert clock.armed == []


def test_a_refused_ttl_is_not_audited(clock, captured):
    captured.clear()
    with pytest.raises(log_control.LogControlError):
        log_control.set_level(level="DEBUG", ttl_seconds=0)
    assert not _audit_records(captured)


def test_a_ttl_is_refused_when_no_timer_can_be_armed():
    """Deliberately NOT swapping in the fake clock: the real ``Timebase`` needs a
    running loop, and there is none in a sync test.

    Refusing beats the alternatives. Applying the change and quietly arming
    nothing would leave exactly the forgotten-DEBUG state this feature exists to
    prevent, while reporting success. On the API this is unreachable — the
    handler is ``async def``.
    """
    log_control.set_level(level="ERROR")
    with pytest.raises(log_control.LogControlError) as exc:
        log_control.set_level(level="DEBUG", ttl_seconds=60)
    assert "no asyncio event loop" in str(exc.value)
    assert logging.getLogger().level == logging.ERROR
    assert log_control.describe()["auto_revert_pending"] is False


def test_a_pending_revert_does_not_outlive_or_upset_a_closing_loop():
    """The claim behind choosing a ``TimerHandle`` over a ``Task``: a revert left
    armed when the loop goes away neither keeps it alive nor complains.

    Runs a loop to completion with a real ``call_later`` revert armed and NOT
    cancelled — the worst case, i.e. the hook having been skipped entirely. The
    loop must still finish and close, and the armed thing must be a
    ``TimerHandle``.

    **The load-bearing assertion is the ``isinstance`` one, not the log check.**
    An earlier version of this docstring claimed the test caught the ``Task was
    destroyed but it is pending`` grumble; review showed it cannot. That warning
    is emitted from ``Task.__del__``, i.e. at garbage collection — after this
    handler is removed, and while the test is still holding a reference to the
    object anyway. A standalone probe captured zero records. The log assertion is
    kept as a cheap tripwire for noise that *is* emitted during ``close()``, and
    it is honest about being that and no more; a Task-based implementation is
    caught here by the type assertion.

    Not an ``async def`` test: it has to own the loop in order to close it.
    """
    seen: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    handler = Collect()
    asyncio_log = logging.getLogger("asyncio")
    asyncio_log.addHandler(handler)
    loop = asyncio.new_event_loop()
    try:

        async def arm() -> object:
            log_control.set_level(level="DEBUG", ttl_seconds=600)
            return log_control._state.handle

        handle = loop.run_until_complete(arm())
        assert isinstance(handle, asyncio.TimerHandle)
        assert not handle.cancelled(), "this test is only meaningful while armed"
    finally:
        loop.close()
        asyncio_log.removeHandler(handler)
        log_control._reset_for_tests()

    assert loop.is_closed()
    noisy = [r for r in seen if r.levelno >= logging.WARNING]
    assert not noisy, [r.getMessage() for r in noisy]


def test_a_refused_put_leaves_a_pending_revert_ARMED(clock):
    """Atomicity in the direction the other refusal tests cannot see.

    Every other "a 422 changes nothing" test here starts with no timer armed, so
    its assertion is only ever *no timer exists* — which a bug that disarms on
    refusal would satisfy perfectly. This one starts with one armed and asserts
    it survived, deadline and all.

    The property rests on ordering: validation raises before the lock, so the
    ``_cancel_revert_locked()`` at the top of the applied path never runs on a
    refusal. That is one line away from being wrong — "supersede cancels first"
    reads like something to hoist — and hoisting it would turn every typo'd PUT
    into a silent disarm, i.e. the forgotten-DEBUG failure this feature exists
    to prevent, reached by way of a request the server said no to.
    """
    log_control.set_level(level="DEBUG", ttl_seconds=600)
    armed = clock.armed[0]

    clock.advance(30)
    for bad in ({"level": "verbose"}, {"loggers": {"no.such.logger.here": "DEBUG"}},
                {"level": "DEBUG", "ttl_seconds": 0}):
        with pytest.raises(log_control.LogControlError):
            log_control.set_level(**bad)

    state = log_control.describe()
    assert state["auto_revert_pending"] is True, "a refused request disarmed the expiry"
    assert state["ttl_seconds"] == 600
    assert state["expires_in_seconds"] == 570, "the deadline moved"
    assert clock.armed == [armed], "the timer was replaced rather than left alone"

    # And it still actually fires.
    assert clock.advance(570) == 1
    assert log_control.describe()["runtime_override"] is False
