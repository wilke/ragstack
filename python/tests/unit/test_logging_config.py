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

import pytest

from ragstack.observability.context import RequestContextFilter
from ragstack.observability.logging_config import (
    LOG_LEVEL_NAMES,
    JsonFormatter,
    LogfmtFormatter,
    configure_logging,
    resolve_log_level,
)


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Save and restore global logging state. Without this the first test that
    sets DEBUG leaves every later test in the session at DEBUG."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    access_disabled = logging.getLogger("uvicorn.access").disabled
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        logging.getLogger("uvicorn.access").disabled = access_disabled


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


def test_access_log_is_left_alone_by_default():
    """``access_log_replaced`` defaults FALSE in W1 — the summary line that would
    supersede uvicorn's access log does not exist until W3, and turning it off
    now would just lose the access log and replace it with nothing."""
    logging.getLogger("uvicorn.access").disabled = False
    configure_logging(level="INFO", quiet_uvicorn_access=False)
    assert logging.getLogger("uvicorn.access").disabled is False

    configure_logging(level="INFO", quiet_uvicorn_access=True)
    assert logging.getLogger("uvicorn.access").disabled is True
