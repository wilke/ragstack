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

import logging

import pytest

from ragstack.observability import log_control
from ragstack.observability.logging_config import configured_dampen_loggers


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
