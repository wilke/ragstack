"""``configure_logging()`` — the knob that was advertised and never honoured.

Two distinct regressions are pinned here.

**Finding #2 (#427): ``LOG_LEVEL`` configured nothing.** ``config.py`` defined it,
``GET /v1/config`` echoed it and tenant provisioning wrote it, and nothing in the
tree ever called ``setLevel``. Every ``log.info()`` under ``ragstack.*`` was
discarded and everything above it printed through ``logging.lastResort`` with no
timestamp, level or logger name.

**Amendment A1: honouring it naively would have CRASHED two live tenants.**
``logging.setLevel`` rejects a lowercase name outright —
``ValueError: Unknown level: 'info'`` — and the deployed ``dev`` and ``demo``
tenants both carry ``LOG_LEVEL=info`` (written by ``apptainer/new-tenant.sh``),
while ``.env.example`` documented ``warn``, which is not a stdlib level name at
all. So the parse is case-insensitive and, more importantly, **never fatal**: an
unrecognised value degrades the logs, it does not take the API down.

Every test here restores the root logger's handlers and level, because leaking a
DEBUG root or a duplicate handler into the rest of the suite is both a flake and
a doubled-output bug that is miserable to trace back here.
"""
import json
import logging
import time

import pytest

from ragstack.observability.context import RequestContextFilter
from ragstack.observability.logging_config import (
    DEFAULT_DAMPEN_LOGGERS,
    LOG_LEVEL_NAMES,
    JsonFormatter,
    LogfmtFormatter,
    apply_log_level,
    configure_logging,
    resolve_log_level,
)


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Save and restore global logging state.

    Without this the first test that sets DEBUG leaves every later test in the
    session at DEBUG. Every *named* logger's level is snapshotted too, not just
    root's: ``configure_logging`` damps the HTTP transports by setting their
    levels, so a test here would otherwise silence ``httpx`` for the rest of the
    session — the same cross-test leak that made an assertion elsewhere in this
    branch pass alone and fail in the full suite.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    access_disabled = logging.getLogger("uvicorn.access").disabled
    levels = {
        name: lg.level
        for name, lg in logging.root.manager.loggerDict.items()
        if isinstance(lg, logging.Logger)
    }
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        logging.getLogger("uvicorn.access").disabled = access_disabled
        for name, lvl in levels.items():
            logging.getLogger(name).setLevel(lvl)


# --------------------------------------------------------------------------- #
# A1 — the level parse must tolerate what is deployed today
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INFO", logging.INFO),
        ("info", logging.INFO),  # dev and demo tenants, live, right now
        ("DEBUG", logging.DEBUG),
        ("debug", logging.DEBUG),
        ("warn", logging.WARN),  # .env.example documented this; stdlib wants WARN
        ("WARN", logging.WARN),
        ("warning", logging.WARNING),
        ("error", logging.ERROR),
        ("  Error  ", logging.ERROR),  # whitespace from an env file
        ("critical", logging.CRITICAL),
    ],
)
def test_documented_and_deployed_level_names_all_resolve(raw, expected):
    level, warning = resolve_log_level(raw)
    assert level == expected
    assert warning is None


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_empty_level_defaults_to_info_silently(raw):
    assert resolve_log_level(raw) == (logging.INFO, None)


@pytest.mark.parametrize("raw", ["verbose", "TRACE", "9", "INFO;DROP", "🙂"])
def test_unknown_level_falls_back_instead_of_raising(raw):
    """A typo in LOG_LEVEL must degrade the logs, never fail startup."""
    level, warning = resolve_log_level(raw)
    assert level == logging.INFO
    assert warning is not None and repr(raw) in warning


def test_configure_logging_never_raises_on_a_bad_level(caplog):
    """The end-to-end version of the above: the whole point of A1 is that this
    call is what runs at import of ``api.main`` on every tenant."""
    handler = configure_logging(level="not-a-level", log_format="logfmt")
    assert logging.getLogger().level == logging.INFO
    assert handler in logging.getLogger().handlers


def test_the_fallback_warning_is_actually_emitted(capsys):
    """Ordering matters: the warning about a broken LOG_LEVEL must be emitted
    AFTER the handler is installed, or it is swallowed by the very absence of a
    handler this function exists to fix."""
    configure_logging(level="nonsense", log_format="logfmt")
    err = capsys.readouterr().err
    assert "nonsense" in err and "falling back to INFO" in err


def test_every_advertised_level_name_is_accepted():
    """The allowlist and the names the code claims to accept cannot drift."""
    for name in LOG_LEVEL_NAMES:
        level, warning = resolve_log_level(name.lower())
        assert warning is None, name
        assert isinstance(level, int)


# --------------------------------------------------------------------------- #
# Finding #2 — the level now actually reaches the logger
# --------------------------------------------------------------------------- #


def test_log_level_debug_actually_emits_debug(capsys):
    configure_logging(level="DEBUG", log_format="logfmt")
    logging.getLogger("ragstack.test").debug("a debug line")
    assert "a debug line" in capsys.readouterr().err


def test_log_level_warning_suppresses_info(capsys):
    configure_logging(level="WARNING", log_format="logfmt")
    log = logging.getLogger("ragstack.test")
    log.info("should be invisible")
    log.warning("should be visible")
    err = capsys.readouterr().err
    assert "should be invisible" not in err
    assert "should be visible" in err


def test_configure_logging_is_idempotent(capsys):
    """Called at import of ``api.main`` and by anything that reconfigures. A
    stacked handler would double every single line."""
    configure_logging(level="INFO", log_format="logfmt")
    configure_logging(level="INFO", log_format="logfmt")
    configure_logging(level="INFO", log_format="logfmt")
    logging.getLogger("ragstack.test").info("once please")
    assert capsys.readouterr().err.count("once please") == 1


# --------------------------------------------------------------------------- #
# Format
# --------------------------------------------------------------------------- #


def test_existing_percent_style_call_sites_gain_context_for_free(capsys):
    """The whole reason the context lives in a ``logging.Filter`` on the handler:
    ~219 existing ``log.warning("x %s", 1)`` call sites gain ``rid=`` with zero
    call-site edits, and their ``%``-style arguments still interpolate."""
    from ragstack.observability.context import RequestContext, set_context

    configure_logging(level="INFO", log_format="logfmt")
    set_context(RequestContext(request_id="deadbeefdeadbeef", tenant="acme", role="user"))
    logging.getLogger("ragstack.test").warning("x %s", 1)

    line = capsys.readouterr().err.strip()
    assert "rid=deadbeefdeadbeef" in line
    assert "tenant=acme" in line
    assert "role=user" in line
    assert 'msg="x 1"' in line


def test_json_format_emits_parseable_json(capsys):
    """The ``LOG_FORMAT=json`` branch is built and TESTED now, so the day a log
    shipper exists the switch is a config change and not a project."""
    from ragstack.observability.context import RequestContext, set_context

    configure_logging(level="INFO", log_format="json")
    set_context(RequestContext(request_id="cafebabecafebabe", tenant="acme"))
    logging.getLogger("ragstack.test").info("hello %s", "world")

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "ragstack.test"
    assert payload["rid"] == "cafebabecafebabe"
    assert payload["tenant"] == "acme"


def test_unknown_log_format_falls_back_to_logfmt():
    """Same never-fatal rule as the level."""
    handler = configure_logging(level="INFO", log_format="yamlfmt")
    assert isinstance(handler.formatter, LogfmtFormatter)
    assert isinstance(configure_logging(log_format="json").formatter, JsonFormatter)


@pytest.mark.parametrize("fmt", ["logfmt", "json"])
def test_a_record_with_no_request_context_still_formats(capsys, fmt):
    """uvicorn's records and ``warnings``' records never see a request context.
    The filter must fill every field it claims to, or the formatter raises
    inside the logging machinery — where the failure is nearly untraceable."""
    configure_logging(level="INFO", log_format=fmt)
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "plain", (), None)
    RequestContextFilter().filter(record)
    logging.getLogger().handle(record)
    assert "plain" in capsys.readouterr().err


@pytest.mark.parametrize("fmt", ["logfmt", "json"])
def test_a_newline_in_a_message_cannot_forge_a_second_line(capsys, fmt):
    """logfmt quotes and JSON escapes, so a control character in a message never
    becomes an additional log record."""
    configure_logging(level="INFO", log_format=fmt)
    logging.getLogger("ragstack.test").info("first\nlevel=CRITICAL msg=forged")
    err = capsys.readouterr().err
    assert err.count("\n") == 1, f"message forged an extra line: {err!r}"


@pytest.mark.parametrize("formatter_cls", [LogfmtFormatter, JsonFormatter])
def test_timestamps_are_utc_not_local_time_with_a_z_on_them(formatter_cls, monkeypatch):
    """``logging.Formatter.converter`` defaults to ``time.localtime``, so a ``Z``
    suffix on the default output is a silent lie — measured at five hours off on
    this host, against the gateway's own ``Date:`` header.

    That is not cosmetic for #427. The timestamp is the field an operator uses to
    line the app log up with nginx's and with a user's "it failed around 2pm",
    which is the whole activity this work exists to make possible.

    **TZ is pinned, and that is the load-bearing part of this test.** With the
    converter reverted to ``time.localtime`` this passes 2/2 under ``TZ=UTC`` and
    fails 2/2 under this host's ``-0500`` — so without the pin the guard is a
    property of where it happens to run. CI containers run UTC, and #427 W7 adds
    a CI job, which means the guard would switch itself off at exactly the moment
    it started being the only thing watching. A non-UTC zone with no DST
    ambiguity, applied through ``time.tzset`` so the C library actually re-reads
    it.
    """
    monkeypatch.setenv("TZ", "Asia/Kolkata")  # +05:30 — and never equal to UTC
    time.tzset()
    try:
        record = logging.LogRecord("t", logging.INFO, "f.py", 1, "x", (), None)
        rendered = formatter_cls().formatTime(record)

        local = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
        utc = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
        assert local != utc, "TZ pin did not take effect; this test would prove nothing"

        assert rendered.endswith("Z")
        assert rendered.startswith(utc), (
            f"timestamp {rendered!r} is local time with a Z on it (UTC is {utc})"
        )
    finally:
        # tzset() mutates process-global C state; monkeypatch restores the env
        # var but not the library's parsed copy of it.
        monkeypatch.undo()
        time.tzset()


def test_importing_the_app_installs_the_root_handler():
    """The production wiring, pinned separately from the function itself.

    Every other test here calls ``configure_logging()`` directly, so all of them
    would still pass if the call were dropped from ``api/main.py`` — and the
    original bug was precisely that nobody called it. This is what says the API
    process actually gets a handler.

    Deliberately asserts on the ROOT logger: the whole point is that the ~219
    ``ragstack.*`` call sites emit through it without configuring anything
    themselves.
    """
    import ragstack.api.main  # noqa: F401  (imported for its side effect)

    root = logging.getLogger()
    ours = [h for h in root.handlers if getattr(h, "name", None) == "ragstack.observability"]
    assert ours, "importing ragstack.api.main did not install a root log handler"
    assert isinstance(ours[0].formatter, (LogfmtFormatter, JsonFormatter))
    assert any(isinstance(f, RequestContextFilter) for f in ours[0].filters)
    assert root.level <= logging.INFO, (
        f"root logger at {logging.getLevelName(root.level)} — INFO lines are discarded again"
    )


def test_http_transports_are_damped_at_info(capsys):
    """Raising the ROOT logger to INFO un-mutes every third-party library at
    once — they were quiet before only because root sat at WARNING with no
    handler, i.e. silent by accident.

    That matters at a measured scale: one ``/v1/query`` makes 5 outbound HTTP
    calls minimum (embed, Qdrant, ES, rerank, LLM), 6 with query rewriting, and
    up to ~14 multi-collection — one ``httpx`` INFO line each. The single
    summary line #427 exists to produce would land at a signal-to-noise of 1:5,
    worst case 1:14. #427 exists because the logs were unusable; trading one
    kind of unusable for another is not a fix.
    """
    configure_logging(level="INFO", log_format="logfmt")
    for name in DEFAULT_DAMPEN_LOGGERS:
        logging.getLogger(name).info("chatter from %s", name)
    logging.getLogger("ragstack.test").info("ours")

    err = capsys.readouterr().err
    assert "ours" in err, "damping the transports must not damp us"
    assert "chatter" not in err, "a transport INFO line reached the log at LOG_LEVEL=info"


def test_a_damped_logger_still_reports_warnings(capsys):
    """Damping hides routine per-call chatter, never a real problem."""
    configure_logging(level="INFO", log_format="logfmt")
    logging.getLogger(DEFAULT_DAMPEN_LOGGERS[0]).warning("a real problem")
    assert "a real problem" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["neo4j", "qdrant_client"])
def test_data_path_clients_are_deliberately_NOT_damped(capsys, name):
    """A deliberate exclusion, not an oversight.

    ``neo4j`` and ``qdrant_client`` sit closer to our own data path and are far
    less chatty than the HTTP transports — and Qdrant is the store the #427
    incident was actually about. The noise problem is the transports; damping
    these would trade signal for very little quiet.
    """
    configure_logging(level="INFO", log_format="logfmt")
    logging.getLogger(name).info("something from %s", name)
    assert f"something from {name}" in capsys.readouterr().err


def test_debug_leaves_the_transports_alone(capsys):
    """Progressive disclosure: INFO is the production default where the summary
    line has to be findable; DEBUG is what you set when you are actually
    looking, and then you should get the full HTTP detail you asked for."""
    configure_logging(level="DEBUG", log_format="logfmt")
    logging.getLogger(DEFAULT_DAMPEN_LOGGERS[0]).debug("wire detail")
    assert "wire detail" in capsys.readouterr().err


def test_debug_restores_chatter_even_after_an_info_configure(capsys):
    """Re-appliable across a level CHANGE: configure at INFO (which damps), then
    at DEBUG. The second call must undo the first, or a process that raises its
    level to debug something gets everything except the part it wanted. This is
    the property the future runtime level-change endpoint depends on."""
    configure_logging(level="INFO", log_format="logfmt")
    configure_logging(level="DEBUG", log_format="logfmt")

    logging.getLogger(DEFAULT_DAMPEN_LOGGERS[0]).info("chatter")
    assert "chatter" in capsys.readouterr().err


def test_the_dampen_set_is_configurable_not_hardcoded(capsys):
    """``LOG_DAMPEN_LOGGERS`` is a setting so an operator can change it without
    a code change — including damping something we never anticipated.

    Both loggers are invented names rather than one real and one invented. An
    earlier version used ``httpx`` as the not-in-the-set control and failed in
    the full suite for a reason that had nothing to do with the behaviour under
    test: importing the app damps ``httpx`` at session start, and damping is
    only *released* at DEBUG, so ``httpx`` was still at WARNING from ambient
    state. The code was right; the test's assumption was not.
    """
    configure_logging(level="INFO", log_format="logfmt", dampen=["some.vendor.lib"])

    logging.getLogger("some.vendor.lib").info("vendor chatter")
    logging.getLogger("other.vendor.lib").info("other chatter")

    err = capsys.readouterr().err
    assert "vendor chatter" not in err, "the configured logger was not damped"
    assert "other chatter" in err, "a logger outside the configured set was damped anyway"


def test_an_empty_dampen_set_damps_nothing(capsys):
    """``LOG_DAMPEN_LOGGERS=`` must mean "damp nothing", not "use the default".

    A truthiness test on the configured list would make the empty value do the
    exact opposite of what an operator typing it intends — a setting that
    silently ignores you is worse than no setting.
    """
    logging.getLogger("some.vendor.lib").setLevel(logging.NOTSET)
    configure_logging(level="INFO", log_format="logfmt", dampen=[])

    logging.getLogger("some.vendor.lib").info("nothing is damped")
    assert "nothing is damped" in capsys.readouterr().err


def test_apply_log_level_changes_the_level_without_touching_handlers():
    """The seam the future runtime level-change endpoint needs (that endpoint is
    NOT part of this work item). Changing the level must not rebuild the
    handler: doing so would risk doubling every line or dropping the context
    filter, which is exactly the class of bug #427 is cleaning up."""
    handler = configure_logging(level="INFO", log_format="logfmt")
    before = list(logging.getLogger().handlers)

    apply_log_level("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger().handlers == before, "handlers changed on a level change"
    assert handler in logging.getLogger().handlers

    apply_log_level("WARNING")
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger().handlers == before


def test_apply_log_level_never_raises_on_a_bad_value():
    """Same never-fatal rule as start-up — and it matters more here, because a
    runtime caller would be passing operator input straight in."""
    configure_logging(level="INFO", log_format="logfmt")
    numeric, warning = apply_log_level("not-a-level")
    assert numeric == logging.INFO
    assert warning is not None


def test_access_log_is_left_alone_by_default():
    """``access_log_replaced`` defaults FALSE in W1 — the summary line that would
    supersede uvicorn's access log does not exist until W3, and turning it off
    now would just lose the access log and replace it with nothing."""
    logging.getLogger("uvicorn.access").disabled = False
    configure_logging(level="INFO", quiet_uvicorn_access=False)
    assert logging.getLogger("uvicorn.access").disabled is False

    configure_logging(level="INFO", quiet_uvicorn_access=True)
    assert logging.getLogger("uvicorn.access").disabled is True
